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
