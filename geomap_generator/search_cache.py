import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_MAX_HISTORY = 10
_HISTORY_PATH: Path | None = None


def configure_blender_history_path() -> None:
    global _HISTORY_PATH
    try:
        import bpy

        base = Path(bpy.utils.user_resource("CONFIG", path="geomap_generator", create=True))
    except Exception:
        base = _fallback_config_dir()
    _HISTORY_PATH = base / "search_history.json"


def _fallback_config_dir() -> Path:
    base = Path.home() / ".geomap_generator"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir()) / "geomap_generator"
        base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_path() -> Path:
    if _HISTORY_PATH is not None:
        return _HISTORY_PATH
    if threading.current_thread() is not threading.main_thread():
        return _fallback_config_dir() / "search_history.json"
    try:
        import bpy

        base = Path(bpy.utils.user_resource("CONFIG", path="geomap_generator", create=True))
    except Exception:
        base = _fallback_config_dir()
    return base / "search_history.json"


def load_history() -> list[dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_history(history: list[dict[str, Any]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history[:_MAX_HISTORY], indent=2), encoding="utf-8")


def clear_history() -> None:
    save_history([])


def snapshot_from_props(props) -> dict[str, Any]:
    def value(name: str, default: Any) -> Any:
        return getattr(props, name, default)

    if props.input_mode == "COUNTRY":
        label = props.country_region.strip()
    else:
        label = (
            f"{props.latitude:.5f},{props.longitude:.5f} → "
            f"{props.latitude2:.5f},{props.longitude2:.5f}"
        )

    return {
        "label": label,
        "timestamp": int(time.time()),
        "input_mode": props.input_mode,
        "country_region": props.country_region,
        "latitude": props.latitude,
        "longitude": props.longitude,
        "latitude2": props.latitude2,
        "longitude2": props.longitude2,
        "quality_preset": props.quality_preset,
        "output_preset": value("output_preset", "BLENDER_VIEW"),
        "detail_level": props.detail_level,
        "import_coast": props.import_coast,
        "import_rivers": props.import_rivers,
        "import_relief": props.import_relief,
        "dem_resolution": props.dem_resolution,
        "dem_height_scale": props.dem_height_scale,
        "drape_vectors_on_dem": value("drape_vectors_on_dem", True),
        "vector_z_offset": value("vector_z_offset", 0.03),
        "road_width": value("road_width", 0.045),
        "road_geometry": value("road_geometry", "CURVE"),
        "river_width": value("river_width", 0.06),
        "river_geometry": value("river_geometry", "CURVE"),
        "boundary_width": value("boundary_width", 0.025),
        "coast_width": value("coast_width", 0.035),
        "print_base_height": value("print_base_height", 0.25),
        "add_legend": value("add_legend", True),
        "add_scale_bar": value("add_scale_bar", True),
        "import_roads": props.import_roads,
        "import_admin": props.import_admin,
        "import_cities": props.import_cities,
        "import_poi_historic": props.import_poi_historic,
        "import_poi_cultural": props.import_poi_cultural,
        "import_poi_administrative": props.import_poi_administrative,
        "import_poi_natural": props.import_poi_natural,
        "admin_level": props.admin_level,
        "import_satellite": props.import_satellite,
        "map_style": props.map_style,
        "satellite_resolution": props.satellite_resolution,
    }


def add_search(snapshot: dict[str, Any]) -> None:
    if not snapshot.get("label"):
        return
    history = load_history()
    deduped = [
        item
        for item in history
        if not (
            item.get("input_mode") == snapshot.get("input_mode")
            and item.get("country_region") == snapshot.get("country_region")
            and item.get("latitude") == snapshot.get("latitude")
            and item.get("longitude") == snapshot.get("longitude")
            and item.get("latitude2") == snapshot.get("latitude2")
            and item.get("longitude2") == snapshot.get("longitude2")
        )
    ]
    save_history([snapshot, *deduped])


def apply_snapshot(props, snapshot: dict[str, Any]) -> None:
    for key, value in snapshot.items():
        if key in {"label", "timestamp"}:
            continue
        if hasattr(props, key):
            setattr(props, key, value)
