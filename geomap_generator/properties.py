import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

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

_STYLE_ITEMS: dict[str, tuple] = {
    "SATELLITE": ("SATELLITE", "Satellite", "True color satellite imagery"),
    "STREETS":   ("STREETS",   "Street Map",   "Street and road basemap"),
    "TOPO":      ("TOPO",      "Topographic",  "Topographic map with elevation contours"),
    "POLITICAL": ("POLITICAL", "Political",    "National Geographic style political map"),
    "LIGHT_GRAY":("LIGHT_GRAY","Light Gray",   "Minimal light gray canvas"),
    "DARK_GRAY": ("DARK_GRAY", "Dark Gray",    "Minimal dark gray canvas"),
}

_PROVIDER_STYLE_KEYS: dict[str, list[str]] = {
    "AUTO":         ["SATELLITE", "STREETS", "TOPO", "POLITICAL", "LIGHT_GRAY", "DARK_GRAY"],
    "ARCGIS":       ["SATELLITE", "STREETS", "TOPO", "POLITICAL", "LIGHT_GRAY", "DARK_GRAY"],
    "MAPTILER":     ["SATELLITE", "STREETS", "TOPO", "LIGHT_GRAY", "DARK_GRAY"],
    "MAPBOX":       ["SATELLITE", "STREETS", "TOPO", "LIGHT_GRAY", "DARK_GRAY"],
    "GOOGLE":       ["SATELLITE", "STREETS", "TOPO"],
    "NASA_GIBS":    ["SATELLITE"],
    "SENTINEL_HUB": ["SATELLITE"],
    "PLANET":       ["SATELLITE"],
    "MAXAR":        ["SATELLITE"],
    "AIRBUS":       ["SATELLITE"],
}


def _map_style_items(self, context):
    pkg = _addon_preferences_id()
    prefs_addon = context.preferences.addons.get(pkg) if context else None
    provider = prefs_addon.preferences.basemap_provider if prefs_addon else "AUTO"
    keys = _PROVIDER_STYLE_KEYS.get(provider, _PROVIDER_STYLE_KEYS["AUTO"])
    return [_STYLE_ITEMS[k] for k in keys if k in _STYLE_ITEMS]


_BASEMAP_PROVIDER_ITEMS = [
    ("AUTO", "Auto", "Use the best available basemap imagery provider"),
    ("ARCGIS", "ArcGIS / Esri", "ArcGIS Online export services"),
    ("MAPTILER", "MapTiler Satellite", "MapTiler Static Maps API; requires API key"),
    ("MAPBOX", "Mapbox Satellite", "Mapbox Static Images API; requires access token"),
    ("GOOGLE", "Google Satellite", "Google Static Maps API; requires API key"),
    ("NASA_GIBS", "NASA GIBS", "NASA GIBS WMS imagery; no token required"),
    ("SENTINEL_HUB", "Sentinel Hub", "Sentinel Hub imagery; requires service configuration"),
    ("PLANET", "Planet", "Planet imagery; requires account/API integration"),
    ("MAXAR", "Maxar", "Maxar imagery; requires account/API integration"),
    ("AIRBUS", "Airbus", "Airbus imagery; requires account/API integration"),
]

_DATA_BACKEND_ITEMS = [
    ("LOCAL", "Local Blender Thread", "Fetch and preprocess data inside Blender"),
    ("EXTERNAL", "External Local Service", "Use geomap_service.py for data fetching and preprocessing"),
]

_WEATHER_PROVIDER_ITEMS = [
    ("AUTO", "Auto (Open-Meteo)", "Use Open-Meteo — free, no API key required"),
    ("OPEN_METEO", "Open-Meteo", "Free global weather data, no key required"),
    ("OPENWEATHERMAP", "OpenWeatherMap", "Requires API key"),
    ("WEATHERAPI", "WeatherAPI.com", "Requires API key"),
]


def _addon_preferences_id() -> str:
    package = __package__ or "geomap_generator"
    return package.split(".", 1)[0]


def _kmz_items(self, context):
    try:
        from .kmz import kmz_enum_items

        return kmz_enum_items(self, context)
    except Exception:
        return [("NONE", "No KMZ available", "KMZ catalog could not be loaded")]


class GeoMapAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _addon_preferences_id()

    data_backend: EnumProperty(
        name="Data Backend",
        items=_DATA_BACKEND_ITEMS,
        default="LOCAL",
    )
    service_url: StringProperty(
        name="Service URL",
        default="http://127.0.0.1:8765",
    )
    service_auto_start: BoolProperty(
        name="Auto-start Local Service",
        default=True,
    )
    service_port: IntProperty(
        name="Local Service Port",
        default=8765,
        min=1024,
        max=65535,
    )
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
    maptiler_token: StringProperty(
        name="MapTiler API Key",
        subtype="PASSWORD",
    )
    maptiler_token_encrypted: StringProperty(options={"HIDDEN"})
    mapbox_token: StringProperty(
        name="Mapbox Access Token",
        subtype="PASSWORD",
    )
    mapbox_token_encrypted: StringProperty(options={"HIDDEN"})
    google_token: StringProperty(
        name="Google API Key",
        subtype="PASSWORD",
    )
    google_token_encrypted: StringProperty(options={"HIDDEN"})
    sentinel_hub_token: StringProperty(
        name="Sentinel Hub Token",
        subtype="PASSWORD",
    )
    sentinel_hub_token_encrypted: StringProperty(options={"HIDDEN"})
    planet_token: StringProperty(
        name="Planet API Key",
        subtype="PASSWORD",
    )
    planet_token_encrypted: StringProperty(options={"HIDDEN"})
    maxar_token: StringProperty(
        name="Maxar Token",
        subtype="PASSWORD",
    )
    maxar_token_encrypted: StringProperty(options={"HIDDEN"})
    airbus_token: StringProperty(
        name="Airbus Token",
        subtype="PASSWORD",
    )
    airbus_token_encrypted: StringProperty(options={"HIDDEN"})

    weather_provider: EnumProperty(
        name="Weather Provider",
        items=_WEATHER_PROVIDER_ITEMS,
        default="AUTO",
    )
    openweathermap_token: StringProperty(
        name="OpenWeatherMap API Key",
        subtype="PASSWORD",
    )
    openweathermap_token_encrypted: StringProperty(options={"HIDDEN"})
    weatherapi_token: StringProperty(
        name="WeatherAPI Key",
        subtype="PASSWORD",
    )
    weatherapi_token_encrypted: StringProperty(options={"HIDDEN"})

    def draw(self, context):
        from .download_cache import cache_stats

        layout = self.layout
        service_box = layout.box()
        service_box.label(text="Generation Backend")
        service_box.prop(self, "data_backend")
        service_box.prop(self, "service_auto_start")
        service_box.prop(self, "service_port")
        service_box.prop(self, "service_url")

        box = layout.box()
        box.label(text="Data Providers")
        box.prop(self, "dem_provider")
        box.prop(self, "basemap_provider")
        self._draw_basemap_token(layout)
        box.prop(self, "coast_provider")
        box.prop(self, "river_provider")
        box.prop(self, "road_provider")
        box.prop(self, "admin_provider")

        weather_box = layout.box()
        weather_box.label(text="Weather")
        weather_box.prop(self, "weather_provider")
        if self.weather_provider == "OPENWEATHERMAP":
            weather_box.prop(self, "openweathermap_token")
            op = weather_box.operator("geomap.store_basemap_token", text="Store Encrypted")
            op.token_prop = "openweathermap_token"
            op.encrypted_prop = "openweathermap_token_encrypted"
        elif self.weather_provider == "WEATHERAPI":
            weather_box.prop(self, "weatherapi_token")
            op = weather_box.operator("geomap.store_basemap_token", text="Store Encrypted")
            op.token_prop = "weatherapi_token"
            op.encrypted_prop = "weatherapi_token_encrypted"

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

    def _draw_basemap_token(self, layout) -> None:
        provider = self.basemap_provider
        token_props = {
            "MAPTILER": ("maptiler_token", "maptiler_token_encrypted"),
            "MAPBOX": ("mapbox_token", "mapbox_token_encrypted"),
            "GOOGLE": ("google_token", "google_token_encrypted"),
            "SENTINEL_HUB": ("sentinel_hub_token", "sentinel_hub_token_encrypted"),
            "PLANET": ("planet_token", "planet_token_encrypted"),
            "MAXAR": ("maxar_token", "maxar_token_encrypted"),
            "AIRBUS": ("airbus_token", "airbus_token_encrypted"),
        }
        if provider not in token_props:
            return
        from .token_security import has_encrypted_token

        token_prop, encrypted_prop = token_props[provider]
        token_box = layout.box()
        token_box.label(text=f"{provider} Credentials")
        from .provider_help import token_help_lines

        for line in token_help_lines(provider):
            token_box.label(text=line)
        token_box.prop(self, token_prop)
        op = token_box.operator("geomap.store_basemap_token", text="Store Encrypted Token")
        op.token_prop = token_prop
        op.encrypted_prop = encrypted_prop
        stored = has_encrypted_token(getattr(self, encrypted_prop, ""))
        token_box.label(text="Stored encrypted token: yes" if stored else "Stored encrypted token: no")


