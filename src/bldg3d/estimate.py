"""Modelled building height and presence for a recent year (default 2026).

No published dataset has building heights after 2023. This module trains
random-forest models in Google Earth Engine that learn how Sentinel-2 (optical)
and Sentinel-1 (radar) images relate to Google Open Buildings 2.5D heights and
building presence in the *training year* (2023), using pixels in and around the
study area. The same models are then applied to the training-year images and to
the *target-year* images, so change is measured with one model on both dates.

Outputs per building (10 m model, reduced over the footprint):
    est_h_target, est_h_train  - modelled height (m) in the target and training year
    est_p_target, est_p_train  - modelled building presence (0-1)
Validation on held-out training pixels is returned with the results.

These are *estimates*: Sentinel pixels are 10 m, so single-building heights are
rough and tall towers are underestimated; detection of new buildings and added
floors is the main purpose.
"""
from __future__ import annotations

import json
import uuid
from datetime import date

import pandas as pd

from .earthengine import TEMPORAL, _retry

S2 = "COPERNICUS/S2_SR_HARMONIZED"
CS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
S1 = "COPERNICUS/S1_GRD"
BASE = ["B2", "B3", "B4", "B8", "B11", "B12", "ndvi", "ndbi", "mndwi", "VV", "VH", "VVmVH"]
TEX = ["B8", "ndbi", "VV", "VH"]


def _period(year):
    start = f"{year}-01-01"
    end = f"{year + 1}-01-01" if year < date.today().year else date.today().isoformat()
    return start, end


def features(region, year, proj):
    """Stack of Sentinel-2/1 features for one year, on a 10 m grid in ``proj``."""
    import ee
    start, end = _period(year)
    s2 = (ee.ImageCollection(S2).filterBounds(region).filterDate(start, end)
          .linkCollection(ee.ImageCollection(CS), ["cs_cdf"])
          .map(lambda i: i.updateMask(i.select("cs_cdf").gte(0.6))))
    m = s2.select(["B2", "B3", "B4", "B8", "B11", "B12"]).median().divide(10000)
    m = m.addBands([m.normalizedDifference(["B8", "B4"]).rename("ndvi"),
                    m.normalizedDifference(["B11", "B8"]).rename("ndbi"),
                    m.normalizedDifference(["B3", "B11"]).rename("mndwi")])
    s1 = (ee.ImageCollection(S1).filterBounds(region).filterDate(start, end)
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
          .select(["VV", "VH"]).median())
    s1 = s1.addBands(s1.select("VV").subtract(s1.select("VH")).rename("VVmVH"))
    img = m.addBands(s1).select(BASE).reproject(proj)
    tex = img.select(TEX).reduceNeighborhood(ee.Reducer.mean().combine(ee.Reducer.stdDev(), "", True),
                                             ee.Kernel.square(2))      # 5 × 5 pixels = 50 m
    return img.addBands(tex).unmask(0)


