# Zagreb Street History

An interactive map of Zagreb that shows the history behind street names — who a street is named after, their Wikipedia summary, and links to Croatian and English Wikipedia.

Built with MapLibre GL JS (frontend) and Python/FastAPI (backend), using OpenStreetMap data and Wikipedia/Wikidata APIs.

## Demo

Hover over any street to highlight it. Click to open a side panel with:
- Who the street is named after (name + description)
- Wikipedia summary in Croatian and/or English
- Links to Croatian and English Wikipedia
- Wikidata badge linking to the structured data entry
- Wikipedia article about the street itself (where it exists)

## Data

| Metric | Count |
|--------|-------|
| Street segments (OSM ways) | 14,114 |
| Unique street names | 4,407 |
| Streets with named-after info | 575 |
| Streets with Wikipedia article | 40 |

Data sources:
- **Street geometries**: OpenStreetMap via Overpass API
- **Named-after links**: OSM `name:etymology:wikidata` tags → Wikidata `wbgetentities` API
- **Summaries**: Wikipedia REST API (`/api/rest_v1/page/summary/{title}`)
- **Stored in**: SQLite (`data/streets.db`)

## Project Structure

```
zg-street-history/
├── data_pipeline/          # One-time dataset build scripts (Python)
│   ├── fetch_streets.py    # OSM Overpass → data/streets.geojson
│   ├── fetch_wiki.py       # Wikipedia search fallback → data/streets.db
│   ├── enrich_wikidata.py  # Wikidata QIDs → person info + wiki URLs
│   ├── fill_missing_summaries.py  # Fill gaps by person name lookup
│   ├── build_dataset.py    # Orchestrator (runs all steps)
│   └── requirements.txt
├── backend/
│   ├── main.py             # FastAPI app
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/map.js           # MapLibre GL JS map + panel logic
├── data/                   # Git-ignored
│   ├── streets.geojson     # Street geometries (6.1MB, ~1.1MB gzipped)
│   └── streets.db          # SQLite with Wikipedia data
└── run.sh                  # Start the backend
```

## Setup

### Requirements

- Python 3.11+

### Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r data_pipeline/requirements.txt
.venv/bin/pip install -r backend/requirements.txt
```

### Build the dataset (one time)

```bash
.venv/bin/python data_pipeline/build_dataset.py
```

This runs two steps:
1. Fetches all named streets in Zagreb from OSM (~14k ways, ~4400 unique names)
2. Fetches Wikipedia summaries and Wikidata named-after data for each street

The Wikipedia/Wikidata APIs have rate limits — if you get 403 errors midway, wait an hour and re-run. The pipeline resumes from where it left off (already-fetched streets are skipped).

To top up missing summaries after a rate-limit pause:

```bash
.venv/bin/python data_pipeline/fill_missing_summaries.py
```

### Run the backend

```bash
./run.sh
# or manually:
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload
```

Then open **http://localhost:8765** in your browser.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /` | Serves the frontend |
| `GET /api/streets` | Streets GeoJSON (gzip compressed, ETag cached) |
| `GET /api/wiki?name=<street>` | Wikipedia data for a street name |
| `GET /health` | DB stats and status |

## Deployment

The frontend and backend are deployed separately, mirroring the pub-shade-map setup.

### Frontend → GitHub Pages

Automatically deployed via GitHub Actions on every push to `main` that touches `frontend/`.

1. Enable GitHub Pages in repo settings → source: GitHub Actions
2. After the first deploy, set `BACKEND_URL` in `frontend/js/config.js` to your Render URL
3. Push → Actions redeploys automatically

### Backend → Render

1. Connect the repo to [Render](https://render.com)
2. Render auto-detects `render.yaml` and creates the service
3. The pre-built data (`data/streets.geojson`, `data/streets.db`) is committed to the repo — no disk mount or startup fetch needed
4. Copy the Render service URL into `frontend/js/config.js` as `BACKEND_URL`

### Optional: custom domain via DuckDNS

Point a DuckDNS subdomain at your Render service URL and use that as `BACKEND_URL`.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Map | [MapLibre GL JS](https://maplibre.org/) 4.7 |
| Base tiles | [OpenFreeMap](https://openfreemap.org/) (free, no API key) |
| Frontend | Vanilla JS / HTML / CSS |
| Backend | Python, FastAPI, uvicorn |
| Database | SQLite |
| Street data | OpenStreetMap (Overpass API) |
| Person data | Wikidata (`wbgetentities` API) |
| Summaries | Wikipedia REST API |
