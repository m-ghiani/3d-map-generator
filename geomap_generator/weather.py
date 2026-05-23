import json
import urllib.request
from dataclasses import dataclass

from .exceptions import ProviderError
from .models import BoundingBox

_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_OPENWEATHERMAP_URL = "https://api.openweathermap.org/data/2.5/weather"
_WEATHERAPI_URL = "https://api.weatherapi.com/v1/current.json"


@dataclass
class WeatherPoint:
    lat: float
    lon: float
    temperature: float = 0.0   # Celsius
    wind_speed: float = 0.0    # km/h
    wind_dir: float = 0.0      # meteorological degrees: 0=from N, 90=from E
    precipitation: float = 0.0  # mm
    condition: str = "unknown"


class WeatherClient:
    def fetch_grid(
        self,
        bbox: BoundingBox,
        grid_size: int = 3,
        provider: str = "AUTO",
        openweathermap_token: str = "",
        weatherapi_token: str = "",
    ) -> list[WeatherPoint]:
        n = max(1, grid_size)
        if n == 1:
            lats = [(bbox.min_lat + bbox.max_lat) / 2]
            lons = [(bbox.min_lon + bbox.max_lon) / 2]
        else:
            lat_span = bbox.max_lat - bbox.min_lat
            lon_span = bbox.max_lon - bbox.min_lon
            lats = [bbox.min_lat + lat_span * i / (n - 1) for i in range(n)]
            lons = [bbox.min_lon + lon_span * i / (n - 1) for i in range(n)]

        points: list[WeatherPoint] = []
        for lat in lats:
            for lon in lons:
                try:
                    pt = self._fetch_point(
                        lat, lon, provider, openweathermap_token, weatherapi_token
                    )
                    points.append(pt)
                except Exception:
                    pass
        return points

    def _fetch_point(
        self,
        lat: float,
        lon: float,
        provider: str,
        owm_token: str,
        wapi_token: str,
    ) -> WeatherPoint:
        if provider == "OPENWEATHERMAP":
            return self._fetch_openweathermap(lat, lon, owm_token)
        if provider == "WEATHERAPI":
            return self._fetch_weatherapi(lat, lon, wapi_token)
        return self._fetch_open_meteo(lat, lon)

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_open_meteo(self, lat: float, lon: float) -> WeatherPoint:
        params = (
            f"latitude={lat:.5f}&longitude={lon:.5f}"
            "&current=temperature_2m,wind_speed_10m,wind_direction_10m"
            ",precipitation,weather_code&timezone=auto"
        )
        data = self._get(f"{_OPEN_METEO_URL}?{params}")
        cur = data.get("current", {})
        return WeatherPoint(
            lat=lat,
            lon=lon,
            temperature=float(cur.get("temperature_2m", 0)),
            wind_speed=float(cur.get("wind_speed_10m", 0)),
            wind_dir=float(cur.get("wind_direction_10m", 0)),
            precipitation=float(cur.get("precipitation", 0)),
            condition=_wmo_condition(
                int(cur.get("weather_code", cur.get("weathercode", 0)))
            ),
        )

    def _fetch_openweathermap(self, lat: float, lon: float, api_key: str) -> WeatherPoint:
        if not api_key:
            raise ProviderError("OpenWeatherMap API key required")
        data = self._get(
            f"{_OPENWEATHERMAP_URL}?lat={lat:.5f}&lon={lon:.5f}"
            f"&appid={api_key}&units=metric"
        )
        wind = data.get("wind", {})
        rain = data.get("rain", {})
        return WeatherPoint(
            lat=lat,
            lon=lon,
            temperature=float(data.get("main", {}).get("temp", 0)),
            wind_speed=float(wind.get("speed", 0)) * 3.6,  # m/s → km/h
            wind_dir=float(wind.get("deg", 0)),
            precipitation=float(rain.get("1h", 0)),
            condition=data.get("weather", [{}])[0].get("main", "unknown").lower(),
        )

    def _fetch_weatherapi(self, lat: float, lon: float, api_key: str) -> WeatherPoint:
        if not api_key:
            raise ProviderError("WeatherAPI key required")
        data = self._get(f"{_WEATHERAPI_URL}?key={api_key}&q={lat:.5f},{lon:.5f}")
        cur = data.get("current", {})
        return WeatherPoint(
            lat=lat,
            lon=lon,
            temperature=float(cur.get("temp_c", 0)),
            wind_speed=float(cur.get("wind_kph", 0)),
            wind_dir=float(cur.get("wind_degree", 0)),
            precipitation=float(cur.get("precip_mm", 0)),
            condition=cur.get("condition", {}).get("text", "unknown").lower(),
        )


def _wmo_condition(code: int) -> str:
    if code == 0:
        return "clear"
    if code <= 3:
        return "cloudy"
    if code <= 49:
        return "fog"
    if code <= 67:
        return "rain"
    if code <= 77:
        return "snow"
    if code <= 82:
        return "shower"
    return "storm"
