from bpy.types import Panel

from .download_cache import cache_stats
from .kmz import catalog_paths
from .persistent_log import log_path
from .provider_help import provider_quality
from .progress import ProgressTracker
from .search_cache import load_history, load_presets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox_unchanged(context) -> bool:
    """Return False only when a GeoMap exists and current props bbox differs.

    Per-layer generate buttons are disabled in that case to avoid desync.
    """
    from .kmz import current_map_bbox
    stored = current_map_bbox()
    if stored is None:
        return True
    props = getattr(getattr(context, "scene", None), "geomap_props", None)
    if props is None:
        return True
    if getattr(props, "input_mode", "COUNTRY") == "COUNTRY":
        return True
    try:
        from .models import BoundingBox
        cur = BoundingBox.from_corners(
            props.latitude, props.longitude,
            props.latitude2, props.longitude2,
        )
        tol = 0.0001
        return (
            abs(cur.min_lat - stored.min_lat) < tol
            and abs(cur.min_lon - stored.min_lon) < tol
            and abs(cur.max_lat - stored.max_lat) < tol
            and abs(cur.max_lon - stored.max_lon) < tol
        )
    except Exception:
        return True


def _layer_row(layout, props, prop_name: str, layer_kind: str, bbox_ok: bool):
    """Checkbox + inline Generate button on a single row."""
    row = layout.row(align=True)
    row.prop(props, prop_name)
    btn = row.row(align=True)
    btn.enabled = bbox_ok
    btn.scale_x = 0.52
    op = btn.operator("geomap.update_layer", text="Generate", icon="FILE_REFRESH")
    op.layer_kind = layer_kind
    return row


def _gen_btn(layout, layer_kind: str, text: str, bbox_ok: bool, icon: str = "FILE_REFRESH"):
    row = layout.row()
    row.enabled = bbox_ok
    op = row.operator("geomap.update_layer", text=text, icon=icon)
    op.layer_kind = layer_kind
    return op


def _addon_prefs(context):
    package = (__package__ or "geomap_generator").split(".", 1)[0]
    for key in (__package__, package, "geomap_generator", "3d-map-generator"):
        if key and key in context.preferences.addons:
            return context.preferences.addons[key].preferences
    return None


# ---------------------------------------------------------------------------
# Root panel
# ---------------------------------------------------------------------------

class GeoMapPanel(Panel):
    bl_label = "GeoMap Generator"
    bl_idname = "VIEW3D_PT_geomap_generator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, context):
        from .operators import _coord_tracking_active
        layout = self.layout
        tracker = ProgressTracker.get_instance()

        # Primary action
        row = layout.row()
        row.scale_y = 1.4
        if tracker.is_running:
            row.operator("geomap.cancel_generation", text="● Generating…  ABORT", icon="CANCEL")
        else:
            row.operator("geomap.generate", text="Generate All Layers", icon="WORLD")

        # Inline progress when active
        if tracker.is_running or (tracker.status and tracker.status != "Idle"):
            box = layout.box()
            pct = int(tracker.progress * 100)
            box.label(text=f"{tracker.status}  {pct}%", icon="INFO")

        layout.separator()
        layout.operator("geomap.open_dashboard", text="Open Dashboard", icon="WINDOW")
        icon = "HIDE_OFF" if _coord_tracking_active else "EYEDROPPER"
        text = "Tracking Coordinates (ESC to stop)" if _coord_tracking_active else "Track Coordinates"
        layout.operator("geomap.show_coordinates", text=text, icon=icon)
        layout.operator("geomap.import_selected_poi_3d", text="Import Selected POI 3D", icon="EMPTY_DATA")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class GeoMapInputPanel(Panel):
    bl_label = "Location"
    bl_idname = "VIEW3D_PT_geomap_input"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        layout = self.layout
        props = context.scene.geomap_props

        layout.row().prop(props, "input_mode", expand=True)
        layout.operator("geomap.open_map_selector", text="Pick Area on Map", icon="WORLD")

        if props.input_mode == "COUNTRY":
            layout.prop(props, "country_region")
        else:
            col = layout.column(align=True)
            col.label(text="Point A")
            col.prop(props, "latitude")
            col.prop(props, "longitude")
            col.separator()
            col.label(text="Point B")
            col.prop(props, "latitude2")
            col.prop(props, "longitude2")


