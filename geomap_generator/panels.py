from bpy.types import Panel

from .kmz import catalog_paths
from .search_cache import load_presets


# ---------------------------------------------------------------------------
# Root panel — dashboard launcher only
# ---------------------------------------------------------------------------

class GeoMapPanel(Panel):
    bl_label = "GeoMap Generator"
    bl_idname = "VIEW3D_PT_geomap_generator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, _context):
        layout = self.layout
        row = layout.row()
        row.scale_y = 2.2
        row.operator("geomap.open_dashboard", text="Open Dashboard", icon="WINDOW")


# ---------------------------------------------------------------------------
# Annotations  (not in dashboard — specialty post-gen overlay controls)
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

        col = layout.column(align=True)
        col.prop(props, "add_legend")
        col.prop(props, "add_scale_bar")
        col.prop(props, "add_north_arrow")

        layout.separator()
        layout.operator(
            "geomap.update_layer",
            text="Generate Annotations",
            icon="FONT_DATA",
        ).layer_kind = "ANNOTATIONS"


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
# Presets  (standalone — shortcut to preset list without opening Quality panel)
# ---------------------------------------------------------------------------

class GeoMapPresetsPanel(Panel):
    """Standalone preset access panel."""
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
