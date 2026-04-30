"""
GIS Pipeline — Step 5 & 6: Geographic Entity Extraction and Geocoding
======================================================================

University of Chicago Dissertation Spatialization Project
Author: McGallian Data Viz Fellowship 2025

This script extracts the geographic focus of each dissertation from its
text metadata and (optionally) geocodes those locations to lat/lon.

Step 5 — Geographic entity extraction
--------------------------------------
Source text fields (concatenated per record):
  - Title
  - Subject Terms  (parsed from ProQuest list string)
  - Paper Keywords (parsed from ProQuest list string)
  - Class Terms    (parsed from ProQuest list string)

Method: spaCy Named Entity Recognition (en_core_web_lg)
  - Entity type: GPE (countries, cities, states, regions)
  - Entities are deduplicated and ranked by frequency within the record
  - primary_geo: highest-frequency GPE; if tie, preference given to
    entities appearing in the Title

Output columns:
  geo_entities  — semicolon-separated list of all unique GPE entities found
  primary_geo   — single best-guess geographic focus of the dissertation

Step 6 — Geocoding (optional, pass --geocode flag)
--------------------------------------
Geocoding provider: Nominatim (OpenStreetMap)
  - Rate limited to 1 request/second per Nominatim usage policy
  - Results cached in GIS/processed/geocode_cache.json to avoid
    redundant API calls on reruns
  - Only unique primary_geo values are geocoded (not every record)

Output columns:
  geo_lat  — latitude of primary_geo
  geo_lon  — longitude of primary_geo

Usage
------
  # Step 5 only (fast, no network):
  python3 GIS/geo_ripper.py

  # Steps 5 + 6 (slow on first run, fast on reruns via cache):
  python3 GIS/geo_ripper.py --geocode
"""

import ast
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import spacy

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT    = Path(__file__).resolve().parent.parent
GIS_OUT = Path(__file__).resolve().parent / "processed"

IN_CSV       = GIS_OUT / "dissertations_with_departments.csv"
OUT_CSV      = GIS_OUT / "dissertations_geo_enriched.csv"
GEOCODE_CACHE = GIS_OUT / "geocode_cache.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_list_field(raw):
    """
    Parse ProQuest list-string fields like "['Brazil', 'Urban planning']"
    into a plain text string suitable for NER.
    Returns empty string on failure.
    """
    if not raw or not raw.strip():
        return ""
    raw = raw.strip()
    if raw.startswith("["):
        try:
            items = ast.literal_eval(raw)
            return " ".join(str(i) for i in items if i)
        except Exception:
            return raw
    return raw


def build_text(row):
    """
    Concatenate all relevant text fields for a single dissertation row.
    Returns (full_text, title_text) so we can give title priority in
    primary_geo selection.
    """
    title    = row.get("Title", "") or ""
    subjects = parse_list_field(row.get("Subject Terms", "") or "")
    keywords = parse_list_field(row.get("Paper Keywords", "") or "")
    classes  = parse_list_field(row.get("Class Terms", "") or "")
    full = " ".join(filter(None, [title, subjects, keywords, classes]))
    return full, title


def extract_gpe_entities(doc, title_text):
    """
    Extract GPE entities from a spaCy Doc.
    Returns (geo_entities_str, primary_geo).
      geo_entities_str: semicolon-separated unique GPEs
      primary_geo: highest-frequency GPE, title-preferred on ties
    """
    gpes = [ent.text.strip() for ent in doc.ents if ent.label_ == "GPE"]

    if not gpes:
        return "", ""

    counts = Counter(gpes)
    unique = list(counts.keys())

    # Determine primary: highest count; break ties by title presence
    title_lower = title_text.lower()

    def rank_key(g):
        in_title = 1 if g.lower() in title_lower else 0
        return (counts[g], in_title)

    primary = max(unique, key=rank_key)
    geo_str  = "; ".join(sorted(unique, key=lambda g: -counts[g]))

    return geo_str, primary


# ---------------------------------------------------------------------------
# Geocoding (Step 6)
# ---------------------------------------------------------------------------

