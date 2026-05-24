import math
import os
import re

import bpy

from .blender_scene import (
    create_quad_mesh_object,
    create_text_object,
    link_to_geomap_collection,
    material_named,
)
from .layer_style import legend_label
from .mesh_builder import BboxProjector
from .models import BoundingBox, OsmPoint
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread

_PLACE_RANK: dict[str, int] = {
    "capital": 0,
    "city": 1,
    "town": 2,
    "village": 3,
    "hamlet": 4,
}
_PLACE_BASE_SIZE: dict[str, float] = {
    "capital": 0.30,
    "city": 0.22,
    "town": 0.16,
    "village": 0.12,
    "hamlet": 0.09,
}
_PLACE_COLORS: dict[str, tuple[float, float, float, float]] = {
    "capital": (0.02, 0.02, 0.05, 1.0),
    "city": (0.05, 0.05, 0.12, 1.0),
    "town": (0.10, 0.10, 0.18, 1.0),
    "village": (0.18, 0.18, 0.25, 1.0),
    "hamlet": (0.25, 0.25, 0.32, 1.0),
}

_FONT_CANDIDATES: dict[str, list[str]] = {
    "SANS": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ],
    "SERIF": [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Times New Roman.ttf",
    ],
    "MONO": [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ],
}


class AnnotationRenderer:
    def render(
        self,
        context,
        bbox: BoundingBox,
        settings,
        vector_objects,
        scene_scale: SceneScale,
    ) -> None:
        assert_main_thread()
        self._annotate_root_collection(context, bbox, settings, scene_scale.km_per_bu)

        if getattr(settings, "add_north_arrow", False):
            self._create_north_arrow(context, bbox, settings.detail_level, scene_scale)
        if settings.add_scale_bar:
            self._create_scale_bar(context, bbox, settings.detail_level, scene_scale)
        if settings.add_legend:
            layer_keys = []
            for obj in vector_objects:
                layer_key = obj.get("geomap_layer")
                if layer_key and layer_key not in layer_keys:
                    layer_keys.append(layer_key)
            self._create_legend(context, bbox, settings.detail_level, layer_keys, scene_scale)

    @staticmethod
    def _annotate_root_collection(context, bbox: BoundingBox, settings, km_per_bu: float) -> None:
        root = bpy.data.collections.get("GeoMap")
        if not root:
            return
        root["geomap_bbox"] = bbox.to_overpass()
        root["geomap_detail_level"] = settings.detail_level
        root["geomap_output_preset"] = settings.output_preset
        root["geomap_km_per_bu"] = round(km_per_bu, 4)
        root["geomap_scene_unit"] = context.scene.unit_settings.system

    def _create_north_arrow(
        self,
        context,
        bbox: BoundingBox,
        detail_level: str,
        scene_scale: SceneScale,
    ) -> None:
        projector = BboxProjector(bbox, detail_level)
        z = scaled_map_value(0.08, scene_scale)
        x0, y0, _z = projector.project(bbox.max_lat, bbox.max_lon, z=z)
        pad = scaled_map_value(0.35, scene_scale)
        cx = x0 - pad
        cy = y0 - pad
        s = scaled_map_value(0.12, scene_scale)  # half-width of arrow
        h = scaled_map_value(0.28, scene_scale)  # total height
        # Arrow head (upward triangle) + tail rectangle
        verts = [
            (cx, cy + h * 0.35, z),        # 0 base-left
            (cx + s, cy + h * 0.35, z),    # 1 base-right
            (cx + s, cy + h, z),           # 2 top-right
            (cx, cy + h, z),               # 3 top-left (stem cap, unused)
            (cx - s * 0.5, cy + h * 0.35, z),  # 4 arrow left wing
            (cx + s * 1.5, cy + h * 0.35, z),  # 5 arrow right wing
            (cx + s * 0.5, cy + h * 1.45, z),  # 6 arrow tip
        ]
        # Stem face + arrowhead triangle
        faces = [(0, 1, 2, 3), (4, 5, 6)]  # stem quad + head triangle
        create_quad_mesh_object(
            context,
            "GeoMap_NorthArrow",
            verts[:4],
            "Annotations",
            material_named("GeoMap_NorthArrow_Material", (0.02, 0.02, 0.05, 1.0)),
        )
        arrowhead_verts = [verts[4], verts[5], verts[6]]
        create_quad_mesh_object(
            context,
            "GeoMap_NorthArrow_Head",
            arrowhead_verts,
            "Annotations",
            material_named("GeoMap_NorthArrow_Material", (0.02, 0.02, 0.05, 1.0)),
        )
        self._create_text_object(
            context,
            "N",
            (cx + s * 0.5, cy + h * 1.55, z),
            "GeoMap_NorthArrow_N",
            size=scaled_map_value(0.18, scene_scale),
        )

    def _create_scale_bar(
        self,
        context,
        bbox: BoundingBox,
        detail_level: str,
        scene_scale: SceneScale,
    ) -> None:
        km_per_bu = scene_scale.km_per_bu
        if km_per_bu <= 0.0:
            return
        projector = BboxProjector(bbox, detail_level)
        z = scaled_map_value(0.08, scene_scale)
        x0, y0, _z = projector.project(bbox.min_lat, bbox.min_lon, z=z)
        target_km = self._nice_scale_km(km_per_bu * scene_scale.map_units * 0.25)
        width = max(target_km / km_per_bu, 0.1)
        height = max(width * 0.04, scaled_map_value(0.025, scene_scale))
        x = x0 + scaled_map_value(0.25, scene_scale)
        y = y0 + scaled_map_value(0.25, scene_scale)
        verts = [
            (x, y, z),
            (x + width, y, z),
            (x + width, y + height, z),
            (x, y + height, z),
        ]
        create_quad_mesh_object(
            context,
            "GeoMap_Scale_Bar",
            verts,
            "Annotations",
            material_named("GeoMap_Scale_Material", (0.02, 0.02, 0.02, 1.0)),
        )
        self._create_text_object(
            context,
            f"{target_km:g} km",
            (x, y + height + scaled_map_value(0.04, scene_scale), z),
            "GeoMap_Scale_Label",
            size=scaled_map_value(0.14, scene_scale),
        )

    def _create_legend(
        self,
        context,
        bbox: BoundingBox,
        detail_level: str,
        layer_keys: list[str],
        scene_scale: SceneScale,
    ) -> None:
        if not layer_keys:
            return
        projector = BboxProjector(bbox, detail_level)
        z = scaled_map_value(0.08, scene_scale)
        x0, y0, _z = projector.project(bbox.max_lat, bbox.min_lon, z=z)
        y = y0 - scaled_map_value(0.22, scene_scale)
        self._create_text_object(
            context,
            "GeoMap",
            (x0 + scaled_map_value(0.22, scene_scale), y, z),
            "GeoMap_Legend_Title",
            scaled_map_value(0.15, scene_scale),
        )
        for index, layer_key in enumerate(layer_keys[:8], start=1):
            y_item = y - index * scaled_map_value(0.18, scene_scale)
            self._create_legend_swatch(
                context,
                x0 + scaled_map_value(0.22, scene_scale),
                y_item + scaled_map_value(0.02, scene_scale),
                z,
                layer_key,
                scene_scale,
            )
            self._create_text_object(
                context,
                legend_label(layer_key),
                (x0 + scaled_map_value(0.36, scene_scale), y_item, z),
                f"GeoMap_Legend_{index:02d}",
                size=scaled_map_value(0.10, scene_scale),
            )

    def _create_legend_swatch(
        self,
        context,
        x: float,
        y: float,
        z: float,
        layer_key: str,
        scene_scale: SceneScale,
    ) -> None:
        size = scaled_map_value(0.09, scene_scale)
        verts = [
            (x, y, z),
            (x + size, y, z),
            (x + size, y + size, z),
            (x, y + size, z),
        ]
        from .layer_style import color_for_layer

        create_quad_mesh_object(
            context,
            f"GeoMap_Legend_Swatch_{layer_key}",
            verts,
            "Annotations",
            material_named(f"GeoMap_Legend_{layer_key}_Material", color_for_layer(layer_key)),
        )

    @staticmethod
    def _create_text_object(
        context,
        text: str,
        location: tuple[float, float, float],
        object_name: str,
        size: float,
    ) -> None:
        create_text_object(
            context,
            text,
            location,
            object_name,
            size,
            material_named("GeoMap_Text_Material", (0.02, 0.02, 0.02, 1.0)),
        )

    @staticmethod
    def _nice_scale_km(value: float) -> float:
        if value <= 0.0:
            return 1.0
        magnitude = 10 ** math.floor(math.log10(value))
        for factor in (1, 2, 5, 10):
            candidate = factor * magnitude
            if candidate >= value:
                return candidate
        return 10 * magnitude


