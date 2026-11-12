"""Command line: ``bldg3d run --aoi area.kml --project my-ee-project`` or ``bldg3d demo``."""
from __future__ import annotations

import argparse

from .config import Settings, LATEST_HEIGHT_YEAR


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bldg3d", description="Extract 3D buildings (OSM + satellite heights) for an area")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract buildings for a KML/KMZ/GeoJSON/zipped-shapefile area")
    r.add_argument("--aoi", required=True)
    r.add_argument("--project", required=True, help="your Earth Engine project ID")
    r.add_argument("--key", help="service-account JSON key (optional; otherwise uses earthengine authenticate)")
    r.add_argument("--year", type=int, default=LATEST_HEIGHT_YEAR, help="year of satellite heights (2016-2023)")
    r.add_argument("--google-footprints", action="store_true", help="add Google Open Buildings footprints where OSM has none")
    r.add_argument("--no-estimate", action="store_true", help="skip the modelled current-year heights (Sentinel-1/2)")
    r.add_argument("--name", default=None)
    r.add_argument("--out", default="output")
    r.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a setting, e.g. --set tile_km=1 --set cache_dir=D:/bldg3d_cache")
    d = sub.add_parser("demo", help="run the built-in Bengaluru demo (no sign-in)")
    d.add_argument("--out", default="output")
    a = ap.parse_args(argv)

    from .pipeline import run
    if a.cmd == "demo":
        from .demo import demo_data
        aoi, b, sat = demo_data()
        res = run(aoi, Settings(), out_dir=a.out, name="demo_wtc_bengaluru", buildings=b, sat=sat)
    else:
        from .aoi import read_aoi
        import os
        with open(a.aoi, "rb") as f:
            aoi = read_aoi(f.read(), os.path.basename(a.aoi))
        key = open(a.key).read() if a.key else None
        s = Settings(height_year=a.year, fill_google_footprints=a.google_footprints, estimate_target=not a.no_estimate)
        for kv in a.set:
            k, v = (x.strip() for x in kv.split("=", 1))
            if not hasattr(s, k):
                ap.error(f"unknown setting: {k}")
            cur = getattr(s, k)
            if isinstance(cur, bool):
                v = v.lower() in ("1", "true", "yes", "y")
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            setattr(s, k, v)
        res = run(aoi, s, out_dir=a.out, name=a.name or os.path.splitext(os.path.basename(a.aoi))[0],
                  ee_project=a.project, service_account_json=key)
    for k, v in res.files.items():
        print(f"{k:35s} {v}")


if __name__ == "__main__":
    main()