# ---------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------

class GeoMapTerrainPanel(Panel):
    bl_label = "Terrain"
    bl_idname = "VIEW3D_PT_geomap_terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        prefs = _addon_prefs(context)
        bbox_ok = _bbox_unchanged(context)

        if not bbox_ok:
            layout.label(text="Coordinates changed — use Generate All", icon="ERROR")

        # DEM + Satellite generated together as TERRAIN
        row = layout.row(align=True)
        row.prop(props, "import_relief", text="DEM")
        row.prop(props, "import_satellite", text="Satellite")
        _gen_btn(layout, "TERRAIN", "Generate Terrain", bbox_ok, icon="MESH_GRID")

        if props.import_relief:
            box = layout.box()
            box.label(text="DEM Settings", icon="MESH_GRID")
            box.prop(props, "dem_resolution")
            box.prop(props, "dem_height_scale", slider=True)
            box.prop(props, "height_exaggeration", slider=True)
            box.prop(props, "drape_vectors_on_dem")
            box.prop(props, "print_base_height", slider=True)
            row = box.row(align=True)
            row.prop(props, "dem_slope_colors")
            row.prop(props, "add_north_arrow")
            # Contours
            _layer_row(box, props, "import_contours", "CONTOURS", bbox_ok)
            if props.import_contours:
                box.prop(props, "contour_interval_m")

        if props.import_satellite:
            box = layout.box()
            box.label(text="Imagery Settings", icon="IMAGE_DATA")
            box.prop(props, "map_style")
            box.prop(props, "satellite_resolution")
            provider = getattr(prefs, "basemap_provider", "AUTO") if prefs else "AUTO"
            _draw_provider_quality(box, provider)

        layout.separator()
        layout.prop(props, "create_map_box", text="Map Box (Plastico)")
        if props.create_map_box:
            layout.prop(props, "map_box_depth", slider=True)


# ---------------------------------------------------------------------------
# Vectors
# ---------------------------------------------------------------------------

class GeoMapVectorPanel(Panel):
    bl_label = "Vectors"
    bl_idname = "VIEW3D_PT_geomap_vectors"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        bbox_ok = _bbox_unchanged(context)

        if not bbox_ok:
            layout.label(text="Coordinates changed — use Generate All", icon="ERROR")

        _layer_row(layout, props, "import_coast",   "COASTLINES", bbox_ok)
        _layer_row(layout, props, "import_rivers",  "RIVERS",     bbox_ok)
        _layer_row(layout, props, "import_roads",   "ROADS",      bbox_ok)
        _layer_row(layout, props, "import_landuse", "LANDUSE",    bbox_ok)

        # Admin with sub-option
        _layer_row(layout, props, "import_admin", "ADMIN", bbox_ok)
        if props.import_admin:
            layout.prop(props, "admin_level")

        # Buildings with sub-option
        _layer_row(layout, props, "import_buildings", "BUILDINGS", bbox_ok)
        if props.import_buildings:
            layout.prop(props, "building_quality")


# ---------------------------------------------------------------------------
# Cities & POI
# ---------------------------------------------------------------------------

class GeoMapPoiPanel(Panel):
    bl_label = "Cities & POI"
    bl_idname = "VIEW3D_PT_geomap_poi"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        bbox_ok = _bbox_unchanged(context)

        col = layout.column(align=True)
        col.prop(props, "import_cities")
        col.prop(props, "import_place_labels")
        if props.import_place_labels:
            layout.prop(props, "place_label_min_type")
            layout.prop(props, "place_label_size_factor", slider=True)

        layout.separator()
        col = layout.column(align=True)
        col.prop(props, "import_poi_historic")
        col.prop(props, "import_poi_cultural")
        col.prop(props, "import_poi_administrative")
        col.prop(props, "import_poi_natural")

        layout.separator()
        _gen_btn(layout, "CITIES", "Generate Cities/POI", bbox_ok)
        layout.operator(
            "geomap.create_place_label",
            text="Create Text from Selected Markers",
            icon="FONT_DATA",
        )


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

