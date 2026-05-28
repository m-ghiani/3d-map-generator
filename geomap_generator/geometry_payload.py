import math as _math
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
    min_height_m: float = 0.0
    min_height_bu: float = 0.0
    roof_shape: str = ""          # flat | pyramidal | gabled | hipped | dome
    roof_height_m: float = 0.0
    roof_direction: float = 0.0   # degrees, 0=+Y (north), 90=+X (east)


@dataclass(frozen=True)
class BuildingBatchPayload:
    name: str
    building_count: int
    verts: list[tuple[float, float, float]]
    faces: list[tuple]
    material_color: tuple = (0.55, 0.52, 0.46, 1.0)  # RGBA linear


def build_vector_payload(
    layer_key: str,
    object_name: str,
    layer_data: GeoMapData,
    settings,
    _dem_sampler,  # reserved for future per-vertex DEM elevation on vector splines
    scene_scale: SceneScale | None,
) -> VectorCurvePayload | None:
    base_width = scaled_map_value(base_width_for_layer(settings, layer_key), scene_scale)
    if base_width <= 0.0:
        return None
    return _build_curve_payload(
        layer_key, object_name, layer_data, settings, base_width, scene_scale
    )


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
    """Extrude a building footprint into a prismatic Blender mesh with optional roof.

    Supports:
    - ``min_height_m``: floating building:part sections
    - ``roof_shape``: flat (default), pyramidal, gabled, hipped, dome
    - ``roof_height_m``: height of roof portion (subtracted from wall height)
    - ``roof_direction``: ridge orientation in degrees (0=north/+Y, 90=east/+X)
    - ``inner_rings``: courtyard holes — vertical walls generated for each ring
    """
    if km_per_bu <= 0.0:
        raise ValueError("GeoMap scale metadata is missing or invalid.")

    projector = BboxProjector(bbox, detail_level)
    height_bu = building.height_m / (km_per_bu * 1000.0)
    min_height_m = getattr(building, "min_height_m", 0.0) or 0.0
    min_height_bu = min_height_m / (km_per_bu * 1000.0)
    if min_height_bu >= height_bu:
        min_height_bu = 0.0
        min_height_m = 0.0

    # Roof geometry parameters
    roof_shape = (getattr(building, "roof_shape", "") or "").lower()
    roof_height_m = getattr(building, "roof_height_m", 0.0) or 0.0
    roof_height_bu = roof_height_m / (km_per_bu * 1000.0)
    roof_direction = getattr(building, "roof_direction", 0.0) or 0.0

    # OSM convention: building:height includes roof; wall goes to height - roof:height
    floor_z = base_z + min_height_bu
    top_z = base_z + height_bu
    if roof_height_bu > 1e-9 and roof_height_bu < (height_bu - min_height_bu):
        wall_top_z = top_z - roof_height_bu
    else:
        wall_top_z = top_z
        roof_height_bu = 0.0

    # Project outer footprint and normalise to CCW winding (outward wall normals)
    outer_xy: list[tuple[float, float]] = []
    for node in building.geometry:
        x, y, _z = projector.project(node.lat, node.lon, z=floor_z)
        outer_xy.append((x, y))
    if _signed_area_2d(outer_xy) < 0:
        outer_xy.reverse()
    n_outer = len(outer_xy)

    # Body verts: bottom ring (floor_z) + wall-top ring (wall_top_z)
    verts: list[tuple[float, float, float]] = []
    for x, y in outer_xy:
        verts.append((x, y, floor_z))
    for x, y in outer_xy:
        verts.append((x, y, wall_top_z))

    # Bottom face (reversed winding = outward-down normal)
    faces: list[tuple] = [tuple(reversed(range(n_outer)))]
    # Outer wall side quads
    for i in range(n_outer):
        j = (i + 1) % n_outer
        faces.append((i, j, n_outer + j, n_outer + i))

    # Inner ring walls (courtyards / holes)
    inner_rings = getattr(building, "inner_rings", ()) or ()
    has_inner_rings = False
    for ring in inner_rings:
        if len(ring) < 3:
            continue
        has_inner_rings = True
        ring_xy: list[tuple[float, float]] = []
        for node in ring:
            x, y, _z = projector.project(node.lat, node.lon, z=floor_z)
            ring_xy.append((x, y))
        if _signed_area_2d(ring_xy) < 0:
            ring_xy.reverse()
        n_ring = len(ring_xy)
        base_idx = len(verts)
        for x, y in ring_xy:
            verts.append((x, y, floor_z))
        for x, y in ring_xy:
            verts.append((x, y, wall_top_z))
        # Inner walls: reversed winding so normals face the courtyard interior
        for i in range(n_ring):
            j = (i + 1) % n_ring
            faces.append((
                base_idx + i,
                base_idx + n_ring + i,
                base_idx + n_ring + j,
                base_idx + j,
            ))

    # Roof geometry
    if roof_height_bu > 1e-9 and not has_inner_rings:
        if roof_shape == "pyramidal":
            _add_pyramidal_roof(verts, faces, outer_xy, n_outer, wall_top_z, roof_height_bu)
        elif roof_shape in ("gabled", "half-hipped"):
            _add_gabled_roof(verts, faces, outer_xy, n_outer, wall_top_z, roof_height_bu, roof_direction)
        elif roof_shape == "hipped":
            _add_hipped_roof(verts, faces, outer_xy, n_outer, wall_top_z, roof_height_bu, roof_direction)
        elif roof_shape == "dome":
            # Approximate dome as pyramid; true sphere requires subdivision
            _add_pyramidal_roof(verts, faces, outer_xy, n_outer, wall_top_z, roof_height_bu)
        else:
            # Flat roof
            faces.append(tuple(range(n_outer, n_outer * 2)))
    else:
        # Flat top (no roof height, or inner rings present — holes handled by wall normals)
        faces.append(tuple(range(n_outer, n_outer * 2)))

    return BuildingMeshPayload(
        id=building.id,
        name=building.name,
        height_m=building.height_m,
        height_bu=height_bu,
        verts=verts,
        faces=faces,
        min_height_m=min_height_m,
        min_height_bu=min_height_bu,
        roof_shape=roof_shape,
        roof_height_m=roof_height_m,
        roof_direction=roof_direction,
    )


