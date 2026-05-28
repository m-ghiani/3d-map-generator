import urllib.parse
import urllib.request

from .download_cache import cached_json

_SKETCHFAB_SEARCH_URL = "https://api.sketchfab.com/v3/search"
_SKETCHFAB_TTL_SECONDS = 7 * 24 * 60 * 60
_GOOGLE_3D_TILES_URL = "https://tile.googleapis.com/v1/3dtiles/root.json"


def google_photorealistic_3d_tiles_url(api_key: str) -> str:
    return f"{_GOOGLE_3D_TILES_URL}?key={urllib.parse.quote(api_key)}"


def sketchfab_search_url(query: str) -> str:
    params = {
        "type": "models",
        "q": query,
        "downloadable": "true",
        "archives_flavours": "false",
        "sort_by": "-likeCount",
    }
    return f"{_SKETCHFAB_SEARCH_URL}?{urllib.parse.urlencode(params)}"


def find_sketchfab_model(query: str, token: str = "") -> dict | None:
    cleaned = " ".join(str(query or "").split())
    if not cleaned:
        return None
    url = sketchfab_search_url(cleaned)

    def fetch() -> dict:
        headers = {
            "User-Agent": "GeoMapGenerator/2.0",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            import json

            return json.loads(response.read().decode("utf-8"))

    raw = cached_json("sketchfab", url, _SKETCHFAB_TTL_SECONDS, fetch)
    results = raw.get("results", []) if isinstance(raw, dict) else []
    if not results:
        return None
    for item in results:
        if isinstance(item, dict) and item.get("isDownloadable", False):
            return _model_metadata(item, cleaned, url)
    first = results[0]
    return _model_metadata(first, cleaned, url) if isinstance(first, dict) else None


def _model_metadata(item: dict, query: str, search_url: str) -> dict:
    uid = str(item.get("uid") or "")
    return {
        "query": query,
        "uid": uid,
        "name": str(item.get("name") or ""),
        "viewer_url": str(item.get("viewerUrl") or (f"https://sketchfab.com/3d-models/{uid}" if uid else "")),
        "download_api_url": f"https://api.sketchfab.com/v3/models/{uid}/download" if uid else "",
        "license": _license_label(item.get("license")),
        "author": _author_label(item.get("user")),
        "is_downloadable": bool(item.get("isDownloadable", False)),
        "search_url": search_url,
    }


def _license_label(value) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("slug") or value.get("uid") or "")
    return str(value or "")


def _author_label(value) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("username") or value.get("uid") or "")
    return ""
