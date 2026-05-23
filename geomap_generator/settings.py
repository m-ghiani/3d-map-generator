from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSettings:
    data_backend: str = "LOCAL"
    service_url: str = "http://127.0.0.1:8765"
    service_auto_start: bool = True
    service_port: int = 8765
    dem_provider: str = "AUTO"
    coast_provider: str = "AUTO"
    river_provider: str = "AUTO"
    road_provider: str = "AUTO"
    admin_provider: str = "AUTO"
    basemap_provider: str = "AUTO"
    maptiler_token: str = ""
    mapbox_token: str = ""
    google_token: str = ""
    sentinel_hub_token: str = ""
    planet_token: str = ""
    maxar_token: str = ""
    airbus_token: str = ""
    weather_provider: str = "AUTO"
    openweathermap_token: str = ""
    weatherapi_token: str = ""

    @classmethod
    def from_preferences(cls, prefs) -> "ProviderSettings":
        if prefs is None:
            return cls()
        from .token_security import decrypt_token

        return cls(
            data_backend=getattr(prefs, "data_backend", "LOCAL"),
            service_url=getattr(prefs, "service_url", "http://127.0.0.1:8765"),
            service_auto_start=getattr(prefs, "service_auto_start", True),
            service_port=getattr(prefs, "service_port", 8765),
            dem_provider=getattr(prefs, "dem_provider", "AUTO"),
            coast_provider=getattr(prefs, "coast_provider", "AUTO"),
            river_provider=getattr(prefs, "river_provider", "AUTO"),
            road_provider=getattr(prefs, "road_provider", "AUTO"),
            admin_provider=getattr(prefs, "admin_provider", "AUTO"),
            basemap_provider=getattr(prefs, "basemap_provider", "AUTO"),
            maptiler_token=decrypt_token(getattr(prefs, "maptiler_token_encrypted", ""), "maptiler_token_encrypted"),
            mapbox_token=decrypt_token(getattr(prefs, "mapbox_token_encrypted", ""), "mapbox_token_encrypted"),
            google_token=decrypt_token(getattr(prefs, "google_token_encrypted", ""), "google_token_encrypted"),
            sentinel_hub_token=decrypt_token(
                getattr(prefs, "sentinel_hub_token_encrypted", ""), "sentinel_hub_token_encrypted"
            ),
            planet_token=decrypt_token(getattr(prefs, "planet_token_encrypted", ""), "planet_token_encrypted"),
            maxar_token=decrypt_token(getattr(prefs, "maxar_token_encrypted", ""), "maxar_token_encrypted"),
            airbus_token=decrypt_token(getattr(prefs, "airbus_token_encrypted", ""), "airbus_token_encrypted"),
            weather_provider=getattr(prefs, "weather_provider", "AUTO"),
            openweathermap_token=decrypt_token(
                getattr(prefs, "openweathermap_token_encrypted", ""),
                "openweathermap_token_encrypted",
            ),
            weatherapi_token=decrypt_token(
                getattr(prefs, "weatherapi_token_encrypted", ""),
                "weatherapi_token_encrypted",
            ),
        )


@dataclass(frozen=True)
class GenerationSettings:
    input_mode: str
    country_region: str
    latitude: float
    longitude: float
    latitude2: float
    longitude2: float
    quality_preset: str
    output_preset: str
    detail_level: str
    import_coast: bool
    import_rivers: bool
    import_relief: bool
    dem_resolution: str
    dem_height_scale: float
    drape_vectors_on_dem: bool
    vector_z_offset: float
    road_width: float
    road_geometry: str
    river_width: float
    river_geometry: str
    boundary_width: float
    coast_width: float
    print_base_height: float
    add_legend: bool
    add_scale_bar: bool
    import_roads: bool
    import_admin: bool
    import_buildings: bool
    import_cities: bool
    import_place_labels: bool
    place_label_min_type: str
    place_label_size_factor: float
    import_poi_historic: bool
    import_poi_cultural: bool
    import_poi_administrative: bool
    import_poi_natural: bool
    admin_level: str
    import_satellite: bool
    map_style: str
    satellite_resolution: int
    create_map_box: bool
    map_box_depth: float
    height_exaggeration: float
    auto_lod: bool
    import_landuse: bool = False
    import_contours: bool = False
    contour_interval_m: float = 50.0
    dem_slope_colors: bool = False
    add_north_arrow: bool = False
    import_weather: bool = False
    weather_show_temperature: bool = True
    weather_show_wind: bool = True
    weather_grid_size: int = 3

    @classmethod
    def from_props(cls, props) -> "GenerationSettings":
        values = {name: getattr(props, name) for name in cls.__dataclass_fields__}
        values.update(_quality_preset_values(values))
        values.update(_output_preset_values(values))
        return cls(**values)


def apply_quality_preset_to_props(props) -> None:
    values = _quality_preset_values(
        {
            "quality_preset": props.quality_preset,
            "detail_level": props.detail_level,
            "dem_resolution": props.dem_resolution,
            "satellite_resolution": props.satellite_resolution,
        }
    )
    for key, value in values.items():
        setattr(props, key, value)


def apply_output_preset_to_props(props) -> None:
    values = _output_preset_values({"output_preset": props.output_preset})
    for key, value in values.items():
        setattr(props, key, value)


def _quality_preset_values(values: dict) -> dict:
    presets = {
        "PREVIEW": {
            "detail_level": "LOW",
            "dem_resolution": "DEM_LOW",
            "satellite_resolution": 512,
        },
        "BALANCED": {
            "detail_level": "MEDIUM",
            "dem_resolution": "DEM_MEDIUM",
            "satellite_resolution": 2048,
        },
        "HIGH_QUALITY": {
            "detail_level": "HIGH",
            "dem_resolution": "DEM_HIGH",
            "satellite_resolution": 4096,
        },
        "LARGE_AREA": {
            "detail_level": "LOW",
            "dem_resolution": "DEM_LOW",
            "satellite_resolution": 1024,
        },
    }
    return presets.get(values.get("quality_preset"), {})


def _output_preset_values(values: dict) -> dict:
    presets = {
        "BLENDER_VIEW": {
            "road_width": 0.045,
            "river_width": 0.060,
            "boundary_width": 0.025,
            "coast_width": 0.035,
            "vector_z_offset": 0.030,
            "print_base_height": 0.250,
        },
        "RENDER": {
            "road_width": 0.070,
            "river_width": 0.090,
            "boundary_width": 0.035,
            "coast_width": 0.050,
            "vector_z_offset": 0.040,
            "print_base_height": 0.250,
        },
        "PRINT_3D": {
            "road_width": 0.090,
            "river_width": 0.110,
            "boundary_width": 0.055,
            "coast_width": 0.070,
            "vector_z_offset": 0.060,
            "print_base_height": 0.350,
        },
        "GAME_ENGINE": {
            "road_width": 0.035,
            "river_width": 0.045,
            "boundary_width": 0.018,
            "coast_width": 0.025,
            "vector_z_offset": 0.020,
            "print_base_height": 0.0,
        },
    }
    return presets.get(values.get("output_preset"), {})
