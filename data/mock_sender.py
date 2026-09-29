import argparse
import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime

from common.events import VehicleEvent
from data.mock_feed import TYPE_WEIGHTS


def generate_tick_events(rng: random.Random, now: int) -> list[VehicleEvent]:
    types = list(TYPE_WEIGHTS)
    weights = list(TYPE_WEIGHTS.values())
    return [
        VehicleEvent(
            timestamp=now,
            vehicle_type=rng.choices(types, weights=weights, k=1)[0],
        )
        for _ in range(rng.randint(0, 5))
    ]


def post_events(url: str, events: list[VehicleEvent]) -> dict:
    payload = json.dumps(
        [{"timestamp": e.timestamp, "vehicle_type": e.vehicle_type} for e in events]
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def format_ack(ack: dict) -> str:
    buckets = ", ".join(
        f"bucket {b['date']}"
        f" added(car+{b['added']['car']} bike+{b['added']['bike']}"
        f" bus+{b['added']['bus']})"
        f" totals(car={b['totals']['car']} bike={b['totals']['bike']}"
        f" bus={b['totals']['bus']})"
        for b in ack["buckets"]
    )
    return f"ack {ack['status']}: {ack['received']} written | {buckets}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mock Person-2 sender: POSTs random vehicle detections."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000/events")
    parser.add_argument(
        "--interval", type=float, default=5.0, help="seconds between ticks"
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed")
    parser.add_argument(
        "--ticks", type=int, default=0, help="stop after N ticks (0 = forever)"
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    tick = 0
    try:
        while True:
            tick += 1
            now = int(time.time())
            clock = datetime.now().strftime("%H:%M:%S")
            events = generate_tick_events(rng, now)
            if not events:
                print(f"{clock} tick {tick}: no detections")
            else:
                try:
                    ack = post_events(args.url, events)
                except urllib.error.HTTPError as exc:
                    print(
                        f"{clock} tick {tick}: sent {len(events)}"
                        f" -> server error {exc.code}"
                    )
                except OSError as exc:
                    print(
                        f"{clock} tick {tick}: sent {len(events)}"
                        f" -> POST failed ({exc})"
                    )
                else:
                    print(
                        f"{clock} tick {tick}: sent {len(events)}"
                        f" -> {format_ack(ack)}"
                    )
            if args.ticks and tick >= args.ticks:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("sender stopped")


if __name__ == "__main__":
    main()
