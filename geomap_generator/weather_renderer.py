import math

from .blender_scene import link_to_geomap_collection, material_named
from .mesh_builder import BboxProjector
from .models import BoundingBox
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread
from .weather import WeatherPoint

_COLLECTION = "Weather"
_ICON_COLLECTION = "WeatherIcons"
_ICON_TEMPLATE_SIZE = 1.0


def _temp_color(t: float) -> tuple:
    if t <= 0:
        return (0.10, 0.25, 0.85, 1.0)   # blue
    if t <= 10:
        return (0.15, 0.55, 0.85, 1.0)   # cyan-blue
    if t <= 20:
        return (0.20, 0.75, 0.30, 1.0)   # green
    if t <= 30:
        return (0.90, 0.75, 0.10, 1.0)   # yellow
    return (0.90, 0.18, 0.10, 1.0)       # red


def _wind_color(speed: float) -> tuple:
    if speed < 20:
        return (0.55, 0.70, 1.00, 1.0)   # light blue
    if speed < 50:
        return (0.30, 0.40, 0.90, 1.0)   # medium blue
    return (0.15, 0.10, 0.70, 1.0)       # dark blue (strong)


def _display_temperature(value_c: float, unit: str) -> tuple[int, str]:
    if unit == "FAHRENHEIT":
        return int(round((value_c * 9.0 / 5.0) + 32.0)), "°F"
    return int(round(value_c)), "°C"


def _oriented_vertices(
    verts: list[tuple],
    cx: float,
    cy: float,
    cz: float,
    orientation: str,
    z_rotation_deg: float,
) -> list[tuple]:
    radians = math.radians(z_rotation_deg)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    vertical = orientation == "VERTICAL"
    oriented = []
    for vx, vy, vz in verts:
        dx = vx - cx
        dy = vy - cy
        dz = vz - cz
        if vertical:
            oriented.append((cx + dx * cos_a, cy + dx * sin_a, cz + dy + dz))
        else:
            oriented.append((
                cx + dx * cos_a - dy * sin_a,
                cy + dx * sin_a + dy * cos_a,
                cz + dz,
            ))
    return oriented


def _octagon(cx: float, cy: float, cz: float, r: float) -> list[tuple]:
    return [
        (cx + r * math.cos(math.pi * 2 * i / 8),
         cy + r * math.sin(math.pi * 2 * i / 8),
         cz)
        for i in range(8)
    ]


def _poly(cx: float, cy: float, cz: float, r: float, count: int) -> list[tuple]:
    return [
        (cx + r * math.cos(math.pi * 2 * i / count),
         cy + r * math.sin(math.pi * 2 * i / count),
         cz)
        for i in range(count)
    ]


def _append_face(
    verts: list[tuple], faces: list[tuple], face_verts: list[tuple],
) -> None:
    start = len(verts)
    verts.extend(face_verts)
    faces.append(tuple(range(start, start + len(face_verts))))


def _face_normal_z(verts: list[tuple], face: tuple[int, ...]) -> float:
    if len(face) < 3:
        return 0.0
    normal_z = 0.0
    for i, idx in enumerate(face):
        x1, y1, _z1 = verts[idx]
        x2, y2, _z2 = verts[face[(i + 1) % len(face)]]
        normal_z += (x1 * y2) - (x2 * y1)
    return normal_z


def _faces_oriented_positive_z(
    verts: list[tuple], faces: list[tuple],
) -> list[tuple]:
    oriented = []
    for face in faces:
        oriented.append(tuple(reversed(face)) if _face_normal_z(verts, face) < 0.0 else face)
    return oriented


def _rect(cx: float, cy: float, cz: float, w: float, h: float) -> list[tuple]:
    hw = w / 2
    hh = h / 2
    return [
        (cx - hw, cy - hh, cz),
        (cx + hw, cy - hh, cz),
        (cx + hw, cy + hh, cz),
        (cx - hw, cy + hh, cz),
    ]


