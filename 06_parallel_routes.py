"""
06_parallel_routes.py
---------------------
Analyzes traffic volume (AADT) on streets parallel to Pennsylvania Avenue NW
in the impact zone (17th–22nd St NW) to assess diversion capacity.

THE CORE QUESTION THIS ANSWERS:
  "How loaded are those parallel streets already, and how much MORE traffic
  can they handle before they hit capacity?"

  A parallel route already at 85% capacity has almost no room for diverted
  traffic. A route at 40% capacity could absorb a significant diversion.



CAPACITY ASSUMPTIONS:
  800 veh/hour/lane is the HCM LOS C threshold for urban signalized arterials.
  We use 10 effective peak hours to convert hourly capacity to daily.
  This is a sketch-level estimate — a full traffic study would use peak-hour
  counts, turning movement counts, and signal timing to refine these numbers.
  These assumptions are intentionally conservative (real capacity may be
  slightly higher depending on signal coordination).

CONFIRMED LANE COUNTS (from DC Open Data street centerlines, May 2026):
  Pennsylvania Ave NW : 6 lanes (ROUTEID 11069812)
  K St NW             : 4 lanes (ROUTEID 11050892)
  I St NW             : 2 lanes (ROUTEID 11047772)
  H St NW             : 3 lanes (ROUTEID 11042442)
  Virginia Ave NW     : 4 lanes (estimated — diagonal, not in centerline layer)
  Constitution Ave NW : 6 lanes (ROUTEID 11025352)

Inputs:
  data/raw/aadt_raw.geojson     — full DC AADT (from 01_data_pull.py)
  config.yaml

Outputs (to data/processed/ and data/tableau/):
  parallel_routes.csv           — one row per parallel route with AADT,
                                  capacity, utilization, and diversion headroom
"""

import pathlib
import json
import pandas as pd
import geopandas as gpd
import yaml

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

PROCESSED_DIR = pathlib.Path(__file__).parent / "data" / "processed"
TABLEAU_DIR   = pathlib.Path(__file__).parent / "data" / "tableau"
TABLEAU_DIR.mkdir(parents=True, exist_ok=True)

# Impact zone bounding box (from config) — we search slightly wider for
# parallel streets north and south of Penn Ave
IZ = config["corridor"]["impact_zone"]
LON_MIN = IZ["min_lon"] - 0.002   # small buffer west
LON_MAX = IZ["max_lon"] + 0.002   # small buffer east
LAT_MIN = IZ["min_lat"] - 0.015   # ~1.5km south to catch Constitution Ave
LAT_MAX = IZ["max_lat"] + 0.015   # ~1.5km north to catch K St

# ── Parallel route definitions ────────────────────────────────────────────────
# Each route: confirmed ROUTEID from DC Open Data, confirmed lane count,
# approximate latitude center (used to verify we're getting the right segments).
#
# HOW WE GOT THESE:
#   Queried the DC street centerlines API (Transportation_WebMercator/MapServer/38)
#   with a WHERE clause filtering to these street names. Verified ROUTEID and
#   TOTALTRAVELLANES fields from the API response.
#
# WHY LANE COUNT MATTERS:
#   Daily capacity = lanes × 8,000 vehicles/day (HCM urban arterial, LOS C).
#   More lanes = more room to absorb diverted Penn Ave traffic.

PARALLEL_ROUTES = [
    {
        "name":        "Pennsylvania Ave NW (impact zone)",
        "routeid":     "11069812",
        "lanes":       6,      # confirmed from centerline data
        "lat_center":  38.898, # Penn Ave runs at ~38.897–38.900 in this zone
        "role":        "study_corridor",
        "notes":       "Current 6-lane configuration; lane removal proposal would reduce to 4 or 5"
    },
    {
        "name":        "K St NW",
        "routeid":     "11050892",
        "lanes":       4,      # confirmed
        "lat_center":  38.902,
        "role":        "parallel_north",
        "notes":       "Major east-west arterial ~0.4km north of Penn Ave; primary diversion candidate"
    },
    {
        "name":        "I St NW",
        "routeid":     "11047772",
        "lanes":       2,      # confirmed
        "lat_center":  38.900,
        "role":        "parallel_north",
        "notes":       "Minor street between Penn Ave and K St; limited capacity"
    },
    {
        "name":        "H St NW",
        "routeid":     "11042442",
        "lanes":       3,      # confirmed
        "lat_center":  38.897,
        "role":        "parallel_south",
        "notes":       "One block south of Penn Ave; moderate capacity"
    },
    {
        "name":        "Virginia Ave NW",
        "routeid":     None,   # diagonal — not in centerlines layer; spatial filter only
        "lanes":       4,      # estimated from field observation / DC street standards
        "lat_center":  38.893,
        "role":        "parallel_diagonal",
        "notes":       "Diagonal parallel to Penn Ave; serves Foggy Bottom–GWU area"
    },
    {
        "name":        "Constitution Ave NW",
        "routeid":     "11025352",
        "lanes":       6,      # confirmed
        "lat_center":  38.893,
        "role":        "parallel_south",
        "notes":       "Federal highway ~0.5km south; high capacity but serves different trip patterns"
    },
]

