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

_REGISTERED_CLASSES = ()
_SCENE_PROPS_REGISTERED = False
_CONTEXT_MENUS_REGISTERED = False


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
        "model_library",
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
        GeoMapImportSelectedModelCandidateOperator,
        GeoMapImportSelectedKmzOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapLoadHistoryOperator,
        GeoMapLoadPresetOperator,
        GeoMapRegenerateFromCacheOperator,
        GeoMapRemoveRouteOperator,
        GeoMapRenameHistoryOperator,
        GeoMapSavePresetOperator,
        GeoMapShowCoordinatesOperator,
        GeoMapSearchRoutePointOperator,
        GeoMapStoreBasemapTokenOperator,
        GeoMapUpdateAddonOperator,
        GeoMapUpdateLayerOperator,
    )
    from .panels import (
        GeoMapPanel,
        GeoMapToolbarPanel,
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
        GeoMapToolbarPanel,
        GeoMapPanel,
        GeoMapGenerateOperator,
        GeoMapImportRouteOperator,
        GeoMapAddRouteOperator,
        GeoMapRemoveRouteOperator,
        GeoMapImportAllRoutesOperator,
        GeoMapImportSelectedKmzOperator,
        GeoMapImportSelectedPoi3DOperator,
        GeoMapImportSelectedModelCandidateOperator,
        GeoMapCancelOperator,
        GeoMapLoadHistoryOperator,
        GeoMapRenameHistoryOperator,
        GeoMapClearHistoryOperator,
        GeoMapClearDownloadCacheOperator,
        GeoMapUpdateAddonOperator,
        GeoMapCreatePlaceLabelOperator,
        GeoMapUpdateLayerOperator,
        GeoMapSearchRoutePointOperator,
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
            "geomap.open_dashboard",
            text="Open GeoMap Dashboard",
            icon="WORLD",
        )


def _draw_place_label_context_menu(self, context):
    objects = list(getattr(context, "selected_objects", ()) or ())
    active = getattr(context, "active_object", None)
    if active is not None and active not in objects:
        objects.append(active)

    def _can_create_label(obj) -> bool:
        layer = obj.get("geomap_layer", "") if obj is not None else ""
        return layer == "place_label" or layer.startswith("poi_")

    if not any(_can_create_label(obj) for obj in objects):
        return

    self.layout.separator()
    self.layout.operator(
        "geomap.create_place_label",
        text="Create Label from Selected POI",
        icon="FONT_DATA",
    )


def _draw_model_candidate_context_menu(self, context):
    obj = getattr(context, "active_object", None)
    if obj is None or obj.get("geomap_layer", "") != "model_candidate":
        return

    self.layout.separator()
    self.layout.operator(
        "geomap.import_selected_model_candidate",
        text="Download and Apply 3D Model",
        icon="IMPORT",
    )


def register():
    global _CONTEXT_MENUS_REGISTERED, _REGISTERED_CLASSES, _SCENE_PROPS_REGISTERED
    import bpy

    classes = _load_classes()
    for cls in classes:
        bpy.utils.register_class(cls)
    _REGISTERED_CLASSES = classes
    from .properties import GeoMapProperties

    bpy.types.Scene.geomap_props = bpy.props.PointerProperty(type=GeoMapProperties)
    _SCENE_PROPS_REGISTERED = True
    if not _CONTEXT_MENUS_REGISTERED:
        bpy.types.VIEW3D_MT_object_context_menu.append(_draw_routes_context_menu)
        bpy.types.VIEW3D_MT_object_context_menu.append(_draw_place_label_context_menu)
        bpy.types.VIEW3D_MT_object_context_menu.append(_draw_model_candidate_context_menu)
        _CONTEXT_MENUS_REGISTERED = True


def unregister():
    global _CONTEXT_MENUS_REGISTERED, _REGISTERED_CLASSES, _SCENE_PROPS_REGISTERED
    import bpy

    if _CONTEXT_MENUS_REGISTERED:
        for menu_func in (
            _draw_model_candidate_context_menu,
            _draw_place_label_context_menu,
            _draw_routes_context_menu,
        ):
            try:
                bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)
            except Exception:
                pass
        _CONTEXT_MENUS_REGISTERED = False

    if _SCENE_PROPS_REGISTERED and hasattr(bpy.types.Scene, "geomap_props"):
        del bpy.types.Scene.geomap_props
        _SCENE_PROPS_REGISTERED = False

    for cls in reversed(_REGISTERED_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _REGISTERED_CLASSES = ()
