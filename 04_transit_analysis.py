"""
04_transit_analysis.py
----------------------
Analyzes WMATA bus transit service along the Pennsylvania Avenue NW corridor.

TWO MODES — script auto-detects which to use:

  MODE A — GTFS (preferred):
    Uses WMATA's static GTFS feed from data/raw/gtfs/.
    Gives full analysis: stop locations, AM/PM peak trip counts, headways.
    To get GTFS: go to https://developer.wmata.com → free account → download
    Bus GTFS ZIP → unzip into data/raw/gtfs/

  MODE B — DC Open Data fallback (runs automatically if GTFS missing):
    Uses the DC Open Data Metro Bus Stops layer via API.
    Gives stop locations and corridor coverage — but NO frequency data.
    This is still useful for a coverage map and stop spacing analysis.
    Documents the limitation clearly in the output.

DATA GOVERNANCE NOTE:
  GTFS reflects scheduled service only — not real-time delays or cancellations.
  DC Open Data stops reflect the physical stop inventory — not which routes
  serve them or how often. Both sources have blind spots; the project
  documents which was used and what is missing.

Inputs:
  data/raw/gtfs/                  — GTFS files (Mode A only)
  data/processed/corridor_buffer.geojson

Outputs (to data/processed/ and data/tableau/):
  transit_corridor.csv            — Tableau-ready: one row per corridor stop
  transit_stops.geojson           — Geospatial stops within corridor
"""

import pathlib
import json
import requests
import pandas as pd
import geopandas as gpd
import yaml
from shapely.geometry import Point

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

GTFS_DIR      = pathlib.Path(__file__).parent / "data" / "raw" / "gtfs"
PROCESSED_DIR = pathlib.Path(__file__).parent / "data" / "processed"
TABLEAU_DIR   = pathlib.Path(__file__).parent / "data" / "tableau"
TABLEAU_DIR.mkdir(parents=True, exist_ok=True)

PEAK_AM_START = config["analysis"]["peak_am_start"]
PEAK_AM_END   = config["analysis"]["peak_am_end"]
PEAK_PM_START = config["analysis"]["peak_pm_start"]
PEAK_PM_END   = config["analysis"]["peak_pm_end"]
WALKABLE_M    = config["analysis"]["walkable_buffer_meters"]


# ── Step 1: Detect mode and load stops ───────────────────────────────────────
gtfs_files = list(GTFS_DIR.glob("*.txt")) if GTFS_DIR.exists() else []
USE_GTFS = len(gtfs_files) >= 3  # need at least stops.txt, stop_times.txt, trips.txt

if USE_GTFS:
    print("=== Step 1: GTFS detected — loading full schedule data (Mode A) ===")
    stops      = pd.read_csv(GTFS_DIR / "stops.txt")
    stop_times = pd.read_csv(GTFS_DIR / "stop_times.txt",
                             dtype={"arrival_time": str, "departure_time": str})
    trips      = pd.read_csv(GTFS_DIR / "trips.txt")
    routes     = pd.read_csv(GTFS_DIR / "routes.txt")
    print(f"  stops: {len(stops):,}  |  stop_times: {len(stop_times):,}  |  trips: {len(trips):,}")