class GeoMapWeatherPanel(Panel):
    bl_label = "Weather"
    bl_idname = "VIEW3D_PT_geomap_weather"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        bbox_ok = _bbox_unchanged(context)

        layout.prop(props, "import_weather")
        if props.import_weather:
            col = layout.column(align=True)
            col.prop(props, "weather_show_temperature")
            col.prop(props, "weather_show_wind")
            layout.prop(props, "weather_grid_size")

        layout.separator()
        _gen_btn(layout, "WEATHER", "Generate Weather", bbox_ok, icon="OUTLINER_OB_LIGHT")


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

class GeoMapAnnotationsPanel(Panel):
    bl_label = "Annotations"
    bl_idname = "VIEW3D_PT_geomap_annotations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        bbox_ok = _bbox_unchanged(context)

        col = layout.column(align=True)
        col.prop(props, "add_legend")
        col.prop(props, "add_scale_bar")
        col.prop(props, "add_north_arrow")

        layout.separator()
        _gen_btn(layout, "ANNOTATIONS", "Generate Annotations", bbox_ok, icon="FONT_DATA")


# ---------------------------------------------------------------------------
# Routes  (only visible when a GeoMap bbox is stored in the scene)
# ---------------------------------------------------------------------------

class GeoMapRoutePanel(Panel):
    bl_label = "Routes"
    bl_idname = "VIEW3D_PT_geomap_route"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout

        layout.row().prop(props, "route_mode", expand=True)
        if props.route_mode == "ROUTE":
            layout.prop(props, "route_profile")

        layout.label(text="Routes")
        row = layout.row()
        row.template_list(
            "UI_UL_list", "geomap_routes",
            props, "routes",
            props, "route_active_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("geomap.add_route", icon="ADD", text="")
        col.operator("geomap.remove_route", icon="REMOVE", text="")

        if props.routes and 0 <= props.route_active_index < len(props.routes):
            active = props.routes[props.route_active_index]
            box = layout.box()
            box.prop(active, "name")
            box.prop(active, "mode", expand=True)
            if active.mode == "ROUTE":
                box.prop(active, "profile")
            box.label(text="Start")
            col = box.column(align=True)
            col.prop(active, "lat1")
            col.prop(active, "lon1")
            op = box.operator("geomap.pick_route_point", text="Pick Start on Map", icon="RESTRICT_SELECT_OFF")
            op.target = "START"
            box.label(text="End")
            col = box.column(align=True)
            col.prop(active, "lat2")
            col.prop(active, "lon2")
            op = box.operator("geomap.pick_route_point", text="Pick End on Map", icon="RESTRICT_SELECT_OFF")
            op.target = "END"
            box.prop(active, "color")
            box.prop(active, "label_start", icon="FONT_DATA")
            box.prop(active, "label_end", icon="FONT_DATA")
            layout.operator("geomap.import_all_routes", text="Import All Routes", icon="CURVE_PATH")
        else:
            box = layout.box()
            box.label(text="Search within map area")
            box.prop(props, "route_search_query", text="", icon="VIEWZOOM")
            row = box.row(align=True)
            op = row.operator("geomap.search_route_point", text="→ Start")
            op.target = "START"
            op = row.operator("geomap.search_route_point", text="→ End")
            op.target = "END"

            layout.label(text="Start")
            col = layout.column(align=True)
            col.prop(props, "route_lat1")
            col.prop(props, "route_lon1")
            op = layout.operator("geomap.pick_route_point", text="Pick Start on Map", icon="RESTRICT_SELECT_OFF")
            op.target = "START"
            layout.prop(props, "route_label_start", icon="FONT_DATA")

            layout.label(text="End")
            col = layout.column(align=True)
            col.prop(props, "route_lat2")
            col.prop(props, "route_lon2")
            op = layout.operator("geomap.pick_route_point", text="Pick End on Map", icon="RESTRICT_SELECT_OFF")
            op.target = "END"
            layout.prop(props, "route_label_end", icon="FONT_DATA")
            layout.operator("geomap.import_route", text="Import Route", icon="CURVE_PATH")


# ---------------------------------------------------------------------------
# KMZ Layers
# ---------------------------------------------------------------------------

class GeoMapKmzPanel(Panel):
    bl_label = "KMZ Layers"
    bl_idname = "VIEW3D_PT_geomap_kmz"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        layout.prop(props, "kmz_selection")
        layout.operator("geomap.import_selected_kmz", text="Download and Integrate KMZ")
        _package_path, user_path = catalog_paths()
        layout.label(text=f"Catalog: {user_path}")


# ---------------------------------------------------------------------------
# Output Geometry (widths, road geometry)
# ---------------------------------------------------------------------------

class GeoMapOutputPanel(Panel):
    bl_label = "Output Geometry"
    bl_idname = "VIEW3D_PT_geomap_output"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        layout.prop(props, "output_preset")
        layout.separator()
        layout.prop(props, "vector_z_offset", slider=True)
        col = layout.column(align=True)
        col.prop(props, "road_geometry")
        col.prop(props, "road_width", slider=True)
        col.separator()
        col.prop(props, "river_geometry")
        col.prop(props, "river_width", slider=True)
        col.separator()
        col.prop(props, "boundary_width", slider=True)
        col.prop(props, "coast_width", slider=True)


# ---------------------------------------------------------------------------
# Quality & Presets
# ---------------------------------------------------------------------------

class GeoMapQualityPanel(Panel):
    bl_label = "Quality & Presets"
    bl_idname = "VIEW3D_PT_geomap_quality"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        layout.prop(props, "quality_preset")
        layout.prop(props, "detail_level")
        layout.prop(props, "auto_lod")
        layout.separator()
        presets = load_presets()
        if presets:
            layout.label(text="Saved Presets")
            for preset in presets:
                name = preset.get("preset_name", "?")
                row = layout.row(align=True)
                op = row.operator("geomap.load_preset", text=name[:28], icon="PRESET")
                op.preset_name = name
                op2 = row.operator("geomap.delete_preset", text="", icon="TRASH")
                op2.preset_name = name
        layout.operator("geomap.save_preset", text="Save Current as Preset", icon="ADD")


# ---------------------------------------------------------------------------
# Progress  (standalone — always accessible during generation)
# ---------------------------------------------------------------------------

class GeoMapProgressPanel(Panel):
    bl_label = "Generation Progress"
    bl_idname = "VIEW3D_PT_geomap_progress"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        tracker = ProgressTracker.get_instance()

        if not tracker.is_running and tracker.status == "Idle":
            layout.label(text="No generation in progress")
            return

        box = layout.box()
        box.label(text=tracker.status, icon="INFO")

        col = layout.column()
        col.scale_y = 0.8
        split = col.split(factor=max(0.01, tracker.progress))
        split.box().label(text=f"{tracker.progress * 100:.0f}%")
        split.label(text="")

        if tracker.error:
            err_box = layout.box()
            err_box.label(text="ERROR:", icon="ERROR")
            for line in tracker.error.split("\n"):
                if line.strip():
                    err_box.label(text=line[:60])

        log_box = layout.box()
        log_box.label(text="Log", icon="TEXT")
        for msg in tracker.logs[-10:]:
            icon = "CHECKMARK" if "✓" in msg else ("CANCEL" if "✗" in msg else "CONSOLE")
            log_box.label(text=msg[:70], icon=icon)

        stats = cache_stats()
        debug_box = layout.box()
        debug_box.label(text=f"Cache: {stats['files']} files  {stats['bytes'] / 1048576:.1f} MB", icon="NETWORK_DRIVE")
        debug_box.label(text=f"hit {stats['hits']}  miss {stats['misses']}  offline {stats['stale_hits']}")
        path = log_path()
        if path:
            debug_box.label(text=f"Log: {path}")

        if tracker.is_running:
            row = layout.row()
            row.scale_y = 1.3
            row.operator("geomap.cancel_generation", text="● ABORT", icon="CANCEL")


# ---------------------------------------------------------------------------
# Search History  (standalone)
# ---------------------------------------------------------------------------

class GeoMapSearchHistoryPanel(Panel):
    bl_label = "Search History"
    bl_idname = "VIEW3D_PT_geomap_search_history"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        layout = self.layout
        history = load_history()

        if not history:
            layout.label(text="No cached searches")
            return

        for index, item in enumerate(history[:10]):
            row = layout.row(align=True)
            label = item.get("label") or "Untitled"
            op = row.operator("geomap.load_history", text=label[:32], icon="TIME")
            op.index = index
            op2 = row.operator("geomap.rename_history", text="", icon="GREASEPENCIL")
            op2.index = index

        layout.operator("geomap.clear_history", text="Clear History", icon="TRASH")


# ---------------------------------------------------------------------------
# Layer Visibility  (standalone, only when GeoMap collection exists)
# ---------------------------------------------------------------------------

class GeoMapLayerVisibilityPanel(Panel):
    bl_label = "Layer Visibility"
    bl_idname = "VIEW3D_PT_geomap_layer_visibility"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, _context):
        import bpy as _bpy
        return _bpy.data.collections.get("GeoMap") is not None

    def draw(self, _context):
        import bpy as _bpy
        layout = self.layout
        root = _bpy.data.collections.get("GeoMap")
        if root is None:
            return
        for child in root.children:
            row = layout.row(align=True)
            row.label(text=child.name)
            row.prop(child, "hide_viewport", text="", emboss=False)
            row.prop(child, "hide_render", text="", emboss=False)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _draw_provider_quality(layout, provider: str) -> None:
    info = provider_quality(provider)
    if not info:
        return
    box = layout.box()
    box.scale_y = 0.75
    box.label(text=info.get("label", provider))
    for key in ("quality", "coverage", "notes"):
        text = info.get(key)
        if text:
            box.label(text=text[:100])


