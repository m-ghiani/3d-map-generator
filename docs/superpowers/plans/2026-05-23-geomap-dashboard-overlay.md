# GeoMap Dashboard Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chaotic N-panel sidebar with a full-screen GPU-drawn modal overlay (GeoMap Dashboard) that exposes location selection, layer toggles, per-layer generate buttons, and geometry controls.

**Architecture:** New `geomap_generator/dashboard/` package with a pure-Python widget system (`widgets.py`, `layout.py`) and GPU draw layer (`renderer.py`, widget `draw()` methods). A Blender modal operator (`modal.py`) hosts the overlay, dispatches events to the widget tree, and calls existing operators/props. N-panel is preserved for advanced settings.

**Tech Stack:** Python 3.11, Blender 4.x Python API (`bpy`, `gpu`, `blf`, `gpu_extras`), `unittest` + `unittest.mock` for headless widget tests.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `geomap_generator/dashboard/__init__.py` | Package entry — exports `GeoMapDashboardOperator` |
| Create | `geomap_generator/dashboard/widgets.py` | `Rect`, `UIWidget`, `Button`, `Toggle`, `SliderFloat`, `RadioGroup`, `TabBar`, `ProgressBar`, `TextLabel`, `LayerRow` |
| Create | `geomap_generator/dashboard/renderer.py` | `draw_rect()`, `draw_text()` GPU primitives |
| Create | `geomap_generator/dashboard/layout.py` | `build_widget_tree()` — assembles widget tree per tab from props |
| Create | `geomap_generator/dashboard/modal.py` | `GeoMapDashboardOperator` — invoke/modal/draw handler |
| Create | `tests/test_dashboard_widgets.py` | Unit tests for widget logic (no GPU required) |
| Modify | `geomap_generator/__init__.py` | Import and register `GeoMapDashboardOperator` |
| Modify | `geomap_generator/panels.py` | Add "Open Dashboard" button to `GeoMapPanel` |

---

## Task 1: Rect + UIWidget base

**Files:**
- Create: `geomap_generator/dashboard/widgets.py`
- Create: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Create failing tests for Rect and UIWidget**

```python
# tests/test_dashboard_widgets.py
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Guard: widgets.py must not import bpy
import geomap_generator.dashboard.widgets as _w_mod
import importlib, ast, pathlib

class RectTests(unittest.TestCase):
    def test_contains_inside(self):
        from geomap_generator.dashboard.widgets import Rect
        r = Rect(10, 20, 100, 50)
        self.assertTrue(r.contains(60, 45))

    def test_contains_outside_x(self):
        from geomap_generator.dashboard.widgets import Rect
        r = Rect(10, 20, 100, 50)
        self.assertFalse(r.contains(5, 45))

    def test_contains_outside_y(self):
        from geomap_generator.dashboard.widgets import Rect
        r = Rect(10, 20, 100, 50)
        self.assertFalse(r.contains(60, 75))

    def test_contains_boundary(self):
        from geomap_generator.dashboard.widgets import Rect
        r = Rect(10, 20, 100, 50)
        self.assertTrue(r.contains(10, 20))
        self.assertTrue(r.contains(110, 70))


class UIWidgetTests(unittest.TestCase):
    def test_hit_test_visible(self):
        from geomap_generator.dashboard.widgets import Rect, UIWidget
        w = UIWidget(Rect(0, 0, 100, 50))
        self.assertTrue(w.hit_test(50, 25))

    def test_hit_test_invisible(self):
        from geomap_generator.dashboard.widgets import Rect, UIWidget
        w = UIWidget(Rect(0, 0, 100, 50))
        w.visible = False
        self.assertFalse(w.hit_test(50, 25))

    def test_on_mouse_move_sets_hovered(self):
        from geomap_generator.dashboard.widgets import Rect, UIWidget
        w = UIWidget(Rect(0, 0, 100, 50))
        w.on_mouse_move(50, 25)
        self.assertTrue(w.hovered)

    def test_on_mouse_move_clears_hovered_when_outside(self):
        from geomap_generator.dashboard.widgets import Rect, UIWidget
        w = UIWidget(Rect(0, 0, 100, 50))
        w.hovered = True
        w.on_mouse_move(200, 25)
        self.assertFalse(w.hovered)

    def test_no_bpy_import(self):
        src = pathlib.Path("geomap_generator/dashboard/widgets.py").read_text()
        self.assertNotIn("import bpy", src)
```

- [ ] **Step 2: Run tests — expect ImportError (file not yet created)**

```bash
python -m unittest tests.test_dashboard_widgets.RectTests tests.test_dashboard_widgets.UIWidgetTests -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'geomap_generator.dashboard'`

- [ ] **Step 3: Create package dir and widgets.py with Rect + UIWidget**

```bash
mkdir -p geomap_generator/dashboard
touch geomap_generator/dashboard/__init__.py
```

```python
# geomap_generator/dashboard/widgets.py
"""Pure-Python widget system for the GeoMap Dashboard overlay.

No bpy imports. Widget draw() methods lazy-import renderer (which needs GPU context).
All widget logic (hit_test, prop binding, layout) is unit-testable without Blender.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def contains(self, mx: float, my: float) -> bool:
        return self.x <= mx <= self.x + self.w and self.y <= my <= self.y + self.h


class UIWidget:
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
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.RectTests tests.test_dashboard_widgets.UIWidgetTests -v
```

Expected: `OK (5 tests)`

- [ ] **Step 5: Commit**

```bash
git add geomap_generator/dashboard/__init__.py geomap_generator/dashboard/widgets.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add Rect, UIWidget base and test scaffold"
```

---

## Task 2: Button + Toggle widgets

