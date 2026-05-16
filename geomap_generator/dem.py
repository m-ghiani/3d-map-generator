import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .download_cache import cached_json, cache_file_path, read_index, write_index
from .exceptions import CancelledGeneration, ProviderError
from .models import BoundingBox, DemGrid

OPEN_METEO_URL = "https://api.open-meteo.com/v1/elevation"
OPEN_TOPO_DATA_URL = "https://api.opentopodata.org/v1"
_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"
_BATCH_SIZE = 50
_GRID_SIZES = {
    "LOW": 8,
    "MEDIUM": 12,
    "HIGH": 16,
    "DEM_LOW": 16,
    "DEM_MEDIUM": 32,
    "DEM_HIGH": 48,
    "DEM_ULTRA": 64,
}
_BATCH_PAUSE_SECONDS = {
    "LOW": 0.8,
    "MEDIUM": 0.8,
    "HIGH": 0.8,
    "DEM_LOW": 0.8,
    "DEM_MEDIUM": 1.2,
    "DEM_HIGH": 1.8,
    "DEM_ULTRA": 2.5,
}
_MAX_RETRIES = 4
_RETRY_DELAYS = (5.0, 15.0, 35.0, 60.0)
_RETRYABLE_HTTP_CODES = {429, 502, 503, 504}
_DEM_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_AUTO_PROVIDERS = (
    "OPEN_METEO",
    "OPEN_TOPO_SRTM90",
    "OPEN_TOPO_SRTM30",
    "OPEN_TOPO_ASTER30",
)
_OPEN_TOPO_DATASETS = {
    "OPEN_TOPO_SRTM90": "srtm90m",
    "OPEN_TOPO_SRTM30": "srtm30m",
    "OPEN_TOPO_ASTER30": "aster30m",
}


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


