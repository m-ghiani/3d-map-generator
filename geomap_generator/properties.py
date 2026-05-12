import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

_OVERPASS_PROVIDER_ITEMS = [
    ("AUTO", "Auto", "Try the best available Overpass endpoint with fallback"),
    ("OVERPASS_MAIN", "Overpass Main", "overpass-api.de"),
    ("OVERPASS_PRIVATE_COFFEE", "Private.coffee", "overpass.private.coffee"),
    ("OVERPASS_MAPRVA", "MapRVA", "overpass.maprva.org"),
]


_DEM_PROVIDER_ITEMS = [
    ("AUTO", "Auto Fallback", "Try Open-Meteo, then Open Topo Data SRTM"),
    ("OPEN_METEO", "Open-Meteo Copernicus 90m", "Open-Meteo elevation API"),
    ("OPEN_TOPO_SRTM90", "Open Topo Data SRTM 90m", "Open Topo Data public SRTM 90m API"),
    ("OPEN_TOPO_SRTM30", "Open Topo Data SRTM 30m", "Open Topo Data public SRTM 30m API"),
    ("OPEN_TOPO_ASTER30", "Open Topo Data ASTER 30m (NASA)", "ASTER GDEM 30m via Open Topo Data"),
]

_BASEMAP_PROVIDER_ITEMS = [
    ("AUTO", "Auto", "Use the best available basemap imagery provider"),
    ("ARCGIS", "ArcGIS / Esri", "ArcGIS Online export services"),
]


def _addon_preferences_id() -> str:
    package = __package__ or "geomap_generator"
    return package.split(".", 1)[0]


class GeoMapAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _addon_preferences_id()

    dem_provider: EnumProperty(
        name="DEM Provider",
        items=_DEM_PROVIDER_ITEMS,
        default="AUTO",
    )
    coast_provider: EnumProperty(
        name="Coastline Provider",
        items=_OVERPASS_PROVIDER_ITEMS,
        default="AUTO",
    )
    river_provider: EnumProperty(
        name="River Provider",
        items=_OVERPASS_PROVIDER_ITEMS,
        default="AUTO",
    )
    road_provider: EnumProperty(
        name="Road Provider",
        items=_OVERPASS_PROVIDER_ITEMS,
        default="AUTO",
    )
    admin_provider: EnumProperty(
        name="Administrative Boundary Provider",
        items=_OVERPASS_PROVIDER_ITEMS,
        default="AUTO",
    )
    basemap_provider: EnumProperty(
        name="Map Imagery Provider",
        items=_BASEMAP_PROVIDER_ITEMS,
        default="AUTO",
    )

    def draw(self, context):
        from .download_cache import cache_stats

        layout = self.layout
        box = layout.box()
        box.label(text="Data Providers")
        box.prop(self, "dem_provider")
        box.prop(self, "basemap_provider")
        box.prop(self, "coast_provider")
        box.prop(self, "river_provider")
        box.prop(self, "road_provider")
        box.prop(self, "admin_provider")

        stats = cache_stats()
        cache_box = layout.box()
        cache_box.label(text="Download Cache")
        cache_box.label(
            text=(
                f"{stats['files']} files, "
                f"{stats['bytes'] / (1024 * 1024):.1f} MB"
            )
        )
        row = cache_box.row(align=True)
        row.operator("geomap.clear_download_cache", text="Clear All").namespace = ""
        row.operator("geomap.clear_download_cache", text="Clear DEM").namespace = "dem"
        row.operator("geomap.clear_download_cache", text="Clear OSM").namespace = "overpass"
        row.operator("geomap.clear_download_cache", text="Clear Maps").namespace = "imagery"


