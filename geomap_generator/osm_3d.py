import re
from dataclasses import dataclass, field

import bpy

from .blender_scene import link_to_geomap_collection, material_named, set_active
from .exceptions import ProviderError
from .geometry_payload import (
    BuildingBatchPayload,
    BuildingMeshPayload,
    build_building_payload,
)
from .models import BoundingBox, OsmNode, OsmWay
from .overpass import OsmApiClient
from .threading_utils import assert_main_thread

_DEFAULT_BUILDING_HEIGHT_M = 9.0
# Per-level height for buildings lacking an explicit height tag.
# map3d uses 2.2 m; industry average (residential + commercial mix) is ~2.8 m.
_LEVEL_HEIGHT_M = 2.8

# bbox diagonal (km) threshold for AUTO LOD: below = DETAILED, above = SIMPLE
_AUTO_LOD_DETAILED_KM = 2.5
_AUTO_LOD_SIMPLE_KM = 15.0

# ---------------------------------------------------------------------------
# Batch material palette: OSM building:material → RGBA linear colour
# Used for SIMPLE/batch mode to colour-code building meshes by material type.
# ---------------------------------------------------------------------------
_BATCH_MATERIAL_COLORS: dict[str, tuple[float, float, float, float]] = {
    "brick":          (0.55, 0.28, 0.15, 1.0),
    "stone":          (0.58, 0.54, 0.48, 1.0),
    "concrete":       (0.52, 0.52, 0.50, 1.0),
    "plaster":        (0.82, 0.78, 0.70, 1.0),
    "render":         (0.82, 0.78, 0.70, 1.0),
    "glass":          (0.72, 0.82, 0.90, 1.0),
    "metal":          (0.75, 0.75, 0.78, 1.0),
    "steel":          (0.72, 0.72, 0.75, 1.0),
    "wood":           (0.48, 0.32, 0.18, 1.0),
    "timber_framing": (0.58, 0.44, 0.28, 1.0),
}
_BATCH_DEFAULT_COLOR: tuple[float, float, float, float] = (0.55, 0.52, 0.46, 1.0)


def building_batch_material_key(tags: dict[str, str]) -> str:
    """Return a stable material category key for batch-mesh colouring."""
    mat = (tags.get("building:material") or "").lower()
    return mat if mat in _BATCH_MATERIAL_COLORS else "default"


