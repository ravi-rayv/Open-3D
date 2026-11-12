"""Offline tests for downloading large areas in tiles / batches (fake OSM and Earth Engine)."""
import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import box

from bldg3d.config import Settings
from bldg3d import tiling

AOI = box(77.50, 12.90, 77.62, 13.02)            # about 13 x 13 km = ~170 km²
UTM = gpd.GeoSeries([AOI], crs=4326).estimate_utm_crs()

# a fake city: 40 x 40 buildings on a regular grid, plus one long building across many tiles
xs, ys = np.meshgrid(np.linspace(77.501, 77.619, 40), np.linspace(12.901, 13.019, 40))
CITY = gpd.GeoDataFrame({"osm_type": "way", "osmid": np.arange(xs.size), "building": "yes"},
                        geometry=[box(x, y, x + 0.0002, y + 0.0002) for x, y in zip(xs.ravel(), ys.ravel())],
                        crs=4326)
LONG = gpd.GeoDataFrame({"osm_type": ["way"], "osmid": [99999], "building": ["yes"]},
                        geometry=[box(77.51, 12.95, 77.60, 12.9502)], crs=4326)
CITY = pd.concat([CITY, LONG], ignore_index=True)


def fake_overpass(max_km2, calls):
    def q(poly):
        calls.append(poly)
        if tiling.area_km2(poly, UTM) > max_km2:
            raise TimeoutError("Overpass: runtime error, query timed out")
        return CITY[CITY.intersects(poly)].copy()
    return q


def test_tiles_cover_area():
    tiles = tiling.make_tiles(AOI, UTM, 2.0)
    assert len(tiles) > 30
    total = sum(tiling.area_km2(t, UTM) for t in tiles)
    assert total == pytest.approx(tiling.area_km2(AOI, UTM), rel=1e-3)


def test_fetch_tiled_splits_dedupes_and_resumes(tmp_path):
    s = Settings(tile_km=4.0, tile_above_km2=10, osm_pause_s=0)
    calls = []
    g = tiling.fetch_tiled(AOI, UTM, fake_overpass(6.0, calls), s, str(tmp_path), "buildings", log=lambda m: None)
    assert len(g) == len(CITY)                         # every building, once (edge duplicates removed)
    assert g["osmid"].is_unique
    n_first = len(calls)
    assert n_first > len(tiling.make_tiles(AOI, UTM, 4.0))   # some 16 km² tiles were split
    calls.clear()                                      # second run: everything comes from the cache
    g2 = tiling.fetch_tiled(AOI, UTM, fake_overpass(6.0, calls), s, str(tmp_path), "buildings", log=lambda m: None)
    assert calls == [] and len(g2) == len(g)


def test_fetch_tiled_reports_missing_tiles(tmp_path):
    s = Settings(tile_km=4.0, tile_above_km2=10, osm_pause_s=0, max_split_depth=0)
    with pytest.raises(RuntimeError, match="run again"):
        tiling.fetch_tiled(AOI, UTM, fake_overpass(6.0, []), s, str(tmp_path), "b", log=lambda m: None)
    s.allow_missing_tiles = True
    g = tiling.fetch_tiled(AOI, UTM, fake_overpass(6.0, []), s, str(tmp_path), "b", log=lambda m: None)
    assert 0 < len(g) < len(CITY)


def test_small_area_single_request(tmp_path):
    small = box(77.50, 12.90, 77.52, 12.92)
    calls = []
    tiling.fetch_tiled(small, UTM, fake_overpass(100, calls), Settings(osm_pause_s=0), str(tmp_path), "b",
                       log=lambda m: None)
    assert len(calls) == 1


def test_run_batches_spatial_split_and_cache(tmp_path):
    s = Settings(chunk_size=500, workers=3, ee_retries=1)
    bld = CITY.assign(bid=CITY["osmid"].astype(str))
    sent = []

    def request(ch):                                   # fake Earth Engine: big batches run out of memory
        sent.append(len(ch))
        if len(ch) > 200:
            raise RuntimeError("User memory limit exceeded.")
        return pd.DataFrame({"bid": ch["bid"].values, "median": 10.0, "p90": 12.0})

    df = tiling.run_batches(request, bld, ["bid", "median", "p90"], s, str(tmp_path), "h", utm=UTM,
                            log=lambda m: None)
    assert len(df) == len(bld) and df["bid"].is_unique
    assert max(n for n in sent if n <= 200) <= 200
    sent.clear()
    tiling.run_batches(request, bld, ["bid", "median", "p90"], s, str(tmp_path), "h", utm=UTM, log=lambda m: None)
    assert sent == []                                  # resumed entirely from the cache


def test_spatial_order_groups_neighbours():
    order = tiling.spatial_order(CITY, UTM, cell_m=2000)
    c = CITY.geometry.to_crs(UTM).centroid.iloc[order]
    first = c.iloc[:200]
    assert (first.x.max() - first.x.min()) < 14000 and (first.y.max() - first.y.min()) < 4500


def test_parallel_tiles(tmp_path):
    s = Settings(tile_km=4.0, tile_above_km2=10, osm_pause_s=0)
    g = tiling.fetch_tiled(AOI, UTM, fake_overpass(6.0, []), s, str(tmp_path), "b", log=lambda m: None,
                           workers=4, source="Earth Engine")
    assert len(g) == len(CITY) and g["osmid"].is_unique


def test_osm_fetch_buildings_tiled(tmp_path, monkeypatch):
    from bldg3d import osm
    city = CITY.assign(**{"building:levels": "3", "height": "10 m"})
    monkeypatch.setattr(osm, "_overpass", lambda poly, log, timeout: city[city.intersects(poly)].copy())
    s = Settings(tile_km=3.0, tile_above_km2=10, osm_pause_s=0, cache_dir=str(tmp_path))
    out = osm.fetch_buildings(AOI, UTM, log=lambda m: None, s=s)
    assert len(out) == len(CITY) and out["bid"].is_unique
    assert (out["building_levels"] == 3).all() and (out["height"] == 10).all()   # tags survive the tiling
