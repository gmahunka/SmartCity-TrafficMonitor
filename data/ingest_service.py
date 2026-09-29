import argparse
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel

from common.events import VEHICLE_TYPES
from data.db import (
    DEFAULT_DB_PATH,
    clear_db,
    connect,
    fetch_buckets,
    fetch_counts,
    upsert_counts,
)


class EventIn(BaseModel):
    timestamp: int
    vehicle_type: Literal[VEHICLE_TYPES]


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = connect(app.state.db_path)
    try:
        clear_db(conn)
    finally:
        conn.close()
    print(f"[ingest] db cleared and ready: {app.state.db_path}")
    yield


app = FastAPI(title="Traffic ingest", lifespan=lifespan)
app.state.db_path = DEFAULT_DB_PATH


def bucket_minute(timestamp: int) -> int:
    return timestamp - timestamp % 60


def format_datetime(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


@app.post("/events")
def post_events(events: list[EventIn], request: Request):
    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(VEHICLE_TYPES, 0)
    )
    for event in events:
        buckets[bucket_minute(event.timestamp)][event.vehicle_type] += 1
    rows = [
        (ts, counts["car"], counts["bike"], counts["bus"])
        for ts, counts in sorted(buckets.items())
    ]

    conn = connect(request.app.state.db_path)
    try:
        upsert_counts(conn, rows)
        totals = fetch_buckets(conn, [ts for ts, _, _, _ in rows])
    finally:
        conn.close()
    totals_by_ts = {ts: (car, bike, bus) for ts, car, bike, bus in totals}

    for ts, car, bike, bus in rows:
        tcar, tbike, tbus = totals_by_ts[ts]
        print(
            f"[ingest] committed {format_datetime(ts)}:"
            f" added car+{car} bike+{bike} bus+{bus}"
            f" -> totals car={tcar} bike={tbike} bus={tbus}"
        )

    return {
        "status": "ok",
        "received": len(events),
        "buckets": [
            {
                "timestamp": ts,
                "date": format_datetime(ts),
                "added": {"car": car, "bike": bike, "bus": bus},
                "totals": dict(zip(VEHICLE_TYPES, totals_by_ts[ts])),
            }
            for ts, car, bike, bus in rows
        ],
    }


@app.get("/counts")
def get_counts(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        rows = fetch_counts(conn)
    finally:
        conn.close()
    return {
        "buckets": [
            {
                "timestamp": ts,
                "date": format_datetime(ts),
                "car": car,
                "bike": bike,
                "bus": bus,
            }
            for ts, car, bike, bus in rows
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vehicle event ingest HTTP service."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path"
    )
    args = parser.parse_args()
    app.state.db_path = args.db
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
