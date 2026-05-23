import math
from typing import Callable, Protocol, runtime_checkable

from .coordinates import CoordinateTransformer
from .models import BoundingBox, DemGrid, GeoMapData, OsmNode, OsmWay

# Minimum distance (km) between kept vertices per detail level.
_MIN_DIST: dict[str, float] = {"LOW": 5.0, "MEDIUM": 1.0, "HIGH": 0.2}
_ZOOM: dict[str, float] = {"LOW": 0.5, "MEDIUM": 1.0, "HIGH": 1.5}
ROAD_WIDTH_FACTORS = {
    "motorway": 1.85,
    "trunk": 1.55,
    "primary": 1.25,
    "secondary": 1.00,
    "tertiary": 0.82,
    "residential": 0.62,
    "service": 0.45,
    "unclassified": 0.65,
}
RIVER_WIDTH_FACTORS = {
    "river": 1.45,
    "canal": 1.05,
    "stream": 0.55,
    "ditch": 0.35,
    "drain": 0.35,
}

# Full geographic extent maps to this many Blender units at zoom=1 (MEDIUM).
_TARGET_UNITS = 10.0


def _zoom_for_detail(detail_level: str) -> float:
    return _ZOOM.get(detail_level, _ZOOM["MEDIUM"])


def _min_dist_for_detail(detail_level: str) -> float:
    return _MIN_DIST.get(detail_level, _MIN_DIST["MEDIUM"])


def lane_width_factor(lanes_value: str | None) -> float:
    if not lanes_value:
        return 1.0
    try:
        lanes = int(str(lanes_value).split(";", 1)[0].strip())
    except (TypeError, ValueError):
        return 1.0
    return max(0.75, min(2.0, lanes / 2.0))


def road_width_factor(tags: dict[str, str]) -> float:
    return ROAD_WIDTH_FACTORS.get(tags.get("highway", ""), 0.75) * lane_width_factor(
        tags.get("lanes")
    )


def river_width_factor(tags: dict[str, str]) -> float:
    return RIVER_WIDTH_FACTORS.get(tags.get("waterway", ""), 0.80)


def _decimate(nodes: list[OsmNode], min_dist_km: float, center_lon: float) -> list[OsmNode]:
    """Drop nodes closer than min_dist_km to the previous kept node."""
    if len(nodes) <= 2:
        return nodes
    kept = [nodes[0]]
    lx, ly = CoordinateTransformer.mercator_projection(nodes[0].lat, nodes[0].lon, center_lon)
    thresh_sq = min_dist_km ** 2
    for node in nodes[1:-1]:
        x, y = CoordinateTransformer.mercator_projection(node.lat, node.lon, center_lon)
        if (x - lx) ** 2 + (y - ly) ** 2 >= thresh_sq:
            kept.append(node)
            lx, ly = x, y
    kept.append(nodes[-1])
    return kept


class BboxProjector:
    """
    Projects lat/lon coordinates into Blender's Z-up space.

    Longitude maps to X, latitude/north maps to Y, and elevation/offset maps to Z.
    """

    def __init__(self, bbox: BoundingBox, detail_level: str) -> None:
        self.center_lon = bbox.center_lon()
        zoom = _zoom_for_detail(detail_level)
        min_x, min_y = CoordinateTransformer.mercator_projection(
            bbox.min_lat, bbox.min_lon, self.center_lon
        )
        max_x, max_y = CoordinateTransformer.mercator_projection(
            bbox.max_lat, bbox.max_lon, self.center_lon
        )
        self.cx = (min_x + max_x) / 2
        self.cy = (min_y + max_y) / 2
        extent_km = max(abs(max_x - min_x), abs(max_y - min_y)) or 1.0
        self.norm = (_TARGET_UNITS * zoom) / extent_km

    def project(self, lat: float, lon: float, z: float = 0.0) -> tuple[float, float, float]:
        east_km, north_km = CoordinateTransformer.mercator_projection(
            lat, lon, self.center_lon
        )
        return (
            (east_km - self.cx) * self.norm,
            (north_km - self.cy) * self.norm,
            z,
        )

    def unproject(self, bx: float, by: float) -> tuple[float, float]:
        east_km = bx / self.norm + self.cx
        north_km = by / self.norm + self.cy
        return CoordinateTransformer.inverse_mercator_projection(
            east_km, north_km, self.center_lon
        )


@runtime_checkable
class MeshBuilder(Protocol):
    """
    Returns (verts, edges, faces) for bpy.data.meshes.from_pydata().
    Add new feature types by implementing this protocol (OCP).
    """

    def build(
        self, data: GeoMapData, detail_level: str
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple],
    ]:
        ...


