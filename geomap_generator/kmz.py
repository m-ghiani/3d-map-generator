import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .download_cache import cached_bytes
from .models import BoundingBox, GeoMapData, OsmNode, OsmPoint, OsmWay

_PACKAGE_CATALOG = Path(__file__).with_name("kmz_catalog.json")
_USER_CATALOG = Path.home() / ".geomap_generator" / "kmz_catalog.json"
_KMZ_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_KML_NAMESPACE = "{http://www.opengis.net/kml/2.2}"


def load_kmz_catalog() -> list[dict]:
    entries: list[dict] = []
    for path in (_PACKAGE_CATALOG, _USER_CATALOG):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            entries.extend(item for item in data if isinstance(item, dict))
    return entries


def kmz_entries_for_bbox(bbox: BoundingBox | None) -> list[dict]:
    entries = []
    for entry in load_kmz_catalog():
        if not _valid_entry(entry) or _entry_type(entry) not in {"kmz", "kml"}:
            continue
        entry_bbox = _entry_bbox(entry)
        if bbox is None or entry_bbox is None or _intersects(bbox, entry_bbox):
            entries.append(entry)
    return entries


def asset_entries_for_bbox(bbox: BoundingBox | None) -> list[dict]:
    entries = []
    for entry in load_kmz_catalog():
        if not _valid_entry(entry):
            continue
        entry_type = _entry_type(entry)
        if entry_type not in {"kmz", "kml", "glb", "gltf"}:
            continue
        entry_bbox = _entry_bbox(entry)
        if bbox is None or entry_bbox is None or _intersects(bbox, entry_bbox):
            entries.append(entry)
    return entries


def asset_entry_location(entry: dict) -> tuple[float, float] | None:
    try:
        if entry.get("lat") is not None and entry.get("lon") is not None:
            return float(entry["lat"]), float(entry["lon"])
        bbox = _entry_bbox(entry)
        if bbox is not None:
            return (
                (bbox.min_lat + bbox.max_lat) / 2.0,
                (bbox.min_lon + bbox.max_lon) / 2.0,
            )
    except (TypeError, ValueError):
        return None
    return None


def kmz_enum_items(_self, context) -> list[tuple[str, str, str]]:
    bbox = current_bbox_from_context(context, resolve_place=False)
    items = []
    for entry in kmz_entries_for_bbox(bbox):
        entry_id = str(entry.get("id") or entry.get("url"))
        name = str(entry.get("name") or entry_id)
        description = str(entry.get("description") or entry.get("url") or "")
        items.append((entry_id, name[:64], description[:256]))
    if items:
        return items
    return [("NONE", "No KMZ available", "No KMZ catalog entry intersects the current area")]


def entry_by_id(entry_id: str, bbox: BoundingBox | None = None) -> dict | None:
    if not entry_id or entry_id == "NONE":
        return None
    for entry in kmz_entries_for_bbox(bbox):
        candidate_id = str(entry.get("id") or entry.get("url"))
        if candidate_id == entry_id:
            return entry
    return None


def current_bbox_from_context(context, resolve_place: bool = False) -> BoundingBox | None:
    bbox = current_map_bbox()
    if bbox:
        return bbox

    props = getattr(getattr(context, "scene", None), "geomap_props", None)
    if props is None:
        return None
    if getattr(props, "input_mode", "COUNTRY") == "COORDS":
        return BoundingBox.from_corners(
            props.latitude,
            props.longitude,
            props.latitude2,
            props.longitude2,
        )
    if resolve_place and getattr(props, "country_region", ""):
        from .overpass import OsmApiClient

        return OsmApiClient().resolve_bbox(props.country_region)
    return None


def current_map_bbox() -> BoundingBox | None:
    try:
        import bpy

        root = bpy.data.collections.get("GeoMap")
        raw_bbox = root.get("geomap_bbox") if root else None
    except Exception:
        raw_bbox = None
    if not raw_bbox:
        return None
    try:
        values = [float(value) for value in str(raw_bbox).split(",")]
    except ValueError:
        return None
    if len(values) != 4:
        return None
    return BoundingBox(values[0], values[1], values[2], values[3])


def download_kmz(entry: dict) -> Path:
    path = download_catalog_asset(entry, namespace="kmz")
    if path.suffix.lower() in {".kmz", ".kml"}:
        return path
    raise RuntimeError("Catalog entry is not a KMZ/KML asset")


