from dataclasses import dataclass

VEHICLE_TYPES = ("car", "bike", "bus")


@dataclass(frozen=True)
class VehicleEvent:
    timestamp: int
    vehicle_type: str

    def __post_init__(self):
        if self.vehicle_type not in VEHICLE_TYPES:
            raise ValueError(
                f"unknown vehicle_type {self.vehicle_type!r}, "
                f"expected one of {VEHICLE_TYPES}"
            )
