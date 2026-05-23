# geomap_generator/dashboard/widgets.py
"""Pure-Python widget system for the GeoMap Dashboard overlay.

No bpy imports. Widget draw() methods lazy-import renderer (which needs GPU context).
All widget logic (hit_test, prop binding, layout) is unit-testable without Blender.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["Rect", "UIWidget", "Button", "Toggle", "SliderFloat", "RadioGroup", "TabBar", "ProgressBar", "TextLabel", "LayerRow"]


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

    def draw(self, ctx: object) -> None:
        """Render button with hover/pressed state feedback."""
        from .renderer import draw_rect, draw_text
        if self._pressed:
            bg = (0.5, 0.5, 0.5, 0.95)
        elif self.hovered:
            bg = (0.38, 0.38, 0.38, 0.95)
        else:
            bg = (0.28, 0.28, 0.28, 0.90)
        draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, bg)
        draw_text(self.label, self.rect.x + 6, self.rect.y + 8, 12, (1.0, 1.0, 1.0, 1.0))


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

    def draw(self, ctx: object) -> None:
        """Render checkbox square + label text."""
        from .renderer import draw_rect, draw_text
        check_sz = self.rect.h
        color = (0.18, 0.65, 0.28, 0.90) if self.value else (0.22, 0.22, 0.22, 0.90)
        draw_rect(self.rect.x, self.rect.y, check_sz, check_sz, color)
        draw_text(
            self.label,
            self.rect.x + check_sz + 6, self.rect.y + 8,
            12, (0.90, 0.90, 0.90, 1.0),
        )


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

    def draw(self, ctx: object) -> None:
        """Render track + filled portion + value text."""
        from .renderer import draw_rect, draw_text
        draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, (0.14, 0.14, 0.14, 0.90))
        span = max(self.max_val - self.min_val, 1e-6)
        t = (self.value - self.min_val) / span
        fill_w = self.rect.w * max(0.0, min(1.0, t))
        draw_rect(self.rect.x, self.rect.y, fill_w, self.rect.h, (0.18, 0.48, 0.78, 0.90))
        draw_text(f"{self.value:.3f}", self.rect.x + 4, self.rect.y + 6, 11, (1.0, 1.0, 1.0, 1.0))


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
        # Iterate in reverse so the rightmost option wins boundary ties.
        for i in range(len(self.options) - 1, -1, -1):
            if self._option_rect(i).contains(mx, my):
                setattr(self.props, self.prop_name, self.options[i][0])
                return True
        return False

    def draw(self, ctx: object) -> None:
        """Render each option as a colored segment."""
        from .renderer import draw_rect, draw_text
        for i, (val, label) in enumerate(self.options):
            r = self._option_rect(i)
            selected = val == self.value
            bg = (0.18, 0.48, 0.78, 0.90) if selected else (0.20, 0.20, 0.20, 0.90)
            draw_rect(r.x, r.y, r.w, r.h, bg)
            draw_text(label, r.x + 4, r.y + 8, 11, (1.0, 1.0, 1.0, 1.0))


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

    def draw(self, ctx: object) -> None:
        """Render tab labels with active tab highlighted."""
        from .renderer import draw_rect, draw_text
        for i, name in enumerate(self.tabs):
            r = self._tab_rect(i)
            bg = (0.20, 0.20, 0.20, 0.96) if i == self.active_index else (0.11, 0.11, 0.11, 0.92)
            draw_rect(r.x, r.y, r.w, r.h, bg)
            draw_text(name, r.x + 10, r.y + 9, 13, (1.0, 1.0, 1.0, 1.0))


class ProgressBar(UIWidget):
    """Progress bar displaying a 0..1 value and a status text string."""

    def __init__(self, rect: Rect) -> None:
        super().__init__(rect)
        self.progress: float = 0.0
        self.status: str = ""

    def draw(self, ctx: object) -> None:
        """Render progress track + fill + status text."""
        from .renderer import draw_rect, draw_text
        draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, (0.11, 0.11, 0.11, 0.90))
        fill_w = self.rect.w * max(0.0, min(1.0, self.progress))
        draw_rect(self.rect.x, self.rect.y, fill_w, self.rect.h, (0.08, 0.55, 0.18, 0.90))
        pct = int(self.progress * 100)
        label = f"{self.status}  {pct}%" if self.status else f"{pct}%"
        draw_text(label, self.rect.x + 8, self.rect.y + 7, 12, (1.0, 1.0, 1.0, 1.0))


class TextLabel(UIWidget):
    """Non-interactive text label."""

    def __init__(self, rect: Rect, text: str) -> None:
        super().__init__(rect)
        self.text = text

    def draw(self, ctx: object) -> None:
        """Render static text."""
        from .renderer import draw_text
        draw_text(self.text, self.rect.x, self.rect.y + 8, 12, (0.85, 0.85, 0.85, 1.0))


class LayerRow(UIWidget):
    """Composite row: Toggle + optional SliderFloat + optional RadioGroup + Button.

    Inline settings (slider, radio) are visible only when toggle is ON (_enabled).
    """

    _TOGGLE_W: int = 200
    _BTN_W: int = 90
    _SLIDER_W: int = 80
    _RADIO_W: int = 100
    _GAP: int = 8

    def __init__(
        self,
        rect: Rect,
        label: str,
        props: object,
        toggle_prop: str,
        generate_callback: Callable[[], None],
        width_prop: Optional[str] = None,
        width_min: float = 0.0,
        width_max: float = 0.5,
        geometry_prop: Optional[str] = None,
        geometry_options: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        super().__init__(rect)
        self.props = props
        self.toggle_prop = toggle_prop

        self._toggle = Toggle(
            Rect(rect.x, rect.y, self._TOGGLE_W, rect.h),
            label, props, toggle_prop,
        )
        self._btn = Button(
            Rect(rect.x + rect.w - self._BTN_W, rect.y, self._BTN_W, rect.h),
            "Generate", generate_callback,
        )

        self._slider: Optional[SliderFloat] = None
        if width_prop:
            sx = rect.x + self._TOGGLE_W + self._GAP
            self._slider = SliderFloat(
                Rect(sx, rect.y, self._SLIDER_W, rect.h),
                "width", props, width_prop, width_min, width_max,
            )

        self._radio: Optional[RadioGroup] = None
        if geometry_prop and geometry_options:
            rx = rect.x + self._TOGGLE_W + self._GAP + (
                self._SLIDER_W + self._GAP if width_prop else 0
            )
            self._radio = RadioGroup(
                Rect(rx, rect.y, self._RADIO_W, rect.h),
                props, geometry_prop, geometry_options,
            )

    @property
    def _enabled(self) -> bool:
        """True when the layer toggle is ON."""
        return bool(getattr(self.props, self.toggle_prop, False))

    def _active_widgets(self) -> list[UIWidget]:
        """Return widgets that should receive events (slider/radio only when enabled)."""
        result: list[UIWidget] = [self._toggle, self._btn]
        if self._enabled:
            if self._slider is not None:
                result.append(self._slider)
            if self._radio is not None:
                result.append(self._radio)
        return result

    def on_mouse_press(self, mx: float, my: float) -> bool:
        for w in self._active_widgets():
            if w.on_mouse_press(mx, my):
                return True
        return False

    def on_mouse_release(self, mx: float, my: float) -> bool:
        for w in self._active_widgets():
            if w.on_mouse_release(mx, my):
                return True
        return False

    def on_mouse_move(self, mx: float, my: float) -> None:
        for w in self._active_widgets():
            w.on_mouse_move(mx, my)

    def draw(self, ctx: object) -> None:
        """Render row background + all active sub-widgets."""
        from .renderer import draw_rect
        bg = (0.17, 0.17, 0.17, 0.85) if self._enabled else (0.12, 0.12, 0.12, 0.85)
        draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, bg)
        for w in self._active_widgets():
            w.draw(ctx)
