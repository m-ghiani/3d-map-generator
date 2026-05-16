from pathlib import Path
from typing import Any

from .models import BoundingBox, DemGrid, GeoMapData, OsmNode, OsmPoint, OsmWay, SatelliteTile


def bbox_to_dict(bbox: BoundingBox) -> dict[str, float]:
    return {
        "min_lat": bbox.min_lat,
        "min_lon": bbox.min_lon,
        "max_lat": bbox.max_lat,
        "max_lon": bbox.max_lon,
    }


def bbox_from_dict(data: dict[str, Any]) -> BoundingBox:
    return BoundingBox(
        float(data["min_lat"]),
        float(data["min_lon"]),
        float(data["max_lat"]),
        float(data["max_lon"]),
    )


def geomap_data_to_dict(data: GeoMapData) -> dict[str, Any]:
    return {
        "bbox": bbox_to_dict(data.bbox),
        "ways": [
            {
                "id": way.id,
                "tags": way.tags,
                "geometry": [
                    {"id": node.id, "lat": node.lat, "lon": node.lon, "tags": node.tags}
                    for node in way.geometry
                ],
            }
            for way in data.ways
        ],
        "points": [
            {
                "id": point.id,
                "lat": point.lat,
                "lon": point.lon,
                "name": point.name,
                "category": point.category,
                "osm_type": point.osm_type,
                "tags": point.tags,
            }
            for point in data.points
        ],
    }


def geomap_data_from_dict(data: dict[str, Any]) -> GeoMapData:
    return GeoMapData(
        bbox=bbox_from_dict(data["bbox"]),
        ways=[
            OsmWay(
                id=int(way.get("id", 0)),
                tags=dict(way.get("tags") or {}),
                geometry=[
                    OsmNode(
                        id=int(node.get("id", 0)),
                        lat=float(node["lat"]),
                        lon=float(node["lon"]),
                        tags=dict(node.get("tags") or {}),
                    )
                    for node in way.get("geometry", [])
                ],
            )
            for way in data.get("ways", [])
        ],
        points=[
            OsmPoint(
                id=int(point.get("id", 0)),
                lat=float(point["lat"]),
                lon=float(point["lon"]),
                name=str(point.get("name") or ""),
                category=str(point.get("category") or ""),
                osm_type=str(point.get("osm_type") or "node"),
                tags=dict(point.get("tags") or {}),
            )
            for point in data.get("points", [])
        ],
    )


def satellite_tile_to_dict(tile: SatelliteTile) -> dict[str, Any]:
    return {"bbox": bbox_to_dict(tile.bbox), "image_path": str(tile.image_path)}


def satellite_tile_from_dict(data: dict[str, Any]) -> SatelliteTile:
    return SatelliteTile(bbox=bbox_from_dict(data["bbox"]), image_path=Path(data["image_path"]))


def dem_grid_to_dict(grid: DemGrid) -> dict[str, Any]:
    return {
        "bbox": bbox_to_dict(grid.bbox),
        "rows": grid.rows,
        "cols": grid.cols,
        "elevations": grid.elevations,
    }


def dem_grid_from_dict(data: dict[str, Any]) -> DemGrid:
    return DemGrid(
        bbox=bbox_from_dict(data["bbox"]),
        rows=int(data["rows"]),
        cols=int(data["cols"]),
        elevations=[float(value) for value in data.get("elevations", [])],
    )
