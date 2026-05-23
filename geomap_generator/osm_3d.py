import math
import re
from dataclasses import dataclass

import bpy

from .blender_scene import link_to_geomap_collection, material_named, set_active
from .exceptions import ProviderError
from .geometry_payload import (
    BuildingBatchPayload,
    BuildingMeshPayload,
    build_building_payload,
)
from .mesh_builder import BboxProjector
from .models import BoundingBox, OsmNode, OsmWay
from .overpass import OsmApiClient
from .threading_utils import assert_main_thread

_DEFAULT_BUILDING_HEIGHT_M = 9.0

# bbox diagonal (km) threshold for AUTO LOD: below = DETAILED, above = SIMPLE
_AUTO_LOD_DETAILED_KM = 2.5
_AUTO_LOD_SIMPLE_KM = 15.0

# CSS-style hex colour → linear RGB (approximate, gamma 2.2)
_COLOUR_NAME_MAP: dict[str, tuple[float, float, float]] = {
    "white":   (1.0,  1.0,  1.0),
    "gray":    (0.45, 0.45, 0.45),
    "grey":    (0.45, 0.45, 0.45),
    "black":   (0.02, 0.02, 0.02),
    "red":     (0.80, 0.05, 0.05),
    "brown":   (0.28, 0.12, 0.04),
    "orange":  (0.80, 0.35, 0.02),
    "yellow":  (0.90, 0.78, 0.10),
    "green":   (0.08, 0.35, 0.08),
    "blue":    (0.05, 0.18, 0.70),
    "silver":  (0.65, 0.65, 0.65),
    "beige":   (0.76, 0.70, 0.52),
    "tan":     (0.64, 0.50, 0.34),
    "cream":   (0.90, 0.84, 0.68),
    "sand":    (0.72, 0.62, 0.42),
}

# OSM building:material → (roughness, metallic, base_color_rgb)
_MATERIAL_PROPS: dict[str, tuple[float, float, tuple[float, float, float]]] = {
    "brick":       (0.90, 0.00, (0.55, 0.28, 0.15)),
    "stone":       (0.85, 0.00, (0.58, 0.54, 0.48)),
    "concrete":    (0.75, 0.00, (0.52, 0.52, 0.50)),
    "plaster":     (0.80, 0.00, (0.82, 0.78, 0.70)),
    "render":      (0.80, 0.00, (0.82, 0.78, 0.70)),
    "wood":        (0.88, 0.00, (0.48, 0.32, 0.18)),
    "timber_framing": (0.85, 0.00, (0.58, 0.44, 0.28)),
    "glass":       (0.05, 0.00, (0.72, 0.82, 0.90)),
    "metal":       (0.30, 0.90, (0.75, 0.75, 0.78)),
    "steel":       (0.25, 0.95, (0.72, 0.72, 0.75)),
    "copper":      (0.40, 0.90, (0.48, 0.62, 0.42)),
    "zinc":        (0.50, 0.80, (0.68, 0.70, 0.72)),
    "aluminium":   (0.35, 0.85, (0.80, 0.80, 0.82)),
    "tile":        (0.70, 0.00, (0.62, 0.28, 0.18)),
    "slate":       (0.80, 0.00, (0.28, 0.28, 0.32)),
    "granite":     (0.78, 0.00, (0.55, 0.48, 0.44)),
    "marble":      (0.30, 0.00, (0.90, 0.88, 0.84)),
    "sandstone":   (0.82, 0.00, (0.72, 0.58, 0.38)),
    "limestone":   (0.80, 0.00, (0.88, 0.84, 0.72)),
}


def _hex_to_rgb(hex_str: str) -> tuple[float, float, float] | None:
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    if len(s) != 6:
        return None
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (r / 255) ** 2.2, (g / 255) ** 2.2, (b / 255) ** 2.2
    except ValueError:
        return None


