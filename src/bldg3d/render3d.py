"""3D views of the buildings: a static image (PNG/SVG, oblique view, painter's
algorithm) and an interactive HTML page (deck.gl via pydeck)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import (FLOOR_COLOURS, FLOOR_NAMES, NO_HEIGHT_COLOUR, SOURCE_COLOURS, SOURCE_LABELS,
                     CHANGE_COLOURS, CHANGE_LABELS)


def _shade(hexc, f):
    c = np.array([int(hexc[i:i + 2], 16) for i in (1, 3, 5)]) / 255
    return tuple(np.clip(c * f, 0, 1))


def _colour(r, colour_by):
    if colour_by == "source":
        return SOURCE_COLOURS.get(r["height_src"], NO_HEIGHT_COLOUR)
    if colour_by == "change":
        return CHANGE_COLOURS.get(r.get("change", "") or "", CHANGE_COLOURS[""])
    b = r.get("floor_band") or ""
    return FLOOR_COLOURS[FLOOR_NAMES.index(b)] if b in FLOOR_NAMES else NO_HEIGHT_COLOUR


def static_3d(gdf, path_base, title="3D buildings", colour_by="floors", bearing=-30, elev=35, vex=1.0,
              max_buildings=40_000, floor_lines=True, dpi=300):
    """Oblique 3D image of the buildings. Writes <path_base>.png and .svg; returns the PNG path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Patch

    g = gdf[gdf.geometry.notna() & gdf.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    note = ""
    if len(g) > max_buildings:
        g = g.nlargest(max_buildings, "footprint_m2")
        note = f" · {max_buildings:,} largest of {len(gdf):,} buildings shown"
    lat0, lon0 = g["lat"].mean(), g["lon"].mean()
    kx, ky = 111320 * np.cos(np.radians(lat0)), 110540
    br = np.radians(bearing)
    fwd, rgt = np.array([np.sin(br), np.cos(br)]), np.array([np.cos(br), -np.sin(br)])
    se, ce = np.sin(np.radians(elev)), np.cos(np.radians(elev))
    proj = lambda xy, z: np.c_[xy @ rgt, (xy @ fwd) * se + z * vex * ce]  # noqa: E731
    light = np.array([-0.6, -0.8])
    items = []
    for _, r in g.iterrows():
        geom = r.geometry
        geom = max(geom.geoms, key=lambda q: q.area) if geom.geom_type == "MultiPolygon" else geom
        ring = np.array(geom.exterior.coords)[:, :2]
        xy = np.c_[(ring[:, 0] - lon0) * kx, (ring[:, 1] - lat0) * ky]
        if np.allclose(xy[0], xy[-1]):
            xy = xy[:-1]
        if 0.5 * np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]) < 0:
            xy = xy[::-1]
        h = float(r["height_m"]) if pd.notna(r["height_m"]) else 0.5
        fl = int(r["floors"]) if pd.notna(r["floors"]) and floor_lines else 1
        items.append((-(xy @ fwd).mean(), xy, h, max(1, min(fl, 60)), _colour(r, colour_by)))
    items.sort(key=lambda t: t[0])
    polys, fcs, lws = [], [], []
    many = len(items) > 3000
    for _, xy, h, fl, col in items:
        n = len(xy)
        steps = fl if (not many and fl > 1) else 1
        for k in range(steps):
            z0, z1 = h * k / steps, h * (k + 1) / steps
            for i in range(n):
                p1, p2 = xy[i], xy[(i + 1) % n]
                d = p2 - p1
                nrm = np.array([d[1], -d[0]])
                L = np.hypot(*nrm)
                if L == 0:
                    continue
                nrm /= L
                if nrm @ fwd < 0:
                    polys.append(proj(np.array([p1, p2, p2, p1]), np.array([z0, z0, z1, z1])))
                    fcs.append(_shade(col, 0.78 + 0.22 * max(0, nrm @ light))); lws.append(0.15)
        polys.append(proj(xy, np.full(n, h))); fcs.append(_shade(col, 1.0)); lws.append(0.1)
    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_axes([0, 0, 1, 0.9]); ax.set_aspect("equal"); ax.axis("off")
    ax.add_collection(PolyCollection(polys, facecolors=fcs, edgecolors="white", linewidths=lws))
    P = np.vstack(polys)
    ax.set_xlim(P[:, 0].min() - 10, P[:, 0].max() + 10); ax.set_ylim(P[:, 1].min() - 10, P[:, 1].max() + 10)
    fig.text(0.01, 0.985, title, fontsize=15, weight="bold", va="top")
    sub = {"source": "Colour = source of height", "change": "Colour = modelled change"}.get(colour_by, "Colour = floors") + note
    fig.text(0.01, 0.95, sub, fontsize=10.5, color="#222220", va="top")
    if colour_by == "source":
        hs = [Patch(color=SOURCE_COLOURS[k], label=SOURCE_LABELS[k]) for k in SOURCE_COLOURS]
    elif colour_by == "change":
        hs = [Patch(color=CHANGE_COLOURS[k], label=CHANGE_LABELS[k]) for k in CHANGE_LABELS]
    else:
        hs = [Patch(color=c, label=f"{n} floor" + ("" if n == "1" else "s")) for n, c in zip(FLOOR_NAMES, FLOOR_COLOURS)] + \
             [Patch(color=NO_HEIGHT_COLOUR, label="No height")]
    fig.legend(handles=hs, loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=7, frameon=False, fontsize=10.5)
    for ext in ("png", "svg"):
        fig.savefig(f"{path_base}.{ext}", dpi=dpi if ext == "png" else None, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"{path_base}.png"


def deck(gdf, basemap=None, colour_by="floors", max_buildings=60_000):
    """pydeck Deck object (used by the app and for the HTML export)."""
    import pydeck as pdk
    g = gdf.copy()
    if len(g) > max_buildings:
        g = g.nlargest(max_buildings, "footprint_m2")
    hexes = g.apply(lambda r: _colour(r, colour_by), axis=1)
    g["fill"] = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] + [235] for h in hexes]
    g["elev"] = g["height_m"].fillna(1.0).astype(float)
    g["label"] = g["name"].where(g["name"].astype(str).str.len() > 0, "Building " + g["bid"].astype(str))
    g["h_txt"] = g["height_m"].map(lambda v: "–" if pd.isna(v) else f"{v:.1f}")
    g["f_txt"] = g["floors"].map(lambda v: "–" if pd.isna(v) else f"{v:.0f}")
    g["src_txt"] = g["height_src"].map(SOURCE_LABELS)
    g["chg_txt"] = g["change"].map(CHANGE_LABELS).fillna("–") if "change" in g.columns else "–"
    gj = json.loads(g[["label", "h_txt", "f_txt", "src_txt", "chg_txt", "fill", "elev", "geometry"]].to_json())
    layers = []
    if basemap is not None:
        layers.append(pdk.Layer("BitmapLayer", data=None, image=pdk.types.String(basemap[0]), bounds=basemap[1], opacity=0.9))
    layers.append(pdk.Layer("GeoJsonLayer", gj, extruded=True, wireframe=True, pickable=True,
                            get_elevation="properties.elev", get_fill_color="properties.fill",
                            get_line_color=[255, 255, 255]))
    minx, miny, maxx, maxy = g.total_bounds
    lat_c, lon_c = (miny + maxy) / 2, (minx + maxx) / 2
    span_m = max((maxx - minx) * 111320 * np.cos(np.radians(lat_c)), (maxy - miny) * 110540, 150)
    zoom = float(np.clip(np.log2(800 * 156543.03 * np.cos(np.radians(lat_c)) / span_m), 12, 18.5))
    view = pdk.ViewState(latitude=lat_c, longitude=lon_c, zoom=zoom, pitch=50, bearing=-20)
    return pdk.Deck(layers=layers, initial_view_state=view, map_provider=None, map_style=None,
                    tooltip={"text": "{label}\nHeight: {h_txt} m ({src_txt})\nFloors: {f_txt}\nChange: {chg_txt}"})


def html_3d(gdf, path, basemap=None, colour_by="floors"):
    d = deck(gdf, basemap=basemap, colour_by=colour_by)
    d.to_html(path, open_browser=False, notebook_display=False)
    return path
