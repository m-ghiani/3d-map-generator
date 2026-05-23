# tests/test_dashboard_widgets.py
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import pathlib


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
        from types import SimpleNamespace
        props = SimpleNamespace(enabled=False)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(15, 15)
        self.assertTrue(props.enabled)

    def test_toggles_true_to_false(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        from types import SimpleNamespace
        props = SimpleNamespace(enabled=True)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(15, 15)
        self.assertFalse(props.enabled)

    def test_no_toggle_when_outside(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        from types import SimpleNamespace
        props = SimpleNamespace(enabled=False)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        t.on_mouse_press(100, 15)
        self.assertFalse(props.enabled)

    def test_value_reflects_prop(self):
        from geomap_generator.dashboard.widgets import Rect, Toggle
        from types import SimpleNamespace
        props = SimpleNamespace(enabled=True)
        t = Toggle(Rect(0, 0, 30, 30), "Enable", props, "enabled")
        self.assertTrue(t.value)


class SliderFloatTests(unittest.TestCase):
    def _make(self, initial: float = 0.5):
        from geomap_generator.dashboard.widgets import Rect, SliderFloat
        from types import SimpleNamespace
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
        s.on_mouse_press(10, 10)    # inside → starts drag
        s.on_mouse_move(-50, 10)    # drag past left edge → clamped to 0.0
        self.assertAlmostEqual(props.width, 0.0, places=2)

    def test_clamps_above_max(self):
        s, props = self._make()
        s.on_mouse_press(90, 10)    # inside → starts drag
        s.on_mouse_move(200, 10)    # drag past right edge → clamped to 1.0
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
