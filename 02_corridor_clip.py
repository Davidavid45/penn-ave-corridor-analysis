"""
02_corridor_clip.py
-------------------
Extracts the Pennsylvania Avenue NW corridor geometry from OpenStreetMap
using OSMnx, creates a 100m buffer polygon, and clips all raw datasets
to that buffer.

Also creates a SECOND, tighter boundary around the active lane reconfiguration
zone (17th–22nd St NW) so the crash and transit analyses can compare the
impact zone against the rest of the corridor.

WHY TWO BOUNDARIES:
  The full corridor buffer (100m around all of Penn Ave NW) gives you the
  big picture. The impact zone buffer isolates specifically the segment where
  DDOT is reconfiguring travel lanes. Having both lets you ask: "Are crashes
  in the construction zone different from the rest of the corridor?" and
  "Is transit coverage in the reconfiguration zone adequate before we remove
  the lane?" — which is exactly what the two-week analysis question requires.

WHY THIS MATTERS (data governance):
  Every spatial analysis needs a defined study area. By deriving the
  corridor from OSM street network data (not hand-drawn), the boundary
  is reproducible and auditable. The 100m buffer is configured in
  config.yaml so it can be adjusted without touching analysis code.

Outputs (saved to data/processed/):
  corridor.geojson            — The raw Penn Ave centerline geometry
  corridor_buffer.geojson     — 100m buffer polygon (full corridor study area)
  impact_zone_buffer.geojson  — Tight boundary around 17th–22nd St NW  ← NEW
  crashes_clipped.geojson     — Crashes within the full corridor buffer
  vision_zero_clipped.geojson
  bike_lanes_clipped.geojson
  aadt_clipped.geojson        — AADT segments within the corridor buffer  ← NEW
"""

import pathlib
import json
import geopandas as gpd
import osmnx as ox
import yaml
from shapely.geometry import box
from shapely.ops import unary_union

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

RAW_DIR      = pathlib.Path(__file__).parent / "data" / "raw"
PROCESSED_DIR = pathlib.Path(__file__).parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

BUFFER_METERS = config["corridor"]["buffer_meters"]  # 100m
OSM_FILTER    = config["corridor"]["osm_filter"]      # "Pennsylvania Avenue Northwest"
IMPACT_ZONE   = config["corridor"]["impact_zone"]     # 17th–22nd St NW bounding box


# ── Step 1: Extract Penn Ave from OSMnx street network ───────────────────────
print("=== Step 1: Extracting Pennsylvania Avenue NW from OSMnx ===")
print("  Downloading DC street network (drive) from OpenStreetMap...")
print("  (This takes 30-60 seconds the first time — OSMnx fetches the full DC graph)")

# ox.graph_from_place returns a NetworkX graph of every road in DC.
# We convert it to a GeoDataFrame of edges (each edge = one road segment).
G = ox.graph_from_place("Washington, D.C., USA", network_type="drive")
edges = ox.graph_to_gdfs(G, nodes=False)  # just the edges (roads), not intersections

print(f"  DC street network: {len(edges)} road segments total")

# The 'name' column can be a string OR a list (when a segment has multiple names).
# We normalize it: if it's a list, check if our target name is in the list.
def matches_penn_ave(name_val):
    """Return True if this edge is part of Pennsylvania Avenue NW."""
    if name_val is None:
        return False
    if isinstance(name_val, list):
        return any(OSM_FILTER in str(n) for n in name_val)
    return OSM_FILTER in str(name_val)

penn_mask = edges["name"].apply(matches_penn_ave)
penn_edges = edges[penn_mask].copy()

print(f"  Found {len(penn_edges)} road segments matching '{OSM_FILTER}'")

if len(penn_edges) == 0:
    raise ValueError(
        f"No road segments found for '{OSM_FILTER}'. "
        "Check the osm_filter value in config.yaml."
    )

# ── Step 2: Union all segments into one corridor LineString ──────────────────
print("\n=== Step 2: Merging segments into corridor centerline ===")

# unary_union merges all the individual LineString geometries into one
# MultiLineString (or LineString if they're all connected).
corridor_geometry = unary_union(penn_edges.geometry)
print(f"  Corridor geometry type: {corridor_geometry.geom_type}")
print(f"  Corridor length: {corridor_geometry.length:.4f} degrees")

# Save as GeoDataFrame (CRS = EPSG:4326, standard lat/lon)
corridor_gdf = gpd.GeoDataFrame(
    {"name": ["Pennsylvania Avenue NW"], "geometry": [corridor_geometry]},
    crs="EPSG:4326"
)
corridor_path = PROCESSED_DIR / "corridor.geojson"
corridor_gdf.to_file(corridor_path, driver="GeoJSON")
print(f"  Saved corridor centerline → {corridor_path}")


# ── Step 3: Create 100m buffer ───────────────────────────────────────────────
print(f"\n=== Step 3: Creating {BUFFER_METERS}m buffer polygon ===")

# IMPORTANT: Buffers must be computed in a projected CRS (meters), not
# geographic CRS (degrees). We reproject to UTM Zone 18N (EPSG:32618),
# which covers Washington DC, then buffer, then reproject back to WGS84.
corridor_projected = corridor_gdf.to_crs("EPSG:32618")
buffer_projected   = corridor_projected.copy()
buffer_projected["geometry"] = corridor_projected.geometry.buffer(BUFFER_METERS)

