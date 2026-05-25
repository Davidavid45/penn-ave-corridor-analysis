"""
03_crash_analysis.py
--------------------
Analyzes crash patterns along the Pennsylvania Avenue NW corridor.

WHY THIS APPROACH:
  Crash data from DC Open Data uses separate fields for each mode
  (FATAL_DRIVER, FATAL_BICYCLIST, FATAL_PEDESTRIAN, MAJORINJURIES_*).
  We classify each crash by its most severe mode, then compute density
  per 0.25-mile corridor segment so severity concentrations are visible
  in Tableau rather than just a scatter of points.

Inputs (from data/processed/):
  crashes_clipped.geojson
  corridor.geojson
  corridor_buffer.geojson

Outputs (to data/processed/ and data/tableau/):
  crash_analysis.geojson         — Crashes with mode & segment columns added
  crash_summary_by_year.csv      — Year × mode crash counts
  crashes_corridor.csv           — Tableau-ready flat export
  corridor_segments.csv          — Segment-level crash density (for heatmap)
"""

import pathlib
import numpy as np
import pandas as pd
import geopandas as gpd
import yaml
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

PROCESSED_DIR = pathlib.Path(__file__).parent / "data" / "processed"
TABLEAU_DIR   = pathlib.Path(__file__).parent / "data" / "tableau"
TABLEAU_DIR.mkdir(parents=True, exist_ok=True)

SEGMENT_LENGTH_MILES = config["analysis"]["segment_length_miles"]


# ── Load data ─────────────────────────────────────────────────────────────────
print("=== Loading clipped crash data ===")
crashes_path = PROCESSED_DIR / "crashes_clipped.geojson"
corridor_path = PROCESSED_DIR / "corridor.geojson"

if not crashes_path.exists():
    raise FileNotFoundError("crashes_clipped.geojson not found — run 02_corridor_clip.py first")

crashes = gpd.read_file(crashes_path).to_crs("EPSG:4326")
corridor = gpd.read_file(corridor_path).to_crs("EPSG:4326")

print(f"  Loaded {len(crashes)} corridor crashes")
print(f"  Columns: {list(crashes.columns)}")


# ── Step 1: Classify each crash by mode ──────────────────────────────────────
print("\n=== Step 1: Classifying crashes by mode ===")

# These field names come from the DC Crashes dataset.
# Each field is a count: 0 means none of that mode were affected.
# We check in order: pedestrian > bicycle > vehicle (most vulnerable first).
def classify_mode(row) -> str:
    """
    Returns the primary mode affected in a crash.
    Prioritizes most vulnerable road users.
    """
    # Check if field exists before reading (API may vary)
    def val(field):
        return row.get(field, 0) or 0

    if val("FATAL_PEDESTRIAN") > 0 or val("MAJORINJURIES_PEDESTRIAN") > 0:
        return "pedestrian"
    elif val("FATAL_BICYCLIST") > 0 or val("MAJORINJURIES_BICYCLIST") > 0:
        return "bicycle"
    elif val("FATAL_DRIVER") > 0 or val("MAJORINJURIES_DRIVER") > 0:
        return "vehicle"
    else:
        return "vehicle"  # default: any crash without mode-specific injury is vehicle

crashes["MODE"] = crashes.apply(classify_mode, axis=1)

# Add severity column
def classify_severity(row) -> str:
    def val(field):
        return row.get(field, 0) or 0
    fatalities = val("FATAL_DRIVER") + val("FATAL_BICYCLIST") + val("FATAL_PEDESTRIAN")
    major      = val("MAJORINJURIES_DRIVER") + val("MAJORINJURIES_BICYCLIST") + val("MAJORINJURIES_PEDESTRIAN")
    if fatalities > 0:
        return "fatal"
    elif major > 0:
        return "major_injury"
    else:
        return "minor_or_property"

crashes["SEVERITY"] = crashes.apply(classify_severity, axis=1)