else:
    print("=== Step 1: GTFS not found — using DC Open Data bus stops fallback (Mode B) ===")
    print("  NOTE: This mode gives stop locations only — no frequency/headway data.")
    print("  To enable full analysis: download GTFS from https://developer.wmata.com")
    print("  and unzip into data/raw/gtfs/\n")

    # Pull bus stops from DC Open Data
    stops_url = config["data_sources"]["bus_stops_api"]
    all_features = []
    page = 0
    while True:
        params = {"where": "1=1", "outFields": "*", "f": "geojson",
                  "resultRecordCount": 1000, "resultOffset": page * 1000}
        r = requests.get(stops_url, params=params, timeout=60)
        feats = r.json().get("features", [])
        all_features.extend(feats)
        if len(feats) < 1000:
            break
        page += 1

    print(f"  Pulled {len(all_features):,} bus stops from DC Open Data")

    # Normalize into a DataFrame that matches GTFS stops.txt structure
    records = []
    for feat in all_features:
        p = feat["properties"]
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates", [None, None])
        records.append({
            "stop_id":   p.get("OBJECTID"),
            "stop_name": p.get("BSTP_MSG_TEXT", ""),
            "stop_lat":  p.get("BSTP_LAT") or (coords[1] if len(coords) > 1 else None),
            "stop_lon":  p.get("BSTP_LON") or (coords[0] if coords else None),
        })
    stops = pd.DataFrame(records).dropna(subset=["stop_lat", "stop_lon"])
    print(f"  {len(stops):,} stops with valid coordinates")


# ── Step 2: Clip stops to corridor buffer ────────────────────────────────────
print("\n=== Step 2: Filtering stops within corridor buffer ===")

# Build GeoDataFrame from stop lat/lon
stops_gdf = gpd.GeoDataFrame(
    stops,
    geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
    crs="EPSG:4326"
)

# Load corridor buffer
buffer_path = PROCESSED_DIR / "corridor_buffer.geojson"
if not buffer_path.exists():
    raise FileNotFoundError("corridor_buffer.geojson not found — run 02_corridor_clip.py first")

corridor_buffer_gdf = gpd.read_file(buffer_path)
buffer_polygon = corridor_buffer_gdf.geometry.iloc[0]

# Spatial filter
corridor_stops = stops_gdf[stops_gdf.geometry.intersects(buffer_polygon)].copy()
print(f"  {len(corridor_stops)} stops within {config['corridor']['buffer_meters']}m of corridor")

if len(corridor_stops) == 0:
    print("  WARNING: No stops found in corridor. Check corridor_buffer.geojson.")


# ── Step 3: Get service patterns (GTFS only) ─────────────────────────────────
print("\n=== Step 3: Computing service frequency for corridor stops ===")

# We want: for each stop, how many bus trips stop there during AM and PM peaks?
# The linkage is: stop → stop_times → trips → routes

# Get all stop_time records for corridor stops only
corridor_stop_ids = set(corridor_stops["stop_id"].astype(str))
corridor_st = stop_times[stop_times["stop_id"].astype(str).isin(corridor_stop_ids)].copy()

print(f"  Stop-time records for corridor stops: {len(corridor_st):,}")


def time_to_seconds(t_str: str) -> int:
    """
    Convert GTFS time string (HH:MM:SS) to seconds since midnight.

    NOTE: GTFS allows times > 24:00:00 for trips past midnight.
    We handle this correctly by not modding at 24.
    """
    if pd.isna(t_str):
        return -1
    parts = str(t_str).strip().split(":")
    if len(parts) != 3:
        return -1
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s

if USE_GTFS:
    AM_START_SEC = time_to_seconds(PEAK_AM_START)
    AM_END_SEC   = time_to_seconds(PEAK_AM_END)
    PM_START_SEC = time_to_seconds(PEAK_PM_START)
    PM_END_SEC   = time_to_seconds(PEAK_PM_END)

    corridor_st["dep_sec"]    = corridor_st["departure_time"].apply(time_to_seconds)
    corridor_st["is_am_peak"] = (corridor_st["dep_sec"] >= AM_START_SEC) & (corridor_st["dep_sec"] < AM_END_SEC)
    corridor_st["is_pm_peak"] = (corridor_st["dep_sec"] >= PM_START_SEC) & (corridor_st["dep_sec"] < PM_END_SEC)

    corridor_st_trips = corridor_st.merge(trips[["trip_id", "route_id"]], on="trip_id", how="left")

    stop_service = (
        corridor_st_trips
        .groupby("stop_id")
        .agg(
            am_peak_trips = ("trip_id", lambda x: x[corridor_st_trips.loc[x.index, "is_am_peak"]].nunique()),
            pm_peak_trips = ("trip_id", lambda x: x[corridor_st_trips.loc[x.index, "is_pm_peak"]].nunique()),
            route_ids     = ("route_id", lambda x: ",".join(sorted(x.dropna().astype(str).unique()))),
            total_trips   = ("trip_id", "nunique"),
        )
        .reset_index()
    )
    print(f"  Service summary for {len(stop_service)} corridor stops computed")

