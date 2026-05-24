import math

from .blender_scene import link_to_geomap_collection, material_named
from .mesh_builder import BboxProjector
from .models import BoundingBox
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread
from .weather import WeatherPoint

_COLLECTION = "Weather"


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
        c = (condition or "").lower()
        if "storm" in c or "thunder" in c:
            return WeatherSymbolLibrary._storm(x, y, z, size)
        if "snow" in c:
            return WeatherSymbolLibrary._snow(x, y, z, size)
        if "rain" in c or "shower" in c or "drizzle" in c:
            return WeatherSymbolLibrary._rain(x, y, z, size)
        if "cloud" in c or "fog" in c or "mist" in c:
            return WeatherSymbolLibrary._cloud(x, y, z, size)
        return WeatherSymbolLibrary._sun(x, y, z, size)

    @staticmethod
    def material_name(condition: str) -> str:
        c = (condition or "").lower()
        if "storm" in c or "thunder" in c:
            return "GeoMap_WeatherSymbol_Storm"
        if "snow" in c:
            return "GeoMap_WeatherSymbol_Snow"
        if "rain" in c or "shower" in c or "drizzle" in c:
            return "GeoMap_WeatherSymbol_Rain"
        if "cloud" in c or "fog" in c or "mist" in c:
            return "GeoMap_WeatherSymbol_Cloud"
        return "GeoMap_WeatherSymbol_Clear"

    @staticmethod
    def color(condition: str) -> tuple:
        c = (condition or "").lower()
        if "storm" in c or "thunder" in c:
            return (0.95, 0.80, 0.18, 1.0)
        if "snow" in c:
            return (0.86, 0.94, 1.00, 1.0)
        if "rain" in c or "shower" in c or "drizzle" in c:
            return (0.25, 0.55, 1.00, 1.0)
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
        return verts, faces

    @staticmethod
    def _cloud(x: float, y: float, z: float, size: float):
        verts: list[tuple] = []
        faces: list[tuple] = []
        for cx, cy, r in [
            (x - size * 0.26, y - size * 0.02, size * 0.30),
            (x, y + size * 0.12, size * 0.38),
            (x + size * 0.30, y - size * 0.04, size * 0.30),
        ]:
            _append_face(verts, faces, _poly(cx, cy, z, r, 12))
        _append_face(verts, faces, _rect(x, y - size * 0.20, z, size * 0.95, size * 0.32))
        return verts, faces

    @staticmethod
    def _rain(x: float, y: float, z: float, size: float):
        verts, faces = WeatherSymbolLibrary._cloud(x, y + size * 0.14, z, size * 0.82)
        for dx in (-0.25, 0.0, 0.25):
            _append_face(verts, faces, [
                (x + size * dx, y - size * 0.34, z),
                (x + size * (dx + 0.08), y - size * 0.62, z),
                (x + size * (dx - 0.06), y - size * 0.60, z),
            ])
        return verts, faces

    @staticmethod
    def _snow(x: float, y: float, z: float, size: float):
        verts, faces = WeatherSymbolLibrary._cloud(x, y + size * 0.14, z, size * 0.82)
        for dx in (-0.22, 0.12):
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

        show_temp = getattr(settings, "weather_show_temperature", True)
        show_wind = getattr(settings, "weather_show_wind", True)

        created = []
        for idx, pt in enumerate(points):
            x, y, _ = projector.project(pt.lat, pt.lon)
            z = z_off

            symbol = self._create_condition_symbol(context, pt, idx, x, y, z, icon_r)
            if symbol:
                created.append(symbol)

            if show_temp:
                badge = self._create_temp_badge(
                    context, pt, idx, x - icon_r * 1.35, y, z, icon_r * 0.55
                )
                if badge:
                    created.append(badge)

            if show_wind and pt.wind_speed > 0.5:
                arrow = self._create_wind_arrow(
                    context, pt, idx, x + icon_r * 1.6, y, z, arrow_l
                )
                if arrow:
                    created.append(arrow)

        return created

    def _create_condition_symbol(self, context, pt, idx, x, y, z, size):
        import bpy as _bpy
        name = f"Weather_Symbol_{idx:03d}"
        verts, faces = WeatherSymbolLibrary.build(pt.condition, x, y, z, size)
        mesh = _bpy.data.meshes.new(f"{name}_Mesh")
        obj = _bpy.data.objects.new(name, mesh)
        link_to_geomap_collection(context, obj, _COLLECTION)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        mat = material_named(
            WeatherSymbolLibrary.material_name(pt.condition),
            WeatherSymbolLibrary.color(pt.condition),
        )
        obj.data.materials.append(mat)
        obj["geomap_type"] = "weather_symbol"
        obj["weather_condition"] = pt.condition
        return obj

    def _create_temp_badge(self, context, pt, idx, x, y, z, r):
        import bpy as _bpy
        name = f"Weather_TempSymbol_{idx:03d}"
        color = _temp_color(pt.temperature)
        mat = material_named(f"GeoMap_WeatherTemp_{int(pt.temperature + 50)}", color)
        verts = []
        faces = []
        _append_face(verts, faces, _rect(x, y - r * 0.10, z, r * 0.34, r * 1.15))
        _append_face(verts, faces, _poly(x, y - r * 0.78, z, r * 0.28, 12))
        mesh = _bpy.data.meshes.new(f"{name}_Mesh")
        obj = _bpy.data.objects.new(name, mesh)
        link_to_geomap_collection(context, obj, _COLLECTION)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj.data.materials.append(mat)
        obj["geomap_type"] = "weather_temperature_symbol"
        obj["weather_temp"] = round(pt.temperature, 1)
        obj["weather_condition"] = pt.condition
        return obj

    def _create_wind_arrow(self, context, pt, idx, x, y, z, length):
        import bpy as _bpy
        name = f"Weather_Wind_{idx:03d}"
        verts, faces = _arrow_mesh(x, y, z, length, pt.wind_dir)
        mesh = _bpy.data.meshes.new(f"{name}_Mesh")
        obj = _bpy.data.objects.new(name, mesh)
        link_to_geomap_collection(context, obj, _COLLECTION)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        color = _wind_color(pt.wind_speed)
        speed_bucket = int(pt.wind_speed / 10) * 10
        mat = material_named(f"GeoMap_WeatherWind_{speed_bucket}", color)
        obj.data.materials.append(mat)
        obj["geomap_type"] = "weather_wind"
        obj["weather_wind_speed"] = round(pt.wind_speed, 1)
        obj["weather_wind_dir"] = round(pt.wind_dir, 1)
        return obj
