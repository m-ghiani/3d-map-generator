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

    def test_int_property_gets_int_value(self):
        from geomap_generator.dashboard.widgets import Rect, SliderFloat
        props = SimpleNamespace(satellite_resolution=2048)
        s = SliderFloat(
            Rect(0, 0, 100, 20),
            "Resolution",
            props,
            "satellite_resolution",
            512,
            4096,
        )
        s.on_mouse_press(50, 10)
        self.assertIs(type(props.satellite_resolution), int)


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
        # toggle occupies the left side of the row.
        row.on_mouse_press(10, 15)
        self.assertTrue(props.import_rivers)

    def test_generate_btn_fires_when_clicked(self):
        row, props, called = self._make(enabled=True)
        # generate button sits at the right edge of the row.
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
        # slider left edge maps to min value.
        row.on_mouse_press(row._slider.rect.x, 15)
        self.assertAlmostEqual(props.river_width, 0.0, places=2)

    def test_geometry_radio_accessible_when_enabled(self):
        row, props, _ = self._make(enabled=True, with_width=False, with_geometry=True)
        # second radio segment selects mesh.
        row.on_mouse_press(row._radio.rect.x + row._radio.rect.w * 0.75, 15)
        self.assertEqual(props.river_geometry, "MESH")

    def test_geometry_radio_not_accessible_when_disabled(self):
        row, props, _ = self._make(enabled=False, with_geometry=True)
        row.on_mouse_press(row._radio.rect.x + row._radio.rect.w * 0.75, 15)
        self.assertEqual(props.river_geometry, "CURVE")


