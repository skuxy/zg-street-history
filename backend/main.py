"""
FastAPI backend for Zagreb Street History.

Serves:
  GET /                     → frontend index.html
  GET /api/wiki?name=<str>  → wiki data for a street name (from SQLite)
  GET /api/streets          → streets.geojson (with ETag caching)
  GET /health               → DB stats
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = DATA_DIR / "streets.db"
GEOJSON_PATH = DATA_DIR / "streets.geojson"

app = FastAPI(title="Zagreb Street History")

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# GeoJSON with ETag
# ---------------------------------------------------------------------------

_geojson_cache: bytes | None = None
_geojson_etag: str | None = None


def _load_geojson() -> tuple[bytes, str]:
    global _geojson_cache, _geojson_etag
    if _geojson_cache is None:
        if not GEOJSON_PATH.exists():
            raise FileNotFoundError("streets.geojson not found — run the data pipeline first")
        _geojson_cache = GEOJSON_PATH.read_bytes()
        _geojson_etag = hashlib.md5(_geojson_cache).hexdigest()
    return _geojson_cache, _geojson_etag


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/streets")
async def get_streets(request: Request):
    try:
        data, etag = _load_geojson()
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    return Response(
        content=data,
        media_type="application/geo+json",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=3600",
        },
    )


@app.get("/api/wiki")
async def get_wiki(name: str):
    if not name or len(name) > 200:
        raise HTTPException(400, "Invalid name")

    if not DB_PATH.exists():
        raise HTTPException(503, "Database not found — run the data pipeline first")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM street_wiki WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return JSONResponse({"found": False, "name": name})

    return JSONResponse({
        "found": True,
        "name": name,
        "wikidata_qid": row["wikidata_qid"],
        # Named after
        "named_after": {
            "qid": row["named_after_qid"],
            "name": row["named_after_name"],
            "description": row["named_after_description"],
            "summary_hr": row["named_after_summary_hr"],
            "summary_en": row["named_after_summary_en"],
            "wiki_url_hr": row["named_after_wiki_url_hr"],
            "wiki_url_en": row["named_after_wiki_url_en"],
            "image_url": row["named_after_image_url"],
        } if row["named_after_name"] else None,
        # Street article
        "street_article": {
            "summary_hr": row["street_summary_hr"],
            "summary_en": row["street_summary_en"],
            "wiki_url_hr": row["street_wiki_url_hr"],
            "wiki_url_en": row["street_wiki_url_en"],
        } if (row["street_wiki_url_hr"] or row["street_wiki_url_en"]) else None,
    })


@app.get("/health")
async def health():
    stats = {"geojson_exists": GEOJSON_PATH.exists(), "db_exists": DB_PATH.exists()}
    if DB_PATH.exists():
        conn = get_db()
        stats["total_streets"] = conn.execute("SELECT COUNT(*) FROM street_wiki").fetchone()[0]
        stats["with_named_after"] = conn.execute(
            "SELECT COUNT(*) FROM street_wiki WHERE named_after_name IS NOT NULL"
        ).fetchone()[0]
        stats["with_street_article"] = conn.execute(
            "SELECT COUNT(*) FROM street_wiki WHERE street_wiki_url_hr IS NOT NULL OR street_wiki_url_en IS NOT NULL"
        ).fetchone()[0]
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