class LineMeshBuilder:
    """
    Builds an edge-wire mesh from way polylines (coastlines, rivers, roads).

    Two-pass approach:
      Pass 1 — project + decimate + deduplicate into raw (x, y) km coords.
      Pass 2 — normalize to _TARGET_UNITS × zoom, centered at origin,
               then scale to the requested detail level.
    """

    def build(
        self, data: GeoMapData, detail_level: str
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple],
    ]:
        min_dist = _min_dist_for_detail(detail_level)
        projector = BboxProjector(data.bbox, detail_level)

        # Pass 1: project, decimate, deduplicate
        verts: list[tuple[float, float, float]] = []
        vert_map: dict[tuple[int, int], int] = {}
        edges: list[tuple[int, int]] = []
        idx = 0

        for way in data.ways:
            nodes = _decimate(way.geometry, min_dist, projector.center_lon)
            way_indices: list[int] = []
            for node in nodes:
                x, y, z = projector.project(node.lat, node.lon)
                key = (round(x * 1000), round(y * 1000))
                if key not in vert_map:
                    vert_map[key] = idx
                    verts.append((x, y, z))
                    idx += 1
                way_indices.append(vert_map[key])

            for i in range(len(way_indices) - 1):
                a, b = way_indices[i], way_indices[i + 1]
                if a != b:
                    edges.append((a, b))

        return verts, edges, []


class RibbonMeshBuilder:
    """Builds flat Z-up ribbon meshes from way polylines."""

    def build(
        self,
        data: GeoMapData,
        detail_level: str,
        width: float | Callable[[object], float],
        z_offset: float = 0.0,
        z_provider: Callable[[float, float], float] | None = None,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple[int, int, int, int]],
    ]:
        fixed_width = width if isinstance(width, (int, float)) else None
        if fixed_width is not None and fixed_width <= 0.0:
            return LineMeshBuilder().build(data, detail_level)

        min_dist = _min_dist_for_detail(detail_level)
        projector = BboxProjector(data.bbox, detail_level)
        verts: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int, int]] = []

        for way in data.ways:
            way_width = width(way) if callable(width) else float(width)
            if way_width <= 0.0:
                continue
            half_width = way_width / 2.0
            nodes = _decimate(way.geometry, min_dist, projector.center_lon)
            if len(nodes) < 2:
                continue

            centerline = []
            for node in nodes:
                z = z_provider(node.lat, node.lon) if z_provider else 0.0
                centerline.append(projector.project(node.lat, node.lon, z + z_offset))

            if len(centerline) < 2:
                continue

            normals = self._vertex_normals(centerline)
            start_index = len(verts)
            for point, normal in zip(centerline, normals):
                nx, ny = normal
                verts.append((point[0] + nx * half_width, point[1] + ny * half_width, point[2]))
                verts.append((point[0] - nx * half_width, point[1] - ny * half_width, point[2]))

            for index in range(len(centerline) - 1):
                a = start_index + index * 2
                b = a + 1
                c = a + 3
                d = a + 2
                faces.append((a, b, c, d))

        return verts, [], faces

    @staticmethod
    def _vertex_normals(
        points: list[tuple[float, float, float]]
    ) -> list[tuple[float, float]]:
        segment_normals: list[tuple[float, float]] = []
        for first, second in zip(points, points[1:]):
            dx = second[0] - first[0]
            dy = second[1] - first[1]
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                segment_normals.append((0.0, 1.0))
            else:
                segment_normals.append((-dy / length, dx / length))

        normals = []
        for index in range(len(points)):
            if index == 0:
                normal = segment_normals[0]
            elif index == len(points) - 1:
                normal = segment_normals[-1]
            else:
                prev_normal = segment_normals[index - 1]
                next_normal = segment_normals[index]
                nx = prev_normal[0] + next_normal[0]
                ny = prev_normal[1] + next_normal[1]
                length = math.hypot(nx, ny)
                normal = (nx / length, ny / length) if length > 1e-9 else next_normal
            normals.append(normal)
        return normals


class BboxPlaneBuilder:
    """Builds a textured plane covering the requested geographic bbox."""

    def build(
        self,
        bbox: BoundingBox,
        detail_level: str,
        z: float = -0.01,
        projection_bbox: BoundingBox | None = None,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple[int, int, int, int]],
    ]:
        projector = BboxProjector(projection_bbox or bbox, detail_level)
        verts = [
            projector.project(bbox.min_lat, bbox.min_lon, z),
            projector.project(bbox.min_lat, bbox.max_lon, z),
            projector.project(bbox.max_lat, bbox.max_lon, z),
            projector.project(bbox.max_lat, bbox.min_lon, z),
        ]
        return verts, [], [(0, 1, 2, 3)]


