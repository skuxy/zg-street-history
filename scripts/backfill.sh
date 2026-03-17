#!/usr/bin/env bash
# Backfill missing Wikipedia summaries, commit DB if anything changed, push.
# Safe to run repeatedly — the Python script skips streets that already have data.
set -e
cd "$(dirname "$0")/.."

echo "[$(date)] Starting backfill..."
.venv/bin/python data_pipeline/fill_missing_summaries.py

# Check if the DB actually changed
if git diff --quiet data/streets.db; then
    echo "[$(date)] No new data — nothing to commit."
    exit 0
fi

echo "[$(date)] DB updated, committing and pushing..."
git add data/streets.db
git commit -m "Backfill Wikipedia summaries ($(date '+%Y-%m-%d'))"
git push
echo "[$(date)] Done."
