# geomap_generator/dashboard/modal.py
"""GeoMap Dashboard modal operator.

Opens a full-viewport GPU overlay. Dispatches mouse/key events to the widget
tree built by layout.build_widget_tree(). Writes to geomap_props directly
and calls existing geomap.* operators for generation actions.
"""
from __future__ import annotations

import traceback
from typing import Callable

import bpy
from bpy.types import Operator

from .layout import build_widget_tree
from .renderer import draw_rect
from .widgets import Rect


class GeoMapDashboardOperator(Operator):
    """Open the GeoMap Dashboard overlay for interactive layer control."""

    bl_idname = "geomap.open_dashboard"
    bl_label = "GeoMap Dashboard"
    bl_description = "Open the GeoMap Dashboard — interactive layer control overlay"

    _draw_handle = None
    _tree: dict = {}
    _open: bool = False

    _tab_bar = None
    _tabs: list = []
    _gen_btn = None
    _progress_bar = None
    _weather_progress_bar = None
    _close_btn = None
    _overlay_rect: Rect | None = None
    _log_y: float | None = None
    _sep_btm_y: float | None = None
    _generate_cb: Callable | None = None

    @classmethod
    def poll(cls, context) -> bool:
        """Only available in VIEW_3D."""
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event) -> set[str]:
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

    def _build_tree(self, context, active_tab_index: int | None = None) -> None:
        """Build the widget tree from current context and register callbacks."""
        from ..progress import ProgressTracker
        props = context.scene.geomap_props
        tracker = ProgressTracker.get_instance()
        region = context.region

        def _layer_cb(kind: str):
            def _cb():
                bpy.ops.geomap.update_layer("INVOKE_DEFAULT", layer_kind=kind)
            return _cb

        def _op_and_rebuild(op_call: Callable[[], None]):
            def _cb():
                op_call()
                idx = self._tab_bar.active_index if self._tab_bar is not None else 0
                self._build_tree(context, idx)
            return _cb

        self._generate_cb = lambda: bpy.ops.geomap.generate("INVOKE_DEFAULT")

        callbacks: dict[str, Callable] = {
            "generate_all": self._generate_cb,
            "close": self._make_close_cb(context),
            "pick_on_map": lambda: bpy.ops.geomap.open_map_selector("INVOKE_DEFAULT"),
            "clear_history": lambda: bpy.ops.geomap.clear_history("INVOKE_DEFAULT"),
            "create_place_label": self._create_place_label_cb,
            "import_kmz": self._import_kmz_cb,
            "save_preset": (
                lambda: self._run_geomap_operator("save_preset", "INVOKE_DEFAULT")
            ),
            "route_add": _op_and_rebuild(lambda: bpy.ops.geomap.add_route("EXEC_DEFAULT")),
            "route_remove": _op_and_rebuild(lambda: bpy.ops.geomap.remove_route("EXEC_DEFAULT")),
            "route_pick_start": (
                lambda: self._run_geomap_operator(
                    "pick_route_point", "INVOKE_DEFAULT", target="START",
                )
            ),
            "route_pick_end": (
                lambda: self._run_geomap_operator(
                    "pick_route_point", "INVOKE_DEFAULT", target="END",
                )
            ),
            "route_import": (
                lambda: self._run_geomap_operator("import_route", "EXEC_DEFAULT")
            ),
            "route_import_all": (
                lambda: self._run_geomap_operator("import_all_routes", "EXEC_DEFAULT")
            ),
            "route_search_start": (
                lambda: self._run_geomap_operator(
                    "search_route_point", "EXEC_DEFAULT", target="START",
                )
            ),
            "route_search_end": (
                lambda: self._run_geomap_operator(
                    "search_route_point", "EXEC_DEFAULT", target="END",
                )
            ),
            **{
                f"gen_{k}": _layer_cb(k) for k in (
                    "TERRAIN", "COASTLINES", "RIVERS", "ROADS",
                    "LANDUSE", "BUILDINGS", "CITIES", "WEATHER",
                    "ANNOTATIONS",
                )
            },
        }

        # Load search history (bpy-side: safe here, modal.py owns bpy access).
        history_entries: list[dict] = []
        try:
            from ..search_cache import load_history as _load_history
            for i, h in enumerate((_load_history() or [])[:12]):
                label = h.get("label") or h.get("name", "Untitled")
                history_entries.append({"label": label, "index": i})
                idx = i
                callbacks[f"load_history_{idx}"] = (
                    lambda j=idx: bpy.ops.geomap.load_history(
                        "INVOKE_DEFAULT", index=j,
                    )
                )
        except Exception:
            pass

        try:
            for i, _route in enumerate(list(getattr(props, "routes", []) or [])[:5]):
                callbacks[f"route_select_{i}"] = _op_and_rebuild(
                    lambda idx=i: setattr(props, "route_active_index", idx)
                )
        except Exception:
            pass

        kmz_entries: list[tuple[str, str, str]] = []
        try:
            from ..kmz import kmz_enum_items as _kmz_enum_items
            kmz_entries = list(_kmz_enum_items(props, context))
        except Exception:
            pass

        preset_entries: list[dict] = []
        try:
            from ..search_cache import load_presets as _load_presets
            preset_entries = list((_load_presets() or [])[:8])
            for i, preset in enumerate(preset_entries):
                name = str(preset.get("preset_name") or "")
                callbacks[f"load_preset_{i}"] = _op_and_rebuild(
                    lambda preset_name=name: bpy.ops.geomap.load_preset(
                        "EXEC_DEFAULT",
                        preset_name=preset_name,
                    )
                )
                callbacks[f"delete_preset_{i}"] = _op_and_rebuild(
                    lambda preset_name=name: bpy.ops.geomap.delete_preset(
                        "EXEC_DEFAULT",
                        preset_name=preset_name,
                    )
                )
        except Exception:
            pass

        layer_entries: list[dict] = []
        try:
            root = bpy.data.collections.get("GeoMap")
            if root is not None:
                layer_entries = [
                    {"name": child.name, "layer": child}
                    for child in root.children
                ]
        except Exception:
            pass

        self._tree = build_widget_tree(
            props, tracker, region.width, region.height, callbacks,
            history_entries=history_entries,
            kmz_entries=kmz_entries,
            preset_entries=preset_entries,
            layer_entries=layer_entries,
        )
        self._tab_bar = self._tree["tab_bar"]
        if active_tab_index is not None:
            self._tab_bar.active_index = max(
                0, min(active_tab_index, len(self._tree["tabs"]) - 1),
            )
        self._tabs = self._tree["tabs"]
        self._gen_btn = self._tree["gen_btn"]
        self._progress_bar = self._tree["progress_bar"]
        self._weather_progress_bar = self._tree.get("weather_progress_bar")
        self._close_btn = self._tree["close_btn"]
        self._overlay_rect = self._tree["overlay_rect"]
        self._log_y = self._tree.get("log_y")
        self._sep_btm_y = self._tree.get("sep_btm_y")

    def _create_place_label_cb(self) -> None:
        """Create text from selected POI markers when Blender context allows it."""
        if not bpy.ops.geomap.create_place_label.poll():
            self.report(
                {"WARNING"},
                "Select one or more generated POI markers first",
            )
            return
        bpy.ops.geomap.create_place_label("EXEC_DEFAULT")

    def _import_kmz_cb(self) -> None:
        """Import the selected KMZ catalog entry when one is available."""
        try:
            selection = str(bpy.context.scene.geomap_props.kmz_selection)
        except Exception:
            selection = "NONE"
        if not selection or selection == "NONE":
            self.report(
                {"WARNING"},
                "No KMZ selected. Add a matching catalog entry or select one first.",
            )
            return
        self._run_geomap_operator("import_selected_kmz", "EXEC_DEFAULT")

    def _run_geomap_operator(self, name: str, execution_context: str, **kwargs) -> None:
        op = getattr(bpy.ops.geomap, name)
        if not op.poll():
            self.report({"WARNING"}, "This action is not available in the current context")
            return
        try:
            op(execution_context, **kwargs)
        except RuntimeError as exc:
            message = str(exc).strip() or "GeoMap action failed"
            if message.startswith("Error: "):
                message = message[7:]
            self.report({"WARNING"}, message)

    def _make_close_cb(self, context) -> Callable[[], None]:
        """Return a closure that removes the draw handler and exits modal."""
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

    def modal(self, context, event) -> set[str]:
        """Process mouse and keyboard events, dispatch to widget tree."""
        if not self._open:
            return {"FINISHED"}

        # Refresh live progress + toggle Generate ↔ Abort each tick
        from ..progress import ProgressTracker
        tracker = ProgressTracker.get_instance()
        if self._progress_bar is not None:
            self._progress_bar.progress = tracker.progress
            self._progress_bar.status = tracker.status or ""
        if self._weather_progress_bar is not None:
            self._weather_progress_bar.progress = tracker.weather_progress
            self._weather_progress_bar.status = tracker.weather_status or ""
        if self._gen_btn is not None:
            if tracker.is_running:
                self._gen_btn.label = "● ABORT"
                self._gen_btn.callback = (
                    lambda: bpy.ops.geomap.cancel_generation("INVOKE_DEFAULT")
                )
            else:
                self._gen_btn.label = "Generate All"
                self._gen_btn.callback = self._generate_cb

        mx = event.mouse_region_x
        my = event.mouse_region_y

        if event.type == "MOUSEMOVE":
            self._dispatch_move(mx, my)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
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

        if event.value == "PRESS" and self._dispatch_key(event):
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
        """Return widget list for the currently active tab."""
        if not self._tabs or self._tab_bar is None:
            return []
        idx = max(0, min(self._tab_bar.active_index, len(self._tabs) - 1))
        return self._tabs[idx]

    def _all_widgets(self) -> list:
        """Return all widgets that receive events and draw calls."""
        base = [w for w in (self._tab_bar, self._close_btn) if w is not None]
        return base + self._active_tab_widgets()

    def _dispatch_press(self, mx: int, my: int) -> None:
        """Dispatch mouse press to first widget that hits."""
        weather_before = None
        input_mode_before = None
        kmz_before = None
        output_preset_before = None
        create_map_box_before = None
        try:
            weather_before = bool(bpy.context.scene.geomap_props.import_weather)
            input_mode_before = str(bpy.context.scene.geomap_props.input_mode)
            kmz_before = str(bpy.context.scene.geomap_props.kmz_selection)
            output_preset_before = str(bpy.context.scene.geomap_props.output_preset)
            create_map_box_before = bool(bpy.context.scene.geomap_props.create_map_box)
        except Exception:
            pass
        handled = False
        for w in self._all_widgets():
            if w.on_mouse_press(mx, my):
                handled = True
                try:
                    weather_after = bool(bpy.context.scene.geomap_props.import_weather)
                    input_mode_after = str(bpy.context.scene.geomap_props.input_mode)
                    kmz_after = str(bpy.context.scene.geomap_props.kmz_selection)
                    output_preset_after = str(bpy.context.scene.geomap_props.output_preset)
                    create_map_box_after = bool(bpy.context.scene.geomap_props.create_map_box)
                except Exception:
                    weather_after = weather_before
                    input_mode_after = input_mode_before
                    kmz_after = kmz_before
                    output_preset_after = output_preset_before
                    create_map_box_after = create_map_box_before
                if (
                    (weather_before is not None and weather_after != weather_before)
                    or (
                        input_mode_before is not None
                        and input_mode_after != input_mode_before
                    )
                    or (kmz_before is not None and kmz_after != kmz_before)
                    or (
                        output_preset_before is not None
                        and output_preset_after != output_preset_before
                    )
                    or (
                        create_map_box_before is not None
                        and create_map_box_after != create_map_box_before
                    )
                ):
                    idx = self._tab_bar.active_index if self._tab_bar is not None else 0
                    self._build_tree(bpy.context, idx)
                return
        if not handled:
            for w in self._all_widgets():
                w.blur()

    def _dispatch_release(self, mx: int, my: int) -> None:
        """Dispatch mouse release to all widgets (for button release detection)."""
        for w in self._all_widgets():
            w.on_mouse_release(mx, my)

    def _dispatch_move(self, mx: int, my: int) -> None:
        """Dispatch mouse move to all widgets for hover state."""
        for w in self._all_widgets():
            w.on_mouse_move(mx, my)

    def _dispatch_key(self, event) -> bool:
        """Dispatch keyboard events to focused widgets."""
        for w in self._all_widgets():
            if w.on_key(event):
                return True
        return False

    # ------------------------------------------------------------------
    # Draw callback (POST_PIXEL — called from GPU context)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_callback(op: "GeoMapDashboardOperator", context) -> None:
        """Render the dashboard overlay. Called by Blender's draw system."""
        try:
            if op._overlay_rect is None:
                return
            r = op._overlay_rect

            # Overlay background — Blender 4.x default dark theme exact values.
            # Content area: region background rgb(40,40,40).
            draw_rect(r.x, r.y, r.w, r.h, (0.157, 0.157, 0.157, 0.97))
            # Tab header strip: slightly darker, matching panel header rgb(35,35,35).
            draw_rect(r.x, r.y + r.h - 36.0, r.w, 36.0, (0.137, 0.137, 0.137, 1.0))
            # Outer border: near-black edges rgb(5,5,5).
            draw_rect(r.x, r.y, r.w, 1.0, (0.020, 0.020, 0.020, 1.0))
            draw_rect(r.x, r.y + r.h - 1.0, r.w, 1.0, (0.020, 0.020, 0.020, 1.0))
            draw_rect(r.x, r.y, 1.0, r.h, (0.020, 0.020, 0.020, 1.0))
            draw_rect(r.x + r.w - 1.0, r.y, 1.0, r.h, (0.020, 0.020, 0.020, 1.0))

            # Separators — subdued, matching Blender panel divider rgb(51,51,51).
            if op._sep_btm_y is not None:
                draw_rect(r.x, op._sep_btm_y, r.w, 1.0, (0.200, 0.200, 0.200, 1.0))
            draw_rect(r.x, r.y + r.h - 36.0, r.w, 1.0, (0.200, 0.200, 0.200, 1.0))

            # All widgets
            for w in op._all_widgets():
                w.draw(context)

            # Generation logs are part of the Generate tab.
            if (
                op._tab_bar is not None
                and op._tabs
                and op._tab_bar.active_index == len(op._tabs) - 1
            ):
                from ..progress import ProgressTracker
                from .renderer import draw_text
                tracker = ProgressTracker.get_instance()
                log_y = op._log_y if op._log_y is not None else r.y + 6.0
                if tracker.error:
                    draw_text(
                        f"ERROR: {tracker.error[:88]}",
                        r.x + 14, log_y, 10, (1.0, 0.35, 0.35, 1.0),
                    )
                    log_y += 14.0
                for msg in (tracker.logs[-4:] if tracker.logs else []):
                    draw_text(msg[:90], r.x + 14, log_y, 10, (0.65, 0.85, 0.65, 1.0))
                    log_y += 14.0

        except Exception:
            traceback.print_exc()
