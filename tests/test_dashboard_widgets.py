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


class RadioGroupTests(unittest.TestCase):
    def _make(self):
        from geomap_generator.dashboard.widgets import RadioGroup, Rect
        from types import SimpleNamespace
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
        # 4 tabs each 100px wide; second tab at x=100..200
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


class LayerRowTests(unittest.TestCase):
    def _make(self, enabled=False, with_width=False, with_geometry=False):
        from geomap_generator.dashboard.widgets import LayerRow, Rect
        from types import SimpleNamespace
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
        # toggle occupies x=0..200 (LayerRow._TOGGLE_W = 200)
        row.on_mouse_press(10, 15)
        self.assertTrue(props.import_rivers)

    def test_generate_btn_fires_when_clicked(self):
        row, props, called = self._make(enabled=True)
        # btn at x = 600 - 90 = 510..600 (LayerRow._BTN_W = 90)
        row.on_mouse_press(550, 15)
        row.on_mouse_release(550, 15)
        self.assertEqual(called, ["gen"])

    def test_slider_not_accessible_when_layer_disabled(self):
        row, props, _ = self._make(enabled=False, with_width=True)
        # slider at x=208..288; layer disabled → not in active_widgets → no change
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