def _colour_from_tag(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    v = value.strip().lower()
    named = _COLOUR_NAME_MAP.get(v)
    if named:
        return named
    return _hex_to_rgb(v)


def building_material_from_tags(tags: dict[str, str]) -> tuple[
    tuple[float, float, float, float],  # RGBA diffuse
    float,                               # roughness
    float,                               # metallic
]:
    """Return (color_rgba, roughness, metallic) derived from OSM building tags."""
    mat_key = (tags.get("building:material") or "").lower()
    roughness, metallic, base_rgb = _MATERIAL_PROPS.get(mat_key, (0.80, 0.00, (0.55, 0.52, 0.46)))
    colour_tag = tags.get("building:colour") or tags.get("building:color")
    rgb = _colour_from_tag(colour_tag)
    if rgb is None:
        rgb = base_rgb
    return (*rgb, 1.0), roughness, metallic


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
    def buildings_from_ways(self, ways: list[OsmWay]) -> list[Osm3DBuilding]:
        buildings = []
        for way in ways:
            if not self._is_building(way.tags):
                continue
            geometry = self._closed_way_geometry(way.geometry)
            if len(geometry) < 3:
                continue
            tags = way.tags
            buildings.append(
                Osm3DBuilding(
                    id=way.id,
                    name=tags.get("name") or tags.get("building") or "OSM Building",
                    geometry=geometry,
                    tags=tags,
                    height_m=self._height_m(tags),
                    center_lat=sum(node.lat for node in geometry) / len(geometry),
                    center_lon=sum(node.lon for node in geometry) / len(geometry),
                )
            )
        return buildings

    def find_nearest_building(
        self,
        lat: float,
        lon: float,
        radius_m: int = 120,
        provider: str = "AUTO",
        osm_id: int | None = None,
        osm_type: str | None = None,
    ) -> Osm3DBuilding:
        candidates = []
        if osm_id is not None and osm_type in {"way", "relation", None, ""}:
            for direct_query in (
                self._build_direct_query(osm_id, osm_type, require_building=True),
                self._build_direct_query(osm_id, osm_type, require_building=False),
            ):
                raw = OsmApiClient._fetch_overpass_json(direct_query, provider=provider)
                candidates = self._parse_buildings(raw)
                if candidates:
                    break
        if not candidates:
            query = self._build_query(lat, lon, radius_m)
            raw = OsmApiClient._fetch_overpass_json(query, provider=provider)
            candidates = self._parse_buildings(raw)
        if not candidates:
            query = self._build_area_query(lat, lon, radius_m)
            raw = OsmApiClient._fetch_overpass_json(query, provider=provider)
            candidates = self._parse_buildings(raw)
        if not candidates:
            raise ProviderError("No closed OSM area or building found near the selected POI.")
        return min(candidates, key=lambda item: self._distance_sq(lat, lon, item))

    @staticmethod
    def _build_direct_query(
        osm_id: int,
        osm_type: str | None,
        require_building: bool,
    ) -> str:
        filters = []
        suffixes = (
            ('["building"]', '["building:part"]')
            if require_building
            else ("",)
        )
        if osm_type in {"way", None, ""}:
            filters.extend(f"way(id:{osm_id}){suffix};" for suffix in suffixes)
        if osm_type in {"relation", None, ""}:
            filters.extend(f"relation(id:{osm_id}){suffix};" for suffix in suffixes)
        return f"""
        [out:json][timeout:25];
        (
          {"".join(filters)}
        );
        out tags geom center;
        """

    @staticmethod
    def _build_area_query(lat: float, lon: float, radius_m: int) -> str:
        filters = []
        for key in ("historic", "tourism", "amenity", "leisure", "natural"):
            filters.append(f'way(around:{radius_m},{lat:.7f},{lon:.7f})["{key}"];')
            filters.append(f'relation(around:{radius_m},{lat:.7f},{lon:.7f})["{key}"];')
        return f"""
        [out:json][timeout:25];
        (
          {"".join(filters)}
        );
        out tags geom center;
        """

    @staticmethod
    def _build_query(lat: float, lon: float, radius_m: int) -> str:
        return f"""
        [out:json][timeout:25];
        (
          way(around:{radius_m},{lat:.7f},{lon:.7f})["building"];
          way(around:{radius_m},{lat:.7f},{lon:.7f})["building:part"];
          relation(around:{radius_m},{lat:.7f},{lon:.7f})["building"];
          relation(around:{radius_m},{lat:.7f},{lon:.7f})["building:part"];
        );
        out tags geom center;
        """

    def _parse_buildings(self, raw: dict) -> list[Osm3DBuilding]:
        buildings = []
        for element in raw.get("elements", []):
            if not isinstance(element, dict) or element.get("type") not in {"way", "relation"}:
                continue
            geometry = self._parse_geometry(element)
            if len(geometry) < 3:
                continue
            tags = element.get("tags") or {}
            height_m = self._height_m(tags)
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
        if element.get("type") == "relation":
            return Osm3DModelClient._parse_relation_geometry(element)

        geometry = Osm3DModelClient._geometry_from_items(element.get("geometry", []))
        return geometry if len(geometry) >= 3 else []

    @staticmethod
    def _parse_relation_geometry(element: dict) -> list[OsmNode]:
        rings = []
        for member in element.get("members", []):
            if not isinstance(member, dict):
                continue
            if member.get("type") != "way":
                continue
            if member.get("role") not in {"outer", ""}:
                continue
            geometry = Osm3DModelClient._geometry_from_items(member.get("geometry", []))
            if len(geometry) >= 3:
                rings.append(geometry)
        if not rings:
            return []
        return max(rings, key=Osm3DModelClient._ring_area_abs)

    @staticmethod
    def _geometry_from_items(items: list) -> list[OsmNode]:
        geometry = [
            OsmNode(id=0, lat=item["lat"], lon=item["lon"])
            for item in items
            if isinstance(item, dict)
            and item.get("lat") is not None
            and item.get("lon") is not None
        ]
        if len(geometry) > 1:
            first = geometry[0]
            last = geometry[-1]
            if abs(first.lat - last.lat) < 1e-9 and abs(first.lon - last.lon) < 1e-9:
                geometry.pop()
            else:
                return []
        return geometry

    @staticmethod
    def _ring_area_abs(geometry: list[OsmNode]) -> float:
        area = 0.0
        for first, second in zip(geometry, [*geometry[1:], geometry[0]]):
            area += first.lon * second.lat - second.lon * first.lat
        return abs(area)

    @staticmethod
    def _height_m(tags: dict[str, str]) -> float:
        for key in ("height", "building:height", "est_height"):
            height = _parse_number(tags.get(key))
            if height:
                return height
        levels = _parse_number(tags.get("building:levels"))
        return levels * 3.0 if levels else _DEFAULT_BUILDING_HEIGHT_M

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

    @staticmethod
    def _is_building(tags: dict[str, str]) -> bool:
        return bool(tags.get("building") or tags.get("building:part"))

    @staticmethod
    def _closed_way_geometry(geometry: list[OsmNode]) -> list[OsmNode]:
        if len(geometry) < 4:
            return []
        first = geometry[0]
        last = geometry[-1]
        if abs(first.lat - last.lat) >= 1e-9 or abs(first.lon - last.lon) >= 1e-9:
            return []
        return geometry[:-1]


class Osm3DModelRenderer:
    def build_payload(
        self,
        building: Osm3DBuilding,
        bbox: BoundingBox,
        detail_level: str,
        km_per_bu: float,
        base_z: float,
    ) -> BuildingMeshPayload:
        return build_building_payload(building, bbox, detail_level, km_per_bu, base_z)

    def commit_payload(
        self,
        context,
        payload: BuildingMeshPayload,
        tags: dict[str, str] | None = None,
    ):
        assert_main_thread()
        mesh = bpy.data.meshes.new(f"GeoMap_3D_{payload.id}_Mesh")
        obj = bpy.data.objects.new(f"GeoMap_3D_{payload.name}_{payload.id}", mesh)
        link_to_geomap_collection(context, obj, "3D Models")
        mesh.from_pydata(payload.verts, [], payload.faces)
        mesh.update()
        if tags:
            mat = self._osm_material(payload.id, tags)
        else:
            mat = material_named("GeoMap_3D_Building_Material", (0.55, 0.52, 0.46, 1.0))
        obj.data.materials.append(mat)
        obj["geomap_layer"] = "osm_3d_building"
        obj["geomap_osm_id"] = str(payload.id)
        obj["geomap_height_m"] = round(payload.height_m, 2)
        obj["geomap_height_bu"] = round(payload.height_bu, 6)
        set_active(context, obj)
        return obj

    @staticmethod
    def _osm_material(building_id: int, tags: dict[str, str]):
        color_rgba, roughness, metallic = building_material_from_tags(tags)
        mat_key = (
            f"GeoMap_3D_Bld_{tags.get('building:material','')}"
            f"_{tags.get('building:colour','')}"
        )
        mat = bpy.data.materials.get(mat_key) or bpy.data.materials.new(mat_key)
        mat.use_nodes = True
        mat.diffuse_color = color_rgba
        tree = mat.node_tree
        if tree:
            bsdf = next(
                (n for n in tree.nodes if getattr(n, "type", None) == "BSDF_PRINCIPLED"),
                None,
            )
            if bsdf:
                bc = bsdf.inputs.get("Base Color")
                if bc:
                    bc.default_value = color_rgba
                r = bsdf.inputs.get("Roughness")
                if r:
                    r.default_value = roughness
                m = bsdf.inputs.get("Metallic")
                if m:
                    m.default_value = metallic
        return mat

    def commit_batch_payload(self, context, payload: BuildingBatchPayload, batch_index: int):
        assert_main_thread()
        if not payload.verts:
            return None
        mesh = bpy.data.meshes.new(f"{payload.name}_Mesh")
        obj = bpy.data.objects.new(payload.name, mesh)
        link_to_geomap_collection(context, obj, "3D Models")
        mesh.from_pydata(payload.verts, [], payload.faces)
        mesh.update()
        obj.data.materials.append(
            material_named("GeoMap_3D_Building_Material", (0.55, 0.52, 0.46, 1.0))
        )
        obj["geomap_layer"] = "osm_3d_buildings_batch"
        obj["geomap_batch_index"] = batch_index
        obj["geomap_building_count"] = payload.building_count
        obj["geomap_geometry_type"] = "MERGED_MESH"
        obj["geomap_simplified"] = True
        set_active(context, obj)
        return obj

    def render_building(
        self,
        context,
        building: Osm3DBuilding,
        bbox: BoundingBox,
        detail_level: str,
        km_per_bu: float,
        base_z: float,
        use_osm_material: bool = False,
    ):
        assert_main_thread()
        try:
            payload = self.build_payload(building, bbox, detail_level, km_per_bu, base_z)
        except ValueError as error:
            raise ProviderError(str(error)) from error
        return self.commit_payload(
            context, payload, tags=building.tags if use_osm_material else None
        )


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
