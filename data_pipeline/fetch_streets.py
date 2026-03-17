"""
Fetch Zagreb named streets from OSM via Overpass API.
Outputs data/streets.geojson — lean file (geometry + name + osm_id only).
"""

import json
import time
import httpx
from pathlib import Path

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Zagreb bounding box: south, west, north, east
ZAGREB_BBOX = "45.72,15.85,45.87,16.13"

OVERPASS_QUERY = f"""
[out:json][timeout:90];
(
  way["highway"]["name"]({ZAGREB_BBOX});
);
out geom;
"""

HIGHWAY_EXCLUDE = {"motorway", "motorway_link", "trunk", "trunk_link"}


def fetch_from_overpass(query: str) -> dict:
    for url in OVERPASS_ENDPOINTS:
        try:
            print(f"  Trying {url} ...")
            r = httpx.post(url, data={"data": query}, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  Failed ({e}), trying next mirror...")
            time.sleep(2)
    raise RuntimeError("All Overpass mirrors failed")


def ways_to_geojson(elements: list) -> dict:
    features = []
    for el in elements:
        if el.get("type") != "way":
            continue
        name = el.get("tags", {}).get("name")
        highway = el.get("tags", {}).get("highway", "")
        if not name or highway in HIGHWAY_EXCLUDE:
            continue

        geometry = el.get("geometry", [])
        if len(geometry) < 2:
            continue

        coords = [[pt["lon"], pt["lat"]] for pt in geometry]

        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": str(el["id"]),
                "name": name,
                "highway": highway,
                # Wikidata/Wikipedia tags embedded in OSM — gold source for linking
                "wikidata": tags.get("wikidata") or tags.get("subject:wikidata"),
                "wikipedia": tags.get("wikipedia"),
                "name_etymology_wikidata": tags.get("name:etymology:wikidata"),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        })

    return {"type": "FeatureCollection", "features": features}


def main():
    out_path = Path(__file__).parent.parent / "data" / "streets.geojson"
    out_path.parent.mkdir(exist_ok=True)

    print("Fetching Zagreb streets from OSM Overpass API...")
    raw = fetch_from_overpass(OVERPASS_QUERY)
    elements = raw.get("elements", [])
    print(f"  Got {len(elements)} elements from OSM")

    geojson = ways_to_geojson(elements)
    n = len(geojson["features"])
    print(f"  Converted to {n} GeoJSON features")

    # Collect unique names for the pipeline
    names = sorted({f["properties"]["name"] for f in geojson["features"]})
    print(f"  Unique street names: {len(names)}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"Saved → {out_path}")

    names_path = out_path.parent / "street_names.json"
    with open(names_path, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)
    print(f"Saved → {names_path}")

    return names


if __name__ == "__main__":
    main()
