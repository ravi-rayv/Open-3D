"""Google Earth Engine: sign-in, satellite building heights and presence
(Open Buildings 2.5D Temporal) and, optionally, Google Open Buildings v3
footprints to fill gaps in OpenStreetMap.

Each user signs in with their own Earth Engine project. Only building IDs and
slightly simplified outlines are sent to Earth Engine.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import geopandas as gpd

TEMPORAL = "GOOGLE/Research/open-buildings-temporal/v1"
POLYGONS = "GOOGLE/Research/open-buildings/v3/polygons"


def init_earth_engine(project: str, service_account_json: str | bytes | None = None):
    """Local use: run ``earthengine authenticate`` once and pass your project ID.
    Hosted app: upload your OWN service-account key (JSON); it is used in memory only."""
    import ee
    if not project:
        raise ValueError("An Earth Engine project ID is required")
    creds = None
    if service_account_json:
        info = json.loads(service_account_json if isinstance(service_account_json, str)
                          else service_account_json.decode("utf-8"))
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=json.dumps(info))
    else:
        try:
            creds = ee.data.get_persistent_credentials()
        except Exception:  # noqa: BLE001
            creds = None
    if creds is not None and hasattr(creds, "with_quota_project"):
        try:
            creds = creds.with_quota_project(project)
        except Exception:  # noqa: BLE001
            pass
    try:
        ee.Reset()
    except Exception:  # noqa: BLE001
        pass
    ee.Initialize(creds, project=project) if creds is not None else ee.Initialize(project=project)
    ee.Number(1).getInfo()
    return True


def _retry(fn, tries=4):
    err = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(4 * (k + 1))
    raise RuntimeError(str(err)[:300])


def building_heights(gdf, aoi_geom, year, s, log=print, progress=None, cache=None, utm=None) -> pd.DataFrame:
    """Median and 90th-percentile height, and median presence confidence, per building.

    Buildings are sent in spatially compact, cached batches that split on failure (see :mod:`tiling`)."""
    import ee
    from . import tiling
    cache = cache or tiling.cache_dir(s, "bldg3d")
    col = ee.ImageCollection(TEMPORAL).filterBounds(tiling.ee_region(aoi_geom))
    img = (col.filterDate(f"{year}-01-01", f"{year + 1}-01-01")
              .select(["building_height", "building_presence"]).mosaic())
    reducer = ee.Reducer.median().combine(ee.Reducer.percentile([90]), "", True)
    cols = ["bid", "building_height_median", "building_height_p90", "building_presence_median"]

    def request(ch):
        fc = ee.FeatureCollection(json.loads(ch.to_json()))
        st = (img.reduceRegions(collection=fc, reducer=reducer, scale=s.gee_scale_m, tileScale=4)
                 .map(lambda f: f.select(cols, None, False)))
        rows = st.getInfo()["features"]
        return pd.DataFrame([r["properties"] for r in rows], columns=cols)

    df = tiling.run_batches(request, gdf, cols, s, cache, f"heights {year}|scale={s.gee_scale_m}|{TEMPORAL}",
                            utm=utm, log=log, progress=progress)
    return df.rename(columns={
        "building_height_median": "sat_h_median", "building_height_p90": "sat_h_p90",
        "building_presence_median": "sat_presence"})


def google_footprints(aoi_geom, utm, min_conf, log=print, progress=None, tile_m=None, s=None,
                      cache=None) -> gpd.GeoDataFrame:
    """Google Open Buildings v3 polygons (imagery up to 2023), downloaded in cached tiles.

    A tile with too many buildings for one request (Earth Engine returns at most 5,000) is split."""
    import ee
    from shapely.geometry import mapping
    from . import tiling
    from .config import Settings
    s = s or Settings()
    cache = cache or tiling.cache_dir(s, "bldg3d")
    tile_km = (tile_m / 1000.0) if tile_m else s.google_tile_km
    fc_all = ee.FeatureCollection(POLYGONS).filter(ee.Filter.gte("confidence", min_conf))

    def query(t):
        fc = fc_all.filterBounds(ee.Geometry(mapping(t))).select(["confidence", "area_in_meters"])
        feats = fc.limit(5001).getInfo()["features"]
        if len(feats) > 5000:
            raise RuntimeError("more than 5000 footprints in this tile")
        if not feats:
            return gpd.GeoDataFrame(geometry=[], crs=4326)
        return gpd.GeoDataFrame.from_features(feats, crs=4326)

    def query_retry(t):
        err = None
        for k in range(max(int(s.ee_retries), 1)):
            try:
                return query(t)
            except Exception as e:  # noqa: BLE001
                err = e
                if "5000" in str(e) or "memory" in str(e).lower():
                    break            # too big: let fetch_tiled split the tile
                time.sleep(4 * (k + 1))
        raise RuntimeError(str(err)[:300])

    g = tiling.fetch_tiled(aoi_geom, utm, query_retry, s, cache, f"google_v3 conf>={min_conf}", log=log,
                           progress=progress, id_cols=(), tile_km=tile_km, always_tile=True,
                           workers=s.workers, pause=0, source="Earth Engine (Google footprints)")
    if not len(g):
        return gpd.GeoDataFrame(geometry=[], crs=4326)
    cen = g.to_crs(utm).representative_point().to_crs(4326)
    g = g[cen.within(aoi_geom).values]
    key = g.geometry.representative_point().apply(lambda p: (round(p.x, 6), round(p.y, 6)))
    return g.loc[~key.duplicated()].reset_index(drop=True)


def add_google_gaps(osm, google, utm, log=print) -> gpd.GeoDataFrame:
    """Keep Google footprints that do not overlap any OSM footprint."""
    if google is None or not len(google):
        return osm
    pts = gpd.GeoDataFrame(geometry=google.geometry.representative_point(), crs=4326)
    hit = gpd.sjoin(pts, osm[["geometry"]], how="inner", predicate="within")
    add = google[~google.index.isin(hit.index)].copy()
    add["bid"] = ["g" + str(i) for i in range(len(add))]
    add["footprint_source"] = "google_v3"
    add["osmid"] = np.nan
    add["osm_type"] = ""
    for c in osm.columns:
        if c not in add.columns:
            add[c] = np.nan if osm[c].dtype != object else ""
    add["building"] = "yes"
    log(f"Footprints: added {len(add):,} Google Open Buildings footprints where OSM has none")
    return gpd.GeoDataFrame(pd.concat([osm, add[osm.columns]], ignore_index=True), geometry="geometry", crs=4326)