else:
    # Mode B: no schedule data — create empty service table with null frequency columns
    # This is documented as a limitation in the Tableau export
    print("  Skipping frequency analysis — GTFS not available.")
    print("  Output will include stop locations and spacing only.")
    print("  Limitation will be noted in data_governance.csv")
    stop_service = pd.DataFrame(columns=["stop_id","am_peak_trips","pm_peak_trips","route_ids","total_trips"])
    corridor_stops["am_peak_trips"] = None
    corridor_stops["pm_peak_trips"] = None
    corridor_stops["route_ids"]     = "GTFS not available"
    corridor_stops["total_trips"]   = None


# ── Step 4: Compute stop spacing along corridor ────────────────────────────────
print("\n=== Step 4: Computing stop spacing along corridor ===")

# Sort stops by their position along the corridor line
corridor_gdf  = gpd.read_file(PROCESSED_DIR / "corridor.geojson")
corridor_utm  = corridor_gdf.to_crs("EPSG:32618")
corridor_line = corridor_utm.geometry.iloc[0]

stops_utm = corridor_stops.to_crs("EPSG:32618").copy()
# Project each stop onto the corridor line to get its distance from the start
stops_utm["dist_along_m"] = stops_utm.geometry.apply(
    lambda pt: corridor_line.project(pt)
)
stops_utm = stops_utm.sort_values("dist_along_m").reset_index(drop=True)

# Stop spacing = distance to next stop
stops_utm["stop_spacing_m"] = stops_utm["dist_along_m"].diff().shift(-1).fillna(0).round(1)

# Add stop_spacing back to original CRS GeoDataFrame
corridor_stops = corridor_stops.merge(
    stops_utm[["stop_id", "dist_along_m", "stop_spacing_m"]],
    on="stop_id",
    how="left"
)
print(f"  Average stop spacing: {stops_utm['stop_spacing_m'][stops_utm['stop_spacing_m'] > 0].mean():.0f}m")


# ── Step 5: Create 400m walkable catchment buffers ────────────────────────────
print(f"\n=== Step 5: Computing {WALKABLE_M}m walkable catchment areas ===")

stops_projected = corridor_stops.to_crs("EPSG:32618").copy()
stops_projected["walkable_catchment"] = stops_projected.geometry.buffer(WALKABLE_M)

# Save stops with walkable buffers as geospatial file
# Drop the extra walkable_catchment column first — GeoPandas to_file() requires
# exactly one geometry column. We overwrite geometry with the buffer polygon,
# then drop the now-redundant walkable_catchment Series before writing.
stops_geo = stops_projected.copy()
stops_geo["geometry"] = stops_geo["walkable_catchment"]
stops_geo = stops_geo.drop(columns=["walkable_catchment"]).to_crs("EPSG:4326")
stops_geo.to_file(PROCESSED_DIR / "transit_catchment.geojson", driver="GeoJSON")
print(f"  Saved walkable catchment areas → data/processed/transit_catchment.geojson")


# ── Step 5b: Impact zone transit comparison ───────────────────────────────────
print("\n=== Step 5b: Impact zone transit analysis (17th–22nd St NW) ===")
# WHY THIS STEP:
#   One of the key questions in a lane removal analysis is whether there
#   is adequate transit to absorb displaced vehicle trips. If DDOT removes
#   a lane to add a bus-only lane or protected bike lane, people need to
#   know whether the buses in that specific zone are frequent enough to be
#   a real alternative. This step isolates stops in the reconfiguration zone.

