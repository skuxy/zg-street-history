"""
Enrich streets.db using Wikidata QIDs already embedded in the OSM GeoJSON.

OSM tag  name:etymology:wikidata  → QID of the person/thing the street is named after
OSM tag  wikidata                 → QID of the street itself

Uses wbgetentities API (50 items per request) — no SPARQL, no rate-limiting.
Safe to run multiple times (resumes from existing DB state).

Usage:
    python enrich_wikidata.py
"""

import asyncio
import json
import sqlite3
import urllib.parse
from collections import defaultdict
from pathlib import Path

import httpx
from tqdm import tqdm
from tqdm.asyncio import tqdm as atqdm

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "streets.db"
GEOJSON_PATH = DATA_DIR / "streets.geojson"

HEADERS = {
    "User-Agent": (
        "zg-street-history/1.0 "
        "(https://github.com/zg-street-history; educational project) "
        "python-httpx/0.27"
    )
}

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKI_REST_HR = "https://hr.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_REST_EN = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

CHUNK = 50     # wbgetentities max per request
CONCURRENCY = 12


# ---------------------------------------------------------------------------
# Step 1: Extract QIDs from GeoJSON
# ---------------------------------------------------------------------------

def extract_qids(geojson_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """
    Returns:
        etymology_map: { street_name → etymology_QID }   (named-after person)
        street_map:    { street_name → street_QID }       (the street itself)
    Both are deduplicated by name (first value wins).
    """
    with open(geojson_path, encoding="utf-8") as f:
        gj = json.load(f)

    etymology_map: dict[str, str] = {}
    street_map: dict[str, str] = {}

    for feat in gj["features"]:
        props = feat["properties"]
        name = props.get("name")
        if not name:
            continue

        et_qid = props.get("name_etymology_wikidata")
        if et_qid and name not in etymology_map:
            etymology_map[name] = et_qid.strip()

        st_qid = props.get("wikidata")
        if st_qid and name not in street_map:
            street_map[name] = st_qid.strip()

    return etymology_map, street_map


# ---------------------------------------------------------------------------
# Step 2: Fetch Wikidata entity info in bulk
# ---------------------------------------------------------------------------

def chunked(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def fetch_entities(qids: list[str]) -> dict[str, dict]:
    """Fetch wikidata entity data for a list of QIDs. Returns {QID: entity_data}."""
    results = {}
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        for batch in tqdm(list(chunked(qids, CHUNK)), desc="Wikidata entities", unit="batch"):
            try:
                r = client.get(
                    WIKIDATA_API,
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(batch),
                        "props": "labels|descriptions|sitelinks",
                        "languages": "hr|en",
                        "sitefilter": "hrwiki|enwiki",
                        "format": "json",
                    },
                )
                r.raise_for_status()
                entities = r.json().get("entities", {})
                results.update(entities)
            except Exception as e:
                print(f"  Batch failed ({batch[0]}…): {e}")
    return results


def parse_entity(entity: dict) -> dict:
    """Extract label, description, HR/EN wikipedia article title."""
    labels = entity.get("labels", {})
    descs = entity.get("descriptions", {})
    sitelinks = entity.get("sitelinks", {})

    label_hr = labels.get("hr", {}).get("value")
    label_en = labels.get("en", {}).get("value")
    desc_hr = descs.get("hr", {}).get("value")
    desc_en = descs.get("en", {}).get("value")

    hr_title = sitelinks.get("hrwiki", {}).get("title")
    en_title = sitelinks.get("enwiki", {}).get("title")

    return {
        "label": label_hr or label_en,
        "description": desc_hr or desc_en,
        "hr_title": hr_title,
        "en_title": en_title,
    }


# ---------------------------------------------------------------------------
# Step 3: Fetch Wikipedia summaries (async)
# ---------------------------------------------------------------------------

async def wiki_summary(
    client: httpx.AsyncClient, title: str, lang: str
) -> tuple[str | None, str | None]:
    if not title:
        return None, None
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (WIKI_REST_HR if lang == "hr" else WIKI_REST_EN).format(title=encoded)
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        d = r.json()
        summary = d.get("extract", "").strip() or None
        page_url = d.get("content_urls", {}).get("desktop", {}).get("page") or None
        return summary, page_url
    except Exception:
        return None, None


async def fetch_all_summaries(
    jobs: list[tuple[str, str, str]]  # (key, title, lang)
) -> dict[str, tuple[str | None, str | None]]:
    """Returns {key: (summary, url)}."""
    results: dict[str, tuple] = {}
    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY)

    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        async def one(key, title, lang):
            async with sem:
                s, u = await wiki_summary(client, title, lang)
                results[key] = (s, u)

        tasks = [one(*j) for j in jobs]
        for coro in atqdm.as_completed(tasks, total=len(tasks), desc="Wikipedia summaries"):
            await coro

    return results


# ---------------------------------------------------------------------------
# Step 4: Write to DB
# ---------------------------------------------------------------------------