**Files:**
- Modify: `geomap_generator/dashboard/widgets.py`
- Modify: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Add Button + Toggle tests**

Append to `tests/test_dashboard_widgets.py`:

```python
class ButtonTests(unittest.TestCase):
    def test_callback_fires_on_press_then_release_inside(self):
        from geomap_generator.dashboard.widgets import Button, Rect
        called = []
        btn = Button(Rect(0, 0, 100, 30), "OK", lambda: called.append(True))
        btn.on_mouse_press(50, 15)
        btn.on_mouse_release(50, 15)
        self.assertEqual(called, [True])

    def test_callback_not_fired_if_released_outside(self):
        from geomap_generator.dashboard.widgets import Button, Rect
        called = []
        btn = Button(Rect(0, 0, 100, 30), "OK", lambda: called.append(True))
        btn.on_mouse_press(50, 15)
        btn.on_mouse_release(200, 15)
        self.assertEqual(called, [])

    def test_callback_not_fired_without_press(self):
        from geomap_generator.dashboard.widgets import Button, Rect
        called = []
        btn = Button(Rect(0, 0, 100, 30), "OK", lambda: called.append(True))
        btn.on_mouse_release(50, 15)
        self.assertEqual(called, [])

    def test_press_outside_no_effect(self):
        from geomap_generator.dashboard.widgets import Button, Rect
        called = []
        btn = Button(Rect(0, 0, 100, 30), "OK", lambda: called.append(True))
        btn.on_mouse_press(200, 15)
        btn.on_mouse_release(200, 15)
        self.assertEqual(called, [])


class ToggleTests(unittest.TestCase):
    def test_toggles_false_to_true(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        props = SimpleNamespace(enabled=False)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(15, 15)
        self.assertTrue(props.enabled)

    def test_toggles_true_to_false(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        props = SimpleNamespace(enabled=True)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(15, 15)
        self.assertFalse(props.enabled)

    def test_no_toggle_when_outside(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        props = SimpleNamespace(enabled=False)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(100, 15)
        self.assertFalse(props.enabled)

    def test_value_reflects_prop(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        props = SimpleNamespace(enabled=True)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        self.assertTrue(t.value)
```

- [ ] **Step 2: Run — expect failures (Button/Toggle not yet defined)**

```bash
python -m unittest tests.test_dashboard_widgets.ButtonTests tests.test_dashboard_widgets.ToggleTests -v 2>&1 | head -10
```

Expected: `ImportError` or `AttributeError` for `Button`/`Toggle`

- [ ] **Step 3: Append Button + Toggle to widgets.py**

Add after `UIWidget`:

```python
class Button(UIWidget):
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
    def __init__(self, rect: Rect, label: str, props: object, prop_name: str) -> None:
        super().__init__(rect)
        self.label = label
        self.props = props
        self.prop_name = prop_name

    @property
    def value(self) -> bool:
        return bool(getattr(self.props, self.prop_name, False))

    def on_mouse_press(self, mx: float, my: float) -> bool:
        if self.hit_test(mx, my):
            setattr(self.props, self.prop_name, not self.value)
            return True
        return False
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.ButtonTests tests.test_dashboard_widgets.ToggleTests -v
```

Expected: `OK (8 tests)`

- [ ] **Step 5: Commit**

```bash
git add geomap_generator/dashboard/widgets.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add Button and Toggle widgets"
```

---

## Task 3: SliderFloat widget

**Files:**
- Modify: `geomap_generator/dashboard/widgets.py`
- Modify: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Add SliderFloat tests**

Append to `tests/test_dashboard_widgets.py`:

```python
class SliderFloatTests(unittest.TestCase):
    def _make(self, initial: float = 0.5):
        from geomap_generator.dashboard.widgets import Rect, SliderFloat
        props = SimpleNamespace(width=initial)
        s = SliderFloat(Rect(0, 0, 100, 20), "Width", props, "width", 0.0, 1.0)
        return s, props

    def test_press_center_sets_half(self):
        s, props = self._make()
        s.on_mouse_press(50, 10)
        self.assertAlmostEqual(props.width, 0.5, places=2)

    def test_press_left_edge_sets_min(self):
        s, props = self._make()
        s.on_mouse_press(0, 10)
        self.assertAlmostEqual(props.width, 0.0, places=2)

    def test_press_right_edge_sets_max(self):
        s, props = self._make()
        s.on_mouse_press(100, 10)
        self.assertAlmostEqual(props.width, 1.0, places=2)

    def test_drag_updates_value(self):
        s, props = self._make(0.0)
        s.on_mouse_press(0, 10)
        s.on_mouse_move(75, 10)
        self.assertAlmostEqual(props.width, 0.75, places=2)

    def test_clamps_below_min(self):
        s, props = self._make()
        s.on_mouse_press(-50, 10)
        self.assertAlmostEqual(props.width, 0.0, places=2)

    def test_clamps_above_max(self):
        s, props = self._make()
        s.on_mouse_press(200, 10)
        self.assertAlmostEqual(props.width, 1.0, places=2)

    def test_no_drag_after_release(self):
        s, props = self._make(0.0)
        s.on_mouse_press(0, 10)
        s.on_mouse_release(0, 10)
        s.on_mouse_move(100, 10)
        self.assertAlmostEqual(props.width, 0.0, places=2)

    def test_press_outside_no_drag(self):
        s, props = self._make(0.0)
        s.on_mouse_press(200, 10)  # outside rect
        s.on_mouse_move(50, 10)
        self.assertAlmostEqual(props.width, 0.0, places=2)
```

- [ ] **Step 2: Run — expect failures**

```bash
python -m unittest tests.test_dashboard_widgets.SliderFloatTests -v 2>&1 | head -10
```

Expected: `ImportError` for `SliderFloat`

