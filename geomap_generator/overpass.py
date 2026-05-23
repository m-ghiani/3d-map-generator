import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .download_cache import cached_json
from .exceptions import CancelledGeneration
from .models import BoundingBox, GeoMapData, OsmNode, OsmPoint, OsmWay

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_PROVIDER_URLS = {
    "OVERPASS_MAIN": "https://overpass-api.de/api/interpreter",
    "OVERPASS_PRIVATE_COFFEE": "https://overpass.private.coffee/api/interpreter",
    "OVERPASS_MAPRVA": "https://overpass.maprva.org/api/interpreter",
}
OVERPASS_URL = OVERPASS_PROVIDER_URLS["OVERPASS_MAIN"]
OVERPASS_FALLBACK_URLS = tuple(OVERPASS_PROVIDER_URLS.values())
_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"

# Nominatim policy: identify app with contact email in User-Agent.
_MAX_BBOX_LAT_SPAN = 40.0
_MAX_BBOX_LON_SPAN = 80.0
_MAX_QUERY_TILE_LAT_SPAN = 4.0
_MAX_QUERY_TILE_LON_SPAN = 4.0
_OVERPASS_CACHE_TTL_SECONDS = 24 * 60 * 60


class OsmJsonParser:
    """
    Parses Overpass JSON (elements array: nodes / ways).

    Feature queries use ``out geom``, so ways usually include their geometry
    directly. Node resolution is kept for responses that return way node refs.
    """

    def parse(self, raw: dict, bbox: BoundingBox) -> GeoMapData:
        if not isinstance(raw, dict):
            return GeoMapData(ways=[], bbox=bbox)
        elements = raw.get("elements", [])
        if not isinstance(elements, list):
            return GeoMapData(ways=[], bbox=bbox)

        node_index: dict[int, OsmNode] = {}
        ways: list[OsmWay] = []
        points: list[OsmPoint] = []

        for el in elements:
            if not isinstance(el, dict):
                continue
            if el.get("type") == "node":
                node_id = el.get("id")
                lat = el.get("lat")
                lon = el.get("lon")
                if node_id is None or lat is None or lon is None:
                    continue
                point = self._parse_point(el, lat, lon)
                if point:
                    points.append(point)
                node_index[node_id] = OsmNode(
                    id=node_id,
                    lat=lat,
                    lon=lon,
                    tags=el.get("tags") or {},
                )

        for el in elements:
            if not isinstance(el, dict):
                continue
            if el.get("type") != "way":
                continue
            way = self._parse_way(el, node_index)
            if way.geometry:
                ways.append(way)

        for el in elements:
            if not isinstance(el, dict) or el.get("type") == "node":
                continue
            center = el.get("center")
            if not isinstance(center, dict):
                continue
            lat = center.get("lat")
            lon = center.get("lon")
            if lat is None or lon is None:
                continue
            point = self._parse_point(el, lat, lon)
            if point:
                points.append(point)

        return GeoMapData(ways=ways, bbox=bbox, points=points)

    def _parse_way(self, el: dict, node_index: dict[int, OsmNode]) -> OsmWay:
        raw_geometry = el.get("geometry")
        if isinstance(raw_geometry, list):
            geometry = [
                OsmNode(id=0, lat=g["lat"], lon=g["lon"])
                for g in raw_geometry
                if isinstance(g, dict)
                and g.get("lat") is not None
                and g.get("lon") is not None
            ]
        else:
            raw_nodes = el.get("nodes", [])
            if not isinstance(raw_nodes, list):
                raw_nodes = []
            geometry = [node_index[nid] for nid in raw_nodes if nid in node_index]
        return OsmWay(id=el.get("id", 0), geometry=geometry, tags=el.get("tags") or {})

    def _parse_point(self, el: dict, lat: float, lon: float) -> OsmPoint | None:
        tags = el.get("tags") or {}
        category = self._point_category(tags)
        if not category:
            return None
        name = tags.get("name") or tags.get("official_name") or f"{category}_{el.get('id', 0)}"
        return OsmPoint(
            id=el.get("id", 0),
            lat=lat,
            lon=lon,
            name=name,
            category=category,
            osm_type=el.get("type", "node"),
            tags=tags,
        )

    @staticmethod
    def _point_category(tags: dict[str, str]) -> str | None:
        if tags.get("place") in {"city", "town", "village", "hamlet"}:
            return "city"
        if tags.get("historic"):
            return "historic"
        if tags.get("tourism") in {"museum", "gallery", "artwork", "attraction"}:
            return "cultural"
        if tags.get("amenity") in {"theatre", "arts_centre", "library", "community_centre"}:
            return "cultural"
        if tags.get("office") == "government" or tags.get("amenity") in {
            "townhall",
            "courthouse",
            "police",
            "post_office",
        }:
            return "administrative"
        if tags.get("natural") in {
            "peak",
            "volcano",
            "cave_entrance",
            "spring",
            "waterfall",
            "beach",
            "bay",
            "cliff",
        }:
            return "natural"
        return None


