"""Settings for the 3D building extractor."""
from __future__ import annotations

from dataclasses import dataclass, asdict

from datetime import date as _date

LATEST_HEIGHT_YEAR = 2023        # Open Buildings 2.5D Temporal covers 2016-2023
TARGET_YEAR = _date.today().year # modelled estimate (Sentinel-1/2) for the current year

OVERPASS_URLS = ["https://overpass-api.de/api",
                 "https://overpass.private.coffee/api",
                 "https://overpass.kumi.systems/api",
                 "https://maps.mail.ru/osm/tools/overpass/api"]

# floors = a + b * height, fitted on 11,216 OSM-tagged buildings in Bengaluru
# (used when a study area has too few local floor tags to fit its own model)
DEFAULT_FLOOR_MODEL = {"intercept": 0.67, "slope": 0.301, "source": "Bengaluru calibration (n = 11,216)"}

FLOOR_BANDS = [0, 1, 3, 6, 10, 15, 10_000]
FLOOR_NAMES = ["1", "2–3", "4–6", "7–10", "11–15", "16+"]
FLOOR_COLOURS = ["#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c", "#08306b"]
NO_HEIGHT_COLOUR = "#bdbdbd"

SOURCE_COLOURS = {"osm_height": "#d95f02", "osm_levels": "#e6ab02", "satellite": "#1b9e77",
                  "estimate": "#7570b3", "none": "#bdbdbd"}
SOURCE_LABELS = {"osm_height": "OSM height tag", "osm_levels": "OSM floor tag",
                 "satellite": "Satellite (Open Buildings 2.5D)", "estimate": "Modelled estimate (Sentinel-1/2)",
                 "none": "No height"}
CHANGE_COLOURS = {"new": "#d7191c", "taller": "#fdae61", "stable": "#d9d9d9", "lower": "#2c7bb6", "": "#d9d9d9"}
CHANGE_LABELS = {"new": "New since height year", "taller": "Taller (≥ 3 m)", "stable": "No clear change",
                 "lower": "Lower / demolished (≥ 3 m)"}


@dataclass
class Settings:
    height_year: int = LATEST_HEIGHT_YEAR   # year of satellite heights (2016-2023)
    estimate_target: bool = True            # also estimate heights for target_year from Sentinel-1/2 (needs Earth Engine)
    target_year: int = TARGET_YEAR          # year of the modelled estimate (default: current year)
    min_change_m: float = 3.0               # modelled change smaller than this is treated as no change
    est_presence_min: float = 0.4           # modelled presence needed to give a new building a height
    fill_google_footprints: bool = False    # add Google Open Buildings v3 footprints where OSM has none
    google_min_confidence: float = 0.75
    min_height_m: float = 2.5               # satellite heights below this are treated as missing
    presence_new_max: float = 0.3           # satellite presence below this in the height year -> "not seen"
    storey_m: float = 3.3                   # metres per floor when converting OSM floor tags to height
    ground_floor_m: float = 4.0
    min_calibration: int = 100              # local floor tags needed to fit the area's own model
    max_area_km2: float = 5000.0
    gee_scale_m: int = 4
    chunk_size: int = 2000
    workers: int = 6
    # --- large areas: download in tiles / batches, then compile ----------------------
    tile_above_km2: float = 10.0            # areas larger than this are downloaded from OSM tile by tile
    tile_km: float = 2.0                    # OSM tile edge (km); a failing tile is split into 4
    google_tile_km: float = 1.0             # Google Open Buildings footprint tile edge (km)
    max_split_depth: int = 2                # times a failing tile / Earth Engine batch may be split
    osm_pause_s: float = 1.0                # pause between Overpass requests
    allow_missing_tiles: bool = False       # True: continue if some tiles still fail (results incomplete)
    batch_cell_m: float = 1000.0            # Earth Engine batches group buildings from cells of this size
    ee_retries: int = 4                     # retries per Earth Engine batch before it is split
    cache_dir: str = ""                     # where finished tiles/batches are saved ("" = system temp folder)
    cache_days: float = 7.0                 # cached pieces older than this are downloaded again (0 = forever)

    def as_dict(self):
        return asdict(self)
