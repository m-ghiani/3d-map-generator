from dataclasses import dataclass

from .mesh_builder import river_width_factor, road_width_factor

LAYER_COLORS = {
    "coastlines": (0.05, 0.05, 0.05, 1.0),
    "rivers": (0.1, 0.35, 0.9, 1.0),
    "roads": (0.85, 0.25, 0.1, 1.0),
    "admin": (0.0, 0.0, 0.0, 1.0),
    "kmz": (0.95, 0.55, 0.05, 1.0),
    "other": (0.45, 0.45, 0.45, 1.0),
    "poi_city": (1.0, 0.85, 0.1, 1.0),
    "poi_historic": (0.65, 0.35, 0.1, 1.0),
    "poi_cultural": (0.55, 0.15, 0.8, 1.0),
    "poi_administrative": (0.1, 0.6, 0.25, 1.0),
    "poi_natural": (0.15, 0.7, 0.15, 1.0),
    "poi_kmz": (0.95, 0.55, 0.05, 1.0),
    # Land use fills
    "landuse_forest": (0.08, 0.32, 0.08, 0.85),
    "landuse_wood": (0.08, 0.32, 0.08, 0.85),
    "landuse_park": (0.18, 0.55, 0.18, 0.80),
    "landuse_grass": (0.28, 0.60, 0.22, 0.75),
    "landuse_meadow": (0.28, 0.60, 0.22, 0.75),
    "landuse_water": (0.12, 0.42, 0.82, 0.90),
    "landuse_scrub": (0.35, 0.48, 0.22, 0.70),
    "landuse_wetland": (0.22, 0.45, 0.38, 0.75),
    "landuse_sand": (0.82, 0.76, 0.52, 0.80),
    "landuse_beach": (0.82, 0.76, 0.52, 0.80),
    "landuse_residential": (0.82, 0.78, 0.72, 0.50),
    "landuse_industrial": (0.72, 0.65, 0.55, 0.55),
    "landuse_commercial": (0.78, 0.70, 0.60, 0.55),
    "landuse_farmland": (0.75, 0.82, 0.52, 0.65),
    "landuse_cemetery": (0.52, 0.62, 0.48, 0.75),
    "landuse_other": (0.55, 0.58, 0.50, 0.60),
    # Contour lines
    "contours": (0.58, 0.42, 0.22, 1.0),
}


@dataclass(frozen=True)
class LayerStyle:
    layer_key: str
    object_name: str
    collection_name: str
    color: tuple[float, float, float, float]


def layer_style_for_tags(tags: dict[str, str]) -> LayerStyle:
    layer_key, object_name = vector_layer_identity(tags)
    return LayerStyle(
        layer_key=layer_key,
        object_name=object_name,
        collection_name=collection_name_for_layer(layer_key),
        color=color_for_layer(layer_key),
    )


def vector_layer_identity(tags: dict[str, str]) -> tuple[str, str]:
    if tags.get("source") == "kmz":
        return "kmz", "GeoMap_KMZ"
    if tags.get("natural") == "coastline":
        return "coastlines", "GeoMap_Coastlines"
    if tags.get("waterway"):
        subtype = label_token(tags["waterway"])
        return f"rivers_{subtype.lower()}", f"GeoMap_Rivers_{subtype}"
    if tags.get("highway"):
        subtype = label_token(tags["highway"])
        return f"roads_{subtype.lower()}", f"GeoMap_Roads_{subtype}"
    if tags.get("boundary") == "administrative":
        level = label_token(tags.get("admin_level", "unknown"))
        return f"admin_level_{level.lower()}", f"GeoMap_Admin_Level_{level}"
    landuse_key = _landuse_key(tags)
    if landuse_key:
        return landuse_key, f"GeoMap_{label_token(landuse_key)}"
    return "other", "GeoMap_Other"


