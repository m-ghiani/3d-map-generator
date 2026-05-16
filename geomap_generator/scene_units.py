import math
from dataclasses import dataclass

from .models import BoundingBox


@dataclass(frozen=True)
class SceneScale:
    unit_system: str
    unit_label: str
    scale_length: float
    extent_lat_km: float
    extent_lon_km: float
    km_per_bu: float
    dem_height_scale: float
    map_units: float
    map_unit: float
    object_scale: float

    @classmethod
    def from_scene(
        cls,
        scene,
        bbox: BoundingBox,
        detail_level: str,
        dem_height_scale: float,
    ) -> "SceneScale":
        unit_settings = scene.unit_settings
        scale_length = max(getattr(unit_settings, "scale_length", 1.0), 1e-9)
        unit_system = getattr(unit_settings, "system", "NONE")
        unit_label = {"METRIC": "m", "IMPERIAL": "ft", "NONE": "BU"}.get(unit_system, "BU")

        lat_mid = (bbox.min_lat + bbox.max_lat) / 2
        extent_lat_km = bbox.lat_span() * 111.32
        extent_lon_km = bbox.lon_span() * 111.32 * math.cos(math.radians(lat_mid))
        extent_km = max(extent_lat_km, extent_lon_km) or 1.0
        zoom = {"LOW": 0.5, "MEDIUM": 1.0, "HIGH": 1.5}.get(detail_level, 1.0)
        map_units = 10.0 * zoom
        km_per_bu = extent_km / map_units

        return cls(
            unit_system=unit_system,
            unit_label=unit_label,
            scale_length=scale_length,
            extent_lat_km=extent_lat_km,
            extent_lon_km=extent_lon_km,
            km_per_bu=km_per_bu,
            dem_height_scale=dem_height_scale,
            map_units=map_units,
            map_unit=map_units / 10.0,
            object_scale=max(0.08, min(1.0, 2.0 / km_per_bu)),
        )


def scaled_map_value(value: float, scene_scale: SceneScale | None) -> float:
    if scene_scale is None:
        return value
    return value * scene_scale.map_unit * scene_scale.object_scale