def write_to_db(
    conn: sqlite3.Connection,
    name: str,
    entity_type: str,   # "person" or "street"
    info: dict,
    summaries: dict,
):
    if entity_type == "person":
        hr = summaries.get(f"person_hr_{name}", (None, None))
        en = summaries.get(f"person_en_{name}", (None, None))
        conn.execute(
            """UPDATE street_wiki SET
                named_after_qid         = :qid,
                named_after_name        = :label,
                named_after_description = :desc,
                named_after_summary_hr  = :sum_hr,
                named_after_wiki_url_hr = :url_hr,
                named_after_summary_en  = :sum_en,
                named_after_wiki_url_en = :url_en
               WHERE name = :name""",
            {
                "qid": info.get("qid"),
                "label": info["label"],
                "desc": info["description"],
                "sum_hr": hr[0],
                "url_hr": hr[1],
                "sum_en": en[0],
                "url_en": en[1],
                "name": name,
            },
        )
    else:  # street
        hr = summaries.get(f"street_hr_{name}", (None, None))
        en = summaries.get(f"street_en_{name}", (None, None))
        # Only update if we have data (don't overwrite existing non-null values with null)
        if hr[1] or en[1]:
            conn.execute(
                """UPDATE street_wiki SET
                    street_summary_hr  = COALESCE(:sum_hr, street_summary_hr),
                    street_wiki_url_hr = COALESCE(:url_hr, street_wiki_url_hr),
                    street_summary_en  = COALESCE(:sum_en, street_summary_en),
                    street_wiki_url_en = COALESCE(:url_en, street_wiki_url_en)
                   WHERE name = :name""",
                {
                    "sum_hr": hr[0],
                    "url_hr": hr[1],
                    "sum_en": en[0],
                    "url_en": en[1],
                    "name": name,
                },
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not DB_PATH.exists():
        print("streets.db not found — run fetch_wiki.py (or build_dataset.py) first")
        return

    print("Extracting QIDs from OSM GeoJSON...")
    etymology_map, street_map = extract_qids(GEOJSON_PATH)
    print(f"  Streets with etymology QID: {len(etymology_map)}")
    print(f"  Streets with street QID:    {len(street_map)}")

    # Gather all unique QIDs to fetch
    all_qids = list(set(etymology_map.values()) | set(street_map.values()))
    print(f"  Unique Wikidata QIDs to fetch: {len(all_qids)}")

    # Fetch entity data
    entities = fetch_entities(all_qids)
    parsed: dict[str, dict] = {qid: parse_entity(e) for qid, e in entities.items()}

    # Build Wikipedia fetch jobs
    jobs: list[tuple[str, str, str]] = []   # (key, title, lang)
    for name, qid in etymology_map.items():
        info = parsed.get(qid, {})
        if info.get("hr_title"):
            jobs.append((f"person_hr_{name}", info["hr_title"], "hr"))
        if info.get("en_title"):
            jobs.append((f"person_en_{name}", info["en_title"], "en"))

    for name, qid in street_map.items():
        info = parsed.get(qid, {})
        if info.get("hr_title"):
            jobs.append((f"street_hr_{name}", info["hr_title"], "hr"))
        if info.get("en_title"):
            jobs.append((f"street_en_{name}", info["en_title"], "en"))

    print(f"\nFetching {len(jobs)} Wikipedia summaries...")
    summaries = asyncio.run(fetch_all_summaries(jobs))

    # Write to DB
    conn = sqlite3.connect(DB_PATH)
    print("\nWriting to DB...")
    updated = 0
    for name, qid in tqdm(etymology_map.items(), desc="Writing person data"):
        info = parsed.get(qid, {})
        if info.get("label"):
            info["qid"] = qid
            write_to_db(conn, name, "person", info, summaries)
            updated += 1
    for name, qid in tqdm(street_map.items(), desc="Writing street data"):
        info = parsed.get(qid, {})
        if info.get("hr_title") or info.get("en_title"):
            # Also store the street's own wikidata QID
            conn.execute("UPDATE street_wiki SET wikidata_qid = ? WHERE name = ?", (qid, name))
            write_to_db(conn, name, "street", info, summaries)
            updated += 1

    conn.commit()
    conn.close()
    print(f"Updated {updated} records")

    _stats()


def _stats():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM street_wiki").fetchone()[0]
    with_person = conn.execute(
        "SELECT COUNT(*) FROM street_wiki WHERE named_after_name IS NOT NULL"
    ).fetchone()[0]
    with_street_wiki = conn.execute(
        "SELECT COUNT(*) FROM street_wiki "
        "WHERE street_wiki_url_hr IS NOT NULL OR street_wiki_url_en IS NOT NULL"
    ).fetchone()[0]
    print(f"\nFinal DB stats:")
    print(f"  Total streets:           {total}")
    print(f"  With named-after info:   {with_person}")
    print(f"  With street wiki page:   {with_street_wiki}")

    print("\nSample enriched streets:")
    rows = conn.execute(
        "SELECT name, named_after_name, named_after_description "
        "FROM street_wiki WHERE named_after_name IS NOT NULL LIMIT 8"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]!r:40s} → {r[1]!r} ({r[2] or ''})")
    conn.close()


if __name__ == "__main__":
    main()