# ── HCM capacity parameters ───────────────────────────────────────────────────
# Urban signalized arterial, LOS C threshold
# Source: Highway Capacity Manual 7th Edition, Chapter 16
CAPACITY_PER_LANE_PER_HOUR = 800    # vehicles/hour/lane at LOS C
PEAK_HOURS_PER_DAY         = 10     # effective peak hours (AM + PM combined)
DAILY_CAPACITY_PER_LANE    = CAPACITY_PER_LANE_PER_HOUR * PEAK_HOURS_PER_DAY  # 8,000

# ── Step 1: Load full DC AADT dataset ─────────────────────────────────────────
print("=== Step 1: Loading full DC AADT dataset ===")
aadt_path = pathlib.Path(__file__).parent / "data" / "raw" / "aadt_raw.geojson"
if not aadt_path.exists():
    raise FileNotFoundError("aadt_raw.geojson not found — run 01_data_pull.py first")

aadt_gdf = gpd.read_file(aadt_path)
aadt_gdf = aadt_gdf[aadt_gdf["AADT"].notna()].copy()
print(f"  Loaded {len(aadt_gdf):,} AADT segments with volume data (out of {len(gpd.read_file(aadt_path)):,} total)")
print(f"  AADT range across DC: {int(aadt_gdf['AADT'].min()):,} – {int(aadt_gdf['AADT'].max()):,} vehicles/day")


# ── Step 2: Filter to impact zone longitude band ──────────────────────────────
print(f"\n=== Step 2: Filtering to impact zone area ===")
print(f"  Bounding box: lon {LON_MIN:.4f}–{LON_MAX:.4f}, lat {LAT_MIN:.4f}–{LAT_MAX:.4f}")

aadt_area = aadt_gdf.cx[LON_MIN:LON_MAX, LAT_MIN:LAT_MAX].copy()
print(f"  {len(aadt_area):,} segments in expanded impact zone area")


# ── Step 3: Match segments to each parallel route ────────────────────────────
print("\n=== Step 3: Matching AADT segments to parallel routes ===")

results = []

for route in PARALLEL_ROUTES:
    name    = route["name"]
    routeid = route["routeid"]
    lanes   = route["lanes"]

    # Filter by ROUTEID if we have one, otherwise spatial filter by latitude
    if routeid:
        segs = aadt_area[aadt_area["ROUTEID"] == routeid].copy()
    else:
        # Virginia Ave NW: diagonal — use lat band ± 0.003 degrees (~330m)
        lat_lo = route["lat_center"] - 0.003
        lat_hi = route["lat_center"] + 0.003
        segs = aadt_area.cx[LON_MIN:LON_MAX, lat_lo:lat_hi].copy()
        # Exclude Penn Ave (already captured by ROUTEID)
        segs = segs[segs["ROUTEID"] != "11069812"]

    n_segs   = len(segs)
    avg_aadt = segs["AADT"].mean() if n_segs > 0 else None
    max_aadt = segs["AADT"].max()  if n_segs > 0 else None

    # Capacity math
    daily_capacity = lanes * DAILY_CAPACITY_PER_LANE
    utilization    = (avg_aadt / daily_capacity * 100) if avg_aadt else None
    spare          = (daily_capacity - avg_aadt) if avg_aadt else None

    print(f"  {name:45} → {n_segs:3} segs | avg AADT: {int(avg_aadt or 0):>7,} | util: {utilization:.0f}%" if utilization else f"  {name:45} → {n_segs:3} segs | no data")

    results.append({
        "route_name":           name,
        "role":                 route["role"],
        "confirmed_lanes":      lanes,
        "routeid":              routeid or "spatial_filter",
        "n_segments":           n_segs,
        "avg_aadt":             round(avg_aadt, 0) if avg_aadt else None,
        "max_aadt":             round(max_aadt, 0) if max_aadt else None,
        "daily_capacity_est":   daily_capacity,
        "utilization_pct":      round(utilization, 1) if utilization else None,
        "spare_capacity":       round(spare, 0) if spare else None,
        "notes":                route["notes"],
    })

df = pd.DataFrame(results)


# ── Step 4: Diversion scenario analysis ──────────────────────────────────────
print("\n=== Step 4: Diversion scenario analysis ===")

