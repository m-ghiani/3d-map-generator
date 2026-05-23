# GeoMap Dashboard Overlay — Design Spec

**Date:** 2026-05-23  
**Status:** Approved  
**Scope:** Full-screen modal UI overlay for GeoMap Generator addon (Blender 4.x)

---

## Problem

N-panel sidebar too narrow (~220px), too many sub-panels (12+), no clear workflow order.
Primary workflow (select area → toggle layers → generate per-layer) buried in scroll.

---

## Approach

GPU-drawn full-screen modal overlay inside the existing 3D viewport.
Extends pattern already established by `map_selector` (modal operator + GPU draw callbacks).
Writes directly to existing `geomap_props`. Calls existing operators. N-panel retained for advanced settings.

---

## Architecture

```
[Open Dashboard button / Alt+G]
      ↓
GeoMapDashboardOperator.invoke()
  → registers draw_handler (GPU)
  → enters modal loop
      ↓
modal() receives mouse/key events
  → dispatches to widget tree
      ↓
widget.draw()   → gpu.shader quads + blf text
widget.on_click() → setattr(geomap_props, ...) / bpy.ops.geomap.*
      ↓
ESC or [×] → removes handler, returns to viewport
```

**Scope of overlay (primary workflow — 80% use case):**
- Area selection (text search + pick on map)
- Layer toggles + per-layer Generate buttons
- Progress bar + Generate All / Abort
- Width sliders for active layers
- Curve/Mesh radio for roads and rivers

**Remains in N-panel (advanced / infrequent):**
- Provider settings, DEM resolution, quality presets, KMZ, annotations, routes, search history

---

## Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  GeoMap  │ Location │ Layers │ Output │ History │          [×]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  TAB: LAYERS                                                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ [✓] Terrain   DEM [●] Sat [●]   ────────  [Generate ↺]  │  │
│  │ [✓] Coastlines                  ────────  [Generate ↺]  │  │
│  │ [✓] Rivers    width ━━━●━━ 0.06 ────────  [Generate ↺]  │  │
│  │ [ ] Roads     width ━━━●━━ 0.04  Curve|●Mesh  [Gen ↺]  │  │
│  │ [ ] Buildings                   ────────  [Generate ↺]  │  │
│  │ [ ] Cities/POI                  ────────  [Generate ↺]  │  │
│  │ [ ] Weather                     ────────  [Generate ↺]  │  │
│  │ [ ] Annotations                 ────────  [Generate ↺]  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  TAB: LOCATION                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  [● Place  ○ Coords]                                     │  │
│  │  [🔍 ________________________________] [Search]          │  │
│  │  [🗺  Pick Area on Map]                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  [●●● GENERATE ALL]    ████████████░░░░ 65%  Fetching DEM...   │
└─────────────────────────────────────────────────────────────────┘
```

**Layout rules:**
- Overlay: centered rect, ~80% viewport, semi-transparent dark background
- Tab bar: fixed top
- Bottom bar: fixed (Generate All + progress)
- Center area: scrollable (layer list can grow)
- Layer row expands inline settings only when toggle is ON
- ESC or click outside → closes overlay

---

## Widget System

New package: `geomap_generator/dashboard/`

### Class hierarchy

```
UIWidget (base)
  .rect: (x, y, w, h)
  .draw(ctx)
  .hit_test(mx, my) → bool
  .on_mouse_press(mx, my)
  .on_mouse_release(mx, my)
  .on_mouse_move(mx, my)

├── Button         label + callback fn
├── Toggle         bool — reads/writes prop_name on geomap_props
├── SliderFloat    float — drag horizontally, prop_name + min/max
├── RadioGroup     enum — list of labels + prop_name
├── TabBar         list of tab labels, tracks active index
├── ProgressBar    float 0..1 + status text label
├── TextLabel      static display text
├── TextInput      StringProperty — click opens bpy text input operator
└── LayerRow       composite: Toggle + optional SliderFloat + optional RadioGroup + Button
                   inline settings visible only when toggle ON
```

### Modules

| Module | Responsibility |
|--------|---------------|
| `dashboard/__init__.py` | exports operator + classes for registration |
| `dashboard/widgets.py` | all UIWidget subclasses |
| `dashboard/renderer.py` | `draw_rect()`, `draw_text()` via `gpu.shader` + `blf` |
| `dashboard/modal.py` | `GeoMapDashboardOperator` — invoke, modal, draw handler |
| `dashboard/layout.py` | builds widget tree per tab from current `geomap_props` state |

### GPU rendering primitives

```python
# renderer.py
def draw_rect(x, y, w, h, color: tuple[float,float,float,float]) -> None:
    # gpu.shader 'UNIFORM_COLOR' + batch TRIS

def draw_text(text: str, x: int, y: int, size: int,
              color: tuple[float,float,float,float]) -> None:
    # blf.position / blf.size / blf.draw
```

### Prop binding pattern

```python
# Toggle example
props = context.scene.geomap_props
current = getattr(props, self.prop_name)
setattr(props, self.prop_name, not current)
```

### Event routing

```python
def modal(self, context, event):
    if event.type == 'MOUSEMOVE':
        self._update_hover(event.mouse_region_x, event.mouse_region_y)
    elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
        self._dispatch_press(event.mouse_region_x, event.mouse_region_y)
    elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
        self._dispatch_release(event.mouse_region_x, event.mouse_region_y)
    elif event.type == 'ESC':
        self._close(context)
        return {'FINISHED'}
    context.area.tag_redraw()
    return {'RUNNING_MODAL'}
```

---

## Integration

- `GeoMapPanel` (N-panel root): add `[Open Dashboard]` button calling `geomap.open_dashboard`
- Optional: `Alt+G` keymap entry via `bpy.types.KeyMap` in `__init__.py`
- `GeoMapDashboardOperator` registered in `_load_classes()` alongside existing operators
- N-panel unchanged — coexists as advanced settings fallback
- No state duplication — dashboard reads/writes same `geomap_props` as N-panel

---

## Error Handling

- `draw()` wrapped in `try/except` → GPU error logs to console, shows red error label in overlay, does not crash Blender
- Missing `geomap_props` → overlay shows error message, closes immediately
- Operator `poll()` on Generate buttons: reuses existing operator poll conditions
- `ProgressTracker` polled same as N-panel modal timer (tag_redraw triggers redraw)

---

## Testing

- Widget logic (`hit_test`, prop binding, layout math) tested without real `bpy`:
  ```python
  # tests/test_dashboard_widgets.py
  props = SimpleNamespace(import_rivers=False)
  toggle = Toggle(prop_name="import_rivers", props=props)
  toggle.on_mouse_press(0, 0)
  assert props.import_rivers is True
  ```
- GPU draw code: `@unittest.skip("requires GPU context")` — not testable in headless unittest
- Integration tested manually in Blender Script Editor

---

## File Structure

```
geomap_generator/
  dashboard/
    __init__.py       ← exports GeoMapDashboardOperator
    widgets.py        ← UIWidget + all subclasses
    renderer.py       ← draw_rect, draw_text primitives
    modal.py          ← GeoMapDashboardOperator
    layout.py         ← build_widget_tree(context) → root widget
tests/
  test_dashboard_widgets.py
```

---

## Out of Scope

- Replacing N-panel entirely (it stays for advanced settings)
- Animated transitions between tabs
- Drag-and-drop layer reordering
- Custom text input (uses existing bpy string input via operator invoke)
- Dropdown/enum widgets for rarely-changed settings (DEM resolution, providers, etc.)