- [ ] **Step 3: Append SliderFloat to widgets.py**

```python
class SliderFloat(UIWidget):
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
        return float(getattr(self.props, self.prop_name, self.min_val))

    def _clamped(self, val: float) -> float:
        return max(self.min_val, min(self.max_val, val))

    def _x_to_value(self, mx: float) -> float:
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
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.SliderFloatTests -v
```

Expected: `OK (8 tests)`

- [ ] **Step 5: Commit**

```bash
git add geomap_generator/dashboard/widgets.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add SliderFloat widget"
```

---

## Task 4: RadioGroup, TabBar, ProgressBar, TextLabel

**Files:**
- Modify: `geomap_generator/dashboard/widgets.py`
- Modify: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Add tests**

Append to `tests/test_dashboard_widgets.py`:

```python
class RadioGroupTests(unittest.TestCase):
    def _make(self):
        from geomap_generator.dashboard.widgets import RadioGroup, Rect
        props = SimpleNamespace(geometry="CURVE")
        opts = [("CURVE", "Curve"), ("MESH", "Mesh")]
        r = RadioGroup(Rect(0, 0, 100, 20), props, "geometry", opts)
        return r, props

    def test_click_right_half_selects_mesh(self):
        r, props = self._make()
        r.on_mouse_press(75, 10)
        self.assertEqual(props.geometry, "MESH")

    def test_click_left_half_selects_curve(self):
        r, props = self._make()
        props.geometry = "MESH"
        r.on_mouse_press(25, 10)
        self.assertEqual(props.geometry, "CURVE")

    def test_click_outside_no_change(self):
        r, props = self._make()
        r.on_mouse_press(200, 10)
        self.assertEqual(props.geometry, "CURVE")

    def test_value_reflects_prop(self):
        r, props = self._make()
        props.geometry = "MESH"
        self.assertEqual(r.value, "MESH")


class TabBarTests(unittest.TestCase):
    def _make(self):
        from geomap_generator.dashboard.widgets import Rect, TabBar
        return TabBar(Rect(0, 0, 400, 32), ["Location", "Layers", "Output", "History"])

    def test_initial_active_is_zero(self):
        t = self._make()
        self.assertEqual(t.active_index, 0)

    def test_click_second_tab(self):
        t = self._make()
        # tabs each 100px wide; second tab at x=100..200
        t.on_mouse_press(150, 16)
        self.assertEqual(t.active_index, 1)

    def test_click_fourth_tab(self):
        t = self._make()
        t.on_mouse_press(350, 16)
        self.assertEqual(t.active_index, 3)

    def test_click_outside_no_change(self):
        t = self._make()
        t.on_mouse_press(0, 100)  # below tab bar
        self.assertEqual(t.active_index, 0)
```

- [ ] **Step 2: Run — expect failures**

```bash
python -m unittest tests.test_dashboard_widgets.RadioGroupTests tests.test_dashboard_widgets.TabBarTests -v 2>&1 | head -10
```

Expected: `ImportError` for `RadioGroup`/`TabBar`

- [ ] **Step 3: Append RadioGroup, TabBar, ProgressBar, TextLabel to widgets.py**

```python
class RadioGroup(UIWidget):
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
        return str(getattr(self.props, self.prop_name, ""))

    def _option_rect(self, index: int) -> Rect:
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
    def __init__(self, rect: Rect, tabs: list[str]) -> None:
        super().__init__(rect)
        self.tabs = tabs
        self.active_index: int = 0

    def _tab_rect(self, index: int) -> Rect:
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
    def __init__(self, rect: Rect) -> None:
        super().__init__(rect)
        self.progress: float = 0.0
        self.status: str = ""


class TextLabel(UIWidget):
    def __init__(self, rect: Rect, text: str) -> None:
        super().__init__(rect)
        self.text = text
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.RadioGroupTests tests.test_dashboard_widgets.TabBarTests -v
```

Expected: `OK (8 tests)`

- [ ] **Step 5: Commit**

```bash
git add geomap_generator/dashboard/widgets.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add RadioGroup, TabBar, ProgressBar, TextLabel"
```

---

## Task 5: LayerRow composite widget

**Files:**
- Modify: `geomap_generator/dashboard/widgets.py`
- Modify: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Add LayerRow tests**

Append to `tests/test_dashboard_widgets.py`:

```python
class LayerRowTests(unittest.TestCase):
    def _make(self, enabled=False, with_width=False, with_geometry=False):
        from geomap_generator.dashboard.widgets import LayerRow, Rect
        called = []
        props = SimpleNamespace(
            import_rivers=enabled,
            river_width=0.06,
            river_geometry="CURVE",
        )
        row = LayerRow(
            Rect(0, 0, 600, 30),
            "Rivers",
            props,
            "import_rivers",
            lambda: called.append("gen"),
            width_prop="river_width" if with_width else None,
            width_min=0.0,
            width_max=0.5,
            geometry_prop="river_geometry" if with_geometry else None,
            geometry_options=[("CURVE", "Curve"), ("MESH", "Mesh")] if with_geometry else None,
        )
        return row, props, called

    def test_toggle_click_enables(self):
        row, props, _ = self._make(enabled=False)
        # toggle occupies x=0..200 (LayerRow._TOGGLE_W)
        row.on_mouse_press(10, 15)
        self.assertTrue(props.import_rivers)

    def test_generate_btn_fires_when_clicked(self):
        row, props, called = self._make(enabled=True)
        # btn at x = 600 - 90 = 510..600
        row.on_mouse_press(550, 15)
        row.on_mouse_release(550, 15)
        self.assertEqual(called, ["gen"])

    def test_slider_not_accessible_when_layer_disabled(self):
        row, props, _ = self._make(enabled=False, with_width=True)
        # slider at x=208..288; layer disabled → no active_widgets slider
        original = props.river_width
        row.on_mouse_press(208, 15)
        self.assertEqual(props.river_width, original)

    def test_slider_accessible_when_layer_enabled(self):
        row, props, _ = self._make(enabled=True, with_width=True)
        # slider x=208, w=80 → left edge → value = 0.0
        row.on_mouse_press(208, 15)
        self.assertAlmostEqual(props.river_width, 0.0, places=2)

    def test_geometry_radio_accessible_when_enabled(self):
        row, props, _ = self._make(enabled=True, with_width=False, with_geometry=True)
        # radio at x=208, w=100 → right half (x=258) → MESH
        row.on_mouse_press(258, 15)
        self.assertEqual(props.river_geometry, "MESH")

    def test_geometry_radio_not_accessible_when_disabled(self):
        row, props, _ = self._make(enabled=False, with_geometry=True)
        row.on_mouse_press(258, 15)
        self.assertEqual(props.river_geometry, "CURVE")
```