def download_catalog_asset(entry: dict, namespace: str = "assets") -> Path:
    source = str(entry.get("url") or entry.get("path") or "")
    if not source:
        raise RuntimeError("Catalog entry has no URL or path")
    if not source.startswith(("http://", "https://")):
        path = Path(source).expanduser()
        if not path.exists():
            raise RuntimeError(f"Catalog asset file not found: {path}")
        return path

    def fetch() -> bytes:
        req = urllib.request.Request(source, headers={"User-Agent": "GeoMapGenerator/2.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()

    data = cached_bytes(namespace, source, _KMZ_CACHE_TTL_SECONDS, fetch)
    suffix = _entry_suffix(entry, source)
    cache_dir = Path(tempfile.gettempdir()) / "geomap_generator" / namespace
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{_safe_filename(str(entry.get('id') or Path(source).stem))}{suffix}"
    target.write_bytes(data)
    return target


def parse_kmz(path: Path, bbox: BoundingBox) -> GeoMapData:
    kml_bytes = _extract_kml(path)
    root = ElementTree.fromstring(kml_bytes)
    ways: list[OsmWay] = []
    points: list[OsmPoint] = []
    next_id = -1

    for placemark in _iter_by_local_name(root, "Placemark"):
        name = _child_text(placemark, "name") or "KMZ feature"
        tags = {"source": "kmz", "name": name}
        for point in _iter_by_local_name(placemark, "Point"):
            coords = _first_coordinates(point)
            if not coords:
                continue
            lon, lat = coords[0]
            if _contains_point(bbox, lat, lon):
                points.append(
                    OsmPoint(
                        id=next_id,
                        lat=lat,
                        lon=lon,
                        name=name,
                        category="kmz",
                        osm_type="kmz",
                        tags=dict(tags),
                    )
                )
                next_id -= 1
        for line in _iter_by_local_name(placemark, "LineString"):
            nodes = _nodes_from_coordinates(_first_coordinates(line), bbox, next_id)
            if len(nodes) >= 2:
                ways.append(OsmWay(id=next_id, geometry=nodes, tags=dict(tags)))
                next_id -= 1
        for polygon in _iter_by_local_name(placemark, "Polygon"):
            ring = _first_child_by_local_name(polygon, "outerBoundaryIs")
            ring = _first_child_by_local_name(ring, "LinearRing") if ring is not None else None
            nodes = _nodes_from_coordinates(_first_coordinates(ring), bbox, next_id)
            if len(nodes) >= 3:
                if nodes[0].lat != nodes[-1].lat or nodes[0].lon != nodes[-1].lon:
                    nodes.append(nodes[0])
                poly_tags = dict(tags)
                poly_tags["geomap_polygon"] = "yes"
                ways.append(OsmWay(id=next_id, geometry=nodes, tags=poly_tags))
                next_id -= 1

    return GeoMapData(ways=ways, points=points, bbox=bbox)


def catalog_paths() -> tuple[Path, Path]:
    return _PACKAGE_CATALOG, _USER_CATALOG


def _valid_entry(entry: dict) -> bool:
    return bool((entry.get("url") or entry.get("path")) and (entry.get("id") or entry.get("name")))


def _entry_type(entry: dict) -> str:
    explicit = str(entry.get("type") or entry.get("asset_type") or "").lower().strip(".")
    if explicit:
        return explicit
    source = str(entry.get("url") or entry.get("path") or "").lower()
    suffix = Path(source.split("?", 1)[0]).suffix.lower().strip(".")
    return suffix or "kmz"


def _entry_suffix(entry: dict, source: str) -> str:
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix:
        return suffix
    entry_type = _entry_type(entry)
    return f".{entry_type}" if entry_type else ".bin"


def _entry_bbox(entry: dict) -> BoundingBox | None:
    bbox = entry.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        return BoundingBox(
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
    except (TypeError, ValueError):
        return None


def _intersects(a: BoundingBox, b: BoundingBox) -> bool:
    return not (
        a.max_lat < b.min_lat
        or a.min_lat > b.max_lat
        or a.max_lon < b.min_lon
        or a.min_lon > b.max_lon
    )


def _contains_point(bbox: BoundingBox, lat: float, lon: float) -> bool:
    return bbox.min_lat <= lat <= bbox.max_lat and bbox.min_lon <= lon <= bbox.max_lon


def _extract_kml(path: Path) -> bytes:
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
            if not names:
                raise RuntimeError("KMZ archive contains no KML file")
            return archive.read(names[0])
    return path.read_bytes()


def _iter_by_local_name(node, local_name: str):
    for child in node.iter():
        if _local_name(child.tag) == local_name:
            yield child


def _first_child_by_local_name(node, local_name: str):
    if node is None:
        return None
    for child in node.iter():
        if _local_name(child.tag) == local_name:
            return child
    return None


def _child_text(node, local_name: str) -> str | None:
    child = _first_child_by_local_name(node, local_name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _first_coordinates(node) -> list[tuple[float, float]]:
    text = _child_text(node, "coordinates") if node is not None else None
    if not text:
        return []
    coords = []
    for raw in text.replace("\n", " ").replace("\t", " ").split():
        parts = raw.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append((lon, lat))
    return coords


def _nodes_from_coordinates(
    coordinates: list[tuple[float, float]],
    bbox: BoundingBox,
    feature_id: int,
) -> list[OsmNode]:
    nodes = []
    for index, (lon, lat) in enumerate(coordinates):
        if _contains_point(bbox, lat, lon):
            nodes.append(OsmNode(id=feature_id * 10_000 - index, lat=lat, lon=lon))
    return nodes


def _local_name(tag: str) -> str:
    if tag.startswith(_KML_NAMESPACE):
        return tag[len(_KML_NAMESPACE) :]
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _safe_filename(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return safe.strip("_") or "kmz"
