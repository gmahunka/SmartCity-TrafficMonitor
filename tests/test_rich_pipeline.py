import sqlite3
import tempfile
import unittest
from pathlib import Path

from common.events import VehicleEvent
from cv.detector import CounterConfig, TrackCounter
from cv.run import _flush
from data.db import clear_db, connect, fetch_rich_counts, insert_events, upsert_counts


class RichEventTests(unittest.TestCase):
    def test_bicycle_is_stored_as_bicycle(self) -> None:
        event = VehicleEvent(timestamp=120, vehicle_type="bicycle")
        self.assertEqual(event.vehicle_type, "bicycle")
        self.assertEqual(event.minute, 2)

    def test_rich_counts_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traffic.db"
            conn = connect(path)
            try:
                insert_events(conn, [VehicleEvent(
                    timestamp=120,
                    vehicle_type="truck",
                    track_id=7,
                    confidence=0.8,
                    direction="inbound",
                )])
                upsert_counts(conn, [(120, 1, 2, 3, 4, 5)])
                self.assertEqual(
                    fetch_rich_counts(conn),
                    [(120, 1, 2, 3, 4, 5)],
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT vehicle_type, track_id, confidence, direction "
                        "FROM traffic_events"
                    ).fetchone(),
                    ("truck", 7, 0.8, "inbound"),
                )
            finally:
                conn.close()

    def test_cv_flush_writes_six_rich_count_columns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traffic.db"
            conn = connect(path)
            try:
                written = _flush(conn, [
                    VehicleEvent(timestamp=120, vehicle_type="bicycle"),
                ])
                self.assertEqual(written, 1)
                self.assertEqual(fetch_rich_counts(conn), [(120, 0, 0, 0, 0, 1)])
            finally:
                conn.close()

    def test_clear_db_removes_events_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            conn = connect(Path(folder) / "traffic.db")
            try:
                insert_events(conn, [VehicleEvent(timestamp=120, vehicle_type="car")])
                upsert_counts(conn, [(120, 1, 0, 0, 0, 0)])
                clear_db(conn)
                self.assertEqual(fetch_rich_counts(conn), [])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM traffic_events").fetchone()[0], 0)
            finally:
                conn.close()

    def test_tracked_vehicle_registers_after_hits_without_movement(self) -> None:
        counter = TrackCounter(CounterConfig(min_hits=3))
        det = lambda y: [(7, "car", 0.9, 50, y)]
        self.assertEqual(counter.update(1, det(10), 100, 100), [])
        self.assertEqual(counter.update(2, det(10), 100, 100), [])
        self.assertEqual(counter.update(3, det(10), 100, 100), [(7, "car", 0.9, None)])
        self.assertEqual(counter.update(4, det(10), 100, 100), [])

    def test_dashboard_exposes_rich_counts(self) -> None:
        from dashboard.app import app, dashboard_data
        from starlette.requests import Request

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traffic.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE traffic_counts ("
                "timestamp INTEGER PRIMARY KEY, car_count INTEGER NOT NULL,"
                "bus_count INTEGER NOT NULL, truck_count INTEGER NOT NULL,"
                "motorcycle_count INTEGER NOT NULL, bicycle_count INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO traffic_counts VALUES (1, 4, 1, 0, 0, 2)")
            conn.commit()
            conn.close()
            app.state.db_path = path
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/dashboard/data",
                    "app": app,
                }
            )
            bucket = dashboard_data(request)["buckets"][0]

        self.assertEqual(bucket["car"], 4)
        self.assertEqual(bucket["bicycle"], 2)


if __name__ == "__main__":
    unittest.main()