class WeatherSymbolLibrary:
    """Mesh symbol library for weather conditions on the map plane."""

    @staticmethod
    def build(condition: str, x: float, y: float, z: float, size: float):
        key = WeatherSymbolLibrary.icon_key(condition)
        c = key.lower()
        if "storm" in c or "thunder" in c:
            return WeatherSymbolLibrary._storm(x, y, z, size)
        if c.startswith("snow"):
            intensity = 1 if "light" in c else (3 if "heavy" in c else 2)
            return WeatherSymbolLibrary._snow(x, y, z, size, intensity)
        if c in {"drizzle", "rainlight", "rain", "rainheavy", "shower"}:
            intensity = {
                "drizzle": 1,
                "rainlight": 1,
                "rain": 2,
                "rainheavy": 3,
                "shower": 3,
            }.get(c, 2)
            return WeatherSymbolLibrary._rain(x, y, z, size, intensity)
        if c == "fog":
            return WeatherSymbolLibrary._fog(x, y, z, size)
        if c.startswith("cloud"):
            return WeatherSymbolLibrary._cloud(x, y, z, size, key)
        if c.startswith("wind"):
            return WeatherSymbolLibrary._wind(x, y, z, size, c)
        return WeatherSymbolLibrary._sun(x, y, z, size)

    @staticmethod
    def material_name(condition: str) -> str:
        c = WeatherSymbolLibrary.icon_key(condition).lower()
        if "storm" in c or "thunder" in c:
            return "GeoMap_WeatherSymbol_Storm"
        if "snow" in c:
            return "GeoMap_WeatherSymbol_Snow"
        if "rain" in c or "shower" in c or "drizzle" in c:
            return "GeoMap_WeatherSymbol_Rain"
        if "wind" in c:
            return "GeoMap_WeatherSymbol_Wind"
        if "cloud" in c or "fog" in c or "mist" in c:
            return "GeoMap_WeatherSymbol_Cloud"
        return "GeoMap_WeatherSymbol_Clear"

    @staticmethod
    def color(condition: str) -> tuple:
        c = WeatherSymbolLibrary.icon_key(condition).lower()
        if "storm" in c or "thunder" in c:
            return (0.95, 0.80, 0.18, 1.0)
        if "snow" in c:
            return (0.86, 0.94, 1.00, 1.0)
        if "rain" in c or "shower" in c or "drizzle" in c:
            return (0.25, 0.55, 1.00, 1.0)
        if "wind" in c:
            return (0.55, 0.70, 1.00, 1.0)
        if "cloud" in c or "fog" in c or "mist" in c:
            return (0.72, 0.76, 0.80, 1.0)
        return (1.00, 0.78, 0.18, 1.0)

    @staticmethod
    def _sun(x: float, y: float, z: float, size: float):
        verts: list[tuple] = []
        faces: list[tuple] = []
        _append_face(verts, faces, _poly(x, y, z, size * 0.34, 16))
        for i in range(8):
            angle = math.pi * 2 * i / 8
            dx = math.cos(angle)
            dy = math.sin(angle)
            px = -dy
            py = dx
            inner = size * 0.50
            outer = size * 0.78
            width = size * 0.10
            _append_face(verts, faces, [
                (x + dx * inner + px * width, y + dy * inner + py * width, z),
                (x + dx * outer, y + dy * outer, z),
                (x + dx * inner - px * width, y + dy * inner - py * width, z),
            ])
        return verts, _faces_oriented_positive_z(verts, faces)

    @staticmethod
    def icon_key(condition: str) -> str:
        c = (condition or "").lower()
        direct = {
            "clear": "Clear",
            "cloudfew": "CloudFew",
            "cloudscattered": "CloudScattered",
            "cloudovercast": "CloudOvercast",
            "fog": "Fog",
            "drizzle": "Drizzle",
            "rainlight": "RainLight",
            "rain": "Rain",
            "rainheavy": "RainHeavy",
            "shower": "Shower",
            "snowlight": "SnowLight",
            "snow": "Snow",
            "snowheavy": "SnowHeavy",
            "storm": "Storm",
            "windlight": "WindLight",
            "windmoderate": "WindModerate",
            "windstrong": "WindStrong",
        }
        if c in direct:
            return direct[c]
        if "storm" in c or "thunder" in c:
            return "Storm"
        if "heavy snow" in c or "blizzard" in c:
            return "SnowHeavy"
        if "light snow" in c or "sleet" in c:
            return "SnowLight"
        if "snow" in c:
            return "Snow"
        if "shower" in c:
            return "Shower"
        if "heavy rain" in c or "downpour" in c:
            return "RainHeavy"
        if "light rain" in c:
            return "RainLight"
        if "drizzle" in c:
            return "Drizzle"
        if "rain" in c:
            return "Rain"
        if "fog" in c or "mist" in c or "haze" in c:
            return "Fog"
        if "few" in c or "partly" in c:
            return "CloudFew"
        if "scattered" in c or "broken" in c:
            return "CloudScattered"
        if "overcast" in c or "cloudy" in c or "cloud" in c:
            return "CloudOvercast"
        return "Clear"

    @staticmethod
    def icon_key_for_point(point: WeatherPoint) -> str:
        c = (point.condition or "").lower()
        precip = float(getattr(point, "precipitation", 0.0) or 0.0)
        key = WeatherSymbolLibrary.icon_key(c)
        if key == "Rain" and precip >= 8.0:
            return "RainHeavy"
        if key == "Rain" and 0.0 < precip < 2.5:
            return "RainLight"
        if key == "Snow" and precip >= 8.0:
            return "SnowHeavy"
        if key == "Snow" and 0.0 < precip < 2.5:
            return "SnowLight"
        return key

    @staticmethod
    def wind_icon_key(speed: float) -> str:
        if speed < 20:
            return "WindLight"
        if speed < 50:
            return "WindModerate"
        return "WindStrong"

    @staticmethod
    def _cloud(x: float, y: float, z: float, size: float, key: str = "CloudOvercast"):
        verts: list[tuple] = []
        faces: list[tuple] = []
        for cx, cy, r in WeatherSymbolLibrary._cloud_parts(x, y, size, key):
            _append_face(verts, faces, _poly(cx, cy, z, r, 12))
        _append_face(verts, faces, _rect(x, y - size * 0.20, z, size * 0.95, size * 0.32))
        return verts, _faces_oriented_positive_z(verts, faces)

    @staticmethod
    def _cloud_parts(x: float, y: float, size: float, key: str) -> list[tuple]:
        if key == "CloudFew":
            return [(x, y + size * 0.06, size * 0.34)]
        if key == "CloudScattered":
            return [
                (x - size * 0.18, y - size * 0.02, size * 0.28),
                (x + size * 0.16, y + size * 0.08, size * 0.34),
            ]
        return [
            (x - size * 0.26, y - size * 0.02, size * 0.30),
            (x, y + size * 0.12, size * 0.38),
            (x + size * 0.30, y - size * 0.04, size * 0.30),
        ]

    @staticmethod
    def _rain(x: float, y: float, z: float, size: float, intensity: int = 2):
        verts, faces = WeatherSymbolLibrary._cloud(x, y + size * 0.14, z, size * 0.82)
        drops = {
            1: (-0.16, 0.16),
            2: (-0.25, 0.0, 0.25),
            3: (-0.34, -0.16, 0.02, 0.20, 0.38),
        }.get(intensity, (-0.25, 0.0, 0.25))
        for dx in drops:
            _append_face(verts, faces, [
                (x + size * dx, y - size * 0.34, z),
                (x + size * (dx + 0.08), y - size * 0.62, z),
                (x + size * (dx - 0.06), y - size * 0.60, z),
            ])
        return verts, faces

    @staticmethod
    def _snow(x: float, y: float, z: float, size: float, intensity: int = 2):
        verts, faces = WeatherSymbolLibrary._cloud(x, y + size * 0.14, z, size * 0.82)
        flakes = {
            1: (-0.10,),
            2: (-0.22, 0.12),
            3: (-0.32, -0.08, 0.16, 0.36),
        }.get(intensity, (-0.22, 0.12))
        for dx in flakes:
            cx = x + size * dx
            cy = y - size * 0.48
            for angle in (0, math.pi / 3, -math.pi / 3):
                ca = math.cos(angle)
                sa = math.sin(angle)
                l = size * 0.16
                w = size * 0.025
                _append_face(verts, faces, [
                    (cx - ca * l - sa * w, cy - sa * l + ca * w, z),
                    (cx + ca * l - sa * w, cy + sa * l + ca * w, z),
                    (cx + ca * l + sa * w, cy + sa * l - ca * w, z),
                    (cx - ca * l + sa * w, cy - sa * l - ca * w, z),
                ])
        return verts, faces

    @staticmethod
    def _fog(x: float, y: float, z: float, size: float):
        verts: list[tuple] = []
        faces: list[tuple] = []
        for idx, yy in enumerate((0.22, 0.0, -0.22)):
            width = size * (0.80 if idx != 1 else 1.05)
            _append_face(verts, faces, _rect(x, y + size * yy, z, width, size * 0.065))
        return verts, faces

    @staticmethod
    def _wind(x: float, y: float, z: float, size: float, key: str):
        variants = {
            "windlight": (size * 0.78, 1),
            "windmoderate": (size * 0.96, 2),
            "windstrong": (size * 1.15, 3),
        }
        length, barb_count = variants.get(key, (size * 0.96, 2))
        return WeatherSymbolLibrary._wind_barb(x, y, z, length, barb_count)

    @staticmethod
    def _wind_barb(
        x: float, y: float, z: float, length: float, barb_count: int,
    ):
        """Return a flat wind arrow template pointing along local +X."""
        verts: list[tuple] = []
        faces: list[tuple] = []
        shaft_w = length * 0.07
        head_len = length * 0.18
        head_w = length * 0.22

        _append_face(
            verts,
            faces,
            [
                (x - length * 0.50, y - shaft_w * 0.5, z),
                (x + length * 0.34, y - shaft_w * 0.5, z),
                (x + length * 0.34, y + shaft_w * 0.5, z),
                (x - length * 0.50, y + shaft_w * 0.5, z),
            ],
        )
        _append_face(
            verts,
            faces,
            [
                (x + length * 0.50, y, z),
                (x + length * 0.50 - head_len, y + head_w * 0.5, z),
                (x + length * 0.50 - head_len, y - head_w * 0.5, z),
            ],
        )

        for idx in range(barb_count):
            bx = x + length * (0.22 - idx * 0.17)
            tip_x = bx - length * 0.20
            tip_y = y + length * (0.26 + idx * 0.02)
            _append_face(
                verts,
                faces,
                [
                    (bx, y + shaft_w * 0.55, z),
                    (tip_x, tip_y, z),
                    (tip_x + length * 0.055, tip_y + length * 0.050, z),
                    (bx + length * 0.045, y + shaft_w * 0.55, z),
                ],
            )
        return verts, _faces_oriented_positive_z(verts, faces)

    @staticmethod
    def _storm(x: float, y: float, z: float, size: float):
        verts, faces = WeatherSymbolLibrary._cloud(x, y + size * 0.16, z, size * 0.82)
        _append_face(verts, faces, [
            (x + size * 0.02, y - size * 0.16, z),
            (x - size * 0.14, y - size * 0.50, z),
            (x + size * 0.04, y - size * 0.46, z),
            (x - size * 0.06, y - size * 0.78, z),
            (x + size * 0.22, y - size * 0.34, z),
            (x + size * 0.04, y - size * 0.38, z),
        ])
        return verts, faces


