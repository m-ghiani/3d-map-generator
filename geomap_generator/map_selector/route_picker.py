import math
import os

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from .bbox_drawer import draw_confirmed_bbox, draw_tiles
from .tile_loader import (
    lat_lon_to_screen,
    lat_lon_to_world_px,
    pop_failed,
    pop_ready,
    request_tile,
    screen_to_lat_lon,
    start_worker,
    tile_path,
    visible_tiles,
    world_px_to_lat_lon,
)


def _get_cache_dir() -> str:
    try:
        return bpy.utils.user_resource("CACHE", path="geomap_generator/map_tiles", create=True)
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "geomap_map_tiles")


def _load_texture(path: str):
    import gpu
    img = bpy.data.images.load(path)
    try:
        tex = gpu.texture.from_image(img)
    finally:
        bpy.data.images.remove(img)
    return tex


def _draw_marker(
    state,
    lat: float,
    lon: float,
    color: tuple,
    radius: float = 10.0,
) -> None:
    import gpu
    from gpu_extras.batch import batch_for_shader

    sw, sh = state.screen_w, state.screen_h
    x, y = lat_lon_to_screen(
        lat, lon, state.center_lat, state.center_lon, state.zoom, sw, sh
    )
    steps = 24
    ring = [
        (x + radius * math.cos(2 * math.pi * i / steps),
         y + radius * math.sin(2 * math.pi * i / steps))
        for i in range(steps + 1)
    ]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (*color[:3], 0.35))
    batch_for_shader(shader, "TRI_FAN", {"pos": [(x, y)] + ring[:steps]}).draw(shader)
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINE_STRIP", {"pos": ring}).draw(shader)
    cs = radius * 0.55
    batch_for_shader(shader, "LINES", {
        "pos": [(x - cs, y), (x + cs, y), (x, y - cs), (x, y + cs)],
    }).draw(shader)
    gpu.state.blend_set("NONE")


def _draw_hud(state, target: str, point) -> None:
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    sw, sh = state.screen_w, state.screen_h
    label = "Start" if target == "START" else "End"
    lines = [
        f"Placing: {label} point  |  Click: place  |  Scroll: zoom  |  Middle drag: pan",
        "Enter: confirm   |   ESC: cancel",
        (f"{label}: {point[0]:.5f}, {point[1]:.5f}" if point
         else f"Click on the map to place the {label.lower()} point"),
    ]
    h = 14 + len(lines) * 22 + 14
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.65))
    batch_for_shader(shader, "TRI_FAN", {
        "pos": [(0, sh - h), (sw, sh - h), (sw, sh), (0, sh)],
    }).draw(shader)
    gpu.state.blend_set("NONE")

    font_id = 0
    blf.size(font_id, 14)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    for i, line in enumerate(lines):
        blf.position(font_id, 10, sh - 22 - i * 22, 0)
        blf.draw(font_id, line)
    blf.size(font_id, 12)
    blf.color(font_id, 0.85, 0.85, 0.85, 0.9)
    blf.position(font_id, 10, 8, 0)
    blf.draw(font_id, f"Zoom: {state.zoom}   Center: {state.center_lat:.4f}, {state.center_lon:.4f}")


