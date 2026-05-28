# geomap_generator/dashboard/renderer.py
"""GPU draw primitives for the GeoMap Dashboard overlay.

draw_rect() and draw_text() must be called from within a Blender draw
callback (POST_PIXEL) where a GPU context is active. Do not call from
the main thread outside a draw handler.
"""
from __future__ import annotations


def draw_rect(
    x: float, y: float, w: float, h: float,
    color: tuple[float, float, float, float],
) -> None:
    """Draw a filled rectangle using the UNIFORM_COLOR shader.

    Args:
        x, y: Bottom-left corner in region pixels.
        w, h: Width and height in pixels.
        color: RGBA tuple, each channel 0.0..1.0. Alpha blending is enabled.
    """
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    indices = ((0, 1, 2), (0, 2, 3))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.blend_set("ALPHA")
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def draw_text(
    text: str, x: float, y: float, size: int,
    color: tuple[float, float, float, float],
) -> None:
    """Draw text at position using blf.

    Args:
        text: String to render.
        x, y: Bottom-left of text in region pixels.
        size: Font size in points.
        color: RGBA tuple, each channel 0.0..1.0.
    """
    import blf

    font_id = 0
    blf.position(font_id, x, y, 0)
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def text_width(text: str, size: int) -> float:
    """Return rendered pixel width of a text string at the given font size.

    Uses blf.dimensions() — must be called from within a GPU draw callback.
    """
    import blf

    font_id = 0
    blf.size(font_id, size)
    w, _ = blf.dimensions(font_id, text)
    return w
