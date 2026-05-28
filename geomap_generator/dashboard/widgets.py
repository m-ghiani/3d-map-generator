# geomap_generator/dashboard/widgets.py
"""Pure-Python widget system for the GeoMap Dashboard overlay.

No bpy imports. Widget draw() methods lazy-import renderer (which needs GPU context).
All widget logic (hit_test, prop binding, layout) is unit-testable without Blender.

Visual style matches Blender 4.x default dark theme:
  - Two-tone gradient on widget fills (bottom/top halves slightly differ)
  - Blender blue (#324D79) as the active/selected accent
  - Dark field background for sliders and text inputs
  - Compact 22 px row height, 2 px row gap
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "Rect", "UIWidget",
    "Button", "Toggle", "SliderFloat", "RadioGroup",
    "TabBar", "ProgressBar", "TextLabel", "TextInput",
    "LayerRow", "Separator", "SectionHeader",
]

# -- Blender 4.x default dark theme palette --
# Region / panel background
_BG          = (0.157, 0.157, 0.157, 1.00)
_BG_HEADER   = (0.137, 0.137, 0.137, 1.00)

# Widget fills (normal state)
_W_INNER     = (0.278, 0.278, 0.278, 1.00)   # lower half
_W_INNER_HI  = (0.318, 0.318, 0.318, 1.00)   # upper half (subtle gradient)

# Widget fills (hover)
_W_HOVER     = (0.353, 0.353, 0.353, 1.00)
_W_HOVER_HI  = (0.392, 0.392, 0.392, 1.00)

# Widget fills (pressed / sunken)
_W_PRESS     = (0.196, 0.196, 0.196, 1.00)

# Widget fills (active / selected — Blender blue)
_W_ACTIVE    = (0.196, 0.455, 0.753, 1.00)
_W_ACTIVE_HI = (0.255, 0.518, 0.820, 1.00)

# Field backgrounds (text input, slider track)
_W_FIELD     = (0.157, 0.157, 0.157, 1.00)   # same as panel bg
_W_FIELD_FOC = (0.118, 0.118, 0.118, 1.00)   # focused / darker

# Borders and separators
_BORDER      = (0.000, 0.000, 0.000, 0.420)
_SEP_COLOR   = (0.090, 0.090, 0.090, 1.00)

# Text
_TEXT        = (0.902, 0.902, 0.902, 1.00)   # normal
_TEXT_ACT    = (1.000, 1.000, 1.000, 1.00)   # on active (blue) background
_TEXT_DIM    = (0.549, 0.549, 0.549, 1.00)   # muted / placeholder
_TEXT_HDR    = (0.780, 0.780, 0.780, 1.00)   # section header

# Special fills
_ACCENT      = (0.196, 0.455, 0.753, 1.00)   # Blender blue accent
_PROG_FG     = (0.251, 0.541, 0.224, 1.00)   # progress bar fill (green)


# ── Drawing helpers ───────────────────────────────────────────────────────

def _draw_widget(
    rect: "Rect",
    bottom: tuple,
    top: tuple | None = None,
) -> None:
    """Draw a Blender-style widget: two-tone gradient fill + dark outline.

    Args:
        rect:   Widget bounding box.
        bottom: RGBA colour for the lower half of the widget.
        top:    RGBA colour for the upper half. When None, derived from
                *bottom* by brightening each channel by 0.040.
    """
    from .renderer import draw_rect
    x, y, w, h = rect.x, rect.y, rect.w, rect.h
    mid = y + h * 0.5
    if top is None:
        top = (
            min(1.0, bottom[0] + 0.040),
            min(1.0, bottom[1] + 0.040),
            min(1.0, bottom[2] + 0.040),
            bottom[3],
        )
    # Lower half
    draw_rect(x + 1, y + 1, w - 2, mid - y - 1, bottom)
    # Upper half
    draw_rect(x + 1, mid, w - 2, y + h - 1 - mid, top)
    # Dark outline (4 sides, 1 px each)
    draw_rect(x,         y,         w, 1, _BORDER)
    draw_rect(x,         y + h - 1, w, 1, _BORDER)
    draw_rect(x,         y + 1,     1, h - 2, _BORDER)
    draw_rect(x + w - 1, y + 1,     1, h - 2, _BORDER)


def _text_y(rect: "Rect") -> float:
    """Vertical baseline for text drawn inside a widget rect.

    Positions text visually centred for both 11 pt and 12 pt fonts
    inside widgets from 20 px to 30 px tall.
    """
    return rect.y + max(4.0, rect.h * 0.27)


# ── Base ──────────────────────────────────────────────────────────────────

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
        """Render the widget into the active GPU context. Override in subclasses."""

    def hit_test(self, mx: float, my: float) -> bool:
        """Return True if (mx, my) falls within this widget's visible bounds."""
        return self.visible and self.rect.contains(mx, my)

    def on_mouse_press(self, _mx: float, _my: float) -> bool:
        """Handle a left-mouse press. Return True if the event was consumed."""
        return False

    def on_mouse_release(self, _mx: float, _my: float) -> bool:
        """Handle a left-mouse release. Return True if the event was consumed."""
        return False

    def on_mouse_move(self, mx: float, my: float) -> None:
        """Update hover state from current cursor position."""
        self.hovered = self.hit_test(mx, my)

    def on_key(self, _event: object) -> bool:
        """Handle a keyboard event. Return True if the event was consumed."""
        return False

    def blur(self) -> None:
        """Remove focus from this widget (called when another area is clicked)."""


