from dataclasses import dataclass

from .mesh_builder import river_width_factor, road_width_factor

LAYER_COLORS = {
    "coastlines": (0.05, 0.05, 0.05, 1.0),
    "rivers": (0.1, 0.35, 0.9, 1.0),
    "roads": (0.85, 0.25, 0.1, 1.0),
    "admin": (0.0, 0.0, 0.0, 1.0),
    "other": (0.45, 0.45, 0.45, 1.0),
    "poi_city": (1.0, 0.85, 0.1, 1.0),
    "poi_historic": (0.65, 0.35, 0.1, 1.0),
    "poi_cultural": (0.55, 0.15, 0.8, 1.0),
    "poi_administrative": (0.1, 0.6, 0.25, 1.0),
    "poi_natural": (0.15, 0.7, 0.15, 1.0),
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
    return "other", "GeoMap_Other"


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
    return "Other"


def color_for_layer(layer_key: str) -> tuple[float, float, float, float]:
    if layer_key.startswith("roads_"):
        return LAYER_COLORS["roads"]
    if layer_key.startswith("rivers_"):
        return LAYER_COLORS["rivers"]
    if layer_key.startswith("admin_"):
        return LAYER_COLORS["admin"]
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
    return max(settings.boundary_width, 0.0)


def width_for_way(settings, layer_key: str, tags: dict[str, str]) -> float:
    base_width = base_width_for_layer(settings, layer_key)
    if layer_key.startswith("roads_"):
        return base_width * road_width_factor(tags)
    if layer_key.startswith("rivers_"):
        return base_width * river_width_factor(tags)
    return base_width


def legend_label(layer_key: str) -> str:
    if layer_key == "coastlines":
        return "Coastlines"
    if layer_key.startswith("roads_"):
        return f"Roads {layer_key.removeprefix('roads_').replace('_', ' ').title()}"
    if layer_key.startswith("rivers_"):
        return f"Rivers {layer_key.removeprefix('rivers_').replace('_', ' ').title()}"
    if layer_key.startswith("admin_"):
        return f"Admin {layer_key.removeprefix('admin_').replace('_', ' ').title()}"
    return layer_key.replace("_", " ").title()