def _arrow_mesh(cx: float, cy: float, cz: float, length: float, wind_dir: float):
    """
    Return (verts, faces) for a flat wind arrow.
    wind_dir: meteorological degrees (0=from N).
    Arrow points in the direction the wind is GOING (opposite of origin direction).
    """
    # met 0 = from N = wind heading south → tip is at -Y, tail at +Y
    rad = math.radians(wind_dir + 180)  # +180 → direction wind is heading
    dx = math.sin(rad)
    dy = math.cos(rad)
    perp_x = -dy
    perp_y = dx

    shaft_w = length * 0.12
    head_w = length * 0.30
    shaft_len = length * 0.55
    head_len = length * 0.45

    # Base of shaft (tail)
    sx = cx - dx * shaft_len
    sy = cy - dy * shaft_len
    # Junction shaft/head
    jx = cx
    jy = cy

    shaft_verts = [
        (sx + perp_x * shaft_w, sy + perp_y * shaft_w, cz),
        (sx - perp_x * shaft_w, sy - perp_y * shaft_w, cz),
        (jx - perp_x * shaft_w, jy - perp_y * shaft_w, cz),
        (jx + perp_x * shaft_w, jy + perp_y * shaft_w, cz),
    ]
    tip_x = cx + dx * head_len
    tip_y = cy + dy * head_len
    head_verts = [
        (tip_x, tip_y, cz),
        (jx + perp_x * head_w, jy + perp_y * head_w, cz),
        (jx - perp_x * head_w, jy - perp_y * head_w, cz),
    ]
    verts = shaft_verts + head_verts
    faces = [(0, 1, 2, 3), (4, 5, 6)]
    return verts, faces


