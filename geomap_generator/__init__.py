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
        "provider_help",
        "token_security",
        "progress",
        "search_cache",
        "serialization",
        "settings",
        "validation",
        "mesh_builder",
        "blender_scene",
        "layer_style",
        "topology",
        "geometry_payload",
        "kmz",
        "imagery",
        "dem",
        "overpass",
        "providers",
        "terrain_renderer",
        "vector_renderer",
        "annotation_renderer",
        "osm_3d",
        "geocoder",
        "route_fetcher",
        "service_client",
        "weather",
        "weather_renderer",
    ):
        module = importlib.import_module(f"{__name__}.{module_name}")
        importlib.reload(module)

    map_selector = importlib.import_module(f"{__name__}.map_selector")
    importlib.reload(map_selector)

    # Reload dashboard submodules in dependency order (widgets/layout/renderer
    # are pure-Python; modal imports bpy but must be reloaded last so it picks
    # up the freshly-reloaded widget and layout modules).
    for dashboard_sub in ("widgets", "renderer", "layout", "modal"):
        sub = importlib.import_module(f"{__name__}.dashboard.{dashboard_sub}")
        importlib.reload(sub)
    dashboard = importlib.import_module(f"{__name__}.dashboard")
    importlib.reload(dashboard)

    operators = importlib.import_module(f"{__name__}.operators")
    panels = importlib.import_module(f"{__name__}.panels")
    properties = importlib.import_module(f"{__name__}.properties")

    operators = importlib.reload(operators)
    panels = importlib.reload(panels)
    properties = importlib.reload(properties)

    from .dashboard.modal import GeoMapDashboardOperator
    from .map_selector import GeoMapRoutePickerOperator, GeoMapSelectorOperator
    from .operators import (
        GeoMapAddRouteOperator,
        GeoMapCancelOperator,
        GeoMapClearDownloadCacheOperator,
        GeoMapClearHistoryOperator,
        GeoMapCreatePlaceLabelOperator,
        GeoMapDeletePresetOperator,
        GeoMapGenerateOperator,
        GeoMapImportAllRoutesOperator,
        GeoMapImportRouteOperator,
        GeoMapImportSelectedKmzOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapLoadHistoryOperator,
        GeoMapLoadPresetOperator,
        GeoMapOpenRoutesPanelOperator,
        GeoMapRegenerateFromCacheOperator,
        GeoMapRemoveRouteOperator,
        GeoMapRenameHistoryOperator,
        GeoMapSavePresetOperator,
        GeoMapShowCoordinatesOperator,
        GeoMapSearchRoutePointOperator,
        GeoMapStoreBasemapTokenOperator,
        GeoMapUpdateLayerOperator,
    )
    from .panels import (
        GeoMapAnnotationsPanel,
        GeoMapInputPanel,
        GeoMapKmzPanel,
        GeoMapLayerVisibilityPanel,
        GeoMapOutputPanel,
        GeoMapPanel,
        GeoMapPoiPanel,
        GeoMapPresetsPanel,
        GeoMapProgressPanel,
        GeoMapQualityPanel,
        GeoMapRoutePanel,
        GeoMapSearchHistoryPanel,
        GeoMapTerrainPanel,
        GeoMapUpdatePanel,
        GeoMapVectorPanel,
        GeoMapWeatherPanel,
    )
    from .properties import GeoMapAddonPreferences, GeoMapProperties, GeoMapRouteItem

    return (
        GeoMapSelectorOperator,
        GeoMapRoutePickerOperator,
        GeoMapDashboardOperator,
        GeoMapStoreBasemapTokenOperator,
        GeoMapAddonPreferences,
        GeoMapRouteItem,
        GeoMapProperties,
        GeoMapPanel,
        GeoMapInputPanel,
        GeoMapQualityPanel,
        GeoMapVectorPanel,
        GeoMapPoiPanel,
        GeoMapTerrainPanel,
        GeoMapKmzPanel,
        GeoMapOutputPanel,
        GeoMapAnnotationsPanel,
        GeoMapUpdatePanel,
        GeoMapWeatherPanel,
        GeoMapProgressPanel,
        GeoMapSearchHistoryPanel,
        GeoMapPresetsPanel,
        GeoMapLayerVisibilityPanel,
        GeoMapRoutePanel,
        GeoMapGenerateOperator,
        GeoMapImportRouteOperator,
        GeoMapAddRouteOperator,
        GeoMapRemoveRouteOperator,
        GeoMapImportAllRoutesOperator,
        GeoMapImportSelectedKmzOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapCancelOperator,
        GeoMapLoadHistoryOperator,
        GeoMapRenameHistoryOperator,
        GeoMapClearHistoryOperator,
        GeoMapClearDownloadCacheOperator,
        GeoMapCreatePlaceLabelOperator,
        GeoMapUpdateLayerOperator,
        GeoMapSearchRoutePointOperator,
        GeoMapOpenRoutesPanelOperator,
        GeoMapShowCoordinatesOperator,
        GeoMapRegenerateFromCacheOperator,
        GeoMapSavePresetOperator,
        GeoMapLoadPresetOperator,
        GeoMapDeletePresetOperator,
    )


def _draw_routes_context_menu(self, context):
    import bpy as _bpy
    col = _bpy.data.collections.get("GeoMap")
    if col is not None and col.get("geomap_bbox"):
        self.layout.separator()
        self.layout.operator(
            "geomap.open_routes_popup",
            text="Add Routes",
            icon="CURVE_PATH",
        )


def register():
    import bpy

    for cls in _load_classes():
        bpy.utils.register_class(cls)
    from .properties import GeoMapProperties

    bpy.types.Scene.geomap_props = bpy.props.PointerProperty(type=GeoMapProperties)
    bpy.types.VIEW3D_MT_object_context_menu.append(_draw_routes_context_menu)


def unregister():
    import bpy

    bpy.types.VIEW3D_MT_object_context_menu.remove(_draw_routes_context_menu)
    for cls in reversed(_load_classes()):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.geomap_props
