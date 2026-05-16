import threading

from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from .annotation_renderer import AnnotationRenderer
from .dem import DemClient
from .download_cache import cache_stats, clear_cache, configure_blender_cache_root
from .exceptions import (
    CancelledGeneration,
    GeoMapError,
    MeshBuildError,
    ProviderError,
    ValidationError,
)
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
from .scene_units import SceneScale
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

        thread = threading.Thread(target=self._generate_threaded, daemon=True)
        thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        tracker = ProgressTracker.get_instance()

        if event.type == "TIMER":
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    for region in area.regions:
                        if region.type == "UI":
                            region.tag_redraw()

            mesh_data = tracker.pop_mesh_data()
            if mesh_data is not None:
                osm_data, props = mesh_data
                self._create_mesh(context, osm_data, props, tracker)

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
            tracker.set_status("Preparing mesh creation...", 0.84)
            tracker.set_mesh_data((osm_data, self._props))
            tracker.log("✓ Mesh data ready")
            tracker.set_status("Waiting for Blender main thread...", 0.85)

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
                    "admin_level": props.admin_level,
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

    def _create_mesh(self, context, osm_data: GeoMapData, props, tracker: ProgressTracker) -> None:
        try:
            assert_main_thread()
            self._raise_if_cancelled(tracker)
            scene_scale = SceneScale.from_scene(
                context.scene, osm_data.bbox, props.detail_level, props.dem_height_scale
            )
            terrain_renderer = TerrainRenderer()
            if props.import_satellite and not props.import_relief:
                tracker.set_status("Creating satellite bounding box...", 0.86)
                for index, tile in enumerate(self._satellite_tiles, start=1):
                    terrain_renderer.create_satellite_bbox(
                        context, tile, osm_data.bbox, props, index
                    )

            if props.import_relief and self._dem_tiles:
                tracker.set_status("Creating textured DEM terrain tiles...", 0.88)
                dem_height_scale = scene_scale.dem_height_scale
                dem_min = min(grid.min_elevation() for _tile, grid in self._dem_tiles)
                for index, (tile, dem_grid) in enumerate(self._dem_tiles, start=1):
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
            elif props.import_relief and self._dem_grid:
                status = (
                    "Creating textured DEM terrain mesh..."
                    if props.import_satellite
                    else "Creating DEM terrain mesh..."
                )
                tracker.set_status(status, 0.88)
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

            tracker.set_status("Projecting coordinates...", 0.90)
            dem_sampler = self._create_dem_sampler(scene_scale, props)
            vector_layers = self._split_vector_layers(osm_data)
            vector_objects = []
            if not vector_layers and not osm_data.points and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
                )
            if not vector_layers and not osm_data.points:
                tracker.log("No vector ways returned; non-vector layers created")
                tracker.result = True
                tracker.set_status("Completed", 1.0)
                return None

            tracker.set_status("Creating separated vector objects...", 0.93)
            vector_renderer = VectorRenderer()
            vector_objects = vector_renderer.render_layers(
                context, vector_layers, props, dem_sampler, scene_scale
            )
            point_objects = vector_renderer.render_points(
                context, osm_data, props, dem_sampler, scene_scale
            )

            if not vector_objects and not point_objects and not (
                props.import_satellite or props.import_relief
            ):
                raise MeshBuildError(
                    "Mesh builder returned no vertices — ways may have no usable geometry."
                )
            if not vector_objects and not point_objects:
                tracker.log("Selected vector layers produced no usable geometry")
                tracker.result = True
                tracker.set_status("Completed", 1.0)
                return None

            tracker.set_status("Annotating map scale...", 0.98)
            # Real-world scale annotation
            tracker.log(
                f"Scene: {scene_scale.unit_system} "
                f"(scale_length={scene_scale.scale_length:.4f} {scene_scale.unit_label}/BU)"
            )
            tracker.log(
                f"Map extent: {scene_scale.extent_lat_km:.0f} km × "
                f"{scene_scale.extent_lon_km:.0f} km"
            )
            tracker.log(f"Scale: 1 BU ≈ {scene_scale.km_per_bu:.2f} km")

            for obj in [*vector_objects, *point_objects]:
                obj["geomap_km_per_bu"] = round(scene_scale.km_per_bu, 4)
                obj["geomap_extent_km"] = (
                    f"{scene_scale.extent_lat_km:.1f} × {scene_scale.extent_lon_km:.1f}"
                )
                obj["geomap_scene_unit"] = scene_scale.unit_system

            AnnotationRenderer().render(
                context, osm_data.bbox, props, vector_objects, scene_scale
            )

            tracker.log(
                f"Vector layers: {len(vector_objects)} objects, POI empties: {len(point_objects)}"
            )
            tracker.result = True
            tracker.set_status("Completed", 1.0)
        except Exception as e:
            tracker.error = f"Mesh creation failed: {e}"
            tracker.log(f"✗ Mesh error: {e}")
        finally:
            tracker.is_running = False
        return None  # prevents timer from re-registering

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