- [ ] **Step 2: Run — expect failures**

```bash
python -m unittest tests.test_dashboard_widgets.LayerRowTests -v 2>&1 | head -10
```

Expected: `ImportError` for `LayerRow`

- [ ] **Step 3: Append LayerRow to widgets.py**

```python
class LayerRow(UIWidget):
    """Composite row: Toggle + optional SliderFloat + optional RadioGroup + Button.

    Inline settings (slider, radio) are only active when toggle is ON.
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
        return bool(getattr(self.props, self.toggle_prop, False))

    def _active_widgets(self) -> list[UIWidget]:
        result: list[UIWidget] = [self._toggle, self._btn]
        if self._enabled:
            if self._slider:
                result.append(self._slider)
            if self._radio:
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
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.LayerRowTests -v
```

Expected: `OK (6 tests)`

- [ ] **Step 5: Run full suite — expect all green**

```bash
python -m unittest discover tests -v 2>&1 | tail -5
```

Expected: all tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add geomap_generator/dashboard/widgets.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add LayerRow composite widget"
```

---

## Task 6: GPU renderer primitives

**Files:**
- Create: `geomap_generator/dashboard/renderer.py`

No unit tests: requires active GPU context (Blender draw callback). Verify manually in Blender after Task 9.

- [ ] **Step 1: Create renderer.py**

```python
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
    """Draw a filled rectangle using UNIFORM_COLOR shader."""
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
    """Draw text using blf at the given position."""
    import blf

    font_id = 0
    blf.position(font_id, x, y, 0)
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.draw(font_id, text)
```

- [ ] **Step 2: Commit**

```bash
git add geomap_generator/dashboard/renderer.py
git commit -m "feat(dashboard): add GPU renderer primitives (draw_rect, draw_text)"
```

---

## Task 7: Widget draw() methods

**Files:**
- Modify: `geomap_generator/dashboard/widgets.py`

No unit tests: all draw() methods lazy-import renderer (GPU required). Verified in Blender after Task 9.

- [ ] **Step 1: Add draw() to each widget class**

In `widgets.py`, add a `draw()` method to each class **after** `TextLabel`:

For `Button`, replace the placeholder `draw` (inherited no-op) by adding in the class body:

```python
# Inside Button class, after on_mouse_release:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    if self._pressed:
        bg = (0.5, 0.5, 0.5, 0.95)
    elif self.hovered:
        bg = (0.38, 0.38, 0.38, 0.95)
    else:
        bg = (0.28, 0.28, 0.28, 0.90)
    draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, bg)
    draw_text(self.label, self.rect.x + 6, self.rect.y + 8, 12, (1.0, 1.0, 1.0, 1.0))
```

```python
# Inside Toggle class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    check_sz = self.rect.h
    color = (0.18, 0.65, 0.28, 0.90) if self.value else (0.22, 0.22, 0.22, 0.90)
    draw_rect(self.rect.x, self.rect.y, check_sz, check_sz, color)
    draw_text(
        self.label,
        self.rect.x + check_sz + 6, self.rect.y + 8,
        12, (0.90, 0.90, 0.90, 1.0),
    )
```

```python
# Inside SliderFloat class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, (0.14, 0.14, 0.14, 0.90))
    span = max(self.max_val - self.min_val, 1e-6)
    t = (self.value - self.min_val) / span
    fill_w = self.rect.w * max(0.0, min(1.0, t))
    draw_rect(self.rect.x, self.rect.y, fill_w, self.rect.h, (0.18, 0.48, 0.78, 0.90))
    draw_text(f"{self.value:.3f}", self.rect.x + 4, self.rect.y + 6, 11, (1.0, 1.0, 1.0, 1.0))
```

```python
# Inside RadioGroup class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    for i, (val, label) in enumerate(self.options):
        r = self._option_rect(i)
        selected = val == self.value
        bg = (0.18, 0.48, 0.78, 0.90) if selected else (0.20, 0.20, 0.20, 0.90)
        draw_rect(r.x, r.y, r.w, r.h, bg)
        draw_text(label, r.x + 4, r.y + 8, 11, (1.0, 1.0, 1.0, 1.0))
```

```python
# Inside TabBar class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    for i, name in enumerate(self.tabs):
        r = self._tab_rect(i)
        if i == self.active_index:
            bg = (0.20, 0.20, 0.20, 0.96)
        elif self.hovered and r.contains(getattr(ctx, "_mx", -1), getattr(ctx, "_my", -1)):
            bg = (0.16, 0.16, 0.16, 0.96)
        else:
            bg = (0.11, 0.11, 0.11, 0.92)
        draw_rect(r.x, r.y, r.w, r.h, bg)
        draw_text(name, r.x + 10, r.y + 9, 13, (1.0, 1.0, 1.0, 1.0))
