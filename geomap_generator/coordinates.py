import math


class CoordinateTransformer:
    EARTH_RADIUS_KM = 6371.0

    @staticmethod
    def mercator_projection(lat: float, lon: float, center_lon: float = 0.0) -> tuple[float, float]:
        lon_rad = math.radians(lon - center_lon)
        lat_rad = math.radians(lat)
        x = CoordinateTransformer.EARTH_RADIUS_KM * lon_rad
        y = CoordinateTransformer.EARTH_RADIUS_KM * math.log(
            math.tan(math.pi / 4 + lat_rad / 2)
        )
        return x, y

    @staticmethod
    def inverse_mercator_projection(x_km: float, y_km: float, center_lon: float = 0.0) -> tuple[float, float]:
        R = CoordinateTransformer.EARTH_RADIUS_KM
        lon = math.degrees(x_km / R) + center_lon
        lat = math.degrees(2.0 * math.atan(math.exp(y_km / R)) - math.pi / 2.0)
        return lat, lon
