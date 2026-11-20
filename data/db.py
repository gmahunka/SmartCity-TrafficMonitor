import json
import sqlite3
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "traffic.db"
RICH_TYPES = ("car", "bus", "truck", "motorcycle", "bicycle")
_COUNT_COLUMNS = {
    "car": "car_count",
    "bus": "bus_count",
    "truck": "truck_count",
    "motorcycle": "motorcycle_count",
    "bicycle": "bicycle_count",
}

_CREATE_TABLE = """
CREATE TABLE traffic_counts (
    timestamp INTEGER PRIMARY KEY,
    car_count INTEGER NOT NULL DEFAULT 0,
    bus_count INTEGER NOT NULL DEFAULT 0,
    truck_count INTEGER NOT NULL DEFAULT 0,
    motorcycle_count INTEGER NOT NULL DEFAULT 0,
    bicycle_count INTEGER NOT NULL DEFAULT 0
)
"""
_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS traffic_events (
    id INTEGER PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    vehicle_type TEXT NOT NULL,
    camera TEXT NOT NULL,
    track_id INTEGER,
    confidence REAL,
    direction TEXT
)
"""
_CREATE_FORECASTS = """
CREATE TABLE IF NOT EXISTS traffic_forecasts (
    camera TEXT NOT NULL,
    cutoff INTEGER NOT NULL,
    predicted_count REAL NOT NULL,
    horizon_buckets INTEGER NOT NULL DEFAULT 3,
    predicted_buckets TEXT,
    model TEXT,
    PRIMARY KEY (camera, cutoff)
)
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS traffic_counts ("
        "timestamp INTEGER PRIMARY KEY, car_count INTEGER NOT NULL DEFAULT 0,"
        "bus_count INTEGER NOT NULL DEFAULT 0)"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(traffic_counts)")}
    for column in ("truck_count", "motorcycle_count", "bicycle_count"):
        if column not in existing:
            conn.execute(
                f"ALTER TABLE traffic_counts ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )
    conn.execute(_CREATE_EVENTS)
    conn.execute(_CREATE_FORECASTS)
    forecast_columns = _forecast_columns(conn)
    for column, declaration in (
        ("horizon_buckets", "INTEGER NOT NULL DEFAULT 3"),
        ("predicted_buckets", "TEXT"),
        ("model", "TEXT"),
    ):
        if column in forecast_columns:
            continue
        try:
            conn.execute(f"ALTER TABLE traffic_forecasts ADD COLUMN {column} {declaration}")
        except sqlite3.OperationalError:
            # The runner and dashboard can open the same legacy database together.
            if column not in _forecast_columns(conn):
                raise
    conn.commit()


def _forecast_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(traffic_forecasts)")}


def init_db(conn: sqlite3.Connection) -> None:
    _ensure_schema(conn)


def clear_db(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM traffic_events")
    conn.execute("DELETE FROM traffic_counts")
    conn.execute("DELETE FROM traffic_forecasts")
    conn.commit()


def fetch_minute_totals(conn: sqlite3.Connection, start: int, end: int) -> dict[int, int]:
    return dict(conn.execute(
        "SELECT timestamp, car_count + bus_count + truck_count + motorcycle_count + bicycle_count"
        " FROM traffic_counts WHERE timestamp >= ? AND timestamp < ? AND timestamp % 60 = 0",
        (start, end),
    ))


def insert_forecast(
    conn: sqlite3.Connection, camera: str, cutoff: int, predicted_counts: Sequence[float], model: str,
) -> None:
    conn.execute(
        "INSERT INTO traffic_forecasts (camera, cutoff, predicted_count, horizon_buckets, predicted_buckets, model)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(camera, cutoff) DO NOTHING",
        (camera, cutoff, predicted_counts[0], len(predicted_counts), json.dumps(predicted_counts), model),
    )


def fetch_forecasts(
    conn: sqlite3.Connection, camera: str, start: int, end: int,
) -> list[tuple[int, list[float], str]]:
    rows = conn.execute(
        "SELECT cutoff, predicted_count, horizon_buckets, predicted_buckets, model FROM traffic_forecasts"
        " WHERE camera = ? AND cutoff >= ? AND cutoff < ? ORDER BY cutoff",
        (camera, start, end),
    )
    return [
        (cutoff, [count] * horizon, "average") if encoded is None
        else (cutoff, json.loads(encoded), model or "ridge_ar")
        for cutoff, count, horizon, encoded, model in rows
    ]


def fetch_forecast_ranges(conn: sqlite3.Connection, camera: str) -> list[tuple[int, int]]:
    return conn.execute(
        "SELECT cutoff, horizon_buckets FROM traffic_forecasts WHERE camera = ?", (camera,),
    ).fetchall()


def insert_counts(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[int]],
) -> None:
    conn.executemany(
        "INSERT INTO traffic_counts "
        "(timestamp, car_count, bus_count, truck_count, motorcycle_count, bicycle_count)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_to_rich_row(row) for row in rows),
    )
    conn.commit()


def insert_events(conn: sqlite3.Connection, events: Iterable) -> None:
    conn.executemany(
        "INSERT INTO traffic_events "
        "(timestamp, vehicle_type, camera, track_id, confidence, direction)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                event.timestamp,
                event.vehicle_type,
                event.camera,
                event.track_id,
                event.confidence,
                event.direction,
            )
            for event in events
        ),
    )
    conn.commit()


