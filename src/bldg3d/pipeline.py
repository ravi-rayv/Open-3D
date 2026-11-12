"""Run the whole extraction for one study area."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import geopandas as gpd

from .aoi import aoi_geometry, check_aoi
from .config import Settings, SOURCE_LABELS
from . import heights3d, export, render3d, tiling


@dataclass
class Result:
    buildings: gpd.GeoDataFrame
    summary: dict
    files: dict = field(default_factory=dict)
    out_dir: str = ""


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "area"


def run(aoi: gpd.GeoDataFrame, s: Settings | None = None, out_dir: str = "output", name: str = "area",
        ee_project: str | None = None, service_account_json=None, buildings=None, sat=None, est=None,
        log=print, progress=None, basemap=None, make_images=True) -> Result:
    """Extract 3D buildings for ``aoi``.

    ``buildings`` / ``sat`` can be supplied (demo, tests, offline); otherwise OSM is
    downloaded and satellite values are read from Earth Engine with the user's project.
    """
    s = s or Settings()
    p = (lambda frac, step: progress(frac, step)) if progress else (lambda *a: None)
    info = check_aoi(aoi, s.max_area_km2)
    geom = aoi_geometry(aoi)
    utm = aoi.estimate_utm_crs()
    name = _slug(name)
    out_dir = os.path.join(out_dir, name)

    cache = tiling.cache_dir(s, "bldg3d")
    download = {"tiled": info["area_km2"] > s.tile_above_km2, "cache_dir": cache}
    if download["tiled"]:
        download.update(tiles=len(tiling.make_tiles(geom, utm, s.tile_km)), tile_km=s.tile_km)
        log(f"Large area ({info['area_km2']:,.0f} km²): OSM is downloaded in {download['tiles']} tiles of "
            f"{s.tile_km:g} km and Earth Engine in spatial batches; finished pieces are saved in {cache}, "
            "so a stopped run resumes when started again.")

    p(0.02, "OpenStreetMap buildings")
    if buildings is None:
        from .osm import fetch_buildings
        buildings = fetch_buildings(geom, utm, log=log, s=s, cache=cache,
                                    progress=lambda f: p(0.02 + 0.18 * f, "OpenStreetMap buildings"))
    bld = buildings.copy()
    p(0.20, "Earth Engine")
    used_ee = sat is None
    if sat is None:
        from . import earthengine as eem
        eem.init_earth_engine(ee_project, service_account_json)
        if s.fill_google_footprints:
            p(0.22, "Google footprints")
            gfp = eem.google_footprints(geom, utm, s.google_min_confidence, log=log, s=s, cache=cache,
                                        progress=lambda f: p(0.22 + 0.18 * f, "Google footprints"))
            bld = eem.add_google_gaps(bld, gfp, utm, log=log)
        sat = eem.building_heights(bld, geom, s.height_year, s, log=log, cache=cache, utm=utm,
                                   progress=lambda f: p(0.40 + 0.40 * f, "Satellite heights"))
    if len(bld) == 0:
        raise RuntimeError("No buildings found in the study area")
    bld = bld.merge(sat, on="bid", how="left") if sat is not None and len(sat) else bld
    validation = None
    if est is not None:
        bld = bld.merge(est, on="bid", how="left")
    elif s.estimate_target and used_ee:
        from . import estimate as est_mod
        p(0.80, f"Modelling {s.target_year} heights")
        log(f"Estimating {s.target_year} heights from Sentinel-1/2 (trained on {s.height_year} Google heights)…")
        stack, validation = est_mod.train_and_predict(geom, utm.to_epsg(), train_year=s.height_year,
                                                      target_year=s.target_year, log=log)
        est = est_mod.per_building(stack, bld, s, log=log, cache=cache, utm=utm,
                                   progress=lambda f: p(0.82 + 0.06 * f, f"{s.target_year} estimate"))
        bld = bld.merge(est, on="bid", how="left")
    p(0.82, "Heights and floors")
    for c in ["sat_h_median", "sat_h_p90", "sat_presence"]:
        bld[c] = pd.to_numeric(bld[c], errors="coerce") if c in bld.columns else float("nan")
    model = heights3d.fit_floor_model(bld, s, log=log)
    if "est_h_target" in bld.columns:
        bld = heights3d.apply_estimate(bld, s)
    res = heights3d.combine(bld, model, s)
    res = heights3d.add_geometry_stats(gpd.GeoDataFrame(res, geometry="geometry", crs=4326), utm)

    src = res["height_src"].value_counts().to_dict()
    summary = {
        "area_name": name, "area_km2": info["area_km2"], "bounds": info["bounds"],
        "buildings": int(len(res)),
        "footprints": res["footprint_source"].value_counts().to_dict(),
        "osm_date": str(date.today()), "satellite_height_year": s.height_year,
        "height_source_counts": {SOURCE_LABELS[k]: int(v) for k, v in src.items()},
        "with_height": int(res["height_m"].notna().sum()),
        "median_height_m": float(res["height_m"].median()) if res["height_m"].notna().any() else None,
        "max_height_m": float(res["height_m"].max()) if res["height_m"].notna().any() else None,
        "share_4plus_floors": float((res["floors"] >= 4).mean()),
        "possibly_new_after_height_year": int(res["possibly_new"].sum()),
        "floor_model": {k: v for k, v in model.items()},
        "total_floor_area_m2": float(res["floor_area_m2"].sum()),
        "settings": s.as_dict(),
        "target_year": s.target_year if "change" in res.columns else None,
        "change_counts": res["change"].value_counts().to_dict() if "change" in res.columns else {},
        "estimate_validation": validation,
        "download": download,
        "data": {"footprints": "© OpenStreetMap contributors (ODbL)" + ("; Google Open Buildings v3 (CC BY 4.0)"
                                                                          if s.fill_google_footprints else ""),
                 "heights": "Google Open Buildings 2.5D Temporal (CC BY 4.0)"},
    }
    log(f"Done: {len(res):,} buildings · {summary['with_height']:,} with a height · "
        f"{summary['possibly_new_after_height_year']:,} possibly built after {s.height_year}")

    figs = {}
    if make_images:
        p(0.88, "3D images")
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, name)
        yr = f"{s.target_year} estimate" if "change" in res.columns else str(s.height_year)
        t = f"{name.replace('_', ' ')}: buildings in 3D (OSM {date.today():%b %Y}, heights {yr})"
        figs["3D image (.png)"] = render3d.static_3d(res, base + "_3d", title=t)
        figs["3D image (.svg)"] = base + "_3d.svg"
        render3d.static_3d(res, base + "_3d_by_source", title=t, colour_by="source")
        figs["3D image by height source (.png)"] = base + "_3d_by_source.png"
        if "change" in res.columns:
            render3d.static_3d(res, base + "_3d_change", title=t, colour_by="change")
            figs[f"3D image: change {s.height_year}–{s.target_year} (.png)"] = base + "_3d_change.png"
        try:
            figs["Interactive 3D (.html)"] = render3d.html_3d(res, base + "_3d.html", basemap=basemap)
        except Exception as e:  # noqa: BLE001
            log(f"HTML export skipped: {e}")
    p(0.95, "Writing files")
    files = export.write_all(res, out_dir, name, summary, figs)
    p(1.0, "Done")
    return Result(res, summary, files, out_dir)
