from dataclasses import dataclass

VEHICLE_TYPES = ("car", "bus", "truck", "motorcycle", "bicycle")


@dataclass(frozen=True)
class VehicleEvent:
    timestamp: int
    vehicle_type: str
    camera: str = "cam1"
    track_id: int | None = None
    confidence: float | None = None
    direction: str | None = None

    def __post_init__(self):
        if self.vehicle_type not in VEHICLE_TYPES:
            raise ValueError(
                f"unknown vehicle_type {self.vehicle_type!r}, "
                f"expected one of {VEHICLE_TYPES}"
            )
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be within 0..1")

    @property
    def minute(self) -> int:
        return self.timestamp // 60
