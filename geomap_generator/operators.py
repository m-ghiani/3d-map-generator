import threading
import time
import importlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from .annotation_renderer import AnnotationRenderer, PlaceLabelRenderer
from .blender_scene import (
    add_shrinkwrap_nearest_surface,
    add_shrinkwrap_to_dem,
    clear_geomap_child_collection,
    link_to_geomap_collection,
)
from .dem import DemClient
from .download_cache import (
    cache_stats,
    clear_cache,
    configure_blender_cache_root,
    set_offline_mode,
)
from .exceptions import (
    CancelledGeneration,
    GeoMapError,
    MeshBuildError,
    ProviderError,
    ValidationError,
)
from .geometry_payload import build_building_batch_payload, build_vector_payload
from .imagery import SatelliteImageryClient
from .kmz import (
    asset_entries_for_bbox,
    asset_entry_location,
    catalog_paths,
    current_bbox_from_context,
    download_catalog_asset,
    download_kmz,
    entry_by_id,
    parse_kmz,
)
from .layer_style import collection_name_for_layer, vector_layer_identity
from .mesh_builder import BboxProjector, DemHeightSampler
from .model_library import (
    find_sketchfab_model,
    google_photorealistic_3d_tiles_url,
    sketchfab_search_url,
)
from .models import BoundingBox, DemGrid, GeoMapData, SatelliteTile
from .overpass import OsmApiClient
from .osm_3d import (
    Osm3DModelClient,
    Osm3DModelRenderer,
    aggregate_building_parts,
    building_batch_color,
    building_batch_material_key,
)
from .persistent_log import configure_blender_log_path
from .progress import ProgressTracker
from .search_cache import (
    add_search,
    apply_snapshot,
    clear_history,
    configure_blender_history_path,
    delete_preset,
    load_history,
    load_presets,
    rename_history_entry,
    save_preset,
    snapshot_from_props,
)
from .scene_units import SceneScale, scaled_map_value
from .service_client import GeoMapServiceClient
from .settings import (
    GenerationSettings,
    ProviderSettings,
    apply_output_preset_to_props,
    apply_quality_preset_to_props,
)
from .terrain_renderer import TerrainRenderer
from .threading_utils import assert_main_thread
from .validation import validate_settings
from .vector_renderer import VectorRenderer


def _addon_package_name() -> str:
    return (__package__ or "geomap_generator").split(".", 1)[0]


def _addon_root_path() -> Path:
    return Path(__file__).resolve().parents[1]


def _hot_reload_addon(addon_package: str):
    """Timer callback: unregister, reload modules from disk, and register again."""
    module = sys.modules.get(addon_package)
    if module is None:
        return None

    try:
        if hasattr(module, "unregister"):
            module.unregister()
        module = importlib.reload(module)
        if hasattr(module, "register"):
            module.register()
        print(f"GeoMap Generator: hot reload completed for {addon_package}")
    except Exception:
        import traceback
        traceback.print_exc()
    return None