# ---------------------------------------------------------------------------
# Kept for backwards compatibility / __init__.py reference
# Remove GeoMapUpdatePanel and GeoMapPresetsPanel references:
# presetsare now in GeoMapQualityPanel; update buttons are inline.
# These stubs prevent KeyError during registration if old .blend files
# reference the old idnames.
# ---------------------------------------------------------------------------

class GeoMapUpdatePanel(Panel):
    """Kept as no-op stub — per-layer buttons are now inline in each panel."""
    bl_label = "Layer Actions"
    bl_idname = "VIEW3D_PT_geomap_update"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        bbox_ok = _bbox_unchanged(context)
        layout.label(text="Quick access — all layers")
        for kind, label, icon in (
            ("TERRAIN",     "Terrain (DEM + Imagery)", "MESH_GRID"),
            ("DEM",         "DEM only",   "MESH_GRID"),
            ("IMAGERY",     "Imagery only","IMAGE_DATA"),
            ("COASTLINES",  "Coastlines", "CURVE_PATH"),
            ("RIVERS",      "Rivers",     "FORCE_CURVE"),
            ("ROADS",       "Roads",      "DRIVER_DISTANCE"),
            ("ADMIN",       "Admin",      "COMMUNITY"),
            ("LANDUSE",     "Land Use",   "TEXTURE"),
            ("BUILDINGS",   "Buildings",  "HOME"),
            ("CITIES",      "Cities/POI", "OUTLINER_OB_EMPTY"),
            ("WEATHER",     "Weather",    "OUTLINER_OB_LIGHT"),
            ("ANNOTATIONS", "Annotations","FONT_DATA"),
        ):
            row = layout.row()
            row.enabled = bbox_ok
            op = row.operator("geomap.update_layer", text=label, icon=icon)
            op.layer_kind = kind
        layout.separator()
        layout.operator("geomap.regenerate_from_cache", text="Regenerate from Cache", icon="FILE_REFRESH")


class GeoMapPresetsPanel(Panel):
    """Stub — presets moved into Quality & Presets panel."""
    bl_label = "Map Presets"
    bl_idname = "VIEW3D_PT_geomap_presets"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        layout = self.layout
        presets = load_presets()
        if not presets:
            layout.label(text="No presets — save one in Quality & Presets panel")
            return
        for preset in presets:
            name = preset.get("preset_name", "?")
            row = layout.row(align=True)
            op = row.operator("geomap.load_preset", text=name[:28], icon="PRESET")
            op.preset_name = name
            op2 = row.operator("geomap.delete_preset", text="", icon="TRASH")
            op2.preset_name = name
        layout.operator("geomap.save_preset", text="Save Current as Preset", icon="ADD")