# Parse year from REPORTDATE
crashes["REPORTDATE"] = pd.to_datetime(crashes["REPORTDATE"], unit='ms', errors='coerce')
crashes["YEAR"] = crashes["REPORTDATE"].dt.year

mode_counts = crashes["MODE"].value_counts()
print(f"  Mode breakdown:\n{mode_counts.to_string()}")
print(f"\n  Severity breakdown:\n{crashes['SEVERITY'].value_counts().to_string()}")


# ── Step 2: Divide corridor into 0.25-mile segments ──────────────────────────
print(f"\n=== Step 2: Dividing corridor into {SEGMENT_LENGTH_MILES}-mile segments ===")

# Reproject corridor to UTM (meters) for accurate distance calculations
corridor_utm = corridor.to_crs("EPSG:32618")
corridor_line = corridor_utm.geometry.iloc[0]

# Total corridor length in miles (1 meter = 0.000621371 miles)
total_length_m     = corridor_line.length
total_length_miles = total_length_m * 0.000621371
segment_length_m   = SEGMENT_LENGTH_MILES / 0.000621371

n_segments = int(np.ceil(total_length_miles / SEGMENT_LENGTH_MILES))
print(f"  Corridor length: {total_length_miles:.2f} miles → {n_segments} segments")

# Build segment polygons by walking along the corridor line
# We create a small buffer around each sub-segment to catch nearby crashes
segments = []
for i in range(n_segments):
    start_frac = (i * segment_length_m) / total_length_m
    end_frac   = min(((i + 1) * segment_length_m) / total_length_m, 1.0)

    # interpolate() returns a Point at a fractional distance along the line
    start_point = corridor_line.interpolate(start_frac, normalized=True)
    end_point   = corridor_line.interpolate(end_frac,   normalized=True)

    # Sub-segment geometry
    # We extract the portion of the line between start and end
    sub_coords = []
    for frac in np.linspace(start_frac, end_frac, 20):
        pt = corridor_line.interpolate(frac, normalized=True)
        sub_coords.append((pt.x, pt.y))

    sub_line = LineString(sub_coords)
    seg_buffer = sub_line.buffer(100)  # 100m on each side

    # Get the midpoint for lat/lon labeling
    mid = corridor_line.interpolate((start_frac + end_frac) / 2, normalized=True)

    segments.append({
        "segment_id":   i + 1,
        "start_mile":   round(i * SEGMENT_LENGTH_MILES, 2),
        "end_mile":     round(min((i + 1) * SEGMENT_LENGTH_MILES, total_length_miles), 2),
        "mid_x_utm":    mid.x,
        "mid_y_utm":    mid.y,
        "geometry":     seg_buffer,
    })

segments_gdf = gpd.GeoDataFrame(segments, crs="EPSG:32618")
print(f"  Created {len(segments_gdf)} corridor segments")


# ── Step 3: Assign each crash to its corridor segment ─────────────────────────
print("\n=== Step 3: Assigning crashes to segments ===")

# Reproject crashes to UTM for the join
crashes_utm = crashes.to_crs("EPSG:32618")

# Linear referencing: project each crash onto the corridor centerline to get its
# distance from the start, then use integer division to assign a segment.
#
# WHY NOT gpd.sjoin:
#   Segment buffers overlap at their boundaries (a 100m buffer on adjacent
#   0.25-mile sub-segments shares ~100m of overlap). sjoin assigns a crash in
#   that overlap zone to BOTH segments, inflating row count ~12x.
#   Linear referencing avoids this entirely — every crash maps to exactly one
#   segment based on its position along the centerline.
crashes_utm["dist_along_m"] = crashes_utm.geometry.apply(
    lambda pt: corridor_line.project(pt)
)
crashes_utm["segment_id"] = crashes_utm["dist_along_m"].apply(
    lambda d: min(int(d / segment_length_m) + 1, n_segments)
)
crashes_with_seg = crashes_utm.copy()
print(f"  All {len(crashes_with_seg)} crashes assigned to segments (linear referencing, no duplication)")