class DemMeshBuilder:
    """Builds a terrain mesh from a sampled DEM grid."""

    def build(
        self,
        dem: DemGrid,
        detail_level: str,
        height_scale: float,
        projection_bbox: BoundingBox | None = None,
        min_elevation_override: float | None = None,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple[int, int, int, int]],
    ]:
        projector = BboxProjector(projection_bbox or dem.bbox, detail_level)
        min_elevation = (
            dem.min_elevation()
            if min_elevation_override is None
            else min_elevation_override
        )
        verts: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int, int]] = []

        for row in range(dem.rows):
            lat_t = row / (dem.rows - 1) if dem.rows > 1 else 0.0
            lat = dem.bbox.min_lat + (dem.bbox.max_lat - dem.bbox.min_lat) * lat_t
            for col in range(dem.cols):
                lon_t = col / (dem.cols - 1) if dem.cols > 1 else 0.0
                lon = dem.bbox.min_lon + (dem.bbox.max_lon - dem.bbox.min_lon) * lon_t
                elevation = dem.elevation_at(row, col)
                z = (elevation - min_elevation) * height_scale
                verts.append(projector.project(lat, lon, z))

        for row in range(dem.rows - 1):
            for col in range(dem.cols - 1):
                a = row * dem.cols + col
                b = a + 1
                c = a + dem.cols + 1
                d = a + dem.cols
                faces.append((a, b, c, d))

        return verts, [], faces

    @staticmethod
    def uv_coords(rows: int, cols: int) -> list[tuple[float, float]]:
        coords: list[tuple[float, float]] = []
        for row in range(rows):
            v = row / (rows - 1) if rows > 1 else 0.0
            for col in range(cols):
                u = col / (cols - 1) if cols > 1 else 0.0
                coords.append((u, v))
        return coords


class DemHeightSampler:
    """Samples DEM grids and returns Blender Z values matching DemMeshBuilder."""

    def __init__(self, grids: list[DemGrid], height_scale: float) -> None:
        self.grids = grids
        self.height_scale = height_scale
        self.min_elevation = min((grid.min_elevation() for grid in grids), default=0.0)

    def sample_z(self, lat: float, lon: float) -> float:
        grid = self._grid_for_point(lat, lon)
        if grid is None:
            return 0.0

        lat_span = grid.bbox.lat_span() or 1.0
        lon_span = grid.bbox.lon_span() or 1.0
        row_pos = (lat - grid.bbox.min_lat) / lat_span * (grid.rows - 1)
        col_pos = (lon - grid.bbox.min_lon) / lon_span * (grid.cols - 1)
        row_pos = max(0.0, min(grid.rows - 1, row_pos))
        col_pos = max(0.0, min(grid.cols - 1, col_pos))

        row0 = int(math.floor(row_pos))
        col0 = int(math.floor(col_pos))
        row1 = min(grid.rows - 1, row0 + 1)
        col1 = min(grid.cols - 1, col0 + 1)
        row_t = row_pos - row0
        col_t = col_pos - col0

        e00 = grid.elevation_at(row0, col0)
        e01 = grid.elevation_at(row0, col1)
        e10 = grid.elevation_at(row1, col0)
        e11 = grid.elevation_at(row1, col1)
        top = e00 + (e01 - e00) * col_t
        bottom = e10 + (e11 - e10) * col_t
        elevation = top + (bottom - top) * row_t
        return (elevation - self.min_elevation) * self.height_scale

    def _grid_for_point(self, lat: float, lon: float) -> DemGrid | None:
        for grid in self.grids:
            if (
                grid.bbox.min_lat <= lat <= grid.bbox.max_lat
                and grid.bbox.min_lon <= lon <= grid.bbox.max_lon
            ):
                return grid
        return self.grids[0] if self.grids else None