class GeoMapRouteItem(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Route")
    lat1: FloatProperty(name="Start Lat", default=0.0, min=-90.0, max=90.0, precision=6)
    lon1: FloatProperty(name="Start Lon", default=0.0, min=-180.0, max=180.0, precision=6)
    lat2: FloatProperty(name="End Lat", default=0.0, min=-90.0, max=90.0, precision=6)
    lon2: FloatProperty(name="End Lon", default=0.0, min=-180.0, max=180.0, precision=6)
    mode: EnumProperty(
        name="Mode",
        items=[
            ("STRAIGHT", "As the crow flies", "Direct straight line"),
            ("ROUTE", "Road routing", "Follow road network via OSRM"),
        ],
        default="ROUTE",
    )
    profile: EnumProperty(
        name="Profile",
        items=[
            ("driving", "Driving", ""),
            ("walking", "Walking", ""),
            ("cycling", "Cycling", ""),
        ],
        default="driving",
    )
    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        default=(0.9, 0.15, 0.05, 1.0),
        min=0.0,
        max=1.0,
    )
    label_start: StringProperty(name="Start Label", default="")
    label_end: StringProperty(name="End Label", default="")


def _update_height_exaggeration(self, context):
    import bpy as _bpy
    new_exag = self.height_exaggeration
    old_exag = context.scene.get("geomap_last_height_exag", 1.0)
    ratio = new_exag / max(old_exag, 1e-6)

    for obj in _bpy.data.objects:
        layer = obj.get("geomap_layer")
        if not layer:
            continue
        if layer == "dem":
            obj.scale.z = new_exag
        elif obj.type == "EMPTY" and layer:
            # Shrinkwrap does not work on empties — scale Z location proportionally
            obj.location.z *= ratio

    context.scene["geomap_last_height_exag"] = new_exag