# ── Widgets ───────────────────────────────────────────────────────────────

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
        """Render with Blender-style gradient and hover/press feedback."""
        from .renderer import draw_text
        if self._pressed:
            _draw_widget(self.rect, _W_PRESS, _W_PRESS)
        elif self.hovered:
            _draw_widget(self.rect, _W_HOVER, _W_HOVER_HI)
        else:
            _draw_widget(self.rect, _W_INNER, _W_INNER_HI)
        draw_text(self.label, self.rect.x + 8, _text_y(self.rect), 11, _TEXT)


class Toggle(UIWidget):
    """Boolean toggle rendered as a push-button (Blender blue when ON).

    Reads/writes a bool property on a props object.
    """

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
        """Render as filled push-button: Blender blue when ON, grey when OFF."""
        from .renderer import draw_text
        if self.value:
            _draw_widget(self.rect, _W_ACTIVE, _W_ACTIVE_HI)
            tc = _TEXT_ACT
        elif self.hovered:
            _draw_widget(self.rect, _W_HOVER, _W_HOVER_HI)
            tc = _TEXT
        else:
            _draw_widget(self.rect, _W_INNER, _W_INNER_HI)
            tc = _TEXT_DIM
        draw_text(self.label, self.rect.x + 8, _text_y(self.rect), 11, tc)


class SliderFloat(UIWidget):
    """Horizontal drag slider bound to a float (or int) property.

    Rendered as a dark field with a Blender-blue proportional fill.
    """

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
        """Current value read from props (always float internally)."""
        return float(getattr(self.props, self.prop_name, self.min_val))

    def _clamped(self, val: float):
        """Clamp val to [min_val, max_val], preserving bound prop type."""
        clamped = max(self.min_val, min(self.max_val, val))
        current = getattr(self.props, self.prop_name, self.min_val)
        if isinstance(current, int) and not isinstance(current, bool):
            return int(round(clamped))
        return float(clamped)

    def _x_to_value(self, mx: float) -> float:
        """Convert pixel x position to slider value."""
        t = (mx - self.rect.x) / max(self.rect.w, 1.0)
        return self.min_val + t * (self.max_val - self.min_val)

    def _normalized_t(self) -> float:
        """Return 0..1 position of current value within [min_val, max_val]."""
        span = max(self.max_val - self.min_val, 1e-6)
        return max(0.0, min(1.0, (self.value - self.min_val) / span))

    def _display_value(self) -> str:
        """Format the current value for display inside the slider track."""
        current = getattr(self.props, self.prop_name, self.min_val)
        if isinstance(current, int) and not isinstance(current, bool):
            return str(int(round(self.value)))
        if self.max_val <= 1.0:
            return f"{int(round(self._normalized_t() * 100.0))}%"
        if self.max_val <= 10.0:
            return f"{self.value:.1f}"
        return f"{self.value:g}"

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
        """Render dark track + Blender-blue fill proportional to value."""
        from .renderer import draw_rect, draw_text
        x, y, w, h = self.rect.x, self.rect.y, self.rect.w, self.rect.h
        # Dark field background
        draw_rect(x, y, w, h, _W_FIELD)
        # Blue fill
        fill_w = max(0.0, (w - 2) * self._normalized_t())
        if fill_w > 0:
            draw_rect(x + 1, y + 1, fill_w, h - 2, _W_ACTIVE)
        # Field outline
        draw_rect(x,         y,         w, 1, _BORDER)
        draw_rect(x,         y + h - 1, w, 1, _BORDER)
        draw_rect(x,         y,         1, h, _BORDER)
        draw_rect(x + w - 1, y,         1, h, _BORDER)
        # Value text (white — readable over both fill and empty track)
        draw_text(self._display_value(), x + 8, _text_y(self.rect), 11, _TEXT_ACT)