class LayoutTests(unittest.TestCase):
    def _props(self):
        return SimpleNamespace(
            import_relief=False, import_satellite=False,
            import_coast=True, import_rivers=False,
            import_roads=False, import_landuse=False, import_buildings=False,
            import_cities=False, add_legend=False,
            building_quality="AUTO", building_provider="AUTO",
            drape_vectors_on_dem=True,
            vector_z_offset=0.03, dem_height_scale=0.001,
            height_exaggeration=1.0,
            coast_width=0.035, river_width=0.06, road_width=0.045,
            boundary_width=0.025, river_geometry="CURVE", road_geometry="CURVE",
            dem_resolution="DEM_MEDIUM", map_style="SATELLITE",
            satellite_resolution=2048,
            input_mode="COUNTRY",
            route_mode="ROUTE", route_profile="driving",
            routes=[], route_active_index=0,
            import_weather=False, weather_provider="AUTO",
            weather_forecast_day=0, weather_granularity="GRID",
            weather_show_temperature=True, weather_show_wind=True,
            weather_grid_size=3, weather_z_offset=0.12,
            import_place_labels=False, place_label_min_type="town",
            place_label_size_factor=1.0,
            place_label_size_capital=1.0, place_label_size_city=1.0,
            place_label_size_town=1.0, place_label_size_village=1.0,
            place_label_size_hamlet=1.0,
            place_label_size_historic=1.0, place_label_size_cultural=1.0,
            place_label_size_administrative=1.0, place_label_size_natural=1.0,
            place_label_font_capital="DEFAULT", place_label_font_city="DEFAULT",
            place_label_font_town="DEFAULT", place_label_font_village="DEFAULT",
            place_label_font_hamlet="DEFAULT",
            place_label_font_historic="DEFAULT", place_label_font_cultural="DEFAULT",
            place_label_font_administrative="DEFAULT", place_label_font_natural="DEFAULT",
        )

    def _tracker(self):
        return SimpleNamespace(progress=0.0, status="Idle")

    def test_returns_required_keys(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        for key in (
            "tab_bar", "close_btn", "tabs", "gen_btn", "progress_bar",
            "weather_progress_bar", "overlay_rect",
        ):
            self.assertIn(key, tree)

    def test_has_nine_tabs(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        self.assertEqual(len(tree["tabs"]), 9)

    def test_layers_tab_has_seven_layer_rows(self):
        # DEM + Imagery + Coastlines + Rivers + Roads + Land Use +
        # Cities/POI = 7 rows
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import LayerRow
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        layers_tab = tree["tabs"][1]
        rows = [w for w in layers_tab if isinstance(w, LayerRow)]
        self.assertEqual(len(rows), 7)

    def test_dem_layer_shows_height_scale_when_enabled(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import SliderFloat
        props = self._props()
        props.import_relief = True
        tree = build_widget_tree(props, self._tracker(), 1920, 1080, {})
        layers_tab = tree["tabs"][1]
        slider_props = [w.prop_name for w in layers_tab if isinstance(w, SliderFloat)]
        self.assertIn("dem_height_scale", slider_props)

    def test_progress_bar_reflects_tracker(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tracker = SimpleNamespace(progress=0.42, status="Fetching DEM")
        tree = build_widget_tree(self._props(), tracker, 1920, 1080, {})
        self.assertAlmostEqual(tree["progress_bar"].progress, 0.42)
        self.assertEqual(tree["progress_bar"].status, "Fetching DEM")

    def test_routes_tab_has_route_actions(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import Button
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        routes_tab = tree["tabs"][5]
        labels = [w.label for w in routes_tab if isinstance(w, Button)]
        for label in ("Add Route", "Pick Start", "Pick End", "Import Route"):
            self.assertIn(label, labels)

    def test_buildings_tab_has_generation_and_quality_controls(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import Button, RadioGroup, SliderFloat
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        buildings_tab = tree["tabs"][4]
        button_labels = [w.label for w in buildings_tab if isinstance(w, Button)]
        radio_props = [w.prop_name for w in buildings_tab if isinstance(w, RadioGroup)]
        slider_props = [w.prop_name for w in buildings_tab if isinstance(w, SliderFloat)]
        self.assertIn("Generate Buildings", button_labels)
        self.assertIn("building_quality", radio_props)
        self.assertIn("building_provider", radio_props)
        self.assertEqual(slider_props, [])

    def test_labels_tab_has_per_type_controls(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import RadioGroup, SliderFloat
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        labels_tab = tree["tabs"][3]
        slider_props = [w.prop_name for w in labels_tab if isinstance(w, SliderFloat)]
        radio_props = [w.prop_name for w in labels_tab if isinstance(w, RadioGroup)]
        self.assertIn("place_label_size_city", slider_props)
        self.assertIn("place_label_font_city", radio_props)
        self.assertIn("place_label_size_historic", slider_props)
        self.assertIn("place_label_font_historic", radio_props)

    def test_weather_tab_hides_preferences_when_disabled(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import RadioGroup, Toggle
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        weather_tab = tree["tabs"][2]
        self.assertEqual(len([w for w in weather_tab if isinstance(w, Toggle)]), 1)
        self.assertFalse(any(isinstance(w, RadioGroup) for w in weather_tab))

    def test_weather_tab_has_forecast_controls_when_enabled(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        from geomap_generator.dashboard.widgets import Button, ProgressBar, RadioGroup, SliderFloat
        props = self._props()
        props.import_weather = True
        tree = build_widget_tree(props, self._tracker(), 1920, 1080, {})
        weather_tab = tree["tabs"][2]
        labels = [w.label for w in weather_tab if isinstance(w, Button)]
        self.assertIn("Generate Weather", labels)
        radio_props = [w.prop_name for w in weather_tab if isinstance(w, RadioGroup)]
        self.assertIn("weather_provider", radio_props)
        self.assertIn("weather_granularity", radio_props)
        slider_props = [w.prop_name for w in weather_tab if isinstance(w, SliderFloat)]
        self.assertIn("weather_z_offset", slider_props)
        self.assertTrue(any(isinstance(w, ProgressBar) for w in weather_tab))

    def test_generate_tab_contains_gen_button_and_progress(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, {})
        generate_tab = tree["tabs"][8]
        self.assertIs(tree["gen_btn"], generate_tab[0])
        self.assertIs(tree["progress_bar"], generate_tab[1])

    def test_overlay_centered(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        tree = build_widget_tree(self._props(), self._tracker(), 1000, 800, {})
        r = tree["overlay_rect"]
        self.assertAlmostEqual(r.x + r.w / 2, 500, delta=5)
        self.assertAlmostEqual(r.y + r.h / 2, 400, delta=5)

    def test_callbacks_wired_to_gen_btn(self):
        from geomap_generator.dashboard.layout import build_widget_tree
        fired = []
        cbs = {"generate_all": lambda: fired.append("all")}
        tree = build_widget_tree(self._props(), self._tracker(), 1920, 1080, cbs)
        tree["gen_btn"].callback()
        self.assertEqual(fired, ["all"])