def building_batch_color(key: str) -> tuple[float, float, float, float]:
    """Return the RGBA linear colour for a batch material key."""
    return _BATCH_MATERIAL_COLORS.get(key, _BATCH_DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# building:part aggregation helpers
# ---------------------------------------------------------------------------

def aggregate_building_parts(buildings: list) -> list:
    """Remove base building footprints superseded by building:part children.

    When building:part ways compose the full 3-D shape of a building, rendering
    the parent building=yes footprint on top causes Z-fighting and inflated
    geometry. This function discards any base building whose bounding box
    overlaps with at least one building:part.
    """
    parts = [b for b in buildings if b.tags.get("building:part")]
    bases = [b for b in buildings if not b.tags.get("building:part")]

    if not parts:
        return buildings

    part_bboxes = [_geo_bbox(b.geometry) for b in parts]
    result = list(parts)
    for base in bases:
        base_bbox = _geo_bbox(base.geometry)
        if not any(_geo_bboxes_overlap(base_bbox, pb) for pb in part_bboxes):
            result.append(base)
    return result


def _geo_bbox(geometry: list) -> tuple[float, float, float, float]:
    lats = [n.lat for n in geometry]
    lons = [n.lon for n in geometry]
    return min(lats), min(lons), max(lats), max(lons)


def _geo_bboxes_overlap(a: tuple, b: tuple) -> bool:
    """Return True when two lat/lon bboxes intersect."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


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
    """Parsed OSM building or building:part ready for 3D extrusion."""

    id: int
    name: str
    geometry: list[OsmNode]      # outer footprint ring (closed, no duplicate end node)
    tags: dict[str, str]
    height_m: float              # total height above ground (from OSM height/levels tags)
    center_lat: float
    center_lon: float
    min_height_m: float = 0.0    # base height above ground (building:part floating sections)
    inner_rings: tuple = field(  # courtyard rings; tuple[list[OsmNode], ...]
        default_factory=tuple,
    )
    # S3DB extended tags
    roof_shape: str = ""         # flat | pyramidal | gabled | hipped | dome | mansard | …
    roof_height_m: float = 0.0   # height of roof portion only (OSM roof:height tag)
    roof_direction: float = 0.0  # ridge orientation degrees, 0=north/+Y, 90=east/+X
    building_layer: int = 0      # OSM layer tag (positive=bridge/elevated, negative=tunnel)


@dataclass
class Osm3DModelCandidate:
    id: int
    osm_type: str
    name: str
    lat: float
    lon: float
    tags: dict[str, str]
    has_geometry: bool = False


class Osm3DModelClient:
    def list_model_candidates(
        self,
        bbox: BoundingBox,
        provider: str = "AUTO",
        limit: int = 300,
    ) -> list[Osm3DModelCandidate]:
        query = self._build_candidate_query(bbox, limit)
        raw = OsmApiClient._fetch_overpass_json(query, provider=provider)
        return self._parse_model_candidates(raw)

    def fetch_model_by_id(
        self,
        osm_id: int,
        osm_type: str | None,
        provider: str = "AUTO",
        debug_log=None,
    ) -> Osm3DBuilding:
        for require_building in (True, False):
            direct_query = self._build_direct_query(
                osm_id,
                osm_type,
                require_building=require_building,
            )
            if debug_log:
                debug_log(
                    "3D model direct query "
                    f"osm_type={osm_type} osm_id={osm_id} "
                    f"require_building={require_building} provider={provider}"
                )
            raw = OsmApiClient._fetch_overpass_json(direct_query, provider=provider)
            if debug_log:
                elements = raw.get("elements", []) if isinstance(raw, dict) else []
                types = [
                    str(item.get("type", "?"))
                    for item in elements[:8]
                    if isinstance(item, dict)
                ]
                debug_log(
                    "3D model Overpass response "
                    f"elements={len(elements)} first_types={types}"
                )
            candidates = self._parse_buildings(raw)
            if debug_log:
                debug_log(f"3D model parsed closed candidates={len(candidates)}")
            if candidates:
                return candidates[0]
            node_query = self._build_direct_node_query(
                osm_id,
                osm_type,
                require_building=require_building,
            )
            if debug_log:
                debug_log(
                    "3D model direct node query "
                    f"osm_type={osm_type} osm_id={osm_id} "
                    f"require_building={require_building} provider={provider}"
                )
            raw = OsmApiClient._fetch_overpass_json(node_query, provider=provider)
            if debug_log:
                elements = raw.get("elements", []) if isinstance(raw, dict) else []
                types = [
                    str(item.get("type", "?"))
                    for item in elements[:8]
                    if isinstance(item, dict)
                ]
                debug_log(
                    "3D model node response "
                    f"elements={len(elements)} first_types={types}"
                )
            candidates = self._parse_buildings(raw)
            if debug_log:
                debug_log(f"3D model parsed node candidates={len(candidates)}")
            if candidates:
                return candidates[0]
        raise ProviderError("Selected 3D model marker is not a closed OSM area.")

    def fetch_buildings_in_bbox(
        self,
        bbox: BoundingBox,
        provider: str = "AUTO",
    ) -> list[Osm3DBuilding]:
        """Fetch ALL buildings in the bounding box via a single Overpass query.

        Mirrors the map3d batch approach: queries way["building"] + relation["building"]
        (plus building:part) with ``out body geom`` so full geometry is returned in one
        round-trip, avoiding the need to re-resolve node references.
        """
        bounds = bbox.to_overpass()
        query = f"""
        [out:json][timeout:60];
        (
          way["building"]({bounds});
          way["building:part"]({bounds});
          relation["building"]({bounds});
          relation["building:part"]({bounds});
        );
        out body geom;
        """
        raw = OsmApiClient._fetch_overpass_json(query, provider=provider)
        return self._parse_buildings(raw)

    def buildings_from_ways(self, ways: list[OsmWay]) -> list[Osm3DBuilding]:
        """Convert pre-fetched OSM ways tagged building/building:part to Osm3DBuilding list."""
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
                    min_height_m=self._min_height_m(tags),
                    roof_shape=self._roof_shape(tags),
                    roof_height_m=self._roof_height_m(tags),
                    roof_direction=self._roof_direction(tags),
                    building_layer=self._building_layer(tags),
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
    def _build_candidate_query(bbox: BoundingBox, limit: int) -> str:
        bounds = bbox.to_overpass()
        return f"""
        [out:json][timeout:25];
        (
          way({bounds})["building"]["name"];
          relation({bounds})["building"]["name"];
          way({bounds})["historic"];
          relation({bounds})["historic"];
          way({bounds})["historic"]["name"];
          relation({bounds})["historic"]["name"];
          way({bounds})["tourism"="attraction"];
          relation({bounds})["tourism"="attraction"];
          way({bounds})["tourism"]["name"];
          relation({bounds})["tourism"]["name"];
          way({bounds})["heritage"];
          relation({bounds})["heritage"];
          way({bounds})["wikidata"];
          relation({bounds})["wikidata"];
          way({bounds})["amenity"="place_of_worship"]["name"];
          relation({bounds})["amenity"="place_of_worship"]["name"];
        );
        out body geom {int(limit)};
        """

    @staticmethod
    def _parse_model_candidates(raw: dict) -> list[Osm3DModelCandidate]:
        candidates: list[Osm3DModelCandidate] = []
        seen: set[tuple[str, int]] = set()
        for element in raw.get("elements", []):
            if not isinstance(element, dict):
                continue
            osm_type = str(element.get("type", ""))
            if osm_type not in {"way", "relation"}:
                continue
            geometry = Osm3DModelClient._parse_geometry(element)
            center = element.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")
            if lat is None or lon is None:
                if len(geometry) < 3:
                    continue
                lat, lon = Osm3DModelClient._center(element, geometry)
            osm_id = int(element.get("id", 0) or 0)
            if osm_id <= 0 or (osm_type, osm_id) in seen:
                continue
            seen.add((osm_type, osm_id))
            tags = element.get("tags") or {}
            name = tags.get("name") or tags.get("building") or tags.get("historic")
            if not name:
                name = "OSM 3D Model"
            candidates.append(
                Osm3DModelCandidate(
                    id=osm_id,
                    osm_type=osm_type,
                    name=str(name),
                    lat=float(lat),
                    lon=float(lon),
                    tags={str(k): str(v) for k, v in tags.items()},
                    has_geometry=len(geometry) >= 3,
                )
            )
        return sorted(candidates, key=Osm3DModelClient._candidate_sort_key)

    @staticmethod
    def _candidate_sort_key(candidate: Osm3DModelCandidate) -> tuple[int, str]:
        tags = candidate.tags
        score = 0
        name = candidate.name.lower()
        if tags.get("wikidata"):
            score += 1000
        if tags.get("wikipedia"):
            score += 800
        if tags.get("heritage"):
            score += 500
        if tags.get("tourism") == "attraction":
            score += 400
        if tags.get("historic"):
            score += 350
        if tags.get("building"):
            score += 120
        if tags.get("amenity") == "place_of_worship":
            score += 80
        if candidate.has_geometry:
            score += 60
        for landmark_word in (
            "colosseo",
            "colosseum",
            "foro",
            "pantheon",
            "basilica",
            "castel",
            "palazzo",
        ):
            if landmark_word in name:
                score += 1200
                break
        return (-score, name)

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
        out body geom;
        """

    @staticmethod
    def _build_direct_node_query(
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
        out body;
        >;
        out skel qt;
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
        out body geom;
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
        out body geom;
        """

    def _parse_buildings(self, raw: dict) -> list[Osm3DBuilding]:
        buildings = []
        node_index: dict[int, OsmNode] = {}
        for element in raw.get("elements", []):
            if not isinstance(element, dict) or element.get("type") != "node":
                continue
            node_id = element.get("id")
            lat = element.get("lat")
            lon = element.get("lon")
            if node_id is None or lat is None or lon is None:
                continue
            node_index[int(node_id)] = OsmNode(
                id=int(node_id),
                lat=float(lat),
                lon=float(lon),
                tags=element.get("tags") or {},
            )
        for element in raw.get("elements", []):
            if not isinstance(element, dict) or element.get("type") not in {"way", "relation"}:
                continue
            if element.get("type") == "relation":
                outer, inner_rings = self._parse_relation_rings(element)
            else:
                outer = self._parse_geometry(element, node_index)
                inner_rings = ()
            if len(outer) < 3:
                continue
            tags = element.get("tags") or {}
            center_lat, center_lon = self._center(element, outer)
            buildings.append(
                Osm3DBuilding(
                    id=element.get("id", 0),
                    name=tags.get("name") or tags.get("building") or "OSM Building",
                    geometry=outer,
                    tags=tags,
                    height_m=self._height_m(tags),
                    center_lat=center_lat,
                    center_lon=center_lon,
                    min_height_m=self._min_height_m(tags),
                    inner_rings=inner_rings,
                    roof_shape=self._roof_shape(tags),
                    roof_height_m=self._roof_height_m(tags),
                    roof_direction=self._roof_direction(tags),
                    building_layer=self._building_layer(tags),
                )
            )
        return buildings

    @staticmethod
    def _parse_geometry(
        element: dict,
        node_index: dict[int, OsmNode] | None = None,
    ) -> list[OsmNode]:
        if element.get("type") == "relation":
            return Osm3DModelClient._parse_relation_geometry(element)

        geometry = Osm3DModelClient._geometry_from_items(element.get("geometry", []))
        if not geometry and node_index:
            geometry = [
                node_index[int(node_id)]
                for node_id in element.get("nodes", [])
                if int(node_id) in node_index
            ]
            if len(geometry) > 1:
                first = geometry[0]
                last = geometry[-1]
                if abs(first.lat - last.lat) < 1e-9 and abs(first.lon - last.lon) < 1e-9:
                    geometry = geometry[:-1]
                else:
                    geometry = []
        return geometry if len(geometry) >= 3 else []

    @staticmethod
    def _parse_relation_geometry(element: dict) -> list[OsmNode]:
        """Return largest outer ring of a relation (legacy — use _parse_relation_rings)."""
        outer, _inner = Osm3DModelClient._parse_relation_rings(element)
        return outer

    @staticmethod
    def _parse_relation_rings(
        element: dict,
    ) -> tuple[list[OsmNode], tuple[list[OsmNode], ...]]:
        """Parse a building relation into (outer_ring, inner_rings).

        Outer ring = largest outer member way (main footprint).
        Inner rings = member ways with role 'inner' (courtyards / holes).
        """
        outer_rings: list[list[OsmNode]] = []
        inner_rings: list[list[OsmNode]] = []
        for member in element.get("members", []):
            if not isinstance(member, dict) or member.get("type") != "way":
                continue
            role = member.get("role", "")
            geometry = Osm3DModelClient._geometry_from_items(member.get("geometry", []))
            if len(geometry) < 3:
                continue
            if role in {"outer", ""}:
                outer_rings.append(geometry)
            elif role == "inner":
                inner_rings.append(geometry)
        if not outer_rings:
            return [], ()
        outer = max(outer_rings, key=Osm3DModelClient._ring_area_abs)
        return outer, tuple(inner_rings)

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
        """Resolve building height in metres from OSM tags.

        Priority (same as map3d): explicit height tag → levels × _LEVEL_HEIGHT_M → default.
        """
        for key in ("height", "building:height", "est_height"):
            height = _parse_number(tags.get(key))
            if height:
                return height
        levels = _parse_number(tags.get("building:levels"))
        return levels * _LEVEL_HEIGHT_M if levels else _DEFAULT_BUILDING_HEIGHT_M

    @staticmethod
    def _min_height_m(tags: dict[str, str]) -> float:
        """Resolve the height above ground where a building:part starts.

        Used for floating sections (e.g. overhangs, elevated podium blocks).
        Priority: min_height tag → building:min_level × _LEVEL_HEIGHT_M → 0.
        """
        h = _parse_number(tags.get("min_height"))
        if h:
            return h
        levels = _parse_number(tags.get("building:min_level"))
        return levels * _LEVEL_HEIGHT_M if levels else 0.0

    @staticmethod
    def _roof_shape(tags: dict[str, str]) -> str:
        """Return normalised roof:shape tag value (empty string = flat/default)."""
        return (tags.get("roof:shape") or "").lower().strip()

    @staticmethod
    def _roof_height_m(tags: dict[str, str]) -> float:
        """Resolve roof height in metres: roof:height tag → roof:levels × _LEVEL_HEIGHT_M → 0."""
        h = _parse_number(tags.get("roof:height"))
        if h > 0:
            return h
        levels = _parse_number(tags.get("roof:levels"))
        return levels * _LEVEL_HEIGHT_M if levels else 0.0

    @staticmethod
    def _roof_direction(tags: dict[str, str]) -> float:
        """Return roof ridge direction in degrees (0=north/+Y, 90=east/+X)."""
        return _parse_number(tags.get("roof:direction"))

    @staticmethod
    def _building_layer(tags: dict[str, str]) -> int:
        """Return OSM layer tag as int (positive=elevated, negative=underground)."""
        return int(_parse_number(tags.get("layer")) or 0)

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
    def _osm_material(_building_id: int, tags: dict[str, str]):
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
        color = getattr(payload, "material_color", (0.55, 0.52, 0.46, 1.0))
        mat_name = f"GeoMap_3D_Batch_{batch_index}"
        mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        mat.diffuse_color = color
        tree = mat.node_tree
        if tree:
            bsdf = next(
                (n for n in tree.nodes if getattr(n, "type", None) == "BSDF_PRINCIPLED"),
                None,
            )
            if bsdf:
                bc = bsdf.inputs.get("Base Color")
                if bc:
                    bc.default_value = color
        obj.data.materials.append(mat)
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
