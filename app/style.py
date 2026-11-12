"""Look and feel of the app: one type family (Archivo), slate ink on white,
one blue accent shared with the floors ramp, and a skyline header drawn from
the buildings themselves."""
from __future__ import annotations

import numpy as np
import pandas as pd

INK, GRANITE, HAIRLINE, MIST, ACCENT = "#1D2430", "#5B6472", "#E3E6EB", "#F4F6F9", "#1A5FB4"

CSS = f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..800&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select, label {{
  font-family: 'Archivo', 'Source Sans 3', system-ui, sans-serif !important;
}}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stMain"] {{ background:#FFFFFF !important; color:{INK}; }}
[data-testid="stSidebar"] *, .stApp label, .stApp p, .stApp span, .stApp li {{ color:{INK}; }}
.stApp input, .stApp textarea, .stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"] {{ background:#FFFFFF !important; color:{INK} !important; }}
[data-testid="stFileUploaderDropzone"] {{ background:#FFFFFF !important; }}
.block-container {{ padding-top: 3.6rem; max-width: 1240px; }}
h1, h2, h3 {{ font-family: 'Archivo', sans-serif !important; color: {INK}; letter-spacing: -0.01em; }}
h1 {{ font-stretch: 112%; font-weight: 750; font-size: 2.6rem !important; line-height: 1.05; }}
h2, h3 {{ font-stretch: 106%; font-weight: 650; }}
p, li {{ color: {INK}; line-height: 1.55; }}
[data-testid="stCaptionContainer"], .stCaption {{ color: {GRANITE} !important; }}

.step {{ display:flex; align-items:center; gap:.65rem; margin: 1.6rem 0 .5rem; }}
.step b {{ display:inline-flex; width:1.85rem; height:1.85rem; border-radius:50%; align-items:center; justify-content:center;
          background:{ACCENT}; color:#fff; font-weight:700; font-size:.95rem; flex:none; }}
.step span {{ font-size:1.35rem; font-weight:650; font-stretch:106%; color:{INK}; }}
.step i {{ font-style:normal; color:{GRANITE}; font-size:.95rem; margin-left:.2rem; }}

.hero {{ border-bottom: 1px solid {HAIRLINE}; margin-bottom: .6rem; }}
.hero svg {{ display:block; width:100%; height:auto; }}
.hero .lede {{ font-size:1.12rem; color:{INK}; max-width: 46rem; margin:.35rem 0 .2rem; }}
.fresh {{ display:flex; flex-wrap:wrap; gap:.4rem 1.4rem; margin:.5rem 0 1rem; color:{GRANITE}; font-size:.92rem; }}
.fresh span b {{ color:{INK}; font-weight:600; }}

[data-testid="stMetric"] {{ border-top: 3px solid {INK}; padding-top:.45rem; }}
[data-testid="stMetricLabel"] p {{ color:{GRANITE} !important; font-size:.9rem !important; }}
[data-testid="stMetricValue"] {{ font-stretch:108%; font-weight:700; color:{INK}; }}

.stTabs [data-baseweb="tab-list"] {{ gap: 1.4rem; border-bottom:1px solid {HAIRLINE}; }}
.stTabs [data-baseweb="tab"] {{ padding: .55rem 0; font-weight:600; color:{GRANITE}; }}
.stTabs [aria-selected="true"] {{ color:{INK} !important; }}

.stButton > button[kind="primary"] {{ background:{ACCENT}; border:0; border-radius:6px; padding:.65rem 1.3rem;
                                      font-weight:650; font-size:1.02rem; }}
.stButton > button[kind="primary"], .stButton > button[kind="primary"] p {{ color:#fff !important; }}
.stButton > button[kind="primary"]:hover {{ background:#164f96; }}
.stButton > button:disabled {{ opacity:.45; cursor:not-allowed; }}
.areainfo {{ display:inline-block; background:{MIST}; border:1px solid {HAIRLINE}; border-radius:6px;
             padding:.35rem .75rem; margin:.4rem 0 .2rem; color:{INK}; }}
.areainfo b {{ font-weight:700; }}
.stButton > button, .stDownloadButton > button {{ border-radius:6px; }}
.stDownloadButton > button {{ justify-content:flex-start; border:1px solid {HAIRLINE}; background:#fff; color:{INK}; }}
.stDownloadButton > button:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
button:focus-visible {{ outline: 3px solid {ACCENT} !important; outline-offset: 2px; }}

[data-testid="stSidebar"] {{ background:{MIST}; border-right:1px solid {HAIRLINE}; }}
[data-testid="stSidebar"] h2 {{ font-size:1.1rem; margin-top:.6rem; }}
.authors {{ font-size:.85rem; color:{GRANITE}; line-height:1.45; }}
.dl-group {{ font-weight:650; color:{INK}; margin:.9rem 0 .3rem; }}
.legend span.sw {{ display:inline-block; width:12px; height:12px; border-radius:2px; margin:0 5px 0 14px; vertical-align:-1px; }}
footer, #MainMenu {{ visibility:hidden; }}
@media (max-width: 640px) {{ h1 {{ font-size: 1.9rem !important; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; animation:none !important; }} }}
</style>
"""

FLOOR_RAMP = [(1, "#c6dbef"), (3, "#9ecae1"), (6, "#6baed6"), (10, "#3182bd"), (15, "#08519c"), (10_000, "#08306b")]


def _fc(f):
    if pd.isna(f):
        return "#cfd4db"
    for t, c in FLOOR_RAMP:
        if f <= t:
            return c
    return FLOOR_RAMP[-1][1]


def skyline_svg(gdf, width=1240, height=190, bearing_deg=-20):
    """Profile of the buildings seen from the south, coloured by floors, with the title set in it."""
    g = gdf[gdf["height_m"].notna()].copy()
    if g.empty:
        return ""
    lat0 = g["lat"].mean()
    kx = 111320 * np.cos(np.radians(lat0))
    b = np.radians(bearing_deg)
    rgt = np.array([np.cos(b), -np.sin(b)])
    bounds = g.geometry.bounds
    xs0 = ((bounds["minx"] - g["lon"].mean()) * kx, (bounds["maxx"] - g["lon"].mean()) * kx)
    cx = ((g["lon"] - g["lon"].mean()) * kx) * rgt[0] + ((g["lat"] - lat0) * 110540) * rgt[1]
    half = np.minimum((xs0[1] - xs0[0]).abs() / 2, np.sqrt(g["footprint_m2"].clip(lower=1)) * 0.45)
    x0, x1 = (cx - half).min(), (cx + half).max()
    sx = width / max(x1 - x0, 1)
    hmax = max(float(g["height_m"].quantile(0.995)), 25.0)
    ground = height - 6
    sy = (height - 14) / hmax
    rows = sorted(zip(g["height_m"], cx, half, g["floors"]), key=lambda t: -t[0])   # tall at the back
    rects = []
    for h, c, hw, fl in rows:
        x = (c - hw - x0) * sx
        w = max(hw * 2 * sx, 0.8)
        hh = min(h, hmax * 1.15) * sy
        rects.append(f'<rect x="{x:.1f}" y="{ground - hh:.1f}" width="{w:.1f}" height="{hh:.1f}" fill="{_fc(fl)}" '
                     f'stroke="#fff" stroke-width="0.6"/>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Skyline of the buildings in the area" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(rects)}'
            f'<line x1="0" x2="{width}" y1="{ground}" y2="{ground}" stroke="{INK}" stroke-width="2"/></svg>')


def step(n, text, note=""):
    return f'<div class="step"><b>{n}</b><span>{text}</span>{f"<i>{note}</i>" if note else ""}</div>'


def legend(items):
    return '<div class="legend">' + "".join(
        f'<span class="sw" style="background:{c}"></span>{t}' for t, c in items) + "</div>"
