# geomap_generator/dashboard/widgets.py
"""Pure-Python widget system for the GeoMap Dashboard overlay.

No bpy imports. Widget draw() methods lazy-import renderer (which needs GPU context).
All widget logic (hit_test, prop binding, layout) is unit-testable without Blender.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["Rect", "UIWidget", "Button", "Toggle", "SliderFloat", "RadioGroup", "TabBar", "ProgressBar", "TextLabel"]


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


class Button(UIWidget):
    """Clickable button. Fires callback on mouse-press + release inside bounds."""

    def __init__(self, rect: Rect, label: str, callback: Callable[[], None]) -> None:
        super().__init__(rect)
        self.label = label
        self.callback = callback
        self._pressed: bool = False

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if self.hit_test(mx, my):
            self._pressed = True
            return True
        return False

    def on_mouse_release(self, mx: float, my: float) -> bool:
        if self._pressed:
            self._pressed = False
            if self.hit_test(mx, my):
                self.callback()
                return True
        return False


class Toggle(UIWidget):
    """Boolean toggle. Reads/writes a bool property on a props object."""

    def __init__(self, rect: Rect, label: str, props: object, prop_name: str) -> None:
        super().__init__(rect)
        self.label = label
        self.props = props
        self.prop_name = prop_name

    @property
    def value(self) -> bool:
        """Current boolean value read from props."""
        return bool(getattr(self.props, self.prop_name, False))

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if self.hit_test(mx, my):
            setattr(self.props, self.prop_name, not self.value)
            return True
        return False


class SliderFloat(UIWidget):
    """Horizontal drag slider bound to a float property on a props object."""

    def __init__(
        self, rect: Rect, label: str, props: object, prop_name: str,
        min_val: float, max_val: float,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.props = props
        self.prop_name = prop_name
        self.min_val = min_val
        self.max_val = max_val
        self._dragging: bool = False

    @property
    def value(self) -> float:
        """Current float value read from props."""
        return float(getattr(self.props, self.prop_name, self.min_val))

    def _clamped(self, val: float) -> float:
        """Clamp val to [min_val, max_val]."""
        return max(self.min_val, min(self.max_val, val))

    def _x_to_value(self, mx: float) -> float:
        """Convert pixel x position to slider value."""
        t = (mx - self.rect.x) / max(self.rect.w, 1.0)
        return self.min_val + t * (self.max_val - self.min_val)

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if self.hit_test(mx, my):
            self._dragging = True
            setattr(self.props, self.prop_name, self._clamped(self._x_to_value(mx)))
            return True
        return False

    def on_mouse_move(self, mx: float, my: float) -> None:
        super().on_mouse_move(mx, my)
        if self._dragging:
            setattr(self.props, self.prop_name, self._clamped(self._x_to_value(mx)))

    def on_mouse_release(self, mx: float, my: float) -> bool:
        if self._dragging:
            self._dragging = False
            return True
        return False


class RadioGroup(UIWidget):
    """Horizontal radio group bound to an enum property on a props object."""

    def __init__(
        self, rect: Rect, props: object, prop_name: str,
        options: list[tuple[str, str]],
    ) -> None:
        super().__init__(rect)
        self.props = props
        self.prop_name = prop_name
        self.options = options  # [(value, label), ...]

    @property
    def value(self) -> str:
        """Current string value read from props."""
        return str(getattr(self.props, self.prop_name, ""))

    def _option_rect(self, index: int) -> Rect:
        """Return the Rect for option at index."""
        n = len(self.options) or 1
        w = self.rect.w / n
        return Rect(self.rect.x + index * w, self.rect.y, w, self.rect.h)

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if not self.hit_test(mx, my):
            return False
        for i, (val, _) in enumerate(self.options):
            if self._option_rect(i).contains(mx, my):
                setattr(self.props, self.prop_name, val)
                return True
        return False


class TabBar(UIWidget):
    """Tab bar that switches active_index on click."""

    def __init__(self, rect: Rect, tabs: list[str]) -> None:
        super().__init__(rect)
        self.tabs = tabs
        self.active_index: int = 0

    def _tab_rect(self, index: int) -> Rect:
        """Return the Rect for tab at index."""
        n = len(self.tabs) or 1
        w = self.rect.w / n
        return Rect(self.rect.x + index * w, self.rect.y, w, self.rect.h)

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if not self.hit_test(mx, my):
            return False
        for i in range(len(self.tabs)):
            if self._tab_rect(i).contains(mx, my):
                self.active_index = i
                return True
        return False


class ProgressBar(UIWidget):
    """Progress bar displaying a 0..1 value and a status text string."""

    def __init__(self, rect: Rect) -> None:
        super().__init__(rect)
        self.progress: float = 0.0
        self.status: str = ""


class TextLabel(UIWidget):
    """Non-interactive text label."""

    def __init__(self, rect: Rect, text: str) -> None:
        super().__init__(rect)
        self.text = text
