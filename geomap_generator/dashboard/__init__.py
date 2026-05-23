# geomap_generator/dashboard/__init__.py
"""GeoMap Dashboard overlay package.

GeoMapDashboardOperator is imported directly from .modal by the addon
__init__.py to avoid pulling bpy into the package namespace at import time
(which would break headless unit tests for widgets and layout).
"""
