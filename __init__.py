"""
Blender addon entrypoint.

The implementation lives in the ``geomap_generator`` package. Keeping this
file as a thin wrapper prevents Blender from loading stale legacy code when the
project folder is installed directly as an addon.
"""

import importlib
import sys

bl_info = {
    "name": "GeoMap Generator",
    "author": "Massimo",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > GeoMap",
    "description": "Generate 3D maps from geographic data",
    "category": "Object",
}

if __package__:
    _IMPL_NAME = f"{__package__}.geomap_generator"
    _impl = importlib.import_module(".geomap_generator", __package__)
else:
    _IMPL_NAME = "geomap_generator"
    _impl = importlib.import_module("geomap_generator")

if _IMPL_NAME in sys.modules:
    _impl = importlib.reload(_impl)


def register():
    _impl.register()


def unregister():
    _impl.unregister()

__all__ = ["bl_info", "register", "unregister"]
