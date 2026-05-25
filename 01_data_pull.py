"""
01_data_pull.py
---------------
Downloads all raw datasets for the Pennsylvania Avenue corridor analysis.
Saves everything to data/raw/.

Data sources (all configured in config.yaml):
  - Crashes in DC       → DC Open Data ArcGIS REST API
  - Vision Zero Safety  → DC Open Data ArcGIS REST API
  - Bike Lanes          → DC Open Data ArcGIS REST API
  - Street Centerlines  → DC Open Data ArcGIS REST API
  - WMATA GTFS          → WMATA static GTFS ZIP (bus routes, stops, schedules)
  - AADT Counts         → DDOT via DC Open Data ArcGIS REST API  ← NEW

WHY THIS SCRIPT EXISTS:
  Keeping data pull separate from analysis means you can re-run any
  downstream script without hitting the API again. It also makes the
  data provenance explicit — you always know where each file came from.

ABOUT AADT (Average Annual Daily Traffic):
  AADT is how many vehicles use a road segment per day, averaged across
  a full year. It is the foundational metric for any lane removal analysis
  because it tells you: how much traffic are we moving through this lane
  right now? Without it, you can describe crash history but you cannot
  say whether removing a lane will create congestion.

  GOVERNANCE LIMITATION: DDOT collects AADT on a ~3-year cycle as part
  of federal HPMS reporting. The numbers in this dataset may be 1-3 years
  old. Do not use for current traffic conditions — use as a baseline order
  of magnitude only.
"""

import json
import pathlib
import requests
import time
import yaml
import zipfile
import io

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

RAW_DIR = pathlib.Path(__file__).parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

GTFS_DIR = RAW_DIR / "gtfs"
GTFS_DIR.mkdir(exist_ok=True)


# ── Helper: fetch one ArcGIS REST endpoint (handles pagination) ───────────────
def fetch_arcgis(name: str, url: str, extra_params: dict = None) -> dict:
    """
    Fetches all features from a DC Open Data ArcGIS REST endpoint.

    ArcGIS APIs return at most 1000 records per request, so we loop using
    resultOffset to page through all records.

    Args:
        name:        Human-readable name for log messages.
        url:         ArcGIS REST query endpoint URL.
        extra_params: Any additional query parameters (e.g. date filters).

    Returns:
        A GeoJSON FeatureCollection dict (all pages merged).
    """
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "geojson",
        "resultRecordCount": 1000,
        "resultOffset": 0,
    }
    if extra_params:
        params.update(extra_params)

    all_features = []
    page = 0

    while True:
        params["resultOffset"] = page * 1000
        print(f"  [{name}] Fetching page {page + 1} (offset {params['resultOffset']})...")

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ERROR fetching {name}: {e}")
            break
        except json.JSONDecodeError as e:
            print(f"  ERROR parsing JSON for {name}: {e}")
            break

        features = data.get("features", [])
        all_features.extend(features)
        print(f"  [{name}] Got {len(features)} features (total so far: {len(all_features)})")

        # If we got fewer than 1000, we've reached the last page
        if len(features) < 1000:
            break

        page += 1
        time.sleep(0.5)  # be polite to the API

    geojson = {
        "type": "FeatureCollection",
        "features": all_features
    }
    print(f"  [{name}] Done — {len(all_features)} total features.")
    return geojson


# ── 1. Crashes in DC ─────────────────────────────────────────────────────────
print("\n=== Pulling Crashes in DC ===")
# Only pull crashes from 2020 onward (configured in config.yaml)
crash_start = config["analysis"]["crash_start_date"]
crashes = fetch_arcgis(
    name="Crashes",
    url=config["data_sources"]["crashes_api"],
    extra_params={"where": f"REPORTDATE >= DATE '{crash_start}'"}
)
out_path = RAW_DIR / "crashes_raw.geojson"
with open(out_path, "w") as f:
    json.dump(crashes, f)
print(f"  Saved → {out_path}")


# ── 2. Vision Zero Safety Reports ────────────────────────────────────────────
print("\n=== Pulling Vision Zero Safety Reports ===")
# Hosted on the DDOT FeatureServer (separate from main DCGIS_DATA MapServer).
# Confirmed working endpoint: services/DDOT/VisionZero/FeatureServer/0
vision_zero = fetch_arcgis(
    name="Vision Zero",
    url=config["data_sources"]["vision_zero_api"]
)
out_path = RAW_DIR / "vision_zero_raw.geojson"
with open(out_path, "w") as f:
    json.dump(vision_zero, f)
print(f"  Saved → {out_path}")


