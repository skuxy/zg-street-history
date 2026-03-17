#!/usr/bin/env bash
# Start the Zagreb Street History backend
cd "$(dirname "$0")"
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload
