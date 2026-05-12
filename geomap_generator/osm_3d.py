import math
import re
from dataclasses import dataclass

import bpy

from .blender_scene import link_to_geomap_collection, material_named, set_active
from .exceptions import ProviderError
from .mesh_builder import BboxProjector
from .models import BoundingBox, OsmNode
from .overpass import OsmApiClient
from .threading_utils import assert_main_thread


@dataclass(frozen=True)
class Osm3DBuilding:
    id: int
    name: str
    geometry: list[OsmNode]
    tags: dict[str, str]
    height_m: float
    center_lat: float
    center_lon: float


class Osm3DModelClient:
    def find_nearest_building(
        self,
        lat: float,
        lon: float,
        radius_m: int = 120,
        provider: str = "AUTO",
    ) -> Osm3DBuilding:
        query = self._build_query(lat, lon, radius_m)
        raw = OsmApiClient._fetch_overpass_json(query, provider=provider)
        candidates = self._parse_buildings(raw)
        if not candidates:
            raise ProviderError("No OSM 3D building found near the selected POI.")
        return min(candidates, key=lambda item: self._distance_sq(lat, lon, item))

    @staticmethod
    def _build_query(lat: float, lon: float, radius_m: int) -> str:
        return f"""
        [out:json][timeout:25];
        (
          way(around:{radius_m},{lat:.7f},{lon:.7f})["building"];
          way(around:{radius_m},{lat:.7f},{lon:.7f})["building:part"];
        );
        out tags geom center;
        """

    def _parse_buildings(self, raw: dict) -> list[Osm3DBuilding]:
        buildings = []
        for element in raw.get("elements", []):
            if not isinstance(element, dict) or element.get("type") != "way":
                continue
            geometry = self._parse_geometry(element)
            if len(geometry) < 3:
                continue
            tags = element.get("tags") or {}
            height_m = self._height_m(tags)
            if height_m <= 0.0:
                continue
            center_lat, center_lon = self._center(element, geometry)
            buildings.append(
                Osm3DBuilding(
                    id=element.get("id", 0),
                    name=tags.get("name") or tags.get("building") or "OSM Building",
                    geometry=geometry,
                    tags=tags,
                    height_m=height_m,
                    center_lat=center_lat,
                    center_lon=center_lon,
                )
            )
        return buildings

    @staticmethod
    def _parse_geometry(element: dict) -> list[OsmNode]:
        geometry = []
        for item in element.get("geometry", []):
            if not isinstance(item, dict):
                continue
            lat = item.get("lat")
            lon = item.get("lon")
            if lat is None or lon is None:
                continue
            geometry.append(OsmNode(id=0, lat=lat, lon=lon))
        if len(geometry) > 1:
            first = geometry[0]
            last = geometry[-1]
            if abs(first.lat - last.lat) < 1e-9 and abs(first.lon - last.lon) < 1e-9:
                geometry.pop()
        return geometry

    @staticmethod
    def _height_m(tags: dict[str, str]) -> float:
        for key in ("height", "building:height", "est_height"):
            height = _parse_number(tags.get(key))
            if height:
                return height
        levels = _parse_number(tags.get("building:levels"))
        return levels * 3.0 if levels else 0.0

    @staticmethod
    def _center(element: dict, geometry: list[OsmNode]) -> tuple[float, float]:
        center = element.get("center")
        if isinstance(center, dict) and center.get("lat") and center.get("lon"):
            return center["lat"], center["lon"]
        return (
            sum(node.lat for node in geometry) / len(geometry),
            sum(node.lon for node in geometry) / len(geometry),
        )

    @staticmethod
    def _distance_sq(lat: float, lon: float, building: Osm3DBuilding) -> float:
        return (lat - building.center_lat) ** 2 + (lon - building.center_lon) ** 2


class Osm3DModelRenderer:
    def render_building(
        self,
        context,
        building: Osm3DBuilding,
        bbox: BoundingBox,
        detail_level: str,
        km_per_bu: float,
        base_z: float,
    ):
        assert_main_thread()
        if km_per_bu <= 0.0:
            raise ProviderError("GeoMap scale metadata is missing or invalid.")

        projector = BboxProjector(bbox, detail_level)
        height_bu = building.height_m / (km_per_bu * 1000.0)
        verts = []
        for node in building.geometry:
            x, y, _z = projector.project(node.lat, node.lon, z=base_z)
            verts.append((x, y, base_z))
        top_start = len(verts)
        verts.extend((x, y, base_z + height_bu) for x, y, _z in verts[:top_start])

        bottom = tuple(reversed(range(top_start)))
        top = tuple(range(top_start, top_start * 2))
        faces = [bottom, top]
        for index in range(top_start):
            next_index = (index + 1) % top_start
            faces.append((index, next_index, top_start + next_index, top_start + index))

        mesh = bpy.data.meshes.new(f"GeoMap_3D_{building.id}_Mesh")
        obj = bpy.data.objects.new(f"GeoMap_3D_{building.name}_{building.id}", mesh)
        link_to_geomap_collection(context, obj, "3D Models")
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj.data.materials.append(
            material_named("GeoMap_3D_Building_Material", (0.55, 0.52, 0.46, 1.0))
        )
        obj["geomap_layer"] = "osm_3d_building"
        obj["geomap_osm_id"] = str(building.id)
        obj["geomap_height_m"] = round(building.height_m, 2)
        obj["geomap_height_bu"] = round(height_bu, 6)
        set_active(context, obj)
        return obj


def _parse_number(value: str | None) -> float:
    if not value:
        return 0.0
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    if not match:
        return 0.0
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return 0.0