def load_cache(path):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def geocode_location(name, cache):
    """
    Geocode a place name using Nominatim.
    Returns (lat, lon) or (None, None).
    Respects 1-req/sec rate limit. Reads from / writes to cache.
    """
    if name in cache:
        result = cache[name]
        return result.get("lat"), result.get("lon")

    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError

    geolocator = Nominatim(
        user_agent="uchicago-dissertation-gis/1.0 (mcgallian@uchicago.edu)"
    )
    time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
    try:
        location = geolocator.geocode(name, timeout=10)
        if location:
            lat, lon = str(location.latitude), str(location.longitude)
        else:
            lat, lon = "", ""
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"  [WARN] Geocoding failed for {repr(name)}: {e}")
        lat, lon = "", ""

    cache[name] = {"lat": lat, "lon": lon}
    return lat, lon


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(do_geocode=False):
    print("=" * 70)
    print("UChicago Dissertation GIS Pipeline — Steps 5 & 6")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    print(f"\n[LOAD] Reading {IN_CSV.name}...")
    with open(IN_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"  {len(rows):,} records loaded")

    # ------------------------------------------------------------------
    # Step 5 — NER geographic extraction
    # ------------------------------------------------------------------
    print("\n[STEP 5] Loading spaCy model (en_core_web_lg)...")
    nlp = spacy.load("en_core_web_lg")
    # Disable components we don't need — speeds up pipeline significantly
    nlp.select_pipes(enable=["tok2vec", "ner"])
    print("  Model loaded. Running NER on all records...")

    texts_and_titles = [build_text(row) for row in rows]
    texts  = [t[0] for t in texts_and_titles]
    titles = [t[1] for t in texts_and_titles]

    geo_entities_list = []
    primary_geo_list  = []

    # Process in batches for efficiency
    batch_size = 256
    total = len(texts)
    processed = 0

    for doc, title in zip(nlp.pipe(texts, batch_size=batch_size), titles):
        geo_str, primary = extract_gpe_entities(doc, title)
        geo_entities_list.append(geo_str)
        primary_geo_list.append(primary)
        processed += 1
        if processed % 5000 == 0:
            print(f"  {processed:,}/{total:,} records processed...")

    print(f"  {processed:,}/{total:,} records processed.")

    # Stats
    with_geo = sum(1 for g in primary_geo_list if g)
    print(f"\n  Records with ≥1 GPE entity: {with_geo:,} "
          f"({with_geo/total*100:.1f}%)")
    print(f"  Records with no GPE found:  {total - with_geo:,}")

    # Top primary_geo values
    top = Counter(g for g in primary_geo_list if g).most_common(15)
    print("\n  Top 15 primary_geo values:")
    for place, count in top:
        print(f"    {count:5d}  {place}")

    # Attach to rows
    for row, geo_str, primary in zip(rows, geo_entities_list, primary_geo_list):
        row["geo_entities"] = geo_str
        row["primary_geo"]  = primary
        row["geo_lat"]      = ""
        row["geo_lon"]      = ""

    # ------------------------------------------------------------------
    # Step 6 — Geocoding (optional)
    # ------------------------------------------------------------------
    if do_geocode:
        print("\n[STEP 6] Geocoding primary_geo values via Nominatim...")
        cache = load_cache(GEOCODE_CACHE)

        unique_locs = sorted(set(p for p in primary_geo_list if p))
        cached      = sum(1 for u in unique_locs if u in cache)
        to_fetch    = len(unique_locs) - cached
        print(f"  Unique locations: {len(unique_locs)}")
        print(f"  Already cached:   {cached}")
        print(f"  To fetch:         {to_fetch} "
              f"(est. {to_fetch * 1.1 / 60:.0f} min at 1 req/sec)")

        geo_lookup = {}
        for i, loc in enumerate(unique_locs):
            lat, lon = geocode_location(loc, cache)
            geo_lookup[loc] = (lat, lon)
            if (i + 1) % 50 == 0:
                save_cache(GEOCODE_CACHE, cache)
                print(f"  {i+1}/{len(unique_locs)} locations geocoded...")

        save_cache(GEOCODE_CACHE, cache)
        print(f"  Cache saved → {GEOCODE_CACHE}")

        # Attach lat/lon
        resolved = 0
        for row in rows:
            primary = row["primary_geo"]
            if primary and primary in geo_lookup:
                lat, lon = geo_lookup[primary]
                row["geo_lat"] = lat
                row["geo_lon"] = lon
                if lat:
                    resolved += 1
        print(f"  Records with lat/lon: {resolved:,}")
    else:
        print("\n[STEP 6] Skipped (pass --geocode to enable geocoding)")

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------
    print(f"\n[WRITE] Writing {OUT_CSV.name}...")
    original_fields = list(rows[0].keys())
    new_fields = ["geo_entities", "primary_geo", "geo_lat", "geo_lon"]
    out_fields = [f for f in original_fields
                  if f not in new_fields] + new_fields

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: {OUT_CSV}")

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    print(f"\nOutputs in: {GIS_OUT}/")
    print(f"  dissertations_geo_enriched.csv  — {len(rows):,} rows")
    print(f"  Records with geographic focus:  {with_geo:,} ({with_geo/total*100:.1f}%)")
    print()


if __name__ == "__main__":
    do_geocode = "--geocode" in sys.argv
    run(do_geocode=do_geocode)
