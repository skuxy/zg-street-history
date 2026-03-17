"""
List streets that have named-after info but no Wikipedia article linked.
Outputs:
  - missing_wiki.md   — readable list with Wikidata links
  - missing_wiki.csv  — spreadsheet-friendly

Usage:
    python scripts/list_missing_wiki.py
"""

import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "streets.db"
OUT_DIR = Path(__file__).parent.parent

WIKIDATA_URL = "https://www.wikidata.org/wiki/{}"
HR_WIKI_NEW  = "https://hr.wikipedia.org/w/index.php?title={}&action=edit"
EN_WIKI_NEW  = "https://en.wikipedia.org/w/index.php?title={}&action=edit"


def main():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT name, named_after_name, named_after_description, named_after_qid
        FROM street_wiki
        WHERE named_after_name IS NOT NULL
          AND named_after_wiki_url_hr IS NULL
          AND named_after_wiki_url_en IS NULL
        ORDER BY named_after_name
    """).fetchall()
    conn.close()

    print(f"Streets with named-after info but no Wikipedia article: {len(rows)}")

    # ── Markdown ────────────────────────────────────────────────────────────
    md_path = OUT_DIR / "missing_wiki.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Zagreb Streets Missing Wikipedia Articles\n\n")
        f.write(f"{len(rows)} streets have named-after info but no linked Wikipedia article.\n\n")
        f.write("| Street | Named after | Description | Wikidata | Create HR article | Create EN article |\n")
        f.write("|--------|-------------|-------------|----------|-------------------|-------------------|\n")

        for street, person, desc, qid in rows:
            wd_link = f"[{qid}]({WIKIDATA_URL.format(qid)})" if qid else "—"
            person_slug = person.replace(" ", "_")
            hr_link = f"[create]({HR_WIKI_NEW.format(person_slug)})"
            en_link = f"[create]({EN_WIKI_NEW.format(person_slug)})"
            f.write(f"| {street} | {person} | {desc or '—'} | {wd_link} | {hr_link} | {en_link} |\n")

    print(f"Markdown → {md_path}")

    # ── CSV ─────────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "missing_wiki.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Street", "Named after", "Description", "Wikidata URL", "HR Wikipedia (create)", "EN Wikipedia (create)"])
        for street, person, desc, qid in rows:
            wd_url = WIKIDATA_URL.format(qid) if qid else ""
            person_slug = person.replace(" ", "_")
            writer.writerow([
                street,
                person,
                desc or "",
                wd_url,
                HR_WIKI_NEW.format(person_slug),
                EN_WIKI_NEW.format(person_slug),
            ])

    print(f"CSV      → {csv_path}")


if __name__ == "__main__":
    main()