def _auto_lod_update(self, _context):
    import math
    if not getattr(self, "auto_lod", True):
        return
    la1, lo1 = self.latitude, self.longitude
    la2, lo2 = self.latitude2, self.longitude2
    if la1 == la2 == 0.0 and lo1 == lo2 == 0.0:
        return
    dlat = math.radians(abs(la2 - la1))
    dlon = math.radians(abs(lo2 - lo1))
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlon / 2) ** 2)
    dist_km = 2 * 6371 * math.asin(math.sqrt(max(0.0, min(1.0, a))))
    if dist_km < 20:
        self.quality_preset = "HIGH_QUALITY"
    elif dist_km < 100:
        self.quality_preset = "BALANCED"
    elif dist_km < 500:
        self.quality_preset = "PREVIEW"
    else:
        self.quality_preset = "LARGE_AREA"


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
    latitude: FloatProperty(name="Latitude A", default=0.0, min=-90.0, max=90.0, update=_auto_lod_update)
    longitude: FloatProperty(name="Longitude A", default=0.0, min=-180.0, max=180.0, update=_auto_lod_update)
    latitude2: FloatProperty(name="Latitude B", default=0.0, min=-90.0, max=90.0, update=_auto_lod_update)
    longitude2: FloatProperty(name="Longitude B", default=0.0, min=-180.0, max=180.0, update=_auto_lod_update)
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
        soft_min=0.0001,
        soft_max=0.01,
        step=1,
        precision=4,
        description="Blender units per meter of elevation",
    )
    create_map_box: BoolProperty(
        name="Create Map Box",
        default=False,
        description="Generate side walls and bottom plate below the map, like a physical scale model (plastico)",
    )
    map_box_depth: FloatProperty(
        name="Box Depth",
        default=0.30,
        min=0.01,
        max=5.0,
        soft_min=0.05,
        soft_max=2.0,
        step=1,
        precision=3,
        description="Depth of the box walls below the map plane (Blender units)",
    )
    height_exaggeration: FloatProperty(
        name="Height Exaggeration",
        default=1.0,
        min=0.1,
        max=20.0,
        soft_min=0.5,
        soft_max=10.0,
        step=10,
        precision=2,
        description="Vertical scale multiplier applied live to DEM terrain objects (1.0 = true scale)",
        update=_update_height_exaggeration,
    )
    auto_lod: BoolProperty(
        name="Auto Quality Preset",
        default=True,
        description="Automatically adjust quality preset based on bounding box size when coordinates change",
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
        soft_min=0.0,
        soft_max=0.12,
        step=1,
        precision=3,
        description="Vertical offset above terrain or map plane in Blender units",
    )
    road_width: FloatProperty(
        name="Road Width",
        default=0.045,
        min=0.0,
        max=1.0,
        soft_min=0.0,
        soft_max=0.18,
        step=1,
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
        soft_min=0.0,
        soft_max=0.22,
        step=1,
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
        soft_min=0.0,
        soft_max=0.12,
        step=1,
        precision=3,
        description="Generated administrative boundary ribbon width in Blender units",
    )
    coast_width: FloatProperty(
        name="Coast Width",
        default=0.035,
        min=0.0,
        max=1.0,
        soft_min=0.0,
        soft_max=0.14,
        step=1,
        precision=3,
        description="Generated coastline ribbon width in Blender units",
    )
    print_base_height: FloatProperty(
        name="Print Base Height",
        default=0.25,
        min=0.0,
        max=5.0,
        soft_min=0.0,
        soft_max=1.0,
        step=1,
        precision=3,
        description="Thickness of the closed terrain base for 3D printing",
    )
    add_legend: BoolProperty(name="Legend", default=True)
    add_scale_bar: BoolProperty(name="Scale Bar", default=True)
    import_roads: BoolProperty(name="Main Roads", default=False)
    import_admin: BoolProperty(name="Administrative Boundaries", default=False)
    import_buildings: BoolProperty(name="3D Buildings", default=False)
    import_cities: BoolProperty(name="Cities (markers)", default=False)
    import_place_labels: BoolProperty(
        name="Place Labels",
        default=False,
        description="Add 3D text labels for cities, towns, villages and hamlets",
    )
    place_label_min_type: EnumProperty(
        name="Show labels from",
        items=[
            ("capital", "Capitals only", "National and regional capitals"),
            ("city", "Cities+", "Capitals and cities"),
            ("town", "Towns+", "Cities and towns (default)"),
            ("village", "Villages+", "Towns and villages"),
            ("hamlet", "All places", "All named places including hamlets and suburbs"),
        ],
        default="town",
    )
    place_label_size_factor: FloatProperty(
        name="Label Size",
        default=1.0,
        min=0.1,
        max=5.0,
        step=10,
        description="Multiplier for place label font size",
    )
    import_poi_historic: BoolProperty(name="Historic POI", default=False)
    import_poi_cultural: BoolProperty(name="Cultural POI", default=False)
    import_poi_administrative: BoolProperty(name="Administrative POI", default=False)
    import_poi_natural: BoolProperty(name="Natural POI", default=False)
    kmz_selection: EnumProperty(
        name="Available KMZ",
        description="KMZ/KML entry from the local catalog intersecting the current area",
        items=_kmz_items,
    )
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
        items=_map_style_items,
    )
    satellite_resolution: IntProperty(
        name="Satellite Resolution",
        default=2048,
        min=256,
        max=8192,
        step=256,
        description="Pixel size for the satellite texture request, up to 8192",
    )
    route_mode: EnumProperty(
        name="Mode",
        items=[
            ("STRAIGHT", "As the crow flies", "Direct straight line between points"),
            ("ROUTE", "Road routing", "Follow road network via OSRM"),
        ],
        default="ROUTE",
    )
    route_profile: EnumProperty(
        name="Profile",
        items=[
            ("driving", "Driving", "Car routing on roads"),
            ("walking", "Walking", "Pedestrian routing"),
            ("cycling", "Cycling", "Cycling routing"),
        ],
        default="driving",
    )
    route_lat1: FloatProperty(
        name="Start Lat", default=0.0, min=-90.0, max=90.0, precision=6,
        description="Latitude of route start point",
    )
    route_lon1: FloatProperty(
        name="Start Lon", default=0.0, min=-180.0, max=180.0, precision=6,
        description="Longitude of route start point",
    )
    route_lat2: FloatProperty(
        name="End Lat", default=0.0, min=-90.0, max=90.0, precision=6,
        description="Latitude of route end point",
    )
    route_lon2: FloatProperty(
        name="End Lon", default=0.0, min=-180.0, max=180.0, precision=6,
        description="Longitude of route end point",
    )
    route_search_query: StringProperty(
        name="Search",
        default="",
        description="Place name to search within the current map area (Nominatim)",
    )
    route_label_start: StringProperty(
        name="Start Label",
        default="",
        description="Text label placed at route start point (empty = no label)",
    )
    route_label_end: StringProperty(
        name="End Label",
        default="",
        description="Text label placed at route end point (empty = no label)",
    )
    # Multi-route list
    routes: CollectionProperty(type=GeoMapRouteItem)
    route_active_index: IntProperty(name="Active Route", default=0, min=0)
    # Land use
    import_landuse: BoolProperty(
        name="Land Use (forest, parks, water…)",
        default=False,
        description="Import land use polygons: forests, parks, water bodies, residential areas",
    )
    # Contour lines
    import_contours: BoolProperty(
        name="Contour Lines",
        default=False,
        description="Generate elevation contour lines from the DEM grid (requires Relief DEM)",
    )
    contour_interval_m: FloatProperty(
        name="Contour Interval (m)",
        default=50.0,
        min=1.0,
        max=1000.0,
        soft_min=10.0,
        soft_max=500.0,
        step=100,
        precision=0,
        description="Elevation interval in metres between contour lines",
    )
    dem_slope_colors: BoolProperty(
        name="Slope Shading",
        default=False,
        description="Add vertex color shading to DEM terrain: green flats, grey slopes",
    )
    add_north_arrow: BoolProperty(
        name="North Arrow",
        default=False,
        description="Place a north arrow at the NE corner of the map",
    )
    import_weather: BoolProperty(
        name="Weather Layer",
        default=False,
        description="Fetch current weather and place flat icons on the map",
    )
    weather_show_temperature: BoolProperty(
        name="Temperature",
        default=True,
        description="Show temperature badge icons",
    )
    weather_show_wind: BoolProperty(
        name="Wind Arrows",
        default=True,
        description="Show wind direction arrows",
    )
    weather_grid_size: IntProperty(
        name="Grid Points",
        default=3,
        min=1,
        max=9,
        description="Number of weather sample points per axis (NxN grid)",
    )
    # Building quality / LOD
    building_quality: EnumProperty(
        name="Building Quality",
        items=[
            ("AUTO", "Auto (LOD by area)",
             "Detailed per-building for small areas, simplified batch for large areas"),
            ("SIMPLE", "Simple (merged mesh)",
             "Fast merged mesh, no individual materials"),
            ("DETAILED", "Detailed (per-building)",
             "Individual mesh per building with procedural OSM materials"),
        ],
        default="AUTO",
        description="Controls LOD and material quality for 3D buildings",
    )
