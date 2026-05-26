# Pennsylvania Avenue NW — Multimodal Corridor Analysis

A reproducible, open-source safety and capacity analysis of Pennsylvania Avenue NW in Washington, DC, built entirely from publicly available DDOT, WMATA, and OpenStreetMap data. This project identifies crash hotspots, assesses transit accessibility, and evaluates lane reallocation feasibility along the active DDOT Pennsylvania Avenue West Streetscape Project corridor (17th–22nd St NW).

**Tableau Dashboard:** [Pennsylvania Avenue NW · DDOT Portfolio Project](https://public.tableau.com/app/profile/oluwasegun.adegoke/viz/PennsylvaniaAvenueNWDDOTPortfolioProject/PennsylvaniaAvenueNWDDOTPortfolioProject) 
**Author:** Oluwasegun (David) Adegoke | [github.com/Davidavid45](https://github.com/Davidavid45)

---

## The Impact Zone

The **impact zone** is the 0.82-mile segment of Pennsylvania Avenue NW between **17th and 22nd Streets NW** — the active construction footprint of DDOT's Pennsylvania Avenue West Streetscape Project (construction began July 2025, estimated completion 2027). This segment is the analytical focus of this project: it has the highest crash density on the corridor, carries 8 bus routes, and is the subject of a proposed lane reduction from 6 to 4–5 lanes.

---

## Motivation

Pennsylvania Avenue NW is one of DC's most complex multimodal corridors, running from the US Capitol to Georgetown and carrying significant vehicle traffic, 8 bus routes, bicycle infrastructure, and dense pedestrian activity. DDOT's active streetscape project makes this an ideal real-world context for before-construction analysis.

This project applies the core methods of DDOT's data science function: integrating heterogeneous spatial data, computing corridor-level safety and capacity metrics, and communicating findings in decision-ready formats across three dashboards.

---

## Key Findings

**Safety Analysis**
- **1,578 total crashes** recorded along the corridor between January 2020 and early 2026
- **3 fatal crashes** during the study period
- Mode split: vehicle 99.3% (1,567) | pedestrian 0.6% (9) | bicycle 0.1% (2)
- **Impact zone: 464 crashes** (29% of corridor total) in just 0.82 miles
- **Crash rate differential: 1.6×** — impact zone averages 565.9 crashes/mile vs. 343.8/mile for the rest of the corridor
- Crash volume peaked in 2023–2024 and has been declining through 2025–2026
- Segment 5 (1.0–1.25 mile mark) is the single highest-density crash segment with 235 crashes

**Transit Coverage**
- **35 WMATA bus stops** serve the full corridor; **8 stops** are within the impact zone
- **8 bus routes** operate through the impact zone: 1Y, 16Y, 32, 33, 36, 38B, 80, and others
- Busiest stop by daily trips: **Pennsylvania Ave NW + 22nd St NW** (AM peak: 222 trips, PM peak: 339 trips)
- All impact zone stops fall within the 400m walkable catchment standard

**Lane Removal Feasibility**
- Penn Ave current AADT: **10,286 vehicles/day** — only **21% of its 48,000-vehicle daily capacity**
- The corridor is severely underutilized, making lane reduction low-risk for vehicular throughput
- **K St NW spare capacity: 10,290 vehicles/day** after absorbing 5% diversion (67% → 72% utilization)
- **Constitution Ave NW spare capacity: 19,356 vehicles/day** at 60% current utilization — substantial absorption buffer

---

## Data Sources

| Dataset | Source | Format | Key Fields | Limitations |
|---|---|---|---|---|
| Crashes in DC | DC Open Data (ArcGIS REST) | GeoJSON | REPORTDATE, FATAL_*, MAJORINJURIES_*, LATITUDE, LONGITUDE | Reported crashes only; under-reporting of minor incidents |
| Vision Zero Safety | DC Open Data (DDOT FeatureServer) | GeoJSON | REQUESTTYPE, STATUS, LATITUDE, LONGITUDE | Self-reported; biased toward higher-engagement neighborhoods |
| WMATA Bus GTFS | MobilityDatabase (feed #1846) | ZIP / GTFS CSV | stop_id, stop_lat/lon, trip_id, departure_time | Schedule-based; does not reflect real-time delays or ridership |
| OSM Street Network | OpenStreetMap via OSMnx | NetworkX → GeoDataFrame | name, geometry, highway | Volunteer-maintained; recent changes may lag |
| Bike Lanes | DC Open Data (DDOT) | GeoJSON | BIKE_LANE_TYPE, PROTECTED | No temporary or proposed lanes |
| AADT Counts | DC Open Data (2024 HPMS) | GeoJSON | AADT, AADT_YEAR, ROUTEID | Annual average; not peak-hour or real-time |

All API endpoints and GTFS URLs are stored in `config.yaml` and were verified working as of May 2026.

---

## Data Governance Notes

| Dataset | Good for | Do NOT use for |
|---|---|---|
| Crashes in DC | Hotspot identification, mode trend analysis, year-over-year comparisons | Total crash volume estimation; real-time monitoring |
| Vision Zero Safety | Perceived safety gaps, community concern mapping | Objective safety measurement; cross-neighborhood comparisons |
| WMATA GTFS | Scheduled headways, stop coverage, frequency analysis | Real-time reliability; actual ridership; on-time performance |
| OSM / OSMnx | Corridor geometry, network connectivity, walk access | Official DC road inventory; legal classifications |
| Bike Lanes (DDOT) | Existing infrastructure mapping, gap analysis | Temporary lanes; construction-zone conditions |
| AADT (HPMS) | Annual average volume, capacity utilization estimates | Peak-hour analysis; real-time traffic conditions |

---

## Methodology

**Corridor Extraction.** The Pennsylvania Avenue NW corridor geometry is derived from OpenStreetMap using OSMnx, filtering edges named "Pennsylvania Avenue Northwest." All matched segments are unioned into a single corridor centerline. A 100-meter buffer polygon is computed in UTM Zone 18N (EPSG:32618) for spatial accuracy, then reprojected to WGS84 (EPSG:4326) for compatibility with all other datasets.

**Crash Analysis.** DC Crashes records from 2020 onward are pulled via the DC Open Data ArcGIS REST API and spatially clipped to the corridor buffer. Each crash is classified by primary mode (pedestrian > bicycle > vehicle, prioritizing the most vulnerable road user) and severity. The corridor is divided into 0.25-mile segments; crash counts per segment per mode are computed for Tableau visualization. The impact zone bounding box (17th–22nd St NW) is defined in `config.yaml` and applied as a spatial flag on each crash record.

**Transit Accessibility Analysis.** The WMATA static GTFS feed is parsed to extract all bus stops within the corridor buffer. AM peak (7–9am) and PM peak (4–7pm) scheduled trip counts are computed from `stop_times.txt` for each stop. Stop spacing along the corridor centerline is calculated using OSMnx linear referencing. A 400-meter walkable catchment buffer is generated around each stop to assess pedestrian access coverage.

**Parallel Route Capacity Analysis.** AADT data from DC's 2024 HPMS survey is clipped to five parallel corridors (K St NW, I St NW, H St NW, Virginia Ave NW, Constitution Ave NW). Lane counts are verified from OpenStreetMap. Daily capacity is estimated at 8,000 vehicles per lane. Post-diversion AADT is modeled by redistributing 5% of Penn Ave traffic to K St and Constitution Ave — the two routes with the most spare capacity.

---

## Project Structure

```
penn-ave-corridor-analysis/
├── config.yaml                    # All API endpoints, bbox, analysis parameters
├── requirements.txt               # Python dependencies
│
├── 01_data_pull.py                # Pull raw data from DC Open Data APIs + WMATA GTFS
├── 02_corridor_clip.py            # Build corridor geometry + clip all datasets
├── 03_crash_analysis.py           # Crash classification, segmentation, density
├── 04_transit_analysis.py         # GTFS stop analysis, AM/PM peak trips
├── 05_export_tableau.py           # Validate + export 4 CSVs for Tableau
├── 06_parallel_routes.py          # Parallel route AADT + diversion capacity
│
├── notebooks/
│   └── viz_prototype.ipynb        # Python prototype of all Tableau views
│
└── data/
    ├── raw/                       # Downloaded source files (gitignored)
    ├── processed/                 # Clipped GeoJSONs (corridor, crashes, transit, etc.)
    └── tableau/
        ├── crashes_corridor.csv           # 1,578 crashes with mode, severity, segment
        ├── corridor_segments.csv          # 0.25-mi segments with geometry + stats
        ├── transit_corridor.csv           # 35 stops with AM/PM peak trip counts
        ├── parallel_routes.csv            # 6 routes with AADT + capacity + diversion
        ├── impact_zone_comparison.csv     # Pre-computed crash rates (manually maintained)
        ├── data_governance.csv            # Dataset provenance + use/don't-use guidance
        ├── viz_02b_corridor_map.html      # Interactive Folium/Leaflet corridor map
        └── viz_lane_removal_prototype.html # Plotly lane removal analysis prototype
```

---

## How to Run

**1. Clone the repository**
```bash
git clone https://github.com/Davidavid45/penn-ave-corridor-analysis.git
cd penn-ave-corridor-analysis
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Pull raw data** (~2–5 min, requires internet)
```bash
python 01_data_pull.py
```
Downloads DC Open Data crash records, Vision Zero reports, bike lanes, AADT, and WMATA GTFS. Saves to `data/raw/`.

**4. Extract corridor and clip datasets** (~1–2 min)
```bash
python 02_corridor_clip.py
```
Builds the Penn Ave corridor geometry and 100m buffer via OSMnx, clips all raw datasets to the corridor. Saves to `data/processed/`.

**5. Run crash analysis**
```bash
python 03_crash_analysis.py
```
Classifies crashes by mode and severity, assigns each to a 0.25-mile segment, flags impact zone crashes, computes density statistics.

**6. Run transit analysis**
```bash
python 04_transit_analysis.py
```
Parses WMATA GTFS, computes AM/PM peak trip counts per stop, calculates stop spacing and walkable catchment.

**7. Run parallel routes analysis**
```bash
python 06_parallel_routes.py
```
Pulls AADT for parallel corridors, estimates lane capacity, models post-diversion utilization.

**8. Export Tableau CSVs**
```bash
python 05_export_tableau.py
```
Validates all output CSVs and writes `data_governance.csv`. Final outputs in `data/tableau/`.

**9. Open Tableau**  
Connect Tableau Public to the CSVs in `data/tableau/`. The Tableau workbook (`penn_ave_corridor_analysis.twbx`) contains all three dashboards pre-built.

---

## Tableau Dashboards

The Tableau story (`Pennsylvania Avenue NW · DDOT Portfolio Project`) contains three dashboards:

1. **Safety Analysis** — Crash density by 0.25-mile segment (Vehicle/Pedestrian/Bicycle), year-over-year trend (2020–2026), and impact zone vs. rest-of-corridor crash rate comparison
2. **Transit Coverage** — Top stops by daily trips in the impact zone, AM vs. PM peak frequency by stop, and KPI tiles for stops, peak trips, and routes in zone
3. **Lane Removal Impact** — Penn Ave capacity utilization, parallel route spare capacity table, and AADT vs. daily capacity grouped bar chart

---

## Limitations

- Crash data reflects **reported** incidents only. Pedestrian and bicycle crashes are likely undercounted relative to vehicle crashes due to differential reporting rates.
- WMATA GTFS data is **schedule-based** and does not reflect actual ridership, real-time delays, or service disruptions.
- AADT figures are **2024 annual averages** and do not capture peak-hour conditions, seasonal variation, or post-COVID recovery trends.
- The impact zone bounding box approximates the DDOT construction footprint; the official project boundary may differ slightly.
- Parallel route diversion modeling assumes uniform redistribution and does not account for route preference, intersection capacity, or induced demand.
