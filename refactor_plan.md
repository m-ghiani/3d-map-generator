# Refactor Plan: SOLID + Multi-file Split

## Context

Current `__init__.py` (831 lines) violates every SOLID principle.
OSM data fetched via **Overpass API** (correct choice — OSM API v0.6 is for editing/individual lookups,
0.25° bbox limit makes it unsuitable for bulk queries). But current Overpass queries are too limited:
only `natural=coastline` via way, admin boundaries via broken area query, relations entirely ignored.

---

## Target Structure

```
geomap_generator/
├── __init__.py       # bl_info + register/unregister only (~30 lines)
├── models.py         # Typed dataclasses: OsmNode, OsmWay, BoundingBox, GeoMapData
├── progress.py       # Thread-safe ProgressTracker with Lock + cancel Event
├── coordinates.py    # CoordinateTransformer (pure math, no Blender deps)
├── overpass.py       # OverpassQueryBuilder + OverpassClient (fetch + parse)
├── mesh_builder.py   # Protocol MeshBuilder + concrete builders per feature type
├── properties.py     # GeoMapProperties (bpy.props PropertyGroup)
├── panels.py         # GeoMapPanel, GeoMapProgressPanel
└── operators.py      # GeoMapGenerateOperator, GeoMapCancelOperator
```

---

## SOLID Fixes

### S — Single Responsibility

| Was | Fix |
|-----|-----|
| `GeoMapGenerateOperator` does validation + threading + fetch + mesh + UI | Split: operator orchestrates only; mesh → `MeshBuilder`; fetch → `OverpassClient` |
| `ProgressTracker` holds logs + progress + result + cancel signal | Add `_cancel_event: threading.Event` (separate from `is_running`) |
| `OverpassClient` builds query strings AND executes HTTP AND parses JSON | Split into `OverpassQueryBuilder` + `OverpassClient` |

### O — Open/Closed

Current: adding rivers/roads requires modifying `OverpassClient._fetch_coastline_data`.

Fix — `OverpassQueryBuilder` returns query strings per feature type:
```python
class OverpassQueryBuilder:
    def coastlines(self, bbox: BoundingBox) -> str: ...
    def rivers(self, bbox: BoundingBox) -> str: ...
    def roads(self, bbox: BoundingBox) -> str: ...
    def admin_boundary(self, name: str, level: int = 2) -> str: ...
```

Fix — `MeshBuilder` protocol lets new geometry types extend without touching existing code:
```python
class MeshBuilder(Protocol):
    def build(self, ways: list[OsmWay], bbox: BoundingBox, scale: float) -> tuple[list, list]: ...

class LineMeshBuilder:    # coastlines, rivers, roads → edge loops
    ...
class FallbackMeshBuilder:  # rectangle plane
    ...
```

### L — Liskov Substitution

`FallbackMeshBuilder` and `LineMeshBuilder` both satisfy `MeshBuilder` protocol.
`OverpassClient` accepts any `OverpassQueryBuilder` — can be mocked in tests.

### I — Interface Segregation

`ProgressTracker` split into two roles:
- `ProgressReporter` (log, set_status) — used by workers
- `ProgressReader` (status, progress, logs, error) — used by UI panels

Single class implements both; panels only import the reading interface.

### D — Dependency Inversion

`GeoMapGenerateOperator` depends on `OverpassClient` and `MeshBuilder` abstractions,
not concrete implementations. Builder injected by factory based on feature flags.

---

## OSM Data Model (models.py)

```python
@dataclass
class OsmNode:
    id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)

@dataclass
class OsmWay:
    id: int
    geometry: list[OsmNode]   # always resolved (out geom guaranteed)
    tags: dict[str, str] = field(default_factory=dict)

@dataclass
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @classmethod
    def from_corners(cls, lat_a, lon_a, lat_b, lon_b) -> 'BoundingBox': ...
    def to_overpass(self) -> str:  # "min_lat,min_lon,max_lat,max_lon"
        ...

@dataclass
class GeoMapData:
    ways: list[OsmWay]
    bbox: BoundingBox
```

Eliminates redundant `vertices` dict (no longer needed since `out geom` always used).

---

## Overpass Query Fixes

### Problem: Admin boundaries are relations, not ways

Current broken query:
```
area[name="Italy"]["admin_level"="2"];
relation(area)[boundary="administrative"][admin_level="2"];
out geom;
```

Fixed query (relations with full geometry):
```
[out:json][timeout:25];
relation["name"="Italy"]["boundary"="administrative"]["admin_level"="2"];
out geom;
```

Relations return `members` array with `geometry` per member — need parser update.

### Problem: `out geom` returns geometry differently for ways vs relations

- **Way** with `out geom`: `way.geometry = [{lat, lon}, ...]`
- **Relation** with `out geom`: `relation.members = [{type, role, geometry:[{lat,lon},...]}]`

Parser must handle both. Extract outer-role members for boundary outline.

### Feature query map

| Feature flag | Overpass filter |
|---|---|
| coastlines | `way["natural"="coastline"](bbox)` |
| rivers | `way["waterway"="river"](bbox)` + `way["waterway"="stream"](bbox)` |
| roads | `way["highway"~"motorway|trunk|primary"](bbox)` |
| admin boundary | `relation["boundary"="administrative"]["admin_level"="2"]["name"=X]` |

---

## Threading Fixes (progress.py)

```python
class ProgressTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()  # replaces is_running=False hack
        ...

    def request_cancel(self):
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()
```

Workers call `tracker.is_cancelled()` instead of checking `tracker.is_running`.
Cancellation no longer conflated with "finished normally".

---

## What stays the same

- Blender registration pattern (register/unregister in `__init__.py`)
- Modal operator + timer (0.5s) for UI redraw — Blender threading constraint unchanged
- `bpy.app.timers.register` for main-thread mesh creation
- `CoordinateTransformer.mercator_projection` math — correct, keep as-is
- Fallback rectangular bbox when API fails

---

## Out of scope (Phase 3+)

- SRTM elevation integration
- Texture mapping
- Expanding hardcoded REGIONS beyond 5 countries (separate task)
