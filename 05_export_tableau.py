"""
05_export_tableau.py
--------------------
Step 5: validates, cleans, and finalizes the 4 Tableau-ready CSV files.
Also produces the data_governance.csv — the key data governance deliverable.

WHY A SEPARATE EXPORT SCRIPT:
  Tableau requires clean flat CSVs with no nulls in key columns and
  consistent column names. This script acts as a quality gate — it checks
  each file and reports any issues before you open Tableau.

  The data_governance.csv demonstrates the "centralized data resource"
  role the DDOT job describes: documenting what each source is good for,
  its limitations, and recommended vs. not-recommended use cases.

Inputs (from data/tableau/ — built by scripts 03 and 04):
  crashes_corridor.csv
  transit_corridor.csv
  corridor_segments.csv

Outputs (all to data/tableau/):
  crashes_corridor.csv       — Validated & finalized
  transit_corridor.csv       — Validated & finalized
  corridor_segments.csv      — Validated & finalized
  data_governance.csv        — Data governance reference table
"""

import pathlib
import pandas as pd
import yaml

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

TABLEAU_DIR = pathlib.Path(__file__).parent / "data" / "tableau"
TABLEAU_DIR.mkdir(parents=True, exist_ok=True)


# ── Helper: validate CSV ──────────────────────────────────────────────────────
def validate_csv(path: pathlib.Path, required_cols: list, label: str):
    """Load a CSV, check for required columns and nulls, report status."""
    if not path.exists():
        print(f"  ✗ MISSING: {label} — {path.name} not found")
        return None

    df = pd.read_csv(path)
    issues = []

    for col in required_cols:
        if col not in df.columns:
            issues.append(f"missing column '{col}'")
        elif df[col].isna().any():
            null_count = df[col].isna().sum()
            issues.append(f"'{col}' has {null_count} nulls")

    if issues:
        print(f"  ⚠ {label} ({len(df)} rows) — issues: {'; '.join(issues)}")
    else:
        print(f"  ✓ {label} ({len(df)} rows) — all required columns present, no nulls")

    return df


# ── Step 1: Validate all Tableau CSVs ────────────────────────────────────────
print("=== Step 1: Validating Tableau exports ===\n")

crashes_df = validate_csv(
    TABLEAU_DIR / "crashes_corridor.csv",
    required_cols=["LATITUDE", "LONGITUDE", "MODE", "SEVERITY", "YEAR"],
    label="crashes_corridor.csv"
)

transit_df = validate_csv(
    TABLEAU_DIR / "transit_corridor.csv",
    required_cols=["stop_lat", "stop_lon", "am_peak_trips", "pm_peak_trips"],
    label="transit_corridor.csv"
)

segments_df = validate_csv(
    TABLEAU_DIR / "corridor_segments.csv",
    required_cols=["segment_id", "crash_density_total"],
    label="corridor_segments.csv"
)


# ── Step 2: Clean any nulls in crashes file ───────────────────────────────────
print("\n=== Step 2: Final cleaning ===")

if crashes_df is not None:
    before = len(crashes_df)
    crashes_df = crashes_df.dropna(subset=["LATITUDE", "LONGITUDE"])
    crashes_df["MODE"]     = crashes_df["MODE"].fillna("vehicle")
    crashes_df["SEVERITY"] = crashes_df["SEVERITY"].fillna("minor_or_property")
    crashes_df["YEAR"]     = crashes_df["YEAR"].fillna(0).astype(int)
    after = len(crashes_df)
    if before != after:
        print(f"  Dropped {before - after} crashes with missing coordinates")
    crashes_df.to_csv(TABLEAU_DIR / "crashes_corridor.csv", index=False)
    print(f"  crashes_corridor.csv finalized ({after} rows)")

if transit_df is not None:
    transit_df = transit_df.fillna({
        "am_peak_trips": 0,
        "pm_peak_trips": 0,
        "total_trips":   0,
        "route_ids":     "",
        "stop_spacing_m": 0,
    })
    transit_df.to_csv(TABLEAU_DIR / "transit_corridor.csv", index=False)
    print(f"  transit_corridor.csv finalized ({len(transit_df)} rows)")

if segments_df is not None:
    segments_df = segments_df.fillna(0)
    segments_df.to_csv(TABLEAU_DIR / "corridor_segments.csv", index=False)
    print(f"  corridor_segments.csv finalized ({len(segments_df)} rows)")


# ── Step 3: Build data_governance.csv ────────────────────────────────────────
print("\n=== Step 3: Building data_governance.csv ===")

# This is the DATA GOVERNANCE deliverable — it documents every source used,
# what it's good for, what it should NOT be used for, and its known limitations.
# This is exactly the "centralized data resource" function the DDOT role requires.

