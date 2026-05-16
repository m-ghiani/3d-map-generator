import json
from pathlib import Path


def token_help_lines(provider: str) -> list[str]:
    path = Path(__file__).with_name("provider_token_help.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    lines = data.get(provider, [])
    if isinstance(lines, str):
        return [lines]
    if not isinstance(lines, list):
        return []
    return [str(line) for line in lines if str(line).strip()]


def provider_quality(provider: str) -> dict[str, str]:
    path = Path(__file__).with_name("provider_quality.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    item = data.get("ARCGIS" if provider == "AUTO" else provider, {})
    if not isinstance(item, dict):
        return {}
    return {str(key): str(value) for key, value in item.items()}