class GeoMapRoutePickerOperator(Operator):
    bl_idname = "geomap.pick_route_point"
    bl_label = "Pick Route Point on Map"
    bl_description = "Click on the interactive map to set a route start/end point"

    target: EnumProperty(
        items=[("START", "Start", ""), ("END", "End", "")],
        default="START",
    )

    _draw_handle = None
    _state = None
    _cache_dir: str = ""
    _target: str = "START"
    _point = None  # (lat, lon) currently placed

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        from .map_state import MapViewState

        props = context.scene.geomap_props
        state = MapViewState()

        # Load confirmed map bbox from the GeoMap collection
        map_bbox = self._read_map_bbox()
        if map_bbox:
            la1, lo1, la2, lo2 = map_bbox
            state.bbox = map_bbox
            state.center_lat = (la1 + la2) / 2.0
            state.center_lon = (lo1 + lo2) / 2.0
            state.zoom = self._zoom_for_bbox(la1, lo1, la2, lo2)
        elif props.latitude != 0.0 or props.longitude != 0.0:
            state.center_lat = (props.latitude + props.latitude2) / 2.0
            state.center_lon = (props.longitude + props.longitude2) / 2.0
            state.zoom = 10

        cache = _get_cache_dir()
        GeoMapRoutePickerOperator._state = state
        GeoMapRoutePickerOperator._cache_dir = cache
        GeoMapRoutePickerOperator._target = self.target
        # Pre-fill with existing value
        if self.target == "START" and props.route_lat1 != 0.0:
            GeoMapRoutePickerOperator._point = (props.route_lat1, props.route_lon1)
        elif self.target == "END" and props.route_lat2 != 0.0:
            GeoMapRoutePickerOperator._point = (props.route_lat2, props.route_lon2)
        else:
            GeoMapRoutePickerOperator._point = None

        start_worker(cache)
        GeoMapRoutePickerOperator._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            GeoMapRoutePickerOperator._draw_callback, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    @staticmethod
    def _draw_callback():
        state = GeoMapRoutePickerOperator._state
        if state is None:
            return

        region = bpy.context.region
        state.screen_w = region.width
        state.screen_h = region.height
        cache = GeoMapRoutePickerOperator._cache_dir

        for z, x, y in pop_failed():
            state.loading.discard((z, x, y))
            state.failed.add((z, x, y))
        for z, x, y, path in pop_ready():
            key = (z, x, y)
            if key not in state.textures:
                try:
                    state.textures[key] = _load_texture(path)
                except Exception:
                    state.failed.add(key)
            state.loading.discard(key)
        for tx, ty in visible_tiles(state):
            key = (state.zoom, tx, ty)
            if key in state.textures or key in state.loading or key in state.failed:
                continue
            path = tile_path(cache, state.zoom, tx, ty)
            if os.path.exists(path):
                try:
                    state.textures[key] = _load_texture(path)
                except Exception:
                    state.failed.add(key)
            else:
                state.loading.add(key)
                request_tile(state.zoom, tx, ty)

        draw_tiles(state)
        draw_confirmed_bbox(state)

        # Draw the other endpoint from props (for context)
        try:
            props = bpy.context.scene.geomap_props
            target = GeoMapRoutePickerOperator._target
            if target == "END" and props.route_lat1 != 0.0:
                _draw_marker(state, props.route_lat1, props.route_lon1,
                             (0.1, 0.9, 0.2, 1.0), radius=9)
            elif target == "START" and props.route_lat2 != 0.0:
                _draw_marker(state, props.route_lat2, props.route_lon2,
                             (0.9, 0.15, 0.05, 1.0), radius=9)
        except Exception:
            pass

        # Draw currently placed point
        pt = GeoMapRoutePickerOperator._point
        target = GeoMapRoutePickerOperator._target
        if pt:
            color = (0.1, 0.9, 0.2, 1.0) if target == "START" else (0.9, 0.15, 0.05, 1.0)
            _draw_marker(state, pt[0], pt[1], color, radius=12)

        _draw_hud(state, target, pt)

    def modal(self, context, event):
        state = GeoMapRoutePickerOperator._state
        if state is None:
            return {"CANCELLED"}
        if context.area:
            context.area.tag_redraw()

        if event.type == "ESC" and event.value == "PRESS":
            return self._cancel(context)

        if event.type in ("RET", "NUMPAD_ENTER") and event.value == "PRESS":
            return self._confirm(context)

        if event.type == "WHEELUPMOUSE":
            state.zoom = min(18, state.zoom + 1)
            self._clear_tiles(state)
            return {"RUNNING_MODAL"}
        if event.type == "WHEELDOWNMOUSE":
            state.zoom = max(1, state.zoom - 1)
            self._clear_tiles(state)
            return {"RUNNING_MODAL"}

        mx, my = event.mouse_region_x, event.mouse_region_y

        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                state.panning = True
                cx, cy = lat_lon_to_world_px(state.center_lat, state.center_lon, state.zoom)
                state.pan_mouse = (mx, my)
                state.pan_world = (cx, cy)
            elif event.value == "RELEASE":
                state.panning = False
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and state.panning:
            if state.pan_mouse and state.pan_world:
                dx = mx - state.pan_mouse[0]
                dy = my - state.pan_mouse[1]
                wx = state.pan_world[0] - dx
                wy = state.pan_world[1] + dy
                state.center_lat, state.center_lon = world_px_to_lat_lon(wx, wy, state.zoom)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            lat, lon = screen_to_lat_lon(
                mx, my,
                state.center_lat, state.center_lon,
                state.zoom, state.screen_w, state.screen_h,
            )
            GeoMapRoutePickerOperator._point = (lat, lon)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def _confirm(self, context):
        pt = GeoMapRoutePickerOperator._point
        if pt is None:
            self.report({"WARNING"}, "No point placed — click the map first")
            return {"RUNNING_MODAL"}
        props = context.scene.geomap_props
        lat, lon = pt
        target = GeoMapRoutePickerOperator._target
        if target == "START":
            props.route_lat1 = lat
            props.route_lon1 = lon
        else:
            props.route_lat2 = lat
            props.route_lon2 = lon
        self.report({"INFO"}, f"{target.title()}: {lat:.5f}, {lon:.5f}")
        self._cleanup(context)
        return {"FINISHED"}

    def _cancel(self, context):
        self._cleanup(context)
        return {"CANCELLED"}

    def _cleanup(self, context):
        if GeoMapRoutePickerOperator._draw_handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(
                GeoMapRoutePickerOperator._draw_handle, "WINDOW"
            )
            GeoMapRoutePickerOperator._draw_handle = None
        if GeoMapRoutePickerOperator._state is not None:
            GeoMapRoutePickerOperator._state.textures.clear()
            GeoMapRoutePickerOperator._state = None
        GeoMapRoutePickerOperator._point = None
        if context.area:
            context.area.tag_redraw()

    @staticmethod
    def _read_map_bbox():
        try:
            col = bpy.data.collections.get("GeoMap")
            raw = col.get("geomap_bbox") if col else None
            if not raw:
                return None
            parts = [float(p) for p in str(raw).split(",")]
            if len(parts) == 4:
                return tuple(parts)  # (min_lat, min_lon, max_lat, max_lon)
        except Exception:
            pass
        return None

    @staticmethod
    def _zoom_for_bbox(la1: float, lo1: float, la2: float, lo2: float) -> int:
        lat_span = abs(la2 - la1)
        lon_span = abs(lo2 - lo1)
        span = max(lat_span, lon_span)
        if span <= 0:
            return 10
        # rough mapping: zoom = log2(360 / span) - 1, clamped to [5, 16]
        import math as _math
        zoom = int(_math.log2(360.0 / span)) - 1
        return max(5, min(16, zoom))

    @staticmethod
    def _clear_tiles(state) -> None:
        state.textures.clear()
        state.loading.clear()
        state.failed.clear()
