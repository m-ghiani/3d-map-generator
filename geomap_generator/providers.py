from typing import Callable, Protocol

from .models import BoundingBox, DemGrid, GeoMapData, SatelliteTile


class VectorDataProvider(Protocol):
    def resolve_bbox(self, query: str) -> BoundingBox:
        ...

    def fetch_features(
        self,
        bbox: BoundingBox,
        *,
        coastlines: bool = False,
        rivers: bool = False,
        roads: bool = False,
        admin_level: str | None = None,
        cities: bool = False,
        poi_historic: bool = False,
        poi_cultural: bool = False,
        poi_administrative: bool = False,
        poi_natural: bool = False,
        provider: str = "AUTO",
        progress: Callable[[str, float | None], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GeoMapData:
        ...


class DemProvider(Protocol):
    def fetch_grid(
        self,
        bbox: BoundingBox,
        resolution: str,
        *,
        progress: Callable[[str, float | None], None] | None = None,
        progress_start: float = 0.75,
        progress_end: float = 0.82,
        provider: str = "AUTO",
        should_cancel: Callable[[], bool] | None = None,
    ) -> DemGrid:
        ...


class ImageryProvider(Protocol):
    def fetch_bbox_tiles(
        self,
        bbox: BoundingBox,
        resolution: int,
        map_style: str = "SATELLITE",
        provider: str = "AUTO",
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[SatelliteTile]:
        ...
