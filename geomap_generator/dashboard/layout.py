# geomap_generator/dashboard/layout.py
"""Builds the widget tree for the GeoMap Dashboard overlay.

No bpy imports. All callbacks are injected by the caller (modal.py).
Fully testable with SimpleNamespace props and tracker.

Layout constants follow Blender 4.x N-panel proportions:
  _ROW_H   = 22 px  (standard widget row — matches Blender's 1 UI unit)
  _ROW_GAP =  2 px  (between rows — same tight spacing as N-panel)
  _PAD     = 10 px  (content inset from overlay edges)
  _TAB_H   = 26 px  (tab bar — slightly taller for hit-area comfort)
  _BTN_H   = 26 px  (generate / action buttons)
  _SEP_H   =  8 px  (Separator widget height)
  _HDR_H   = 20 px  (SectionHeader widget height)
"""
from __future__ import annotations

from typing import Any, Callable

from .widgets import (
    Button,
    LayerRow,
    ProgressBar,
    Rect,
    RadioGroup,
    SectionHeader,
    Separator,
    SliderFloat,
    TabBar,
    TextLabel,
    TextInput,
    Toggle,
    UIWidget,
)

# -- Spacing constants (Blender N-panel proportions) -----------------------
_TAB_H   = 26
_BTN_H   = 26
_PAD     = 10
_ROW_H   = 22
_ROW_GAP = 2
_SEP_H   = 8    # Separator widget height
_HDR_H   = 20   # SectionHeader widget height

# Label column width used across tabs (property name, left side of row)
_LABEL_W = 120


def build_widget_tree(
    props: Any,
    tracker: Any,
    viewport_w: int,
    viewport_h: int,
    callbacks: dict[str, Callable],
    history_entries: list[dict] | None = None,
    kmz_entries: list[tuple[str, str, str]] | None = None,
    preset_entries: list[dict] | None = None,
    layer_entries: list[dict] | None = None,
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
            'gen_ANNOTATIONS', 'import_kmz',
            'route_add', 'route_remove', 'route_pick_start', 'route_pick_end',
            'route_import', 'route_import_all', route search/select callbacks
        history_entries: list of {'label': str, 'index': int} from search cache.

    Returns dict with keys:
        'overlay_rect': Rect
        'tab_bar': TabBar
        'close_btn': Button
        'tabs': list[list[UIWidget]]
        'gen_btn': Button
        'progress_bar': ProgressBar
        'weather_progress_bar': ProgressBar
        'log_y': float — y baseline for log line rendering (modal.py draws directly)
        'sep_btm_y': float — y of bottom separator line
    """
    dash_w = int(viewport_w * 0.82)
    dash_h = int(viewport_h * 0.82)
    dash_x = (viewport_w - dash_w) // 2
    dash_y = (viewport_h - dash_h) // 2

    overlay_rect = Rect(float(dash_x), float(dash_y), float(dash_w), float(dash_h))

    tab_names = [
        "Location", "Layers", "Routes", "Weather",
        "Style", "Settings", "Import", "History", "Generate",
    ]

    tab_bar = TabBar(
        Rect(float(dash_x), float(dash_y + dash_h - _TAB_H),
             float(dash_w - 38), float(_TAB_H)),
        tab_names,
    )

    close_btn = Button(
        Rect(float(dash_x + dash_w - 38),
             float(dash_y + dash_h - _TAB_H + 3), 30.0, 20.0),
        "×", callbacks.get("close", lambda: None),
    )

    content_y = dash_y
    content_h = dash_h - _TAB_H

    tabs: list[list[UIWidget]] = [
        _build_location_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_layers_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_routes_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_weather_tab(
            props, tracker, dash_x, content_y, dash_w, content_h, callbacks,
        ),
        _build_style_tab(props, dash_x, content_y, dash_w, content_h, callbacks),
        _build_settings_tab(
            props, dash_x, content_y, dash_w, content_h, callbacks,
            preset_entries or [], layer_entries or [],
        ),
        _build_kmz_tab(
            props, dash_x, content_y, dash_w, content_h, callbacks,
            kmz_entries or [],
        ),
        _build_history_tab(
            history_entries or [], dash_x, content_y, dash_w, content_h, callbacks,
        ),
        _build_generate_tab(
            tracker, dash_x, content_y, dash_w, content_h, callbacks,
        ),
    ]
    gen_btn = tabs[-1][0]
    progress_bar = tabs[-1][1]
    weather_progress_bar = tabs[3][1]

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


# ---------------------------------------------------------------------------
# Tab builders
# ---------------------------------------------------------------------------

def _build_location_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Location tab: input mode selector + matching input fields."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    # Input mode selector (COUNTRY / COORDS)
    widgets.append(RadioGroup(
        Rect(px, py, 220.0, float(_ROW_H)),
        props, "input_mode",
        [("COUNTRY", "Place / Area"), ("COORDS", "Coordinates")],
    ))
    py -= _ROW_H + _ROW_GAP + 2

    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    if getattr(props, "input_mode", "COUNTRY") == "COUNTRY":
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Search Area"))
        widgets.append(TextInput(
            Rect(px + _LABEL_W + 8, py, min(520.0, row_w - _LABEL_W - 8), float(_ROW_H)),
            props, "country_region", "City, region or country …",
        ))
        py -= _ROW_H + _ROW_GAP
    else:
        coord_w = min(160.0, (row_w - _LABEL_W - 24) / 2.0)
        for label, lat_p, lon_p in [
            ("Point A", "latitude",  "longitude"),
            ("Point B", "latitude2", "longitude2"),
        ]:
            widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), label))
            widgets.append(TextInput(
                Rect(px + _LABEL_W + 8, py, coord_w, float(_ROW_H)),
                props, lat_p, "Latitude",
            ))
            widgets.append(TextInput(
                Rect(px + _LABEL_W + 16 + coord_w, py, coord_w, float(_ROW_H)),
                props, lon_p, "Longitude",
            ))
            py -= _ROW_H + _ROW_GAP

    py -= _ROW_GAP
    widgets.append(Button(
        Rect(px, py, 200.0, float(_ROW_H)),
        "Pick Area on Map",
        callbacks.get("pick_on_map", lambda: None),
    ))
    return widgets


def _build_layers_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Layers tab: grouped LayerRows with section headers."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    _dem_res_opts = [
        ("DEM_LOW", "Lo"), ("DEM_MEDIUM", "Med"),
        ("DEM_HIGH", "Hi"), ("DEM_ULTRA", "4K"),
    ]
    _style_opts = [("SATELLITE", "Satellite"), ("STREETS", "Streets"), ("TOPO", "Topo")]

    # ── Terrain ──────────────────────────────────────────────────────────
    widgets.append(SectionHeader(Rect(px, py, row_w, float(_HDR_H)), "Terrain"))
    py -= _HDR_H + _ROW_GAP

    for label, toggle_prop, cb_key, width_prop, wmin, wmax, geo_prop, geo_opts in [
        ("DEM",
         "import_relief",   "gen_TERRAIN",
         None, 0.0, 0.0, "dem_resolution", _dem_res_opts),
        ("Imagery",
         "import_satellite", "gen_TERRAIN",
         "satellite_resolution", 512, 4096, "map_style", _style_opts),
    ]:
        widgets.append(LayerRow(
            Rect(px, py, row_w, float(_ROW_H)),
            label, props, toggle_prop,
            callbacks.get(cb_key, lambda: None),
            width_prop=width_prop, width_min=wmin, width_max=wmax,
            geometry_prop=geo_prop, geometry_options=geo_opts,
        ))
        py -= _ROW_H + _ROW_GAP

        if label == "DEM" and bool(getattr(props, "import_relief", False)):
            widgets.append(TextLabel(
                Rect(px + 24, py, _LABEL_W, float(_ROW_H - 2)), "Height Scale",
            ))
            widgets.append(SliderFloat(
                Rect(px + 24 + _LABEL_W + 8, py, 200.0, float(_ROW_H - 2)),
                "Height Scale", props, "dem_height_scale", 0.0001, 0.02,
            ))
            py -= (_ROW_H - 2) + _ROW_GAP

    # ── Vector Layers ─────────────────────────────────────────────────────
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(
        Rect(px, py, row_w, float(_HDR_H)), "Vector Layers",
    ))
    py -= _HDR_H + _ROW_GAP

    for label, toggle_prop, cb_key, width_prop, wmin, wmax, geo_prop, geo_opts in [
        ("Coastlines", "import_coast",   "gen_COASTLINES",
         "coast_width", 0.0, 0.14, None, None),
        ("Rivers",     "import_rivers",  "gen_RIVERS",
         "river_width", 0.0, 0.22,
         "river_geometry", [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Roads",      "import_roads",   "gen_ROADS",
         "road_width", 0.0, 0.18,
         "road_geometry", [("CURVE", "Curve"), ("MESH", "Mesh")]),
        ("Land Use",   "import_landuse", "gen_LANDUSE",
         None, 0.0, 0.0, None, None),
    ]:
        widgets.append(LayerRow(
            Rect(px, py, row_w, float(_ROW_H)),
            label, props, toggle_prop,
            callbacks.get(cb_key, lambda: None),
            width_prop=width_prop, width_min=wmin, width_max=wmax,
            geometry_prop=geo_prop, geometry_options=geo_opts,
        ))
        py -= _ROW_H + _ROW_GAP

    # ── Points of Interest ────────────────────────────────────────────────
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(
        Rect(px, py, row_w, float(_HDR_H)), "Points of Interest",
    ))
    py -= _HDR_H + _ROW_GAP

    widgets.append(LayerRow(
        Rect(px, py, row_w, float(_ROW_H)),
        "Cities / POI", props, "import_cities",
        callbacks.get("gen_CITIES", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    # POI sub-type toggles
    widgets.append(TextLabel(Rect(px + 24, py, 80.0, float(_ROW_H)), "POI Types"))
    tx = px + 110.0
    toggle_w = 110.0
    for text, prop_name in [
        ("Historic",    "import_poi_historic"),
        ("Cultural",    "import_poi_cultural"),
        ("Admin",       "import_poi_administrative"),
        ("Natural",     "import_poi_natural"),
    ]:
        widgets.append(Toggle(Rect(tx, py, toggle_w - 6, float(_ROW_H)), text, props, prop_name))
        tx += toggle_w
    py -= _ROW_H + _ROW_GAP

    # -- 3D Buildings --
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(Rect(px, py, row_w, float(_HDR_H)), "3D Buildings"))
    py -= _HDR_H + _ROW_GAP

    widgets.append(Toggle(
        Rect(px, py, 200.0, float(_ROW_H)), "3D Buildings", props, "import_buildings",
    ))
    widgets.append(Button(
        Rect(px + row_w - 160.0, py, 160.0, float(_ROW_H)),
        "Generate Buildings", callbacks.get("gen_BUILDINGS", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Quality"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 380.0, float(_ROW_H)),
        props, "building_quality",
        [("AUTO", "Auto"), ("SIMPLE", "Simple"), ("DETAILED", "Detailed")],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Source"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 540.0, float(_ROW_H)),
        props, "building_provider",
        [
            ("AUTO", "Auto"),
            ("OVERPASS_MAIN", "Overpass"),
            ("OVERPASS_PRIVATE_COFFEE", "Private.coffee"),
            ("OVERPASS_MAPRVA", "MapRVA"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    # -- Map Overlays --
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(Rect(px, py, row_w, float(_HDR_H)), "Map Overlays"))
    py -= _HDR_H + _ROW_GAP

    _ann_gap = 8.0
    _ann_w = 150.0
    tx = px
    for ann_label, ann_prop in [
        ("Legend", "add_legend"),
        ("Scale Bar", "add_scale_bar"),
        ("North Arrow", "add_north_arrow"),
    ]:
        widgets.append(Toggle(Rect(tx, py, _ann_w, float(_ROW_H)), ann_label, props, ann_prop))
        tx += _ann_w + _ann_gap
    widgets.append(Button(
        Rect(px + row_w - 180.0, py, 180.0, float(_ROW_H)),
        "Generate Annotations", callbacks.get("gen_ANNOTATIONS", lambda: None),
    ))
    return widgets


def _build_style_tab(
    props: Any, x: int, y: int, _w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Style tab: place label sizes and fonts, plus vector layer line widths."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    py = float(y + h - _ROW_H - _PAD)

    # -- Place Labels --
    widgets.append(SectionHeader(Rect(px, py, 500.0, float(_HDR_H)), "Place Labels"))
    py -= _HDR_H + _ROW_GAP

    widgets.append(Toggle(
        Rect(px, py, 200.0, float(_ROW_H)), "Place Labels", props, "import_place_labels",
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Show From"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 480.0, float(_ROW_H)),
        props, "place_label_min_type",
        [
            ("capital", "Capitals"), ("city", "Cities"), ("town", "Towns"),
            ("village", "Villages"), ("hamlet", "All"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Global Size"))
    widgets.append(SliderFloat(
        Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
        "Global Size", props, "place_label_size_factor", 0.1, 5.0,
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(Separator(Rect(px, py, 500.0, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(
        Rect(px, py, 500.0, float(_HDR_H)), "Per-Type Size & Font",
    ))
    py -= _HDR_H + _ROW_GAP

    for place_type, lbl in [
        ("capital", "Capital"), ("city", "City"), ("town", "Town"),
        ("village", "Village"), ("hamlet", "Hamlet"),
        ("historic", "Historic"), ("cultural", "Cultural"),
        ("administrative", "Admin"), ("natural", "Natural"),
    ]:
        widgets.append(TextLabel(Rect(px, py, 80.0, float(_ROW_H)), lbl))
        widgets.append(SliderFloat(
            Rect(px + 86, py, 150.0, float(_ROW_H)),
            f"{lbl} Size", props, f"place_label_size_{place_type}", 0.1, 5.0,
        ))
        widgets.append(RadioGroup(
            Rect(px + 244, py, 320.0, float(_ROW_H)),
            props, f"place_label_font_{place_type}",
            [
                ("DEFAULT", "Default"), ("SANS", "Sans"),
                ("SERIF", "Serif"), ("MONO", "Mono"),
            ],
        ))
        py -= _ROW_H + _ROW_GAP

    # -- Line Widths --
    widgets.append(Separator(Rect(px, py, 300.0, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(Rect(px, py, 300.0, float(_HDR_H)), "Line Widths"))
    py -= _HDR_H + _ROW_GAP

    for width_lbl, prop_name, wmin, wmax in [
        ("Road Width",     "road_width",     0.0, 0.18),
        ("River Width",    "river_width",    0.0, 0.22),
        ("Boundary Width", "boundary_width", 0.0, 0.12),
        ("Coast Width",    "coast_width",    0.0, 0.14),
    ]:
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), width_lbl))
        widgets.append(SliderFloat(
            Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
            width_lbl, props, prop_name, wmin, wmax,
        ))
        py -= _ROW_H + _ROW_GAP
    return widgets


def _build_kmz_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
    kmz_entries: list[tuple[str, str, str]],
) -> list[UIWidget]:
    """KMZ tab: available catalog entries and import action."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    entries = kmz_entries or [
        ("NONE", "No KMZ available", "No KMZ catalog entry intersects the current area"),
    ]
    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Available KMZ"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, min(740.0, row_w - _LABEL_W - 8), float(_ROW_H)),
        props, "kmz_selection",
        [(item[0], item[1][:18]) for item in entries[:8]],
    ))
    py -= _ROW_H + _ROW_GAP

    selected_id = str(getattr(props, "kmz_selection", "NONE"))
    selected = next((item for item in entries if item[0] == selected_id), None)
    if selected is not None:
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)), str(selected[2])[:120],
        ))
        py -= _ROW_H + _ROW_GAP

    can_import = selected_id != "NONE"
    widgets.append(Button(
        Rect(px, py, 220.0, float(_ROW_H)),
        "Download and Integrate KMZ" if can_import else "No KMZ Selected",
        callbacks.get("import_kmz", lambda: None) if can_import else (lambda: None),
    ))
    return widgets


def _build_settings_tab(
    props: Any, x: int, y: int, w: int, h: int, callbacks: dict,
    preset_entries: list[dict],
    layer_entries: list[dict],
) -> list[UIWidget]:
    """Settings tab: quality/output presets, scene settings, and layer visibility."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    # ── Presets ───────────────────────────────────────────────────────────
    widgets.append(SectionHeader(Rect(px, py, row_w, float(_HDR_H)), "Presets"))
    py -= _HDR_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Quality"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 580.0, float(_ROW_H)),
        props, "quality_preset",
        [
            ("CUSTOM", "Custom"), ("PREVIEW", "Preview"),
            ("BALANCED", "Balanced"), ("HIGH_QUALITY", "High"),
            ("LARGE_AREA", "Large"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Detail"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 290.0, float(_ROW_H)),
        props, "detail_level",
        [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")],
    ))
    widgets.append(Toggle(
        Rect(px + _LABEL_W + 8 + 298, py, 160.0, float(_ROW_H)),
        "Auto Quality", props, "auto_lod",
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Output"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 580.0, float(_ROW_H)),
        props, "output_preset",
        [
            ("CUSTOM", "Custom"), ("BLENDER_VIEW", "View"),
            ("RENDER", "Render"), ("PRINT_3D", "Print"),
            ("GAME_ENGINE", "Game"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    # ── Scene Settings ────────────────────────────────────────────────────
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(
        Rect(px, py, row_w, float(_HDR_H)), "Scene Settings",
    ))
    py -= _HDR_H + _ROW_GAP

    gap = 8.0
    widgets.append(Toggle(
        Rect(px, py, 190.0, float(_ROW_H)), "Drape on DEM", props, "drape_vectors_on_dem",
    ))
    widgets.append(Toggle(
        Rect(px + 190.0 + gap, py, 140.0, float(_ROW_H)), "Map Box", props, "create_map_box",
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Height Scale"))
    widgets.append(SliderFloat(
        Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
        "Height Scale", props, "height_exaggeration", 0.1, 20.0,
    ))
    py -= _ROW_H + _ROW_GAP

    if getattr(props, "create_map_box", False):
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Box Depth"))
        widgets.append(SliderFloat(
            Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
            "Box Depth", props, "map_box_depth", 0.01, 5.0,
        ))
        py -= _ROW_H + _ROW_GAP

    if getattr(props, "output_preset", "") == "PRINT_3D":
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Print Base"))
        widgets.append(SliderFloat(
            Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
            "Print Base", props, "print_base_height", 0.0, 5.0,
        ))
        py -= _ROW_H + _ROW_GAP

    # ── Saved Presets ─────────────────────────────────────────────────────
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(Button(
        Rect(px, py, 170.0, float(_ROW_H)),
        "Save Current Preset", callbacks.get("save_preset", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    if preset_entries:
        widgets.append(SectionHeader(
            Rect(px, py, row_w, float(_HDR_H)), "Saved Presets",
        ))
        py -= _HDR_H + _ROW_GAP
        for i, preset in enumerate(preset_entries[:6]):
            name = str(preset.get("preset_name") or preset.get("label") or "?")[:34]
            widgets.append(Button(
                Rect(px, py, 240.0, float(_ROW_H)),
                name, callbacks.get(f"load_preset_{i}", lambda: None),
            ))
            widgets.append(Button(
                Rect(px + 248.0, py, 76.0, float(_ROW_H)),
                "Delete", callbacks.get(f"delete_preset_{i}", lambda: None),
            ))
            py -= _ROW_H + _ROW_GAP

    # -- Layer Visibility --
    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(
        Rect(px, py, row_w, float(_HDR_H)), "Layer Visibility",
    ))
    py -= _HDR_H + _ROW_GAP

    if not layer_entries:
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)), "No GeoMap layers in scene",
        ))
    else:
        bottom = float(y + _ROW_H + _PAD)
        widgets.append(TextLabel(Rect(px, py, 260.0, float(_ROW_H)), "Layer"))
        widgets.append(TextLabel(Rect(px + 300.0, py, 110.0, float(_ROW_H)), "Viewport"))
        widgets.append(TextLabel(Rect(px + 430.0, py, 110.0, float(_ROW_H)), "Render"))
        py -= _ROW_H + _ROW_GAP
        widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
        py -= _SEP_H + _ROW_GAP
        for entry in layer_entries:
            if py < bottom:
                break
            layer = entry.get("layer")
            if layer is None:
                continue
            name = str(entry.get("name") or getattr(layer, "name", "Layer"))[:36]
            widgets.append(TextLabel(Rect(px, py, 280.0, float(_ROW_H)), name))
            widgets.append(Toggle(
                Rect(px + 300.0, py, 110.0, float(_ROW_H)), "Hide", layer, "hide_viewport",
            ))
            widgets.append(Toggle(
                Rect(px + 430.0, py, 110.0, float(_ROW_H)), "Hide", layer, "hide_render",
            ))
            py -= _ROW_H + _ROW_GAP
    return widgets


def _build_weather_tab(
    props: Any, tracker: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Weather tab: forecast provider, day and sampling controls."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(Toggle(
        Rect(px, py, 180.0, float(_ROW_H)), "Weather Layer", props, "import_weather",
    ))
    if getattr(props, "import_weather", False):
        widgets.append(Button(
            Rect(px + row_w - 155.0, py, 155.0, float(_ROW_H)),
            "Generate Weather", callbacks.get("gen_WEATHER", lambda: None),
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

    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    widgets.append(SectionHeader(Rect(px, py, row_w, float(_HDR_H)), "Settings"))
    py -= _HDR_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Provider"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 400.0, float(_ROW_H)),
        props, "weather_provider",
        [
            ("AUTO", "Auto"), ("OPEN_METEO", "Open-Meteo"),
            ("OPENWEATHERMAP", "OWM"), ("WEATHERAPI", "WeatherAPI"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Temperature"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 260.0, float(_ROW_H)),
        props, "weather_unit",
        [("CELSIUS", "Celsius"), ("FAHRENHEIT", "Fahrenheit")],
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Forecast Day"))
    widgets.append(SliderFloat(
        Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
        "Forecast Day", props, "weather_forecast_day", 0, 7,
    ))
    day_label = _forecast_day_label(int(getattr(props, "weather_forecast_day", 0)))
    widgets.append(TextLabel(
        Rect(px + _LABEL_W + 216, py, 200.0, float(_ROW_H)), day_label,
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Height Above DEM"))
    widgets.append(SliderFloat(
        Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
        "Height Above DEM", props, "weather_z_offset", 0.0, 2.0,
    ))
    widgets.append(Toggle(
        Rect(px + _LABEL_W + 224, py, 150.0, float(_ROW_H)),
        "Follow DEM", props, "weather_follow_dem",
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Orientation"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 260.0, float(_ROW_H)),
        props, "weather_orientation",
        [("HORIZONTAL", "Horizontal"), ("VERTICAL", "Vertical")],
    ))
    widgets.append(TextLabel(Rect(px + _LABEL_W + 286, py, 64.0, float(_ROW_H)), "Z Rot"))
    widgets.append(SliderFloat(
        Rect(px + _LABEL_W + 352, py, 180.0, float(_ROW_H)),
        "Z Rotation", props, "weather_z_rotation", -180.0, 180.0,
    ))
    py -= _ROW_H + _ROW_GAP

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Granularity"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 460.0, float(_ROW_H)),
        props, "weather_granularity",
        [
            ("MAIN_CITY", "Main"), ("CITIES", "Cities"),
            ("LOCALITIES", "Localities"), ("GRID", "Grid"),
        ],
    ))
    py -= _ROW_H + _ROW_GAP

    if getattr(props, "weather_granularity", "GRID") == "GRID":
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Grid Size"))
        widgets.append(SliderFloat(
            Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
            "Grid Size", props, "weather_grid_size", 1, 9,
        ))
        py -= _ROW_H + _ROW_GAP

    widgets.append(Toggle(
        Rect(px, py, 160.0, float(_ROW_H)), "Temperature", props, "weather_show_temperature",
    ))
    widgets.append(Toggle(
        Rect(px + 168.0, py, 150.0, float(_ROW_H)), "Wind Arrows", props, "weather_show_wind",
    ))
    if getattr(props, "weather_show_temperature", True):
        py -= _ROW_H + _ROW_GAP
        widgets.append(Toggle(
            Rect(px, py, 200.0, float(_ROW_H)), "Min/Max Range", props, "weather_show_temp_range",
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
    """Routes tab: route mode, profile, and per-route controls."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)

    widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Route Mode"))
    widgets.append(RadioGroup(
        Rect(px + _LABEL_W + 8, py, 200.0, float(_ROW_H)),
        props, "route_mode",
        [("ROUTE", "Route"), ("STRAIGHT", "Straight")],
    ))
    py -= _ROW_H + _ROW_GAP

    if getattr(props, "route_mode", "ROUTE") == "ROUTE":
        widgets.append(TextLabel(Rect(px, py, _LABEL_W, float(_ROW_H)), "Profile"))
        widgets.append(RadioGroup(
            Rect(px + _LABEL_W + 8, py, 240.0, float(_ROW_H)),
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
        "No saved routes — use the buttons below or open the full route panel."
    )
    widgets.append(TextLabel(Rect(px, py, row_w, float(_ROW_H)), summary))
    py -= _ROW_H + _ROW_GAP

    if active is not None:
        name = str(getattr(active, "name", f"Route {active_idx + 1}"))[:36]
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)), f"Active: {name}",
        ))
        py -= _ROW_H + _ROW_GAP

    btn_w = 120.0
    gap = 6.0
    for i, route in enumerate(routes[:5]):
        label = str(getattr(route, "name", f"Route {i + 1}"))[:16]
        if i == active_idx:
            label = f"● {label}"
        widgets.append(Button(
            Rect(px + i * (btn_w + gap), py, btn_w, float(_ROW_H)),
            label,
            callbacks.get(f"route_select_{i}", lambda idx=i: setattr(
                props, "route_active_index", idx,
            )),
        ))
    if routes:
        py -= _ROW_H + _ROW_GAP

    widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
    py -= _SEP_H + _ROW_GAP

    # Route action buttons — three rows of two so they don't overlap
    half = 148.0
    for left_label, left_cb, right_label, right_cb in [
        ("Add Route",    "route_add",         "Remove Route",    "route_remove"),
        ("Pick Start",   "route_pick_start",  "Pick End",        "route_pick_end"),
        ("Import Route", "route_import",      "Import All",      "route_import_all"),
    ]:
        widgets.append(Button(
            Rect(px, py, half, float(_ROW_H)),
            left_label, callbacks.get(left_cb, lambda: None),
        ))
        widgets.append(Button(
            Rect(px + half + gap, py, half, float(_ROW_H)),
            right_label, callbacks.get(right_cb, lambda: None),
        ))
        py -= _ROW_H + _ROW_GAP

    if active is not None:
        widgets.append(Separator(Rect(px, py, row_w, float(_SEP_H))))
        py -= _SEP_H + _ROW_GAP

        widgets.append(TextLabel(Rect(px, py, 72.0, float(_ROW_H)), "Name"))
        widgets.append(TextInput(
            Rect(px + 78.0, py, 240.0, float(_ROW_H)), active, "name", "Route name",
        ))
        widgets.append(RadioGroup(
            Rect(px + 326.0, py, 200.0, float(_ROW_H)),
            active, "mode",
            [("ROUTE", "Route"), ("STRAIGHT", "Straight")],
        ))
        py -= _ROW_H + _ROW_GAP

        if getattr(active, "mode", "ROUTE") == "ROUTE":
            widgets.append(TextLabel(Rect(px, py, 72.0, float(_ROW_H)), "Profile"))
            widgets.append(RadioGroup(
                Rect(px + 78.0, py, 240.0, float(_ROW_H)),
                active, "profile",
                [("driving", "Drive"), ("walking", "Walk"), ("cycling", "Cycle")],
            ))
            py -= _ROW_H + _ROW_GAP

        coord_w = 120.0
        for title, lat_prop, lon_prop, label_prop in [
            ("Start", "lat1", "lon1", "label_start"),
            ("End",   "lat2", "lon2", "label_end"),
        ]:
            widgets.append(TextLabel(Rect(px, py, 62.0, float(_ROW_H)), title))
            widgets.append(TextInput(
                Rect(px + 68.0, py, coord_w, float(_ROW_H)),
                active, lat_prop, f"{title} lat",
            ))
            widgets.append(TextInput(
                Rect(px + 76.0 + coord_w, py, coord_w, float(_ROW_H)),
                active, lon_prop, f"{title} lon",
            ))
            widgets.append(TextInput(
                Rect(px + 84.0 + coord_w * 2, py, 200.0, float(_ROW_H)),
                active, label_prop, f"{title} label",
            ))
            py -= _ROW_H + _ROW_GAP

        for idx, (label, color) in enumerate([
            ("Red",   (0.9, 0.15, 0.05, 1.0)),
            ("Blue",  (0.10, 0.42, 0.95, 1.0)),
            ("Green", (0.18, 0.70, 0.25, 1.0)),
        ]):
            widgets.append(Button(
                Rect(px + idx * 84.0, py, 76.0, float(_ROW_H)),
                label, lambda c=color: setattr(active, "color", c),
            ))
        return widgets

    # Search fields when no active route
    widgets.append(TextLabel(Rect(px, py, 72.0, float(_ROW_H)), "Search"))
    widgets.append(TextInput(
        Rect(px + 78.0, py, 340.0, float(_ROW_H)),
        props, "route_search_query", "Search within map area",
    ))
    widgets.append(Button(
        Rect(px + 426.0, py, 90.0, float(_ROW_H)),
        "To Start", callbacks.get("route_search_start", lambda: None),
    ))
    widgets.append(Button(
        Rect(px + 524.0, py, 80.0, float(_ROW_H)),
        "To End", callbacks.get("route_search_end", lambda: None),
    ))
    py -= _ROW_H + _ROW_GAP

    for title, lat_prop, lon_prop, label_prop in [
        ("Start", "route_lat1", "route_lon1", "route_label_start"),
        ("End",   "route_lat2", "route_lon2", "route_label_end"),
    ]:
        coord_w = 120.0
        widgets.append(TextLabel(Rect(px, py, 62.0, float(_ROW_H)), title))
        widgets.append(TextInput(
            Rect(px + 68.0, py, coord_w, float(_ROW_H)),
            props, lat_prop, f"{title} lat",
        ))
        widgets.append(TextInput(
            Rect(px + 76.0 + coord_w, py, coord_w, float(_ROW_H)),
            props, lon_prop, f"{title} lon",
        ))
        widgets.append(TextInput(
            Rect(px + 84.0 + coord_w * 2, py, 200.0, float(_ROW_H)),
            props, label_prop, f"{title} label",
        ))
        py -= _ROW_H + _ROW_GAP
    return widgets


def _build_history_tab(
    history_entries: list, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """History tab: one load-button per search history entry + Clear."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _ROW_H - _PAD)
    bottom = float(y + _ROW_H + _PAD)

    if not history_entries:
        widgets.append(TextLabel(
            Rect(px, py, row_w, float(_ROW_H)), "No search history",
        ))
        return widgets

    for entry in history_entries:
        if py < bottom:
            break
        label = str(entry.get("label", "Untitled"))[:48]
        cb_key = f"load_history_{entry['index']}"
        widgets.append(Button(
            Rect(px, py, row_w, float(_ROW_H)),
            label, callbacks.get(cb_key, lambda: None),
        ))
        py -= _ROW_H + _ROW_GAP

    if py >= bottom:
        py -= _ROW_GAP
        widgets.append(Button(
            Rect(px, py, 140.0, float(_ROW_H)),
            "Clear History", callbacks.get("clear_history", lambda: None),
        ))
    return widgets


def _build_generate_tab(
    tracker: Any, x: int, y: int, w: int, h: int, callbacks: dict,
) -> list[UIWidget]:
    """Generate tab: main action button + live progress bar."""
    widgets: list[UIWidget] = []
    px = float(x + _PAD)
    row_w = float(w - _PAD * 2)
    py = float(y + h - _BTN_H - _PAD)

    gen_btn = Button(
        Rect(px, py, 180.0, float(_BTN_H)),
        "Generate Map", callbacks.get("generate_all", lambda: None),
    )
    widgets.append(gen_btn)
    py -= _BTN_H + _ROW_GAP

    progress_bar = ProgressBar(Rect(px, py, row_w, float(_BTN_H)))
    progress_bar.progress = float(getattr(tracker, "progress", 0.0))
    progress_bar.status = str(getattr(tracker, "status", ""))
    widgets.append(progress_bar)
    return widgets