# ── Step 4: Compute crash density per segment per mode ───────────────────────
print("\n=== Step 4: Computing crash density per segment ===")

# Count crashes by segment and mode
density = (
    crashes_with_seg
    .groupby(["segment_id", "MODE"])
    .size()
    .unstack(fill_value=0)
    .reset_index()
)

# Ensure all mode columns exist
for mode_col in ["vehicle", "bicycle", "pedestrian"]:
    if mode_col not in density.columns:
        density[mode_col] = 0

density = density.rename(columns={
    "vehicle":    "crash_density_vehicle",
    "bicycle":    "crash_density_bike",
    "pedestrian": "crash_density_ped",
})
density["crash_density_total"] = (
    density["crash_density_vehicle"] +
    density["crash_density_bike"] +
    density["crash_density_ped"]
)

# Merge back with segment geometry info
segments_with_density = segments_gdf.merge(density, on="segment_id", how="left").fillna(0)

# Reproject midpoints back to WGS84 for lat/lon columns
segments_wgs = segments_with_density.to_crs("EPSG:4326")
# Recompute midpoints in WGS84
for i, row in segments_wgs.iterrows():
    centroid = row.geometry.centroid
    segments_wgs.at[i, "mid_lon"] = centroid.x
    segments_wgs.at[i, "mid_lat"] = centroid.y

print("  Top 5 highest-risk segments:")
top5 = segments_with_density.nlargest(5, "crash_density_total")[
    ["segment_id", "start_mile", "end_mile", "crash_density_total",
     "crash_density_ped", "crash_density_bike", "crash_density_vehicle"]
]
print(top5.to_string(index=False))


# ── Step 4b: Impact zone crash comparison ────────────────────────────────────
print("\n=== Step 4b: Impact zone crash analysis (17th–22nd St NW) ===")
# WHY THIS STEP:
#   For the lane removal question, DDOT leadership wants to know: what is
#   the CURRENT safety picture specifically in the reconfiguration zone?
#   Is it better or worse than the corridor average? This comparison is
#   the core of a two-week preliminary impact assessment.

impact_zone_path = PROCESSED_DIR / "impact_zone_buffer.geojson"
if impact_zone_path.exists():
    impact_zone_gdf = gpd.read_file(impact_zone_path).to_crs("EPSG:32618")
    impact_polygon  = impact_zone_gdf.geometry.iloc[0]

    # Filter crashes to just the impact zone
    crashes_impact = crashes_utm[crashes_utm.geometry.intersects(impact_polygon)].copy()

    # Mode breakdown in impact zone
    impact_mode_counts = crashes_impact["MODE"].value_counts()
    total_corridor     = len(crashes_utm)
    total_impact       = len(crashes_impact)
    impact_pct         = (total_impact / total_corridor * 100) if total_corridor > 0 else 0

    print(f"  Impact zone crashes: {total_impact} of {total_corridor} corridor total ({impact_pct:.0f}%)")
    print(f"  Mode breakdown in impact zone:\n{impact_mode_counts.to_string()}")

    # Compare crash rate: impact zone vs. rest of corridor
    # Compute impact zone road length from geometry intersection (not a hardcoded estimate).
    # Penn Ave is DIAGONAL — the road distance from 17th to 22nd St is ~0.82 miles,
    # not the ~0.4 miles you'd guess from a straight-line map measurement.
    _impact_segment          = corridor_line.intersection(impact_polygon)
    impact_zone_length_miles = _impact_segment.length * 0.000621371
    rest_length_miles        = (total_length_miles - impact_zone_length_miles)

    crashes_rest   = total_corridor - total_impact
    rate_impact    = total_impact  / impact_zone_length_miles if impact_zone_length_miles > 0 else 0
    rate_rest      = crashes_rest  / rest_length_miles        if rest_length_miles > 0        else 0

    print(f"\n  Crash rate comparison:")
    print(f"    Impact zone (17th–22nd St):  {rate_impact:.1f} crashes/mile")
    print(f"    Rest of corridor:            {rate_rest:.1f} crashes/mile")
    if rate_impact > rate_rest * 1.2:
        print("  ⚠ Impact zone crash rate is >20% higher than rest of corridor")
    elif rate_impact < rate_rest * 0.8:
        print("  ✓ Impact zone crash rate is lower than rest of corridor")
    else:
        print("  ~ Impact zone crash rate is roughly consistent with rest of corridor")

    # Add impact zone flag to crashes for Tableau filtering
    crashes_utm["in_impact_zone"] = crashes_utm.geometry.intersects(impact_polygon)

