"""Built-in demo: 680 m around World Trade Center Bengaluru (stored OSM footprints
and Open Buildings 2.5D 2023 heights), so the tool can be tried without signing in."""
from __future__ import annotations

import os

import pandas as pd
import geopandas as gpd

from .aoi import read_aoi
from .osm import clean

DEMO_DIR = os.path.join(os.path.dirname(__file__), "data", "demo")


def demo_data():
    with open(os.path.join(DEMO_DIR, "aoi.kml"), "rb") as f:
        aoi = read_aoi(f.read(), "aoi.kml")
    b = gpd.read_file(os.path.join(DEMO_DIR, "buildings.gpkg"))
    utm = aoi.estimate_utm_crs()
    geom = aoi.geometry.union_all() if hasattr(aoi.geometry, "union_all") else aoi.geometry.unary_union
    b = clean(b, geom, utm)
    sat = pd.read_csv(os.path.join(DEMO_DIR, "heights.csv")).rename(
        columns={"h_median": "sat_h_median", "h_p90": "sat_h_p90"})
    sat["sat_presence"] = float("nan")
    return aoi, b, sat