class GeoMapProperties(bpy.types.PropertyGroup):
    input_mode: EnumProperty(
        name="Input Mode",
        items=[
            ("COUNTRY", "Place/Area", "Search any place, landmark, city, region, or country"),
            ("COORDS", "Coordinates", "Enter two geographic corner points"),
        ],
        default="COUNTRY",
    )
    country_region: StringProperty(
        name="Place/Area",
        description="Search query, e.g. a landmark, park, neighborhood, city, region, or country",
        default="",
    )
    latitude: FloatProperty(name="Latitude A", default=0.0, min=-90.0, max=90.0)
    longitude: FloatProperty(name="Longitude A", default=0.0, min=-180.0, max=180.0)
    latitude2: FloatProperty(name="Latitude B", default=0.0, min=-90.0, max=90.0)
    longitude2: FloatProperty(name="Longitude B", default=0.0, min=-180.0, max=180.0)
    quality_preset: EnumProperty(
        name="Quality Preset",
        items=[
            ("CUSTOM", "Custom", "Use the values configured below"),
            ("PREVIEW", "Preview", "Fast preview with low detail and small textures"),
            ("BALANCED", "Balanced", "Balanced detail, texture size, and request volume"),
            ("HIGH_QUALITY", "High Quality", "Higher terrain and texture quality"),
            ("LARGE_AREA", "Large Area", "Conservative settings for large bounding boxes"),
        ],
        default="CUSTOM",
    )
    output_preset: EnumProperty(
        name="Output Preset",
        items=[
            ("CUSTOM", "Custom", "Use custom output controls"),
            ("BLENDER_VIEW", "Blender View", "Balanced geometry for viewport inspection"),
            ("RENDER", "Render", "Wider visible layers for rendered maps"),
            ("PRINT_3D", "3D Print", "Closed terrain base and printable raised layers"),
            ("GAME_ENGINE", "Game Engine", "Lower-profile geometry for export"),
        ],
        default="BLENDER_VIEW",
    )
    detail_level: EnumProperty(
        name="Detail Level",
        items=[
            ("LOW", "Low", "Basic features, faster generation"),
            ("MEDIUM", "Medium", "Balanced detail and performance"),
            ("HIGH", "High", "High detail, slower generation"),
        ],
        default="MEDIUM",
    )
    import_coast: BoolProperty(name="Coastlines", default=True)
    import_rivers: BoolProperty(name="Rivers", default=False)
    import_relief: BoolProperty(name="Relief (DEM)", default=False)
    dem_resolution: EnumProperty(
        name="DEM Resolution",
        items=[
            ("DEM_LOW", "Low 16×16", "Fast terrain sampling with low request volume"),
            ("DEM_MEDIUM", "Medium 32×32", "Balanced terrain definition and request pacing"),
            ("DEM_HIGH", "High 48×48", "Higher terrain definition, slower download"),
            ("DEM_ULTRA", "Ultra 64×64", "Maximum terrain definition, slowest and most rate-limit sensitive"),
        ],
        default="DEM_MEDIUM",
    )
    dem_height_scale: FloatProperty(
        name="DEM Height Scale",
        default=0.002,
        min=0.0001,
        max=0.05,
        precision=4,
        description="Blender units per meter of elevation",
    )
    drape_vectors_on_dem: BoolProperty(
        name="Drape Vectors on DEM",
        default=True,
        description="Place roads, rivers, boundaries, and POI on the sampled terrain height",
    )
    vector_z_offset: FloatProperty(
        name="Layer Z Offset",
        default=0.03,
        min=0.0,
        max=1.0,
        precision=3,
        description="Vertical offset above terrain or map plane in Blender units",
    )
    road_width: FloatProperty(
        name="Road Width",
        default=0.045,
        min=0.0,
        max=1.0,
        precision=3,
        description="Generated road ribbon width in Blender units",
    )
    road_geometry: EnumProperty(
        name="Road Geometry",
        items=[
            ("CURVE", "Curve", "Create roads as one Blender curve per road layer"),
            ("MESH", "Mesh", "Create roads as ribbon mesh faces"),
        ],
        default="CURVE",
    )
    river_width: FloatProperty(
        name="River Width",
        default=0.06,
        min=0.0,
        max=1.0,
        precision=3,
        description="Generated river ribbon width in Blender units",
    )
    river_geometry: EnumProperty(
        name="River Geometry",
        items=[
            ("CURVE", "Curve", "Create rivers as one Blender curve per river layer"),
            ("MESH", "Mesh", "Create rivers as ribbon mesh faces"),
        ],
        default="CURVE",
    )
    boundary_width: FloatProperty(
        name="Boundary Width",
        default=0.025,
        min=0.0,
        max=1.0,
        precision=3,
        description="Generated administrative boundary ribbon width in Blender units",
    )
    coast_width: FloatProperty(
        name="Coast Width",
        default=0.035,
        min=0.0,
        max=1.0,
        precision=3,
        description="Generated coastline ribbon width in Blender units",
    )
    print_base_height: FloatProperty(
        name="Print Base Height",
        default=0.25,
        min=0.0,
        max=5.0,
        precision=3,
        description="Thickness of the closed terrain base for 3D printing",
    )
    add_legend: BoolProperty(name="Legend", default=True)
    add_scale_bar: BoolProperty(name="Scale Bar", default=True)
    import_roads: BoolProperty(name="Main Roads", default=False)
    import_admin: BoolProperty(name="Administrative Boundaries", default=False)
    import_cities: BoolProperty(name="Cities", default=False)
    import_poi_historic: BoolProperty(name="Historic POI", default=False)
    import_poi_cultural: BoolProperty(name="Cultural POI", default=False)
    import_poi_administrative: BoolProperty(name="Administrative POI", default=False)
    import_poi_natural: BoolProperty(name="Natural POI", default=False)
    admin_level: EnumProperty(
        name="Admin Level",
        items=[
            ("2", "Country", "National borders, admin_level=2"),
            ("4", "Region/State", "Regions, states, or equivalent, admin_level=4"),
            ("6", "Province/County", "Provinces, counties, or equivalent, admin_level=6"),
            ("8", "City/Municipality", "Municipal boundaries, admin_level=8"),
            ("ALL", "All Common Levels", "Admin levels 2, 4, 6, and 8"),
        ],
        default="4",
    )
    import_satellite: BoolProperty(name="Satellite Bounding Box", default=False)
    map_style: EnumProperty(
        name="Map Type",
        items=[
            ("SATELLITE", "Satellite", "Esri World Imagery"),
            ("STREETS", "Street Map", "Esri World Street Map"),
            ("TOPO", "Topographic", "Esri World Topographic Map"),
            ("POLITICAL", "Political", "Esri National Geographic style political map"),
            ("LIGHT_GRAY", "Light Gray", "Esri Light Gray Canvas"),
            ("DARK_GRAY", "Dark Gray", "Esri Dark Gray Canvas"),
        ],
        default="SATELLITE",
    )
    satellite_resolution: IntProperty(
        name="Satellite Resolution",
        default=2048,
        min=256,
        max=8192,
        step=256,
        description="Pixel size for the satellite texture request, up to 8192",
    )