else:
    print("  impact_zone_buffer.geojson not found — run 02_corridor_clip.py first")
    crashes_utm["in_impact_zone"] = False


# ── Step 5: Year-over-year summary ───────────────────────────────────────────
print("\n=== Step 5: Year-over-year crash summary ===")

yearly = (
    crashes_with_seg
    .groupby(["YEAR", "MODE"])
    .size()
    .unstack(fill_value=0)
    .reset_index()
)
print(yearly.to_string(index=False))

yearly_path = PROCESSED_DIR / "crash_summary_by_year.csv"
yearly.to_csv(yearly_path, index=False)
print(f"  Saved → {yearly_path}")


# ── Step 6: Export Tableau-ready files ───────────────────────────────────────
print("\n=== Step 6: Exporting Tableau CSVs ===")

# crashes_corridor.csv — one row per crash
crashes_tableau = crashes_with_seg.copy()

# Keep lat/lon from geometry (projected back to WGS84)
crashes_tableau_wgs = crashes_tableau.to_crs("EPSG:4326")
crashes_tableau_wgs["LATITUDE"]  = crashes_tableau_wgs.geometry.y
crashes_tableau_wgs["LONGITUDE"] = crashes_tableau_wgs.geometry.x

# Select and clean columns for Tableau
keep_cols = ["LATITUDE", "LONGITUDE", "MODE", "SEVERITY", "YEAR",
             "REPORTDATE", "segment_id", "in_impact_zone",
             "FATAL_DRIVER", "FATAL_BICYCLIST", "FATAL_PEDESTRIAN",
             "MAJORINJURIES_BICYCLIST", "MAJORINJURIES_PEDESTRIAN",
             "MAJORINJURIES_DRIVER", "WARD"]
# Only keep columns that actually exist
keep_cols = [c for c in keep_cols if c in crashes_tableau_wgs.columns]

crashes_out = crashes_tableau_wgs[keep_cols].copy()
crashes_out = crashes_out.dropna(subset=["LATITUDE", "LONGITUDE"])

crashes_csv_path = TABLEAU_DIR / "crashes_corridor.csv"
crashes_out.to_csv(crashes_csv_path, index=False)
print(f"  crashes_corridor.csv → {len(crashes_out)} rows, saved to {crashes_csv_path}")

# corridor_segments.csv — one row per 0.25mi segment
seg_cols = ["segment_id", "start_mile", "end_mile",
            "crash_density_vehicle", "crash_density_bike",
            "crash_density_ped", "crash_density_total",
            "mid_lat", "mid_lon"]
seg_cols_exist = [c for c in seg_cols if c in segments_wgs.columns]
segments_out = segments_wgs[seg_cols_exist].copy().fillna(0)

segments_csv_path = TABLEAU_DIR / "corridor_segments.csv"
segments_out.to_csv(segments_csv_path, index=False)
print(f"  corridor_segments.csv → {len(segments_out)} rows, saved to {segments_csv_path}")


# ── Done ─────────────────────────────────────────────────────────────────────
print("\n=== Crash analysis complete ===")
print(f"Total crashes analyzed: {len(crashes_out)}")
print(f"Mode breakdown: {crashes_out['MODE'].value_counts().to_dict()}")
print("\nNext step: run python 04_transit_analysis.py")