class RadioGroup(UIWidget):
    """Horizontal radio group bound to an enum property.

    Each option is a button segment; the active one renders in Blender blue.
    """

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
        """Render each segment with gradient; active segment in Blender blue."""
        from .renderer import draw_rect, draw_text
        current = self.value
        for i, (val, label) in enumerate(self.options):
            r = self._option_rect(i)
            selected = val == current
            if selected:
                _draw_widget(r, _W_ACTIVE, _W_ACTIVE_HI)
                tc = _TEXT_ACT
            else:
                _draw_widget(r, _W_INNER, _W_INNER_HI)
                tc = _TEXT_DIM
            # Vertical divider between adjacent segments
            if i > 0:
                draw_rect(r.x, r.y + 2, 1, r.h - 4, _BORDER)
            draw_text(label, r.x + 6, _text_y(r), 11, tc)


class TabBar(UIWidget):
    """Horizontal tab bar. Active tab has a Blender-blue accent strip."""

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
        """Render tabs: active matches panel bg + blue accent; inactive darker."""
        from .renderer import draw_rect, draw_text
        for i, name in enumerate(self.tabs):
            r = self._tab_rect(i)
            if i == self.active_index:
                # Matches content-area background → tab appears "connected"
                draw_rect(r.x, r.y, r.w, r.h, _BG)
                # Blue accent line at the top edge (outer boundary of tab bar)
                draw_rect(r.x, r.y + r.h - 2, r.w, 2, _ACCENT)
                tc = _TEXT
            else:
                draw_rect(r.x, r.y, r.w, r.h, _BG_HEADER)
                tc = _TEXT_DIM
            # Thin vertical divider between tabs
            draw_rect(r.x + r.w - 1, r.y + 4, 1, r.h - 8, _SEP_COLOR)
            draw_text(name, r.x + 8, _text_y(r), 11, tc)


class ProgressBar(UIWidget):
    """Progress bar: dark field + green fill + status text."""

    def __init__(self, rect: Rect) -> None:
        super().__init__(rect)
        self.progress: float = 0.0
        self.status: str = ""

    def draw(self, ctx: object) -> None:
        from .renderer import draw_rect, draw_text
        x, y, w, h = self.rect.x, self.rect.y, self.rect.w, self.rect.h
        draw_rect(x, y, w, h, _W_FIELD)
        fill_w = max(0.0, (w - 2) * max(0.0, min(1.0, self.progress)))
        if fill_w > 0:
            draw_rect(x + 1, y + 1, fill_w, h - 2, _PROG_FG)
        draw_rect(x,         y,         w, 1, _BORDER)
        draw_rect(x,         y + h - 1, w, 1, _BORDER)
        draw_rect(x,         y,         1, h, _BORDER)
        draw_rect(x + w - 1, y,         1, h, _BORDER)
        pct = int(self.progress * 100)
        label = f"{self.status}  {pct}%" if self.status else f"{pct}%"
        draw_text(label, x + 8, _text_y(self.rect), 11, _TEXT_ACT)


class TextLabel(UIWidget):
    """Non-interactive muted label (property name or hint text)."""

    def __init__(self, rect: Rect, text: str) -> None:
        super().__init__(rect)
        self.text = text

    def draw(self, ctx: object) -> None:
        from .renderer import draw_text
        draw_text(self.text, self.rect.x, _text_y(self.rect), 11, _TEXT_DIM)


