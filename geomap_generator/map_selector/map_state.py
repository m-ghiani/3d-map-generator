from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MapViewState:
    center_lat: float = 45.0
    center_lon: float = 9.0
    zoom: int = 5
    screen_w: int = 800
    screen_h: int = 600

    sel_start: Optional[tuple] = None
    sel_end: Optional[tuple] = None
    selecting: bool = False

    panning: bool = False
    pan_mouse: Optional[tuple] = None
    pan_world: Optional[tuple] = None

    # confirmed bbox: (lat_min, lon_min, lat_max, lon_max)
    bbox: Optional[tuple] = None

    textures: dict = field(default_factory=dict)  # (zoom, tx, ty) -> GPUTexture
    loading: set = field(default_factory=set)      # (zoom, tx, ty) being fetched
    failed: set = field(default_factory=set)       # (zoom, tx, ty) fetch failed
