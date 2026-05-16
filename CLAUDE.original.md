# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Blender 4.x addon — generates 3D terrain maps from real geographic data (OpenStreetMap + elevation services + satellite imagery). Pure Python, no external dependencies (Blender-bundled stdlib only).

## Installation / Running

No build system. Manual install:

1. Copy the `geomap_generator/` directory to Blender's addon directory (e.g. `~/Library/Application Support/Blender/4.x/scripts/addons/geomap_generator/`)
2. Enable in Blender > Preferences > Add-ons > "GeoMap Generator"
3. Access: View3D > Sidebar (N) > GeoMap tab

For iteration: Blender's Script Editor can reload the addon without restart via `bpy.ops.preferences.addon_disable()` + `addon_enable()`.

The root-level `__init__.py` is the old monolithic version — the active addon is `geomap_generator/`.

## Testing

Run from project root (must be run from root — tests open source files by relative path):

```sh
python -m unittest discover tests
```

Run a single test class:

```sh
python -m unittest tests.test_core_geometry.OsmApiClientTests
```

Tests use stdlib `unittest` + `unittest.mock` only. Tests that need `bpy` mock it via `patch.dict("sys.modules", {"bpy": SimpleNamespace()})`.

## Architecture

The addon lives in `geomap_generator/` as a package. `geomap_generator/__init__.py` handles `register()`/`unregister()` and hot-reloads all submodules on each register call (supports Script Editor reload without Blender restart).

### Module map

| Module | Responsibility |
| ------ | -------------- |
| `models.py` | Core data classes: `OsmNode`, `OsmWay`, `OsmPoint`, `BoundingBox`, `GeoMapData`, `SatelliteTile`, `DemGrid` |
| `exceptions.py` | Custom exceptions: `ValidationError`, `GeoMapError`, `MeshBuildError`, `ProviderError`, `CancelledGeneration` |
| `settings.py` | Frozen dataclasses `GenerationSettings` + `ProviderSettings`. Preset logic (`from_props`). **No bpy.** |
| `validation.py` | `validate_settings()` — rejects out-of-range bbox, missing fields. **No bpy.** |
| `coordinates.py` | `CoordinateTransformer` — WGS84 lat/lon → Web Mercator → Blender cartesian. Static methods. **No bpy.** |
| `scene_units.py` | `SceneScale` — converts DEM height and map values to Blender scene units. **No bpy.** |
| `layer_style.py` | `vector_layer_identity()`, `width_for_way()`, `base_width_for_layer()` — resolves OSM tags → layer key + width. **No bpy.** |
| `providers.py` | Provider enum/constant resolution. **No bpy.** |
| `mesh_builder.py` | Pure geometry: `BboxPlaneBuilder`, `DemMeshBuilder`, `LineMeshBuilder`, `RibbonMeshBuilder`, `BboxProjector`, `DemHeightSampler`, `river_width_factor`, `road_width_factor`. **No bpy.** |
| `overpass.py` | `OsmApiClient` — Overpass API queries, tiling, rate-limit fallback, city/bbox resolution. `OsmJsonParser` — parses Overpass JSON to `GeoMapData`. |
| `dem.py` | `DemClient` — fetches elevation grids from Open-Meteo / OpenTopoData. Batches, retries 429/502, falls back between providers. |
| `imagery.py` | `SatelliteImageryClient` — fetches satellite/basemap tiles from ArcGIS. Handles tiling for high-res requests, caching. |
| `download_cache.py` | Disk cache with index files. `cached_bytes()`, `read_index()`, `cache_stats()`, `clear_cache()`. |
| `search_cache.py` | Search history: `add_search()`, `load_history()`, `apply_snapshot()`. Stored in Blender user data dir. |
| `persistent_log.py` | Session log file in Blender user data dir. |
| `progress.py` | `ProgressTracker` singleton — thread-safe bridge between background thread and modal timer. |
| `threading_utils.py` | `assert_main_thread()` — guards bpy calls. |
| `blender_scene.py` | Low-level Blender object/mesh/material creation helpers. |
| `terrain_renderer.py` | `TerrainRenderer` — creates DEM terrain mesh + satellite texture in Blender. |
| `vector_renderer.py` | `VectorRenderer` — creates OSM line/ribbon/point objects in Blender. Stores OSM metadata as custom properties. |
| `annotation_renderer.py` | `AnnotationRenderer` — places text annotations and scale bars. |
| `osm_3d.py` | `Osm3DModelClient` + `Osm3DModelRenderer` — parses building footprints (ways + relations) and extrudes 3D volumes. |
| `operators.py` | Blender operators: `GeoMapGenerateOperator` (modal), `GeoMapCancelOperator`, `GeoMapLoadHistoryOperator`, `GeoMapClearHistoryOperator`, `GeoMapClearDownloadCacheOperator`, `GeoMapImportSelectedPoi3DOperator`. |
| `panels.py` | N-sidebar panels: `GeoMapPanel`, `GeoMapProgressPanel`, `GeoMapSearchHistoryPanel`. |
| `properties.py` | `GeoMapProperties` (scene props, accessed via `context.scene.geomap_props`), `GeoMapAddonPreferences` (provider settings in addon prefs, not the panel). |

