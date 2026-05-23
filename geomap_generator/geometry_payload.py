from dataclasses import dataclass
from typing import Callable

from .layer_style import base_width_for_layer, z_offset_for_layer
from .mesh_builder import BboxProjector
from .models import BoundingBox, GeoMapData
from .scene_units import SceneScale, scaled_map_value


@dataclass(frozen=True)
class VectorMeshPayload:
    layer_key: str
    object_name: str
    way_count: int
    ribbon_width: float
    geometry_type: str
    verts: list[tuple[float, float, float]]
    edges: list[tuple[int, int]]
    faces: list[tuple]


@dataclass(frozen=True)
class VectorCurvePayload:
    layer_key: str
    object_name: str
    way_count: int
    curve_width: float
    splines: list[list[tuple[float, float, float, float]]]


@dataclass(frozen=True)
class BuildingMeshPayload:
    id: int
    name: str
    height_m: float
    height_bu: float
    verts: list[tuple[float, float, float]]
    faces: list[tuple]


@dataclass(frozen=True)
class BuildingBatchPayload:
    name: str
    building_count: int
    verts: list[tuple[float, float, float]]
    faces: list[tuple]


def build_vector_payload(
    layer_key: str,
    object_name: str,
    layer_data: GeoMapData,
    settings,
    dem_sampler,
    scene_scale: SceneScale | None,
) -> VectorCurvePayload | None:
    base_width = scaled_map_value(base_width_for_layer(settings, layer_key), scene_scale)
    if base_width <= 0.0:
        return None
    return _build_curve_payload(layer_key, object_name, layer_data, settings, base_width, scene_scale)


def _build_curve_payload(
    layer_key: str,
    object_name: str,
    layer_data: GeoMapData,
    settings,
    base_width: float,
    scene_scale: SceneScale | None,
) -> VectorCurvePayload | None:
    from .topology import merge_ways_by_topology

    projector = BboxProjector(layer_data.bbox, settings.detail_level)
    z_offset = scaled_map_value(
        settings.vector_z_offset + z_offset_for_layer(layer_key), scene_scale
    )
    ways = merge_ways_by_topology(layer_data.ways)
    splines = []
    for way in ways:
        if len(way.geometry) < 2:
            continue
        points = [
            (*projector.project(node.lat, node.lon, z=z_offset), 1.0)
            for node in way.geometry
        ]
        splines.append(points)
    if not splines:
        return None
    return VectorCurvePayload(
        layer_key=layer_key,
        object_name=object_name,
        way_count=len(splines),
        curve_width=base_width,
        splines=splines,
    )


def build_building_payload(
    building,
    bbox: BoundingBox,
    detail_level: str,
    km_per_bu: float,
    base_z: float,
) -> BuildingMeshPayload:
    if km_per_bu <= 0.0:
        raise ValueError("GeoMap scale metadata is missing or invalid.")

    projector = BboxProjector(bbox, detail_level)
    height_bu = building.height_m / (km_per_bu * 1000.0)
    verts = []
    for node in building.geometry:
        x, y, _z = projector.project(node.lat, node.lon, z=base_z)
        verts.append((x, y, base_z))
    top_start = len(verts)
    verts.extend((x, y, base_z + height_bu) for x, y, _z in verts[:top_start])

    bottom = tuple(reversed(range(top_start)))
    top = tuple(range(top_start, top_start * 2))
    faces = [bottom, top]
    for index in range(top_start):
        next_index = (index + 1) % top_start
        faces.append((index, next_index, top_start + next_index, top_start + index))

    return BuildingMeshPayload(
        id=building.id,
        name=building.name,
        height_m=building.height_m,
        height_bu=height_bu,
        verts=verts,
        faces=faces,
    )


def build_building_batch_payload(
    buildings,
    bbox: BoundingBox,
    detail_level: str,
    km_per_bu: float,
    base_z_for_building: Callable[[object], float],
    *,
    name: str,
    max_vertices_per_building: int = 12,
) -> BuildingBatchPayload:
    if km_per_bu <= 0.0:
        raise ValueError("GeoMap scale metadata is missing or invalid.")

    projector = BboxProjector(bbox, detail_level)
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple] = []
    building_count = 0

    for building in buildings:
        geometry = _simplify_ring(building.geometry, max_vertices_per_building)
        if len(geometry) < 3:
            continue

        base_z = base_z_for_building(building)
        height_bu = building.height_m / (km_per_bu * 1000.0)
        start = len(verts)
        for node in geometry:
            x, y, _z = projector.project(node.lat, node.lon, z=base_z)
            verts.append((x, y, base_z))
        top_start = len(verts)
        verts.extend((x, y, base_z + height_bu) for x, y, _z in verts[start:top_start])

        count = top_start - start
        faces.append(tuple(reversed(range(start, top_start))))
        faces.append(tuple(range(top_start, top_start + count)))
        for index in range(count):
            next_index = (index + 1) % count
            faces.append(
                (
                    start + index,
                    start + next_index,
                    top_start + next_index,
                    top_start + index,
                )
            )
        building_count += 1

    return BuildingBatchPayload(
        name=name,
        building_count=building_count,
        verts=verts,
        faces=faces,
    )


def _simplify_ring(geometry, max_vertices: int):
    if len(geometry) <= max_vertices:
        return geometry
    step = max(1, round(len(geometry) / max_vertices))
    simplified = geometry[::step]
    if len(simplified) > max_vertices:
        simplified = simplified[:max_vertices]
    return simplified if len(simplified) >= 3 else geometry[:max_vertices]


