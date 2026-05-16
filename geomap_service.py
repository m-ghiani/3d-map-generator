#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from geomap_generator.dem import DemClient
from geomap_generator.download_cache import configure_blender_cache_root
from geomap_generator.exceptions import CancelledGeneration
from geomap_generator.imagery import SatelliteImageryClient
from geomap_generator.models import BoundingBox, GeoMapData, OsmPoint, OsmWay
from geomap_generator.overpass import OsmApiClient
from geomap_generator.serialization import (
    dem_grid_to_dict,
    geomap_data_to_dict,
    satellite_tile_to_dict,
)
from geomap_generator.settings import GenerationSettings, ProviderSettings


def _merge_data(target_ways: list[OsmWay], target_points: list[OsmPoint], data: GeoMapData) -> None:
    seen = {
        (
            way.id,
            tuple((round(node.lat, 7), round(node.lon, 7)) for node in way.geometry),
        )
        for way in target_ways
    }
    for way in data.ways:
        key = (
            way.id,
            tuple((round(node.lat, 7), round(node.lon, 7)) for node in way.geometry),
        )
        if key not in seen:
            seen.add(key)
            target_ways.append(way)
    target_points.extend(data.points)


class GeoMapGenerationService:
    def __init__(self) -> None:
        configure_blender_cache_root()
        self.client = OsmApiClient()

    def generate(self, settings: GenerationSettings, providers: ProviderSettings) -> dict[str, Any]:
        logs: list[str] = []
        bbox = self._bbox(settings, logs)
        osm_data = self._fetch_vector_data(bbox, settings, providers, logs)

        satellite_tiles = []
        dem_grid = None
        dem_tiles = []

        if settings.import_satellite:
            logs.append(f"Fetching {settings.map_style.lower()} map tiles")
            satellite_tiles = SatelliteImageryClient().fetch_bbox_tiles(
                bbox,
                settings.satellite_resolution,
                settings.map_style,
                provider=providers.basemap_provider,
                tokens={
                    "maptiler": providers.maptiler_token,
                    "mapbox": providers.mapbox_token,
                    "google": providers.google_token,
                    "sentinel_hub": providers.sentinel_hub_token,
                    "planet": providers.planet_token,
                    "maxar": providers.maxar_token,
                    "airbus": providers.airbus_token,
                },
            )
            logs.append(f"Satellite tiles: {len(satellite_tiles)}")

        if settings.import_relief and satellite_tiles:
            dem_client = DemClient()
            for index, tile in enumerate(satellite_tiles, start=1):
                logs.append(f"Fetching DEM tile {index}/{len(satellite_tiles)}")
                grid = dem_client.fetch_grid(
                    tile.bbox,
                    settings.dem_resolution,
                    provider=providers.dem_provider,
                )
                dem_tiles.append((tile, grid))
            logs.append(f"DEM tiles: {len(dem_tiles)}")
        elif settings.import_relief:
            logs.append("Fetching DEM elevation grid")
            dem_grid = DemClient().fetch_grid(
                bbox,
                settings.dem_resolution,
                provider=providers.dem_provider,
            )
            logs.append(f"DEM grid: {dem_grid.rows}x{dem_grid.cols}")

        return {
            "ok": True,
            "logs": logs,
            "osm_data": geomap_data_to_dict(osm_data),
            "satellite_tiles": [satellite_tile_to_dict(tile) for tile in satellite_tiles],
            "dem_grid": dem_grid_to_dict(dem_grid) if dem_grid else None,
            "dem_tiles": [
                {"tile": satellite_tile_to_dict(tile), "grid": dem_grid_to_dict(grid)}
                for tile, grid in dem_tiles
            ],
        }

    def _bbox(self, settings: GenerationSettings, logs: list[str]) -> BoundingBox:
        if settings.input_mode == "COUNTRY":
            logs.append(f"Resolving bbox for {settings.country_region}")
            bbox = self.client.resolve_bbox(settings.country_region)
        else:
            bbox = BoundingBox.from_corners(
                settings.latitude,
                settings.longitude,
                settings.latitude2,
                settings.longitude2,
            )
        logs.append(
            f"Bbox: {bbox.min_lat:.3f},{bbox.min_lon:.3f} -> "
            f"{bbox.max_lat:.3f},{bbox.max_lon:.3f}"
        )
        return bbox

    def _fetch_vector_data(
        self,
        bbox: BoundingBox,
        settings: GenerationSettings,
        providers: ProviderSettings,
        logs: list[str],
    ) -> GeoMapData:
        requests = self._feature_requests(settings, providers)
        if not requests:
            return GeoMapData(ways=[], bbox=bbox)

        grouped: dict[str, dict[str, Any]] = {}
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
            for key in (
                "coastlines",
                "rivers",
                "roads",
                "buildings",
                "cities",
                "poi_historic",
                "poi_cultural",
                "poi_administrative",
                "poi_natural",
            ):
                grouped[provider][key] |= request[key]
            if request["admin_level"]:
                grouped[provider]["admin_level"] = request["admin_level"]

        ways: list[OsmWay] = []
        points: list[OsmPoint] = []
        for index, (provider, request) in enumerate(grouped.items(), start=1):
            logs.append(
                f"OSM provider {index}/{len(grouped)}: {provider} "
                f"for {', '.join(request['labels'])}"
            )
            data = self.client.fetch_features(
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
            )
            _merge_data(ways, points, data)
        logs.append(f"OSM data: {len(ways)} ways, {len(points)} points")
        return GeoMapData(ways=ways, bbox=bbox, points=points)

    @staticmethod
    def _feature_requests(settings: GenerationSettings, providers: ProviderSettings) -> list[dict]:
        specs = [
            ("coastlines", settings.import_coast, providers.coast_provider, {"coastlines": True}),
            ("rivers", settings.import_rivers, providers.river_provider, {"rivers": True}),
            ("roads", settings.import_roads, providers.road_provider, {"roads": True}),
            (
                f"admin level {settings.admin_level}",
                settings.import_admin,
                providers.admin_provider,
                {"admin_level": settings.admin_level},
            ),
            ("3D buildings", settings.import_buildings, providers.road_provider, {"buildings": True}),
            ("cities", settings.import_cities, providers.road_provider, {"cities": True}),
            (
                "historic POI",
                settings.import_poi_historic,
                providers.road_provider,
                {"poi_historic": True},
            ),
            (
                "cultural POI",
                settings.import_poi_cultural,
                providers.road_provider,
                {"poi_cultural": True},
            ),
            (
                "administrative POI",
                settings.import_poi_administrative,
                providers.admin_provider,
                {"poi_administrative": True},
            ),
            (
                "natural POI",
                settings.import_poi_natural,
                providers.road_provider,
                {"poi_natural": True},
            ),
        ]
        requests = []
        for label, enabled, provider, values in specs:
            if not enabled:
                continue
            request = {
                "label": label,
                "provider": provider,
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
            request.update(values)
            requests.append(request)
        return requests


class Handler(BaseHTTPRequestHandler):
    service = GeoMapGenerationService()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json({"ok": True})
            return
        self._write_json({"ok": False, "error": "Not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/generate":
            self._write_json({"ok": False, "error": "Not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}
            settings = GenerationSettings(**payload["settings"])
            providers = ProviderSettings(**payload.get("providers", {}))
            self._write_json(self.service.generate(settings, providers))
        except CancelledGeneration as error:
            self._write_json({"ok": False, "error": str(error)}, status=499)
        except Exception as error:
            self._write_json({"ok": False, "error": str(error)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="GeoMap Generator local data service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"GeoMap service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
