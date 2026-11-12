"""Write the 3D building layer to GIS formats and Google Earth KML, and zip everything."""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import date
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import geopandas as gpd

from .config import FLOOR_COLOURS, FLOOR_NAMES, NO_HEIGHT_COLOUR, SOURCE_LABELS

COLUMNS = ["bid", "osmid", "footprint_source", "name", "building", "height_m", "height_src", "floors", "floors_src",
           "floor_band", "building_levels", "height", "sat_h_median", "sat_h_p90", "sat_presence", "floors_sat",
           "h_target_est", "est_change_m", "change", "est_h_target", "est_p_target",
           "possibly_new", "flags", "footprint_m2", "floor_area_m2", "lon", "lat", "addr_housenumber",
           "addr_street", "start_date"]

# shapefile field names are limited to 10 characters
SHP_NAMES = {"footprint_source": "fp_src", "building": "bld_type", "height_src": "h_src", "floors_src": "fl_src",
             "floor_band": "fl_band", "building_levels": "osm_levels", "height": "osm_height",
             "sat_h_median": "sat_h_med", "sat_h_p90": "sat_h_p90", "sat_presence": "sat_pres",
             "floors_sat": "floors_sat", "possibly_new": "maybe_new", "h_target_est": "h_tgt_est",
             "est_change_m": "est_chg_m", "change": "change", "est_h_target": "est_h_tgt", "est_p_target": "est_p_tgt", "footprint_m2": "fp_m2",
             "floor_area_m2": "flr_ar_m2", "addr_housenumber": "addr_no", "addr_street": "addr_st",
             "start_date": "start_dt"}

FIELD_NOTES = {
    "bid": "building ID (w/r = OSM way/relation, g = Google Open Buildings footprint)",
    "footprint_source": "osm or google_v3",
    "height_m": "best available height (m)",
    "height_src": "osm_height = OSM height tag; osm_levels = OSM floors x storey height; satellite = Open Buildings 2.5D; estimate = modelled target-year change; none",
    "floors": "best available number of floors",
    "floors_src": "osm_levels, osm_height or satellite (floor model applied to satellite height)",
    "floor_band": "1, 2-3, 4-6, 7-10, 11-15, 16+",
    "building_levels": "OSM building:levels tag",
    "height": "OSM height tag (m)",
    "sat_h_median": "satellite median height over the footprint (m)",
    "sat_h_p90": "satellite 90th percentile height (m)",
    "sat_presence": "satellite building-presence confidence (0-1) in the height year",
    "floors_sat": "floors from satellite height only",
    "possibly_new": "not seen by the satellite in the height year and no OSM tag, or modelled as new",
    "h_target_est": "modelled target-year (e.g. 2026) height: satellite height + modelled change, or modelled height for new buildings",
    "est_change_m": "modelled height change from the height year to the target year (m)",
    "change": "new, taller, lower or stable between the height year and the target year (modelled)",
    "est_h_target": "raw modelled height in the target year (Sentinel-1/2 random forest, 10 m)",
    "est_p_target": "modelled building presence in the target year (0-1)",
    "flags": "not_seen_sat; no_sat_height; tag_vs_sat (OSM floors and satellite disagree); sat_capped_100m",
    "footprint_m2": "footprint area (m2)",
    "floor_area_m2": "footprint area x floors (m2)",
}


def _cols(gdf):
    return [c for c in COLUMNS if c in gdf.columns]


def to_shapefile_zip(gdf, path_zip, layer="buildings_3d"):
    import tempfile
    g = gdf[_cols(gdf) + ["geometry"]].copy()
    g["possibly_new"] = g["possibly_new"].astype(int) if "possibly_new" in g else 0
    g = g.rename(columns=SHP_NAMES)
    with tempfile.TemporaryDirectory() as tmp:
        g.to_file(os.path.join(tmp, f"{layer}.shp"), driver="ESRI Shapefile", encoding="utf-8")
        with zipfile.ZipFile(path_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(tmp)):
                z.write(os.path.join(tmp, f), f)
            z.writestr(f"{layer}_fields.txt", "\n".join(f"{SHP_NAMES.get(k, k)}: {k} - {v}" for k, v in FIELD_NOTES.items()))
    return path_zip


def _kml_colour(hexc, alpha="dd"):
    h = hexc.lstrip("#")
    return alpha + h[4:6] + h[2:4] + h[0:2]          # KML colours are aabbggrr


def to_kml(gdf, path, title="3D buildings"):
    """Google Earth KML with each building extruded to its height."""
    styles = "".join(
        f'<Style id="b{i}"><LineStyle><color>ff555555</color><width>0.5</width></LineStyle>'
        f'<PolyStyle><color>{_kml_colour(c)}</color></PolyStyle></Style>' for i, c in enumerate(FLOOR_COLOURS))
    styles += (f'<Style id="none"><LineStyle><color>ff777777</color></LineStyle>'
               f'<PolyStyle><color>{_kml_colour(NO_HEIGHT_COLOUR, "99")}</color></PolyStyle></Style>')
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>')
    out.write(f"<name>{escape(title)}</name>{styles}")
    for _, r in gdf.iterrows():
        h = r.get("height_m")
        h = float(h) if pd.notna(h) else 3.0
        band = r.get("floor_band") or ""
        sid = f"b{FLOOR_NAMES.index(band)}" if band in FLOOR_NAMES else "none"
        geoms = list(r.geometry.geoms) if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
        polys = ""
        for g in geoms:
            ring = " ".join(f"{x:.7f},{y:.7f},{h:.1f}" for x, y in g.exterior.coords)
            polys += (f"<Polygon><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>"
                      f"<outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon>")
        geom = polys if len(geoms) == 1 else f"<MultiGeometry>{polys}</MultiGeometry>"
        name = escape(str(r.get("name") or r["bid"]))
        fl = r.get("floors")
        desc = escape(f"Height {h:.1f} m ({SOURCE_LABELS.get(r.get('height_src'), '')}); floors "
                      f"{'' if pd.isna(fl) else int(fl)} ({r.get('floors_src', '')})")
        out.write(f"<Placemark><name>{name}</name><description>{desc}</description><styleUrl>#{sid}</styleUrl>{geom}</Placemark>")
    out.write("</Document></kml>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out.getvalue())
    return path


def write_all(gdf, out_dir, name, summary, figures=None):
    """Write every format and a zip with all of them. Returns {label: path}."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    files = {}
    g = gdf[_cols(gdf) + ["geometry"]].copy()
    g.to_file(base + ".gpkg", driver="GPKG", layer="buildings_3d"); files["GeoPackage (.gpkg)"] = base + ".gpkg"
    to_shapefile_zip(gdf, base + "_shapefile.zip"); files["Shapefile (.zip)"] = base + "_shapefile.zip"
    g.to_file(base + ".geojson", driver="GeoJSON"); files["GeoJSON"] = base + ".geojson"
    g.drop(columns="geometry").to_csv(base + ".csv", index=False); files["CSV table"] = base + ".csv"
    to_kml(gdf, base + "_3d.kml", title=name); files["Google Earth 3D (.kml)"] = base + "_3d.kml"
    for k, v in (figures or {}).items():
        files[k] = v
    meta = dict(summary, generated=str(date.today()), fields=FIELD_NOTES)
    with open(base + "_summary.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)
    files["Summary (.json)"] = base + "_summary.json"
    zp = base + "_all.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files.values():
            z.write(p, os.path.basename(p))
    files["Everything (.zip)"] = zp
    return files
