import math

from .blender_scene import link_to_geomap_collection, material_named
from .mesh_builder import BboxProjector
from .models import BoundingBox
from .scene_units import SceneScale
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
        text_sz = scene_scale.map_units * 0.030
        z_off = scene_scale.map_units * 0.005

        show_temp = getattr(settings, "weather_show_temperature", True)
        show_wind = getattr(settings, "weather_show_wind", True)

        created = []
        for idx, pt in enumerate(points):
            x, y, _ = projector.project(pt.lat, pt.lon)
            z = z_off

            if show_temp:
                badge = self._create_temp_badge(context, pt, idx, x, y, z, icon_r)
                if badge:
                    created.append(badge)
                label = self._create_temp_label(context, pt, idx, x, y, z, text_sz)
                if label:
                    created.append(label)

            if show_wind and pt.wind_speed > 0.5:
                arrow = self._create_wind_arrow(
                    context, pt, idx, x + icon_r * 1.6, y, z, arrow_l
                )
                if arrow:
                    created.append(arrow)

        return created

    def _create_temp_badge(self, context, pt, idx, x, y, z, r):
        import bpy as _bpy
        name = f"Weather_Badge_{idx:03d}"
        color = _temp_color(pt.temperature)
        mat = material_named(f"GeoMap_WeatherTemp_{int(pt.temperature + 50)}", color)
        verts = _octagon(x, y, z, r)
        mesh = _bpy.data.meshes.new(f"{name}_Mesh")
        obj = _bpy.data.objects.new(name, mesh)
        link_to_geomap_collection(context, obj, _COLLECTION)
        mesh.from_pydata(verts, [], [tuple(range(8))])
        mesh.update()
        obj.data.materials.append(mat)
        obj["geomap_type"] = "weather_badge"
        obj["weather_temp"] = round(pt.temperature, 1)
        obj["weather_condition"] = pt.condition
        return obj

    def _create_temp_label(self, context, pt, idx, x, y, z, size):
        import bpy as _bpy
        name = f"Weather_Temp_{idx:03d}"
        curve = _bpy.data.curves.new(f"{name}_Curve", "FONT")
        curve.body = f"{pt.temperature:.0f}°"
        curve.size = size
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        mat = material_named("GeoMap_WeatherText", (1.0, 1.0, 1.0, 1.0))
        curve.materials.append(mat)
        obj = _bpy.data.objects.new(name, curve)
        obj.location = (x, y, z + size * 0.05)
        link_to_geomap_collection(context, obj, _COLLECTION)
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
