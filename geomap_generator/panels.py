from bpy.types import Panel

from .download_cache import cache_stats
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
        props = context.scene.geomap_props

        box = layout.box()
        box.label(text="Input")
        box.row().prop(props, "input_mode", expand=True)

        if props.input_mode == "COUNTRY":
            box.prop(props, "country_region")
        else:
            box.label(text="Point A")
            col = box.column()
            col.prop(props, "latitude")
            col.prop(props, "longitude")
            box.label(text="Point B")
            col.prop(props, "latitude2")
            col.prop(props, "longitude2")

        quality_box = layout.box()
        quality_box.label(text="Quality")
        quality_box.prop(props, "quality_preset")
        quality_box.prop(props, "output_preset")
        quality_box.prop(props, "detail_level")

        vector_box = layout.box()
        vector_box.label(text="Vector Layers")
        col = vector_box.column(align=True)
        col.prop(props, "import_coast")
        col.prop(props, "import_rivers")
        col.prop(props, "import_roads")
        col.prop(props, "import_admin")
        if props.import_admin:
            vector_box.prop(props, "admin_level")

        poi_box = layout.box()
        poi_box.label(text="Points of Interest")
        col = poi_box.column(align=True)
        col.prop(props, "import_cities")
        col.prop(props, "import_poi_historic")
        col.prop(props, "import_poi_cultural")
        col.prop(props, "import_poi_administrative")
        col.prop(props, "import_poi_natural")

        terrain_box = layout.box()
        terrain_box.label(text="Terrain and Map")
        col = terrain_box.column(align=True)
        col.prop(props, "import_relief")
        if props.import_relief:
            terrain_box.prop(props, "dem_resolution")
            terrain_box.prop(props, "dem_height_scale")
            terrain_box.prop(props, "drape_vectors_on_dem")
            terrain_box.prop(props, "print_base_height")
        col.prop(props, "import_satellite")
        if props.import_satellite:
            terrain_box.prop(props, "map_style")
            terrain_box.prop(props, "satellite_resolution")

        output_box = layout.box()
        output_box.label(text="Output Geometry")
        output_box.prop(props, "vector_z_offset")
        output_box.prop(props, "road_geometry")
        output_box.prop(props, "road_width")
        output_box.prop(props, "river_geometry")
        output_box.prop(props, "river_width")
        output_box.prop(props, "boundary_width")
        output_box.prop(props, "coast_width")
        row = output_box.row(align=True)
        row.prop(props, "add_legend")
        row.prop(props, "add_scale_bar")

        layout.operator("geomap.generate", text="Generate Map")
        layout.operator("geomap.import_selected_poi_3d", text="Import Selected POI 3D")


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
