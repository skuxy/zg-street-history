"""
Master pipeline script. Run this once to build the full dataset.

Steps:
  1. fetch_streets  — OSM → data/streets.geojson + data/street_names.json
  2. fetch_wiki     — Wikidata + Wikipedia → data/streets.db

Usage:
  cd data_pipeline
  pip install -r requirements.txt
  python build_dataset.py

Options:
  --streets-only   Skip Wikipedia fetching
  --wiki-only      Skip OSM fetching (uses existing street_names.json)
"""

import argparse
import sys

import fetch_streets
import fetch_wiki


def main():
    parser = argparse.ArgumentParser(description="Build Zagreb street history dataset")
    parser.add_argument("--streets-only", action="store_true")
    parser.add_argument("--wiki-only", action="store_true")
    args = parser.parse_args()

    if not args.wiki_only:
        print("=" * 60)
        print("STEP 1: Fetching streets from OSM")
        print("=" * 60)
        fetch_streets.main()

    if not args.streets_only:
        print()
        print("=" * 60)
        print("STEP 2: Fetching Wikipedia/Wikidata info")
        print("=" * 60)
        fetch_wiki.main()

    print()
    print("Dataset build complete.")
    print("  data/streets.geojson  — street geometries")
    print("  data/streets.db       — Wikipedia info (SQLite)")
    print()
    print("Next: start the backend")
    print("  cd ../backend && pip install -r requirements.txt")
    print("  uvicorn main:app --reload")


if __name__ == "__main__":
    main()
