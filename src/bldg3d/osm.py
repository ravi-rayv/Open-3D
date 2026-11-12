"""Current OpenStreetMap building footprints (downloaded live, so they reflect
OSM as of the day you run the tool)."""
from __future__ import annotations

import re
import time
from datetime import date

import numpy as np
import pandas as pd
import geopandas as gpd

from .config import OVERPASS_URLS

KEEP = ["building", "building_levels", "building_min_level", "height", "min_height", "roof_levels",
        "name", "addr_housenumber", "addr_street", "start_date"]


def _num(v):
    """Parse OSM numeric tags such as '12', '12.5 m', '40 ft', '3;4'."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    s = str(v).strip().lower().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return np.nan
    x = float(m.group(0))
    if "ft" in s or "'" in s:
        x *= 0.3048
    return x


def _overpass(poly, log, timeout):
    """One tile, trying each Overpass server in turn; empty GeoDataFrame if the tile has no buildings."""
    import osmnx as ox
    ox.settings.requests_timeout = timeout
    ox.settings.use_cache = True
    last = None
    for url in OVERPASS_URLS:
        try:
            ox.settings.overpass_url = url
            g = ox.features_from_polygon(poly, tags={"building": True})
            g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index()
            return g.rename(columns={"id": "osmid", "element": "osm_type", "element_type": "osm_type"})
        except Exception as e:  # noqa: BLE001
            if "No matching features" in str(e) or "InsufficientResponse" in type(e).__name__:
                return gpd.GeoDataFrame(geometry=[], crs=4326)
            last = e
            log(f"OSM: {url} failed ({type(e).__name__}); trying the next server")
            time.sleep(3)
    raise RuntimeError(f"All Overpass servers failed: {last}")


def fetch_buildings(aoi_geom, utm, log=print, timeout=600, s=None, cache=None, progress=None) -> gpd.GeoDataFrame:
    """OSM buildings for the study area. Large areas are downloaded in tiles and compiled (see :mod:`tiling`)."""
    from . import tiling
    from .config import Settings
    s = s or Settings()
    cache = cache or tiling.cache_dir(s, "bldg3d")
    log("OSM: downloading buildings")
    keep = [k.replace("building_", "building:").replace("roof_", "roof:").replace("addr_", "addr:")
            for k in KEEP]
    gdf = tiling.fetch_tiled(aoi_geom, utm, lambda poly: _overpass(poly, log, timeout), s, cache, "buildings",
                             keep_cols=keep, log=log, progress=progress)
    if gdf.empty:
        return clean(gpd.GeoDataFrame(geometry=[], crs=4326), aoi_geom, utm)
    gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])]
    out = clean(gdf, aoi_geom, utm)
    log(f"OSM: {len(out):,} buildings (OpenStreetMap as of {date.today():%d %b %Y})")
    return out


def clean(gdf, aoi_geom, utm) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(4326)
    gdf.columns = [c.replace(":", "_") for c in gdf.columns]
    if len(gdf):
        inside = gdf.to_crs(utm).representative_point().to_crs(4326).within(aoi_geom).values
        gdf = gdf[inside]
    for c in ["osm_type", "osmid"] + KEEP:
        if c not in gdf.columns:
            gdf[c] = np.nan
    if len(gdf) and gdf["osmid"].isna().all():
        gdf["osmid"] = np.arange(len(gdf))
        gdf["osm_type"] = "x"
    if "bid" not in gdf.columns:
        gdf["bid"] = gdf["osm_type"].astype(str).str[0] + gdf["osmid"].astype(str)
    for c in ["building_levels", "building_min_level", "height", "min_height", "roof_levels"]:
        gdf[c] = gdf[c].map(_num)
    for c in ["name", "addr_housenumber", "addr_street", "start_date"]:
        gdf[c] = gdf[c].astype(object).where(gdf[c].notna(), "").astype(str)
    gdf["building"] = gdf["building"].fillna("yes").astype(str)
    gdf["footprint_source"] = "osm"
    gdf = gdf[["bid", "osmid", "osm_type", "footprint_source"] + KEEP + ["geometry"]]
    return gpd.GeoDataFrame(gdf.drop_duplicates("bid").reset_index(drop=True), geometry="geometry", crs=4326)
