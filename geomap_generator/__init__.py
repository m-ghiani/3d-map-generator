"""
GeoMap Generator — Blender addon for 3D geographic maps.
"""

bl_info = {
    "name": "GeoMap Generator",
    "author": "Massimo",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > GeoMap",
    "description": "Generate 3D maps from geographic data",
    "category": "Object",
}


def _load_classes():
    import importlib

    for module_name in (
        "models",
        "exceptions",
        "threading_utils",
        "coordinates",
        "scene_units",
        "download_cache",
        "persistent_log",
        "progress",
        "search_cache",
        "settings",
        "validation",
        "mesh_builder",
        "blender_scene",
        "layer_style",
        "imagery",
        "dem",
        "overpass",
        "providers",
        "terrain_renderer",
        "vector_renderer",
        "annotation_renderer",
        "osm_3d",
    ):
        module = importlib.import_module(f"{__name__}.{module_name}")
        importlib.reload(module)

    operators = importlib.import_module(f"{__name__}.operators")
    panels = importlib.import_module(f"{__name__}.panels")
    properties = importlib.import_module(f"{__name__}.properties")

    operators = importlib.reload(operators)
    panels = importlib.reload(panels)
    properties = importlib.reload(properties)

    from .operators import (
        GeoMapCancelOperator,
        GeoMapClearDownloadCacheOperator,
        GeoMapClearHistoryOperator,
        GeoMapGenerateOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapLoadHistoryOperator,
    )
    from .panels import GeoMapPanel, GeoMapProgressPanel, GeoMapSearchHistoryPanel
    from .properties import GeoMapAddonPreferences, GeoMapProperties

    return (
        GeoMapAddonPreferences,
        GeoMapProperties,
        GeoMapPanel,
        GeoMapProgressPanel,
        GeoMapSearchHistoryPanel,
        GeoMapGenerateOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapCancelOperator,
        GeoMapLoadHistoryOperator,
        GeoMapClearHistoryOperator,
        GeoMapClearDownloadCacheOperator,
    )


def register():
    import bpy

    for cls in _load_classes():
        bpy.utils.register_class(cls)
    from .properties import GeoMapProperties

    bpy.types.Scene.geomap_props = bpy.props.PointerProperty(type=GeoMapProperties)


def unregister():
    import bpy

    for cls in reversed(_load_classes()):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.geomap_props
