from .exceptions import ValidationError
from .models import BoundingBox
from .settings import GenerationSettings

_MAX_BBOX_DEGREES = 12.0
_MAX_TEXTURE_PIXELS = 8192


def validate_settings(settings: GenerationSettings) -> None:
    if settings.input_mode == "COUNTRY" and not settings.country_region.strip():
        raise ValidationError("Enter a place, area, landmark, city, region, or country name")

    if settings.input_mode == "COORDS":
        _validate_coordinate_bounds(settings)
        bbox = BoundingBox.from_corners(
            settings.latitude,
            settings.longitude,
            settings.latitude2,
            settings.longitude2,
        )
        _validate_bbox_size(bbox, settings)

    if not _has_selected_output(settings):
        raise ValidationError("Select at least one feature or Satellite Bounding Box")

    if settings.satellite_resolution > _MAX_TEXTURE_PIXELS:
        raise ValidationError(f"Satellite resolution must be <= {_MAX_TEXTURE_PIXELS}")


def _validate_coordinate_bounds(settings: GenerationSettings) -> None:
    if not (-90 <= settings.latitude <= 90 and -90 <= settings.latitude2 <= 90):
        raise ValidationError("Latitudes must be between -90 and 90")
    if not (-180 <= settings.longitude <= 180 and -180 <= settings.longitude2 <= 180):
        raise ValidationError("Longitudes must be between -180 and 180")


def _validate_bbox_size(bbox: BoundingBox, settings: GenerationSettings) -> None:
    if max(bbox.lat_span(), bbox.lon_span()) <= _MAX_BBOX_DEGREES:
        return
    if settings.quality_preset == "LARGE_AREA" or settings.detail_level == "LOW":
        return
    raise ValidationError(
        "Bounding box is large. Use the Large Area preset or lower the detail level."
    )


def _has_selected_output(settings: GenerationSettings) -> bool:
    return any(
        (
            settings.import_coast,
            settings.import_rivers,
            settings.import_roads,
            settings.import_admin,
            settings.import_satellite,
            settings.import_relief,
        )
    )