governance_records = [
    {
        "dataset_name":      "Crashes in DC",
        "source":            "opendata.dc.gov (DC Open Data)",
        "access_method":     "ArcGIS REST API (GeoJSON)",
        "last_updated":      "Updated continuously; API reflects current MPD data",
        "format":            "GeoJSON / ArcGIS REST",
        "key_fields":        "REPORTDATE, FATAL_DRIVER, FATAL_BICYCLIST, FATAL_PEDESTRIAN, MAJORINJURIES_*, LATITUDE, LONGITUDE, WARD",
        "limitations":       "Reported crashes only; minor incidents are systematically under-reported. Geocoding errors in ~2% of records. Does not distinguish distracted driving causes.",
        "recommended_use":   "Crash hotspot identification, mode-specific safety analysis, year-over-year trend analysis, corridor risk scoring",
        "not_recommended":   "Total crash volume estimation (under-reporting bias); real-time incident monitoring; property-damage-only frequency analysis",
    },
    {
        "dataset_name":      "Vision Zero Safety Reports",
        "source":            "opendata.dc.gov (DC Open Data / Vision Zero DC)",
        "access_method":     "ArcGIS REST API (GeoJSON)",
        "last_updated":      "Updated as reports are submitted; no fixed cadence",
        "format":            "GeoJSON / ArcGIS REST",
        "key_fields":        "REQUESTTYPE (Pedestrian/Bicyclist/Motorist), STATUS, LATITUDE, LONGITUDE",
        "limitations":       "Self-reported by residents; significant geographic bias toward higher-income, higher-engagement neighborhoods. Not a complete safety inventory. Does not capture crash data.",
        "recommended_use":   "Perceived safety gap analysis; community engagement mapping; complement to crash data to identify high-concern areas not yet in crash data",
        "not_recommended":   "Objective safety measurement; comparing safety between neighborhoods with different engagement rates; replacing crash data",
    },
    {
        "dataset_name":      "WMATA Bus GTFS",
        "source":            "WMATA (Washington Metropolitan Area Transit Authority)",
        "access_method":     "Static GTFS ZIP download (gtfs.wmata.com/gtfs/bus-gtfs.zip)",
        "last_updated":      "Published with each service change (typically quarterly)",
        "format":            "GTFS (ZIP of CSV files: stops, routes, trips, stop_times, calendar)",
        "key_fields":        "stop_id, stop_lat, stop_lon, route_id, trip_id, departure_time, arrival_time",
        "limitations":       "Schedule-based only; does not reflect real-time delays, cancellations, or bus bunching. Service changes between GTFS releases not captured. Headway calculation assumes all scheduled trips operate.",
        "recommended_use":   "Stop coverage mapping, scheduled headway analysis, AM/PM peak frequency, stop spacing, transit access gap identification",
        "not_recommended":   "Actual ridership levels; real-time service reliability; on-time performance analysis (use WMATA GTFS-RT for real-time)",
    },
    {
        "dataset_name":      "OpenStreetMap Street Network (via OSMnx)",
        "source":            "OpenStreetMap contributors / OSMnx Python library",
        "access_method":     "OSMnx API (ox.graph_from_place)",
        "last_updated":      "OSM is continuously edited by volunteers; data pulled at analysis date",
        "format":            "NetworkX graph → GeoDataFrame (LineString geometries)",
        "key_fields":        "name (street name), geometry (LineString), highway (road type), length",
        "limitations":       "OSM completeness varies; recent infrastructure changes (new bike lanes, road reconfigurations) may lag by weeks to months. Name matching requires exact OSM street name conventions. Not authoritative for DC legal road classifications.",
        "recommended_use":   "Corridor geometry extraction, intersection density, network connectivity, pedestrian walkability analysis, walk-access distance calculation",
        "not_recommended":   "Official DC road inventory; AADT volume data; legal jurisdiction questions; real-time conditions",
    },
    {
        "dataset_name":      "Bike Lanes",
        "source":            "opendata.dc.gov (DDOT)",
        "access_method":     "ArcGIS REST API (GeoJSON)",
        "last_updated":      "Updated by DDOT; check opendata.dc.gov for current date",
        "format":            "GeoJSON / ArcGIS REST (LineString geometries)",
        "key_fields":        "GEOMETRY, BIKE_LANE_TYPE, PROTECTED",
        "limitations":       "Does not include temporary, proposed, or under-construction lanes. Construction zones (e.g. Penn Ave Streetscape 17th-22nd St) may not be reflected. No directional information.",
        "recommended_use":   "Existing cycling infrastructure mapping, protected vs. unprotected lane analysis, corridor bike infrastructure gap identification",
        "not_recommended":   "Temporary lane configuration; construction-zone conditions; planned future network analysis",
    },
]

governance_df = pd.DataFrame(governance_records)
gov_path = TABLEAU_DIR / "data_governance.csv"
governance_df.to_csv(gov_path, index=False)
print(f"  data_governance.csv → {len(governance_df)} datasets documented, saved to {gov_path}")


# ── Step 4: Final summary ─────────────────────────────────────────────────────
print("\n=== FINAL STATUS — All 4 Tableau files ===")

tableau_files = {
    "crashes_corridor.csv":    ["LATITUDE", "LONGITUDE", "MODE", "SEVERITY", "YEAR"],
    "transit_corridor.csv":    ["stop_lat", "stop_lon", "am_peak_trips"],
    "corridor_segments.csv":   ["segment_id", "crash_density_total"],
    "data_governance.csv":     ["dataset_name", "source", "limitations"],
}

all_good = True
for filename, req_cols in tableau_files.items():
    fpath = TABLEAU_DIR / filename
    if fpath.exists():
        df = pd.read_csv(fpath)
        missing = [c for c in req_cols if c not in df.columns]
        status = "✓ READY" if not missing else f"✗ MISSING COLS: {missing}"
        print(f"  {status:12}  {filename:35} ({len(df)} rows)")
        if missing:
            all_good = False
    else:
        print(f"  ✗ FILE NOT FOUND  {filename}")
        all_good = False

print()
if all_good:
    print("All 4 Tableau CSV files are ready.")
    print(f"Location: {TABLEAU_DIR}")
    print("\nNext steps:")
    print("  1. Open Tableau Public (free)")
    print("  2. Connect to → Text File → crashes_corridor.csv")
    print("  3. Build your dashboard, then publish to Tableau Public")
    print("  4. Copy the public URL and add it to README.md")
    print("  5. Run: python outputs/memo/ (write executive memo)")
else:
    print("Some files need attention — see issues above.")
    print("Re-run scripts 03 and 04 to regenerate missing files.")
