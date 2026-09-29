import argparse
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from common.events import VEHICLE_TYPES, VehicleEvent
from data.db import (
    DEFAULT_DB_PATH,
    clear_db,
    connect,
    fetch_counts,
    insert_counts,
)
from data.mock_feed import generate_events


def bucket_minute(timestamp: int) -> int:
    return timestamp - timestamp % 60


def aggregate_by_minute(
    events: Sequence[VehicleEvent],
) -> list[tuple[int, int, int, int]]:
    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(VEHICLE_TYPES, 0)
    )
    for event in events:
        buckets[bucket_minute(event.timestamp)][event.vehicle_type] += 1
    return [
        (ts, counts["car"], counts["bike"], counts["bus"])
        for ts, counts in sorted(buckets.items())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest mock vehicle detections into SQLite."
    )
    parser.add_argument("--count", type=int, default=200, help="mock event count")
    parser.add_argument("--seed", type=int, default=42, help="mock feed RNG seed")
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path"
    )
    args = parser.parse_args()

    events = generate_events(n=args.count, seed=args.seed)
    rows = aggregate_by_minute(events)

    conn = connect(args.db)
    try:
        clear_db(conn)
        insert_counts(conn, rows)
        stored = fetch_counts(conn)
    finally:
        conn.close()

    print(f"db: {args.db}")
    print(f"events: {len(events)} -> minute buckets: {len(stored)}")
    print()
    print(f"{'minute':<17}{'car':>6}{'bike':>6}{'bus':>6}")
    totals = [0, 0, 0]
    for ts, car, bike, bus in stored:
        totals[0] += car
        totals[1] += bike
        totals[2] += bus
        minute = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        print(f"{minute:<17}{car:>6}{bike:>6}{bus:>6}")
    print(f"{'TOTAL':<17}{totals[0]:>6}{totals[1]:>6}{totals[2]:>6}")


if __name__ == "__main__":
    main()
