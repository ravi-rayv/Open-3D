"""Open 3D Buildings: draw or upload an area, get every building with its best
available height and floors, view it in 3D and download GIS files.

Run locally:   streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date

import streamlit as st

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))  # run from a clone without installing
sys.path.insert(0, HERE)

from bldg3d import Settings, run, read_aoi, AOIError, LATEST_HEIGHT_YEAR  # noqa: E402
from bldg3d.aoi import check_aoi  # noqa: E402
from bldg3d.basemap import SOURCES, add_folium_layers, stitched_basemap  # noqa: E402
from bldg3d.config import (FLOOR_COLOURS, FLOOR_NAMES, NO_HEIGHT_COLOUR, SOURCE_COLOURS, SOURCE_LABELS,  # noqa: E402
                           TARGET_YEAR, CHANGE_COLOURS, CHANGE_LABELS)
from bldg3d import render3d  # noqa: E402
import style  # noqa: E402

st.set_page_config(page_title="Open 3D Buildings", page_icon="🏙️", layout="wide")


def html(fragment, where=None):
    """Render a small piece of our own HTML (works on old and new Streamlit)."""
    w = where or st
    if hasattr(w, "html"):
        w.html(fragment)
    else:
        w.markdown(fragment, unsafe_allow_html=True)
html(style.CSS)
DEFAULT_CENTER = [12.9716, 77.5946]  # Bengaluru


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def cached_basemap(bounds, source):
    return stitched_basemap(bounds, source)


@st.cache_data(show_spinner=False)
def demo_skyline():
    from bldg3d.demo import demo_data
    aoi, b, sat = demo_data()
    res = run(aoi, Settings(), out_dir=tempfile.mkdtemp(), name="skyline", buildings=b, sat=sat,
              log=lambda *a: None, make_images=False)
    return style.skyline_svg(res.buildings)


# ============================================================================ sidebar
with st.sidebar:
    st.markdown("## Data")
    mode = st.radio("Area", ["My own area", "Demo: World Trade Center, Bengaluru"], label_visibility="collapsed")
    demo_mode = mode.startswith("Demo")
    ee_project, sa_json = None, None
    if not demo_mode:
        st.markdown("## Earth Engine sign-in")
        auth = st.radio("Sign-in method", ["Service-account key (website)", "This computer (local install)"],
                        help="On the website, use a key. 'This computer' works only when you run the app yourself.")
        if auth.startswith("Service"):
            key = st.file_uploader("Your key file (.json)", type=["json"], help="Used only in memory; never stored.")
            if key:
                sa_json = key.getvalue().decode("utf-8")
        ee_project = st.text_input("Project ID", placeholder="read from the key if left empty")
        if sa_json:
            try:
                kp = json.loads(sa_json).get("project_id")
            except ValueError:
                kp = None
                st.error("This file is not a valid JSON key.")
            if kp and not ee_project:
                ee_project = kp
                st.caption(f"Project: {kp}")
            elif kp and ee_project != kp:
                st.warning(f"This key belongs to '{kp}', not '{ee_project}'.")
        if st.button("Test connection", disabled=not ee_project, width="stretch"):
            try:
                from bldg3d.earthengine import init_earth_engine
                init_earth_engine(ee_project, sa_json)
                st.success("Connected")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not connect: {e}")
        st.caption("[How to get a key](https://github.com/ravi-rayv/open-3d-buildings/blob/main/docs/earth_engine.md)")

    st.markdown("## Heights")
    s = Settings()
    target = st.radio("Heights for", [f"{TARGET_YEAR}, estimated", f"{LATEST_HEIGHT_YEAR} or earlier, satellite"],
                      label_visibility="collapsed",
                      help=f"{TARGET_YEAR}: Google's {LATEST_HEIGHT_YEAR} heights plus the change modelled from "
                           f"{TARGET_YEAR} Sentinel-1/2 images.")
    s.estimate_target = target.startswith(str(TARGET_YEAR))
    s.height_year = st.select_slider("Satellite year" + (" used for training" if s.estimate_target else ""),
                                     options=list(range(2016, LATEST_HEIGHT_YEAR + 1)), value=LATEST_HEIGHT_YEAR)
    s.fill_google_footprints = st.checkbox("Add Google footprints where OpenStreetMap has none", value=False,
                                           help="Google Open Buildings v3 (imagery up to 2023). Slower.")
    with st.expander("More settings"):
        s.storey_m = st.number_input("Storey height for OSM floor tags (m)", 2.5, 5.0, s.storey_m, 0.1)
        s.min_change_m = st.number_input("Smallest change counted (m)", 1.0, 10.0, s.min_change_m, 0.5)
        s.max_area_km2 = st.number_input("Largest area allowed (km²)", 1.0, 20000.0, s.max_area_km2, 100.0)
    with st.expander("Large areas (download in tiles)"):
        st.caption("Areas bigger than the threshold are downloaded tile by tile and sent to Earth Engine in small "
                   "spatial batches, then compiled. Finished pieces are saved, so if a run stops, press "
                   "Extract again with the same area to continue where it stopped.")
        s.tile_above_km2 = st.number_input("Use tiles for areas larger than (km²)", 1.0, 500.0, s.tile_above_km2, 5.0)
        s.tile_km = st.select_slider("OSM tile size (km)", [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0], value=s.tile_km,
                                     help="Use smaller tiles for very dense city centres if tiles keep failing.")
        s.chunk_size = st.select_slider("Buildings per Earth Engine batch", [250, 500, 1000, 2000, 3000],
                                        value=s.chunk_size)
        s.workers = st.slider("Parallel Earth Engine requests", 1, 12, s.workers)
        s.allow_missing_tiles = st.checkbox("Continue if some tiles still fail (results incomplete)", value=False)
    html('<div class="authors">Ravi Satyappa Dabbanavar and Rohini N<br>Nitte School of Architecture, '
                'Planning and Design, Nitte University, Bengaluru<br>ravisd@nittesoa.ac.in</div>')

# ============================================================================ hero
res = st.session_state.get("res")
st.markdown("# Open 3D Buildings")
html('<p class="lede">Every building in your area with its height and number of floors, in 3D, '
            'ready to download for GIS and Google Earth.</p>')
sky = style.skyline_svg(res.buildings) if res is not None else demo_skyline()
import base64 as _b64
_sky64 = _b64.b64encode(sky.encode("utf-8")).decode() if sky else ""
html(f'<div class="hero"><img alt="Skyline of the buildings in the area" style="width:100%;display:block" '
     f'src="data:image/svg+xml;base64,{_sky64}"/></div>' if sky else '<div class="hero"></div>')
html(
    f'<div class="fresh"><span><b>Footprints and floor tags</b> OpenStreetMap, {date.today():%B %Y}</span>'
    f'<span><b>Satellite heights</b> Google Open Buildings, 2016–{LATEST_HEIGHT_YEAR}</span>'
    f'<span><b>{TARGET_YEAR} heights</b> estimated from Sentinel-1/2</span></div>')
if res is not None:
    st.caption(f"Skyline of {res.summary['area_name'].replace('_', ' ')}, seen from the south.")

# ============================================================================ area
aoi, demo = None, None
html(style.step(1, "Choose an area"))
if demo_mode:
    from bldg3d.demo import demo_data
    demo = demo_data()
    aoi = demo[0]
    st.markdown("The demo covers 680 m around World Trade Center, Bengaluru, with stored 2023 heights. "
                "It needs no sign-in.")
    if s.estimate_target:
        st.caption(f"{TARGET_YEAR} estimates need Earth Engine, so the demo uses {LATEST_HEIGHT_YEAR} heights.")
else:
    t_draw, t_up = st.tabs(["Draw on the map", "Upload a file"])
    with t_draw:
        import folium
        from folium.plugins import Draw
        from streamlit_folium import st_folium
        c1, c2 = st.columns([4, 1])
        place = c1.text_input("Find a place", placeholder="e.g. Indiranagar, Bengaluru", label_visibility="collapsed")
        if c2.button("Find", width="stretch") and place:
            try:
                import osmnx as ox
                st.session_state["center"] = list(ox.geocode(place))
            except Exception:  # noqa: BLE001
                st.warning(f"No place called '{place}' was found. Try adding the city name.")
        center = st.session_state.get("center", DEFAULT_CENTER)
        m = folium.Map(location=center, zoom_start=15, tiles=None)
        add_folium_layers(m)
        Draw(export=False, draw_options={"polyline": False, "circle": False, "marker": False,
                                         "circlemarker": False, "polygon": True, "rectangle": True},
             edit_options={"edit": True, "remove": True}).add_to(m)
        out = st_folium(m, height=440, use_container_width=True, returned_objects=["all_drawings"], key=f"draw_{center}")
        drawings = (out or {}).get("all_drawings") or []
        if drawings:
            try:
                aoi = read_aoi({"type": "FeatureCollection", "features": drawings})
            except AOIError as e:
                st.error(str(e))
        else:
            st.caption("Draw a rectangle or polygon with the tools at the left edge of the map.")
    with t_up:
        up = st.file_uploader("KML, KMZ, GeoJSON, GeoPackage or zipped shapefile",
                              type=["kml", "kmz", "geojson", "json", "zip", "gpkg"])
        if up is not None:
            try:
                aoi = read_aoi(up.getvalue(), up.name)
            except Exception as e:  # noqa: BLE001
                st.error(f"This file could not be read as an area: {e}")

if aoi is not None:
    try:
        info = check_aoi(aoi, s.max_area_km2)
        note = ""
        if info["area_km2"] > s.tile_above_km2:
            from bldg3d.tiling import make_tiles
            from bldg3d.aoi import aoi_geometry
            n_t = len(make_tiles(aoi_geometry(aoi), aoi.estimate_utm_crs(), s.tile_km))
            note = (f" It will be downloaded in <b>{n_t} tiles</b> and compiled before the analysis; this can take "
                    "30+ minutes. If it stops, press Extract again to resume.")
        html(f'<div class="areainfo">Area <b>{info["area_km2"]:,} km²</b>{note}</div>')
    except AOIError as e:
        st.error(str(e)); aoi = None

# ============================================================================ extract
html(style.step(2, "Extract the buildings"))
name = st.text_input("Name for the files", value="demo_wtc_bengaluru" if demo else "my_area")
ready = aoi is not None and (demo is not None or bool(ee_project))
if not demo and not ee_project:
    st.caption("Sign in to Earth Engine in the sidebar first.")
elif aoi is None:
    st.caption("Choose an area first.")
if st.button("Extract buildings", type="primary", disabled=not ready):
    bar = st.progress(0.0, text="Starting")
    logbox = st.status("Working", expanded=False)
    try:
        out_dir = tempfile.mkdtemp(prefix="bldg3d_")
        bm = cached_basemap(tuple(round(v, 5) for v in aoi.total_bounds), list(SOURCES)[0])
        kw = dict(buildings=demo[1], sat=demo[2]) if demo else dict(ee_project=ee_project, service_account_json=sa_json)
        res = run(aoi, s, out_dir=out_dir, name=name, log=lambda m_: logbox.write(m_),
                  progress=lambda f, step_: bar.progress(min(f, 1.0), text=step_), basemap=bm, **kw)
        st.session_state["res"] = res
        logbox.update(label="Done", state="complete")
        st.rerun()
    except Exception as e:  # noqa: BLE001
        logbox.update(label="Stopped", state="error")
        st.error(f"Extraction stopped: {e}")

# ============================================================================ results
if res is not None:
    b, sm = res.buildings, res.summary
    html(style.step(3, "Explore and download", f"{sm['area_name'].replace('_', ' ')} · {sm['area_km2']} km²"))
    c = st.columns(5)
    c[0].metric("Buildings", f"{sm['buildings']:,}")
    c[1].metric("With a height", f"{sm['with_height'] / max(sm['buildings'], 1):.0%}")
    c[2].metric("Median height", f"{sm['median_height_m']:.1f} m" if sm["median_height_m"] else "–")
    c[3].metric("Tallest", f"{sm['max_height_m']:.0f} m" if sm["max_height_m"] else "–")
    if sm.get("change_counts"):
        cc_ = sm["change_counts"]
        c[4].metric(f"New or taller since {sm['satellite_height_year']}", f"{cc_.get('new', 0) + cc_.get('taller', 0):,}")
    else:
        c[4].metric(f"Not seen in {sm['satellite_height_year']}", f"{sm['possibly_new_after_height_year']:,}")

    val = sm.get("estimate_validation")
    if val:
        with st.expander(f"How accurate is the {val['target_year']} estimate?"):
            v = st.columns(4)
            v[0].metric("Height error, typical", f"{val.get('height_mae_m', float('nan')):.1f} m")
            v[1].metric("Height error, RMSE", f"{val.get('height_rmse_m', float('nan')):.1f} m")
            v[2].metric("Height R²", f"{val.get('height_r2', float('nan')):.2f}")
            v[3].metric("Building presence right", f"{val.get('presence_accuracy', float('nan')):.0%}")
            st.caption(f"Tested on {val.get('n_test', 0):,} pixels of {val['train_year']} held back from training. "
                       f"Buildings without a clear change keep Google's {val['train_year']} height; changes under "
                       f"{s.min_change_m:g} m are ignored. Check against what you know of the area.")

    tabs = st.tabs(["3D view", "Images", "Map", "Numbers", "Download"])
    with tabs[0]:
        cc = st.columns([3, 2])
        opts = ["Floors", "Height source"] + (["Change since " + str(sm["satellite_height_year"])] if "change" in b.columns else [])
        colour_by = cc[0].radio("Colour", opts, horizontal=True)
        bm_choice = cc[1].radio("Background", list(SOURCES), horizontal=True, format_func=lambda k: k.split(" (")[0])
        cb = "floors" if colour_by == "Floors" else "source" if colour_by == "Height source" else "change"
        bm = cached_basemap(tuple(round(v, 5) for v in b.total_bounds), bm_choice)
        st.pydeck_chart(render3d.deck(b, basemap=bm, colour_by=cb), width="stretch", height=600)
        if cb == "floors":
            items = [(f"{n} floor{'' if n == '1' else 's'}", c_) for n, c_ in zip(FLOOR_NAMES, FLOOR_COLOURS)] + [("No height", NO_HEIGHT_COLOUR)]
        elif cb == "source":
            items = [(SOURCE_LABELS[k], v_) for k, v_ in SOURCE_COLOURS.items()]
        else:
            items = [(CHANGE_LABELS[k], CHANGE_COLOURS[k]) for k in CHANGE_LABELS]
        html(style.legend(items))
        st.caption("Drag to move, right-drag or Ctrl-drag to tilt and turn, scroll to zoom. Point at a building for "
                   "its height, floors and source. " + SOURCES[bm_choice][1])
    with tabs[1]:
        imgs = [k for k in res.files if k.startswith("3D image") and k.endswith("(.png)")]
        for k in imgs:
            st.image(res.files[k], width="stretch")
        st.caption("300 dpi PNG and vector SVG versions are under Download.")
    with tabs[2]:
        import folium
        from streamlit_folium import st_folium
        fm = folium.Map(location=[b["lat"].mean(), b["lon"].mean()], zoom_start=16, tiles=None)
        add_folium_layers(fm)
        show = b.nlargest(15000, "footprint_m2") if len(b) > 15000 else b
        colmap = dict(zip(FLOOR_NAMES, FLOOR_COLOURS))
        gj = json.loads(show[["name", "height_m", "floors", "height_src", "floor_band", "geometry"]]
                        .assign(height_m=show["height_m"].round(1)).to_json())
        folium.GeoJson(gj, style_function=lambda f: {"fillColor": colmap.get(f["properties"]["floor_band"], NO_HEIGHT_COLOUR),
                                                     "color": "#1D2430", "weight": 0.3, "fillOpacity": 0.85},
                       tooltip=folium.GeoJsonTooltip(["name", "height_m", "floors", "height_src"],
                                                     aliases=["Name", "Height (m)", "Floors", "Source"])).add_to(fm)
        st_folium(fm, height=540, use_container_width=True, returned_objects=[], key="resmap")
    with tabs[3]:
        import altair as alt
        cc = st.columns(2)
        order = FLOOR_NAMES + ["No height"]
        fb = (b["floor_band"].replace("", "No height").value_counts().reindex(order).fillna(0)
              .rename_axis("Floors").reset_index(name="Buildings"))
        cc[0].markdown("**Buildings by floors**")
        cc[0].altair_chart(alt.Chart(fb).mark_bar(color=style.ACCENT).encode(
            x=alt.X("Floors:N", sort=order, axis=alt.Axis(labelAngle=0)), y="Buildings:Q",
            tooltip=["Floors", "Buildings"]).properties(height=260), width="stretch")
        src = b["height_src"].map(SOURCE_LABELS).value_counts().rename_axis("Source").reset_index(name="Buildings")
        cc[1].markdown("**Where each height comes from**")
        cc[1].altair_chart(alt.Chart(src).mark_bar(color=style.ACCENT).encode(
            y=alt.Y("Source:N", sort="-x", axis=alt.Axis(labelLimit=260, title=None)), x="Buildings:Q",
            tooltip=["Source", "Buildings"]).properties(height=260), width="stretch")
        fmod = sm["floor_model"]
        st.markdown(f"Floors from satellite height: **floors = {fmod['intercept']:.2f} + {fmod['slope']:.3f} × height** "
                    f"({fmod.get('source')}).")
        st.markdown("**Tallest 50 buildings**")
        show_cols = {"name": "Name", "height_m": "Height (m)", "height_src": "Height source", "floors": "Floors",
                     "floors_src": "Floors source", "building_levels": "OSM floors", "sat_h_median": f"Satellite {sm['satellite_height_year']} (m)",
                     "h_target_est": f"{TARGET_YEAR} estimate (m)", "change": "Change", "footprint_m2": "Footprint (m²)", "bid": "ID"}
        tbl = b[[c for c in show_cols if c in b.columns]].sort_values("height_m", ascending=False).head(50)
        tbl = tbl.assign(height_src=tbl["height_src"].map(SOURCE_LABELS), floors_src=tbl["floors_src"].map(SOURCE_LABELS)).rename(columns=show_cols)
        st.dataframe(tbl.round(1), width="stretch", hide_index=True)
    with tabs[4]:
        groups = {"For GIS": ["Shapefile (.zip)", "GeoPackage (.gpkg)", "GeoJSON", "CSV table"],
                  "3D": ["Google Earth 3D (.kml)", "Interactive 3D (.html)"] + [k for k in res.files if k.startswith("3D image")],
                  "Everything": ["Everything (.zip)", "Summary (.json)"]}
        cols = st.columns(3)
        for col, (gname, keys) in zip(cols, groups.items()):
            html(f'<div class="dl-group">{gname}</div>', col)
            for k in keys:
                if k in res.files:
                    with open(res.files[k], "rb") as f:
                        col.download_button(k, f.read(), file_name=os.path.basename(res.files[k]),
                                            key=f"dl_{k}", width="stretch")
        st.caption("Shapefile field names are shortened to 10 characters; the zip lists what each field means. "
                   "Footprints © OpenStreetMap contributors (ODbL). Heights: Google Open Buildings 2.5D Temporal "
                   "(CC BY 4.0). Please cite both.")