class ContourLineBuilder:
    """Extract iso-elevation contour lines from a DEM grid using marching squares."""

    # Case index: bit0=BL, bit1=BR, bit2=TR, bit3=TL (1=above level)
    # Each entry: list of (edge_a, edge_b) pairs to draw
    # Edges: 0=bottom(BL-BR), 1=right(BR-TR), 2=top(TR-TL), 3=left(TL-BL)
    _SEGMENTS: list[list[tuple[int, int]]] = [
        [],            # 0  0000
        [(0, 3)],      # 1  0001 BL
        [(0, 1)],      # 2  0010 BR
        [(1, 3)],      # 3  0011 BL+BR
        [(1, 2)],      # 4  0100 TR
        [(0, 3), (1, 2)],  # 5  0101 BL+TR saddle
        [(0, 2)],      # 6  0110 BR+TR
        [(2, 3)],      # 7  0111 BL+BR+TR
        [(2, 3)],      # 8  1000 TL
        [(0, 2)],      # 9  1001 BL+TL
        [(0, 1), (2, 3)],  # 10 1010 BR+TL saddle
        [(1, 2)],      # 11 1011 BL+BR+TL
        [(1, 3)],      # 12 1100 TR+TL
        [(0, 1)],      # 13 1101 BL+TR+TL
        [(0, 3)],      # 14 1110 BR+TR+TL
        [],            # 15 1111
    ]

    def build(
        self,
        dem: DemGrid,
        detail_level: str,
        height_scale: float,
        contour_interval_m: float,
        z_offset: float = 0.005,
        projection_bbox: BoundingBox | None = None,
        min_elevation_override: float | None = None,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list,
    ]:
        if contour_interval_m <= 0 or dem.rows < 2 or dem.cols < 2:
            return [], [], []
        projector = BboxProjector(projection_bbox or dem.bbox, detail_level)
        min_elev = min_elevation_override if min_elevation_override is not None else dem.min_elevation()
        max_elev = dem.max_elevation()
        first_level = math.ceil(min_elev / contour_interval_m) * contour_interval_m
        levels = []
        lvl = first_level
        while lvl <= max_elev:
            levels.append(lvl)
            lvl += contour_interval_m
        if not levels:
            return [], [], []

        verts: list[tuple[float, float, float]] = []
        edges: list[tuple[int, int]] = []
        vi = 0
        for row in range(dem.rows - 1):
            for col in range(dem.cols - 1):
                v0 = dem.elevation_at(row, col)
                v1 = dem.elevation_at(row, col + 1)
                v2 = dem.elevation_at(row + 1, col + 1)
                v3 = dem.elevation_at(row + 1, col)
                for level in levels:
                    case = (
                        (1 if v0 >= level else 0)
                        | (2 if v1 >= level else 0)
                        | (4 if v2 >= level else 0)
                        | (8 if v3 >= level else 0)
                    )
                    segs = self._SEGMENTS[case]
                    if not segs:
                        continue
                    edge_pts: dict[int, tuple[float, float, float]] = {}
                    for ea, eb in segs:
                        for e in (ea, eb):
                            if e not in edge_pts:
                                edge_pts[e] = self._edge_point(
                                    e, row, col, v0, v1, v2, v3, level,
                                    dem, projector, height_scale, min_elev, z_offset,
                                )
                    for ea, eb in segs:
                        verts.append(edge_pts[ea])
                        verts.append(edge_pts[eb])
                        edges.append((vi, vi + 1))
                        vi += 2
        return verts, edges, []

    @staticmethod
    def _edge_point(
        edge: int, row: int, col: int,
        v0: float, v1: float, v2: float, v3: float, level: float,
        dem: DemGrid, projector: BboxProjector,
        height_scale: float, min_elev: float, z_offset: float,
    ) -> tuple[float, float, float]:
        def _t(a: float, b: float) -> float:
            dv = b - a
            return max(0.0, min(1.0, (level - a) / dv)) if abs(dv) > 1e-9 else 0.5

        if edge == 0:
            row_t, col_t = float(row), col + _t(v0, v1)
        elif edge == 1:
            row_t, col_t = row + _t(v1, v2), float(col + 1)
        elif edge == 2:
            row_t, col_t = float(row + 1), col + 1 - _t(v2, v3)
        else:
            row_t, col_t = row + 1 - _t(v3, v0), float(col)

        lat_t = row_t / (dem.rows - 1)
        lon_t = col_t / (dem.cols - 1)
        lat = dem.bbox.min_lat + (dem.bbox.max_lat - dem.bbox.min_lat) * lat_t
        lon = dem.bbox.min_lon + (dem.bbox.max_lon - dem.bbox.min_lon) * lon_t
        z = (level - min_elev) * height_scale + z_offset
        return projector.project(lat, lon, z)


class PolygonFillBuilder:
    """Build flat filled polygon meshes from closed OSM ways (land use areas)."""

    def build(
        self,
        data: GeoMapData,
        detail_level: str,
        z_offset: float = 0.0,
        z_provider: Callable[[float, float], float] | None = None,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[int, int]],
        list[tuple],
    ]:
        projector = BboxProjector(data.bbox, detail_level)
        all_verts: list[tuple[float, float, float]] = []
        all_faces: list[tuple] = []
        vi = 0
        for way in data.ways:
            geom = way.geometry
            if len(geom) < 3:
                continue
            if (abs(geom[0].lat - geom[-1].lat) < 1e-9
                    and abs(geom[0].lon - geom[-1].lon) < 1e-9):
                geom = geom[:-1]
            if len(geom) < 3:
                continue
            face_verts = []
            for node in geom:
                z = z_provider(node.lat, node.lon) if z_provider else 0.0
                x, y, _ = projector.project(node.lat, node.lon, z + z_offset)
                face_verts.append((x, y, z + z_offset))
            n = len(face_verts)
            all_verts.extend(face_verts)
            all_faces.append(tuple(range(vi, vi + n)))
            vi += n
        return all_verts, [], all_faces
