#!/usr/bin/env bash
# Run on the VM via cron. Pulls latest DB from git, reloads service if changed.
set -e
cd ~/zg-street-history

BEFORE=$(git rev-parse HEAD)
git pull --quiet
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
    echo "[$(date)] New commits — reloading service..."
    sudo systemctl reload zg-street-history
    echo "[$(date)] Done."
else
    echo "[$(date)] Already up to date."
fi