# How much traffic is on Penn Ave in the impact zone?
penn_row = df[df["route_name"].str.contains("Pennsylvania")]
penn_avg_aadt = penn_row["avg_aadt"].values[0] if len(penn_row) > 0 else 0

# What is the estimated traffic removed by taking away one lane?
# One lane at LOS C carries ~800 veh/hr × 10 hrs = 8,000 veh/day
LANE_REMOVAL_VOLUME = DAILY_CAPACITY_PER_LANE
print(f"  Penn Ave avg AADT in impact zone:    {int(penn_avg_aadt or 0):,} vehicles/day")
print(f"  Estimated daily volume in removed lane: {LANE_REMOVAL_VOLUME:,} vehicles/day")
print(f"  (= 1 lane × {CAPACITY_PER_LANE_PER_HOUR} veh/hr × {PEAK_HOURS_PER_DAY} peak hrs — HCM LOS C)")
print()

# Parallel routes that could absorb diversion (exclude Penn Ave itself)
parallel_only = df[df["role"] != "study_corridor"].copy()
total_spare   = parallel_only["spare_capacity"].dropna().sum()

print(f"  Total spare capacity across parallel routes: {int(total_spare):,} vehicles/day")
print(f"  Lane removal adds {LANE_REMOVAL_VOLUME:,} diverted vehicles/day to the network")

if total_spare >= LANE_REMOVAL_VOLUME:
    print(f"  ✓ Parallel routes have ENOUGH spare capacity to absorb one lane removal")
    print(f"    ({int(total_spare):,} spare ≥ {LANE_REMOVAL_VOLUME:,} diverted)")
else:
    print(f"  ⚠ Parallel routes may NOT have enough spare capacity")
    print(f"    ({int(total_spare):,} spare < {LANE_REMOVAL_VOLUME:,} diverted)")

print()

# Show what happens if diverted traffic splits proportionally to spare capacity
print("  Proportional diversion scenario (traffic splits to available spare capacity):")
viable = parallel_only[parallel_only["spare_capacity"].notna()].copy()
total_spare_viable = viable["spare_capacity"].sum()

for _, row in viable.iterrows():
    share = row["spare_capacity"] / total_spare_viable if total_spare_viable > 0 else 0
    diverted_to_this = LANE_REMOVAL_VOLUME * share
    new_aadt = (row["avg_aadt"] or 0) + diverted_to_this
    new_util = new_aadt / row["daily_capacity_est"] * 100
    flag = "⚠" if new_util > 85 else "✓"
    print(f"    {flag} {row['route_name']:40} "
          f"current {row['utilization_pct']:.0f}% → after diversion {new_util:.0f}%")

# Add post-diversion columns to df
diversion_rows = []
for _, row in df.iterrows():
    if row["role"] == "study_corridor" or pd.isna(row["spare_capacity"]):
        diversion_rows.append({
            "post_diversion_aadt": None,
            "post_diversion_util_pct": None,
        })
    else:
        share = row["spare_capacity"] / total_spare_viable if total_spare_viable > 0 else 0
        diverted = LANE_REMOVAL_VOLUME * share
        new_aadt = (row["avg_aadt"] or 0) + diverted
        new_util = new_aadt / row["daily_capacity_est"] * 100
        diversion_rows.append({
            "post_diversion_aadt": round(new_aadt, 0),
            "post_diversion_util_pct": round(new_util, 1),
        })

df = pd.concat([df, pd.DataFrame(diversion_rows)], axis=1)


# ── Step 5: Export ────────────────────────────────────────────────────────────
print("\n=== Step 5: Exporting to Tableau CSV ===")

out_path = TABLEAU_DIR / "parallel_routes.csv"
df.to_csv(out_path, index=False)
print(f"  parallel_routes.csv → {len(df)} routes saved to {out_path}")

print("\n=== Full results table ===")
display_cols = ["route_name", "confirmed_lanes", "avg_aadt", "daily_capacity_est",
                "utilization_pct", "spare_capacity", "post_diversion_util_pct"]
print(df[display_cols].to_string(index=False))

print("\n=== Parallel routes analysis complete ===")
print("\nKey finding for memo:")
print(f"  Penn Ave impact zone AADT: {int(penn_avg_aadt or 0):,} veh/day")
print(f"  One-lane removal volume:   {LANE_REMOVAL_VOLUME:,} veh/day displaced")
print(f"  Parallel network spare:    {int(total_spare):,} veh/day available")
print(f"  Verdict: {'Network CAN absorb diversion' if total_spare >= LANE_REMOVAL_VOLUME else 'Network CANNOT easily absorb diversion — traffic study required'}")
