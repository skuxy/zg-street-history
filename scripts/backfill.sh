#!/usr/bin/env bash
# Backfill missing Wikipedia summaries, commit DB if anything changed, push.
# Safe to run repeatedly — the Python script skips streets that already have data.
set -e
cd "$(dirname "$0")/.."

echo "[$(date)] Starting backfill..."

# Count before
BEFORE=$(.venv/bin/python - << 'EOF'
import sqlite3
conn = sqlite3.connect("data/streets.db")
n = conn.execute("SELECT COUNT(*) FROM street_wiki WHERE named_after_wiki_url_hr IS NOT NULL OR named_after_wiki_url_en IS NOT NULL").fetchone()[0]
print(n)
conn.close()
EOF
)

.venv/bin/python data_pipeline/fill_missing_summaries.py

# Count after
AFTER=$(.venv/bin/python - << 'EOF'
import sqlite3
conn = sqlite3.connect("data/streets.db")
n = conn.execute("SELECT COUNT(*) FROM street_wiki WHERE named_after_wiki_url_hr IS NOT NULL OR named_after_wiki_url_en IS NOT NULL").fetchone()[0]
print(n)
conn.close()
EOF
)

ADDED=$((AFTER - BEFORE))
echo "[$(date)] Streets with wiki URLs: $BEFORE → $AFTER (+$ADDED)"

if git diff --quiet data/streets.db; then
    echo "[$(date)] No new data — nothing to commit."
    exit 0
fi

echo "[$(date)] DB updated, committing and pushing..."
git add data/streets.db
git commit -m "Backfill: +$ADDED streets with Wikipedia URLs (total $AFTER) — $(date '+%Y-%m-%d')"
git push
echo "[$(date)] Done."
