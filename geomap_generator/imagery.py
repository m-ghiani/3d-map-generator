import tempfile
import urllib.parse
import urllib.request
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .download_cache import cached_bytes, read_index, write_index
from .exceptions import CancelledGeneration, ProviderError
from .models import BoundingBox, SatelliteTile

_ARCGIS_BASE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services"
_BASEMAP_PROVIDERS = {
    "AUTO",
    "ARCGIS",
    "MAPTILER",
    "MAPBOX",
    "GOOGLE",
    "NASA_GIBS",
    "SENTINEL_HUB",
    "PLANET",
    "MAXAR",
    "AIRBUS",
}
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
_PROVIDER_MAX_EXPORT_RESOLUTION = {
    "MAPBOX": 1280,
    "GOOGLE": 640,
}
_WEB_MERCATOR_RADIUS_M = 6378137.0
_WEB_MERCATOR_MAX_LAT = 85.05112878
_MIN_RESOLUTION = 256
_IMAGERY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_TILE_WORKERS = 4


def _bbox_to_entry(bbox: BoundingBox) -> dict[str, float]:
    return {
        "min_lat": bbox.min_lat,
        "min_lon": bbox.min_lon,
        "max_lat": bbox.max_lat,
        "max_lon": bbox.max_lon,
    }


