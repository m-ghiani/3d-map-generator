import math
import os
import queue
import threading
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .map_state import MapViewState

TILE_SIZE = 256
_OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_UA = "GeoMapGenerator/2.0 Blender-Addon (https://github.com/m-ghiani/3d-map-generator)"

_fetch_queue: queue.Queue = queue.Queue()
_ready_queue: queue.Queue = queue.Queue()
_failed_queue: queue.Queue = queue.Queue()
_fetching: set = set()
_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def lat_lon_to_world_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    n = 1 << zoom
    wp = n * TILE_SIZE
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    x = (lon + 180.0) / 360.0 * wp
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * wp
    return x, y


def world_px_to_lat_lon(wx: float, wy: float, zoom: int) -> tuple[float, float]:
    n = 1 << zoom
    wp = n * TILE_SIZE
    lon = max(-180.0, min(180.0, wx / wp * 360.0 - 180.0))
    t = max(0.001, min(0.999, wy / wp))
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * t))))
    return lat, lon


def screen_to_lat_lon(
    sx: float, sy: float,
    clat: float, clon: float,
    zoom: int, sw: int, sh: int,
) -> tuple[float, float]:
    cx, cy = lat_lon_to_world_px(clat, clon, zoom)
    return world_px_to_lat_lon(cx + (sx - sw / 2.0), cy - (sy - sh / 2.0), zoom)


def lat_lon_to_screen(
    lat: float, lon: float,
    clat: float, clon: float,
    zoom: int, sw: int, sh: int,
) -> tuple[float, float]:
    cx, cy = lat_lon_to_world_px(clat, clon, zoom)
    wx, wy = lat_lon_to_world_px(lat, lon, zoom)
    return sw / 2.0 + (wx - cx), sh / 2.0 - (wy - cy)


def visible_tiles(state: "MapViewState"):
    cx, cy = lat_lon_to_world_px(state.center_lat, state.center_lon, state.zoom)
    n = 1 << state.zoom
    tx0 = max(0, int((cx - state.screen_w / 2.0) / TILE_SIZE) - 1)
    ty0 = max(0, int((cy - state.screen_h / 2.0) / TILE_SIZE) - 1)
    tx1 = min(n - 1, int((cx + state.screen_w / 2.0) / TILE_SIZE) + 1)
    ty1 = min(n - 1, int((cy + state.screen_h / 2.0) / TILE_SIZE) + 1)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            yield tx, ty


def tile_screen_rect(tx: int, ty: int, state: "MapViewState") -> tuple[float, float, float, float]:
    cx, cy = lat_lon_to_world_px(state.center_lat, state.center_lon, state.zoom)
    hw, hh = state.screen_w / 2.0, state.screen_h / 2.0
    wx0, wy0 = tx * TILE_SIZE, ty * TILE_SIZE
    wx1, wy1 = wx0 + TILE_SIZE, wy0 + TILE_SIZE
    return (
        hw + (wx0 - cx),  # sx0 left
        hh - (wy1 - cy),  # sy0 bottom
        hw + (wx1 - cx),  # sx1 right
        hh - (wy0 - cy),  # sy1 top
    )


def tile_path(cache_dir: str, z: int, x: int, y: int) -> str:
    return os.path.join(cache_dir, f"osm_{z}_{x}_{y}.png")


def start_worker(cache_dir: str) -> None:
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker_fn, args=(cache_dir,), daemon=True)
    _worker_thread.start()


def request_tile(z: int, x: int, y: int) -> None:
    key = (z, x, y)
    with _lock:
        if key in _fetching:
            return
        _fetching.add(key)
    _fetch_queue.put(key)


def pop_ready() -> list[tuple[int, int, int, str]]:
    result = []
    while True:
        try:
            result.append(_ready_queue.get_nowait())
        except queue.Empty:
            break
    return result


def pop_failed() -> list[tuple[int, int, int]]:
    result = []
    while True:
        try:
            result.append(_failed_queue.get_nowait())
        except queue.Empty:
            break
    return result


def _worker_fn(cache_dir: str) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    while True:
        try:
            z, x, y = _fetch_queue.get(timeout=2.0)
        except queue.Empty:
            continue
        path = tile_path(cache_dir, z, x, y)
        if os.path.exists(path):
            _ready_queue.put((z, x, y, path))
        else:
            try:
                url = _OSM_URL.format(z=z, x=x, y=y)
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
                with open(path, "wb") as f:
                    f.write(data)
                _ready_queue.put((z, x, y, path))
            except Exception:
                _failed_queue.put((z, x, y))
        with _lock:
            _fetching.discard((z, x, y))
