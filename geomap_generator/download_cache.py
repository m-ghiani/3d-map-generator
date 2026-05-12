import hashlib
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

_STATS = {"hits": 0, "misses": 0, "stale_hits": 0, "writes": 0}
_CACHE_ROOT: Path | None = None


def configure_blender_cache_root() -> None:
    global _CACHE_ROOT
    try:
        import bpy

        _CACHE_ROOT = Path(
            bpy.utils.user_resource(
                "CACHE",
                path="geomap_generator/downloads",
                create=True,
            )
        )
    except Exception:
        _CACHE_ROOT = _fallback_cache_root()


def _fallback_cache_root() -> Path:
    path = Path.home() / ".geomap_generator" / "downloads"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "geomap_generator" / "downloads"
        path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_dir(namespace: str) -> Path | None:
    if _CACHE_ROOT is not None:
        path = _CACHE_ROOT / namespace
        path.mkdir(parents=True, exist_ok=True)
        return path
    if threading.current_thread() is not threading.main_thread():
        path = _fallback_cache_root() / namespace
        path.mkdir(parents=True, exist_ok=True)
        return path
    try:
        import bpy

        return Path(
            bpy.utils.user_resource(
                "CACHE",
                path=f"geomap_generator/downloads/{namespace}",
                create=True,
            )
        )
    except Exception:
        return None


def cache_root() -> Path | None:
    if _CACHE_ROOT is not None:
        return _CACHE_ROOT
    if threading.current_thread() is not threading.main_thread():
        return _fallback_cache_root()
    try:
        import bpy

        return Path(
            bpy.utils.user_resource(
                "CACHE",
                path="geomap_generator/downloads",
                create=True,
            )
        )
    except Exception:
        return None


def _cache_path(namespace: str, key: str, suffix: str) -> Path | None:
    base = _cache_dir(namespace)
    if base is None:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return base / f"{digest}.{suffix}"


def _is_fresh(path: Path, ttl_seconds: int) -> bool:
    return time.time() - path.stat().st_mtime <= ttl_seconds


def cached_bytes(
    namespace: str,
    key: str,
    ttl_seconds: int,
    fetch: Callable[[], bytes],
) -> bytes:
    path = _cache_path(namespace, key, "bin")
    if path and path.exists() and _is_fresh(path, ttl_seconds):
        data = path.read_bytes()
        if data:
            _STATS["hits"] += 1
            return data

    _STATS["misses"] += 1
    try:
        data = fetch()
    except Exception:
        if path and path.exists():
            data = path.read_bytes()
            if data:
                _STATS["stale_hits"] += 1
                return data
        raise
    if path and data:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            _STATS["writes"] += 1
        except OSError:
            pass
    return data


def cached_json(
    namespace: str,
    key: str,
    ttl_seconds: int,
    fetch: Callable[[], dict],
) -> dict:
    path = _cache_path(namespace, key, "json")
    if path and path.exists() and _is_fresh(path, ttl_seconds):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _STATS["hits"] += 1
                return data
        except Exception:
            pass

    _STATS["misses"] += 1
    try:
        data = fetch()
    except Exception:
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    _STATS["stale_hits"] += 1
                    return data
            except Exception:
                pass
        raise
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            _STATS["writes"] += 1
        except OSError:
            pass
    return data


def cache_stats() -> dict[str, int]:
    root = cache_root()
    files = 0
    bytes_total = 0
    if root and root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                files += 1
                bytes_total += path.stat().st_size
    return {**_STATS, "files": files, "bytes": bytes_total}


def clear_cache(namespace: str | None = None) -> None:
    root = cache_root()
    if not root or not root.exists():
        return
    target = root / namespace if namespace else root
    if target.exists():
        shutil.rmtree(target)