impact_zone_path = PROCESSED_DIR / "impact_zone_buffer.geojson"
if impact_zone_path.exists():
    impact_zone_gdf  = gpd.read_file(impact_zone_path).to_crs("EPSG:32618")
    impact_polygon   = impact_zone_gdf.geometry.iloc[0]

    stops_utm_check  = corridor_stops.to_crs("EPSG:32618")
    impact_stops_mask = stops_utm_check.geometry.intersects(impact_polygon)
    impact_stops     = corridor_stops[impact_stops_mask.values].copy()

    print(f"  Stops in impact zone: {len(impact_stops)} of {len(corridor_stops)} corridor stops")

    if len(impact_stops) > 0:
        # Merge service data to get peak trip counts for impact zone stops
        impact_stops_service = impact_stops.merge(stop_service, on="stop_id", how="left").fillna(0)
        avg_am = impact_stops_service["am_peak_trips"].mean()
        avg_pm = impact_stops_service["pm_peak_trips"].mean()
        avg_all_corridor = stop_service["am_peak_trips"].mean() if len(stop_service) > 0 else 0

        print(f"  Average AM peak trips in impact zone:   {avg_am:.1f} trips/stop")
        print(f"  Average AM peak trips full corridor:    {avg_all_corridor:.1f} trips/stop")

        if avg_am < avg_all_corridor * 0.8:
            print("  ⚠ Transit frequency in impact zone is BELOW corridor average")
            print("    → Lane removal to bus-only lane may face adequacy concerns")
        else:
            print("  ✓ Transit frequency in impact zone is consistent with corridor average")

    # Flag impact zone stops for Tableau
    corridor_stops["in_impact_zone"] = impact_stops_mask.values
else:
    print("  impact_zone_buffer.geojson not found — skipping impact zone comparison")
    corridor_stops["in_impact_zone"] = False


# ── Step 6: Merge and export for Tableau ─────────────────────────────────────
print("\n=== Step 6: Building Tableau-ready transit export ===")

# Merge service data into corridor stops
transit_full = corridor_stops.merge(
    stop_service,
    on="stop_id",
    how="left"
).fillna({"am_peak_trips": 0, "pm_peak_trips": 0, "total_trips": 0, "route_ids": ""})

# Convert numeric columns
for col in ["am_peak_trips", "pm_peak_trips", "total_trips"]:
    transit_full[col] = transit_full[col].astype(int)

# Add lat/lon explicitly for Tableau
transit_full["stop_lat"] = transit_full.geometry.y
transit_full["stop_lon"] = transit_full.geometry.x

# Save geospatial version
transit_gdf = gpd.GeoDataFrame(transit_full, geometry="geometry", crs="EPSG:4326")
transit_gdf.to_file(PROCESSED_DIR / "transit_stops.geojson", driver="GeoJSON")

# Tableau-flat CSV (no geometry column)
transit_tableau_cols = [
    "stop_id", "stop_name", "stop_lat", "stop_lon",
    "route_ids", "am_peak_trips", "pm_peak_trips", "total_trips",
    "stop_spacing_m", "dist_along_m", "in_impact_zone"
]
transit_tableau_cols = [c for c in transit_tableau_cols if c in transit_full.columns]
transit_csv = transit_full[transit_tableau_cols].copy()

transit_csv_path = TABLEAU_DIR / "transit_corridor.csv"
transit_csv.to_csv(transit_csv_path, index=False)
print(f"  transit_corridor.csv → {len(transit_csv)} stops, saved to {transit_csv_path}")

# Print summary
print(f"\n  Top 5 busiest corridor stops (AM peak):")
top_stops = transit_csv.nlargest(5, "am_peak_trips")[
    ["stop_name", "am_peak_trips", "pm_peak_trips", "route_ids"]
]
print(top_stops.to_string(index=False))


# ── Done ─────────────────────────────────────────────────────────────────────
print("\n=== Transit analysis complete ===")
print("\nNext step: run python 05_export_tableau.py")