class PlaceLabelRenderer:
    def render(
        self,
        context,
        points: list[OsmPoint],
        bbox: BoundingBox,
        settings,
        scene_scale: SceneScale,
        dem_sampler=None,
    ) -> list:
        assert_main_thread()
        min_rank = _PLACE_RANK.get(settings.place_label_min_type, 2)
        size_factor = max(float(settings.place_label_size_factor), 0.01)
        projector = BboxProjector(bbox, settings.detail_level)
        created = []
        seen_ids: set[int] = set()

        for point in points:
            if point.category != "city":
                continue
            if point.id in seen_ids:
                continue
            place_type = self._place_type(point.tags)
            if _PLACE_RANK.get(place_type, 99) > min_rank:
                continue
            seen_ids.add(point.id)
            obj = self._create_label(
                context, point, place_type, projector, settings, scene_scale, dem_sampler, size_factor
            )
            if obj:
                created.append(obj)
        return created

    @staticmethod
    def _place_type(tags: dict) -> str:
        capital = tags.get("capital", "")
        if capital in {"yes", "2", "4", "6"}:
            return "capital"
        place = tags.get("place", "")
        if place == "city":
            return "city"
        if place == "town":
            return "town"
        if place == "village":
            return "village"
        return "hamlet"

    def _create_label(
        self,
        context,
        point: OsmPoint,
        place_type: str,
        projector: BboxProjector,
        settings,
        scene_scale: SceneScale,
        dem_sampler,
        size_factor: float,
    ):
        x, y, _ = projector.project(point.lat, point.lon)
        z_terrain = (
            dem_sampler.sample_z(point.lat, point.lon)
            if dem_sampler and settings.drape_vectors_on_dem
            else 0.0
        )
        z = z_terrain + scaled_map_value(float(settings.vector_z_offset) * 1.5, scene_scale)

        base_size = _PLACE_BASE_SIZE.get(place_type, 0.12)
        type_factor = max(
            float(getattr(settings, f"place_label_size_{place_type}", 1.0)),
            0.01,
        )
        size = max(scaled_map_value(base_size * size_factor * type_factor, scene_scale), 1e-4)

        safe_name = self._sanitize(point.name)[:24]
        obj_name = f"GeoMap_Label_{place_type.title()}_{safe_name}"

        obj = bpy.data.objects.new(obj_name, None)
        obj.location = (x, y, z)
        obj.empty_display_type = "CIRCLE"
        obj.empty_display_size = size
        link_to_geomap_collection(context, obj, "Labels")
        obj["geomap_layer"] = "place_label"
        obj["geomap_place_type"] = place_type
        obj["geomap_name"] = point.name
        obj["geomap_lat"] = point.lat
        obj["geomap_lon"] = point.lon
        obj["geomap_label_text_size"] = size
        obj["geomap_label_font_family"] = getattr(
            settings, f"place_label_font_{place_type}", "DEFAULT",
        )
        return obj

    @staticmethod
    def _sanitize(name: str) -> str:
        return re.sub(r"[^\w\s\-\.\,\'\(\)]", "", name).strip() or "?"


def font_for_family(family: str):
    family = (family or "DEFAULT").upper()
    if family == "DEFAULT":
        return None
    for path in _FONT_CANDIDATES.get(family, []):
        if os.path.exists(path):
            try:
                return bpy.data.fonts.load(path, check_existing=True)
            except Exception:
                return None
    return None