def upsert_counts(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[int]],
) -> None:
    conn.executemany(
        "INSERT INTO traffic_counts "
        "(timestamp, car_count, bus_count, truck_count, motorcycle_count, bicycle_count)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(timestamp) DO UPDATE SET"
        " car_count = car_count + excluded.car_count,"
        " bus_count = bus_count + excluded.bus_count,"
        " truck_count = truck_count + excluded.truck_count,"
        " motorcycle_count = motorcycle_count + excluded.motorcycle_count,"
        " bicycle_count = bicycle_count + excluded.bicycle_count",
        (_to_rich_row(row) for row in rows),
    )
    conn.commit()


def fetch_counts(conn: sqlite3.Connection) -> list[tuple[int, int, int, int, int, int]]:
    return conn.execute(
        "SELECT timestamp, car_count, bus_count, truck_count, motorcycle_count, bicycle_count"
        " FROM traffic_counts ORDER BY timestamp"
    ).fetchall()


def fetch_rich_counts(
    conn: sqlite3.Connection,
) -> list[tuple[int, int, int, int, int, int]]:
    _ensure_schema(conn)
    return conn.execute(
        "SELECT timestamp, car_count, bus_count, truck_count, motorcycle_count, "
        "bicycle_count FROM traffic_counts ORDER BY timestamp"
    ).fetchall()


def fetch_completed_counts(
    conn: sqlite3.Connection,
    current_timestamp: int | None = None,
) -> list[tuple[int, int, int, int, int, int]]:
    """Return minute buckets that ended before the current minute."""
    now = int(time.time()) if current_timestamp is None else current_timestamp
    current_minute = now - now % 60
    return conn.execute(
        "SELECT timestamp, car_count, bus_count, truck_count, motorcycle_count, bicycle_count"
        " FROM traffic_counts WHERE timestamp < ? ORDER BY timestamp",
        (current_minute,),
    ).fetchall()


def fetch_completed_rich_counts(
    conn: sqlite3.Connection,
    current_timestamp: int | None = None,
) -> list[tuple[int, int, int, int, int, int]]:
    _ensure_schema(conn)
    now = int(time.time()) if current_timestamp is None else current_timestamp
    current_minute = now - now % 60
    return conn.execute(
        "SELECT timestamp, car_count, bus_count, truck_count, motorcycle_count, "
        "bicycle_count FROM traffic_counts WHERE timestamp < ? ORDER BY timestamp",
        (current_minute,),
    ).fetchall()


def fetch_buckets(
    conn: sqlite3.Connection,
    timestamps: Sequence[int],
) -> list[tuple[int, int, int, int, int, int]]:
    if not timestamps:
        return []
    _ensure_schema(conn)
    placeholders = ",".join("?" * len(timestamps))
    return conn.execute(
        "SELECT timestamp, car_count, bus_count, truck_count, motorcycle_count, "
        "bicycle_count"
        f" FROM traffic_counts WHERE timestamp IN ({placeholders})"
        " ORDER BY timestamp",
        tuple(timestamps),
    ).fetchall()


def _to_rich_row(row: Sequence[int]) -> tuple[int, int, int, int, int, int]:
    values = tuple(row)
    if len(values) == 6:
        return values
    raise ValueError("count rows must contain 6 rich values")