def train_and_predict(aoi_geom, utm_epsg, train_year=2023, target_year=None, n_samples=8000, buffer_m=2000,
                      trees=120, log=print):
    """Train the models and return (stacked prediction image, validation dict)."""
    import ee
    from shapely.geometry import mapping
    target_year = target_year or date.today().year
    from .tiling import ee_region
    region = ee_region(aoi_geom)
    train_region = region.buffer(buffer_m)
    proj = ee.Projection(f"EPSG:{utm_epsg}").atScale(10)

    obc = (ee.ImageCollection(TEMPORAL).filterBounds(train_region)
           .filterDate(f"{train_year}-01-01", f"{train_year + 1}-01-01"))
    # a mosaic loses the native 0.5 m grid; restore it so the 10 m averaging is correct
    ob = obc.mosaic().setDefaultProjection(obc.first().select("building_height").projection())
    pres = ob.select("building_presence").gte(0.5)
    h = ob.select("building_height").updateMask(pres).unmask(0)
    h10 = h.reduceResolution(ee.Reducer.mean(), maxPixels=1024).reproject(proj).rename("h")
    p10 = pres.reduceResolution(ee.Reducer.mean(), maxPixels=1024).reproject(proj).rename("p")

    f_train = features(train_region, train_year, proj)
    f_target = features(region, target_year, proj)
    bands = f_train.bandNames()

    samples = (f_train.addBands(h10).addBands(p10)
               .sample(region=train_region, scale=10, numPixels=n_samples, seed=7, tileScale=8, geometries=False)
               .randomColumn("rnd", 11))
    train, test = samples.filter(ee.Filter.lt("rnd", 0.7)), samples.filter(ee.Filter.gte("rnd", 0.7))
    built_train = train.filter(ee.Filter.gte("p", 0.3))
    built_test = test.filter(ee.Filter.gte("p", 0.3))

    hm = ee.Classifier.smileRandomForest(trees, minLeafPopulation=3).setOutputMode("REGRESSION").train(
        built_train, "h", bands)
    pm = ee.Classifier.smileRandomForest(trees, minLeafPopulation=3).setOutputMode("REGRESSION").train(
        train, "p", bands)

    # validation on held-out pixels
    ht = built_test.classify(hm, "pred").map(
        lambda f: f.set({"ae": ee.Number(f.get("pred")).subtract(f.get("h")).abs(),
                         "se": ee.Number(f.get("pred")).subtract(f.get("h")).pow(2)}))
    pt = test.classify(pm, "pp").map(
        lambda f: f.set("ok", ee.Number(f.get("pp")).gte(0.5).eq(ee.Number(f.get("p")).gte(0.5))))
    stats = ee.Dictionary({
        "n_train": train.size(), "n_test": test.size(), "n_test_built": built_test.size(),
        "height_mae_m": ht.aggregate_mean("ae"),
        "height_rmse_m": ee.Number(ht.aggregate_mean("se")).sqrt(),
        "height_r": ht.reduceColumns(ee.Reducer.pearsonsCorrelation(), ["pred", "h"]).get("correlation"),
        "presence_accuracy": pt.aggregate_mean("ok"),
    })
    val = _retry(lambda: stats.getInfo())
    if val.get("height_r") is not None:
        val["height_r2"] = val["height_r"] ** 2
    val.update(train_year=train_year, target_year=target_year, model="random forest (Sentinel-1/2, 10 m)",
               period=_period(target_year))
    log(f"2026 model: held-out height MAE {val.get('height_mae_m', float('nan')):.1f} m, "
        f"R² {val.get('height_r2', float('nan')):.2f}; presence accuracy {val.get('presence_accuracy', float('nan')):.0%} "
        f"({val.get('n_test', 0)} test pixels)")

    stack = ee.Image.cat([f_target.classify(hm).rename("est_h_target"), f_train.classify(hm).rename("est_h_train"),
                          f_target.classify(pm).rename("est_p_target"), f_train.classify(pm).rename("est_p_train")])
    return stack, val


def per_building(stack, gdf, s, log=print, progress=None, cache=None, utm=None) -> pd.DataFrame:
    """Mean of the modelled layers over each footprint (5 m sampling so small buildings get a value).

    Buildings are sent in spatially compact batches: each batch then needs the Sentinel composite
    for only a small part of the area, which keeps large areas within Earth Engine's limits."""
    import ee
    from . import tiling
    cache = cache or tiling.cache_dir(s, "bldg3d")
    cols = ["bid", "est_h_target", "est_h_train", "est_p_target", "est_p_train"]

    def request(ch):
        fc = ee.FeatureCollection(json.loads(ch.to_json()))
        st = stack.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=5, tileScale=4).map(
            lambda f: f.select(cols, None, False))
        return pd.DataFrame([r["properties"] for r in st.getInfo()["features"]], columns=cols)

    # the trained model is not identical between runs, so these batches are not reused from the cache
    label = f"estimate {s.target_year}|run={uuid.uuid4().hex}"
    return tiling.run_batches(request, gdf, cols, s, cache, label, utm=utm, log=log, progress=progress)
