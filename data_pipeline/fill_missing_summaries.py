"""
For streets that have named_after_name but no Wikipedia URL,
try to fetch a summary directly by looking up the person's name on Wikipedia.

Safe to run multiple times. Uses async for speed.

Usage:
    python fill_missing_summaries.py
"""

import asyncio
import sqlite3
import urllib.parse
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm as atqdm

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "streets.db"

HEADERS = {
    "User-Agent": (
        "zg-street-history/1.0 "
        "(https://github.com/zg-street-history; educational project) "
        "python-httpx/0.27"
    )
}

CONCURRENCY = 16


async def wiki_summary(
    client: httpx.AsyncClient, title: str, lang: str
) -> tuple[str | None, str | None]:
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    try:
        r = await client.get(url, timeout=12, follow_redirects=True)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        d = r.json()
        summary = d.get("extract", "").strip() or None
        page_url = d.get("content_urls", {}).get("desktop", {}).get("page") or None
        return summary, page_url
    except Exception:
        return None, None


async def main():
    conn = sqlite3.connect(DB_PATH)

    # Streets with person name but missing both wiki URLs
    rows = conn.execute("""
        SELECT name, named_after_name
        FROM street_wiki
        WHERE named_after_name IS NOT NULL
          AND named_after_wiki_url_hr IS NULL
          AND named_after_wiki_url_en IS NULL
    """).fetchall()

    print(f"Streets with person name but no wiki URL: {len(rows)}")

    results: dict[str, tuple] = {}   # name → (hr_sum, hr_url, en_sum, en_url)

    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY)

    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        async def fetch_one(street_name: str, person_name: str):
            async with sem:
                # Try Croatian Wikipedia first, then English
                hr_sum, hr_url = await wiki_summary(client, person_name, "hr")
                en_sum, en_url = await wiki_summary(client, person_name, "en")
                results[street_name] = (hr_sum, hr_url, en_sum, en_url)

        tasks = [fetch_one(row[0], row[1]) for row in rows]
        for coro in atqdm.as_completed(tasks, total=len(tasks), desc="Looking up people"):
            await coro

    # Write results
    updated = 0
    for street_name, (hr_sum, hr_url, en_sum, en_url) in results.items():
        if hr_url or en_url:
            conn.execute("""
                UPDATE street_wiki SET
                    named_after_summary_hr  = COALESCE(:hr_sum, named_after_summary_hr),
                    named_after_wiki_url_hr = COALESCE(:hr_url, named_after_wiki_url_hr),
                    named_after_summary_en  = COALESCE(:en_sum, named_after_summary_en),
                    named_after_wiki_url_en = COALESCE(:en_url, named_after_wiki_url_en)
                WHERE name = :name
            """, {"hr_sum": hr_sum, "hr_url": hr_url, "en_sum": en_sum, "en_url": en_url, "name": street_name})
            updated += 1

    conn.commit()
    conn.close()
    print(f"Updated {updated} streets with Wikipedia URLs")

    # Stats
    conn = sqlite3.connect(DB_PATH)
    with_url = conn.execute(
        "SELECT COUNT(*) FROM street_wiki WHERE named_after_wiki_url_hr IS NOT NULL OR named_after_wiki_url_en IS NOT NULL"
    ).fetchone()[0]
    print(f"Streets with person Wikipedia URL: {with_url}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
