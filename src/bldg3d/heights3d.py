"""Best available height and floors per building.

Priority for height:   OSM ``height`` tag  >  OSM ``building:levels`` × storey height  >  satellite height
Priority for floors:   OSM ``building:levels``  >  OSM ``height`` tag  >  satellite height via a floor model

The floor model is fitted on the study area's own OSM floor tags when there are
enough of them, otherwise the Bengaluru calibration is used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor

from .config import DEFAULT_FLOOR_MODEL, FLOOR_BANDS, FLOOR_NAMES


def fit_floor_model(df: pd.DataFrame, s, log=print) -> dict:
    lv = df["building_levels"].where(df["building_levels"].between(1, 60))
    h = df["sat_h_median"]
    ok = lv.notna() & (h >= s.min_height_m)
    n = int(ok.sum())
    if n >= s.min_calibration:
        x, y = h[ok].values.reshape(-1, 1), lv[ok].values
        m = HuberRegressor().fit(x, y)
        pred = np.clip(np.round(m.predict(x)), 1, None)
        good = np.abs(pred - y) <= np.maximum(3, 0.4 * y)          # drop tagging / height errors
        if good.sum() >= s.min_calibration:
            m = HuberRegressor().fit(x[good], y[good])
        a, b = float(m.intercept_), float(m.coef_[0])
        if b > 0.1:
            e = np.clip(np.round(a + b * x[good].ravel()), 1, None) - y[good]
            model = {"intercept": a, "slope": b, "source": f"fitted on {int(good.sum()):,} local OSM floor tags",
                     "n": int(good.sum()), "mae": float(np.abs(e).mean()), "within_1": float((np.abs(e) <= 1).mean())}
            log(f"Floors: local model floors = {a:.2f} + {b:.3f} × height (≈ {1 / b:.2f} m per floor; "
                f"{model['within_1']:.0%} within ±1 floor on {model['n']:,} tags)")
            return model
    log(f"Floors: {n} local floor tags (< {s.min_calibration}); using the {DEFAULT_FLOOR_MODEL['source']}")
    return dict(DEFAULT_FLOOR_MODEL, n=n)


def floor_band(f):
    return pd.cut(pd.Series(f, dtype=float), FLOOR_BANDS, labels=FLOOR_NAMES, right=True)


def apply_estimate(df: pd.DataFrame, s) -> pd.DataFrame:
    """Target-year height from the modelled change (``est_*`` columns).

    Existing buildings: height = satellite height (height year) + modelled change, if the change is at
    least ``min_change_m``. New buildings (no usable satellite height, or not seen then): modelled height.
    """
    df = df.copy()
    for c in ["est_h_target", "est_h_train", "est_p_target", "est_p_train"]:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    sat = pd.to_numeric(df.get("sat_h_median"), errors="coerce").where(lambda x: x >= s.min_height_m)
    seen = pd.to_numeric(df.get("sat_presence"), errors="coerce").fillna(1.0) >= s.presence_new_max
    delta = df["est_h_target"] - df["est_h_train"]
    big = delta.abs() >= s.min_change_m
    exists_now = df["est_p_target"] >= s.est_presence_min
    old = sat.notna() & seen
    h = pd.Series(np.nan, index=df.index)
    h[old] = np.where(big[old] & exists_now[old], (sat + delta)[old].clip(lower=s.min_height_m), sat[old])
    new = ~old & exists_now & df["est_h_target"].notna()
    h[new] = df.loc[new, "est_h_target"].clip(lower=s.min_height_m)
    df["h_target_est"] = h.round(2)
    df["est_change_m"] = delta.round(2)
    ch = np.select([new & (df["est_p_train"] < s.presence_new_max),
                    old & big & exists_now & (delta > 0), old & big & (delta < 0)],
                   ["new", "taller", "lower"], "stable")
    df["change"] = np.where(df["est_h_target"].isna(), "", ch)
    return df


def combine(df: pd.DataFrame, model: dict, s) -> pd.DataFrame:
    df = df.copy()
    for c in ["sat_h_median", "sat_h_p90", "sat_presence"]:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    tag_h = df["height"].where(df["height"].between(2, 600))
    tag_lv = df["building_levels"].where(df["building_levels"].between(1, 200))
    sat_h = df["sat_h_median"].where(df["sat_h_median"] >= s.min_height_m)
    use_est = "h_target_est" in df.columns and df["h_target_est"].notna().any()
    if use_est:                       # modelled target-year height replaces the satellite value where available
        est = df["h_target_est"]
        changed = df["change"].isin(["new", "taller", "lower"]) if "change" in df.columns else est.notna()
        orig_missing = sat_h.isna()                       # no usable satellite height in the height year
        sat_h = est.where(est.notna(), sat_h)
        sat_src = np.where(est.notna() & (changed | orig_missing), "estimate",
                           np.where(sat_h.notna(), "satellite", "none"))
    else:
        sat_src = np.where(sat_h.notna(), "satellite", "none")

    # height (m)
    df["height_m"] = np.where(tag_h.notna(), tag_h,
                      np.where(tag_lv.notna(), tag_lv * s.storey_m + max(s.ground_floor_m - s.storey_m, 0), sat_h))
    df["height_src"] = np.where(tag_h.notna(), "osm_height", np.where(tag_lv.notna(), "osm_levels", sat_src))
    # floors
    sat_f = np.clip(np.round(model["intercept"] + model["slope"] * sat_h), 1, None)
    tag_h_f = np.clip(np.round((tag_h - s.ground_floor_m) / s.storey_m + 1), 1, None)
    df["floors"] = np.where(tag_lv.notna(), np.round(tag_lv), np.where(tag_h.notna(), tag_h_f, sat_f))
    df["floors_src"] = np.where(tag_lv.notna(), "osm_levels", np.where(tag_h.notna(), "osm_height", sat_src))
    df["floors_sat"] = sat_f
    df["floor_band"] = floor_band(df["floors"]).astype(str).replace("nan", "")

    # quality flags
    flags = pd.DataFrame(index=df.index)
    flags["not_seen_sat"] = df["sat_presence"].notna() & (df["sat_presence"] < s.presence_new_max)
    flags["no_sat_height"] = df["sat_h_median"].where(df["sat_h_median"] >= s.min_height_m).isna()
    flags["tag_vs_sat"] = tag_lv.notna() & sat_f.notna() & ((sat_f - tag_lv).abs() > np.maximum(3, 0.4 * tag_lv))
    flags["sat_capped_100m"] = df["sat_h_p90"] >= 99
    df["flags"] = flags.apply(lambda r: ";".join(k for k, v in r.items() if v), axis=1)
    df["possibly_new"] = flags["not_seen_sat"] & tag_lv.isna() & tag_h.isna()
    if "change" in df.columns:
        df["possibly_new"] = df["possibly_new"] | (df["change"] == "new")
    return df


def add_geometry_stats(gdf, utm):
    m = gdf.to_crs(utm)
    gdf = gdf.copy()
    gdf["footprint_m2"] = m.area.round(1).values
    c = m.centroid.to_crs(4326)
    gdf["lon"], gdf["lat"] = c.x.round(6).values, c.y.round(6).values
    gdf["floor_area_m2"] = (gdf["footprint_m2"] * gdf["floors"]).round(1)
    return gdf