# ---------------------------------------------------------------------------
# Roof shape builders
# ---------------------------------------------------------------------------

def _add_pyramidal_roof(
    verts: list,
    faces: list,
    outer_xy: list[tuple[float, float]],
    n_outer: int,
    wall_top_z: float,
    roof_height_bu: float,
) -> None:
    """Pyramid: single apex at footprint centroid, triangular fans to each wall-top edge."""
    cx = sum(x for x, y in outer_xy) / n_outer
    cy = sum(y for x, y in outer_xy) / n_outer
    apex_idx = len(verts)
    verts.append((cx, cy, wall_top_z + roof_height_bu))
    for i in range(n_outer):
        j = (i + 1) % n_outer
        faces.append((n_outer + j, apex_idx, n_outer + i))


def _add_gabled_roof(
    verts: list,
    faces: list,
    outer_xy: list[tuple[float, float]],
    n_outer: int,
    wall_top_z: float,
    roof_height_bu: float,
    roof_direction_deg: float,
) -> None:
    """Gabled: ridge runs along ``roof_direction``; eave verts stay at wall_top, ridge at +roof_height."""
    factors = _gabled_height_factors(outer_xy, roof_direction_deg)
    roof_start = len(verts)
    for i, (x, y) in enumerate(outer_xy):
        verts.append((x, y, wall_top_z + roof_height_bu * factors[i]))
    # Slope quads: wall-top ring → roof ring
    for i in range(n_outer):
        j = (i + 1) % n_outer
        faces.append((n_outer + i, n_outer + j, roof_start + j, roof_start + i))
    # Ridge cap (non-planar polygon, Blender triangulates it automatically)
    faces.append(tuple(range(roof_start, roof_start + n_outer)))


def _add_hipped_roof(
    verts: list,
    faces: list,
    outer_xy: list[tuple[float, float]],
    n_outer: int,
    wall_top_z: float,
    roof_height_bu: float,
    roof_direction_deg: float,
) -> None:
    """Hipped: height tapers toward both ridge ends AND eave edges → tent/hip shape."""
    factors = _hipped_height_factors(outer_xy, roof_direction_deg)
    roof_start = len(verts)
    for i, (x, y) in enumerate(outer_xy):
        verts.append((x, y, wall_top_z + roof_height_bu * factors[i]))
    for i in range(n_outer):
        j = (i + 1) % n_outer
        faces.append((n_outer + i, n_outer + j, roof_start + j, roof_start + i))
    faces.append(tuple(range(roof_start, roof_start + n_outer)))


