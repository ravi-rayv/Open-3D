"""Open 3D Buildings: best-available 3D building footprints for any area.

Footprints and tags come from OpenStreetMap as of today; satellite heights come
from Google Open Buildings 2.5D Temporal (latest year 2023).

Authors: Ravi Satyappa Dabbanavar and Rohini N, Nitte School of Architecture,
Planning and Design, Nitte University, Bengaluru.
"""
from .config import Settings, LATEST_HEIGHT_YEAR  # noqa: F401
from .aoi import read_aoi, AOIError  # noqa: F401
from .pipeline import run, Result  # noqa: F401

__version__ = "0.1.0"
__authors__ = ["Ravi Satyappa Dabbanavar", "Rohini N"]
