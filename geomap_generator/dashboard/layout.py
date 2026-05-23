# geomap_generator/dashboard/layout.py
"""Builds the widget tree for the GeoMap Dashboard overlay.

No bpy imports. All callbacks are injected by the caller (modal.py).
Fully testable with SimpleNamespace props and tracker.
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
            'gen_LANDUSE', 'gen_BUILDINGS', 'gen_CITIES', 'gen_WEATHER'

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
    """Build Location tab: input mode radio + map picker button + hint."""
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

    widgets.append(TextLabel(
        Rect(px, py, float(w - _PAD * 2), float(_ROW_H)),
        "Use N-panel for text search",
    ))
    return widgets


def _build_layers_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Layers tab: one LayerRow per layer."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    # Each tuple: (label, toggle_prop, cb_key, width_prop, wmin, wmax,
    #              geometry_prop, geometry_options)
    # Terrain is split into DEM + Imagery rows so map type and resolution
    # are accessible from the primary workflow without opening the N-panel.
    _dem_res_opts = [
        ("DEM_LOW", "Lo"), ("DEM_MEDIUM", "Med"),
        ("DEM_HIGH", "Hi"), ("DEM_ULTRA", "4K"),
    ]
    _style_opts = [
        ("SATELLITE", "Sat"), ("STREETS", "Str"), ("TOPO", "Topo"),
    ]
    layer_defs: list[tuple] = [
        ("DEM",
         "import_relief", "gen_TERRAIN",
         None, 0.0, 0.0,
         "dem_resolution", _dem_res_opts),
        ("Imagery",
         "import_satellite", "gen_TERRAIN",
         "satellite_resolution", 512.0, 4096.0,
         "map_style", _style_opts),
        ("Coastlines",
         "import_coast", "gen_COASTLINES",
         "coast_width", 0.0, 0.14, None, None),
        ("Rivers",
         "import_rivers", "gen_RIVERS",
         "river_width", 0.0, 0.22,
         "river_geometry", [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Roads",
         "import_roads", "gen_ROADS",
         "road_width", 0.0, 0.18,
         "road_geometry", [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Land Use",  "import_landuse",   "gen_LANDUSE",
         None, 0.0, 0.0, None, None),
        ("Buildings", "import_buildings", "gen_BUILDINGS",
         None, 0.0, 0.0, None, None),
        ("Cities/POI", "import_cities",   "gen_CITIES",
         None, 0.0, 0.0, None, None),
        ("Weather",   "import_weather",   "gen_WEATHER",
         None, 0.0, 0.0, None, None),
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
    """Build Output tab: width sliders for vector layers."""
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
    """Build History tab: single button to open search history."""
    px = float(x + _PAD)
    py = float(y + h - _ROW_H - _PAD)
    return [Button(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Open Search History",
        callbacks.get("open_history", lambda: None),
    )]
