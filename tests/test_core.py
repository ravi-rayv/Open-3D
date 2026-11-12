import json
import os
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box

from bldg3d import Settings
from bldg3d.osm import _num, clean
from bldg3d import heights3d, export
from bldg3d.earthengine import add_google_gaps


def _bld(rows):
    g = gpd.GeoDataFrame(rows, geometry=[box(77.6 + i * 0.0003, 12.97, 77.6002 + i * 0.0003, 12.9702) for i in range(len(rows))], crs=4326)
    for c in ["building_levels", "height", "name"]:
        if c not in g:
            g[c] = np.nan if c != "name" else ""
    g["bid"] = [f"w{i}" for i in range(len(g))]
    g["footprint_source"] = "osm"
    return g


def test_parse_osm_numbers():
    assert _num("12") == 12 and _num("12.5 m") == 12.5 and abs(_num("40 ft") - 12.192) < 1e-6
    assert _num("3;4") == 3 and np.isnan(_num("tall")) and np.isnan(_num(None))


def test_height_priority_and_flags():
    s = Settings()
    df = _bld([{"height": 45.0, "building_levels": 12, "sat_h_median": 30, "sat_h_p90": 33, "sat_presence": 0.9},
               {"height": np.nan, "building_levels": 5, "sat_h_median": 9, "sat_h_p90": 10, "sat_presence": 0.9},
               {"height": np.nan, "building_levels": np.nan, "sat_h_median": 10, "sat_h_p90": 12, "sat_presence": 0.9},
               {"height": np.nan, "building_levels": np.nan, "sat_h_median": 1.0, "sat_h_p90": 1.0, "sat_presence": 0.1}])
    m = {"intercept": 0.67, "slope": 0.301}
    r = heights3d.combine(df, m, s)
    assert list(r["height_src"]) == ["osm_height", "osm_levels", "satellite", "none"]
    assert r.loc[0, "height_m"] == 45 and r.loc[0, "floors"] == 12            # levels win for floors
    assert abs(r.loc[1, "height_m"] - (5 * 3.3 + 0.7)) < 1e-9
    assert r.loc[2, "floors"] == np.round(0.67 + 0.301 * 10)
    assert bool(r.loc[3, "possibly_new"]) and "not_seen_sat" in r.loc[3, "flags"]
    assert r.loc[2, "floor_band"] == "4–6"


def test_local_floor_model_fitted_when_enough_tags():
    rng = np.random.default_rng(0)
    lv = rng.integers(1, 15, 300).astype(float)
    df = pd.DataFrame({"building_levels": lv, "sat_h_median": 3.0 * lv + rng.normal(0, 1, 300)})
    m = heights3d.fit_floor_model(df, Settings(), log=lambda *a: None)
    assert "local" in m["source"] and abs(1 / m["slope"] - 3.0) < 0.3
    m2 = heights3d.fit_floor_model(df.head(20), Settings(), log=lambda *a: None)
    assert m2["intercept"] == 0.67


def test_google_gap_filling():
    osm = _bld([{}, {}])
    google = gpd.GeoDataFrame({"confidence": [0.9, 0.9]},
                              geometry=[osm.geometry.iloc[0].buffer(-0.00005), box(77.61, 12.97, 77.6102, 12.9702)], crs=4326)
    out = add_google_gaps(osm, google, 32643, log=lambda *a: None)
    assert len(out) == 3 and (out["footprint_source"] == "google_v3").sum() == 1


def test_exports(tmp_path):
    s = Settings()
    df = _bld([{"building_levels": 4, "sat_h_median": 12, "sat_h_p90": 14, "sat_presence": 0.9},
               {"sat_h_median": 7, "sat_h_p90": 8, "sat_presence": 0.8}])
    r = heights3d.combine(df, {"intercept": 0.67, "slope": 0.301}, s)
    r = heights3d.add_geometry_stats(gpd.GeoDataFrame(r, geometry="geometry", crs=4326), 32643)
    files = export.write_all(r, str(tmp_path), "t", {"x": 1})
    shp = gpd.read_file("zip://" + files["Shapefile (.zip)"])
    assert len(shp) == 2 and all(len(c) <= 10 for c in shp.columns)
    root = ET.parse(files["Google Earth 3D (.kml)"]).getroot()
    assert len(root.findall(".//{http://www.opengis.net/kml/2.2}Placemark")) == 2
    assert "extrude" in open(files["Google Earth 3D (.kml)"]).read()
    assert len(gpd.read_file(files["GeoPackage (.gpkg)"])) == 2
    assert zipfile.ZipFile(files["Everything (.zip)"]).testzip() is None


def test_demo_pipeline(tmp_path):
    from bldg3d import run
    from bldg3d.demo import demo_data
    aoi, b, sat = demo_data()
    res = run(aoi, Settings(), out_dir=str(tmp_path), name="demo", buildings=b, sat=sat, log=lambda *a: None,
              make_images=False)
    assert res.summary["buildings"] == len(b) > 1000
    assert res.summary["with_height"] / res.summary["buildings"] > 0.9
    assert os.path.exists(res.files["Shapefile (.zip)"])
    assert json.load(open(res.files["Summary (.json)"]))["satellite_height_year"] == 2023


def test_target_year_estimate_rules():
    s = Settings()
    df = _bld([
        {"sat_h_median": 10, "sat_presence": 0.9, "est_h_target": 16, "est_h_train": 10, "est_p_target": 0.9, "est_p_train": 0.9},
        {"sat_h_median": 10, "sat_presence": 0.9, "est_h_target": 11, "est_h_train": 10, "est_p_target": 0.9, "est_p_train": 0.9},
        {"sat_h_median": np.nan, "sat_presence": 0.05, "est_h_target": 9, "est_h_train": 1, "est_p_target": 0.8, "est_p_train": 0.1},
        {"sat_h_median": 12, "sat_presence": 0.9, "est_h_target": 3, "est_h_train": 12, "est_p_target": 0.2, "est_p_train": 0.9},
        {"building_levels": 8, "sat_h_median": 20, "sat_presence": 0.9, "est_h_target": 30, "est_h_train": 20, "est_p_target": 0.9, "est_p_train": 0.9},
    ])
    e = heights3d.apply_estimate(df, s)
    assert list(e["change"]) == ["taller", "stable", "new", "lower", "taller"]
    assert e.loc[0, "h_target_est"] == 16 and e.loc[1, "h_target_est"] == 10 and e.loc[2, "h_target_est"] == 9
    r = heights3d.combine(e, {"intercept": 0.67, "slope": 0.301}, s)
    assert list(r["height_src"]) == ["estimate", "satellite", "estimate", "estimate", "osm_levels"]
    assert r.loc[4, "floors"] == 8                                   # OSM tag still wins
    assert bool(r.loc[2, "possibly_new"])


def test_pipeline_with_estimate(tmp_path):
    from bldg3d import run
    from bldg3d.demo import demo_data
    aoi, b, sat = demo_data()
    est = sat[["bid"]].copy()
    est["est_h_train"] = sat["sat_h_median"].fillna(0)
    est["est_h_target"] = est["est_h_train"] + np.where(np.arange(len(est)) % 10 == 0, 6.0, 0.5)
    est["est_p_target"] = 0.9; est["est_p_train"] = 0.9
    res = run(aoi, Settings(), out_dir=str(tmp_path), name="d", buildings=b, sat=sat, est=est,
              log=lambda *a: None, make_images=False)
    assert res.summary["change_counts"].get("taller", 0) > 100
    shp = gpd.read_file("zip://" + res.files["Shapefile (.zip)"])
    assert {"h_tgt_est", "change"} <= set(shp.columns)