class OsmApiClient:
    """
    Fetches geographic data via Nominatim + Overpass.

    Place/area mode:
        Nominatim /search → geographic bounding box → Overpass feature query

    Bbox mode (rivers, roads, coastlines):
        Overpass feature query using the requested checkbox filters.
    """

    def resolve_bbox(self, name: str) -> BoundingBox:
        direct_candidates = self._search_bboxes(name, limit=5)
        for bbox in direct_candidates:
            if self._is_reasonable_overpass_bbox(bbox):
                return bbox

        for fallback_name in (f"metropolitan {name}", f"mainland {name}"):
            candidates = self._search_bboxes(fallback_name, limit=3)
            for bbox in candidates:
                if self._is_reasonable_overpass_bbox(bbox):
                    return bbox

        if direct_candidates:
            bbox = direct_candidates[0]
            lat_span = bbox.max_lat - bbox.min_lat
            lon_span = bbox.max_lon - bbox.min_lon
            raise RuntimeError(
                f"Nominatim bbox for '{name}' is too large for Overpass "
                f"({lat_span:.1f}° × {lon_span:.1f}°). Try a more specific place or area name."
            )

        raise RuntimeError(f"Place not found via Nominatim: '{name}'")

    def _search_bboxes(self, name: str, limit: int) -> list[BoundingBox]:
        params = urllib.parse.urlencode({"q": name, "format": "json", "limit": limit})
        url = f"{NOMINATIM_URL}/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                results = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Nominatim bbox lookup failed: {e}") from e

        bboxes: list[BoundingBox] = []
        if not isinstance(results, list):
            raise RuntimeError("Nominatim returned an unexpected response.")

        for result in results:
            if not isinstance(result, dict):
                continue
            raw_bbox = result.get("boundingbox")
            if not raw_bbox or len(raw_bbox) != 4:
                continue
            south, north, west, east = (float(v) for v in raw_bbox)
            bboxes.append(BoundingBox(south, west, north, east))
        return bboxes

    @staticmethod
    def _is_reasonable_overpass_bbox(bbox: BoundingBox) -> bool:
        return (
            bbox.max_lat > bbox.min_lat
            and bbox.max_lon > bbox.min_lon
            and bbox.max_lat - bbox.min_lat <= _MAX_BBOX_LAT_SPAN
            and bbox.max_lon - bbox.min_lon <= _MAX_BBOX_LON_SPAN
        )

    def fetch_features(
        self,
        bbox: BoundingBox,
        *,
        coastlines: bool = False,
        rivers: bool = False,
        roads: bool = False,
        buildings: bool = False,
        admin_level: str | None = None,
        cities: bool = False,
        poi_historic: bool = False,
        poi_cultural: bool = False,
        poi_administrative: bool = False,
        poi_natural: bool = False,
        landuse: bool = False,
        provider: str = "AUTO",
        progress: Callable[[str, float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GeoMapData:
        self._raise_if_cancelled(should_cancel)
        if progress:
            progress("Building Overpass query...", 0.32)
        tiles = bbox.split(_MAX_QUERY_TILE_LAT_SPAN, _MAX_QUERY_TILE_LON_SPAN)
        if len(tiles) > 1:
            return self._fetch_features_tiled(
                bbox,
                tiles,
                coastlines=coastlines,
                rivers=rivers,
                roads=roads,
                buildings=buildings,
                admin_level=admin_level,
                cities=cities,
                poi_historic=poi_historic,
                poi_cultural=poi_cultural,
                poi_administrative=poi_administrative,
                poi_natural=poi_natural,
                landuse=landuse,
                provider=provider,
                progress=progress,
                should_cancel=should_cancel,
            )

        return self._fetch_features_single(
            bbox,
            coastlines=coastlines,
            rivers=rivers,
            roads=roads,
            buildings=buildings,
            admin_level=admin_level,
            cities=cities,
            poi_historic=poi_historic,
            poi_cultural=poi_cultural,
            poi_administrative=poi_administrative,
            poi_natural=poi_natural,
            landuse=landuse,
            provider=provider,
            progress=progress,
            should_cancel=should_cancel,
        )

    def _fetch_features_tiled(
        self,
        original_bbox: BoundingBox,
        tiles: list[BoundingBox],
        *,
        coastlines: bool,
        rivers: bool,
        roads: bool,
        buildings: bool,
        admin_level: str | None,
        cities: bool,
        poi_historic: bool,
        poi_cultural: bool,
        poi_administrative: bool,
        poi_natural: bool,
        landuse: bool = False,
        provider: str,
        progress: Callable[[str, float], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> GeoMapData:
        ways: list[OsmWay] = []
        points: list[OsmPoint] = []
        seen: set[tuple[int, tuple[tuple[float, float], ...]]] = set()
        for index, tile in enumerate(tiles, start=1):
            self._raise_if_cancelled(should_cancel)
            if progress:
                pct = 0.32 + ((index - 1) / len(tiles)) * 0.42
                progress(f"Fetching OSM tile {index}/{len(tiles)}...", pct)
            data = self._fetch_features_single(
                tile,
                coastlines=coastlines,
                rivers=rivers,
                roads=roads,
                buildings=buildings,
                admin_level=admin_level,
                cities=cities,
                poi_historic=poi_historic,
                poi_cultural=poi_cultural,
                poi_administrative=poi_administrative,
                poi_natural=poi_natural,
                landuse=landuse,
                provider=provider,
                progress=None,
                should_cancel=should_cancel,
            )
            for way in data.ways:
                key = (
                    way.id,
                    tuple((round(node.lat, 7), round(node.lon, 7)) for node in way.geometry),
                )
                if key not in seen:
                    seen.add(key)
                    ways.append(way)
            points.extend(data.points)
        if progress:
            progress("OSM tiled data ready", 0.74)
        return GeoMapData(ways=ways, bbox=original_bbox, points=points)

    def _fetch_features_single(
        self,
        bbox: BoundingBox,
        *,
        coastlines: bool = False,
        rivers: bool = False,
        roads: bool = False,
        buildings: bool = False,
        admin_level: str | None = None,
        cities: bool = False,
        poi_historic: bool = False,
        poi_cultural: bool = False,
        poi_administrative: bool = False,
        poi_natural: bool = False,
        landuse: bool = False,
        provider: str = "AUTO",
        progress: Callable[[str, float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GeoMapData:
        self._raise_if_cancelled(should_cancel)
        query = self._build_feature_query(
            bbox,
            coastlines=coastlines,
            rivers=rivers,
            roads=roads,
            buildings=buildings,
            admin_level=admin_level,
            cities=cities,
            poi_historic=poi_historic,
            poi_cultural=poi_cultural,
            poi_administrative=poi_administrative,
            poi_natural=poi_natural,
            landuse=landuse,
        )
        if progress:
            progress("Sending Overpass request...", 0.40)
        raw = self._fetch_overpass_json(query, progress, provider)
        self._raise_if_cancelled(should_cancel)

        if progress:
            progress("Parsing OSM data...", 0.68)
        data = OsmJsonParser().parse(raw, bbox)
        if progress:
            progress("OSM data ready", 0.74)
        return data

    @staticmethod
    def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise CancelledGeneration("Cancelled")

    @staticmethod
    def _fetch_overpass_json(
        query: str,
        progress: Callable[[str, float], None] | None = None,
        provider: str = "AUTO",
    ) -> dict:
        if provider == "AUTO":
            urls = OVERPASS_FALLBACK_URLS
        elif provider in OVERPASS_PROVIDER_URLS:
            urls = (OVERPASS_PROVIDER_URLS[provider],)
        else:
            raise RuntimeError(f"Unsupported Overpass provider: {provider}")

        last_error: Exception | None = None
        for index, url in enumerate(urls, start=1):
            if progress and index > 1:
                progress(f"Retrying Overpass endpoint {index}/{len(urls)}...", 0.42)
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
                headers={
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            try:
                if progress:
                    progress("Downloading OSM data...", 0.55)

                def fetch() -> dict:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        return json.loads(resp.read().decode("utf-8"))

                data = cached_json(
                    "overpass",
                    f"{url}|{query}",
                    _OVERPASS_CACHE_TTL_SECONDS,
                    fetch,
                )
                remark = data.get("remark", "") if isinstance(data, dict) else ""
                if remark and any(
                    k in remark.lower()
                    for k in ("runtime error", "out of memory", "timed out")
                ):
                    raise RuntimeError(
                        f"Overpass query too large: {remark[:200]}. "
                        "Reduce the bounding box or disable some feature types."
                    )
                return data
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code not in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"Overpass HTTP {e.code}: {e.reason}") from e
            except urllib.error.URLError as e:
                last_error = e

        raise RuntimeError(f"All Overpass endpoints failed. Last error: {last_error}")

    @staticmethod
    def _build_feature_query(
        bbox: BoundingBox,
        *,
        coastlines: bool = False,
        rivers: bool = False,
        roads: bool = False,
        buildings: bool = False,
        admin_level: str | None = None,
        cities: bool = False,
        poi_historic: bool = False,
        poi_cultural: bool = False,
        poi_administrative: bool = False,
        poi_natural: bool = False,
        landuse: bool = False,
    ) -> str:
        filters: list[str] = []
        bbox_expr = bbox.to_overpass()

        if coastlines:
            filters.append(f'way["natural"="coastline"]({bbox_expr});')
        if rivers:
            filters.append(f'way["waterway"~"^(river|stream|canal)$"]({bbox_expr});')
        if roads:
            filters.append(f'way["highway"~"^(motorway|trunk|primary)$"]({bbox_expr});')
        if buildings:
            filters.append(f'way["building"]({bbox_expr});')
            filters.append(f'relation["building"]({bbox_expr});')
        if admin_level == "ALL":
            filters.append(
                f'way["boundary"="administrative"]["admin_level"~"^(2|4|6|8)$"]({bbox_expr});'
            )
        elif admin_level:
            filters.append(
                f'way["boundary"="administrative"]["admin_level"="{admin_level}"]({bbox_expr});'
            )
        if cities:
            filters.append(f'node["place"~"^(city|town|village|hamlet)$"]({bbox_expr});')
        if poi_historic:
            filters.append(f'nwr["historic"]["name"]({bbox_expr});')
        if poi_cultural:
            filters.append(
                f'nwr["tourism"~"^(museum|gallery|artwork|attraction)$"]["name"]({bbox_expr});'
            )
            filters.append(
                f'nwr["amenity"~"^(theatre|arts_centre|library|community_centre)$"]["name"]({bbox_expr});'
            )
        if poi_administrative:
            filters.append(f'nwr["office"="government"]["name"]({bbox_expr});')
            filters.append(
                f'nwr["amenity"~"^(townhall|courthouse|police|post_office)$"]["name"]({bbox_expr});'
            )
        if poi_natural:
            filters.append(
                f'nwr["natural"~"^(peak|volcano|cave_entrance|spring|waterfall|beach|bay|cliff)$"]["name"]({bbox_expr});'
            )
        if landuse:
            filters.append(
                f'way["landuse"~"^(forest|park|grass|meadow|residential|industrial|commercial|farmland|cemetery)$"]({bbox_expr});'
            )
            filters.append(
                f'way["natural"~"^(wood|water|scrub|wetland|sand|beach)$"]({bbox_expr});'
            )
            filters.append(f'way["leisure"="park"]({bbox_expr});')

        if not filters:
            raise RuntimeError("Select at least one supported OSM feature to import.")

        timeout = 120 if buildings else 90
        return (
            f"[out:json][timeout:{timeout}][maxsize:134217728];\n(\n"
            + "\n".join(filters)
            + f"\n);\nout center geom({bbox_expr});"
        )
