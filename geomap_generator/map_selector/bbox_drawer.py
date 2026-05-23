import blf
import gpu
from gpu_extras.batch import batch_for_shader

from .map_state import MapViewState
from .tile_loader import lat_lon_to_screen, tile_screen_rect, visible_tiles


def draw_tiles(state: MapViewState) -> None:
    img_shader = gpu.shader.from_builtin("IMAGE")
    ph_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")

    for tx, ty in visible_tiles(state):
        key = (state.zoom, tx, ty)
        sx0, sy0, sx1, sy1 = tile_screen_rect(tx, ty, state)
        tex = state.textures.get(key)

        if tex is None:
            ph_shader.bind()
            ph_shader.uniform_float(
                "color",
                (0.22, 0.22, 0.22, 1.0) if key in state.loading else (0.32, 0.32, 0.32, 1.0),
            )
            batch_for_shader(ph_shader, "TRI_FAN", {
                "pos": [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)],
            }).draw(ph_shader)
        else:
            img_shader.bind()
            img_shader.uniform_sampler("image", tex)
            batch_for_shader(img_shader, "TRI_FAN", {
                "pos": [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)],
                "texCoord": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
            }).draw(img_shader)

    gpu.state.blend_set("NONE")


def draw_selection_rect(state: MapViewState) -> None:
    if not (state.sel_start and state.sel_end):
        return
    _selection_shape(state.sel_start, state.sel_end,
                     (0.2, 0.55, 1.0, 0.25), (0.2, 0.55, 1.0, 0.9))


def draw_confirmed_bbox(state: MapViewState) -> None:
    if not state.bbox:
        return
    la1, lo1, la2, lo2 = state.bbox
    sw, sh = state.screen_w, state.screen_h
    p0 = lat_lon_to_screen(la1, lo1, state.center_lat, state.center_lon, state.zoom, sw, sh)
    p1 = lat_lon_to_screen(la2, lo2, state.center_lat, state.center_lon, state.zoom, sw, sh)
    _selection_shape(p0, p1, (0.1, 0.9, 0.3, 0.2), (0.1, 0.9, 0.3, 0.9))


def _selection_shape(p0: tuple, p1: tuple, fill: tuple, border: tuple) -> None:
    x0, y0 = p0
    x1, y1 = p1
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", fill)
    batch_for_shader(shader, "TRI_FAN", {
        "pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
    }).draw(shader)
    shader.uniform_float("color", border)
    batch_for_shader(shader, "LINE_STRIP", {
        "pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
    }).draw(shader)
    gpu.state.blend_set("NONE")


def draw_hud(state: MapViewState) -> None:
    lines = [
        "Left drag: select area   |   Scroll: zoom   |   Middle drag: pan",
        "Enter: confirm   |   ESC: cancel",
    ]
    if state.bbox:
        la1, lo1, la2, lo2 = state.bbox
        lines.append(f"Selected: ({la1:.4f}, {lo1:.4f}) → ({la2:.4f}, {lo2:.4f})")
    else:
        lines.append("No area selected — drag to select")

    _hud_bg(state.screen_w, state.screen_h, len(lines))

    font_id = 0
    blf.size(font_id, 14)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    for i, line in enumerate(lines):
        blf.position(font_id, 10, state.screen_h - 22 - i * 22, 0)
        blf.draw(font_id, line)

    blf.size(font_id, 12)
    blf.color(font_id, 0.85, 0.85, 0.85, 0.9)
    blf.position(font_id, 10, 8, 0)
    blf.draw(font_id, f"Zoom: {state.zoom}   Center: {state.center_lat:.4f}, {state.center_lon:.4f}")


def _hud_bg(sw: int, sh: int, line_count: int) -> None:
    h = 14 + line_count * 22 + 14
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.65))
    batch_for_shader(shader, "TRI_FAN", {
        "pos": [(0, sh - h), (sw, sh - h), (sw, sh), (0, sh)],
    }).draw(shader)
    gpu.state.blend_set("NONE")
