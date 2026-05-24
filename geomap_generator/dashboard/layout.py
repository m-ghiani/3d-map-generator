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
    Toggle,
    UIWidget,
)

_TAB_H: int = 36
_BTN_H: int = 36    # height of gen/abort button and progress bar
_PAD: int = 18
_ROW_H: int = 34
_ROW_GAP: int = 8


def build_widget_tree(
    props: Any,
    tracker: Any,
    viewport_w: int,
    viewport_h: int,
    callbacks: dict[str, Callable],
    history_entries: list[dict] | None = None,
) -> dict[str, Any]:
    """Assemble the full widget tree for the overlay.

    Args:
        props: geomap_props (bpy PropertyGroup or SimpleNamespace for tests).
        tracker: ProgressTracker instance (or SimpleNamespace for tests).
        viewport_w / viewport_h: pixel size of the active region.
        callbacks: dict keyed by action name:
            'generate_all', 'close', 'pick_on_map',
            'load_history_N', 'clear_history',
            'gen_TERRAIN', 'gen_COASTLINES', 'gen_RIVERS', 'gen_ROADS',
            'gen_LANDUSE', 'gen_BUILDINGS', 'gen_CITIES', 'gen_WEATHER',
            'route_add', 'route_remove', 'route_pick_start', 'route_pick_end',
            'route_import', 'route_import_all', 'route_open_panel'
        history_entries: list of {'label': str, 'index': int} from search cache.

    Returns dict with keys:
        'overlay_rect': Rect
        'tab_bar': TabBar
        'close_btn': Button
        'tabs': list[list[UIWidget]]
        'gen_btn': Button
        'progress_bar': ProgressBar
        'log_y': float — y baseline for log line rendering (modal.py draws directly)
        'sep_btm_y': float — y of bottom separator line
    """
    dash_w = int(viewport_w * 0.82)
    dash_h = int(viewport_h * 0.82)
    dash_x = (viewport_w - dash_w) // 2
    dash_y = (viewport_h - dash_h) // 2

    overlay_rect = Rect(float(dash_x), float(dash_y), float(dash_w), float(dash_h))

    tab_names = [
        "Location", "Layers", "Weather", "Labels", "Buildings", "Routes",
        "Output", "History", "Generate",
    ]

    tab_bar = TabBar(
        Rect(float(dash_x), float(dash_y + dash_h - _TAB_H),
             float(dash_w - 38), float(_TAB_H)),
        tab_names,
    )

    close_btn = Button(
        Rect(float(dash_x + dash_w - 38),
             float(dash_y + dash_h - _TAB_H + 4), 30.0, 28.0),
        "×", callbacks.get("close", lambda: None),
    )

    content_y = dash_y
    content_h = dash_h - _TAB_H

    tabs: list[list[UIWidget]] = [
        _build_location_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_layers_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_weather_tab(
            props, tracker, dash_x, content_y, dash_w, content_h, callbacks,
        ),
        _build_labels_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_buildings_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_routes_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_output_tab(props, dash_x, content_y, dash_w, content_h),
        _build_history_tab(
            history_entries or [], dash_x, content_y, dash_w, content_h, callbacks,
        ),
        _build_generate_tab(
            tracker, dash_x, content_y, dash_w, content_h, callbacks,
        ),
    ]
    gen_btn = tabs[-1][0]
    progress_bar = tabs[-1][1]
    weather_progress_bar = tabs[2][1]

    return {
        "overlay_rect": overlay_rect,
        "tab_bar": tab_bar,
        "close_btn": close_btn,
        "tabs": tabs,
        "gen_btn": gen_btn,
        "progress_bar": progress_bar,
        "weather_progress_bar": weather_progress_bar,
        "log_y": float(content_y + content_h - _PAD - (_BTN_H * 2) - _ROW_GAP - 58),
        "sep_btm_y": None,
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
         "satellite_resolution", 512, 4096,
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
        ("Cities/POI", "import_cities",   "gen_CITIES",
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

        if label == "Cities/POI":
            # Sub-button: generate a text label from the currently selected POI object
            sub_h = _ROW_H - 6
            widgets.append(Button(
                Rect(px + 24, py, 260.0, float(sub_h)),
                "↳ Create Label from Selected POI",
                callbacks.get("create_place_label", lambda: None),
            ))
            py -= sub_h + _ROW_GAP
        elif label == "DEM" and bool(getattr(props, "import_relief", False)):
            sub_h = _ROW_H - 6
            widgets.append(TextLabel(
                Rect(px + 24, py, 130.0, float(sub_h)),
                "DEM Height Scale",
            ))
            widgets.append(SliderFloat(
                Rect(px + 162, py, 240.0, float(sub_h)),
                "DEM Height Scale",
                props,
                "dem_height_scale",
                0.0001,
                0.02,
            ))
            py -= sub_h + _ROW_GAP

    return widgets


def _build_buildings_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Buildings tab: 3D building generation controls."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(Toggle(
        Rect(px, py, 220.0, float(_ROW_H)),
        "3D Buildings",
        props,
        "import_buildings",
    ))
    widgets.append(Button(
        Rect(px + row_w - 180.0, py, 180.0, float(_ROW_H)),
        "Generate Buildings",
        callbacks.get("gen_BUILDINGS", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 90.0, float(_ROW_H)), "Quality"))
    widgets.append(RadioGroup(
        Rect(px + 100, py, 420.0, float(_ROW_H)),
        props,
        "building_quality",
        [
            ("AUTO", "Auto"),
            ("SIMPLE", "Simple"),
            ("DETAILED", "Detailed"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 90.0, float(_ROW_H)), "Source"))
    widgets.append(RadioGroup(
        Rect(px + 100, py, 560.0, float(_ROW_H)),
        props,
        "building_provider",
        [
            ("AUTO", "Auto"),
            ("OVERPASS_MAIN", "Overpass"),
            ("OVERPASS_PRIVATE_COFFEE", "Private.coffee"),
            ("OVERPASS_MAPRVA", "MapRVA"),
        ],
    ))
    return widgets


def _build_labels_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Labels tab: per-place label size and font controls."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(Toggle(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Place Labels",
        props,
        "import_place_labels",
    ))
    widgets.append(Button(
        Rect(px + row_w - 220.0, py, 220.0, float(_ROW_H)),
        "Create Label from Selected POI",
        callbacks.get("create_place_label", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Show From"))
    widgets.append(RadioGroup(
        Rect(px + 130, py, 520.0, float(_ROW_H)),
        props,
        "place_label_min_type",
        [
            ("capital", "Capitals"),
            ("city", "Cities"),
            ("town", "Towns"),
            ("village", "Villages"),
            ("hamlet", "All"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Global Size"))
    widgets.append(SliderFloat(
        Rect(px + 130, py, 220.0, float(_ROW_H)),
        "Global Size",
        props,
        "place_label_size_factor",
        0.1,
        5.0,
    ))
    py -= _ROW_H + _ROW_GAP

    for place_type, label in [
        ("capital", "Capital"),
        ("city", "City"),
        ("town", "Town"),
        ("village", "Village"),
        ("hamlet", "Hamlet"),
        ("historic", "Historic"),
        ("cultural", "Cultural"),
        ("administrative", "Admin"),
        ("natural", "Natural"),
    ]:
        widgets.append(TextLabel(Rect(px, py, 90.0, float(_ROW_H)), label))
        widgets.append(SliderFloat(
            Rect(px + 96, py, 160.0, float(_ROW_H)),
            f"{label} Size",
            props,
            f"place_label_size_{place_type}",
            0.1,
            5.0,
        ))
        widgets.append(RadioGroup(
            Rect(px + 270, py, 360.0, float(_ROW_H)),
            props,
            f"place_label_font_{place_type}",
            [
                ("DEFAULT", "Default"),
                ("SANS", "Sans"),
                ("SERIF", "Serif"),
                ("MONO", "Mono"),
            ],
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


def _build_weather_tab(
    props: Any, tracker: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Weather tab: forecast provider, day and sampling controls."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(Toggle(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Weather Layer",
        props,
        "import_weather",
    ))
    if getattr(props, "import_weather", False):
        widgets.append(Button(
            Rect(px + row_w - 170.0, py, 170.0, float(_ROW_H)),
            "Generate Weather",
            callbacks.get("gen_WEATHER", lambda: None),
        ))
    py -= _ROW_H + _ROW_GAP

    weather_progress = ProgressBar(Rect(px, py, row_w, float(_ROW_H)))
    weather_progress.progress = float(getattr(tracker, "weather_progress", 0.0))
    weather_progress.status = str(getattr(tracker, "weather_status", "Weather idle"))
    weather_progress.visible = bool(getattr(props, "import_weather", False))
    widgets.append(weather_progress)

    if not getattr(props, "import_weather", False):
        return widgets

    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Provider"))
    widgets.append(RadioGroup(
        Rect(px + 130, py, 430.0, float(_ROW_H)),
        props, "weather_provider",
        [
            ("AUTO", "Auto"),
            ("OPEN_METEO", "Open-Meteo"),
            ("OPENWEATHERMAP", "OWM"),
            ("WEATHERAPI", "WeatherAPI"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Forecast Day"))
    widgets.append(SliderFloat(
        Rect(px + 130, py, 220.0, float(_ROW_H)),
        "Forecast Day",
        props,
        "weather_forecast_day",
        0,
        7,
    ))
    widgets.append(TextLabel(
        Rect(px + 360, py, 260.0, float(_ROW_H)),
        _forecast_day_label(int(getattr(props, "weather_forecast_day", 0))),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Icon Height"))
    widgets.append(SliderFloat(
        Rect(px + 130, py, 220.0, float(_ROW_H)),
        "Icon Height",
        props,
        "weather_z_offset",
        0.0,
        1.0,
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Granularity"))
    widgets.append(RadioGroup(
        Rect(px + 130, py, 520.0, float(_ROW_H)),
        props, "weather_granularity",
        [
            ("MAIN_CITY", "Main"),
            ("CITIES", "Cities"),
            ("LOCALITIES", "Localities"),
            ("GRID", "Grid"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    if getattr(props, "weather_granularity", "GRID") == "GRID":
        widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Grid Size"))
        widgets.append(SliderFloat(
            Rect(px + 130, py, 220.0, float(_ROW_H)),
            "Grid Size",
            props,
            "weather_grid_size",
            1,
            9,
        ))
        py -= _ROW_H + _ROW_GAP

    widgets.append(Toggle(
        Rect(px, py, 180.0, float(_ROW_H)),
        "Temperature",
        props,
        "weather_show_temperature",
    ))
    widgets.append(Toggle(
        Rect(px + 190, py, 180.0, float(_ROW_H)),
        "Wind Arrows",
        props,
        "weather_show_wind",
    ))
    return widgets


def _forecast_day_label(day: int) -> str:
    if day <= 0:
        return "Today"
    if day == 1:
        return "Tomorrow"
    return f"+{day} days"


def _build_routes_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Routes tab: route controls backed by the existing route operators."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(TextLabel(Rect(px, py, row_w, float(_ROW_H)), "Route Mode"))
    widgets.append(RadioGroup(
        Rect(px + 130, py, 220.0, float(_ROW_H)),
        props, "route_mode",
        [("ROUTE", "Route"), ("STRAIGHT", "Straight")],
    ))
    py -= _ROW_H + _ROW_GAP

    if getattr(props, "route_mode", "ROUTE") == "ROUTE":
        widgets.append(TextLabel(Rect(px, py, 120.0, float(_ROW_H)), "Profile"))
        widgets.append(RadioGroup(
            Rect(px + 130, py, 260.0, float(_ROW_H)),
            props, "route_profile",
            [("driving", "Drive"), ("walking", "Walk"), ("cycling", "Cycle")],
        ))
        py -= _ROW_H + _ROW_GAP

    routes = list(getattr(props, "routes", []) or [])
    active_idx = int(getattr(props, "route_active_index", 0))
    active = routes[active_idx] if 0 <= active_idx < len(routes) else None
    summary = (
        f"{len(routes)} route(s) configured"
        if routes else
        "No saved routes. Use the buttons below or open the full route panel."
    )
    widgets.append(TextLabel(Rect(px, py, row_w, float(_ROW_H)), summary))
    py -= _ROW_H + _ROW_GAP

    if active is not None:
        name = str(getattr(active, "name", f"Route {active_idx + 1}"))[:36]
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)),
            f"Active: {name}",
        ))
        py -= _ROW_H + _ROW_GAP

    route_btn_w = 132.0
    gap = 8.0
    for i, route in enumerate(routes[:5]):
        label = str(getattr(route, "name", f"Route {i + 1}"))[:18]
        if i == active_idx:
            label = f"* {label}"
        widgets.append(Button(
            Rect(px + i * (route_btn_w + gap), py, route_btn_w, float(_ROW_H)),
            label,
            lambda idx=i: setattr(props, "route_active_index", idx),
        ))
    if routes:
        py -= _ROW_H + _ROW_GAP

    left_w = 150.0
    widgets.append(Button(
        Rect(px, py, left_w, float(_ROW_H)),
        "Add Route",
        callbacks.get("route_add", lambda: None),
    ))
    widgets.append(Button(
        Rect(px + left_w + gap, py, left_w, float(_ROW_H)),
        "Remove Route",
        callbacks.get("route_remove", lambda: None),
    ))
    widgets.append(Button(
        Rect(px + (left_w + gap) * 2, py, 170.0, float(_ROW_H)),
        "Open Route Panel",
        callbacks.get("route_open_panel", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(Button(
        Rect(px, py, left_w, float(_ROW_H)),
        "Pick Start",
        callbacks.get("route_pick_start", lambda: None),
    ))
    widgets.append(Button(
        Rect(px + left_w + gap, py, left_w, float(_ROW_H)),
        "Pick End",
        callbacks.get("route_pick_end", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(Button(
        Rect(px, py, left_w, float(_ROW_H)),
        "Import Route",
        callbacks.get("route_import", lambda: None),
    ))
    widgets.append(Button(
        Rect(px + left_w + gap, py, left_w, float(_ROW_H)),
        "Import All Routes",
        callbacks.get("route_import_all", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(
        Rect(px, py, row_w, float(_ROW_H)),
        "Use the route panel for search, coordinates, labels, and colors.",
    ))
    return widgets


def _build_history_tab(
    history_entries: list,
    x: int,
    y: int,
    w: int,
    h: int,
    callbacks: dict,
) -> list[UIWidget]:
    """Build History tab: one load-button per search history entry + Clear."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)
    bottom = float(y + _ROW_H + _PAD)

    if not history_entries:
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)),
            "No search history",
        ))
        return widgets

    for entry in history_entries:
        if py < bottom:
            break
        label = str(entry.get("label", "Untitled"))[:48]
        cb_key = f"load_history_{entry['index']}"
        widgets.append(Button(
            Rect(px, py, row_w, float(_ROW_H)),
            label,
            callbacks.get(cb_key, lambda: None),
        ))
        py -= _ROW_H + _ROW_GAP

    if py >= bottom:
        py -= _ROW_GAP
        widgets.append(Button(
            Rect(px, py, 160.0, float(_ROW_H)),
            "Clear History",
            callbacks.get("clear_history", lambda: None),
        ))
    return widgets


def _build_generate_tab(
    tracker: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Build Generate tab: main generation action and live progress state."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _BTN_H - _PAD)

    gen_btn = Button(
        Rect(px, py, 180.0, float(_BTN_H)),
        "Generate Map",
        callbacks.get("generate_all", lambda: None),
    )
    widgets.append(gen_btn)
    py -= _BTN_H + _ROW_GAP

    progress_bar = ProgressBar(Rect(px, py, row_w, float(_BTN_H)))
    progress_bar.progress = float(getattr(tracker, "progress", 0.0))
    progress_bar.status = str(getattr(tracker, "status", ""))
    widgets.append(progress_bar)
    return widgets