def _landuse_key(tags: dict[str, str]) -> str | None:
    lu = tags.get("landuse")
    nat = tags.get("natural")
    lei = tags.get("leisure")
    if lu in {"forest", "park", "grass", "meadow", "residential",
               "industrial", "commercial", "farmland", "cemetery"}:
        return f"landuse_{lu}"
    if nat in {"wood", "water", "scrub", "wetland", "sand", "beach"}:
        return f"landuse_{nat}"
    if lei == "park":
        return "landuse_park"
    return None


def label_token(value: str) -> str:
    token = "".join(char if char.isalnum() else "_" for char in str(value)).strip("_")
    return token.title() if token else "Unknown"


def collection_name_for_layer(layer_key: str) -> str:
    if layer_key.startswith("roads_"):
        return "Roads"
    if layer_key.startswith("rivers_"):
        return "Rivers"
    if layer_key.startswith("admin_"):
        return "Admin"
    if layer_key == "coastlines":
        return "Coastlines"
    if layer_key == "kmz":
        return "KMZ"
    if layer_key.startswith("landuse_"):
        return "LandUse"
    if layer_key == "contours":
        return "Contours"
    return "Other"


def color_for_layer(layer_key: str) -> tuple[float, float, float, float]:
    if layer_key.startswith("roads_"):
        return LAYER_COLORS["roads"]
    if layer_key.startswith("rivers_"):
        return LAYER_COLORS["rivers"]
    if layer_key.startswith("admin_"):
        return LAYER_COLORS["admin"]
    if layer_key.startswith("landuse_"):
        return LAYER_COLORS.get(layer_key, LAYER_COLORS["landuse_other"])
    return LAYER_COLORS.get(layer_key, LAYER_COLORS["other"])


def base_width_for_layer(settings, layer_key: str) -> float:
    if layer_key.startswith("roads_"):
        return settings.road_width
    if layer_key.startswith("rivers_"):
        return settings.river_width
    if layer_key.startswith("admin_"):
        return settings.boundary_width
    if layer_key == "coastlines":
        return settings.coast_width
    if layer_key == "kmz":
        return max(settings.boundary_width, 0.025)
    return max(settings.boundary_width, 0.0)


def width_for_way(settings, layer_key: str, tags: dict[str, str]) -> float:
    base_width = base_width_for_layer(settings, layer_key)
    if layer_key.startswith("roads_"):
        return base_width * road_width_factor(tags)
    if layer_key.startswith("rivers_"):
        return base_width * river_width_factor(tags)
    return base_width


_LAYER_Z_OFFSETS: dict[str, float] = {
    "coastlines": 0.001,
    "rivers_ditch": 0.002,
    "rivers_drain": 0.002,
    "rivers_stream": 0.002,
    "rivers_canal": 0.003,
    "rivers_river": 0.004,
    "roads_path": 0.005,
    "roads_track": 0.005,
    "roads_service": 0.006,
    "roads_residential": 0.006,
    "roads_unclassified": 0.006,
    "roads_tertiary": 0.007,
    "roads_secondary": 0.008,
    "roads_primary": 0.009,
    "roads_trunk": 0.010,
    "roads_motorway": 0.011,
}


def z_offset_for_layer(layer_key: str) -> float:
    if layer_key in _LAYER_Z_OFFSETS:
        return _LAYER_Z_OFFSETS[layer_key]
    if layer_key.startswith("roads_"):
        return 0.007
    if layer_key.startswith("rivers_"):
        return 0.003
    if layer_key.startswith("admin_"):
        return 0.012
    if layer_key.startswith("landuse_"):
        return -0.002
    return 0.004


def legend_label(layer_key: str) -> str:
    if layer_key == "coastlines":
        return "Coastlines"
    if layer_key.startswith("roads_"):
        return f"Roads {layer_key.removeprefix('roads_').replace('_', ' ').title()}"
    if layer_key.startswith("rivers_"):
        return f"Rivers {layer_key.removeprefix('rivers_').replace('_', ' ').title()}"
    if layer_key.startswith("admin_"):
        return f"Admin {layer_key.removeprefix('admin_').replace('_', ' ').title()}"
    if layer_key == "kmz":
        return "KMZ"
    return layer_key.replace("_", " ").title()