def _wind_heading_yaw(wind_dir: float) -> float:
    """Return yaw degrees for the direction the wind is going on the map plane."""
    radians = math.radians(wind_dir + 180.0)
    dx = math.sin(radians)
    dy = math.cos(radians)
    return math.degrees(math.atan2(dy, dx))


class WeatherRenderer:
    def render(
        self,
        context,
        points: list[WeatherPoint],
        bbox: BoundingBox,
        settings,
        scene_scale: SceneScale,
    ) -> list:
        assert_main_thread()
        detail_level = getattr(settings, "detail_level", "MEDIUM")
        projector = BboxProjector(bbox, detail_level)

        # Scale icons to ~4% of the map's Blender-unit span
        icon_r = scene_scale.map_units * 0.04
        arrow_l = scene_scale.map_units * 0.055
        z_off = scaled_map_value(
            float(getattr(settings, "weather_z_offset", 0.12)),
            scene_scale,
        )
        base_z = self._highest_dem_z(context)

        show_temp = getattr(settings, "weather_show_temperature", True)
        show_temp_range = getattr(settings, "weather_show_temp_range", True)
        show_wind = getattr(settings, "weather_show_wind", True)
        temp_unit = getattr(settings, "weather_unit", "CELSIUS")
        orientation = getattr(settings, "weather_orientation", "HORIZONTAL")
        z_rotation = float(getattr(settings, "weather_z_rotation", 0.0))
        follow_dem = bool(getattr(settings, "weather_follow_dem", False))
        self._ensure_icon_library(context)

        created = []
        for idx, pt in enumerate(points):
            x, y, _ = projector.project(pt.lat, pt.lon)
            surface_z = (
                self._dem_surface_z_at(context, x, y, base_z)
                if follow_dem else base_z
            )
            z = surface_z + z_off

            symbol = self._create_condition_symbol(
                context, pt, idx, x, y, z, icon_r, orientation, z_rotation,
            )
            if symbol:
                symbol["weather_follow_dem"] = follow_dem
                symbol["weather_surface_z"] = round(surface_z, 4)
                created.append(symbol)

            if show_temp:
                label_x = x - icon_r * 1.35
                label_y = y + (icon_r * 0.32 if show_temp_range else 0.0)
                badge = self._create_temp_label(
                    context,
                    pt,
                    idx,
                    label_x,
                    label_y,
                    z,
                    icon_r * 0.55,
                    temp_unit,
                    orientation,
                    z_rotation,
                )
                if badge:
                    badge["weather_follow_dem"] = follow_dem
                    badge["weather_surface_z"] = round(surface_z, 4)
                    created.append(badge)

                if show_temp_range and (pt.temp_min != 0.0 or pt.temp_max != 0.0):
                    range_label = self._create_temp_range_label(
                        context,
                        pt,
                        idx,
                        label_x,
                        y - icon_r * 0.40,
                        z,
                        icon_r * 0.38,
                        temp_unit,
                        orientation,
                        z_rotation,
                    )
                    if range_label:
                        range_label["weather_follow_dem"] = follow_dem
                        range_label["weather_surface_z"] = round(surface_z, 4)
                        created.append(range_label)

            if show_wind and pt.wind_speed > 0.5:
                arrow = self._create_wind_arrow(
                    context,
                    pt,
                    idx,
                    x + icon_r * 1.6,
                    y,
                    z,
                    arrow_l,
                    orientation,
                    z_rotation,
                )
                if arrow:
                    arrow["weather_follow_dem"] = follow_dem
                    arrow["weather_surface_z"] = round(surface_z, 4)
                    created.append(arrow)

        return created

    @staticmethod
    def _highest_dem_z(context) -> float:
        """Return the highest generated DEM vertex in world Z coordinates."""
        import bpy as _bpy

        max_z: float | None = None
        for obj in WeatherRenderer._dem_objects(_bpy):
            mesh = getattr(obj, "data", None)
            vertices = getattr(mesh, "vertices", None)
            if not vertices:
                continue
            matrix = getattr(obj, "matrix_world", None)
            for vertex in vertices:
                try:
                    z = float((matrix @ vertex.co).z) if matrix is not None else float(vertex.co.z)
                except Exception:
                    z = float(getattr(obj.location, "z", 0.0)) + float(vertex.co.z)
                max_z = z if max_z is None else max(max_z, z)
        return max_z if max_z is not None else 0.0

    @staticmethod
    def _dem_surface_z_at(context, x: float, y: float, fallback_z: float) -> float:
        """Return DEM surface Z under a projected map point, or fallback_z."""
        import bpy as _bpy
        from mathutils import Vector

        dem_objects = WeatherRenderer._dem_objects(_bpy)
        if not dem_objects:
            return fallback_z

        best_z: float | None = None
        for obj in dem_objects:
            z = WeatherRenderer._dem_grid_surface_z(obj, x, y)
            if z is not None:
                best_z = z if best_z is None else max(best_z, z)
        if best_z is not None:
            return best_z

        cast_span = max(
            [
                max(float(dim) for dim in getattr(obj, "dimensions", (0.0, 0.0, 0.0)))
                for obj in dem_objects
            ] + [1.0]
        )
        origin_z = fallback_z + cast_span * 2.0 + 10.0
        direction_world = Vector((0.0, 0.0, -1.0))
        for obj in dem_objects:
            try:
                matrix = obj.matrix_world
                inv = matrix.inverted()
                local_origin = inv @ Vector((x, y, origin_z))
                local_direction = (inv.to_3x3() @ direction_world).normalized()
                hit, location, _normal, _face = obj.ray_cast(
                    local_origin, local_direction
                )
            except Exception:
                continue
            if not hit:
                continue
            world_z = float((matrix @ location).z)
            best_z = world_z if best_z is None else max(best_z, world_z)
        return best_z if best_z is not None else fallback_z

    @staticmethod
    def _dem_grid_surface_z(obj, x: float, y: float) -> float | None:
        """Sample original DEM grid vertices using bilinear interpolation."""
        try:
            rows = int(obj.get("geomap_dem_rows", 0) or 0)
            cols = int(obj.get("geomap_dem_cols", 0) or 0)
        except (TypeError, ValueError):
            return None
        if rows < 2 or cols < 2:
            return None

        mesh = getattr(obj, "data", None)
        vertices = getattr(mesh, "vertices", None)
        if vertices is None or len(vertices) < rows * cols:
            return None

        matrix = getattr(obj, "matrix_world", None)

        def world_vertex(index: int):
            co = vertices[index].co
            return matrix @ co if matrix is not None else co

        v00 = world_vertex(0)
        v0c = world_vertex(cols - 1)
        vr0 = world_vertex((rows - 1) * cols)

        min_x, max_x = sorted((float(v00.x), float(v0c.x)))
        min_y, max_y = sorted((float(v00.y), float(vr0.y)))
        pad = max(max_x - min_x, max_y - min_y, 1.0) * 0.001
        if x < min_x - pad or x > max_x + pad or y < min_y - pad or y > max_y + pad:
            return None

        col_span = float(v0c.x - v00.x)
        row_span = float(vr0.y - v00.y)
        if abs(col_span) < 1e-9 or abs(row_span) < 1e-9:
            return None

        col_pos = (x - float(v00.x)) / col_span * (cols - 1)
        row_pos = (y - float(v00.y)) / row_span * (rows - 1)
        col_pos = max(0.0, min(float(cols - 1), col_pos))
        row_pos = max(0.0, min(float(rows - 1), row_pos))

        col0 = int(math.floor(col_pos))
        row0 = int(math.floor(row_pos))
        col1 = min(cols - 1, col0 + 1)
        row1 = min(rows - 1, row0 + 1)
        col_t = col_pos - col0
        row_t = row_pos - row0

        def z_at(row: int, col: int) -> float:
            return float(world_vertex(row * cols + col).z)

        z00 = z_at(row0, col0)
        z01 = z_at(row0, col1)
        z10 = z_at(row1, col0)
        z11 = z_at(row1, col1)
        top = z00 + (z01 - z00) * col_t
        bottom = z10 + (z11 - z10) * col_t
        return top + (bottom - top) * row_t

    @staticmethod
    def _dem_objects(bpy_module) -> list:
        root = bpy_module.data.collections.get("GeoMap")
        collections = list(root.children) if root is not None else []
        objects = []
        for collection in collections:
            objects.extend(list(getattr(collection, "objects", []) or []))
        if not objects:
            objects = list(bpy_module.data.objects)
        return [
            obj for obj in objects
            if obj.get("geomap_layer") == "dem" and getattr(obj, "type", "") == "MESH"
        ]

    def _create_condition_symbol(
        self, context, pt, idx, x, y, z, size, orientation, z_rotation,
    ):
        name = f"Weather_Symbol_{idx:03d}"
        icon_key = WeatherSymbolLibrary.icon_key_for_point(pt)
        obj = self._create_icon_instance(
            context,
            icon_key,
            name,
            (x, y, z),
            (size, size, size),
            self._weather_rotation(orientation, z_rotation),
        )
        obj["geomap_type"] = "weather_symbol"
        obj["weather_icon_template"] = self._icon_name(icon_key)
        obj["weather_icon_key"] = icon_key
        obj["weather_orientation"] = orientation
        obj["weather_z_rotation"] = z_rotation
        obj["weather_condition"] = pt.condition
        return obj

    @staticmethod
    def _weather_rotation(orientation: str, z_rotation: float):
        if orientation == "VERTICAL":
            return (math.radians(90.0), 0.0, math.radians(z_rotation))
        return (0.0, 0.0, math.radians(z_rotation))

    @staticmethod
    def _icon_name(icon_key: str) -> str:
        return f"WeatherIcon_{icon_key}"

    @staticmethod
    def _icon_template(context, icon_key: str):
        import bpy as _bpy
        WeatherRenderer._ensure_icon_library(context)
        return _bpy.data.objects.get(WeatherRenderer._icon_name(icon_key))

    @staticmethod
    def _icon_collection_name(icon_key: str) -> str:
        return f"WeatherIconCollection_{icon_key}"

    @staticmethod
    def _icon_collection(context, icon_key: str):
        import bpy as _bpy
        WeatherRenderer._ensure_icon_library(context)
        return _bpy.data.collections.get(WeatherRenderer._icon_collection_name(icon_key))

    @staticmethod
    def _create_icon_instance(
        context,
        icon_key: str,
        name: str,
        location: tuple[float, float, float],
        scale: tuple[float, float, float],
        rotation,
    ):
        import bpy as _bpy

        collection = WeatherRenderer._icon_collection(context, icon_key)
        if collection is None:
            raise RuntimeError(f"Weather icon collection missing: {icon_key}")
        obj = _bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 0.05
        obj.instance_type = "COLLECTION"
        obj.instance_collection = collection
        obj.location = location
        obj.scale = scale
        obj.rotation_euler = rotation
        link_to_geomap_collection(context, obj, _COLLECTION)
        return obj

    @staticmethod
    def _ensure_icon_library(context) -> None:
        import bpy as _bpy

        collection = _bpy.data.collections.get(_ICON_COLLECTION)
        if collection is None:
            collection = _bpy.data.collections.new(_ICON_COLLECTION)
            context.scene.collection.children.link(collection)
        collection.hide_viewport = True
        collection.hide_render = True
        try:
            collection.color_tag = "COLOR_01"
        except Exception:
            pass

        for key in [
            "Clear",
            "CloudFew",
            "CloudScattered",
            "CloudOvercast",
            "Fog",
            "Drizzle",
            "RainLight",
            "Rain",
            "RainHeavy",
            "Shower",
            "SnowLight",
            "Snow",
            "SnowHeavy",
            "Storm",
            "WindLight",
            "WindModerate",
            "WindStrong",
        ]:
            condition = key
            name = WeatherRenderer._icon_name(key)
            obj = _bpy.data.objects.get(name)
            if obj is None:
                verts, faces = WeatherSymbolLibrary.build(
                    condition, 0.0, 0.0, 0.0, _ICON_TEMPLATE_SIZE,
                )
                faces = _faces_oriented_positive_z(verts, faces)
                mesh = _bpy.data.meshes.new(f"{name}_Mesh")
                obj = _bpy.data.objects.new(name, mesh)
                collection.objects.link(obj)
                mesh.from_pydata(verts, [], faces)
                mesh.update()
                mat = material_named(
                    WeatherSymbolLibrary.material_name(condition),
                    WeatherSymbolLibrary.color(condition),
                )
                obj.data.materials.append(mat)
            elif WeatherRenderer._should_upgrade_wind_template(obj, key):
                WeatherRenderer._replace_template_mesh(obj, condition)
            obj.hide_viewport = False
            obj.hide_render = False
            obj["geomap_type"] = "weather_icon_template"
            obj["weather_icon_key"] = key
            obj["weather_condition"] = condition
            if key.startswith("Wind"):
                obj["weather_icon_version"] = 2
            WeatherRenderer._fix_template_normals(obj)
            if collection.objects.get(obj.name) is None:
                collection.objects.link(obj)

            sub_name = WeatherRenderer._icon_collection_name(key)
            sub_collection = _bpy.data.collections.get(sub_name)
            if sub_collection is None:
                sub_collection = _bpy.data.collections.new(sub_name)
            if collection.children.get(sub_name) is None:
                collection.children.link(sub_collection)
            sub_collection.hide_viewport = False
            sub_collection.hide_render = False
            try:
                sub_collection.color_tag = "COLOR_01"
            except Exception:
                pass
            if sub_collection.objects.get(obj.name) is None:
                sub_collection.objects.link(obj)

    @staticmethod
    def _should_upgrade_wind_template(obj, key: str) -> bool:
        if not key.startswith("Wind"):
            return False
        try:
            if int(obj.get("weather_icon_version", 0) or 0) >= 2:
                return False
        except (TypeError, ValueError):
            pass
        if obj.get("geomap_type") != "weather_icon_template":
            return False
        if len(getattr(obj, "modifiers", []) or []) > 0:
            return False
        mesh = getattr(obj, "data", None)
        vertices = getattr(mesh, "vertices", None)
        return bool(vertices is not None and len(vertices) <= 8)

    @staticmethod
    def _replace_template_mesh(obj, condition: str) -> None:
        import bpy as _bpy

        name = obj.name
        verts, faces = WeatherSymbolLibrary.build(
            condition, 0.0, 0.0, 0.0, _ICON_TEMPLATE_SIZE,
        )
        faces = _faces_oriented_positive_z(verts, faces)
        mesh = _bpy.data.meshes.new(f"{name}_Mesh")
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        mat = material_named(
            WeatherSymbolLibrary.material_name(condition),
            WeatherSymbolLibrary.color(condition),
        )
        mesh.materials.append(mat)

        old_mesh = getattr(obj, "data", None)
        obj.data = mesh
        if old_mesh is not None and old_mesh.users == 0:
            _bpy.data.meshes.remove(old_mesh)

    @staticmethod
    def _fix_template_normals(obj) -> None:
        mesh = getattr(obj, "data", None)
        if mesh is None or getattr(obj, "type", "") != "MESH":
            return
        try:
            import bmesh
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            for face in bm.faces:
                if face.normal.z < 0.0:
                    face.normal_flip()
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()
        except Exception:
            pass

    def _create_temp_label(
        self, context, pt, idx, x, y, z, r, unit, orientation, z_rotation,
    ):
        import bpy as _bpy
        name = f"Weather_TempLabel_{idx:03d}"
        display_value, suffix = _display_temperature(pt.temperature, unit)
        color = _temp_color(pt.temperature)
        mat = material_named(f"GeoMap_WeatherTemp_{int(pt.temperature + 50)}", color)
        curve = _bpy.data.curves.new(f"{name}_Curve", "FONT")
        curve.body = f"{display_value}{suffix}"
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        curve.size = max(r * 0.82, 0.01)
        obj = _bpy.data.objects.new(name, curve)
        obj.location = (x, y, z)
        if orientation == "VERTICAL":
            obj.rotation_euler = (math.radians(90.0), 0.0, math.radians(z_rotation))
        else:
            obj.rotation_euler = (0.0, 0.0, math.radians(z_rotation))
        link_to_geomap_collection(context, obj, _COLLECTION)
        curve.materials.append(mat)
        obj["geomap_type"] = "weather_temperature_label"
        obj["weather_temp_c"] = round(pt.temperature, 1)
        obj["weather_temp_display"] = f"{display_value}{suffix}"
        obj["weather_temp_unit"] = unit
        obj["weather_orientation"] = orientation
        obj["weather_z_rotation"] = z_rotation
        obj["weather_condition"] = pt.condition
        return obj

    def _create_temp_range_label(
        self, context, pt, idx, x, y, z, r, unit, orientation, z_rotation,
    ):
        import bpy as _bpy
        name = f"Weather_TempRange_{idx:03d}"
        lo, suffix = _display_temperature(pt.temp_min, unit)
        hi, _ = _display_temperature(pt.temp_max, unit)
        text = f"{lo}/{hi}{suffix}"
        color = (0.65, 0.65, 0.68, 1.0)
        mat = material_named(f"GeoMap_WeatherTempRange_{int(pt.temp_min + 50)}_{int(pt.temp_max + 50)}", color)
        curve = _bpy.data.curves.new(f"{name}_Curve", "FONT")
        curve.body = text
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        curve.size = max(r * 0.82, 0.01)
        obj = _bpy.data.objects.new(name, curve)
        obj.location = (x, y, z)
        if orientation == "VERTICAL":
            obj.rotation_euler = (math.radians(90.0), 0.0, math.radians(z_rotation))
        else:
            obj.rotation_euler = (0.0, 0.0, math.radians(z_rotation))
        link_to_geomap_collection(context, obj, _COLLECTION)
        curve.materials.append(mat)
        obj["geomap_type"] = "weather_temp_range_label"
        obj["weather_temp_min_c"] = round(pt.temp_min, 1)
        obj["weather_temp_max_c"] = round(pt.temp_max, 1)
        obj["weather_temp_range_display"] = text
        obj["weather_temp_unit"] = unit
        obj["weather_orientation"] = orientation
        obj["weather_z_rotation"] = z_rotation
        return obj

    def _create_wind_arrow(
        self, context, pt, idx, x, y, z, length, orientation, z_rotation,
    ):
        name = f"Weather_Wind_{idx:03d}"
        icon_key = WeatherSymbolLibrary.wind_icon_key(pt.wind_speed)
        yaw = _wind_heading_yaw(pt.wind_dir) + z_rotation
        obj = self._create_icon_instance(
            context,
            icon_key,
            name,
            (x, y, z),
            (length, length, length),
            self._weather_rotation(orientation, yaw),
        )
        obj["geomap_type"] = "weather_wind"
        obj["weather_icon_template"] = self._icon_name(icon_key)
        obj["weather_icon_key"] = icon_key
        obj["weather_wind_speed"] = round(pt.wind_speed, 1)
        obj["weather_wind_dir"] = round(pt.wind_dir, 1)
        obj["weather_orientation"] = orientation
        obj["weather_z_rotation"] = z_rotation
        obj["weather_wind_yaw"] = round(yaw, 1)
        return obj
