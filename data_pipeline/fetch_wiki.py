"""
For each Zagreb street name:
  1. Query Wikidata SPARQL for the street entity → named_after (P138) + Wikipedia links
  2. Fetch Wikipedia REST API summaries (HR preferred, EN fallback)
  3. Store everything in data/streets.db (SQLite)

Run after fetch_streets.py.  Resumes from where it left off.
"""

import asyncio
import json
import sqlite3
import urllib.parse
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm as atqdm

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "streets.db"

# Wikidata/Wikipedia require a descriptive User-Agent with contact info
HEADERS = {
    "User-Agent": (
        "zg-street-history/1.0 "
        "(https://github.com/zg-street-history; educational project) "
        "python-httpx/0.27"
    )
}

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKI_REST_HR = "https://hr.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_REST_EN = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_SEARCH_HR = (
    "https://hr.wikipedia.org/w/api.php"
    "?action=query&list=search&srsearch={q}&srlimit=1&format=json"
)
WIKI_SEARCH_EN = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&list=search&srsearch={q}&srlimit=1&format=json"
)

CONCURRENCY = 12   # parallel Wikipedia API calls


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS street_wiki (
            name                    TEXT PRIMARY KEY,
            wikidata_qid            TEXT,
            named_after_qid         TEXT,
            named_after_name        TEXT,
            named_after_description TEXT,
            named_after_wiki_url_hr TEXT,
            named_after_wiki_url_en TEXT,
            named_after_summary_hr  TEXT,
            named_after_summary_en  TEXT,
            named_after_image_url   TEXT,
            street_wiki_url_hr      TEXT,
            street_wiki_url_en      TEXT,
            street_summary_hr       TEXT,
            street_summary_en       TEXT,
            updated_at              TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def upsert(conn: sqlite3.Connection, row: dict):
    conn.execute(
        """
        INSERT OR REPLACE INTO street_wiki VALUES (
            :name, :wikidata_qid, :named_after_qid,
            :named_after_name, :named_after_description,
            :named_after_wiki_url_hr, :named_after_wiki_url_en,
            :named_after_summary_hr, :named_after_summary_en,
            :named_after_image_url,
            :street_wiki_url_hr, :street_wiki_url_en,
            :street_summary_hr, :street_summary_en,
            datetime('now')
        )
        """,
        row,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Wikidata bulk query
# ---------------------------------------------------------------------------

SPARQL_QUERY = """
SELECT DISTINCT
  ?street ?streetLabel
  ?namedAfter ?namedAfterLabel ?namedAfterDesc
  ?streetArticleHr ?streetArticleEn
  ?personArticleHr ?personArticleEn
  ?personImage
WHERE {
  ?street wdt:P31/wdt:P279* wd:Q79007 .
  ?street wdt:P131+ wd:Q1435 .
  OPTIONAL { ?street wdt:P138 ?namedAfter . }
  OPTIONAL {
    ?sa schema:about ?street ; schema:inLanguage "hr" ;
        schema:isPartOf <https://hr.wikipedia.org/> .
    BIND(REPLACE(STR(?sa), "https://hr.wikipedia.org/wiki/", "") AS ?streetArticleHr)
  }
  OPTIONAL {
    ?sa2 schema:about ?street ; schema:inLanguage "en" ;
         schema:isPartOf <https://en.wikipedia.org/> .
    BIND(REPLACE(STR(?sa2), "https://en.wikipedia.org/wiki/", "") AS ?streetArticleEn)
  }
  OPTIONAL {
    ?pa schema:about ?namedAfter ; schema:inLanguage "hr" ;
        schema:isPartOf <https://hr.wikipedia.org/> .
    BIND(REPLACE(STR(?pa), "https://hr.wikipedia.org/wiki/", "") AS ?personArticleHr)
  }
  OPTIONAL {
    ?pa2 schema:about ?namedAfter ; schema:inLanguage "en" ;
         schema:isPartOf <https://en.wikipedia.org/> .
    BIND(REPLACE(STR(?pa2), "https://en.wikipedia.org/wiki/", "") AS ?personArticleEn)
  }
  OPTIONAL { ?namedAfter wdt:P18 ?personImage . }
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "hr,en" .
    ?street rdfs:label ?streetLabel .
    ?namedAfter rdfs:label ?namedAfterLabel .
    ?namedAfter schema:description ?namedAfterDesc .
  }
}
"""


def fetch_wikidata() -> list[dict]:
    print("Querying Wikidata SPARQL for Zagreb streets...")
    try:
        r = httpx.get(
            WIKIDATA_SPARQL,
            params={"query": SPARQL_QUERY, "format": "json"},
            headers=HEADERS,
            timeout=90,
        )
        r.raise_for_status()
        bindings = r.json()["results"]["bindings"]
        print(f"  Got {len(bindings)} Wikidata rows")
        return bindings
    except Exception as e:
        print(f"  Wikidata query failed: {e}")
        print("  Continuing without Wikidata (will use Wikipedia search fallback)")
        return []


def val(binding: dict, key: str) -> str | None:
    return binding.get(key, {}).get("value") or None


def qid(uri: str | None) -> str | None:
    if uri and "/Q" in uri:
        return "Q" + uri.split("/Q")[-1]
    return None


def build_wikidata_index(rows: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in rows:
        label = val(row, "streetLabel")
        if not label:
            continue
        key = label.strip()
        entry = {
            "wikidata_qid": qid(val(row, "street")),
            "named_after_qid": qid(val(row, "namedAfter")),
            "named_after_name": val(row, "namedAfterLabel"),
            "named_after_description": val(row, "namedAfterDesc"),
            "person_article_hr": val(row, "personArticleHr"),
            "person_article_en": val(row, "personArticleEn"),
            "street_article_hr": val(row, "streetArticleHr"),
            "street_article_en": val(row, "streetArticleEn"),
            "named_after_image_url": val(row, "personImage"),
        }
        existing = index.get(key)
        if not existing or (entry["named_after_qid"] and not existing.get("named_after_qid")):
            index[key] = entry
    return index


# ---------------------------------------------------------------------------
# Async Wikipedia helpers
# ---------------------------------------------------------------------------

async def wiki_summary(
    client: httpx.AsyncClient, title: str, lang: str = "hr"
) -> tuple[str | None, str | None]:
    """Returns (summary_text, page_url) or (None, None)."""
    if not title:
        return None, None
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (WIKI_REST_HR if lang == "hr" else WIKI_REST_EN).format(title=encoded)
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        summary = data.get("extract", "").strip() or None
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page") or None
        return summary, page_url
    except Exception:
        return None, None


async def wiki_search_title(
    client: httpx.AsyncClient, query: str, lang: str = "hr"
) -> str | None:
    url = (WIKI_SEARCH_HR if lang == "hr" else WIKI_SEARCH_EN).format(
        q=urllib.parse.quote(query)
    )
    try:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if results:
            return results[0]["title"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Per-street processing
# ---------------------------------------------------------------------------

async def process_street(
    name: str,
    wd: dict | None,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    conn: sqlite3.Connection,
):
    async with semaphore:
        row = {
            "name": name,
            "wikidata_qid": None,
            "named_after_qid": None,
            "named_after_name": None,
            "named_after_description": None,
            "named_after_wiki_url_hr": None,
            "named_after_wiki_url_en": None,
            "named_after_summary_hr": None,
            "named_after_summary_en": None,
            "named_after_image_url": None,
            "street_wiki_url_hr": None,
            "street_wiki_url_en": None,
            "street_summary_hr": None,
            "street_summary_en": None,
        }

        if wd:
            row["wikidata_qid"] = wd.get("wikidata_qid")
            row["named_after_qid"] = wd.get("named_after_qid")
            row["named_after_name"] = wd.get("named_after_name")
            row["named_after_description"] = wd.get("named_after_description")
            row["named_after_image_url"] = wd.get("named_after_image_url")

            # Fetch all wiki content in parallel
            tasks = [
                wiki_summary(client, wd.get("person_article_hr") or "", "hr"),
                wiki_summary(client, wd.get("person_article_en") or "", "en"),
                wiki_summary(client, wd.get("street_article_hr") or "", "hr"),
                wiki_summary(client, wd.get("street_article_en") or "", "en"),
            ]
            results = await asyncio.gather(*tasks)

            p_hr_sum, p_hr_url = results[0]
            p_en_sum, p_en_url = results[1]
            s_hr_sum, s_hr_url = results[2]
            s_en_sum, s_en_url = results[3]

            row["named_after_summary_hr"] = p_hr_sum
            row["named_after_wiki_url_hr"] = p_hr_url
            row["named_after_summary_en"] = p_en_sum
            row["named_after_wiki_url_en"] = p_en_url
            row["street_summary_hr"] = s_hr_sum
            row["street_wiki_url_hr"] = s_hr_url
            row["street_summary_en"] = s_en_sum
            row["street_wiki_url_en"] = s_en_url

        else:
            # No Wikidata match — search Croatian Wikipedia for the street
            title_hr = await wiki_search_title(client, f"{name} Zagreb", "hr")
            if title_hr:
                summary, url = await wiki_summary(client, title_hr, "hr")
                row["street_summary_hr"] = summary
                row["street_wiki_url_hr"] = url

        # SQLite writes must be on the main thread (connection is not thread-safe)
        upsert(conn, row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _run(names: list[str], wd_index: dict, conn: sqlite3.Connection):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        tasks = [
            process_street(name, wd_index.get(name), client, semaphore, conn)
            for name in names
        ]
        for coro in atqdm.as_completed(tasks, desc="Fetching wiki data", total=len(tasks)):
            await coro


def main(names: list[str] | None = None):
    DATA_DIR.mkdir(exist_ok=True)

    if names is None:
        names_path = DATA_DIR / "street_names.json"
        if not names_path.exists():
            raise FileNotFoundError("Run fetch_streets.py first")
        with open(names_path, encoding="utf-8") as f:
            names = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM street_wiki").fetchall()
    }
    names_todo = [n for n in names if n not in existing]
    print(f"Streets: {len(names)} total, {len(existing)} in DB, {len(names_todo)} to fetch")

    wd_rows = fetch_wikidata()
    wd_index = build_wikidata_index(wd_rows)
    matched = sum(1 for n in names if n in wd_index)
    print(f"Wikidata matches: {matched}/{len(names)}")

    if names_todo:
        asyncio.run(_run(names_todo, wd_index, conn))

    conn.close()
    _print_stats()


def _print_stats():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM street_wiki").fetchone()[0]
    with_person = conn.execute(
        "SELECT COUNT(*) FROM street_wiki WHERE named_after_name IS NOT NULL"
    ).fetchone()[0]
    with_street_wiki = conn.execute(
        "SELECT COUNT(*) FROM street_wiki "
        "WHERE street_wiki_url_hr IS NOT NULL OR street_wiki_url_en IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    print(f"\nDB stats:")
    print(f"  Total streets:           {total}")
    print(f"  With named-after info:   {with_person}")
    print(f"  With street wiki page:   {with_street_wiki}")


if __name__ == "__main__":
    main()
