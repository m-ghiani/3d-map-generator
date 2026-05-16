from bpy.types import Panel

from .download_cache import cache_stats
from .persistent_log import log_path
from .provider_help import provider_quality
from .progress import ProgressTracker
from .search_cache import load_history


class GeoMapPanel(Panel):
    bl_label = "GeoMap Generator"
    bl_idname = "VIEW3D_PT_geomap_generator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, context):
        layout = self.layout
        layout.operator("geomap.generate", text="Generate Map")
        layout.operator("geomap.import_selected_poi_3d", text="Import Selected POI 3D")


class GeoMapInputPanel(Panel):
    bl_label = "Input"
    bl_idname = "VIEW3D_PT_geomap_input"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        layout = self.layout
        props = context.scene.geomap_props

        layout.row().prop(props, "input_mode", expand=True)

        if props.input_mode == "COUNTRY":
            layout.prop(props, "country_region")
        else:
            layout.label(text="Point A")
            col = layout.column()
            col.prop(props, "latitude")
            col.prop(props, "longitude")
            layout.label(text="Point B")
            col.prop(props, "latitude2")
            col.prop(props, "longitude2")


class GeoMapQualityPanel(Panel):
    bl_label = "Quality"
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
        layout.prop(props, "output_preset")
        layout.prop(props, "detail_level")


class GeoMapVectorPanel(Panel):
    bl_label = "Vector Layers"
    bl_idname = "VIEW3D_PT_geomap_vectors"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        col = layout.column(align=True)
        col.prop(props, "import_coast")
        col.prop(props, "import_rivers")
        col.prop(props, "import_roads")
        col.prop(props, "import_admin")
        col.prop(props, "import_buildings")
        if props.import_admin:
            layout.prop(props, "admin_level")


class GeoMapPoiPanel(Panel):
    bl_label = "Points of Interest"
    bl_idname = "VIEW3D_PT_geomap_poi"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.geomap_props
        col = self.layout.column(align=True)
        col.prop(props, "import_cities")
        col.prop(props, "import_poi_historic")
        col.prop(props, "import_poi_cultural")
        col.prop(props, "import_poi_administrative")
        col.prop(props, "import_poi_natural")


class GeoMapTerrainPanel(Panel):
    bl_label = "Terrain and Map"
    bl_idname = "VIEW3D_PT_geomap_terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"

    def draw(self, context):
        props = context.scene.geomap_props
        layout = self.layout
        prefs = self._addon_preferences(context)
        col = layout.column(align=True)
        col.prop(props, "import_relief")
        if props.import_relief:
            layout.prop(props, "dem_resolution")
            layout.prop(props, "dem_height_scale", slider=True)
            layout.prop(props, "drape_vectors_on_dem")
            layout.prop(props, "print_base_height", slider=True)
        col.prop(props, "import_satellite")
        if props.import_satellite:
            layout.prop(props, "map_style")
            layout.prop(props, "satellite_resolution")
            provider = getattr(prefs, "basemap_provider", "AUTO") if prefs else "AUTO"
            self._draw_provider_quality(layout, provider)

    @staticmethod
    def _addon_preferences(context):
        package = __package__.split(".")[0] if __package__ else "geomap_generator"
        for key in (__package__, package, "geomap_generator", "3d-map-generator"):
            if key and key in context.preferences.addons:
                return context.preferences.addons[key].preferences
        return None

    @staticmethod
    def _draw_provider_quality(layout, provider: str) -> None:
        info = provider_quality(provider)
        if not info:
            return
        box = layout.box()
        box.label(text="Source Imagery Quality")
        box.label(text=info.get("label", provider))
        for key in ("quality", "coverage", "notes"):
            text = info.get(key)
            if text:
                box.label(text=text[:120])


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
        layout.prop(props, "vector_z_offset", slider=True)
        layout.prop(props, "road_geometry")
        layout.prop(props, "road_width", slider=True)
        layout.prop(props, "river_geometry")
        layout.prop(props, "river_width", slider=True)
        layout.prop(props, "boundary_width", slider=True)
        layout.prop(props, "coast_width", slider=True)
        row = layout.row(align=True)
        row.prop(props, "add_legend")
        row.prop(props, "add_scale_bar")


class GeoMapUpdatePanel(Panel):
    bl_label = "Update Existing Layer"
    bl_idname = "VIEW3D_PT_geomap_update"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"
    bl_parent_id = "VIEW3D_PT_geomap_generator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        row = self.layout.row(align=True)
        op = row.operator("geomap.update_layer", text="Imagery")
        op.layer_kind = "IMAGERY"
        op = row.operator("geomap.update_layer", text="DEM")
        op.layer_kind = "DEM"
        op = row.operator("geomap.update_layer", text="Vectors")
        op.layer_kind = "VECTORS"


class GeoMapProgressPanel(Panel):
    bl_label = "Generation Progress"
    bl_idname = "VIEW3D_PT_geomap_progress"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, context):
        layout = self.layout
        tracker = ProgressTracker.get_instance()

        if not tracker.is_running and tracker.status == "Idle":
            layout.label(text="No generation in progress")
            return

        box = layout.box()
        box.label(text=f"Status: {tracker.status}", icon="INFO")

        layout.row().label(text=f"Progress: {tracker.progress * 100:.0f}%")
        col = layout.column()
        col.scale_y = 0.8
        split = col.split(factor=max(0.01, tracker.progress))
        split.box().label(text="")
        split.label(text="")

        if tracker.error:
            err_box = layout.box()
            err_box.label(text="ERROR:", icon="ERROR")
            for line in tracker.error.split("\n"):
                if line.strip():
                    err_box.label(text=line[:60])

        log_box = layout.box()
        log_box.label(text="Logs:", icon="TEXT")
        for msg in tracker.logs[-12:]:
            icon = "CHECKMARK" if "✓" in msg else ("CANCEL" if "✗" in msg else "CONSOLE")
            log_box.label(text=msg[:70], icon=icon)

        stats = cache_stats()
        debug_box = layout.box()
        debug_box.label(text="Download Debug", icon="NETWORK_DRIVE")
        debug_box.label(
            text=(
                f"Cache hit {stats['hits']} | miss {stats['misses']} | "
                f"offline {stats['stale_hits']}"
            )
        )
        debug_box.label(text=f"Cache files {stats['files']} | {stats['bytes'] / (1024 * 1024):.1f} MB")
        path = log_path()
        if path:
            debug_box.label(text=f"Log: {path}")

        if tracker.is_running:
            row = layout.row()
            row.scale_y = 1.2
            row.operator("geomap.cancel_generation", text="● ABORT", icon="CANCEL")


class GeoMapSearchHistoryPanel(Panel):
    bl_label = "Search History"
    bl_idname = "VIEW3D_PT_geomap_search_history"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, _context):
        layout = self.layout
        history = load_history()

        if not history:
            layout.label(text="No cached searches")
            return

        for index, item in enumerate(history[:10]):
            row = layout.row(align=True)
            label = item.get("label") or "Untitled search"
            op = row.operator("geomap.load_history", text=label[:32], icon="TIME")
            op.index = index

        layout.operator("geomap.clear_history", text="Clear History", icon="TRASH")
