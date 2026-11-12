"""Download big study areas in pieces, save every piece to disk, then compile.

Large areas fail when they are sent as one request: Overpass (OpenStreetMap)
runs out of time or memory, and Earth Engine times out when a batch of
buildings is spread over the whole city. This module

* cuts the study area into square tiles (``tile_km``) and downloads OSM tile
  by tile; a tile that still fails is split into four smaller tiles;
* sorts buildings spatially so that each Earth Engine batch covers a small,
  compact part of the area; a batch that fails is split in two and retried;
* saves every finished tile / batch in a cache folder, so a run that stops
  (network error, closed browser, quota) resumes where it left off;
* merges the pieces and removes duplicates (buildings on tile edges are
  returned by both neighbours).

The cache is content-addressed (file names are hashes of what was asked), so
one cache folder can be shared by any number of study areas and runs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box
from shapely.ops import unary_union


# ----------------------------------------------------------------------------- cache
def cache_dir(s, package: str) -> str:
    """Folder for downloaded pieces: ``s.cache_dir`` or a folder in the system temp dir."""
    root = getattr(s, "cache_dir", "") or os.path.join(tempfile.gettempdir(), f"{package}_download_cache")
    os.makedirs(root, exist_ok=True)
    return root


def _key(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:20]


def _fresh(path: str, max_age_days: float) -> bool:
    if not os.path.exists(path):
        return False
    if not max_age_days or max_age_days <= 0:
        return True
    return (time.time() - os.path.getmtime(path)) < max_age_days * 86400


def _save(obj, path):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)          # atomic: a half-written file is never read back


def _load(path):
    with open(path, "rb") as f:
        return pickle.load(f)


# ----------------------------------------------------------------------------- tiles
def _polygonal(g):
    """Keep only the (multi)polygon part of a geometry."""
    if g is None or g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    if hasattr(g, "geoms"):
        parts = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
        return unary_union(parts) if parts else None
    return None


def area_km2(geom, utm) -> float:
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(utm).area.iloc[0] / 1e6)


def make_tiles(aoi_geom, utm, tile_km: float) -> list:
    """Square tiles of ``tile_km`` (in the UTM zone) clipped to the study area, in EPSG:4326."""
    a = gpd.GeoSeries([aoi_geom], crs=4326).to_crs(utm).iloc[0]
    step = tile_km * 1000.0
    minx, miny, maxx, maxy = a.bounds
    boxes = [box(x, y, x + step, y + step)
             for y in np.arange(miny, maxy, step) for x in np.arange(minx, maxx, step)]
    sq = gpd.GeoSeries(boxes, crs=utm)
    sq = sq[sq.intersects(a)]
    clipped = sq.intersection(a).to_crs(4326)
    tiles = [_polygonal(g.buffer(0)) for g in clipped]
    return [t for t in tiles if t is not None and t.area > 0]


def split4(poly):
    """Split a polygon into four quarters (of its bounding box)."""
    minx, miny, maxx, maxy = poly.bounds
    mx, my = (minx + maxx) / 2, (miny + maxy) / 2
    quads = [box(minx, miny, mx, my), box(mx, miny, maxx, my), box(minx, my, mx, maxy), box(mx, my, maxx, maxy)]
    out = [_polygonal(poly.intersection(q).buffer(0)) for q in quads]
    return [p for p in out if p is not None and p.area > 0]


# ----------------------------------------------------------------------------- OSM in tiles
def fetch_tiled(aoi_geom, utm, query, s, cache, label, keep_cols=None, log=print, progress=None,
                id_cols=("osm_type", "osmid"), tile_km=None, always_tile=False, workers=1, pause=None,
                source="OSM") -> gpd.GeoDataFrame:
    """Run ``query(polygon) -> GeoDataFrame`` tile by tile and merge the results.

    ``query`` must return a GeoDataFrame in EPSG:4326 with a flat index (``id_cols`` as
    columns) and an empty GeoDataFrame when a tile has no features. Small areas
    (<= ``s.tile_above_km2``) are fetched as one tile, but still cached.
    ``workers`` > 1 downloads tiles in parallel (keep 1 for the public Overpass servers).
    """
    total = area_km2(aoi_geom, utm)
    tile_km = float(tile_km or getattr(s, "tile_km", 2.0))
    if total <= float(getattr(s, "tile_above_km2", 10.0)) and not always_tile:
        tiles = [aoi_geom]
    else:
        tiles = make_tiles(aoi_geom, utm, tile_km)
    max_depth = int(getattr(s, "max_split_depth", 2))
    max_age = float(getattr(s, "cache_days", 7))
    pause = float(getattr(s, "osm_pause_s", 1.0) if pause is None else pause)
    keep = None
    if keep_cols is not None:
        keep = set(keep_cols) | set(id_cols) | {"geometry"}
        keep |= {c.replace("_", ":") for c in keep_cols} | {c.replace(":", "_") for c in keep_cols}

    if len(tiles) > 1:
        log(f"{source}: area is {total:,.0f} km², downloading {label} in {len(tiles)} tiles of "
            f"{tile_km:g} × {tile_km:g} km (saved to {cache})")

    def trim(g):
        if g is None or not len(g):
            return gpd.GeoDataFrame(geometry=[], crs=4326)
        if keep is not None:
            g = g[[c for c in g.columns if c in keep]]
        return g

    def get(poly, depth, tag):
        path = os.path.join(cache, f"{source.lower().replace(' ', '')}_{_key(source, label, poly.wkb)}.pkl")
        if _fresh(path, max_age):
            return _load(path), 0, True
        try:
            g = trim(query(poly))
            _save(g, path)
            time.sleep(pause)            # be polite to the public Overpass servers
            return g, 0, False
        except Exception as e:  # noqa: BLE001
            if depth >= max_depth:
                log(f"{source}: {tag} failed after splitting ({type(e).__name__}: {str(e)[:120]})")
                return None, 1, False
            parts = split4(poly)
            log(f"{source}: {tag} failed ({type(e).__name__}); splitting it into {len(parts)} smaller tiles")
            got, bad = [], 0
            for j, q in enumerate(parts):
                g, b, _ = get(q, depth + 1, f"{tag}.{j + 1}")
                bad += b
                if g is not None:
                    got.append(g)
            if bad:
                return (pd.concat(got, ignore_index=True) if got else None), bad, False
            g = pd.concat(got, ignore_index=True) if got else gpd.GeoDataFrame(geometry=[], crs=4326)
            _save(g, path)               # the whole tile is complete: cache it as one piece
            return g, 0, False

    parts, failed, n_cached = [], 0, 0
    every = max(10, len(tiles) // 20)

    def done(i, g, bad, was_cached):
        nonlocal failed, n_cached
        failed += bad
        n_cached += int(was_cached)
        if g is not None and len(g):
            parts.append(g)
        if len(tiles) > 1 and (i % every == 0 or i == len(tiles)):
            n = sum(len(p) for p in parts)
            log(f"{source}: {i}/{len(tiles)} tiles done ({n_cached} from cache), {n:,} {label} so far")
        if progress:
            progress(i / len(tiles))

    if workers <= 1 or len(tiles) == 1:
        for i, t in enumerate(tiles, 1):
            done(i, *get(t, 0, f"tile {i}/{len(tiles)}"))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(get, t, 0, f"tile {i}/{len(tiles)}") for i, t in enumerate(tiles, 1)]
            for i, f in enumerate(as_completed(futs), 1):
                done(i, *f.result())

    if failed:
        msg = (f"{source}: {failed} tile(s) could not be downloaded. The {len(tiles) - failed} finished tiles are saved; "
               f"run again to download only the missing ones.")
        if not getattr(s, "allow_missing_tiles", False):
            raise RuntimeError(msg)
        log(msg + " Continuing without them (allow_missing_tiles=True).")

    if not parts:
        return gpd.GeoDataFrame(geometry=[], crs=4326)
    g = pd.concat(parts, ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
    ids = [c for c in id_cols if c in g.columns]
    if ids:
        g = g.drop_duplicates(ids)
    if len(tiles) > 1:
        log(f"{source}: compiled {len(g):,} {label} from {len(tiles)} tiles")
    return g.reset_index(drop=True)


# ----------------------------------------------------------------------------- Earth Engine in batches
def spatial_order(gdf, utm, cell_m: float = 1000.0) -> np.ndarray:
    """Row order that keeps nearby buildings together (cells in a snake pattern)."""
    if not len(gdf):
        return np.arange(0)
    c = gdf.geometry.to_crs(utm).centroid
    x, y = c.x.values, c.y.values
    gx = np.floor((x - np.nanmin(x)) / cell_m).astype(int)
    gy = np.floor((y - np.nanmin(y)) / cell_m).astype(int)
    gx_snake = np.where(gy % 2 == 0, gx, gx.max() - gx)
    return np.lexsort((y, x, gx_snake, gy))


def run_batches(request, gdf, cols, s, cache, label, utm=None, log=print, progress=None) -> pd.DataFrame:
    """Send buildings to Earth Engine in compact batches and compile the answers.

    ``request(chunk_gdf) -> DataFrame`` performs one Earth Engine call for a chunk of
    buildings (columns ``bid`` + ``geometry``). Each finished batch is cached. A batch
    that fails is split in half and retried (``max_split_depth`` times).
    """
    send = gdf[["bid", "geometry"]].copy()
    send["geometry"] = send.geometry.simplify(0.000005)       # ~0.5 m, shrinks request size
    if utm is None:
        utm = send.estimate_utm_crs() if len(send) else 4326
    send = send.iloc[spatial_order(send, utm, float(getattr(s, "batch_cell_m", 1000.0)))]
    size = max(int(s.chunk_size), 1)
    chunks = [send.iloc[i:i + size] for i in range(0, len(send), size)]
    max_depth = int(getattr(s, "max_split_depth", 2)) + 1
    max_age = float(getattr(s, "cache_days", 7))
    tries = int(getattr(s, "ee_retries", 4))

    def attempt(ch):
        err = None
        for k in range(tries):
            try:
                return request(ch)
            except Exception as e:  # noqa: BLE001 - retry with back-off
                err = e
                txt = str(e).lower()
                if "memory" in txt or "timed out" in txt or "too large" in txt or "5000" in txt:
                    break                 # retrying the same batch will not help: split it
                time.sleep(5 * (k + 1))
        raise RuntimeError(str(err)[:200])

    def solve(ch, depth):
        path = os.path.join(cache, f"ee_{_key(label, ch.to_json())}.pkl")
        if _fresh(path, max_age):
            return _load(path), 0
        try:
            df = attempt(ch)
            _save(df, path)
            return df, 0
        except Exception as e:  # noqa: BLE001
            if depth >= max_depth or len(ch) < 50:
                log(f"Earth Engine: {len(ch):,} buildings failed ({str(e)[:120]})")
                return None, len(ch)
            half = len(ch) // 2
            a, fa = solve(ch.iloc[:half], depth + 1)
            b, fb = solve(ch.iloc[half:], depth + 1)
            got = [d for d in (a, b) if d is not None]
            df = pd.concat(got, ignore_index=True) if got else None
            if df is not None and fa + fb == 0:
                _save(df, path)          # whole batch done: next run skips it without re-sending
            return df, fa + fb

    log(f"Earth Engine: {label} for {len(send):,} buildings in {len(chunks)} batches "
        f"(spatially grouped, saved to {cache})")
    out, missing = [], 0
    with ThreadPoolExecutor(max_workers=max(int(s.workers), 1)) as ex:
        futs = {ex.submit(solve, ch, 0): i for i, ch in enumerate(chunks)}
        for n, fut in enumerate(as_completed(futs), 1):
            df, bad = fut.result()
            missing += bad
            if df is not None and len(df):
                out.append(df)
            if progress:
                progress(n / len(chunks))
    if missing:
        log(f"Earth Engine: {missing:,} of {len(send):,} buildings got no value. Finished batches are saved; "
            f"run again to retry only the missing ones.")
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)[cols].drop_duplicates("bid")


def ee_region(aoi_geom, max_vertices: int = 4000):
    """A light Earth Engine geometry for ``filterBounds``: simplified, or the envelope if still complex."""
    import ee
    from shapely.geometry import mapping
    g = aoi_geom.simplify(0.0005)
    n = len(g.exterior.coords) if g.geom_type == "Polygon" else sum(len(p.exterior.coords) for p in getattr(g, "geoms", []))
    if n > max_vertices:
        g = aoi_geom.envelope
    return ee.Geometry(mapping(g))
