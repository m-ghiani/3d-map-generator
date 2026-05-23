# geomap_generator/dashboard/widgets.py
"""Pure-Python widget system for the GeoMap Dashboard overlay.

No bpy imports. Widget draw() methods lazy-import renderer (which needs GPU context).
All widget logic (hit_test, prop binding, layout) is unit-testable without Blender.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["Rect", "UIWidget"]


@dataclass
class Rect:
    """Axis-aligned rectangle for widget hit testing and layout.

    Stores position (x, y) and dimensions (w, h). All values are in pixels.
    """
    x: float
    y: float
    w: float
    h: float

    def contains(self, mx: float, my: float) -> bool:
        """Return True if point (mx, my) is within bounds (inclusive on all edges)."""
        return self.x <= mx <= self.x + self.w and self.y <= my <= self.y + self.h


class UIWidget:
    """Base class for all dashboard UI widgets.

    Subclasses override draw() and mouse event handlers.
    draw() must lazy-import renderer to avoid GPU context issues at import time.
    """
    def __init__(self, rect: Rect) -> None:
        self.rect = rect
        self.hovered: bool = False
        self.visible: bool = True

    def draw(self, ctx: object) -> None:
        pass

    def hit_test(self, mx: float, my: float) -> bool:
        return self.visible and self.rect.contains(mx, my)

    def on_mouse_press(self, mx: float, my: float) -> bool:
        return False

    def on_mouse_release(self, mx: float, my: float) -> bool:
        return False

    def on_mouse_move(self, mx: float, my: float) -> None:
        self.hovered = self.hit_test(mx, my)
