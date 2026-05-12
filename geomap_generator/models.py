from dataclasses import dataclass, field
import math
from pathlib import Path


@dataclass
class OsmNode:
    id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class OsmWay:
    id: int
    geometry: list[OsmNode]
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class OsmPoint:
    id: int
    lat: float
    lon: float
    name: str
    category: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @classmethod
    def from_corners(
        cls, lat_a: float, lon_a: float, lat_b: float, lon_b: float
    ) -> "BoundingBox":
        return cls(
            min_lat=min(lat_a, lat_b),
            min_lon=min(lon_a, lon_b),
            max_lat=max(lat_a, lat_b),
            max_lon=max(lon_a, lon_b),
        )

    def to_overpass(self) -> str:
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

    def center_lon(self) -> float:
        return (self.min_lon + self.max_lon) / 2

    def lat_span(self) -> float:
        return self.max_lat - self.min_lat

    def lon_span(self) -> float:
        return self.max_lon - self.min_lon

    def split(self, max_lat_span: float, max_lon_span: float) -> list["BoundingBox"]:
        rows = max(1, math.ceil(self.lat_span() / max_lat_span))
        cols = max(1, math.ceil(self.lon_span() / max_lon_span))
        lat_step = self.lat_span() / rows
        lon_step = self.lon_span() / cols
        tiles: list[BoundingBox] = []
        for row in range(rows):
            min_lat = self.min_lat + row * lat_step
            max_lat = self.max_lat if row == rows - 1 else min_lat + lat_step
            for col in range(cols):
                min_lon = self.min_lon + col * lon_step
                max_lon = self.max_lon if col == cols - 1 else min_lon + lon_step
                tiles.append(BoundingBox(min_lat, min_lon, max_lat, max_lon))
        return tiles


@dataclass
class GeoMapData:
    ways: list[OsmWay]
    bbox: BoundingBox
    points: list[OsmPoint] = field(default_factory=list)


@dataclass
class SatelliteTile:
    bbox: BoundingBox
    image_path: Path


@dataclass
class DemGrid:
    bbox: BoundingBox
    rows: int
    cols: int
    elevations: list[float]

    def elevation_at(self, row: int, col: int) -> float:
        return self.elevations[row * self.cols + col]

    def min_elevation(self) -> float:
        return min(self.elevations) if self.elevations else 0.0

    def max_elevation(self) -> float:
        return max(self.elevations) if self.elevations else 0.0