def _gabled_height_factors(
    outer_xy: list[tuple[float, float]], direction_deg: float
) -> list[float]:
    """Per-vertex height factor [0..1]: 1 at ridge axis, 0 at eave edges."""
    n = len(outer_xy)
    cx = sum(x for x, y in outer_xy) / n
    cy = sum(y for x, y in outer_xy) / n
    rd = _math.radians(direction_deg)
    # Perpendicular-to-ridge = slope direction
    pdx = _math.cos(rd)
    pdy = -_math.sin(rd)
    perps = [abs((x - cx) * pdx + (y - cy) * pdy) for x, y in outer_xy]
    p_max = max(perps) if perps else 1.0
    if p_max < 1e-9:
        return [1.0] * n
    return [max(0.0, 1.0 - p / p_max) for p in perps]


def _hipped_height_factors(
    outer_xy: list[tuple[float, float]], direction_deg: float
) -> list[float]:
    """Per-vertex height factor [0..1]: tapers toward both ridge ends and eave edges."""
    n = len(outer_xy)
    cx = sum(x for x, y in outer_xy) / n
    cy = sum(y for x, y in outer_xy) / n
    rd = _math.radians(direction_deg)
    rdx = _math.sin(rd)    # ridge direction
    rdy = _math.cos(rd)
    pdx = _math.cos(rd)    # perpendicular (slope direction)
    pdy = -_math.sin(rd)
    ridges = [(x - cx) * rdx + (y - cy) * rdy for x, y in outer_xy]
    perps = [abs((x - cx) * pdx + (y - cy) * pdy) for x, y in outer_xy]
    r_max = max(abs(r) for r in ridges) if ridges else 1.0
    p_max = max(perps) if perps else 1.0
    factors = []
    for r, p in zip(ridges, perps):
        f_ridge = max(0.0, 1.0 - abs(r) / max(r_max, 1e-9))
        f_perp = max(0.0, 1.0 - p / max(p_max, 1e-9))
        factors.append(min(f_ridge, f_perp))
    return factors


# ---------------------------------------------------------------------------
# Batch payload builder
# ---------------------------------------------------------------------------

def build_building_batch_payload(
    buildings,
    bbox: BoundingBox,
    detail_level: str,
    km_per_bu: float,
    base_z_for_building: Callable[[object], float],
    *,
    name: str,
    max_vertices_per_building: int = 12,
    material_color: tuple = (0.55, 0.52, 0.46, 1.0),
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
        min_height_bu = getattr(building, "min_height_m", 0.0) / (km_per_bu * 1000.0)
        if min_height_bu >= height_bu:
            min_height_bu = 0.0
        floor_z = base_z + min_height_bu
        top_z = base_z + height_bu

        ring_xy = []
        for node in geometry:
            x, y, _z = projector.project(node.lat, node.lon, z=floor_z)
            ring_xy.append((x, y))
        if _signed_area_2d(ring_xy) < 0:
            ring_xy.reverse()
        start = len(verts)
        for x, y in ring_xy:
            verts.append((x, y, floor_z))
        top_start = len(verts)
        verts.extend((x, y, top_z) for x, y in ring_xy)

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
        material_color=material_color,
    )


def _signed_area_2d(xy: list[tuple[float, float]]) -> float:
    """Shoelace formula; positive = CCW in standard XY (Blender top view)."""
    n = len(xy)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += xy[i][0] * xy[j][1] - xy[j][0] * xy[i][1]
    return area * 0.5


def _simplify_ring(geometry, max_vertices: int):
    if len(geometry) <= max_vertices:
        return geometry
    step = max(1, round(len(geometry) / max_vertices))
    simplified = geometry[::step]
    if len(simplified) > max_vertices:
        simplified = simplified[:max_vertices]
    return simplified if len(simplified) >= 3 else geometry[:max_vertices]
