class GeoMapError(RuntimeError):
    """Base error for expected GeoMap generation failures."""


class ValidationError(GeoMapError):
    """Raised when user input or selected options are invalid."""


class ProviderError(GeoMapError):
    """Raised when a data provider request fails in an expected way."""


class CancelledGeneration(GeoMapError):
    """Raised when the user cancels a running generation."""


class MeshBuildError(GeoMapError):
    """Raised when Blender mesh creation fails."""
