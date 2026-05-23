import json

import bpy

from .blender_scene import (
    get_or_create_curve_profile,
    link_to_geomap_collection,
    material_named,
    set_active,
)
from .layer_style import (
    base_width_for_layer,
    collection_name_for_layer,
    color_for_layer,
    z_offset_for_layer,
)
from .geometry_payload import VectorCurvePayload, VectorMeshPayload
from .mesh_builder import (
    BboxProjector,
    DemHeightSampler,
    PolygonFillBuilder,
)
from .models import GeoMapData, OsmWay
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread


class VectorRenderer:
    def commit_payload(self, context, payload, active: bool = False):
        assert_main_thread()
        if isinstance(payload, VectorCurvePayload):
            return self._commit_curve_payload(context, payload, active)
        if isinstance(payload, VectorMeshPayload):
            return self._commit_mesh_payload(context, payload, active)
        return None

    def render_layers(
        self,
        context,
        vector_layers: list[tuple[str, str, GeoMapData]],
        settings,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale | None = None,
    ):
        assert_main_thread()
        vector_objects = []
        for layer_index, (layer_key, layer_name, layer_data) in enumerate(vector_layers, start=1):
            vector_obj = self._create_vector_layer_object(
                context,
                layer_data,
                settings,
                layer_key,
                layer_name,
                dem_sampler,
                scene_scale,
                active=layer_index == 1,
            )
            if vector_obj:
                vector_objects.append(vector_obj)
        return vector_objects

    def render_points(
        self,
        context,
        osm_data: GeoMapData,
        settings,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale | None = None,
    ):
        assert_main_thread()
        if not osm_data.points:
            return []

        projector = BboxProjector(osm_data.bbox, settings.detail_level)
        created = []
        used_names: dict[str, int] = {}
        seen: set[tuple[str, int, int, int]] = set()
        for point in osm_data.points:
            key = (
                point.category,
                point.id,
                round(point.lat * 1_000_000),
                round(point.lon * 1_000_000),
            )
            if key in seen:
                continue
            seen.add(key)
            base_name = self._safe_object_name(point.name)
            count = used_names.get(base_name, 0)
            used_names[base_name] = count + 1
            object_name = base_name if count == 0 else f"{base_name}_{count + 1}"

            terrain_z = (
                dem_sampler.sample_z(point.lat, point.lon)
                if dem_sampler and settings.drape_vectors_on_dem
                else 0.0
            )
            obj = bpy.data.objects.new(object_name, None)
            obj.empty_display_type = "PLAIN_AXES"
            obj.empty_display_size = scaled_map_value(0.10, scene_scale)
            z_offset = scaled_map_value(settings.vector_z_offset, scene_scale)
            obj.location = projector.project(
                point.lat,
                point.lon,
                z=terrain_z + z_offset + scaled_map_value(0.035, scene_scale),
            )
            obj.color = color_for_layer(f"poi_{point.category}")
            obj["geomap_layer"] = f"poi_{point.category}"
            obj["geomap_osm_id"] = str(point.id)
            obj["geomap_osm_type"] = point.osm_type
            obj["geomap_name"] = point.name
            obj["geomap_category"] = point.category
            obj["geomap_lat"] = point.lat
            obj["geomap_lon"] = point.lon
            obj["geomap_osm_tags"] = json.dumps(
                point.tags,
                sort_keys=True,
                ensure_ascii=False,
            )
            for tag_key, tag_value in sorted(point.tags.items()):
                obj[f"osm:{tag_key}"] = str(tag_value)
            link_to_geomap_collection(context, obj, "POI")
            created.append(obj)
        return created

    def render_landuse(
        self,
        context,
        landuse_layers: list[tuple[str, str, GeoMapData]],
        settings,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale | None = None,
    ) -> list:
        assert_main_thread()
        objects = []
        z_offset = scaled_map_value(-0.005, scene_scale)
        for layer_key, layer_name, layer_data in landuse_layers:
            if not layer_data.ways:
                continue
            z_provider = (
                dem_sampler.sample_z if dem_sampler and settings.drape_vectors_on_dem else None
            )
            verts, _, faces = PolygonFillBuilder().build(
                layer_data,
                settings.detail_level,
                z_offset=z_offset,
                z_provider=z_provider,
            )
            if not verts:
                continue
            mesh = bpy.data.meshes.new(f"{layer_name}_Mesh")
            obj = bpy.data.objects.new(layer_name, mesh)
            link_to_geomap_collection(context, obj, "LandUse")
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            color = color_for_layer(layer_key)
            obj.data.materials.append(
                material_named(f"GeoMap_{layer_key}_Material", color)
            )
            obj["geomap_layer"] = layer_key
            obj["geomap_way_count"] = len(layer_data.ways)
            obj["geomap_geometry_type"] = "POLYGON"
            objects.append(obj)
        return objects

    def _create_vector_layer_object(
        self,
        context,
        layer_data: GeoMapData,
        settings,
        layer_key: str,
        object_name: str,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale | None,
        active: bool,
    ):
        base_width = scaled_map_value(base_width_for_layer(settings, layer_key), scene_scale)
        if base_width <= 0.0:
            return None
        return self._create_curve_layer_object(
            context,
            layer_data,
            settings,
            layer_key,
            object_name,
            scene_scale,
            active,
        )

    def _commit_mesh_payload(self, context, payload: VectorMeshPayload, active: bool):
        if not payload.verts:
            return None
        mesh = bpy.data.meshes.new(f"{payload.object_name}_Mesh")
        obj = bpy.data.objects.new(payload.object_name, mesh)
        link_to_geomap_collection(context, obj, collection_name_for_layer(payload.layer_key))
        if active:
            set_active(context, obj)
        mesh.from_pydata(payload.verts, payload.edges, payload.faces)
        mesh.update()
        obj.data.materials.append(
            material_named(
                f"GeoMap_{collection_name_for_layer(payload.layer_key)}_Material",
                color_for_layer(payload.layer_key),
            )
        )
        obj["geomap_layer"] = payload.layer_key
        obj["geomap_way_count"] = payload.way_count
        obj["geomap_ribbon_width"] = payload.ribbon_width
        obj["geomap_geometry_type"] = payload.geometry_type
        return obj

    def _commit_curve_payload(self, context, payload: VectorCurvePayload, active: bool):
        if not payload.splines:
            return None
        curve = bpy.data.curves.new(f"{payload.object_name}_Curve", "CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 2
        curve.fill_mode = "FULL"
        profile = get_or_create_curve_profile(context, payload.layer_key, payload.curve_width / 2.0)
        curve.bevel_mode = "OBJECT"
        curve.bevel_object = profile
        for points in payload.splines:
            if len(points) < 2:
                continue
            spline = curve.splines.new("POLY")
            spline.points.add(len(points) - 1)
            for spline_point, point in zip(spline.points, points):
                spline_point.co = point
        obj = bpy.data.objects.new(payload.object_name, curve)
        link_to_geomap_collection(context, obj, collection_name_for_layer(payload.layer_key))
        if active:
            set_active(context, obj)
        obj.data.materials.append(
            material_named(
                f"GeoMap_{collection_name_for_layer(payload.layer_key)}_Material",
                color_for_layer(payload.layer_key),
            )
        )
        obj["geomap_layer"] = payload.layer_key
        obj["geomap_way_count"] = payload.way_count
        obj["geomap_geometry_type"] = "CURVE"
        obj["geomap_curve_width"] = payload.curve_width
        return obj

    def _create_curve_layer_object(
        self,
        context,
        layer_data: GeoMapData,
        settings,
        layer_key: str,
        object_name: str,
        scene_scale: SceneScale | None,
        active: bool,
    ):
        base_width = scaled_map_value(base_width_for_layer(settings, layer_key), scene_scale)
        if base_width <= 0.0:
            return None

        curve = bpy.data.curves.new(f"{object_name}_Curve", "CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 2
        curve.fill_mode = "FULL"
        profile = get_or_create_curve_profile(context, layer_key, base_width / 2.0)
        curve.bevel_mode = "OBJECT"
        curve.bevel_object = profile

        projector = BboxProjector(layer_data.bbox, settings.detail_level)
        z_offset = scaled_map_value(
            settings.vector_z_offset + z_offset_for_layer(layer_key), scene_scale
        )

        created_count = 0
        for way in layer_data.ways:
            if not self._add_way_spline(curve, projector, way, z_offset):
                continue
            created_count += 1

        if created_count == 0:
            return None

        obj = bpy.data.objects.new(object_name, curve)
        link_to_geomap_collection(context, obj, collection_name_for_layer(layer_key))
        if active:
            set_active(context, obj)
        obj.data.materials.append(
            material_named(
                f"GeoMap_{collection_name_for_layer(layer_key)}_Material",
                color_for_layer(layer_key),
            )
        )
        obj["geomap_layer"] = layer_key
        obj["geomap_way_count"] = created_count
        obj["geomap_geometry_type"] = "CURVE"
        obj["geomap_curve_width"] = base_width
        return obj

    @staticmethod
    def _add_way_spline(curve, projector: BboxProjector, way: OsmWay, z_offset: float):
        if len(way.geometry) < 2:
            return False
        spline = curve.splines.new("POLY")
        spline.points.add(len(way.geometry) - 1)
        for point, node in zip(spline.points, way.geometry):
            x, y, z = projector.project(node.lat, node.lon, z=z_offset)
            point.co = (x, y, z, 1.0)
        return True

    @staticmethod
    def _should_create_curve(settings, layer_key: str) -> bool:
        return True

    @staticmethod
    def _safe_object_name(name: str) -> str:
        cleaned = "".join(char if char.isalnum() or char in " _.-" else "_" for char in name)
        cleaned = " ".join(cleaned.split())
        return cleaned[:63] if cleaned else "GeoMap POI"