class GeoMapGenerateOperator(Operator):
    bl_idname = "geomap.generate"
    bl_label = "Generate GeoMap"
    bl_description = "Generate 3D map from geographic data"

    _timer = None
    _POINT_BATCH_SIZE = 200
    _BUILDING_BATCH_SIZE = 5000
    _BUILDING_VERTEX_CHUNK_LIMIT = 120_000
    _MESH_PROGRESS_START = 0.82
    _MESH_PROGRESS_END = 0.995
    _VECTOR_LAYER_CHUNK_SIZE = 5_000   # ways per curve object
    _MAX_TOTAL_VECTOR_WAYS = 50_000    # hard cap before truncation

    def execute(self, context):
        tracker = ProgressTracker.get_instance()
        tracker.reset()
        tracker.is_running = True
        configure_blender_log_path()
        tracker.log("Generation started")
        tracker.set_status("Validating input...", 0.0)

        props = context.scene.geomap_props
        apply_quality_preset_to_props(props)
        apply_output_preset_to_props(props)
        settings = GenerationSettings.from_props(props)
        try:
            validate_settings(settings)
        except ValidationError as error:
            tracker.error = str(error)
            tracker.is_running = False
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self._search_snapshot = snapshot_from_props(props)
        self._props = settings
        self._prefs = ProviderSettings.from_preferences(self._get_addon_preferences(context))
        configure_blender_cache_root()
        configure_blender_history_path()
        self._client = OsmApiClient()
        self._satellite_tiles: list[SatelliteTile] = []
        self._dem_grid: DemGrid | None = None
        self._dem_tiles: list[tuple[SatelliteTile, DemGrid]] = []
        self._model_candidates: list = []
        self._mesh_steps = None

        thread = threading.Thread(target=self._generate_threaded, daemon=True)
        thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        tracker = ProgressTracker.get_instance()

        if event.type == "TIMER":
            advanced_mesh_this_tick = False
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    for region in area.regions:
                        if region.type == "UI":
                            region.tag_redraw()

            mesh_data = tracker.pop_mesh_data()
            if mesh_data is not None:
                osm_data, props = mesh_data
                ready_at = tracker.mesh_data_ready_at
                wait_seconds = time.time() - ready_at if ready_at else 0.0
                tracker.log(
                    "Blender main thread picked up mesh data "
                    f"after {wait_seconds:.1f}s "
                    f"({len(osm_data.ways)} ways, {len(osm_data.points)} points)"
                )
                self._start_mesh_creation(context, osm_data, props, tracker)
                advanced_mesh_this_tick = True
            elif tracker.should_log_pending_mesh_data():
                age = tracker.mesh_data_pending_age()
                if age is not None:
                    tracker.log(f"Mesh data still waiting for Blender main thread ({age:.1f}s)")

            if (
                self._mesh_steps is not None
                and tracker.is_running
                and not advanced_mesh_this_tick
            ):
                self._advance_mesh_creation(tracker)

            if not tracker.is_running:
                self._remove_timer(context)
                if tracker.is_cancelled():
                    self.report({"WARNING"}, "Generation cancelled")
                    return {"CANCELLED"}
                if tracker.error:
                    self.report({"ERROR"}, tracker.error)
                    return {"FINISHED"}
                if tracker.result:
                    import bpy as _bpy
                    _bpy.ops.ed.undo_push(message="GeoMap Generate")
                    self.report({"INFO"}, "GeoMap generation completed ✓")
                    return {"FINISHED"}
                self.report({"ERROR"}, "Generation failed")
                return {"CANCELLED"}

        elif event.type in ("ESC", "RIGHTMOUSE"):
            tracker.request_cancel()
            tracker.set_status("Cancelling...", tracker.progress)
            tracker.log("Cancellation requested by user")
            self.report({"WARNING"}, "Cancellation requested")
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def _remove_timer(self, context) -> None:
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _generate_threaded(self) -> None:
        tracker = ProgressTracker.get_instance()
        try:
            tracker.set_status("Preparing request...", 0.08)
            osm_data = self._fetch_data(tracker)

            if tracker.is_cancelled():
                raise CancelledGeneration("Cancelled")

            tracker.log(f"✓ {len(osm_data.ways)} ways received")
            add_search(self._search_snapshot)
            tracker.set_status("Preparing mesh creation payload...", 0.82)
            tracker.set_mesh_data((osm_data, self._props))
            tracker.log("✓ Mesh data ready")
            tracker.set_status("Waiting for Blender main thread...", 0.83)

        except CancelledGeneration as e:
            tracker.log(f"⊘ {e}")
            tracker.is_running = False
        except GeoMapError as e:
            tracker.error = str(e)
            tracker.log(f"✗ {e}")
            tracker.is_running = False
        except Exception as e:
            tracker.error = str(e)
            tracker.log(f"✗ ERROR: {e}")
            tracker.is_running = False

    def _fetch_data(self, tracker: ProgressTracker) -> GeoMapData:
        props = self._props
        if self._prefs.data_backend == "EXTERNAL":
            return self._fetch_external_data(tracker)

        self._raise_if_cancelled(tracker)
        if props.input_mode == "COUNTRY":
            tracker.set_status("Resolving place bounding box...", 0.12)
            tracker.log(f"Nominatim bbox lookup: '{props.country_region}'...")
            bbox = self._client.resolve_bbox(props.country_region)
            self._raise_if_cancelled(tracker)
            tracker.set_status("Bounding box resolved", 0.24)
            tracker.log(
                f"Bbox: {bbox.min_lat:.3f},{bbox.min_lon:.3f} → "
                f"{bbox.max_lat:.3f},{bbox.max_lon:.3f}"
            )
        else:
            tracker.set_status("Reading coordinate bounds...", 0.18)
            bbox = BoundingBox.from_corners(
                props.latitude, props.longitude, props.latitude2, props.longitude2
            )
            tracker.set_status("Bounding box ready", 0.24)
            tracker.log(
                f"Bbox: {bbox.min_lat:.3f},{bbox.min_lon:.3f} → "
                f"{bbox.max_lat:.3f},{bbox.max_lon:.3f}"
            )

        tracker.set_status("Preparing selected feature filters...", 0.28)
        self._raise_if_cancelled(tracker)
        selected = []
        if props.import_coast:
            selected.append("coastlines")
        if props.import_rivers:
            selected.append("rivers")
        if props.import_roads:
            selected.append("roads")
        if props.import_admin:
            selected.append(f"admin boundaries level {props.admin_level}")
        if props.import_buildings:
            selected.append("3D buildings")
        if props.import_cities:
            selected.append("cities")
        if props.import_place_labels and not props.import_cities:
            selected.append("place labels")
        if props.import_poi_historic:
            selected.append("historic POI")
        if props.import_poi_cultural:
            selected.append("cultural POI")
        if props.import_poi_administrative:
            selected.append("administrative POI")
        if props.import_poi_natural:
            selected.append("natural POI")
        if props.import_relief:
            selected.append("relief DEM")

        vector_selected = [
            name for name in selected if name != "relief DEM"
        ]
        if vector_selected:
            tracker.log(f"Fetching OSM features: {', '.join(vector_selected)}")
            data = self._fetch_vector_data(bbox, props, tracker)
            self._raise_if_cancelled(tracker)
        else:
            data = GeoMapData(ways=[], bbox=bbox)

        if props.import_satellite:
            tracker.set_status("Downloading satellite tiles...", 0.72)
            tracker.log(f"Fetching {props.map_style.lower()} map tiles for bbox")
            try:
                self._satellite_tiles = SatelliteImageryClient().fetch_bbox_tiles(
                    bbox,
                    props.satellite_resolution,
                    props.map_style,
                    provider=self._provider("basemap_provider"),
                    tokens=self._basemap_tokens(),
                    should_cancel=tracker.is_cancelled,
                )
                self._raise_if_cancelled(tracker)
                tracker.log(f"✓ Satellite tiles: {len(self._satellite_tiles)}")
            except CancelledGeneration:
                raise
            except Exception as exc:
                tracker.log(f"⚠ Satellite imagery failed ({exc}), continuing without tiles")
                self._satellite_tiles = []

        if props.import_relief and self._satellite_tiles:
            tracker.set_status("Fetching tiled DEM elevation data...", 0.75)
            dem_client = DemClient()
            for index, tile in enumerate(self._satellite_tiles, start=1):
                self._raise_if_cancelled(tracker)
                tracker.log(f"Fetching DEM tile {index}/{len(self._satellite_tiles)}")
                grid = dem_client.fetch_grid(
                    tile.bbox,
                    props.dem_resolution,
                    progress=tracker.set_status,
                    progress_start=0.75 + ((index - 1) / len(self._satellite_tiles)) * 0.07,
                    progress_end=0.75 + (index / len(self._satellite_tiles)) * 0.07,
                    provider=self._provider("dem_provider"),
                    should_cancel=tracker.is_cancelled,
                )
                self._dem_tiles.append((tile, grid))
            tracker.log(f"✓ DEM tiles: {len(self._dem_tiles)}")
        elif props.import_relief:
            tracker.set_status("Fetching DEM elevation data...", 0.75)
            tracker.log("Fetching DEM elevation grid")
            self._dem_grid = DemClient().fetch_grid(
                bbox,
                props.dem_resolution,
                progress=tracker.set_status,
                provider=self._provider("dem_provider"),
                should_cancel=tracker.is_cancelled,
            )
            tracker.log(
                "✓ DEM grid: "
                f"{self._dem_grid.rows}×{self._dem_grid.cols}, "
                f"{self._dem_grid.min_elevation():.0f}–{self._dem_grid.max_elevation():.0f} m"
            )

        tracker.set_status("Finding available 3D models...", 0.81)
        try:
            provider = getattr(props, "building_provider", self._provider("road_provider"))
            self._model_candidates = Osm3DModelClient().list_model_candidates(
                bbox,
                provider=provider,
            )
            self._model_candidates.extend(self._asset_candidates_for_bbox(bbox))
            self._enrich_model_candidates(self._model_candidates, self._prefs)
            tracker.log(f"Available 3D model markers: {len(self._model_candidates)}")
        except Exception as exc:
            self._model_candidates = []
            tracker.log(f"⚠ 3D model catalog failed ({exc}), continuing")

        if (
            (not data or not data.ways)
            and not props.import_satellite
            and not props.import_relief
            and not data.points
        ):
            raise ProviderError("No ways returned from OSM API. Check place name or bbox.")
        tracker.set_status("Geographic data fetched", 0.83)
        return data

    def _fetch_external_data(self, tracker: ProgressTracker) -> GeoMapData:
        self._raise_if_cancelled(tracker)
        tracker.set_status("Calling external GeoMap service...", 0.18)
        tracker.log(f"External data service: {self._prefs.service_url}")
        result = GeoMapServiceClient(
            self._prefs.service_url,
            auto_start=self._prefs.service_auto_start,
            port=self._prefs.service_port,
        ).generate(self._props, self._prefs)
        self._raise_if_cancelled(tracker)
        for line in result.logs:
            tracker.log(f"service: {line}")
        self._satellite_tiles = result.satellite_tiles
        self._dem_grid = result.dem_grid
        self._dem_tiles = result.dem_tiles
        try:
            provider = getattr(self._props, "building_provider", self._provider("road_provider"))
            self._model_candidates = Osm3DModelClient().list_model_candidates(
                result.osm_data.bbox,
                provider=provider,
            )
            self._model_candidates.extend(self._asset_candidates_for_bbox(result.osm_data.bbox))
            self._enrich_model_candidates(self._model_candidates, self._prefs)
            tracker.log(f"Available 3D model markers: {len(self._model_candidates)}")
        except Exception as exc:
            self._model_candidates = []
            tracker.log(f"⚠ 3D model catalog failed ({exc}), continuing")
        tracker.set_status("External geographic data fetched", 0.83)
        return result.osm_data

    @staticmethod
    def _raise_if_cancelled(tracker: ProgressTracker) -> None:
        if tracker.is_cancelled():
            raise CancelledGeneration("Cancelled")

    def _fetch_vector_data(self, bbox: BoundingBox, props, tracker: ProgressTracker) -> GeoMapData:
        requests = []
        if props.import_coast:
            requests.append(
                {
                    "label": "coastlines",
                    "provider": self._provider("coast_provider"),
                    "coastlines": True,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_rivers:
            requests.append(
                {
                    "label": "rivers",
                    "provider": self._provider("river_provider"),
                    "coastlines": False,
                    "rivers": True,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_roads:
            requests.append(
                {
                    "label": "roads",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": True,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_admin:
            requests.append(
                {
                    "label": f"admin level {props.admin_level}",
                    "provider": self._provider("admin_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": props.admin_level,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_buildings:
            requests.append(
                {
                    "label": "3D buildings",
                    "provider": getattr(props, "building_provider", "AUTO"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": True,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_cities or props.import_place_labels:
            requests.append(
                {
                    "label": "cities",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": True,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_poi_historic:
            requests.append(
                {
                    "label": "historic POI",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": True,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_poi_cultural:
            requests.append(
                {
                    "label": "cultural POI",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": True,
                    "poi_administrative": False,
                    "poi_natural": False,
                }
            )
        if props.import_poi_administrative:
            requests.append(
                {
                    "label": "administrative POI",
                    "provider": self._provider("admin_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": True,
                    "poi_natural": False,
                }
            )
        if props.import_poi_natural:
            requests.append(
                {
                    "label": "natural POI",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": True,
                    "landuse": False,
                }
            )
        if getattr(props, "import_landuse", False):
            requests.append(
                {
                    "label": "land use",
                    "provider": self._provider("road_provider"),
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                    "landuse": True,
                }
            )

        grouped: dict[str, dict] = {}
        for request in requests:
            provider = request["provider"]
            if provider not in grouped:
                grouped[provider] = {
                    "labels": [],
                    "coastlines": False,
                    "rivers": False,
                    "roads": False,
                    "buildings": False,
                    "admin_level": None,
                    "cities": False,
                    "poi_historic": False,
                    "poi_cultural": False,
                    "poi_administrative": False,
                    "poi_natural": False,
                    "landuse": False,
                }
            grouped[provider]["labels"].append(request["label"])
            grouped[provider]["coastlines"] |= request["coastlines"]
            grouped[provider]["rivers"] |= request["rivers"]
            grouped[provider]["roads"] |= request["roads"]
            grouped[provider]["buildings"] |= request["buildings"]
            if request["admin_level"]:
                grouped[provider]["admin_level"] = request["admin_level"]
            grouped[provider]["cities"] |= request["cities"]
            grouped[provider]["poi_historic"] |= request["poi_historic"]
            grouped[provider]["poi_cultural"] |= request["poi_cultural"]
            grouped[provider]["poi_administrative"] |= request["poi_administrative"]
            grouped[provider]["poi_natural"] |= request["poi_natural"]
            grouped[provider]["landuse"] |= request.get("landuse", False)

        ways = []
        points = []
        seen = set()
        for index, (provider, request) in enumerate(grouped.items(), start=1):
            tracker.log(
                f"OSM provider {index}/{len(grouped)}: {provider} "
                f"for {', '.join(request['labels'])}"
            )
            data = self._client.fetch_features(
                bbox,
                coastlines=request["coastlines"],
                rivers=request["rivers"],
                roads=request["roads"],
                buildings=request["buildings"],
                admin_level=request["admin_level"],
                cities=request["cities"],
                poi_historic=request["poi_historic"],
                poi_cultural=request["poi_cultural"],
                poi_administrative=request["poi_administrative"],
                poi_natural=request["poi_natural"],
                landuse=request.get("landuse", False),
                provider=provider,
                progress=tracker.set_status,
                should_cancel=tracker.is_cancelled,
            )
            for way in data.ways:
                key = (
                    way.id,
                    tuple((round(node.lat, 7), round(node.lon, 7)) for node in way.geometry),
                )
                if key not in seen:
                    seen.add(key)
                    ways.append(way)
            points.extend(data.points)
        return GeoMapData(ways=ways, bbox=bbox, points=points)

    @staticmethod
    def _get_addon_preferences(context):
        package = __package__.split(".")[0] if __package__ else "geomap_generator"
        candidates = (
            __package__,
            package,
            "geomap_generator",
            "3d-map-generator",
        )
        addons = context.preferences.addons
        for key in candidates:
            if key and key in addons:
                return addons[key].preferences
        return None

    def _provider(self, name: str) -> str:
        return getattr(self._prefs, name, "AUTO") if self._prefs else "AUTO"

    def _basemap_tokens(self, prefs: ProviderSettings | None = None) -> dict[str, str]:
        prefs = prefs or getattr(self, "_prefs", None)
        if prefs is None:
            return {}
        return {
            "maptiler": prefs.maptiler_token,
            "mapbox": prefs.mapbox_token,
            "google": prefs.google_token,
            "sentinel_hub": prefs.sentinel_hub_token,
            "planet": prefs.planet_token,
            "maxar": prefs.maxar_token,
            "airbus": prefs.airbus_token,
        }

    def _start_mesh_creation(
        self,
        context,
        osm_data: GeoMapData,
        props,
        tracker: ProgressTracker,
    ) -> None:
        try:
            self._mesh_steps = self._mesh_creation_steps(context, osm_data, props, tracker)
            self._advance_mesh_creation(tracker)
        except Exception as e:
            self._mesh_steps = None
            tracker.error = f"Mesh creation failed: {e}"
            tracker.log(f"✗ Mesh error: {e}")
            tracker.is_running = False

    def _advance_mesh_creation(self, tracker: ProgressTracker) -> None:
        if self._mesh_steps is None:
            return
        try:
            next(self._mesh_steps)
        except StopIteration:
            self._mesh_steps = None
        except Exception as e:
            self._mesh_steps = None
            tracker.error = f"Mesh creation failed: {e}"
            tracker.log(f"✗ Mesh error: {e}")
            tracker.is_running = False

    def _mesh_creation_steps(
        self,
        context,
        osm_data: GeoMapData,
        props,
        tracker: ProgressTracker,
    ):
        started_at = time.time()
        executor = ThreadPoolExecutor(max_workers=1)
        assert_main_thread()
        try:
            self._raise_if_cancelled(tracker)
            tracker.set_status("Starting Blender mesh creation...", self._MESH_PROGRESS_START)
            tracker.log("Incremental mesh creation started on Blender main thread")
            phase_at = time.time()
            scene_scale = SceneScale.from_scene(
                context.scene, osm_data.bbox, props.detail_level, props.dem_height_scale
            )
            tracker.log(f"Scene scale ready in {time.time() - phase_at:.2f}s")
            tracker.log(
                f"DEM height scale: {scene_scale.dem_height_scale:.6f} BU/m "
                f"(UI value {props.dem_height_scale:.6f})"
            )
            tracker.set_status("Scene scale ready", 0.835)
            yield

            terrain_renderer = TerrainRenderer()

            if props.import_satellite and not props.import_relief:
                for index, tile in enumerate(self._satellite_tiles, start=1):
                    self._raise_if_cancelled(tracker)
                    tracker.set_status(
                        f"Creating satellite bounding box {index}/{len(self._satellite_tiles)}",
                        0.84,
                    )
                    phase_at = time.time()
                    tracker.log(
                        f"Creating satellite bounding box {index}/{len(self._satellite_tiles)}"
                    )
                    terrain_renderer.create_satellite_bbox(
                        context, tile, osm_data.bbox, props, index
                    )
                    tracker.log(
                        f"Satellite bounding box {index}/{len(self._satellite_tiles)} "
                        f"created in {time.time() - phase_at:.2f}s"
                    )
                    yield

            if props.import_relief and self._dem_tiles:
                dem_height_scale = scene_scale.dem_height_scale
                dem_min = min(grid.min_elevation() for _tile, grid in self._dem_tiles)
                for index, (tile, dem_grid) in enumerate(self._dem_tiles, start=1):
                    self._raise_if_cancelled(tracker)
                    tracker.set_status(
                        f"Building DEM terrain tile {index}/{len(self._dem_tiles)} "
                        f"({dem_grid.rows}x{dem_grid.cols})",
                        0.84,
                    )
                    tile_at = time.time()
                    tracker.log(
                        f"Creating DEM terrain tile {index}/{len(self._dem_tiles)} "
                        f"({dem_grid.rows}x{dem_grid.cols})"
                    )
                    terrain_renderer.create_dem_mesh(
                        context,
                        dem_grid,
                        props,
                        dem_height_scale,
                        scene_scale=scene_scale,
                        texture_path=tile.image_path,
                        projection_bbox=osm_data.bbox,
                        suffix=f"_{index:03d}",
                        min_elevation_override=dem_min,
                    )
                    tracker.log(
                        f"DEM terrain tile {index}/{len(self._dem_tiles)} created "
                        f"in {time.time() - tile_at:.2f}s"
                    )
                    yield
            elif props.import_relief and self._dem_grid:
                status = (
                    "Creating textured DEM terrain mesh..."
                    if props.import_satellite
                    else "Creating DEM terrain mesh..."
                )
                tracker.set_status(f"{status} ({self._dem_grid.rows}x{self._dem_grid.cols})", 0.84)
                self._raise_if_cancelled(tracker)
                phase_at = time.time()
                tracker.log(
                    f"Creating DEM terrain mesh "
                    f"({self._dem_grid.rows}x{self._dem_grid.cols})"
                )
                texture_path = (
                    self._satellite_tiles[0].image_path if self._satellite_tiles else None
                )
                terrain_renderer.create_dem_mesh(
                    context,
                    self._dem_grid,
                    props,
                    scene_scale.dem_height_scale,
                    scene_scale=scene_scale,
                    texture_path=texture_path,
                    projection_bbox=osm_data.bbox,
                )
                tracker.log(f"DEM terrain mesh created in {time.time() - phase_at:.2f}s")
                yield

            # Contour lines (requires DEM)
            if getattr(props, "import_contours", False) and props.import_relief:
                self._raise_if_cancelled(tracker)
                tracker.set_status("Generating contour lines...", 0.852)
                dem_grids = [grid for _tile, grid in self._dem_tiles]
                if self._dem_grid:
                    dem_grids.append(self._dem_grid)
                if dem_grids:
                    dem_min = min(g.min_elevation() for g in dem_grids)
                    for ci, dem_g in enumerate(dem_grids, start=1):
                        terrain_renderer.create_contour_lines(
                            context,
                            dem_g,
                            props,
                            scene_scale.dem_height_scale,
                            projection_bbox=osm_data.bbox,
                            min_elevation_override=dem_min,
                        )
                    tracker.log(
                        f"Contour lines created ({len(dem_grids)} grid(s), "
                        f"interval {getattr(props,'contour_interval_m',50):.0f}m)"
                    )
                    yield

            if props.create_map_box:
                import bpy as _bpy
                self._raise_if_cancelled(tracker)
                tracker.set_status("Creating map box...", 0.855)
                dem_obj = next(
                    (o for o in _bpy.data.objects if o.get("geomap_layer") == "dem"),
                    None,
                )
                terrain_renderer.create_map_box(
                    context, osm_data.bbox, props, scene_scale, dem_obj=dem_obj
                )
                tracker.log("Map box created")
                yield

            tracker.set_status("Splitting data into mesh tasks...", 0.86)
            phase_at = time.time()
            dem_sampler = self._create_dem_sampler(scene_scale, props)
            building_ways, landuse_ways, vector_ways = self._split_building_ways(osm_data, props)
            if len(vector_ways) > self._MAX_TOTAL_VECTOR_WAYS:
                tracker.log(
                    f"⚠ {len(vector_ways)} vector ways exceeds cap "
                    f"{self._MAX_TOTAL_VECTOR_WAYS}, truncating"
                )
                vector_ways = vector_ways[: self._MAX_TOTAL_VECTOR_WAYS]
            vector_data = GeoMapData(ways=vector_ways, bbox=osm_data.bbox, points=osm_data.points)
            vector_layers = self._split_vector_layers(vector_data)
            all_points = self._unique_points(osm_data.points)
            # city points only get EMPTY markers if import_cities is enabled
            points = [
                p for p in all_points
                if p.category != "city" or props.import_cities
            ]
            estimated_buildings = (
                len([way for way in building_ways if len(way.geometry) >= 4])
                if props.import_buildings
                else 0
            )
            total_steps = self._mesh_progress_total_steps(
                satellite_count=len(self._satellite_tiles)
                if props.import_satellite and not props.import_relief
                else 0,
                dem_tile_count=len(self._dem_tiles) if props.import_relief else 0,
                has_single_dem=bool(props.import_relief and self._dem_grid and not self._dem_tiles),
                vector_layer_count=len(vector_layers),
                point_count=len(points),
                building_count=estimated_buildings,
            )
            completed_steps = 1
            tracker.log(
                "Projected data split in "
                f"{time.time() - phase_at:.2f}s "
                f"({len(vector_ways)} vector ways, {len(building_ways)} building ways, "
                f"{len(points)} points, {len(vector_layers)} layers, "
                f"{total_steps} mesh tasks)"
            )
            tracker.set_status(
                "Mesh plan ready: "
                f"{len(vector_layers)} layers, {len(points)} POI, {estimated_buildings} buildings",
                self._mesh_progress(completed_steps, total_steps),
            )
            yield

            vector_objects = []
            building_objects = []
            point_objects = []
            landuse_objects = []
            if not vector_layers and not building_ways and not osm_data.points and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
                )
            # Render land use polygons before other vector layers
            if landuse_ways:
                self._raise_if_cancelled(tracker)
                tracker.set_status(
                    f"Creating land use polygons ({len(landuse_ways)} areas)...",
                    self._mesh_progress(completed_steps, total_steps),
                )
                lu_layers = self._split_vector_layers(
                    GeoMapData(ways=landuse_ways, bbox=osm_data.bbox)
                )
                landuse_objects = VectorRenderer().render_landuse(
                    context, lu_layers, props, dem_sampler, scene_scale
                )
                tracker.log(f"Land use rendered: {len(landuse_objects)} objects")
                yield

            model_marker_objects = self._render_model_candidate_markers(
                context,
                self._model_candidates,
                osm_data.bbox,
                props,
                dem_sampler,
                scene_scale,
            )
            if model_marker_objects:
                tracker.log(f"3D model markers placed: {len(model_marker_objects)}")
                yield

            if not vector_layers and not building_ways and not osm_data.points:
                AnnotationRenderer._annotate_root_collection(
                    context, osm_data.bbox, props, scene_scale.km_per_bu
                )
                tracker.log("No vector ways returned; non-vector layers created")
                tracker.result = True
                tracker.set_status("Completed", 1.0)
                tracker.is_running = False
                return

            vector_renderer = VectorRenderer()
            for index, (layer_key, layer_name, layer_data) in enumerate(vector_layers, start=1):
                self._raise_if_cancelled(tracker)
                tracker.set_status(
                    f"Preparing vector payload {index}/{len(vector_layers)}: "
                    f"{layer_name} ({len(layer_data.ways)} ways)",
                    self._mesh_progress(completed_steps, total_steps),
                )
                phase_at = time.time()
                tracker.log(
                    f"Preparing vector payload {index}/{len(vector_layers)}: "
                    f"{layer_name} ({len(layer_data.ways)} ways)"
                )
                future = executor.submit(
                    build_vector_payload,
                    layer_key,
                    layer_name,
                    layer_data,
                    props,
                    dem_sampler,
                    scene_scale,
                )
                while not future.done():
                    self._raise_if_cancelled(tracker)
                    yield
                payload = future.result()
                completed_steps += 1
                tracker.log(
                    f"Vector payload {index}/{len(vector_layers)} prepared "
                    f"in {time.time() - phase_at:.2f}s"
                )
                tracker.set_status(
                    f"Prepared vector payload {index}/{len(vector_layers)}",
                    self._mesh_progress(completed_steps, total_steps),
                )
                yield
                if payload is None:
                    completed_steps += 1
                    tracker.set_status(
                        f"Skipped empty vector layer {index}/{len(vector_layers)}",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    continue
                tracker.set_status(
                    f"Committing vector layer {index}/{len(vector_layers)}: {layer_name}",
                    self._mesh_progress(completed_steps, total_steps),
                )
                commit_at = time.time()
                vector_obj = vector_renderer.commit_payload(
                    context,
                    payload,
                    active=len(vector_objects) == 0,
                )
                if vector_obj:
                    vector_objects.append(vector_obj)
                tracker.log(
                    f"Vector layer {index}/{len(vector_layers)} committed "
                    f"in {time.time() - commit_at:.2f}s"
                )
                completed_steps += 1
                tracker.set_status(
                    f"Committed vector layer {index}/{len(vector_layers)}",
                    self._mesh_progress(completed_steps, total_steps),
                )
                yield

            if points:
                for start in range(0, len(points), self._POINT_BATCH_SIZE):
                    self._raise_if_cancelled(tracker)
                    end = min(start + self._POINT_BATCH_SIZE, len(points))
                    tracker.set_status(
                        f"Creating POI batch {start + 1}-{end}/{len(points)}",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    phase_at = time.time()
                    batch_data = GeoMapData(
                        ways=[],
                        bbox=osm_data.bbox,
                        points=points[start:end],
                    )
                    point_objects.extend(
                        vector_renderer.render_points(
                            context, batch_data, props, dem_sampler, scene_scale
                        )
                    )
                    tracker.log(
                        f"POI batch {start + 1}-{end}/{len(points)} created "
                        f"in {time.time() - phase_at:.2f}s"
                    )
                    completed_steps += 1
                    tracker.set_status(
                        f"Created POI {end}/{len(points)}",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    yield

            if props.import_buildings and building_ways:
                self._raise_if_cancelled(tracker)
                phase_at = time.time()
                buildings = Osm3DModelClient().buildings_from_ways(building_ways)
                buildings = aggregate_building_parts(buildings)
                use_detailed = self._use_detailed_buildings(props, osm_data.bbox)

                if use_detailed:
                    total_steps = self._mesh_progress_total_steps(
                        satellite_count=len(self._satellite_tiles)
                        if props.import_satellite and not props.import_relief
                        else 0,
                        dem_tile_count=len(self._dem_tiles) if props.import_relief else 0,
                        has_single_dem=bool(props.import_relief and self._dem_grid and not self._dem_tiles),
                        vector_layer_count=len(vector_layers),
                        point_count=len(points),
                        building_count=len(buildings),
                    )
                    tracker.log(
                        f"Prepared {len(buildings)} 3D buildings (DETAILED mode, OSM materials) "
                        f"in {time.time() - phase_at:.2f}s"
                    )
                    tracker.set_status(
                        f"Prepared {len(buildings)} detailed buildings",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    yield

                    renderer = Osm3DModelRenderer()
                    z_offset = scaled_map_value(props.vector_z_offset, scene_scale)
                    _DETAIL_YIELD_EVERY = 20
                    for bld_index, building in enumerate(buildings, start=1):
                        self._raise_if_cancelled(tracker)
                        if dem_sampler and props.drape_vectors_on_dem:
                            # Per-vertex sampling: min so building sits on terrain regardless of slope
                            terrain_z = (
                                min(dem_sampler.sample_z(n.lat, n.lon) for n in building.geometry)
                                if building.geometry
                                else dem_sampler.sample_z(building.center_lat, building.center_lon)
                            )
                        else:
                            terrain_z = 0.0
                        try:
                            obj = renderer.render_building(
                                context,
                                building,
                                osm_data.bbox,
                                props.detail_level,
                                scene_scale.km_per_bu,
                                base_z=terrain_z + z_offset,
                                use_osm_material=True,
                            )
                            if obj:
                                building_objects.append(obj)
                        except Exception:
                            pass
                        if bld_index % _DETAIL_YIELD_EVERY == 0 or bld_index == len(buildings):
                            completed_steps += 1
                            tracker.set_status(
                                f"Built detailed buildings {bld_index}/{len(buildings)}",
                                self._mesh_progress(completed_steps, total_steps),
                            )
                            yield
                else:
                    # Group buildings by material category for coloured batch meshes
                    mat_groups: dict[str, list] = {}
                    for _b in buildings:
                        _key = building_batch_material_key(_b.tags)
                        mat_groups.setdefault(_key, []).append(_b)
                    all_chunks: list[tuple] = []  # (batch, mat_key)
                    for _mat_key, _mat_blds in mat_groups.items():
                        for _chunk in self._building_chunks(_mat_blds):
                            all_chunks.append((_chunk, _mat_key))

                    total_steps = self._mesh_progress_total_steps(
                        satellite_count=len(self._satellite_tiles)
                        if props.import_satellite and not props.import_relief
                        else 0,
                        dem_tile_count=len(self._dem_tiles) if props.import_relief else 0,
                        has_single_dem=bool(props.import_relief and self._dem_grid and not self._dem_tiles),
                        vector_layer_count=len(vector_layers),
                        point_count=len(points),
                        building_chunk_count=len(all_chunks),
                    )
                    tracker.log(
                        f"Prepared {len(buildings)} 3D building footprints "
                        f"as {len(all_chunks)} material-grouped chunk(s) "
                        f"({len(mat_groups)} material bucket(s)) "
                        f"in {time.time() - phase_at:.2f}s"
                    )
                    tracker.set_status(
                        f"Prepared {len(buildings)} building footprints for merged mesh",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    yield

                    renderer = Osm3DModelRenderer()
                    z_offset = scaled_map_value(props.vector_z_offset, scene_scale)
                    for chunk_index, (batch, mat_key) in enumerate(all_chunks, start=1):
                        self._raise_if_cancelled(tracker)
                        tracker.set_status(
                            f"Preparing merged 3D buildings mesh {chunk_index}/{len(all_chunks)}",
                            self._mesh_progress(completed_steps, total_steps),
                        )
                        phase_at = time.time()
                        future = executor.submit(
                            self._build_building_payloads,
                            batch,
                            osm_data.bbox,
                            props,
                            dem_sampler,
                            scene_scale,
                            z_offset,
                            name=self._building_chunk_name(chunk_index, len(all_chunks)),
                            material_key=mat_key,
                        )
                        while not future.done():
                            self._raise_if_cancelled(tracker)
                            yield
                        payload = future.result()
                        completed_steps += 1
                        tracker.log(
                            f"Merged 3D buildings payload {chunk_index}/{len(all_chunks)} "
                            f"({mat_key}) prepared in {time.time() - phase_at:.2f}s"
                        )
                        tracker.set_status(
                            f"Prepared merged 3D buildings mesh {chunk_index}/{len(all_chunks)}",
                            self._mesh_progress(completed_steps, total_steps),
                        )
                        yield
                        tracker.set_status(
                            f"Committing merged 3D buildings mesh {chunk_index}/{len(all_chunks)}",
                            self._mesh_progress(completed_steps, total_steps),
                        )
                        commit_at = time.time()
                        obj = renderer.commit_batch_payload(
                            context,
                            payload,
                            batch_index=chunk_index,
                        )
                        if obj:
                            building_objects.append(obj)
                        tracker.log(
                            f"Merged 3D buildings mesh {chunk_index}/{len(all_chunks)} committed "
                            f"with {payload.building_count} simplified buildings "
                            f"in {time.time() - commit_at:.2f}s"
                        )
                        completed_steps += 1
                        tracker.set_status(
                            f"Committed merged 3D buildings mesh "
                            f"{chunk_index}/{len(all_chunks)}",
                            self._mesh_progress(completed_steps, total_steps),
                        )
                        yield

            if not vector_objects and not point_objects and not building_objects and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
                )
            AnnotationRenderer._annotate_root_collection(
                context, osm_data.bbox, props, scene_scale.km_per_bu
            )
            if not vector_objects and not point_objects and not building_objects:
                tracker.log("Selected vector layers produced no usable geometry")
                tracker.result = True
                tracker.set_status("Completed", 1.0)
                tracker.is_running = False
                return

            tracker.set_status(
                "Annotating map scale and writing metadata...",
                self._mesh_progress(completed_steps, total_steps),
            )
            phase_at = time.time()
            tracker.log(
                f"Scene: {scene_scale.unit_system} "
                f"(scale_length={scene_scale.scale_length:.4f} {scene_scale.unit_label}/BU)"
            )
            tracker.log(
                f"Map extent: {scene_scale.extent_lat_km:.0f} km × "
                f"{scene_scale.extent_lon_km:.0f} km"
            )
            tracker.log(f"Scale: 1 BU ≈ {scene_scale.km_per_bu:.2f} km")

            for obj in [*vector_objects, *point_objects, *building_objects]:
                obj["geomap_km_per_bu"] = round(scene_scale.km_per_bu, 4)
                obj["geomap_extent_km"] = (
                    f"{scene_scale.extent_lat_km:.1f} × {scene_scale.extent_lon_km:.1f}"
                )
                obj["geomap_scene_unit"] = scene_scale.unit_system

            AnnotationRenderer().render(
                context, osm_data.bbox, props, vector_objects, scene_scale
            )
            tracker.log(f"Annotations created in {time.time() - phase_at:.2f}s")

            if props.import_place_labels:
                self._raise_if_cancelled(tracker)
                tracker.set_status("Creating place labels...", self._mesh_progress(completed_steps, total_steps))
                phase_at = time.time()
                city_points = [p for p in all_points if p.category == "city"]
                label_objects = PlaceLabelRenderer().render(
                    context,
                    city_points,
                    osm_data.bbox,
                    props,
                    scene_scale,
                    dem_sampler,
                )
                tracker.log(f"Place labels created: {len(label_objects)} in {time.time() - phase_at:.2f}s")
                yield
            completed_steps += 1
            tracker.set_status(
                "Annotations complete",
                self._mesh_progress(completed_steps, total_steps),
            )
            yield

            if props.import_relief:
                import bpy as _bpy
                dem_obj = next(
                    (o for o in _bpy.data.objects if o.get("geomap_layer") == "dem"),
                    None,
                )
                if dem_obj:
                    tracker.set_status("Linking curves to DEM surface...", 0.998)
                    curve_layers = {
                        "dem", "satellite_bbox", "map_box", "contours",
                        "osm_3d_building", "osm_3d_buildings_batch",
                    }
                    for obj in _bpy.data.objects:
                        lk = obj.get("geomap_layer", "")
                        if lk and lk not in curve_layers and obj.type == "CURVE":
                            add_shrinkwrap_nearest_surface(obj, dem_obj)
                    tracker.log(
                        f"Shrinkwrap (nearest surface) added to curves "
                        f"(target: {dem_obj.name})"
                    )
                    yield

            if props.import_weather:
                self._raise_if_cancelled(tracker)
                tracker.set_status("Fetching weather data...", 0.997)
                tracker.set_weather_status("Weather: fetching forecast data...", 0.10)
                phase_at = time.time()
                try:
                    from .weather import WeatherClient
                    from .weather_renderer import WeatherRenderer
                    weather_pts = WeatherClient().fetch_for_granularity(
                        osm_data.bbox,
                        osm_data.points,
                        getattr(props, "weather_granularity", "GRID"),
                        grid_size=getattr(props, "weather_grid_size", 3),
                        provider=getattr(props, "weather_provider", self._prefs.weather_provider),
                        openweathermap_token=self._prefs.openweathermap_token,
                        weatherapi_token=self._prefs.weatherapi_token,
                        forecast_day=getattr(props, "weather_forecast_day", 0),
                    )
                    tracker.set_weather_status("Weather: building mesh symbols...", 0.70)
                    WeatherRenderer().render(context, weather_pts, osm_data.bbox, props, scene_scale)
                    tracker.set_weather_status("Weather ready", 1.0)
                    tracker.log(f"Weather icons placed: {len(weather_pts)} in {time.time() - phase_at:.2f}s")
                except Exception as exc:
                    tracker.set_weather_status("Weather failed", 1.0)
                    tracker.log(f"⚠ Weather layer failed ({exc}), skipping")
                yield

            tracker.log(
                f"Vector layers: {len(vector_objects)} objects, "
                f"POI empties: {len(point_objects)}, 3D buildings: {len(building_objects)}"
            )
            tracker.log(f"Incremental mesh creation completed in {time.time() - started_at:.2f}s")
            tracker.result = True
            tracker.set_status("Completed", 1.0)
            tracker.is_running = False
            return
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            set_offline_mode(False)

    def _render_buildings(
        self,
        context,
        ways,
        bbox: BoundingBox,
        props,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale,
    ) -> list:
        client = Osm3DModelClient()
        renderer = Osm3DModelRenderer()
        z_offset = scaled_map_value(props.vector_z_offset, scene_scale)
        objects = []
        for building in client.buildings_from_ways(ways):
            terrain_z = (
                dem_sampler.sample_z(building.center_lat, building.center_lon)
                if dem_sampler and props.drape_vectors_on_dem
                else 0.0
            )
            objects.append(
                renderer.render_building(
                    context,
                    building,
                    bbox,
                    props.detail_level,
                    scene_scale.km_per_bu,
                    base_z=terrain_z + z_offset,
                )
            )
        return objects

    @staticmethod
    def _render_model_candidate_markers(
        context,
        candidates,
        bbox: BoundingBox,
        props,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale,
    ) -> list:
        if not candidates:
            clear_geomap_child_collection("3D Model Markers")
            return []
        import bpy as _bpy

        clear_geomap_child_collection("3D Model Markers")
        projector = BboxProjector(bbox, props.detail_level)
        marker_size = scaled_map_value(0.12, scene_scale)
        z_lift = scaled_map_value(float(props.vector_z_offset) * 2.0 + 0.05, scene_scale)
        created = []
        used_names: dict[str, int] = {}
        for candidate in candidates:
            x, y, _ = projector.project(candidate.lat, candidate.lon)
            terrain_z = (
                dem_sampler.sample_z(candidate.lat, candidate.lon)
                if dem_sampler and props.drape_vectors_on_dem
                else 0.0
            )
            base_name = "GeoMap_Model_" + "".join(
                ch if ch.isalnum() or ch in {"_", "-"} else "_"
                for ch in str(candidate.name).strip()
            )[:36]
            count = used_names.get(base_name, 0)
            used_names[base_name] = count + 1
            obj_name = base_name if count == 0 else f"{base_name}_{count + 1}"
            obj = _bpy.data.objects.new(obj_name, None)
            obj.empty_display_type = "CUBE"
            obj.empty_display_size = marker_size
            obj.location = (x, y, terrain_z + z_lift)
            obj.color = (0.95, 0.72, 0.18, 1.0)
            obj["geomap_layer"] = "model_candidate"
            obj["geomap_model_name"] = candidate.name
            obj["geomap_osm_id"] = str(candidate.id)
            obj["geomap_osm_type"] = candidate.osm_type
            obj["geomap_lat"] = candidate.lat
            obj["geomap_lon"] = candidate.lon
            source = str(getattr(candidate, "source", "osm"))
            obj["geomap_model_source"] = source
            obj["geomap_model_has_geometry"] = bool(getattr(candidate, "has_geometry", False))
            if source == "catalog":
                obj["geomap_asset_id"] = str(getattr(candidate, "asset_id", ""))
                obj["geomap_asset_type"] = str(getattr(candidate, "asset_type", ""))
                obj["geomap_asset_url"] = str(getattr(candidate, "url", ""))
                obj["geomap_asset_path"] = str(getattr(candidate, "path", ""))
            sketchfab = getattr(candidate, "sketchfab", None)
            if isinstance(sketchfab, dict):
                obj["geomap_sketchfab_query"] = sketchfab.get("query", "")
                obj["geomap_sketchfab_search_url"] = sketchfab.get("search_url", "")
                obj["geomap_sketchfab_uid"] = sketchfab.get("uid", "")
                obj["geomap_sketchfab_model_url"] = sketchfab.get("viewer_url", "")
                obj["geomap_sketchfab_download_api_url"] = sketchfab.get("download_api_url", "")
                obj["geomap_sketchfab_license"] = sketchfab.get("license", "")
                obj["geomap_sketchfab_author"] = sketchfab.get("author", "")
                obj["geomap_sketchfab_downloadable"] = bool(sketchfab.get("is_downloadable", False))
            google_url = str(getattr(candidate, "google_3d_tiles_url", ""))
            if google_url:
                obj["geomap_google_photorealistic_3d_tiles_url"] = google_url
            link_to_geomap_collection(context, obj, "3D Model Markers")
            created.append(obj)
        return created

    @staticmethod
    def _enrich_model_candidates(candidates, prefs: ProviderSettings | None) -> None:
        sketchfab_token = getattr(prefs, "sketchfab_token", "") if prefs else ""
        google_token = getattr(prefs, "google_token", "") if prefs else ""
        google_url = google_photorealistic_3d_tiles_url(google_token) if google_token else ""
        for candidate in candidates[:40]:
            query = str(getattr(candidate, "name", "") or "").strip()
            if query:
                try:
                    sketchfab = find_sketchfab_model(query, token=sketchfab_token)
                except Exception:
                    sketchfab = None
                if sketchfab is None:
                    sketchfab = {
                        "query": query,
                        "search_url": sketchfab_search_url(query),
                        "uid": "",
                        "viewer_url": "",
                        "download_api_url": "",
                        "license": "",
                        "author": "",
                        "is_downloadable": False,
                    }
                try:
                    setattr(candidate, "sketchfab", sketchfab)
                except Exception:
                    pass
            if google_url:
                try:
                    setattr(candidate, "google_3d_tiles_url", google_url)
                except Exception:
                    pass

    @staticmethod
    def _asset_candidates_for_bbox(bbox: BoundingBox) -> list:
        candidates = []
        for entry in asset_entries_for_bbox(bbox):
            loc = asset_entry_location(entry)
            if loc is None:
                continue
            lat, lon = loc
            entry_id = str(entry.get("id") or entry.get("url") or entry.get("path"))
            source = str(entry.get("url") or entry.get("path") or "")
            asset_type = str(entry.get("type") or entry.get("asset_type") or "").lower().strip(".")
            if not asset_type:
                asset_type = Path(source.split("?", 1)[0]).suffix.lower().strip(".") or "kmz"
            candidates.append(
                SimpleNamespace(
                    id=abs(hash(("catalog", entry_id))) % 2_000_000_000,
                    osm_type="catalog",
                    name=str(entry.get("name") or entry_id),
                    lat=lat,
                    lon=lon,
                    tags={},
                    source="catalog",
                    has_geometry=True,
                    asset_id=entry_id,
                    asset_type=asset_type,
                    url=str(entry.get("url") or ""),
                    path=str(entry.get("path") or ""),
                )
            )
        return candidates

    @staticmethod
    def _build_building_payloads(
        buildings,
        bbox: BoundingBox,
        props,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale,
        z_offset: float,
        name: str,
        material_key: str = "default",
    ):
        def base_z_for_building(building):
            if dem_sampler and props.drape_vectors_on_dem:
                # Per-vertex DEM sampling: use minimum so building never sinks into terrain
                if building.geometry:
                    terrain_z = min(
                        dem_sampler.sample_z(node.lat, node.lon)
                        for node in building.geometry
                    )
                else:
                    terrain_z = dem_sampler.sample_z(
                        building.center_lat, building.center_lon
                    )
            else:
                terrain_z = 0.0
            return terrain_z + z_offset

        return build_building_batch_payload(
            buildings,
            bbox,
            props.detail_level,
            scene_scale.km_per_bu,
            base_z_for_building,
            name=name,
            max_vertices_per_building=12,
            material_color=building_batch_color(material_key),
        )

    @classmethod
    def _mesh_progress_total_steps(
        cls,
        *,
        satellite_count: int,
        dem_tile_count: int,
        has_single_dem: bool,
        vector_layer_count: int,
        point_count: int,
        building_count: int = 0,
        building_chunk_count: int | None = None,
    ) -> int:
        del satellite_count, dem_tile_count, has_single_dem
        point_batches = cls._batch_count(point_count, cls._POINT_BATCH_SIZE)
        building_batches = (
            building_chunk_count
            if building_chunk_count is not None
            else cls._batch_count(building_count, cls._BUILDING_BATCH_SIZE)
        )
        return max(
            1,
            1
            + vector_layer_count * 2
            + point_batches
            + building_batches * 2
            + 1,
        )

    @classmethod
    def _mesh_progress(cls, completed_steps: int, total_steps: int) -> float:
        if total_steps <= 0:
            return cls._MESH_PROGRESS_START
        ratio = max(0.0, min(1.0, completed_steps / total_steps))
        return cls._MESH_PROGRESS_START + (
            cls._MESH_PROGRESS_END - cls._MESH_PROGRESS_START
        ) * ratio

    @staticmethod
    def _batch_count(count: int, batch_size: int) -> int:
        if count <= 0:
            return 0
        return (count + batch_size - 1) // batch_size

    @classmethod
    def _building_chunks(cls, buildings) -> list:
        chunks = []
        current = []
        current_vertices = 0
        for building in buildings:
            estimated_vertices = max(6, min(len(building.geometry), 12) * 2)
            if (
                current
                and current_vertices + estimated_vertices > cls._BUILDING_VERTEX_CHUNK_LIMIT
            ):
                chunks.append(current)
                current = []
                current_vertices = 0
            current.append(building)
            current_vertices += estimated_vertices
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _building_chunk_name(index: int, total: int) -> str:
        if total <= 1:
            return "GeoMap_3D_Buildings"
        return f"GeoMap_3D_Buildings_{index:03d}"

    @staticmethod
    def _use_detailed_buildings(props, bbox) -> bool:
        import math as _math
        quality = getattr(props, "building_quality", "AUTO")
        if quality == "DETAILED":
            return True
        if quality == "SIMPLE":
            return False
        # AUTO: estimate bbox diagonal in km
        lat_km = abs(bbox.max_lat - bbox.min_lat) * 111.0
        mid_lat = (bbox.min_lat + bbox.max_lat) / 2.0
        lon_km = abs(bbox.max_lon - bbox.min_lon) * 111.0 * _math.cos(_math.radians(mid_lat))
        diagonal_km = _math.hypot(lat_km, lon_km)
        from .osm_3d import _AUTO_LOD_DETAILED_KM
        return diagonal_km <= _AUTO_LOD_DETAILED_KM

    @staticmethod
    def _split_building_ways(
        osm_data: GeoMapData, props
    ) -> tuple[list, list, list]:
        building_ways: list = []
        landuse_ways: list = []
        vector_ways: list = []
        for way in osm_data.ways:
            if props.import_buildings and (
                way.tags.get("building") or way.tags.get("building:part")
            ):
                building_ways.append(way)
            elif collection_name_for_layer(
                vector_layer_identity(way.tags)[0]
            ) == "LandUse":
                landuse_ways.append(way)
            else:
                vector_ways.append(way)
        return building_ways, landuse_ways, vector_ways

    @staticmethod
    def _unique_points(points) -> list:
        unique = []
        seen: set[tuple[str, int, int, int]] = set()
        for point in points:
            key = (
                point.category,
                point.id,
                round(point.lat * 1_000_000),
                round(point.lon * 1_000_000),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(point)
        return unique

    @classmethod
    def _split_vector_layers(
        cls, osm_data: GeoMapData
    ) -> list[tuple[str, str, GeoMapData]]:
        grouped: dict[tuple[str, str], list] = {}
        order: list[tuple[str, str]] = []

        for way in osm_data.ways:
            layer_key, object_name = vector_layer_identity(way.tags)
            key = (layer_key, object_name)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(way)

        layers = []
        chunk_size = cls._VECTOR_LAYER_CHUNK_SIZE
        for layer_key, object_name in order:
            ways = grouped[(layer_key, object_name)]
            if len(ways) <= chunk_size:
                layers.append((layer_key, object_name, GeoMapData(ways=ways, bbox=osm_data.bbox)))
            else:
                for i in range(0, len(ways), chunk_size):
                    chunk = ways[i: i + chunk_size]
                    n = i // chunk_size + 1
                    layers.append((
                        layer_key,
                        f"{object_name}_{n:03d}",
                        GeoMapData(ways=chunk, bbox=osm_data.bbox),
                    ))
        return layers

    def _create_dem_sampler(self, scene_scale: SceneScale, props) -> DemHeightSampler | None:
        if not props.import_relief or not props.drape_vectors_on_dem:
            return None
        grids = [grid for _tile, grid in self._dem_tiles]
        if self._dem_grid:
            grids.append(self._dem_grid)
        if not grids:
            return None
        return DemHeightSampler(grids, scene_scale.dem_height_scale)


class GeoMapCancelOperator(Operator):
    bl_idname = "geomap.cancel_generation"
    bl_label = "Cancel Generation"
    bl_description = "Stop the current generation process"

    def execute(self, context):
        tracker = ProgressTracker.get_instance()
        if tracker.is_running:
            tracker.request_cancel()
            tracker.set_status("Cancelling...", tracker.progress)
            tracker.log("⊘ Cancellation requested")
            self.report({"WARNING"}, "Generation stopped")
        return {"FINISHED"}


class GeoMapLoadHistoryOperator(Operator):
    bl_idname = "geomap.load_history"
    bl_label = "Load Search"
    bl_description = "Load a previous GeoMap search into the panel"

    index: IntProperty(default=0)

    def execute(self, context):
        history = load_history()
        if not 0 <= self.index < len(history):
            self.report({"ERROR"}, "Search history entry not found")
            return {"CANCELLED"}
        apply_snapshot(context.scene.geomap_props, history[self.index])
        self.report({"INFO"}, "Search loaded")
        return {"FINISHED"}


class GeoMapRenameHistoryOperator(Operator):
    bl_idname = "geomap.rename_history"
    bl_label = "Rename Search"
    bl_description = "Rename this history entry"

    index: IntProperty(default=0)
    new_label: StringProperty(name="Name", default="")

    def invoke(self, context, event):
        history = load_history()
        if 0 <= self.index < len(history):
            self.new_label = history[self.index].get("label", "")
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.prop(self, "new_label", text="Name")

    def execute(self, context):
        label = self.new_label.strip()
        if not label:
            self.report({"ERROR"}, "Name cannot be empty")
            return {"CANCELLED"}
        if not rename_history_entry(self.index, label):
            self.report({"ERROR"}, "History entry not found")
            return {"CANCELLED"}
        return {"FINISHED"}


class GeoMapClearHistoryOperator(Operator):
    bl_idname = "geomap.clear_history"
    bl_label = "Clear Search History"
    bl_description = "Clear cached GeoMap searches"

    def execute(self, _context):
        clear_history()
        self.report({"INFO"}, "Search history cleared")
        return {"FINISHED"}


class GeoMapClearDownloadCacheOperator(Operator):
    bl_idname = "geomap.clear_download_cache"
    bl_label = "Clear Download Cache"
    bl_description = "Clear cached downloaded GeoMap data"

    namespace: StringProperty(default="")

    def execute(self, _context):
        clear_cache(self.namespace or None)
        stats = cache_stats()
        self.report(
            {"INFO"},
            f"Download cache cleared ({stats['files']} files remaining)",
        )
        return {"FINISHED"}


class GeoMapStoreBasemapTokenOperator(Operator):
    bl_idname = "geomap.store_basemap_token"
    bl_label = "Store Encrypted Basemap Token"
    bl_description = "Encrypt and store the current basemap provider token"

    token_prop: StringProperty(default="")
    encrypted_prop: StringProperty(default="")

    def execute(self, context):
        prefs = GeoMapGenerateOperator._get_addon_preferences(context)
        if prefs is None:
            self.report({"ERROR"}, "GeoMap addon preferences not found")
            return {"CANCELLED"}
        raw = getattr(prefs, self.token_prop, "")
        if not raw:
            self.report({"ERROR"}, "Enter a token first")
            return {"CANCELLED"}
        from .token_security import encrypt_token

        setattr(prefs, self.encrypted_prop, encrypt_token(raw, self.encrypted_prop))
        setattr(prefs, self.token_prop, "")
        self.report({"INFO"}, "Token stored encrypted")
        return {"FINISHED"}


class GeoMapCreatePlaceLabelOperator(Operator):
    bl_idname = "geomap.create_place_label"
    bl_label = "Create Label from Selected POI"
    bl_description = "Create 3D text objects from selected GeoMap POI or label markers"

    @staticmethod
    def _is_label_source(obj) -> bool:
        if obj is None:
            return False
        layer = obj.get("geomap_layer", "")
        return layer == "place_label" or layer.startswith("poi_")

    @classmethod
    def _label_sources(cls, context) -> list:
        objects = []
        seen: set[str] = set()
        for obj in list(getattr(context, "selected_objects", ()) or ()):
            if cls._is_label_source(obj) and obj.name not in seen:
                objects.append(obj)
                seen.add(obj.name)
        active = getattr(context, "active_object", None)
        if cls._is_label_source(active) and active.name not in seen:
            objects.append(active)
        return objects

    @classmethod
    def poll(cls, context):
        return bool(cls._label_sources(context))

    def execute(self, context):
        import math
        import bpy as _bpy

        from .annotation_renderer import _PLACE_COLORS, font_for_family
        from .blender_scene import link_to_geomap_collection, material_named

        created = 0
        for obj in self._label_sources(context):
            name = obj.get("geomap_name", "?")
            place_type = obj.get(
                "geomap_place_type",
                obj.get("geomap_category", "hamlet"),
            )
            size = float(obj.get(
                "geomap_label_text_size",
                getattr(obj, "empty_display_size", 0.12),
            ))
            if not obj.get("geomap_label_text_size"):
                size *= max(
                    float(getattr(
                        context.scene.geomap_props,
                        f"place_label_size_{place_type}",
                        1.0,
                    )),
                    0.01,
                )

            color = _PLACE_COLORS.get(place_type, _PLACE_COLORS["hamlet"])
            material = material_named(f"GeoMap_Label_{place_type}_Material", color)

            curve = _bpy.data.curves.new(f"{obj.name}_TextCurve", "FONT")
            curve.body = name
            curve.size = size
            curve.align_x = "CENTER"
            curve.align_y = "BOTTOM"
            family = obj.get("geomap_label_font_family")
            if not family:
                family = getattr(
                    context.scene.geomap_props,
                    f"place_label_font_{place_type}",
                    "DEFAULT",
                )
            font = font_for_family(family)
            if font is not None:
                curve.font = font
            text_obj = _bpy.data.objects.new(f"{obj.name}_Text", curve)
            text_obj.location = obj.location.copy()
            text_obj.rotation_euler = (math.pi / 2, 0, 0)
            link_to_geomap_collection(context, text_obj, "Labels")
            text_obj.data.materials.append(material)
            text_obj["geomap_layer"] = "place_label_text"
            text_obj["geomap_place_type"] = place_type
            text_obj["geomap_name"] = name
            created += 1

        self.report({"INFO"}, f"Created {created} place label text object(s)")
        return {"FINISHED"}


_SINGLE_FEATURE_KINDS = frozenset(
    {"COASTLINES", "RIVERS", "ROADS", "ADMIN", "LANDUSE", "CONTOURS", "CITIES"}
)

_FEATURE_COLLECTIONS: dict[str, list[str]] = {
    "COASTLINES": ["Coastlines"],
    "RIVERS":     ["Rivers"],
    "ROADS":      ["Roads"],
    "ADMIN":      ["Admin"],
    "LANDUSE":    ["LandUse"],
    "CONTOURS":   ["Contours"],
    "CITIES":     ["POI"],
}

# Which GenerationSettings flags to set True/False for each single-feature fetch
_FEATURE_FLAGS: dict[str, dict[str, bool]] = {
    "COASTLINES": {"import_coast": True},
    "RIVERS":     {"import_rivers": True},
    "ROADS":      {"import_roads": True},
    "ADMIN":      {"import_admin": True},
    "LANDUSE":    {"import_landuse": True},
    "CITIES":     {
        "import_cities": True,
    },
}

_ALL_VECTOR_FLAGS = {
    "import_coast", "import_rivers", "import_roads", "import_admin",
    "import_buildings", "import_cities", "import_place_labels",
    "import_poi_historic", "import_poi_cultural", "import_poi_administrative",
    "import_poi_natural", "import_landuse", "import_contours",
}


class GeoMapUpdateLayerOperator(Operator):
    bl_idname = "geomap.update_layer"
    bl_label = "Generate / Update GeoMap Layer"
    bl_description = "Generate or refresh a single GeoMap layer from current settings"

    layer_kind: EnumProperty(
        items=[
            ("TERRAIN",     "Terrain",     "DEM mesh + satellite imagery together"),
            ("IMAGERY",     "Imagery",     "Satellite/map imagery only"),
            ("DEM",         "DEM",         "Elevation DEM mesh only"),
            ("COASTLINES",  "Coastlines",  "Coastline vectors"),
            ("RIVERS",      "Rivers",      "River/waterway vectors"),
            ("ROADS",       "Roads",       "Road network vectors"),
            ("ADMIN",       "Admin",       "Administrative boundaries"),
            ("LANDUSE",     "Land Use",    "Land use / natural polygons"),
            ("CONTOURS",    "Contours",    "Elevation contour lines"),
            ("CITIES",      "Cities/POI",  "Cities and points of interest"),
            ("BUILDINGS",   "Buildings",   "3D building models"),
            ("ANNOTATIONS", "Annotations", "Scale bar, north arrow, labels"),
            ("WEATHER",     "Weather",     "Current weather icons"),
            ("VECTORS",     "All Vectors", "All OSM vector layers at once"),
        ],
        default="TERRAIN",
    )

    def execute(self, context):
        tracker = ProgressTracker.get_instance()
        if tracker.is_running:
            self.report({"ERROR"}, "A GeoMap generation is already running")
            return {"CANCELLED"}

        props = context.scene.geomap_props
        apply_quality_preset_to_props(props)
        apply_output_preset_to_props(props)
        settings = GenerationSettings.from_props(props)
        prefs = ProviderSettings.from_preferences(GeoMapGenerateOperator._get_addon_preferences(context))
        configure_blender_cache_root()
        configure_blender_log_path()

        try:
            bbox = self._resolve_bbox(context)
            scene_scale = SceneScale.from_scene(
                context.scene,
                bbox,
                settings.detail_level,
                settings.dem_height_scale,
            )
            if self.layer_kind == "TERRAIN":
                self._update_terrain(context, bbox, settings, prefs, scene_scale, tracker)
            elif self.layer_kind == "IMAGERY":
                self._update_imagery(context, bbox, settings, prefs, tracker)
            elif self.layer_kind == "DEM":
                self._update_dem(context, bbox, settings, prefs, scene_scale, tracker)
            elif self.layer_kind in _SINGLE_FEATURE_KINDS:
                self._update_single_feature(
                    context, bbox, settings, prefs, scene_scale, tracker, self.layer_kind
                )
            elif self.layer_kind == "VECTORS":
                self._update_vectors(context, bbox, settings, prefs, scene_scale, tracker)
            elif self.layer_kind == "BUILDINGS":
                self._update_buildings(context, bbox, settings, prefs, scene_scale, tracker)
            elif self.layer_kind == "ANNOTATIONS":
                self._update_annotations(context, bbox, settings, scene_scale, tracker)
            elif self.layer_kind == "WEATHER":
                self._update_weather(context, bbox, settings, prefs, scene_scale, tracker)
        except Exception as error:
            if self.layer_kind == "WEATHER":
                tracker.set_weather_status("Weather failed", 1.0)
            tracker.log(f"✗ Layer update failed: {error}")
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        import bpy as _bpy
        _bpy.ops.ed.undo_push(message=f"GeoMap {self.layer_kind.capitalize()}")
        self.report({"INFO"}, f"GeoMap {self.layer_kind.lower()} layer ready")
        return {"FINISHED"}

    @staticmethod
    def _resolve_bbox(context) -> BoundingBox:
        from .kmz import current_bbox_from_context
        bbox = current_bbox_from_context(context, resolve_place=True)
        if bbox is None:
            raise RuntimeError(
                "No bounding box found. Set coordinates or generate a map first."
            )
        return bbox

    @staticmethod
    def _vector_fetch_context(prefs: ProviderSettings):
        return SimpleNamespace(
            _prefs=prefs,
            _client=OsmApiClient(),
            _provider=lambda name: getattr(prefs, name, "AUTO") if prefs else "AUTO",
        )

    def _update_terrain(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        self._update_dem(context, bbox, settings, prefs, scene_scale, tracker)
        self._update_imagery(context, bbox, settings, prefs, tracker)

    def _update_imagery(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Updating imagery layer only")
        removed = clear_geomap_child_collection("Textures")
        tracker.log(f"Removed {removed} existing imagery objects")
        _fake = SimpleNamespace(_prefs=prefs)
        tiles = SatelliteImageryClient().fetch_bbox_tiles(
            bbox,
            settings.satellite_resolution,
            settings.map_style,
            provider=prefs.basemap_provider,
            tokens=GeoMapGenerateOperator._basemap_tokens(_fake),
        )
        renderer = TerrainRenderer()
        for index, tile in enumerate(tiles, start=1):
            renderer.create_satellite_bbox(context, tile, bbox, settings, index)
        tracker.log(f"Updated imagery tiles: {len(tiles)}")

    def _update_dem(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Updating DEM layer only")
        removed = clear_geomap_child_collection("Terrain")
        tracker.log(f"Removed {removed} existing terrain objects")
        grid = DemClient().fetch_grid(
            bbox,
            settings.dem_resolution,
            provider=prefs.dem_provider,
        )
        TerrainRenderer().create_dem_mesh(
            context,
            grid,
            settings,
            scene_scale.dem_height_scale,
            scene_scale=scene_scale,
            projection_bbox=bbox,
        )
        tracker.log(f"Updated DEM grid: {grid.rows}x{grid.cols}")

    def _update_vectors(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Updating vector layers only")
        for collection_name in (
            "Coastlines",
            "Rivers",
            "Roads",
            "Admin",
            "Other",
            "POI",
            "3D Models",
        ):
            removed = clear_geomap_child_collection(collection_name)
            if removed:
                tracker.log(f"Removed {removed} objects from {collection_name}")

        op = self._vector_fetch_context(prefs)
        data = GeoMapGenerateOperator._fetch_vector_data(op, bbox, settings, tracker)
        building_ways, _landuse_ways2, vector_ways = GeoMapGenerateOperator._split_building_ways(data, settings)
        vector_layers = GeoMapGenerateOperator._split_vector_layers(
            GeoMapData(ways=vector_ways, bbox=bbox, points=data.points)
        )
        dem_sampler = None
        if settings.import_relief and settings.drape_vectors_on_dem:
            grid = DemClient().fetch_grid(
                bbox,
                settings.dem_resolution,
                provider=prefs.dem_provider,
            )
            dem_sampler = DemHeightSampler([grid], scene_scale.dem_height_scale)
            tracker.log(f"Reused DEM grid for vector draping: {grid.rows}x{grid.cols}")
        vector_renderer = VectorRenderer()
        vector_objects = vector_renderer.render_layers(
            context,
            vector_layers,
            settings,
            dem_sampler,
            scene_scale,
        )
        point_objects = vector_renderer.render_points(
            context,
            data,
            settings,
            dem_sampler,
            scene_scale,
        )
        building_objects = []
        if settings.import_buildings and building_ways:
            buildings = Osm3DModelClient().buildings_from_ways(building_ways)
            buildings = aggregate_building_parts(buildings)
            _mat_grps: dict[str, list] = {}
            for _b in buildings:
                _k = building_batch_material_key(_b.tags)
                _mat_grps.setdefault(_k, []).append(_b)
            _all_chunks: list[tuple] = []
            for _mk, _mb in _mat_grps.items():
                for _ch in GeoMapGenerateOperator._building_chunks(_mb):
                    _all_chunks.append((_ch, _mk))
            renderer = Osm3DModelRenderer()
            z_offset = scaled_map_value(settings.vector_z_offset, scene_scale)
            for chunk_index, (chunk, mat_key) in enumerate(_all_chunks, start=1):
                payload = GeoMapGenerateOperator._build_building_payloads(
                    chunk,
                    bbox,
                    settings,
                    dem_sampler,
                    scene_scale,
                    z_offset,
                    name=GeoMapGenerateOperator._building_chunk_name(chunk_index, len(_all_chunks)),
                    material_key=mat_key,
                )
                obj = renderer.commit_batch_payload(context, payload, chunk_index)
                if obj:
                    building_objects.append(obj)
        tracker.log(
            f"Updated vectors: {len(vector_objects)} layer objects, "
            f"{len(point_objects)} POI, {len(building_objects)} building mesh objects"
        )

    def _update_buildings(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Updating buildings layer only (map3d batch query: ways + relations)")
        clear_geomap_child_collection("3D Models")

        # Dedicated single-query fetch: way["building"] + relation["building"] + building:part.
        # Mirrors map3d's approach — one Overpass call with `out body geom`, no secondary
        # node-resolution round-trip, captures multipolygon relation buildings too.
        provider = getattr(prefs, "road_provider", "AUTO")
        buildings = Osm3DModelClient().fetch_buildings_in_bbox(bbox, provider=provider)
        if not buildings:
            tracker.log("No buildings found in this area")
            return
        buildings = aggregate_building_parts(buildings)
        tracker.log(
            f"Fetched {len(buildings)} buildings (ways + relations) via dedicated query"
        )

        dem_sampler = None
        if settings.import_relief and settings.drape_vectors_on_dem:
            from .dem import DemClient
            grid = DemClient().fetch_grid(bbox, settings.dem_resolution, provider=prefs.dem_provider)
            dem_sampler = DemHeightSampler([grid], scene_scale.dem_height_scale)

        mat_groups: dict[str, list] = {}
        for _b in buildings:
            _key = building_batch_material_key(_b.tags)
            mat_groups.setdefault(_key, []).append(_b)
        all_chunks: list[tuple] = []
        for _mat_key, _mat_blds in mat_groups.items():
            for _chunk in GeoMapGenerateOperator._building_chunks(_mat_blds):
                all_chunks.append((_chunk, _mat_key))

        renderer = Osm3DModelRenderer()
        z_offset = scaled_map_value(settings.vector_z_offset, scene_scale)
        for chunk_index, (chunk, mat_key) in enumerate(all_chunks, start=1):
            payload = GeoMapGenerateOperator._build_building_payloads(
                chunk, bbox, settings, dem_sampler, scene_scale, z_offset,
                name=GeoMapGenerateOperator._building_chunk_name(chunk_index, len(all_chunks)),
                material_key=mat_key,
            )
            renderer.commit_batch_payload(context, payload, chunk_index)
        tracker.log(
            f"Updated buildings: {len(buildings)} models in {len(all_chunks)} material chunks"
        )

    def _update_annotations(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Updating annotations layer only")
        clear_geomap_child_collection("Annotations")
        renderer = AnnotationRenderer()
        renderer.render(context, bbox, settings, [], scene_scale)
        tracker.log("Annotations updated")

    def _update_single_feature(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
        feature: str,
    ) -> None:
        import dataclasses

        for col in _FEATURE_COLLECTIONS.get(feature, []):
            clear_geomap_child_collection(col)

        if feature == "CONTOURS":
            self._update_contours_only(context, bbox, settings, prefs, scene_scale, tracker)
            return

        # Build settings with all vector flags off, then turn on only this feature
        off = {f: False for f in _ALL_VECTOR_FLAGS}
        on = _FEATURE_FLAGS.get(feature, {})
        if feature == "CITIES":
            on = {
                **on,
                "import_poi_historic": settings.import_poi_historic,
                "import_poi_cultural": settings.import_poi_cultural,
                "import_poi_administrative": settings.import_poi_administrative,
                "import_poi_natural": settings.import_poi_natural,
            }
        single_settings = dataclasses.replace(settings, **{**off, **on})

        op = self._vector_fetch_context(prefs)
        data = GeoMapGenerateOperator._fetch_vector_data(op, bbox, single_settings, tracker)

        dem_sampler = None
        if settings.import_relief and settings.drape_vectors_on_dem:
            grid = DemClient().fetch_grid(bbox, settings.dem_resolution, provider=prefs.dem_provider)
            dem_sampler = DemHeightSampler([grid], scene_scale.dem_height_scale)

        _bw, _lw, vector_ways = GeoMapGenerateOperator._split_building_ways(data, single_settings)
        vector_data = GeoMapData(ways=vector_ways, bbox=bbox, points=data.points)
        vector_layers = GeoMapGenerateOperator._split_vector_layers(vector_data)

        vector_renderer = VectorRenderer()
        vector_renderer.render_layers(context, vector_layers, single_settings, dem_sampler, scene_scale)
        if feature == "CITIES":
            vector_renderer.render_points(context, data, single_settings, dem_sampler, scene_scale)

        tracker.log(f"Feature layer '{feature}' updated: {len(vector_layers)} objects")

    def _update_contours_only(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        tracker.log("Regenerating contour lines from DEM")
        grid = DemClient().fetch_grid(bbox, settings.dem_resolution, provider=prefs.dem_provider)
        import dataclasses
        contour_settings = dataclasses.replace(settings, import_contours=True)
        TerrainRenderer().create_contour_lines(context, grid, contour_settings, scene_scale)
        tracker.log("Contours updated")

    def _update_weather(
        self,
        context,
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
        tracker: ProgressTracker,
    ) -> None:
        from .weather import WeatherClient
        from .weather_renderer import WeatherRenderer
        tracker.log("Fetching weather data...")
        tracker.set_weather_status("Weather: fetching forecast data...", 0.10)
        clear_geomap_child_collection("Weather")
        grid_size = getattr(settings, "weather_grid_size", 3)
        provider = getattr(settings, "weather_provider", prefs.weather_provider)
        forecast_day = getattr(settings, "weather_forecast_day", 0)
        granularity = getattr(settings, "weather_granularity", "GRID")
        samples = self._weather_samples_from_scene(granularity)
        client = WeatherClient()
        if samples:
            points = client.fetch_samples(
                samples,
                provider=provider,
                openweathermap_token=prefs.openweathermap_token,
                weatherapi_token=prefs.weatherapi_token,
                forecast_day=forecast_day,
            )
        else:
            points = client.fetch_grid(
                bbox,
                grid_size=grid_size,
                provider=provider,
                openweathermap_token=prefs.openweathermap_token,
                weatherapi_token=prefs.weatherapi_token,
                forecast_day=forecast_day,
            )
        tracker.set_weather_status("Weather: building mesh symbols...", 0.70)
        tracker.log(f"✓ Weather: {len(points)} sample points")
        WeatherRenderer().render(context, points, bbox, settings, scene_scale)
        tracker.set_weather_status("Weather ready", 1.0)
        tracker.log("Weather layer ready")

    @staticmethod
    def _weather_samples_from_scene(granularity: str) -> list[tuple[float, float]]:
        if granularity == "GRID":
            return []
        import bpy as _bpy

        samples = []
        seen = set()
        for obj in _bpy.data.objects:
            layer = obj.get("geomap_layer", "")
            is_city = layer == "place_label" or obj.get("geomap_category") == "city"
            is_locality = is_city or layer.startswith("poi_")
            if granularity in {"MAIN_CITY", "CITIES"} and not is_city:
                continue
            if granularity == "LOCALITIES" and not is_locality:
                continue
            lat = obj.get("geomap_lat")
            lon = obj.get("geomap_lon")
            if lat is None or lon is None:
                continue
            key = (round(float(lat), 5), round(float(lon), 5))
            if key in seen:
                continue
            seen.add(key)
            samples.append((float(lat), float(lon)))
            if granularity == "MAIN_CITY":
                break
        return samples


class GeoMapImportSelectedKmzOperator(Operator):
    bl_idname = "geomap.import_selected_kmz"
    bl_label = "Import Selected KMZ"
    bl_description = "Download the selected catalog KMZ/KML and integrate it into the current map"

    def execute(self, context):
        props = context.scene.geomap_props
        selected_id = getattr(props, "kmz_selection", "NONE")
        if not selected_id or selected_id == "NONE":
            _package_path, user_path = catalog_paths()
            self.report({"ERROR"}, f"No KMZ selected. Add entries to {user_path}")
            return {"CANCELLED"}

        tracker = ProgressTracker.get_instance()
        configure_blender_cache_root()
        configure_blender_log_path()
        apply_quality_preset_to_props(props)
        apply_output_preset_to_props(props)
        settings = GenerationSettings.from_props(props)
        prefs = ProviderSettings.from_preferences(
            GeoMapGenerateOperator._get_addon_preferences(context)
        )

        try:
            bbox = current_bbox_from_context(context, resolve_place=True)
            if bbox is None:
                raise RuntimeError("Set a coordinate bbox or generate a map first.")
            entry = entry_by_id(selected_id, bbox)
            if entry is None:
                raise RuntimeError("Selected KMZ is not available for the current area.")

            name = str(entry.get("name") or selected_id)
            tracker.log(f"Downloading KMZ: {name}")
            path = download_kmz(entry)
            tracker.log(f"Parsing KMZ: {path.name}")
            data = parse_kmz(path, bbox)
            if not data.ways and not data.points:
                raise RuntimeError(
                    "KMZ contains no supported Point, LineString, or Polygon in this area."
                )

            scene_scale = SceneScale.from_scene(
                context.scene,
                bbox,
                settings.detail_level,
                settings.dem_height_scale,
            )
            dem_sampler = self._dem_sampler_for_import(bbox, settings, prefs, scene_scale)
            vector_layers = GeoMapGenerateOperator._split_vector_layers(data)
            renderer = VectorRenderer()
            vector_objects = renderer.render_layers(
                context,
                vector_layers,
                settings,
                dem_sampler,
                scene_scale,
            )
            point_objects = renderer.render_points(
                context,
                data,
                settings,
                dem_sampler,
                scene_scale,
            )
        except Exception as error:
            tracker.log(f"✗ KMZ import failed: {error}")
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        tracker.log(
            f"Imported KMZ '{name}': {len(vector_objects)} layer objects, "
            f"{len(point_objects)} points"
        )
        self.report({"INFO"}, f"Imported KMZ: {name}")
        return {"FINISHED"}

    @staticmethod
    def _dem_sampler_for_import(
        bbox: BoundingBox,
        settings: GenerationSettings,
        prefs: ProviderSettings,
        scene_scale: SceneScale,
    ) -> DemHeightSampler | None:
        if not settings.import_relief or not settings.drape_vectors_on_dem:
            return None
        grid = DemClient().fetch_grid(
            bbox,
            settings.dem_resolution,
            provider=prefs.dem_provider,
        )
        return DemHeightSampler([grid], scene_scale.dem_height_scale)


class GeoMapImportSelectedPoi3DOperator(Operator):
    bl_idname = "geomap.import_selected_poi_3d"
    bl_label = "Import Selected POI 3D"
    bl_description = "Find and import an OSM 3D building near the selected GeoMap POI"

    search_radius_m: IntProperty(default=300, min=10, max=2000)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.get("geomap_layer", "").startswith("poi_"))

    def execute(self, context):
        poi = context.active_object
        if poi is None:
            self.report({"ERROR"}, "Select a GeoMap POI empty first")
            return {"CANCELLED"}

        lat = poi.get("geomap_lat")
        lon = poi.get("geomap_lon")
        osm_id = poi.get("geomap_osm_id")
        osm_type = poi.get("geomap_osm_type")
        if lat is None or lon is None:
            self.report({"ERROR"}, "Selected POI has no lat/lon metadata. Regenerate POIs.")
            return {"CANCELLED"}

        try:
            bbox, detail_level, km_per_bu = self._map_metadata()
            provider = self._overpass_provider(context)
            building = Osm3DModelClient().find_nearest_building(
                float(lat),
                float(lon),
                radius_m=self.search_radius_m,
                provider=provider,
                osm_id=int(osm_id) if osm_id else None,
                osm_type=str(osm_type) if osm_type else None,
            )
            obj = Osm3DModelRenderer().render_building(
                context,
                building,
                bbox,
                detail_level,
                km_per_bu,
                base_z=poi.location.z,
            )
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported OSM 3D building: {obj.name}")
        return {"FINISHED"}

    @staticmethod
    def _map_metadata() -> tuple[BoundingBox, str, float]:
        import bpy

        root = bpy.data.collections.get("GeoMap")
        if root is None:
            raise RuntimeError("GeoMap collection metadata not found.")
        raw_bbox = root.get("geomap_bbox")
        if not raw_bbox:
            raise RuntimeError("GeoMap bbox metadata not found.")
        values = [float(value) for value in str(raw_bbox).split(",")]
        if len(values) != 4:
            raise RuntimeError("GeoMap bbox metadata is invalid.")
        return (
            BoundingBox(values[0], values[1], values[2], values[3]),
            root.get("geomap_detail_level", "MEDIUM"),
            float(root.get("geomap_km_per_bu", 1.0)),
        )

    @staticmethod
    def _overpass_provider(context) -> str:
        prefs = GeoMapGenerateOperator._get_addon_preferences(context)
        return getattr(prefs, "road_provider", "AUTO") if prefs else "AUTO"


class GeoMapImportSelectedModelCandidateOperator(Operator):
    bl_idname = "geomap.import_selected_model_candidate"
    bl_label = "Import Selected 3D Model"
    bl_description = "Download and apply the 3D model represented by the selected GeoMap model marker"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.get("geomap_layer", "") == "model_candidate")

    def execute(self, context):
        marker = context.active_object
        if marker is None:
            self.report({"ERROR"}, "Select a GeoMap 3D model marker first")
            return {"CANCELLED"}

        configure_blender_log_path()
        tracker = ProgressTracker.get_instance()

        osm_id = marker.get("geomap_osm_id")
        osm_type = marker.get("geomap_osm_type")
        lat = marker.get("geomap_lat")
        lon = marker.get("geomap_lon")
        model_source = marker.get("geomap_model_source") or "osm"
        tracker.log(
            "3D model import requested: "
            f"marker='{marker.name}' source={model_source} "
            f"osm_type={osm_type} osm_id={osm_id} lat={lat} lon={lon} "
            f"has_geometry={marker.get('geomap_model_has_geometry')} "
            f"asset_type={marker.get('geomap_asset_type') or ''} "
            f"asset_id={marker.get('geomap_asset_id') or ''}"
        )
        if not osm_id or lat is None or lon is None:
            self.report({"ERROR"}, "Selected marker has incomplete OSM model metadata")
            tracker.log("✗ 3D model marker metadata is incomplete")
            return {"CANCELLED"}

        try:
            if marker.get("geomap_model_source") == "catalog":
                tracker.log(
                    "3D model marker is catalog asset: "
                    f"url={marker.get('geomap_asset_url') or ''} "
                    f"path={marker.get('geomap_asset_path') or ''}"
                )
                obj_count = self._import_catalog_asset(context, marker)
                marker["geomap_model_imported"] = True
                tracker.log(f"✓ Catalog 3D asset imported objects={obj_count}")
                self.report({"INFO"}, f"Imported catalog asset: {obj_count} object(s)")
                return {"FINISHED"}

            bbox, detail_level, km_per_bu = GeoMapImportSelectedPoi3DOperator._map_metadata()
            tracker.log(
                "3D model map metadata: "
                f"bbox={bbox.to_overpass()} detail={detail_level} km_per_bu={km_per_bu:.6f}"
            )
            prefs = GeoMapGenerateOperator._get_addon_preferences(context)
            provider = getattr(context.scene.geomap_props, "building_provider", "AUTO")
            if not provider or provider == "AUTO":
                provider = getattr(prefs, "road_provider", "AUTO") if prefs else "AUTO"
            tracker.log(f"3D model provider resolved: {provider}")
            building = Osm3DModelClient().fetch_model_by_id(
                int(osm_id),
                str(osm_type),
                provider=provider,
                debug_log=tracker.log,
            )
            tracker.log(
                "3D model fetched: "
                f"name='{building.name}' height={building.height_m:.2f}m "
                f"vertices={len(building.geometry)} tags={sorted(building.tags.keys())[:12]}"
            )
            obj = Osm3DModelRenderer().render_building(
                context,
                building,
                bbox,
                detail_level,
                km_per_bu,
                base_z=marker.location.z,
                use_osm_material=True,
            )
            obj["geomap_source_marker"] = marker.name
            marker["geomap_model_imported"] = True
            tracker.log(f"✓ 3D model imported object='{obj.name}'")
        except Exception as error:
            tracker.log(f"✗ 3D model import failed: {error}")
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported 3D model: {obj.name}")
        return {"FINISHED"}

    @staticmethod
    def _import_catalog_asset(context, marker) -> int:
        import bpy as _bpy

        source = marker.get("geomap_asset_url") or marker.get("geomap_asset_path")
        if not source:
            raise RuntimeError("Selected catalog marker has no asset URL/path")
        entry = {
            "id": marker.get("geomap_asset_id") or marker.name,
            "name": marker.get("geomap_model_name") or marker.name,
            "url": marker.get("geomap_asset_url") or "",
            "path": marker.get("geomap_asset_path") or "",
            "type": marker.get("geomap_asset_type") or "",
        }
        path = download_catalog_asset(entry, namespace="model_assets")
        suffix = path.suffix.lower()
        if suffix in {".kmz", ".kml"}:
            return GeoMapImportSelectedModelCandidateOperator._import_catalog_kmz(
                context,
                path,
            )
        if suffix in {".glb", ".gltf"}:
            before = set(_bpy.data.objects)
            result = _bpy.ops.import_scene.gltf(filepath=str(path))
            if "FINISHED" not in result:
                raise RuntimeError(f"Blender could not import {path.name}")
            imported = [obj for obj in _bpy.data.objects if obj not in before]
            for obj in imported:
                obj.location.x += marker.location.x
                obj.location.y += marker.location.y
                obj.location.z += marker.location.z
                obj["geomap_layer"] = "catalog_3d_model"
                obj["geomap_asset_source"] = source
                try:
                    link_to_geomap_collection(context, obj, "3D Models")
                except RuntimeError:
                    pass
            return len(imported)
        raise RuntimeError(f"Unsupported catalog asset type: {suffix or path.name}")

    @staticmethod
    def _import_catalog_kmz(context, path: Path) -> int:
        props = context.scene.geomap_props
        apply_quality_preset_to_props(props)
        apply_output_preset_to_props(props)
        settings = GenerationSettings.from_props(props)
        prefs = ProviderSettings.from_preferences(
            GeoMapGenerateOperator._get_addon_preferences(context)
        )
        bbox = current_bbox_from_context(context, resolve_place=True)
        if bbox is None:
            raise RuntimeError("Set a coordinate bbox or generate a map first.")
        data = parse_kmz(path, bbox)
        scene_scale = SceneScale.from_scene(
            context.scene,
            bbox,
            settings.detail_level,
            settings.dem_height_scale,
        )
        dem_sampler = GeoMapImportSelectedKmzOperator._dem_sampler_for_import(
            bbox,
            settings,
            prefs,
            scene_scale,
        )
        renderer = VectorRenderer()
        vector_objects = renderer.render_layers(
            context,
            GeoMapGenerateOperator._split_vector_layers(data),
            settings,
            dem_sampler,
            scene_scale,
        )
        point_objects = renderer.render_points(
            context,
            data,
            settings,
            dem_sampler,
            scene_scale,
        )
        return len(vector_objects) + len(point_objects)


class GeoMapSearchRoutePointOperator(Operator):
    bl_idname = "geomap.search_route_point"
    bl_label = "Search Route Point"
    bl_description = "Search for a place name within the map area and assign it as a route point"

    target: EnumProperty(
        items=[("START", "Start", ""), ("END", "End", "")],
        default="START",
    )

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def execute(self, context):
        from .geocoder import search_within_bbox

        props = context.scene.geomap_props
        query = props.route_search_query.strip()
        if not query:
            self.report({"ERROR"}, "Enter a search query first")
            return {"CANCELLED"}

        bbox = (
            min(props.latitude, props.latitude2),
            min(props.longitude, props.longitude2),
            max(props.latitude, props.latitude2),
            max(props.longitude, props.longitude2),
        )
        try:
            lat, lon = search_within_bbox(query, bbox)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if self.target == "START":
            props.route_lat1 = lat
            props.route_lon1 = lon
        else:
            props.route_lat2 = lat
            props.route_lon2 = lon

        self.report({"INFO"}, f"{self.target.title()} → {lat:.5f}, {lon:.5f}")
        return {"FINISHED"}


def _place_route_markers(
    context,
    start_pt: tuple,
    end_pt: tuple,
    label_start: str,
    label_end: str,
    color: tuple,
    scene_scale,
    z_offset: float = 0.0,
    prefix: str = "Route",
) -> None:
    import bpy as _bpy
    from .blender_scene import link_to_geomap_collection
    from .scene_units import scaled_map_value

    marker_size = scaled_map_value(0.08, scene_scale)
    text_size = scaled_map_value(0.06, scene_scale)
    text_lift = scaled_map_value(0.04, scene_scale)

    for pt, role, label in (
        (start_pt, "Start", label_start),
        (end_pt, "End", label_end),
    ):
        x, y, z = pt[0], pt[1], z_offset

        # Empty marker
        empty = _bpy.data.objects.new(f"GeoMap_{prefix}_{role}", None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = marker_size
        empty.location = (x, y, z)
        empty.color = (*color[:3], 1.0)
        empty["geomap_layer"] = "route_marker"
        empty["geomap_route_role"] = role.lower()
        empty["geomap_label"] = label
        link_to_geomap_collection(context, empty, "Routes")

        # Text label (only if non-empty label)
        if label:
            font_data = _bpy.data.curves.new(
                f"GeoMap_{prefix}_{role}_Label_Curve", type="FONT"
            )
            font_data.body = label
            font_data.size = text_size
            font_data.align_x = "CENTER"
            text_obj = _bpy.data.objects.new(
                f"GeoMap_{prefix}_{role}_Label", font_data
            )
            text_obj.location = (x, y, z + text_lift)
            text_obj.parent = empty
            mat = _bpy.data.materials.new(
                f"GeoMap_{prefix}_{role}_Label_Material"
            )
            mat.diffuse_color = (*color[:3], 1.0)
            font_data.materials.append(mat)
            text_obj["geomap_layer"] = "route_label"
            link_to_geomap_collection(context, text_obj, "Routes")


class GeoMapImportRouteOperator(Operator):
    bl_idname = "geomap.import_route"
    bl_label = "Import Route"
    bl_description = "Fetch a route from OSRM and place it as a curve on the map"

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def execute(self, context):
        import bpy as _bpy

        from .blender_scene import link_to_geomap_collection
        from .mesh_builder import BboxProjector
        from .models import BoundingBox
        from .route_fetcher import fetch_route
        from .scene_units import SceneScale, scaled_map_value

        props = context.scene.geomap_props

        if (props.route_lat1 == props.route_lat2
                and props.route_lon1 == props.route_lon2):
            self.report({"ERROR"}, "Start and end coordinates are identical")
            return {"CANCELLED"}

        bbox = BoundingBox(
            min(props.latitude, props.latitude2),
            min(props.longitude, props.longitude2),
            max(props.latitude, props.latitude2),
            max(props.longitude, props.longitude2),
        )
        if bbox.lat_span() == 0 or bbox.lon_span() == 0:
            self.report({"ERROR"}, "No map bbox found — generate a map first")
            return {"CANCELLED"}

        if props.route_mode == "STRAIGHT":
            waypoints = [
                (props.route_lat1, props.route_lon1),
                (props.route_lat2, props.route_lon2),
            ]
        else:
            try:
                waypoints = fetch_route(
                    props.route_lat1, props.route_lon1,
                    props.route_lat2, props.route_lon2,
                    props.route_profile,
                )
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}

            if len(waypoints) < 2:
                self.report({"ERROR"}, "Route returned fewer than 2 waypoints")
                return {"CANCELLED"}

        scene_scale = SceneScale.from_scene(
            context.scene, bbox, props.detail_level, props.dem_height_scale
        )
        projector = BboxProjector(bbox, props.detail_level)
        z = scaled_map_value(float(props.vector_z_offset) * 2.0, scene_scale)

        projected = [(*projector.project(lat, lon)[:2], z) for lat, lon in waypoints]

        curve_data = _bpy.data.curves.new("GeoMap_Route_Curve", "CURVE")
        curve_data.dimensions = "3D"
        curve_data.resolution_u = 1
        spline = curve_data.splines.new("POLY")
        spline.points.add(len(projected) - 1)
        for i, (x, y, pz) in enumerate(projected):
            spline.points[i].co = (x, y, pz, 1.0)

        obj = _bpy.data.objects.new("GeoMap_Route", curve_data)
        link_to_geomap_collection(context, obj, "Routes")

        mat = _bpy.data.materials.new("GeoMap_Route_Material")
        mat.diffuse_color = (0.9, 0.15, 0.05, 1.0)
        mat.roughness = 0.4
        curve_data.materials.append(mat)

        obj["geomap_layer"] = "route"
        obj["geomap_route_profile"] = props.route_profile
        obj["geomap_route_start"] = f"{props.route_lat1},{props.route_lon1}"
        obj["geomap_route_end"] = f"{props.route_lat2},{props.route_lon2}"

        color = (0.9, 0.15, 0.05, 1.0)
        label_s = getattr(props, "route_label_start", "")
        label_e = getattr(props, "route_label_end", "")
        z_marker = z + scaled_map_value(0.02, scene_scale)
        _place_route_markers(
            context,
            projected[0], projected[-1],
            label_s, label_e,
            color, scene_scale,
            z_offset=z_marker,
        )

        self.report({"INFO"}, f"Route imported: {len(projected)} waypoints")
        return {"FINISHED"}


class GeoMapAddRouteOperator(Operator):
    bl_idname = "geomap.add_route"
    bl_label = "Add Route"
    bl_description = "Add a new route entry to the route list"

    def execute(self, context):
        props = context.scene.geomap_props
        item = props.routes.add()
        item.name = f"Route {len(props.routes)}"
        props.route_active_index = len(props.routes) - 1
        return {"FINISHED"}


class GeoMapRemoveRouteOperator(Operator):
    bl_idname = "geomap.remove_route"
    bl_label = "Remove Route"
    bl_description = "Remove the active route from the route list"

    def execute(self, context):
        props = context.scene.geomap_props
        idx = props.route_active_index
        if 0 <= idx < len(props.routes):
            props.routes.remove(idx)
            props.route_active_index = max(0, idx - 1)
        return {"FINISHED"}


class GeoMapImportAllRoutesOperator(Operator):
    bl_idname = "geomap.import_all_routes"
    bl_label = "Import All Routes"
    bl_description = "Fetch and place all routes in the route list as curves on the map"

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        props = context.scene.geomap_props
        return col is not None and bool(col.get("geomap_bbox")) and len(props.routes) > 0

    def execute(self, context):
        import bpy as _bpy

        from .blender_scene import link_to_geomap_collection
        from .mesh_builder import BboxProjector
        from .models import BoundingBox
        from .route_fetcher import fetch_route
        from .scene_units import SceneScale, scaled_map_value

        props = context.scene.geomap_props

        bbox = BoundingBox(
            min(props.latitude, props.latitude2),
            min(props.longitude, props.longitude2),
            max(props.latitude, props.latitude2),
            max(props.longitude, props.longitude2),
        )
        if bbox.lat_span() == 0 or bbox.lon_span() == 0:
            self.report({"ERROR"}, "No map bbox found — generate a map first")
            return {"CANCELLED"}

        scene_scale = SceneScale.from_scene(
            context.scene, bbox, props.detail_level, props.dem_height_scale
        )
        projector = BboxProjector(bbox, props.detail_level)
        z = scaled_map_value(float(props.vector_z_offset) * 2.0, scene_scale)

        imported = 0
        for route_item in props.routes:
            if route_item.lat1 == route_item.lat2 and route_item.lon1 == route_item.lon2:
                continue
            if route_item.mode == "STRAIGHT":
                waypoints = [
                    (route_item.lat1, route_item.lon1),
                    (route_item.lat2, route_item.lon2),
                ]
            else:
                try:
                    waypoints = fetch_route(
                        route_item.lat1, route_item.lon1,
                        route_item.lat2, route_item.lon2,
                        route_item.profile,
                    )
                except Exception as exc:
                    self.report({"WARNING"}, f"{route_item.name}: {exc}")
                    continue
                if len(waypoints) < 2:
                    continue

            projected = [(*projector.project(lat, lon)[:2], z) for lat, lon in waypoints]
            curve_data = _bpy.data.curves.new(f"GeoMap_Route_{route_item.name}_Curve", "CURVE")
            curve_data.dimensions = "3D"
            curve_data.resolution_u = 1
            spline = curve_data.splines.new("POLY")
            spline.points.add(len(projected) - 1)
            for i, (x, y, pz) in enumerate(projected):
                spline.points[i].co = (x, y, pz, 1.0)

            obj = _bpy.data.objects.new(f"GeoMap_Route_{route_item.name}", curve_data)
            link_to_geomap_collection(context, obj, "Routes")

            r, g, b, a = route_item.color
            mat = _bpy.data.materials.new(f"GeoMap_Route_{route_item.name}_Material")
            mat.diffuse_color = (r, g, b, a)
            mat.roughness = 0.4
            curve_data.materials.append(mat)

            obj["geomap_layer"] = "route"
            obj["geomap_route_name"] = route_item.name
            obj["geomap_route_profile"] = route_item.profile
            obj["geomap_route_start"] = f"{route_item.lat1},{route_item.lon1}"
            obj["geomap_route_end"] = f"{route_item.lat2},{route_item.lon2}"

            z_marker = z + scaled_map_value(0.02, scene_scale)
            _place_route_markers(
                context,
                projected[0], projected[-1],
                route_item.label_start, route_item.label_end,
                tuple(route_item.color),
                scene_scale,
                z_offset=z_marker,
                prefix=route_item.name,
            )
            imported += 1

        self.report({"INFO"}, f"Imported {imported}/{len(props.routes)} routes")
        return {"FINISHED"}


class GeoMapOpenRoutesPanelOperator(Operator):
    bl_idname = "geomap.open_routes_popup"
    bl_label = "Open GeoMap Dashboard"
    bl_description = "Open the GeoMap dashboard route tab"

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def invoke(self, context, event):
        import bpy as _bpy
        return _bpy.ops.geomap.open_dashboard("INVOKE_DEFAULT")

    def execute(self, context):
        return {"FINISHED"}


_coord_tracking_active = False


class GeoMapShowCoordinatesOperator(Operator):
    bl_idname = "geomap.show_coordinates"
    bl_label = "Track Map Coordinates"
    bl_description = "Show lat/lon under the cursor while hovering over the map"

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def invoke(self, context, event):
        global _coord_tracking_active
        if _coord_tracking_active:
            return {"CANCELLED"}
        self._area = context.area
        context.window_manager.modal_handler_add(self)
        _coord_tracking_active = True
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _coord_tracking_active
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._finish()
            _coord_tracking_active = False
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            self._update(context, event)
        return {"PASS_THROUGH"}

    def _finish(self):
        if self._area:
            self._area.header_text_set(None)

    def _update(self, context, event):
        from bpy_extras import view3d_utils
        import bpy as _bpy

        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return

        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        result, location, _normal, _index, obj, _matrix = context.scene.ray_cast(
            context.view_layer, origin, direction
        )
        if not result or obj is None or not obj.get("geomap_layer"):
            if self._area:
                self._area.header_text_set(None)
            return

        col = _bpy.data.collections.get("GeoMap")
        if col is None:
            return
        bbox_str = col.get("geomap_bbox")
        if not bbox_str:
            return
        vals = [float(v) for v in str(bbox_str).split(",")]
        bbox = BoundingBox(vals[0], vals[1], vals[2], vals[3])
        detail = str(col.get("geomap_detail_level", "MEDIUM"))
        lat, lon = BboxProjector(bbox, detail).unproject(location.x, location.y)
        if self._area:
            self._area.header_text_set(
                f"  GeoMap  Lat: {lat:.5f}°   Lon: {lon:.5f}°"
            )


class GeoMapRegenerateFromCacheOperator(Operator):
    bl_idname = "geomap.regenerate_from_cache"
    bl_label = "Regenerate from Cache"
    bl_description = "Re-run generation using only cached data — no network requests"

    @classmethod
    def poll(cls, context):
        import bpy as _bpy
        col = _bpy.data.collections.get("GeoMap")
        return col is not None and bool(col.get("geomap_bbox"))

    def execute(self, context):
        import bpy as _bpy

        col = _bpy.data.collections.get("GeoMap")
        bbox_str = str(col.get("geomap_bbox"))
        vals = [float(v) for v in bbox_str.split(",")]
        if len(vals) != 4:
            self.report({"ERROR"}, "GeoMap bbox metadata invalid")
            return {"CANCELLED"}

        props = context.scene.geomap_props
        props.input_mode = "COORDINATES"
        props.latitude = vals[0]
        props.longitude = vals[1]
        props.latitude2 = vals[2]
        props.longitude2 = vals[3]

        set_offline_mode(True)
        return _bpy.ops.geomap.generate("INVOKE_DEFAULT")


class GeoMapSavePresetOperator(Operator):
    bl_idname = "geomap.save_preset"
    bl_label = "Save Map Preset"
    bl_description = "Save current settings as a named preset"

    preset_name: StringProperty(name="Preset Name", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.prop(self, "preset_name", text="Name")

    def execute(self, context):
        name = self.preset_name.strip()
        if not name:
            self.report({"ERROR"}, "Preset name cannot be empty")
            return {"CANCELLED"}
        props = context.scene.geomap_props
        snapshot = snapshot_from_props(props)
        save_preset(name, snapshot)
        self.report({"INFO"}, f"Preset '{name}' saved")
        return {"FINISHED"}


class GeoMapLoadPresetOperator(Operator):
    bl_idname = "geomap.load_preset"
    bl_label = "Load Map Preset"
    bl_description = "Load a saved map preset into the current settings"

    preset_name: StringProperty(default="")

    def execute(self, context):
        presets = load_presets()
        preset = next((p for p in presets if p.get("preset_name") == self.preset_name), None)
        if preset is None:
            self.report({"ERROR"}, f"Preset '{self.preset_name}' not found")
            return {"CANCELLED"}
        apply_snapshot(context.scene.geomap_props, preset)
        self.report({"INFO"}, f"Preset '{self.preset_name}' loaded")
        return {"FINISHED"}


class GeoMapDeletePresetOperator(Operator):
    bl_idname = "geomap.delete_preset"
    bl_label = "Delete Map Preset"
    bl_description = "Delete a saved map preset"

    preset_name: StringProperty(default="")

    def execute(self, context):
        delete_preset(self.preset_name)
        self.report({"INFO"}, f"Preset '{self.preset_name}' deleted")
        return {"FINISHED"}


class GeoMapUpdateAddonOperator(Operator):
    bl_idname = "geomap.update_addon"
    bl_label = "Update / Reload GeoMap Addon"
    bl_description = "Reload the GeoMap addon in Blender, optionally pulling latest git changes first"

    pull_from_git: BoolProperty(
        name="Pull latest from git first",
        default=False,
        description="Run git pull --ff-only in the addon folder before reloading",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "pull_from_git")
        layout.label(text=f"Addon folder: {_addon_root_path()}")

    def execute(self, context):
        tracker = ProgressTracker.get_instance()
        if tracker.is_running:
            self.report({"WARNING"}, "Cancel or finish generation before updating the addon")
            return {"CANCELLED"}
        try:
            from .dashboard.modal import GeoMapDashboardOperator
            if GeoMapDashboardOperator._draw_handle is not None:
                self.report({"WARNING"}, "Close the GeoMap dashboard before updating the addon")
                return {"CANCELLED"}
        except Exception:
            pass

        addon_root = _addon_root_path()
        if self.pull_from_git:
            git_dir = addon_root / ".git"
            if not git_dir.exists():
                self.report({"ERROR"}, f"Addon folder is not a git checkout: {addon_root}")
                return {"CANCELLED"}
            try:
                result = subprocess.run(
                    ["git", "-C", str(addon_root), "pull", "--ff-only"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except Exception as exc:
                self.report({"ERROR"}, f"Git update failed: {exc}")
                return {"CANCELLED"}
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "git pull failed").strip()
                self.report({"ERROR"}, msg[:240])
                return {"CANCELLED"}

        import bpy as _bpy

        addon_package = _addon_package_name()
        _bpy.app.timers.register(
            lambda: _hot_reload_addon(addon_package),
            first_interval=0.1,
        )
        action = "Update scheduled" if self.pull_from_git else "Reload scheduled"
        self.report({"INFO"}, f"{action}. GeoMap will refresh in this Blender session.")
        return {"FINISHED"}