def _bbox_from_entry(entry: dict) -> BoundingBox | None:
    raw = entry.get("bbox")
    if not isinstance(raw, dict):
        return None
    try:
        return BoundingBox(
            float(raw["min_lat"]),
            float(raw["min_lon"]),
            float(raw["max_lat"]),
            float(raw["max_lon"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _same_bbox(a: BoundingBox, b: BoundingBox, tolerance: float = 1e-7) -> bool:
    return (
        abs(a.min_lat - b.min_lat) <= tolerance
        and abs(a.min_lon - b.min_lon) <= tolerance
        and abs(a.max_lat - b.max_lat) <= tolerance
        and abs(a.max_lon - b.max_lon) <= tolerance
    )


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


def _required_token(tokens: dict[str, str], name: str, provider: str) -> str:
    token = tokens.get(name) or ""
    if not token:
        raise ProviderError(f"{provider} requires an API token in addon preferences.")
    return token


def _static_zoom_for_bbox(bbox: BoundingBox) -> int:
    span = max(abs(bbox.lat_span()), abs(bbox.lon_span()), 1e-9)
    zoom = round(math.log2(360.0 / span))
    return max(1, min(20, zoom))


def _provider_max_export_resolution(provider: str) -> int:
    provider = "ARCGIS" if provider == "AUTO" else provider
    return _PROVIDER_MAX_EXPORT_RESOLUTION.get(provider, _MAX_EXPORT_RESOLUTION)


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
        tokens: dict[str, str] | None = None,
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
                    min(
                        resolution,
                        _provider_max_export_resolution(provider),
                    ),
                    map_style=map_style,
                    provider=provider,
                    tokens=tokens,
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
        tokens: dict[str, str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        suffix: str = "",
    ) -> Path:
        if provider not in _BASEMAP_PROVIDERS:
            raise ProviderError(f"Unsupported map imagery provider: {provider}")
        self._raise_if_cancelled(should_cancel)
        cached_path = self._find_cached_image(bbox, resolution, map_style, provider)
        if cached_path:
            return cached_path

        export_resolution = min(resolution, _provider_max_export_resolution(provider))
        width_px, height_px, mercator_bbox = _image_size_for_bbox(bbox, export_resolution)
        min_x, min_y, max_x, max_y = mercator_bbox
        url = self._build_imagery_url(
            bbox,
            width_px,
            height_px,
            mercator_bbox,
            map_style,
            provider,
            tokens or {},
        )
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

        def fetch() -> bytes:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()

        image_bytes = cached_bytes("imagery", url, _IMAGERY_CACHE_TTL_SECONDS, fetch)
        self._raise_if_cancelled(should_cancel)

        if not image_bytes:
            raise ProviderError("Satellite imagery service returned an empty image.")

        output = self._image_output_path(bbox, resolution, map_style, provider, suffix)
        output.write_bytes(image_bytes)
        self._store_image_index(bbox, resolution, map_style, provider, output)
        return output

    @staticmethod
    def _build_imagery_url(
        bbox: BoundingBox,
        width_px: int,
        height_px: int,
        mercator_bbox: tuple[float, float, float, float],
        map_style: str,
        provider: str,
        tokens: dict[str, str],
    ) -> str:
        provider = "ARCGIS" if provider == "AUTO" else provider
        if provider == "ARCGIS":
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
            return f"{_ARCGIS_BASE_URL}/{service_path}/export?{params}"
        if provider == "MAPBOX":
            token = _required_token(tokens, "mapbox", provider)
            params = urllib.parse.urlencode({"access_token": token})
            bbox_expr = urllib.parse.quote(
                f"[{bbox.min_lon},{bbox.min_lat},{bbox.max_lon},{bbox.max_lat}]",
                safe="[],.-",
            )
            return (
                "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
                f"{bbox_expr}"
                f"/{width_px}x{height_px}?{params}"
            )
        if provider == "MAPTILER":
            token = _required_token(tokens, "maptiler", provider)
            center_lat = (bbox.min_lat + bbox.max_lat) / 2
            center_lon = (bbox.min_lon + bbox.max_lon) / 2
            zoom = _static_zoom_for_bbox(bbox)
            params = urllib.parse.urlencode({"key": token})
            return (
                "https://api.maptiler.com/maps/satellite/static/"
                f"{center_lon},{center_lat},{zoom}/{width_px}x{height_px}.png?{params}"
            )
        if provider == "GOOGLE":
            token = _required_token(tokens, "google", provider)
            center_lat = (bbox.min_lat + bbox.max_lat) / 2
            center_lon = (bbox.min_lon + bbox.max_lon) / 2
            zoom = _static_zoom_for_bbox(bbox)
            params = urllib.parse.urlencode(
                {
                    "center": f"{center_lat},{center_lon}",
                    "zoom": str(zoom),
                    "size": f"{min(width_px, 640)}x{min(height_px, 640)}",
                    "maptype": "satellite",
                    "format": "png",
                    "key": token,
                }
            )
            return f"https://maps.googleapis.com/maps/api/staticmap?{params}"
        if provider == "NASA_GIBS":
            params = urllib.parse.urlencode(
                {
                    "SERVICE": "WMS",
                    "REQUEST": "GetMap",
                    "VERSION": "1.3.0",
                    "LAYERS": "MODIS_Terra_CorrectedReflectance_TrueColor",
                    "CRS": "EPSG:4326",
                    "BBOX": f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}",
                    "WIDTH": str(width_px),
                    "HEIGHT": str(height_px),
                    "FORMAT": "image/png",
                    "TRANSPARENT": "false",
                }
            )
            return f"https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?{params}"
        if provider in {"SENTINEL_HUB", "PLANET", "MAXAR", "AIRBUS"}:
            _required_token(tokens, provider.lower(), provider)
            raise ProviderError(
                f"{provider} requires account-specific imagery endpoint configuration "
                "before it can be used as a basemap provider."
            )
        raise ProviderError(f"Unsupported map imagery provider: {provider}")

    @staticmethod
    def _image_output_path(
        bbox: BoundingBox,
        resolution: int,
        map_style: str,
        provider: str,
        suffix: str,
    ) -> Path:
        key = (
            f"{provider}|{map_style}|{min(resolution, _MAX_EXPORT_RESOLUTION)}|"
            f"{bbox.min_lat:.7f},{bbox.min_lon:.7f},"
            f"{bbox.max_lat:.7f},{bbox.max_lon:.7f}"
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / f"geomap_satellite_bbox_{digest}{suffix}.png"

    def _find_cached_image(
        self,
        bbox: BoundingBox,
        resolution: int,
        map_style: str,
        provider: str,
    ) -> Path | None:
        best: tuple[float, Path] | None = None
        requested = min(resolution, _MAX_EXPORT_RESOLUTION)
        for entry in read_index("imagery_geo"):
            if entry.get("provider") != provider or entry.get("map_style") != map_style:
                continue
            if int(entry.get("resolution", 0)) < requested:
                continue
            cached_bbox = _bbox_from_entry(entry)
            if not cached_bbox or not _same_bbox(cached_bbox, bbox):
                continue
            path = Path(str(entry.get("path", "")))
            if not path.exists():
                continue
            area = max(cached_bbox.lat_span() * cached_bbox.lon_span(), 0.0)
            if best is None or area < best[0]:
                best = (area, path)
        return best[1] if best else None

    @staticmethod
    def _store_image_index(
        bbox: BoundingBox,
        resolution: int,
        map_style: str,
        provider: str,
        path: Path,
    ) -> None:
        key = (
            f"{provider}|{map_style}|{min(resolution, _MAX_EXPORT_RESOLUTION)}|"
            f"{bbox.min_lat:.7f},{bbox.min_lon:.7f},{bbox.max_lat:.7f},{bbox.max_lon:.7f}"
        )
        entries = [
            entry
            for entry in read_index("imagery_geo")
            if entry.get("key") != key and entry.get("path") != str(path)
        ]
        entries.insert(
            0,
            {
                "key": key,
                "provider": provider,
                "map_style": map_style,
                "resolution": min(resolution, _MAX_EXPORT_RESOLUTION),
                "path": str(path),
                "bbox": _bbox_to_entry(bbox),
            },
        )
        write_index("imagery_geo", entries[:300])

    @staticmethod
    def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise CancelledGeneration("Cancelled")
