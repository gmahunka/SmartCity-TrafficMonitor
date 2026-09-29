import random
import time

from common.events import VehicleEvent

TYPE_WEIGHTS = {"car": 0.6, "bike": 0.3, "bus": 0.1}
SPAN_SECONDS = 600


def generate_events(
    n: int = 200,
    seed: int = 42,
    span_seconds: int = SPAN_SECONDS,
) -> list[VehicleEvent]:
    rng = random.Random(seed)
    now = int(time.time())
    start = now - span_seconds
    types = list(TYPE_WEIGHTS)
    weights = list(TYPE_WEIGHTS.values())
    events = [
        VehicleEvent(
            timestamp=rng.randint(start, now),
            vehicle_type=rng.choices(types, weights=weights, k=1)[0],
        )
        for _ in range(n)
    ]
    events.sort(key=lambda event: event.timestamp)
    return events
