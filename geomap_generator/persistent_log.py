import time
from pathlib import Path

_LOG_PATH: Path | None = None


def configure_blender_log_path() -> None:
    global _LOG_PATH
    try:
        import bpy

        base = Path(bpy.utils.user_resource("CONFIG", path="geomap_generator", create=True))
    except Exception:
        base = Path.home() / ".geomap_generator"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            import tempfile

            base = Path(tempfile.gettempdir()) / "geomap_generator"
            base.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = base / "geomap_generator.log"


def write_log_entry(entry: str) -> None:
    path = _LOG_PATH
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d')} {entry}\n")
    except OSError:
        pass


def log_path() -> Path | None:
    return _LOG_PATH