class TextInput(UIWidget):
    """Single-line text input bound to a string or numeric property.

    Keyboard events are dispatched from the modal via on_key().
    blur() is called when the user clicks outside any input.
    """

    def __init__(
        self, rect: Rect, props: object, prop_name: str, placeholder: str = "",
    ) -> None:
        super().__init__(rect)
        self.props = props
        self.prop_name = prop_name
        self.placeholder = placeholder
        self.focused = False
        self._buffer = self._prop_to_text()

    def _prop_to_text(self) -> str:
        value = getattr(self.props, self.prop_name, "")
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    def _commit(self) -> None:
        """Write _buffer back to the bound property with type coercion."""
        current = getattr(self.props, self.prop_name, "")
        if isinstance(current, float):
            try:
                setattr(self.props, self.prop_name, float(self._buffer))
            except ValueError:
                pass
        elif isinstance(current, int) and not isinstance(current, bool):
            try:
                setattr(self.props, self.prop_name, int(float(self._buffer)))
            except ValueError:
                pass
        else:
            setattr(self.props, self.prop_name, self._buffer)

    def on_mouse_press(self, mx: float, my: float) -> bool:
        self.focused = self.hit_test(mx, my)
        if self.focused:
            self._buffer = self._prop_to_text()
            return True
        return False

    def on_key(self, event: object) -> bool:
        if not self.focused:
            return False
        event_type = str(getattr(event, "type", ""))
        if event_type in {"RET", "NUMPAD_ENTER", "ESC"}:
            self.focused = False
            return True
        if event_type == "BACK_SPACE":
            self._buffer = self._buffer[:-1]
            self._commit()
            return True
        if event_type == "DEL":
            self._buffer = ""
            self._commit()
            return True
        text = str(getattr(event, "unicode", "") or "")
        if text:
            self._buffer += text
            self._commit()
            return True
        return False

    def blur(self) -> None:
        self.focused = False

    def draw(self, ctx: object) -> None:
        """Render as a dark field with blue top-edge focus indicator and cursor."""
        from .renderer import draw_rect, draw_text, text_width
        x, y, w, h = self.rect.x, self.rect.y, self.rect.w, self.rect.h
        # Field background
        draw_rect(x, y, w, h, _W_FIELD_FOC if self.focused else _W_FIELD)
        # Outline
        draw_rect(x,         y,         w, 1, _BORDER)
        draw_rect(x,         y + h - 1, w, 1, _BORDER)
        draw_rect(x,         y,         1, h, _BORDER)
        draw_rect(x + w - 1, y,         1, h, _BORDER)
        # Blue accent stripe at top when focused
        if self.focused:
            draw_rect(x, y + h - 2, w, 2, _ACCENT)
        # Text / placeholder
        display_text = self._buffer if self.focused else self._prop_to_text()
        if display_text:
            draw_text(display_text, x + 6, _text_y(self.rect), 11, _TEXT)
        else:
            draw_text(self.placeholder, x + 6, _text_y(self.rect), 11, _TEXT_DIM)
        # Text cursor (1 px wide line after typed text)
        if self.focused:
            try:
                cw = text_width(self._buffer, 11)
                cx = x + 6 + cw
                if cx < x + w - 4:
                    draw_rect(cx, y + 3, 1, h - 6, _TEXT)
            except (AttributeError, RuntimeError):
                pass


class Separator(UIWidget):
    """Non-interactive horizontal separator line (1 px, muted colour)."""

    def draw(self, ctx: object) -> None:
        from .renderer import draw_rect
        mid_y = self.rect.y + self.rect.h * 0.5
        draw_rect(self.rect.x, mid_y, self.rect.w, 1.0, _SEP_COLOR)


class SectionHeader(UIWidget):
    """Non-interactive section label with a dark-tinted background strip.

    Matches Blender's N-panel sub-panel header appearance.
    """

    def __init__(self, rect: Rect, text: str) -> None:
        super().__init__(rect)
        self.text = text

    def draw(self, ctx: object) -> None:
        from .renderer import draw_rect, draw_text
        draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, _BG_HEADER)
        draw_text("▸ " + self.text, self.rect.x + 4, _text_y(self.rect), 11, _TEXT_HDR)


class LayerRow(UIWidget):
    """Composite row: Toggle + optional SliderFloat + optional RadioGroup + Button.

    Inline controls (slider, radio) are only active when the toggle is ON.
    Each sub-widget draws itself independently — no shared row background.
    """

    _TOGGLE_W: int = 170   # label/enable toggle
    _BTN_W:    int = 80    # "Generate" action button
    _SLIDER_W: int = 100   # width slider
    _RADIO_W:  int = 140   # geometry / resolution radio group
    _GAP:      int = 6     # horizontal gap between sub-widgets

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
        """Widgets that receive events (slider/radio only when enabled)."""
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
        """Draw each sub-widget independently (no shared row background)."""
        for w in self._active_widgets():
            w.draw(ctx)
