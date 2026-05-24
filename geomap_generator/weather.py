import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .exceptions import ProviderError
from .models import BoundingBox, OsmPoint

_USER_AGENT = "GeoMapGenerator/2.0 (Blender Addon; m.ghiani@gmail.com)"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_OPENWEATHERMAP_URL = "https://api.openweathermap.org/data/2.5/weather"
_OPENWEATHERMAP_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_WEATHERAPI_URL = "https://api.weatherapi.com/v1/current.json"
_WEATHERAPI_FORECAST_URL = "https://api.weatherapi.com/v1/forecast.json"


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
        forecast_day: int = 0,
    ) -> list[WeatherPoint]:
        sample_points = self._grid_samples(bbox, grid_size)
        return self.fetch_samples(
            sample_points,
            provider=provider,
            openweathermap_token=openweathermap_token,
            weatherapi_token=weatherapi_token,
            forecast_day=forecast_day,
        )

    def fetch_for_granularity(
        self,
        bbox: BoundingBox,
        osm_points: list[OsmPoint],
        granularity: str,
        grid_size: int = 3,
        provider: str = "AUTO",
        openweathermap_token: str = "",
        weatherapi_token: str = "",
        forecast_day: int = 0,
    ) -> list[WeatherPoint]:
        samples = self._samples_for_granularity(
            bbox, osm_points, granularity, grid_size
        )
        return self.fetch_samples(
            samples,
            provider=provider,
            openweathermap_token=openweathermap_token,
            weatherapi_token=weatherapi_token,
            forecast_day=forecast_day,
        )

    def fetch_samples(
        self,
        samples: list[tuple[float, float]],
        provider: str = "AUTO",
        openweathermap_token: str = "",
        weatherapi_token: str = "",
        forecast_day: int = 0,
    ) -> list[WeatherPoint]:
        points: list[WeatherPoint] = []
        for lat, lon in samples:
            try:
                points.append(self._fetch_point(
                    lat, lon, provider, openweathermap_token, weatherapi_token,
                    forecast_day,
                ))
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
        forecast_day: int,
    ) -> WeatherPoint:
        if provider == "OPENWEATHERMAP":
            return self._fetch_openweathermap(lat, lon, owm_token, forecast_day)
        if provider == "WEATHERAPI":
            return self._fetch_weatherapi(lat, lon, wapi_token, forecast_day)
        return self._fetch_open_meteo(lat, lon, forecast_day)

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_open_meteo(self, lat: float, lon: float, forecast_day: int) -> WeatherPoint:
        day = max(0, min(int(forecast_day), 7))
        if day > 0:
            params = (
                f"latitude={lat:.5f}&longitude={lon:.5f}"
                "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m"
                ",precipitation,weather_code&timezone=auto"
                f"&forecast_days={day + 1}"
            )
            data = self._get(f"{_OPEN_METEO_URL}?{params}")
            hourly = data.get("hourly", {})
            idx = min(day * 24 + 12, len(hourly.get("time", [])) - 1)
            if idx >= 0:
                return WeatherPoint(
                    lat=lat,
                    lon=lon,
                    temperature=float(_at(hourly.get("temperature_2m"), idx)),
                    wind_speed=float(_at(hourly.get("wind_speed_10m"), idx)),
                    wind_dir=float(_at(hourly.get("wind_direction_10m"), idx)),
                    precipitation=float(_at(hourly.get("precipitation"), idx)),
                    condition=_wmo_condition(int(_at(hourly.get("weather_code"), idx))),
                )
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

    def _fetch_openweathermap(
        self, lat: float, lon: float, api_key: str, forecast_day: int
    ) -> WeatherPoint:
        if not api_key:
            raise ProviderError("OpenWeatherMap API key required")
        if forecast_day > 0:
            data = self._get(
                f"{_OPENWEATHERMAP_FORECAST_URL}?lat={lat:.5f}&lon={lon:.5f}"
                f"&appid={api_key}&units=metric"
            )
            entries = data.get("list", [])
            target = datetime.now(timezone.utc) + timedelta(days=forecast_day)
            best = min(
                entries,
                key=lambda item: abs(
                    datetime.fromtimestamp(item.get("dt", 0), timezone.utc)
                    - target.replace(hour=12, minute=0, second=0, microsecond=0)
                ),
                default=None,
            )
            if best:
                return self._openweathermap_point(lat, lon, best)
        data = self._get(
            f"{_OPENWEATHERMAP_URL}?lat={lat:.5f}&lon={lon:.5f}"
            f"&appid={api_key}&units=metric"
        )
        return self._openweathermap_point(lat, lon, data)

    def _openweathermap_point(self, lat: float, lon: float, data: dict) -> WeatherPoint:
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

    def _fetch_weatherapi(
        self, lat: float, lon: float, api_key: str, forecast_day: int
    ) -> WeatherPoint:
        if not api_key:
            raise ProviderError("WeatherAPI key required")
        if forecast_day > 0:
            days = max(1, min(int(forecast_day) + 1, 8))
            data = self._get(
                f"{_WEATHERAPI_FORECAST_URL}?key={api_key}&q={lat:.5f},{lon:.5f}"
                f"&days={days}"
            )
            forecast_days = data.get("forecast", {}).get("forecastday", [])
            if not forecast_days:
                raise ProviderError("WeatherAPI returned no forecast days")
            day = forecast_days[min(forecast_day, len(forecast_days) - 1)]
            hours = day.get("hour", [])
            cur = hours[12] if len(hours) > 12 else (hours[0] if hours else {})
        else:
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

    def _grid_samples(self, bbox: BoundingBox, grid_size: int) -> list[tuple[float, float]]:
        n = max(1, grid_size)
        if n == 1:
            return [((bbox.min_lat + bbox.max_lat) / 2, (bbox.min_lon + bbox.max_lon) / 2)]
        lat_span = bbox.max_lat - bbox.min_lat
        lon_span = bbox.max_lon - bbox.min_lon
        lats = [bbox.min_lat + lat_span * i / (n - 1) for i in range(n)]
        lons = [bbox.min_lon + lon_span * i / (n - 1) for i in range(n)]
        return [(lat, lon) for lat in lats for lon in lons]

    def _samples_for_granularity(
        self,
        bbox: BoundingBox,
        osm_points: list[OsmPoint],
        granularity: str,
        grid_size: int,
    ) -> list[tuple[float, float]]:
        if granularity == "GRID":
            return self._grid_samples(bbox, grid_size)

        named = [p for p in osm_points if p.name]
        cities = [p for p in named if p.category == "city"]
        if granularity == "MAIN_CITY":
            source = cities[:1]
        elif granularity == "CITIES":
            source = cities
        else:
            source = named

        samples = []
        seen = set()
        for point in source:
            key = (round(point.lat, 5), round(point.lon, 5))
            if key in seen:
                continue
            seen.add(key)
            samples.append((point.lat, point.lon))
        return samples or self._grid_samples(bbox, 1)


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


def _at(values, index: int, default=0):
    if not isinstance(values, list) or not values:
        return default
    return values[max(0, min(index, len(values) - 1))]
