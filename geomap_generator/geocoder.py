import json
import urllib.parse
import urllib.request

from .exceptions import GeoMapError

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"


def search_within_bbox(
    query: str,
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Search for a place within the map bbox using Nominatim.

    bbox = (lat_min, lon_min, lat_max, lon_max)
    Returns (lat, lon) of best match.
    Raises GeoMapError if nothing is found.
    """
    la_min, lo_min, la_max, lo_max = bbox
    # Nominatim viewbox order: lon_min, lat_max, lon_max, lat_min
    viewbox = f"{lo_min},{la_max},{lo_max},{la_min}"
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": "1",
        "viewbox": viewbox,
        "bounded": "1",
    })
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read())
    except Exception as exc:
        raise GeoMapError(f"Geocoding request failed: {exc}") from exc

    if not results:
        raise GeoMapError(
            f"No results for '{query}' within the current map bounds. "
            "Try a less specific search or widen the map area."
        )
    return float(results[0]["lat"]), float(results[0]["lon"])
