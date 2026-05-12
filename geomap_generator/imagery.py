import tempfile
import urllib.parse
import urllib.request
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .download_cache import cached_bytes
from .exceptions import CancelledGeneration, ProviderError
from .models import BoundingBox, SatelliteTile

_ARCGIS_BASE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services"
_BASEMAP_PROVIDERS = {"AUTO", "ARCGIS"}
MAP_SERVICE_PATHS: dict[str, str] = {
    "SATELLITE": "World_Imagery/MapServer",
    "STREETS": "World_Street_Map/MapServer",
    "TOPO": "World_Topo_Map/MapServer",
    "POLITICAL": "NatGeo_World_Map/MapServer",
    "LIGHT_GRAY": "Canvas/World_Light_Gray_Base/MapServer",
    "DARK_GRAY": "Canvas/World_Dark_Gray_Base/MapServer",
}
_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"
_MAX_RESOLUTION = 8192
_MAX_TILE_LAT_SPAN = 4.0
_MAX_TILE_LON_SPAN = 4.0
_MAX_EXPORT_RESOLUTION = 2048
_WEB_MERCATOR_RADIUS_M = 6378137.0
_WEB_MERCATOR_MAX_LAT = 85.05112878
_MIN_RESOLUTION = 256
_IMAGERY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_TILE_WORKERS = 4


def _web_mercator_meters(lat: float, lon: float) -> tuple[float, float]:
    clamped_lat = max(-_WEB_MERCATOR_MAX_LAT, min(_WEB_MERCATOR_MAX_LAT, lat))
    lon_rad = math.radians(lon)
    lat_rad = math.radians(clamped_lat)
    x = _WEB_MERCATOR_RADIUS_M * lon_rad
    y = _WEB_MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    return x, y


def _image_size_for_bbox(
    bbox: BoundingBox, max_resolution: int
) -> tuple[int, int, tuple[float, float, float, float]]:
    min_x, min_y = _web_mercator_meters(bbox.min_lat, bbox.min_lon)
    max_x, max_y = _web_mercator_meters(bbox.max_lat, bbox.max_lon)
    width_m = max(abs(max_x - min_x), 1.0)
    height_m = max(abs(max_y - min_y), 1.0)
    max_size = max(_MIN_RESOLUTION, min(_MAX_RESOLUTION, int(max_resolution)))

    if width_m >= height_m:
        width_px = max_size
        height_px = max(_MIN_RESOLUTION, round(max_size * height_m / width_m))
    else:
        height_px = max_size
        width_px = max(_MIN_RESOLUTION, round(max_size * width_m / height_m))

    return width_px, height_px, (min_x, min_y, max_x, max_y)


class SatelliteImageryClient:
    """Downloads a satellite image for a geographic bounding box."""

    def tiles_for_bbox(self, bbox: BoundingBox) -> list[BoundingBox]:
        return bbox.split(_MAX_TILE_LAT_SPAN, _MAX_TILE_LON_SPAN)

    def fetch_bbox_tiles(
        self,
        bbox: BoundingBox,
        resolution: int,
        map_style: str = "SATELLITE",
        provider: str = "AUTO",
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[SatelliteTile]:
        if provider not in _BASEMAP_PROVIDERS:
            raise ProviderError(f"Unsupported map imagery provider: {provider}")
        self._raise_if_cancelled(should_cancel)
        tile_bboxes = self._resolution_tiles_for_bbox(bbox, resolution)
        worker_count = min(_MAX_TILE_WORKERS, max(1, len(tile_bboxes)))

        def fetch_tile(item: tuple[int, BoundingBox]) -> SatelliteTile:
            self._raise_if_cancelled(should_cancel)
            index, tile_bbox = item
            return SatelliteTile(
                bbox=tile_bbox,
                image_path=self.fetch_bbox_image(
                    tile_bbox,
                    min(resolution, _MAX_EXPORT_RESOLUTION),
                    map_style=map_style,
                    provider=provider,
                    should_cancel=should_cancel,
                    suffix=f"_{index:03d}",
                ),
            )

        if worker_count == 1:
            return [fetch_tile((1, tile_bboxes[0]))]

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(fetch_tile, enumerate(tile_bboxes, start=1)))

    def _resolution_tiles_for_bbox(self, bbox: BoundingBox, resolution: int) -> list[BoundingBox]:
        base_tiles = self.tiles_for_bbox(bbox)
        requested = max(_MIN_RESOLUTION, min(_MAX_RESOLUTION, int(resolution)))
        splits_per_axis = max(1, math.ceil(requested / _MAX_EXPORT_RESOLUTION))
        if splits_per_axis == 1:
            return base_tiles

        tiles: list[BoundingBox] = []
        for tile in base_tiles:
            lat_step = tile.lat_span() / splits_per_axis
            lon_step = tile.lon_span() / splits_per_axis
            for row in range(splits_per_axis):
                min_lat = tile.min_lat + row * lat_step
                max_lat = tile.max_lat if row == splits_per_axis - 1 else min_lat + lat_step
                for col in range(splits_per_axis):
                    min_lon = tile.min_lon + col * lon_step
                    max_lon = tile.max_lon if col == splits_per_axis - 1 else min_lon + lon_step
                    tiles.append(BoundingBox(min_lat, min_lon, max_lat, max_lon))
        return tiles

    def fetch_bbox_image(
        self,
        bbox: BoundingBox,
        resolution: int,
        map_style: str = "SATELLITE",
        provider: str = "AUTO",
        should_cancel: Callable[[], bool] | None = None,
        suffix: str = "",
    ) -> Path:
        if provider not in _BASEMAP_PROVIDERS:
            raise ProviderError(f"Unsupported map imagery provider: {provider}")
        self._raise_if_cancelled(should_cancel)
        width_px, height_px, mercator_bbox = _image_size_for_bbox(
            bbox, min(resolution, _MAX_EXPORT_RESOLUTION)
        )
        min_x, min_y, max_x, max_y = mercator_bbox
        params = urllib.parse.urlencode(
            {
                "bbox": f"{min_x},{min_y},{max_x},{max_y}",
                "bboxSR": "3857",
                "imageSR": "3857",
                "size": f"{width_px},{height_px}",
                "format": "png",
                "transparent": "false",
                "adjustAspectRatio": "false",
                "f": "image",
            }
        )
        service_path = MAP_SERVICE_PATHS.get(map_style, MAP_SERVICE_PATHS["SATELLITE"])
        url = f"{_ARCGIS_BASE_URL}/{service_path}/export?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

        def fetch() -> bytes:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()

        image_bytes = cached_bytes("imagery", url, _IMAGERY_CACHE_TTL_SECONDS, fetch)
        self._raise_if_cancelled(should_cancel)

        if not image_bytes:
            raise ProviderError("Satellite imagery service returned an empty image.")

        output = Path(tempfile.gettempdir()) / f"geomap_satellite_bbox{suffix}.png"
        output.write_bytes(image_bytes)
        return output

    @staticmethod
    def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise CancelledGeneration("Cancelled")
