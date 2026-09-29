import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "traffic.db"

_CREATE_TABLE = """
CREATE TABLE traffic_counts (
    timestamp INTEGER PRIMARY KEY,
    car_count INTEGER NOT NULL DEFAULT 0,
    bike_count INTEGER NOT NULL DEFAULT 0,
    bus_count INTEGER NOT NULL DEFAULT 0
)
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.commit()


def clear_db(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS traffic_counts")
    init_db(conn)


def insert_counts(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[int]],
) -> None:
    conn.executemany(
        "INSERT INTO traffic_counts (timestamp, car_count, bike_count, bus_count)"
        " VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def upsert_counts(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[int]],
) -> None:
    conn.executemany(
        "INSERT INTO traffic_counts (timestamp, car_count, bike_count, bus_count)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(timestamp) DO UPDATE SET"
        " car_count = car_count + excluded.car_count,"
        " bike_count = bike_count + excluded.bike_count,"
        " bus_count = bus_count + excluded.bus_count",
        rows,
    )
    conn.commit()


def fetch_counts(conn: sqlite3.Connection) -> list[tuple[int, int, int, int]]:
    return conn.execute(
        "SELECT timestamp, car_count, bike_count, bus_count"
        " FROM traffic_counts ORDER BY timestamp"
    ).fetchall()


def fetch_buckets(
    conn: sqlite3.Connection,
    timestamps: Sequence[int],
) -> list[tuple[int, int, int, int]]:
    if not timestamps:
        return []
    placeholders = ",".join("?" * len(timestamps))
    return conn.execute(
        "SELECT timestamp, car_count, bike_count, bus_count"
        f" FROM traffic_counts WHERE timestamp IN ({placeholders})"
        " ORDER BY timestamp",
        tuple(timestamps),
    ).fetchall()
