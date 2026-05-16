import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from .annotation_renderer import AnnotationRenderer
from .blender_scene import clear_geomap_child_collection
from .dem import DemClient
from .download_cache import cache_stats, clear_cache, configure_blender_cache_root
from .exceptions import (
    CancelledGeneration,
    GeoMapError,
    MeshBuildError,
    ProviderError,
    ValidationError,
)
from .geometry_payload import build_building_batch_payload, build_vector_payload
from .imagery import SatelliteImageryClient
from .layer_style import vector_layer_identity
from .mesh_builder import DemHeightSampler
from .models import BoundingBox, DemGrid, GeoMapData, SatelliteTile
from .overpass import OsmApiClient
from .osm_3d import Osm3DModelClient, Osm3DModelRenderer
from .persistent_log import configure_blender_log_path
from .progress import ProgressTracker
from .search_cache import (
    add_search,
    apply_snapshot,
    clear_history,
    configure_blender_history_path,
    load_history,
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
                    "provider": self._provider("road_provider"),
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
        if props.import_cities:
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

            tracker.set_status("Splitting data into mesh tasks...", 0.86)
            phase_at = time.time()
            dem_sampler = self._create_dem_sampler(scene_scale, props)
            building_ways, vector_ways = self._split_building_ways(osm_data, props)
            vector_data = GeoMapData(ways=vector_ways, bbox=osm_data.bbox, points=osm_data.points)
            vector_layers = self._split_vector_layers(vector_data)
            points = self._unique_points(osm_data.points)
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
            if not vector_layers and not building_ways and not osm_data.points and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
                )
            if not vector_layers and not building_ways and not osm_data.points:
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
                building_chunks = self._building_chunks(buildings)
                total_steps = self._mesh_progress_total_steps(
                    satellite_count=len(self._satellite_tiles)
                    if props.import_satellite and not props.import_relief
                    else 0,
                    dem_tile_count=len(self._dem_tiles) if props.import_relief else 0,
                    has_single_dem=bool(props.import_relief and self._dem_grid and not self._dem_tiles),
                    vector_layer_count=len(vector_layers),
                    point_count=len(points),
                    building_chunk_count=len(building_chunks),
                )
                tracker.log(
                    f"Prepared {len(buildings)} 3D building footprints "
                    f"as {len(building_chunks)} merged mesh chunk(s) "
                    f"in {time.time() - phase_at:.2f}s"
                )
                tracker.set_status(
                    f"Prepared {len(buildings)} building footprints for merged mesh",
                    self._mesh_progress(completed_steps, total_steps),
                )
                yield

                renderer = Osm3DModelRenderer()
                z_offset = scaled_map_value(props.vector_z_offset, scene_scale)
                for chunk_index, batch in enumerate(building_chunks, start=1):
                    self._raise_if_cancelled(tracker)
                    tracker.set_status(
                        f"Preparing merged 3D buildings mesh {chunk_index}/{len(building_chunks)}",
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
                        name=self._building_chunk_name(chunk_index, len(building_chunks)),
                    )
                    while not future.done():
                        self._raise_if_cancelled(tracker)
                        yield
                    payload = future.result()
                    completed_steps += 1
                    tracker.log(
                        f"Merged 3D buildings payload {chunk_index}/{len(building_chunks)} "
                        f"prepared in {time.time() - phase_at:.2f}s"
                    )
                    tracker.set_status(
                        f"Prepared merged 3D buildings mesh {chunk_index}/{len(building_chunks)}",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    yield
                    tracker.set_status(
                        f"Committing merged 3D buildings mesh {chunk_index}/{len(building_chunks)}",
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
                        f"Merged 3D buildings mesh {chunk_index}/{len(building_chunks)} committed "
                        f"with {payload.building_count} simplified buildings "
                        f"in {time.time() - commit_at:.2f}s"
                    )
                    completed_steps += 1
                    tracker.set_status(
                        f"Committed merged 3D buildings mesh "
                        f"{chunk_index}/{len(building_chunks)}",
                        self._mesh_progress(completed_steps, total_steps),
                    )
                    yield

            if not vector_objects and not point_objects and not building_objects and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
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
            completed_steps += 1
            tracker.set_status(
                "Annotations complete",
                self._mesh_progress(completed_steps, total_steps),
            )
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
    def _build_building_payloads(
        buildings,
        bbox: BoundingBox,
        props,
        dem_sampler: DemHeightSampler | None,
        scene_scale: SceneScale,
        z_offset: float,
        name: str,
    ):
        def base_z_for_building(building):
            terrain_z = (
                dem_sampler.sample_z(building.center_lat, building.center_lon)
                if dem_sampler and props.drape_vectors_on_dem
                else 0.0
            )
            return terrain_z + z_offset

        return build_building_batch_payload(
            buildings,
            bbox,
            props.detail_level,
            scene_scale.km_per_bu,
            base_z_for_building,
            name=name,
            max_vertices_per_building=12,
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
    def _split_building_ways(osm_data: GeoMapData, props) -> tuple[list, list]:
        if not props.import_buildings:
            return [], osm_data.ways
        building_ways = []
        vector_ways = []
        for way in osm_data.ways:
            if way.tags.get("building") or way.tags.get("building:part"):
                building_ways.append(way)
            else:
                vector_ways.append(way)
        return building_ways, vector_ways

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
        for layer_key, object_name in order:
            ways = grouped[(layer_key, object_name)]
            layers.append((layer_key, object_name, GeoMapData(ways=ways, bbox=osm_data.bbox)))
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

        setattr(prefs, self.encrypted_prop, encrypt_token(raw))
        setattr(prefs, self.token_prop, "")
        self.report({"INFO"}, "Token stored encrypted")
        return {"FINISHED"}


class GeoMapUpdateLayerOperator(Operator):
    bl_idname = "geomap.update_layer"
    bl_label = "Update GeoMap Layer"
    bl_description = "Regenerate only one GeoMap output layer using the existing map bbox and cache"

    layer_kind: EnumProperty(
        items=[
            ("IMAGERY", "Imagery", "Update satellite/map imagery only"),
            ("DEM", "DEM", "Update terrain DEM only"),
            ("VECTORS", "Vectors", "Update OSM vector, POI, and 3D building layers only"),
        ],
        default="IMAGERY",
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
            bbox = self._current_bbox()
            scene_scale = SceneScale.from_scene(
                context.scene,
                bbox,
                settings.detail_level,
                settings.dem_height_scale,
            )
            if self.layer_kind == "IMAGERY":
                self._update_imagery(context, bbox, settings, prefs, tracker)
            elif self.layer_kind == "DEM":
                self._update_dem(context, bbox, settings, prefs, scene_scale, tracker)
            else:
                self._update_vectors(context, bbox, settings, prefs, scene_scale, tracker)
        except Exception as error:
            tracker.log(f"✗ Layer update failed: {error}")
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Updated GeoMap {self.layer_kind.lower()} layer")
        return {"FINISHED"}

    @staticmethod
    def _current_bbox() -> BoundingBox:
        import bpy

        root = bpy.data.collections.get("GeoMap")
        if root is None:
            raise RuntimeError("Generate a map first. GeoMap collection metadata not found.")
        raw_bbox = root.get("geomap_bbox")
        if not raw_bbox:
            raise RuntimeError("Generate a map first. GeoMap bbox metadata not found.")
        values = [float(value) for value in str(raw_bbox).split(",")]
        if len(values) != 4:
            raise RuntimeError("GeoMap bbox metadata is invalid.")
        return BoundingBox(values[0], values[1], values[2], values[3])

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
        tiles = SatelliteImageryClient().fetch_bbox_tiles(
            bbox,
            settings.satellite_resolution,
            settings.map_style,
            provider=prefs.basemap_provider,
            tokens=GeoMapGenerateOperator()._basemap_tokens(prefs),
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

        op = GeoMapGenerateOperator()
        op._prefs = prefs
        op._client = OsmApiClient()
        data = op._fetch_vector_data(bbox, settings, tracker)
        building_ways, vector_ways = op._split_building_ways(data, settings)
        vector_layers = op._split_vector_layers(
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
            chunks = GeoMapGenerateOperator._building_chunks(buildings)
            renderer = Osm3DModelRenderer()
            z_offset = scaled_map_value(settings.vector_z_offset, scene_scale)
            for chunk_index, chunk in enumerate(chunks, start=1):
                payload = GeoMapGenerateOperator._build_building_payloads(
                    chunk,
                    bbox,
                    settings,
                    dem_sampler,
                    scene_scale,
                    z_offset,
                    name=GeoMapGenerateOperator._building_chunk_name(chunk_index, len(chunks)),
                )
                obj = renderer.commit_batch_payload(context, payload, chunk_index)
                if obj:
                    building_objects.append(obj)
        tracker.log(
            f"Updated vectors: {len(vector_objects)} layer objects, "
            f"{len(point_objects)} POI, {len(building_objects)} building mesh objects"
        )


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
