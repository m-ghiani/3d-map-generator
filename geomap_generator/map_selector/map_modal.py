import os

import bpy
from bpy.types import Operator

from .bbox_drawer import draw_confirmed_bbox, draw_hud, draw_selection_rect, draw_tiles
from .map_state import MapViewState
from .tile_loader import (
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


class GeoMapSelectorOperator(Operator):
    bl_idname = "geomap.open_map_selector"
    bl_label = "Pick Area on Map"
    bl_description = "Draw a bounding box on an interactive OpenStreetMap view"

    _draw_handle = None
    _state: MapViewState = None
    _cache_dir: str = ""

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        state = MapViewState()
        props = context.scene.geomap_props

        if props.input_mode == "COORDS" and props.latitude != 0.0:
            state.center_lat = (props.latitude + props.latitude2) / 2.0
            state.center_lon = (props.longitude + props.longitude2) / 2.0
            state.zoom = 8
            state.bbox = (
                min(props.latitude, props.latitude2),
                min(props.longitude, props.longitude2),
                max(props.latitude, props.latitude2),
                max(props.longitude, props.longitude2),
            )

        cache = _get_cache_dir()
        GeoMapSelectorOperator._state = state
        GeoMapSelectorOperator._cache_dir = cache
        start_worker(cache)

        GeoMapSelectorOperator._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            GeoMapSelectorOperator._draw_callback, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    @staticmethod
    def _draw_callback():
        state = GeoMapSelectorOperator._state
        if state is None:
            return

        region = bpy.context.region
        state.screen_w = region.width
        state.screen_h = region.height
        cache = GeoMapSelectorOperator._cache_dir

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
        draw_selection_rect(state)
        draw_confirmed_bbox(state)
        draw_hud(state)

    def modal(self, context, event):
        state = GeoMapSelectorOperator._state
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
                wy = state.pan_world[1] + dy  # screen y up, world y down
                state.center_lat, state.center_lon = world_px_to_lat_lon(wx, wy, state.zoom)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                state.selecting = True
                state.sel_start = (mx, my)
                state.sel_end = (mx, my)
            elif event.value == "RELEASE" and state.selecting:
                state.selecting = False
                if state.sel_start and state.sel_end:
                    x0, y0 = state.sel_start
                    x1, y1 = state.sel_end
                    if abs(x1 - x0) > 5 or abs(y1 - y0) > 5:
                        la0, lo0 = screen_to_lat_lon(
                            x0, y0, state.center_lat, state.center_lon,
                            state.zoom, state.screen_w, state.screen_h,
                        )
                        la1, lo1 = screen_to_lat_lon(
                            x1, y1, state.center_lat, state.center_lon,
                            state.zoom, state.screen_w, state.screen_h,
                        )
                        state.bbox = (
                            min(la0, la1), min(lo0, lo1),
                            max(la0, la1), max(lo0, lo1),
                        )
                state.sel_start = None
                state.sel_end = None
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and state.selecting:
            state.sel_end = (mx, my)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def _confirm(self, context):
        state = GeoMapSelectorOperator._state
        if not state or not state.bbox:
            self.report({"WARNING"}, "No area selected — drag to select before confirming")
            return {"RUNNING_MODAL"}
        la1, lo1, la2, lo2 = state.bbox
        props = context.scene.geomap_props
        props.input_mode = "COORDS"
        props.latitude = la1
        props.longitude = lo1
        props.latitude2 = la2
        props.longitude2 = lo2
        self.report({"INFO"}, f"Area set: ({la1:.4f}, {lo1:.4f}) → ({la2:.4f}, {lo2:.4f})")
        self._cleanup(context)
        return {"FINISHED"}

    def _cancel(self, context):
        self._cleanup(context)
        return {"CANCELLED"}

    def _cleanup(self, context):
        if GeoMapSelectorOperator._draw_handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(
                GeoMapSelectorOperator._draw_handle, "WINDOW"
            )
            GeoMapSelectorOperator._draw_handle = None
        if GeoMapSelectorOperator._state is not None:
            GeoMapSelectorOperator._state.textures.clear()
            GeoMapSelectorOperator._state = None
        if context.area:
            context.area.tag_redraw()

    @staticmethod
    def _clear_tiles(state: MapViewState) -> None:
        state.textures.clear()
        state.loading.clear()
        state.failed.clear()
