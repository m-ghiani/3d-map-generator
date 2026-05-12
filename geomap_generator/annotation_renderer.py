import math

import bpy

from .blender_scene import create_quad_mesh_object, create_text_object, material_named
from .layer_style import legend_label
from .mesh_builder import BboxProjector
from .models import BoundingBox
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread


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