### Threading model

Blender restricts `bpy` calls to the main thread:

1. `GeoMapGenerateOperator.execute()` starts background thread → fetches OSM data, DEM, satellite imagery
2. Background thread updates `ProgressTracker` state only — never calls `bpy.*`
3. Modal timer fires every 0.5s on main thread → reads `ProgressTracker` flags → calls renderers to create Blender objects
4. ESC/abort sets `cancel_requested` on `ProgressTracker`

**Enforced invariant:** modules `settings.py`, `validation.py`, `scene_units.py`, `layer_style.py`, `providers.py` must never `import bpy`. The test `test_thread_boundary_keeps_bpy_out_of_pure_modules` asserts this.

### Coordinate pipeline

```text
User input (lat/lon bbox)
  → OsmApiClient: resolve name → bbox if COUNTRY mode; tile large bboxes
  → OsmJsonParser: Overpass JSON → GeoMapData (OsmWay list + OsmPoint list)
  → DemClient: fetch elevation grid (DemGrid)
  → SatelliteImageryClient: fetch image tiles (SatelliteTile list)
  → BboxProjector.project(lat, lon) → (x, y, z) in Blender units
       └── CoordinateTransformer.mercator_projection() → meters, centered, scaled
  → mesh_builder.*: pure geometry → (verts, edges, faces)
  → TerrainRenderer / VectorRenderer / AnnotationRenderer: create bpy objects
```

### Data flow: settings → generation

`GeoMapProperties` (bpy scene props) → `GenerationSettings.from_props()` applies preset overrides → `validate_settings()` → passed to renderers. `ProviderSettings.from_preferences()` reads `GeoMapAddonPreferences`.

## Implementation Status

- **Phase 1** (UI skeleton): Complete
- **Phase 2** (OSM vector data): Complete — coast, rivers, roads, admin boundaries, cities, POIs
- **Phase 3** (DEM elevation + terrain mesh): Complete
- **Phase 4** (satellite imagery, 3D buildings, annotations): Complete

## Blender Conventions

- `context.scene.geomap_props` is the `GeoMapProperties` instance
- Addon preferences accessed via `bpy.context.preferences.addons[__package__].preferences`
- All classes registered in `geomap_generator/__init__.py:_load_classes()` in dependency order
- Modal operators use `_timer` + `TIMER` event; cancel via `wm.event_timer_remove()`
- OSM metadata stored as Blender custom properties with `geomap_*` and `osm:*` prefixes

## Known Pitfalls

- Overpass API rate-limited (429) → auto-retries on fallback endpoint (`overpass.private.coffee`)
- DEM elevation batched in groups of 50 points; large bboxes with `DEM_ULTRA` (64×64) make ~82 requests
- Large bboxes auto-tiled: Overpass queries split into 3°×3° tiles, imagery into sub-2048px tiles
- `osm_3d.py` imports `bpy` at module level — must be patched in tests via `sys.modules`
- `xml.etree` imported in root `__init__.py` (old monolith) but unused there
