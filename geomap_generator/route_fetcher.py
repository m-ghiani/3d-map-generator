import json
import urllib.request

from .exceptions import GeoMapError

_OSRM_BASE = "https://router.project-osrm.org/route/v1"
_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"


def fetch_route(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    profile: str = "driving",
) -> list[tuple[float, float]]:
    """Fetch a driving/walking/cycling route from OSRM.

    Returns [(lat, lon), ...] waypoints in route order.
    """
    url = (
        f"{_OSRM_BASE}/{profile}/{lon1},{lat1};{lon2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        raise GeoMapError(f"Route fetch failed: {exc}") from exc

    if data.get("code") != "Ok":
        msg = data.get("message") or data.get("code") or "unknown error"
        raise GeoMapError(f"OSRM routing error: {msg}")

    routes = data.get("routes")
    if not routes:
        raise GeoMapError("OSRM returned no routes for the given coordinates")

    # GeoJSON coordinates are [lon, lat] — invert to (lat, lon)
    return [(c[1], c[0]) for c in routes[0]["geometry"]["coordinates"]]


def fetch_route_multi(
    waypoints: list[tuple[float, float]],
    profile: str = "driving",
) -> list[tuple[float, float]]:
    """Fetch a multi-stop route from OSRM.

    waypoints: [(lat, lon), ...] with at least two points.
    Returns [(lat, lon), ...] in route order.
    """
    if len(waypoints) < 2:
        raise GeoMapError("Need at least 2 waypoints for routing")
    coords = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = f"{_OSRM_BASE}/{profile}/{coords}?overview=full&geometries=geojson"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        raise GeoMapError(f"Route fetch failed: {exc}") from exc

    if data.get("code") != "Ok":
        msg = data.get("message") or data.get("code") or "unknown error"
        raise GeoMapError(f"OSRM routing error: {msg}")

    routes = data.get("routes")
    if not routes:
        raise GeoMapError("OSRM returned no routes for the given coordinates")

    return [(c[1], c[0]) for c in routes[0]["geometry"]["coordinates"]]