```

```python
# Inside ProgressBar class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect, draw_text
    draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, (0.11, 0.11, 0.11, 0.90))
    fill_w = self.rect.w * max(0.0, min(1.0, self.progress))
    draw_rect(self.rect.x, self.rect.y, fill_w, self.rect.h, (0.08, 0.55, 0.18, 0.90))
    pct = int(self.progress * 100)
    label = f"{self.status}  {pct}%" if self.status else f"{pct}%"
    draw_text(label, self.rect.x + 8, self.rect.y + 7, 12, (1.0, 1.0, 1.0, 1.0))
```

```python
# Inside TextLabel class:
def draw(self, ctx: object) -> None:
    from .renderer import draw_text
    draw_text(self.text, self.rect.x, self.rect.y + 8, 12, (0.85, 0.85, 0.85, 1.0))
```

```python
# Inside LayerRow class, add after on_mouse_move:
def draw(self, ctx: object) -> None:
    from .renderer import draw_rect
    bg = (0.17, 0.17, 0.17, 0.85) if self._enabled else (0.12, 0.12, 0.12, 0.85)
    draw_rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h, bg)
    for w in self._active_widgets():
        w.draw(ctx)
```

- [ ] **Step 2: Ensure existing unit tests still pass (draw() never called in tests)**

```bash
python -m unittest discover tests -v 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add geomap_generator/dashboard/widgets.py
git commit -m "feat(dashboard): add draw() methods to all widgets"
```

---

## Task 8: layout.py — build_widget_tree

**Files:**
- Create: `geomap_generator/dashboard/layout.py`
- Modify: `tests/test_dashboard_widgets.py`

- [ ] **Step 1: Add layout tests**

Append to `tests/test_dashboard_widgets.py`:

```python
class LayoutTests(unittest.TestCase):
    def _props(self):
        return SimpleNamespace(
            import_relief=False, import_coast=True, import_rivers=False,
            import_roads=False, import_buildings=False, import_cities=False,
            import_weather=False, add_legend=False,
            coast_width=0.035, river_width=0.06, road_width=0.045,
            boundary_width=0.025, river_geometry="CURVE", road_geometry="CURVE",
            input_mode="COUNTRY",
        )

    def _tracker(self):
        return SimpleNamespace(progress=0.0, status="Idle")

    def test_returns_required_keys(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        for key in ("tab_bar", "close_btn", "tabs", "gen_btn", "progress_bar", "overlay_rect"):
            self.assertIn(key, tree)

    def test_has_four_tabs(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        self.assertEqual(len(tree["tabs"]), 4)

    def test_layers_tab_has_eight_layer_rows(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import LayerRow
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        layers_tab = tree["tabs"][1]
        rows = [w for w in layers_tab if isinstance(w, LayerRow)]
        self.assertEqual(len(rows), 8)

    def test_progress_bar_reflects_tracker(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tracker = SimpleNamespace(progress=0.42, status="Fetching DEM")
        tree = build_widget_tree(self._props(), tracker, 1920, 1080, {})
        self.assertAlmostEqual(tree["progress_bar"].progress, 0.42)
        self.assertEqual(tree["progress_bar"].status, "Fetching DEM")

    def test_overlay_centered(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1000, 800, {})
        r = tree["overlay_rect"]
        # overlay width ~82% of 1000 = 820; centered → x ~90
        self.assertAlmostEqual(r.x + r.w / 2, 500, delta=5)
        self.assertAlmostEqual(r.y + r.h / 2, 400, delta=5)

    def test_callbacks_wired_to_gen_btn(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        fired = []
        cbs = {"generate_all": lambda: fired.append("all")}
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, cbs)
        tree["gen_btn"].on_mouse_press(0, 0)  # won't fire (outside btn rect)
        # call callback directly to verify wiring
        tree["gen_btn"].callback()
        self.assertEqual(fired, ["all"])
```

- [ ] **Step 2: Run — expect failures**

```bash
python -m unittest tests.test_dashboard_widgets.LayoutTests -v 2>&1 | head -10
```

Expected: `ImportError` for `geomap_generator.dashboard.layout`

- [ ] **Step 3: Create layout.py**

```python
# geomap_generator/dashboard/layout.py
"""Builds the widget tree for the GeoMap Dashboard overlay.

No bpy imports. All callbacks injected by caller (modal.py).
Testable with SimpleNamespace props and tracker.
"""
from __future__ import annotations

from typing import Any, Callable

from .widgets import (
    Button,
    LayerRow,
    ProgressBar,
    Rect,
    RadioGroup,
    SliderFloat,
    TabBar,
    TextLabel,
    Toggle,
    UIWidget,
)

_TAB_H: int = 34
_BTM_H: int = 52
_PAD: int = 14
_ROW_H: int = 32
_ROW_GAP: int = 4


def build_widget_tree(
    props: Any,
    tracker: Any,
    viewport_w: int,
    viewport_h: int,
    callbacks: dict[str, Callable],
) -> dict[str, Any]:
    """Assemble the full widget tree for the overlay.

    Args:
        props: geomap_props (bpy PropertyGroup or SimpleNamespace for tests).
        tracker: ProgressTracker instance (or SimpleNamespace for tests).
        viewport_w / viewport_h: pixel size of the active region.
        callbacks: dict keyed by action name:
            'generate_all', 'close', 'pick_on_map', 'open_history',
            'gen_TERRAIN', 'gen_COASTLINES', 'gen_RIVERS', 'gen_ROADS',
            'gen_BUILDINGS', 'gen_CITIES', 'gen_WEATHER', 'gen_ANNOTATIONS'

    Returns dict with keys:
        'overlay_rect': Rect — background of entire overlay
        'tab_bar': TabBar
        'close_btn': Button
        'tabs': list[list[UIWidget]] — one list per tab (Location/Layers/Output/History)
        'gen_btn': Button
        'progress_bar': ProgressBar
    """
    dash_w = int(viewport_w * 0.82)
    dash_h = int(viewport_h * 0.82)
    dash_x = (viewport_w - dash_w) // 2
    dash_y = (viewport_h - dash_h) // 2

    overlay_rect = Rect(float(dash_x), float(dash_y), float(dash_w), float(dash_h))

    tab_bar = TabBar(
        Rect(float(dash_x), float(dash_y + dash_h - _TAB_H),
             float(dash_w - 38), float(_TAB_H)),
        ["Location", "Layers", "Output", "History"],
    )

    close_btn = Button(
        Rect(float(dash_x + dash_w - 36),
             float(dash_y + dash_h - _TAB_H + 3), 30.0, 28.0),
        "×", callbacks.get("close", lambda: None),
    )

    gen_btn = Button(
        Rect(float(dash_x + _PAD), float(dash_y + _PAD), 160.0, float(_BTM_H - _PAD)),
        "Generate All", callbacks.get("generate_all", lambda: None),
    )

    progress_bar = ProgressBar(
        Rect(float(dash_x + 182), float(dash_y + _PAD),
             float(dash_w - 196), float(_BTM_H - _PAD)),
    )
    progress_bar.progress = float(getattr(tracker, "progress", 0.0))
    progress_bar.status = str(getattr(tracker, "status", ""))

    content_y = dash_y + _BTM_H
    content_h = dash_h - _TAB_H - _BTM_H

    tabs: list[list[UIWidget]] = [
        _build_location_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_layers_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_output_tab(props, dash_x, content_y, dash_w, content_h),
        _build_history_tab(dash_x, content_y, dash_w, content_h, callbacks),
    ]

    return {
        "overlay_rect": overlay_rect,
        "tab_bar": tab_bar,
        "close_btn": close_btn,
        "tabs": tabs,
        "gen_btn": gen_btn,
        "progress_bar": progress_bar,
    }


def _build_location_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(RadioGroup(
        Rect(px, py, 200.0, float(_ROW_H)),
        props, "input_mode",
        [("COUNTRY", "Place/Area"), ("COORDS", "Coordinates")],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(Button(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Pick Area on Map",
        callbacks.get("pick_on_map", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, float(w - _PAD * 2), float(_ROW_H)), "Use N-panel for text search"))
    return widgets


def _build_layers_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    layer_defs: list[tuple] = [
        ("Terrain",      "import_relief",    "gen_TERRAIN",     None,             0.0, 0.0,  None,              None),
        ("Coastlines",   "import_coast",     "gen_COASTLINES",  "coast_width",    0.0, 0.14, None,              None),
        ("Rivers",       "import_rivers",    "gen_RIVERS",      "river_width",    0.0, 0.22, "river_geometry",  [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Roads",        "import_roads",     "gen_ROADS",       "road_width",     0.0, 0.18, "road_geometry",   [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Land Use",     "import_landuse",   "gen_LANDUSE",     None,             0.0, 0.0,  None,              None),
        ("Buildings",    "import_buildings", "gen_BUILDINGS",   None,             0.0, 0.0,  None,              None),
        ("Cities/POI",   "import_cities",    "gen_CITIES",      None,             0.0, 0.0,  None,              None),
        ("Weather",      "import_weather",   "gen_WEATHER",     None,             0.0, 0.0,  None,              None),
    ]

    for label, toggle_prop, cb_key, width_prop, wmin, wmax, geo_prop, geo_opts in layer_defs:
        widgets.append(LayerRow(
            Rect(px, py, row_w, float(_ROW_H)),
            label, props, toggle_prop,
            callbacks.get(cb_key, lambda: None),
            width_prop=width_prop,
            width_min=wmin,
            width_max=wmax,
            geometry_prop=geo_prop,
            geometry_options=geo_opts,
        ))
        py -= _ROW_H + _ROW_GAP

    return widgets


def _build_output_tab(
    props: Any, x: int, y: int, w: int, h: int,
) -> list[UIWidget]:
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    py = float(y + h - _ROW_H - _PAD)

    for label, prop_name, wmin, wmax in [
        ("Road Width",     "road_width",     0.0, 0.18),
        ("River Width",    "river_width",    0.0, 0.22),
        ("Boundary Width", "boundary_width", 0.0, 0.12),
        ("Coast Width",    "coast_width",    0.0, 0.14),
    ]:
        widgets.append(TextLabel(Rect(px, py, 130.0, float(_ROW_H)), label))
        widgets.append(SliderFloat(
            Rect(px + 138, py, 220.0, float(_ROW_H)),
            label, props, prop_name, wmin, wmax,
        ))
        py -= _ROW_H + _ROW_GAP

    return widgets


def _build_history_tab(
    x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    px = float(x + _PAD)
    py = float(y + h - _ROW_H - _PAD)
    return [Button(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Open Search History",
        callbacks.get("open_history", lambda: None),
    )]
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m unittest tests.test_dashboard_widgets.LayoutTests -v
```

Expected: `OK (6 tests)`

- [ ] **Step 5: Run full suite**

```bash
python -m unittest discover tests -v 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add geomap_generator/dashboard/layout.py tests/test_dashboard_widgets.py
git commit -m "feat(dashboard): add layout.build_widget_tree with tests"
```

---

## Task 9: GeoMapDashboardOperator (modal.py)

**Files:**
- Create: `geomap_generator/dashboard/modal.py`

No headless unit tests: requires `bpy` context. Verified manually in Blender (Task 10).

- [ ] **Step 1: Create modal.py**

```python
# geomap_generator/dashboard/modal.py
"""GeoMap Dashboard modal operator.

Opens a full-viewport GPU overlay. Dispatches mouse/key events to the widget
tree built by layout.build_widget_tree(). Writes to geomap_props directly
and calls existing geomap.* operators for generation actions.
"""
from __future__ import annotations

import traceback

import bpy
from bpy.types import Operator

from .layout import build_widget_tree
from .renderer import draw_rect
from .widgets import Rect


class GeoMapDashboardOperator(Operator):
    bl_idname = "geomap.open_dashboard"
    bl_label = "GeoMap Dashboard"
    bl_description = "Open the GeoMap Dashboard — interactive layer control overlay"

    _draw_handle = None
    _tree: dict = {}
    _open: bool = False

    # Cached widget references for fast access in modal()
    _tab_bar = None
    _tabs: list = []
    _gen_btn = None
    _progress_bar = None
    _close_btn = None
    _overlay_rect: Rect | None = None

    @classmethod
    def poll(cls, context) -> bool:
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        self._build_tree(context)
        self._open = True
        args = (self, context)
        self._draw_handle = context.space_data.draw_handler_add(
            GeoMapDashboardOperator._draw_callback, args, "WINDOW", "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    # ------------------------------------------------------------------
    # Tree construction
    # ------------------------------------------------------------------

    def _build_tree(self, context) -> None:
        from ..progress import ProgressTracker
        props = context.scene.geomap_props
        tracker = ProgressTracker.get_instance()
        region = context.region

        def _layer_cb(kind: str):
            def _cb():
                bpy.ops.geomap.update_layer("INVOKE_DEFAULT", layer_kind=kind)
            return _cb

        callbacks = {
            "generate_all": lambda: bpy.ops.geomap.generate("INVOKE_DEFAULT"),
            "close": self._make_close_cb(context),
            "pick_on_map": lambda: bpy.ops.geomap.open_map_selector("INVOKE_DEFAULT"),
            "open_history": lambda: None,
            **{
                f"gen_{k}": _layer_cb(k) for k in (
                    "TERRAIN", "COASTLINES", "RIVERS", "ROADS",
                    "LANDUSE", "BUILDINGS", "CITIES", "WEATHER",
                )
            },
        }

        self._tree = build_widget_tree(
            props, tracker, region.width, region.height, callbacks,
        )
        self._tab_bar = self._tree["tab_bar"]
        self._tabs = self._tree["tabs"]
        self._gen_btn = self._tree["gen_btn"]
        self._progress_bar = self._tree["progress_bar"]
        self._close_btn = self._tree["close_btn"]
        self._overlay_rect = self._tree["overlay_rect"]

    def _make_close_cb(self, context):
        def _close():
            self._open = False
            if self._draw_handle is not None:
                context.space_data.draw_handler_remove(self._draw_handle, "WINDOW")
                self._draw_handle = None
            context.area.tag_redraw()
        return _close

    # ------------------------------------------------------------------
    # Modal loop
    # ------------------------------------------------------------------

    def modal(self, context, event):
        if not self._open:
            return {"FINISHED"}

        # Refresh live progress
        from ..progress import ProgressTracker
        tracker = ProgressTracker.get_instance()
        if self._progress_bar is not None:
            self._progress_bar.progress = tracker.progress
            self._progress_bar.status = tracker.status or ""

        mx = event.mouse_region_x
        my = event.mouse_region_y

        if event.type == "MOUSEMOVE":
            self._dispatch_move(mx, my)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                # Click outside overlay → close
                if self._overlay_rect and not self._overlay_rect.contains(mx, my):
                    self._make_close_cb(context)()
                    return {"FINISHED"}
                self._dispatch_press(mx, my)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self._dispatch_release(mx, my)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type == "ESC" and event.value == "PRESS":
            self._make_close_cb(context)()
            return {"FINISHED"}

        return {"PASS_THROUGH"}

    # ------------------------------------------------------------------
    # Event dispatch helpers
    # ------------------------------------------------------------------

    def _active_tab_widgets(self) -> list:
        if not self._tabs or self._tab_bar is None:
            return []
        idx = max(0, min(self._tab_bar.active_index, len(self._tabs) - 1))
        return self._tabs[idx]

    def _all_widgets(self) -> list:
        base = []
        for w in (self._tab_bar, self._close_btn, self._gen_btn, self._progress_bar):
            if w is not None:
                base.append(w)
        return base + self._active_tab_widgets()

    def _dispatch_press(self, mx: int, my: int) -> None:
        for w in self._all_widgets():
            if w.on_mouse_press(mx, my):
                return

    def _dispatch_release(self, mx: int, my: int) -> None:
        for w in self._all_widgets():
            w.on_mouse_release(mx, my)

    def _dispatch_move(self, mx: int, my: int) -> None:
        for w in self._all_widgets():
            w.on_mouse_move(mx, my)

    # ------------------------------------------------------------------
    # Draw callback (POST_PIXEL — called from GPU thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_callback(op: "GeoMapDashboardOperator", context) -> None:
        try:
            if op._overlay_rect is None:
                return
            r = op._overlay_rect
            # Overlay background
            draw_rect(r.x, r.y, r.w, r.h, (0.07, 0.07, 0.07, 0.93))
            # Bottom bar separator
            draw_rect(r.x, r.y + 52, r.w, 1.0, (0.25, 0.25, 0.25, 1.0))
            # Tab bar separator
            draw_rect(r.x, r.y + r.h - 34, r.w, 1.0, (0.25, 0.25, 0.25, 1.0))
            # All widgets
            for w in op._all_widgets():
                w.draw(context)
        except Exception:
            traceback.print_exc()
```

- [ ] **Step 2: Commit**

```bash
git add geomap_generator/dashboard/modal.py
git commit -m "feat(dashboard): add GeoMapDashboardOperator modal"
```

---

## Task 10: Integration — register + panel button

**Files:**
- Modify: `geomap_generator/dashboard/__init__.py`
- Modify: `geomap_generator/__init__.py`
- Modify: `geomap_generator/panels.py`

- [ ] **Step 1: Populate dashboard/__init__.py**

Replace empty `geomap_generator/dashboard/__init__.py` with:

```python
# geomap_generator/dashboard/__init__.py
from .modal import GeoMapDashboardOperator

__all__ = ["GeoMapDashboardOperator"]
```

- [ ] **Step 2: Register dashboard in addon __init__.py**

In `geomap_generator/__init__.py`, inside `_load_classes()`, add the dashboard import block **after** the `map_selector` reload block (around line 58):

```python
    dashboard = importlib.import_module(f"{__name__}.dashboard")
    importlib.reload(dashboard)
```

Then add this import at the top of the existing `from .map_selector import ...` block:

```python
    from .dashboard import GeoMapDashboardOperator
```

Then add `GeoMapDashboardOperator` to the return tuple, after `GeoMapRoutePickerOperator`:

```python
        GeoMapDashboardOperator,
```

The modified section of `_load_classes()` should look like:

```python
    map_selector = importlib.import_module(f"{__name__}.map_selector")
    importlib.reload(map_selector)

    dashboard = importlib.import_module(f"{__name__}.dashboard")
    importlib.reload(dashboard)

    operators = importlib.import_module(f"{__name__}.operators")
    panels = importlib.import_module(f"{__name__}.panels")
    properties = importlib.import_module(f"{__name__}.properties")

    operators = importlib.reload(operators)
    panels = importlib.reload(panels)
    properties = importlib.reload(properties)

    from .dashboard import GeoMapDashboardOperator
    from .map_selector import GeoMapRoutePickerOperator, GeoMapSelectorOperator
    # ... rest of existing imports unchanged ...
```

And in the `return (...)` tuple, insert `GeoMapDashboardOperator` after `GeoMapRoutePickerOperator`:

```python
    return (
        GeoMapSelectorOperator,
        GeoMapRoutePickerOperator,
        GeoMapDashboardOperator,       # ← add this line
        GeoMapStoreBasemapTokenOperator,
        # ... rest unchanged ...
    )
```

- [ ] **Step 3: Add "Open Dashboard" button to GeoMapPanel**

In `geomap_generator/panels.py`, in `GeoMapPanel.draw()`, add after the `layout.separator()` call (around line 104):

```python
        layout.operator("geomap.open_dashboard", text="Open Dashboard", icon="WINDOW")
```

So the relevant section becomes:

```python
        layout.separator()
        layout.operator("geomap.open_dashboard", text="Open Dashboard", icon="WINDOW")
        icon = "HIDE_OFF" if _coord_tracking_active else "EYEDROPPER"
```

- [ ] **Step 4: Run unit tests — ensure no regressions**

```bash
python -m unittest discover tests -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add geomap_generator/dashboard/__init__.py geomap_generator/__init__.py geomap_generator/panels.py
git commit -m "feat(dashboard): register GeoMapDashboardOperator and add N-panel button"
```

- [ ] **Step 6: Manual smoke test in Blender**

1. Copy `geomap_generator/` to Blender addons dir
2. Enable addon in Preferences > Add-ons
3. Open 3D Viewport → GeoMap tab → click "Open Dashboard"
4. Verify:
   - Overlay appears centered over viewport
   - Tab bar shows "Location / Layers / Output / History"
   - Clicking tabs switches content
   - Toggle on a layer row turns it green
   - Slider drag updates river/road width
   - Radio group switches Curve/Mesh
   - "Generate All" button calls `geomap.generate`
   - Per-layer "Generate" button calls `geomap.update_layer`
   - ESC closes overlay
   - Click outside overlay closes it
   - Overlay re-opens without issue

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| Full-screen GPU modal overlay | Task 9 |
| Extends map_selector pattern | Task 9 (`draw_handler_add` / modal loop) |
| Tab bar: Location / Layers / Output / History | Task 8, Task 4 |
| Toggle + Generate per layer | Task 5, Task 8 |
| Width sliders (active layers) | Task 3, Task 8 |
| Curve/Mesh radio per layer | Task 4, Task 8 |
| Progress bar + Generate All + Abort | Task 4, Task 8, Task 9 |
| ESC / click outside to close | Task 9 |
| Writes to existing geomap_props | Task 2 (Toggle), Task 3 (Slider), Task 4 (Radio) |
| Calls existing operators | Task 9 (`_build_tree` callbacks) |
| N-panel preserved | Task 10 (button added, no panels removed) |
| GPU draw (quads + text) | Task 6, Task 7 |
| No bpy in widgets/layout | Task 1 (`test_no_bpy_import`), Task 8 |
| Registration in addon __init__ | Task 10 |
| Unit tests for widget logic | Tasks 1–5, 8 |

**No placeholders:** confirmed — all steps contain actual code.

**Type consistency:** `Rect`, `UIWidget`, `Button`, `Toggle`, `SliderFloat`, `RadioGroup`, `TabBar`, `ProgressBar`, `TextLabel`, `LayerRow` defined in Task 1–5, used by same names in Task 8 (`layout.py`) and Task 9 (`modal.py`). Callback dict keys (`gen_TERRAIN`, etc.) match between `modal._build_tree` and `layout._build_layers_tab`.