class DemClient:
    """Fetches elevation samples for a bbox from Open-Meteo Elevation API."""

    def fetch_grid(
        self,
        bbox: BoundingBox,
        detail_level: str,
        progress: Callable[[str, float], None] | None = None,
        progress_start: float = 0.75,
        progress_end: float = 0.82,
        provider: str = "OPEN_METEO",
        should_cancel: Callable[[], bool] | None = None,
    ) -> DemGrid:
        if detail_level not in _GRID_SIZES:
            raise RuntimeError(f"Unsupported DEM resolution: {detail_level}")

        size = _GRID_SIZES[detail_level]
        cached = self._find_cached_grid(bbox, size, provider)
        if cached:
            if progress:
                progress("DEM cache reused from covering bbox", progress_end)
            return cached

        batch_pause = _BATCH_PAUSE_SECONDS.get(detail_level, 1.2)
        samples = self._sample_points(bbox, size, size)
        elevations: list[float] = []
        progress_span = max(0.0, progress_end - progress_start)
        batch_count = max(1, (len(samples) + _BATCH_SIZE - 1) // _BATCH_SIZE)

        for offset in range(0, len(samples), _BATCH_SIZE):
            self._raise_if_cancelled(should_cancel)
            batch_index = (offset // _BATCH_SIZE) + 1
            batch = samples[offset : offset + _BATCH_SIZE]
            if progress:
                done = offset / max(1, len(samples))
                progress(
                    f"Loading DEM elevation samples {batch_index}/{batch_count}...",
                    progress_start + done * progress_span,
                )
            elevations.extend(self._fetch_elevations(batch, provider))
            self._raise_if_cancelled(should_cancel)
            if offset + _BATCH_SIZE < len(samples):
                time.sleep(batch_pause)

        if len(elevations) != len(samples):
            raise ProviderError("DEM service returned an unexpected number of elevations.")

        if progress:
            progress("Validating DEM elevation samples...", progress_end - 0.005)
        grid = DemGrid(bbox=bbox, rows=size, cols=size, elevations=elevations)
        if progress:
            progress(f"Caching DEM grid {size}x{size}...", progress_end - 0.002)
        self._store_grid(grid, provider)
        if progress:
            progress("DEM elevation data ready", progress_end)
        return grid

    def _find_cached_grid(self, bbox: BoundingBox, size: int, provider: str) -> DemGrid | None:
        exact = self._find_exact_cached_grid(bbox, size, provider)
        if exact:
            return exact

        best: tuple[float, DemGrid] | None = None
        for entry in read_index("dem_grid"):
            entry_provider = entry.get("provider")
            if provider != "AUTO" and entry_provider != provider:
                continue
            if int(entry.get("rows", 0)) < size or int(entry.get("cols", 0)) < size:
                continue
            cached_bbox = _bbox_from_entry(entry)
            if not cached_bbox or not cached_bbox.contains(bbox):
                continue
            path = entry.get("path")
            if not path:
                continue
            grid = self._load_grid(Path(path), cached_bbox)
            if grid is None:
                continue
            area = max(cached_bbox.lat_span() * cached_bbox.lon_span(), 0.0)
            if best is None or area < best[0]:
                best = (area, self._resample_grid(grid, bbox, size))
        return best[1] if best else None

    def _find_exact_cached_grid(
        self, bbox: BoundingBox, size: int, provider: str
    ) -> DemGrid | None:
        providers = _AUTO_PROVIDERS if provider == "AUTO" else (provider,)
        for provider_name in (provider, *providers):
            key = self._grid_cache_key(bbox, size, provider_name)
            path = cache_file_path("dem_grid", key, "json")
            if path is None or not path.exists():
                continue
            grid = self._load_grid(path, bbox)
            if grid and grid.rows >= size and grid.cols >= size:
                return grid if grid.rows == size and grid.cols == size else self._resample_grid(
                    grid, bbox, size
                )
        return None

    @staticmethod
    def _load_grid(path: Path, fallback_bbox: BoundingBox) -> DemGrid | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            bbox = _bbox_from_entry(data) or fallback_bbox
            return DemGrid(
                bbox=bbox,
                rows=int(data["rows"]),
                cols=int(data["cols"]),
                elevations=[float(value) for value in data["elevations"]],
            )
        except Exception:
            return None

    def _store_grid(self, grid: DemGrid, provider: str) -> None:
        key = self._grid_cache_key(grid.bbox, grid.rows, provider)
        path = cache_file_path("dem_grid", key, "json")
        if path is None:
            return
        payload = {
            "bbox": _bbox_to_entry(grid.bbox),
            "rows": grid.rows,
            "cols": grid.cols,
            "elevations": grid.elevations,
        }
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            return

        entries = [
            entry
            for entry in read_index("dem_grid")
            if entry.get("key") != key and entry.get("path") != str(path)
        ]
        entries.insert(
            0,
            {
                "key": key,
                "provider": provider,
                "path": str(path),
                "bbox": _bbox_to_entry(grid.bbox),
                "rows": grid.rows,
                "cols": grid.cols,
                "created": time.time(),
            },
        )
        write_index("dem_grid", entries[:200])

    @staticmethod
    def _grid_cache_key(bbox: BoundingBox, size: int, provider: str) -> str:
        return (
            f"{provider}|{size}x{size}|"
            f"{bbox.min_lat:.7f},{bbox.min_lon:.7f},"
            f"{bbox.max_lat:.7f},{bbox.max_lon:.7f}"
        )

    def _resample_grid(self, source: DemGrid, bbox: BoundingBox, size: int) -> DemGrid:
        elevations = []
        for row in range(size):
            lat_t = row / (size - 1) if size > 1 else 0.0
            lat = bbox.min_lat + bbox.lat_span() * lat_t
            for col in range(size):
                lon_t = col / (size - 1) if size > 1 else 0.0
                lon = bbox.min_lon + bbox.lon_span() * lon_t
                elevations.append(self._sample_grid(source, lat, lon))
        return DemGrid(bbox=bbox, rows=size, cols=size, elevations=elevations)

    @staticmethod
    def _sample_grid(source: DemGrid, lat: float, lon: float) -> float:
        lat_span = source.bbox.lat_span() or 1.0
        lon_span = source.bbox.lon_span() or 1.0
        row_pos = (lat - source.bbox.min_lat) / lat_span * (source.rows - 1)
        col_pos = (lon - source.bbox.min_lon) / lon_span * (source.cols - 1)
        row_pos = max(0.0, min(source.rows - 1, row_pos))
        col_pos = max(0.0, min(source.cols - 1, col_pos))
        row0 = int(row_pos)
        col0 = int(col_pos)
        row1 = min(source.rows - 1, row0 + 1)
        col1 = min(source.cols - 1, col0 + 1)
        row_t = row_pos - row0
        col_t = col_pos - col0
        e00 = source.elevation_at(row0, col0)
        e01 = source.elevation_at(row0, col1)
        e10 = source.elevation_at(row1, col0)
        e11 = source.elevation_at(row1, col1)
        top = e00 + (e01 - e00) * col_t
        bottom = e10 + (e11 - e10) * col_t
        return top + (bottom - top) * row_t

    @staticmethod
    def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise CancelledGeneration("Cancelled")

    @staticmethod
    def _sample_points(
        bbox: BoundingBox, rows: int, cols: int
    ) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for row in range(rows):
            lat_t = row / (rows - 1) if rows > 1 else 0.0
            lat = bbox.min_lat + (bbox.max_lat - bbox.min_lat) * lat_t
            for col in range(cols):
                lon_t = col / (cols - 1) if cols > 1 else 0.0
                lon = bbox.min_lon + (bbox.max_lon - bbox.min_lon) * lon_t
                points.append((lat, lon))
        return points

    def _fetch_elevations(
        self, points: list[tuple[float, float]], provider: str = "OPEN_METEO"
    ) -> list[float]:
        if provider == "AUTO":
            return self._fetch_elevations_auto(points)
        if provider == "OPEN_METEO":
            return self._fetch_open_meteo(points)
        if provider in _OPEN_TOPO_DATASETS:
            return self._fetch_open_topo_data(points, _OPEN_TOPO_DATASETS[provider])
        raise RuntimeError(f"Unsupported DEM provider: {provider}")

    def _fetch_elevations_auto(self, points: list[tuple[float, float]]) -> list[float]:
        last_error: RuntimeError | None = None
        for provider in _AUTO_PROVIDERS:
            try:
                return self._fetch_elevations(points, provider)
            except RuntimeError as e:
                last_error = e
        raise RuntimeError(f"All DEM providers failed. Last error: {last_error}")

    def _fetch_open_meteo(self, points: list[tuple[float, float]]) -> list[float]:
        params = urllib.parse.urlencode(
            {
                "latitude": ",".join(f"{lat:.6f}" for lat, _lon in points),
                "longitude": ",".join(f"{lon:.6f}" for _lat, lon in points),
            }
        )
        req = urllib.request.Request(
            f"{OPEN_METEO_URL}?{params}", headers={"User-Agent": _USER_AGENT}
        )
        raw = self._open_json_with_retries(req)

        values = raw.get("elevation")
        if not isinstance(values, list):
            raise RuntimeError("DEM service returned no elevation list.")
        return [float(value or 0.0) for value in values]

    def _fetch_open_topo_data(
        self, points: list[tuple[float, float]], dataset: str
    ) -> list[float]:
        payload = urllib.parse.urlencode(
            {
                "locations": "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in points),
                "interpolation": "bilinear",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{OPEN_TOPO_DATA_URL}/{dataset}",
            data=payload,
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        raw = self._open_json_with_retries(req)
        if raw.get("status") != "OK":
            raise RuntimeError(
                f"Open Topo Data returned an error: {raw.get('error') or raw.get('status')}"
            )
        results = raw.get("results")
        if not isinstance(results, list):
            raise RuntimeError("Open Topo Data returned no results list.")
        return [float((item or {}).get("elevation") or 0.0) for item in results]

    def _open_json_with_retries(self, req: urllib.request.Request) -> dict:
        cache_key = self._request_cache_key(req)
        return cached_json(
            "dem",
            cache_key,
            _DEM_CACHE_TTL_SECONDS,
            lambda: self._open_json_uncached(req),
        )

    @staticmethod
    def _request_cache_key(req: urllib.request.Request) -> str:
        body = req.data.decode("utf-8") if req.data else ""
        return f"{req.full_url}|{body}"

    def _open_json_uncached(self, req: urllib.request.Request) -> dict:
        for attempt in range(_MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code not in _RETRYABLE_HTTP_CODES:
                    raise RuntimeError(f"DEM service HTTP {e.code}: {e.reason}") from e
                if attempt == _MAX_RETRIES - 1:
                    raise RuntimeError(
                        f"DEM service temporarily unavailable after retries "
                        f"(HTTP {e.code}: {e.reason}). Try a lower DEM resolution or retry later."
                    ) from e
                time.sleep(_RETRY_DELAYS[attempt])
            except urllib.error.URLError as e:
                raise RuntimeError(f"DEM service network error: {e.reason}") from e

        raise RuntimeError("DEM service request failed after retries.")
