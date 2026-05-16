import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import DemGrid, GeoMapData, SatelliteTile
from .serialization import (
    dem_grid_from_dict,
    geomap_data_from_dict,
    satellite_tile_from_dict,
)
from .settings import GenerationSettings, ProviderSettings


class ExternalGenerationResult:
    def __init__(
        self,
        osm_data: GeoMapData,
        satellite_tiles: list[SatelliteTile],
        dem_grid: DemGrid | None,
        dem_tiles: list[tuple[SatelliteTile, DemGrid]],
        logs: list[str],
    ) -> None:
        self.osm_data = osm_data
        self.satellite_tiles = satellite_tiles
        self.dem_grid = dem_grid
        self.dem_tiles = dem_tiles
        self.logs = logs


class GeoMapServiceClient:
    def __init__(
        self,
        base_url: str,
        *,
        auto_start: bool = True,
        port: int = 8765,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auto_start = auto_start
        self.port = port
        self._process: subprocess.Popen | None = None

    def generate(
        self,
        settings: GenerationSettings,
        providers: ProviderSettings,
    ) -> ExternalGenerationResult:
        if self.auto_start:
            self.ensure_running()
        payload = {
            "settings": asdict(settings),
            "providers": asdict(providers),
        }
        data = self._post_json("/generate", payload, timeout=900)
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error") or "External GeoMap service failed."))

        dem_grid = data.get("dem_grid")
        return ExternalGenerationResult(
            osm_data=geomap_data_from_dict(data["osm_data"]),
            satellite_tiles=[
                satellite_tile_from_dict(tile) for tile in data.get("satellite_tiles", [])
            ],
            dem_grid=dem_grid_from_dict(dem_grid) if dem_grid else None,
            dem_tiles=[
                (
                    satellite_tile_from_dict(item["tile"]),
                    dem_grid_from_dict(item["grid"]),
                )
                for item in data.get("dem_tiles", [])
            ],
            logs=[str(item) for item in data.get("logs", [])],
        )

    def ensure_running(self) -> None:
        if self._healthcheck():
            return

        script = Path(__file__).resolve().parents[1] / "geomap_service.py"
        if not script.exists():
            raise RuntimeError(f"GeoMap service script not found: {script}")

        self._process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(script.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if self._healthcheck():
                return
            time.sleep(0.2)
        raise RuntimeError("GeoMap service did not start within 10 seconds.")

    def _healthcheck(self) -> bool:
        try:
            data = self._get_json("/health", timeout=1.0)
        except Exception:
            return False
        return bool(data.get("ok"))

    def _get_json(self, path: str, timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                message = data.get("error") or raw
            except json.JSONDecodeError:
                message = raw
            raise RuntimeError(f"GeoMap service HTTP {error.code}: {message}") from error