# Reproject back to standard lat/lon for compatibility with other datasets
corridor_buffer_gdf = buffer_projected.to_crs("EPSG:4326")

buffer_path = PROCESSED_DIR / "corridor_buffer.geojson"
corridor_buffer_gdf.to_file(buffer_path, driver="GeoJSON")
print(f"  Buffer area: {corridor_buffer_gdf.geometry.area.sum():.6f} sq degrees")
print(f"  Saved corridor buffer → {buffer_path}")


# ── Step 3b: Create impact zone buffer (17th–22nd St NW) ─────────────────────
print(f"\n=== Step 3b: Creating impact zone boundary ({IMPACT_ZONE['name']}) ===")

# WHY WE DO THIS:
#   The DDOT streetscape project only covers 17th–22nd St NW — a ~0.5 mile
#   sub-segment of the full corridor. For the lane removal analysis question,
#   we want to isolate crashes, transit stops, and AADT counts specifically
#   within this reconfiguration zone, then compare them to the rest of Penn Ave.
#
#   We use a bounding box (rectangle) defined by the coordinates in config.yaml.
#   This is simpler and more transparent than a complex polygon — any reviewer
#   can verify the boundary by looking at the coordinates.

impact_box = box(
    IMPACT_ZONE["min_lon"],
    IMPACT_ZONE["min_lat"],
    IMPACT_ZONE["max_lon"],
    IMPACT_ZONE["max_lat"],
)

# Intersect the bounding box with the corridor buffer so the impact zone
# stays within the road corridor (not just the rectangular box)
impact_zone_polygon = impact_box.intersection(corridor_buffer_gdf.geometry.iloc[0])

impact_zone_gdf = gpd.GeoDataFrame(
    {"name": [IMPACT_ZONE["name"]], "geometry": [impact_zone_polygon]},
    crs="EPSG:4326"
)
impact_zone_path = PROCESSED_DIR / "impact_zone_buffer.geojson"
impact_zone_gdf.to_file(impact_zone_path, driver="GeoJSON")
print(f"  Impact zone covers: {IMPACT_ZONE['name']}")
print(f"  Bounding box: lon [{IMPACT_ZONE['min_lon']}, {IMPACT_ZONE['max_lon']}], "
      f"lat [{IMPACT_ZONE['min_lat']}, {IMPACT_ZONE['max_lat']}]")
print(f"  Saved impact zone → {impact_zone_path}")


# ── Step 4: Clip raw datasets to corridor buffer ─────────────────────────────
print("\n=== Step 4: Clipping raw datasets to corridor buffer ===")

buffer_polygon = corridor_buffer_gdf.geometry.iloc[0]

def clip_dataset(raw_filename: str, out_filename: str, label: str) -> gpd.GeoDataFrame:
    """
    Load a raw GeoJSON, filter to features within the corridor buffer,
    save the clipped version to data/processed/, and return it.
    """
    raw_path = RAW_DIR / raw_filename
    if not raw_path.exists():
        print(f"  WARNING: {raw_path} not found — run 01_data_pull.py first")
        return None

    gdf = gpd.read_file(raw_path)
    print(f"  [{label}] Loaded {len(gdf)} total features")

    # Ensure same CRS before spatial operation
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    # Keep only features whose geometry falls within (or intersects) the buffer
    clipped = gdf[gdf.geometry.intersects(buffer_polygon)].copy()
    print(f"  [{label}] {len(clipped)} features within {BUFFER_METERS}m of corridor")

    out_path = PROCESSED_DIR / out_filename
    clipped.to_file(out_path, driver="GeoJSON")
    print(f"  [{label}] Saved → {out_path}")
    return clipped


crashes_clipped     = clip_dataset("crashes_raw.geojson",      "crashes_clipped.geojson",      "Crashes")
vision_zero_clipped = clip_dataset("vision_zero_raw.geojson", "vision_zero_clipped.geojson",  "Vision Zero")
bike_lanes_clipped  = clip_dataset("bike_lanes_raw.geojson",  "bike_lanes_clipped.geojson",   "Bike Lanes")
aadt_clipped        = clip_dataset("aadt_raw.geojson",        "aadt_clipped.geojson",         "AADT")


# ── Step 5: Compare full corridor vs. impact zone ─────────────────────────────
print("\n=== Step 5: Full corridor vs. impact zone comparison ===")
# This gives you an immediate sense of whether the reconfiguration zone
# is disproportionately represented in any category — a key framing for
# the lane removal analysis.

impact_polygon = impact_zone_gdf.geometry.iloc[0]

def count_in_impact_zone(gdf, label):
    if gdf is None:
        return
    in_zone = gdf[gdf.geometry.intersects(impact_polygon)]
    pct = (len(in_zone) / len(gdf) * 100) if len(gdf) > 0 else 0
    print(f"  {label:20} — full corridor: {len(gdf):4d}  |  impact zone: {len(in_zone):4d}  ({pct:.0f}%)")

count_in_impact_zone(crashes_clipped,     "Crashes")
count_in_impact_zone(vision_zero_clipped, "Vision Zero reports")
count_in_impact_zone(bike_lanes_clipped,  "Bike lane segments")
count_in_impact_zone(aadt_clipped,        "AADT segments")


# ── Done ─────────────────────────────────────────────────────────────────────
print("\n=== Corridor clip complete ===")
print(f"All clipped files saved to: {PROCESSED_DIR}")
print("\nNext step: run python 03_crash_analysis.py")
