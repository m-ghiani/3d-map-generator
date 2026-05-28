from bpy.types import Panel


# ---------------------------------------------------------------------------
# T-panel (left toolbar) — dashboard launcher with globe icon
# ---------------------------------------------------------------------------

class GeoMapToolbarPanel(Panel):
    bl_label = "GeoMap"
    bl_idname = "VIEW3D_PT_geomap_toolbar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "TOOLS"

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = False
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 1.15
        col.operator("geomap.open_dashboard", text="Dashboard", icon="WORLD")
        col.separator(factor=0.35)
        col.operator("geomap.update_addon", text="Reload", icon="FILE_REFRESH")


# ---------------------------------------------------------------------------
# Root N-panel — only dashboard/update entry points remain here.
# ---------------------------------------------------------------------------

class GeoMapPanel(Panel):
    bl_label = "GeoMap Generator"
    bl_idname = "VIEW3D_PT_geomap_generator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GeoMap"

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 1.12
        col.operator("geomap.open_dashboard", text="Open Dashboard", icon="WORLD")
        col.operator("geomap.update_addon", text="Update / Reload Addon", icon="FILE_REFRESH")