# ── 3. Bike Lanes ─────────────────────────────────────────────────────────────
print("\n=== Pulling Bike Lanes ===")
bike_lanes = fetch_arcgis(
    name="Bike Lanes",
    url=config["data_sources"]["bike_lanes_api"]
)
out_path = RAW_DIR / "bike_lanes_raw.geojson"
with open(out_path, "w") as f:
    json.dump(bike_lanes, f)
print(f"  Saved → {out_path}")


# ── 4. Street Centerlines (to extract Penn Ave geometry) ─────────────────────
print("\n=== Pulling Street Centerlines (Pennsylvania Ave only) ===")
# Filter to just Pennsylvania Avenue NW to keep the file small
penn_centerlines = fetch_arcgis(
    name="Street Centerlines",
    url=config["data_sources"]["street_centerlines_api"],
    extra_params={"where": "ROUTENAME LIKE '%PENNSYLVANIA%'"}
)
out_path = RAW_DIR / "penn_centerlines_raw.geojson"
with open(out_path, "w") as f:
    json.dump(penn_centerlines, f)
print(f"  Saved → {out_path}")


# ── 5. AADT Traffic Volume Counts ────────────────────────────────────────────
print("\n=== Pulling AADT Traffic Volume Counts ===")
# AADT = Average Annual Daily Traffic.
# Each feature in this dataset is a road segment with a daily vehicle count.
# We pull the full DC dataset here; 02_corridor_clip.py will filter to Penn Ave.
#
# WHY WE NEED THIS FOR LANE REMOVAL ANALYSIS:
#   If DDOT asks "what happens if we remove a lane?", the first question is
#   "how much traffic is currently using that lane?"  AADT gives you that
#   baseline. A corridor with 20,000 vehicles/day reacts very differently
#   to lane removal than one with 5,000 vehicles/day.
#
# GOVERNANCE NOTE: Updated on HPMS reporting cycle (~3 years).
#   Field to look for: AADT or CURRENT_VOLUME depending on dataset version.
#   Do NOT use as a substitute for a current traffic count study.
try:
    aadt = fetch_arcgis(
        name="AADT",
        url=config["data_sources"]["aadt_api"]
    )
    out_path = RAW_DIR / "aadt_raw.geojson"
    with open(out_path, "w") as f:
        json.dump(aadt, f)
    print(f"  Saved → {out_path}")

    # Report how many segments have volume data
    segments_with_volume = [
        feat for feat in aadt["features"]
        if feat.get("properties", {}).get("AADT") or feat.get("properties", {}).get("CURRENT_VOLUME")
    ]
    print(f"  {len(segments_with_volume)} of {len(aadt['features'])} segments have volume data")

except Exception as e:
    print(f"  WARNING: AADT pull failed: {e}")
    print("  The analysis will proceed without AADT — lane removal volume context will be unavailable.")
    print("  Manual download: https://opendata.dc.gov/datasets/annual-average-daily-traffic")


# ── 6. WMATA GTFS (bus schedules, routes, stops) ─────────────────────────────
print("\n=== Downloading WMATA GTFS ===")
# Using MobilityDatabase public mirror — no API key required.
# Source: https://database.mobilitydata.org, feed ID 1846 (WMATA Bus, 37.9 MB)
gtfs_url = config["data_sources"]["gtfs_url"]

# Skip if files already exist and look complete
existing = list(GTFS_DIR.glob("*.txt")) if GTFS_DIR.exists() else []
if len(existing) >= 5:
    print(f"  GTFS already downloaded ({len(existing)} files in {GTFS_DIR}) — skipping.")
    print(f"  Delete data/raw/gtfs/ and re-run to refresh.")
else:
    try:
        print(f"  Downloading from MobilityDatabase mirror (~38 MB, may take 30-60 sec)...")
        resp = requests.get(gtfs_url, timeout=120)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(GTFS_DIR)

        files = [f.name for f in GTFS_DIR.iterdir()]
        print(f"  Extracted {len(files)} GTFS files:")
        for fname in sorted(files):
            size_kb = (GTFS_DIR / fname).stat().st_size / 1024
            print(f"    {fname} ({size_kb:.0f} KB)")

    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        print("  GTFS download failed — script 04 will use DC Open Data stops as fallback.")


# ── Done ─────────────────────────────────────────────────────────────────────
print("\n=== All raw data pull complete ===")
print(f"Files saved to: {RAW_DIR}")
print("\nFiles pulled:")
for f in sorted(RAW_DIR.glob("*.geojson")):
    size_kb = f.stat().st_size / 1024
    print(f"  {f.name:40} ({size_kb:.0f} KB)")
print("\nNext step: run python 02_corridor_clip.py")
