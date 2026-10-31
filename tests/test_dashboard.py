import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.db import fetch_completed_counts


class CompletedCountsTests(unittest.TestCase):
    def test_excludes_current_minute(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE traffic_counts ("
            "timestamp INTEGER PRIMARY KEY, car_count INTEGER NOT NULL,"
            "bus_count INTEGER NOT NULL, truck_count INTEGER NOT NULL,"
            "motorcycle_count INTEGER NOT NULL, bicycle_count INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO traffic_counts VALUES (?, ?, ?, ?, ?, ?)",
            [(599, 1, 0, 0, 0, 0), (600, 2, 0, 0, 0, 0), (660, 3, 0, 0, 0, 0)],
        )

        self.assertEqual(
            fetch_completed_counts(conn, current_timestamp=619),
            [(599, 1, 0, 0, 0, 0)],
        )
        conn.close()


class DashboardPageTests(unittest.TestCase):
    def test_dashboard_page_contains_graph_and_polling(self) -> None:
        from dashboard.app import PAGE

        self.assertIn("<canvas", PAGE)
        self.assertIn("STREAM_URL", PAGE)
        self.assertIn('fetch("/dashboard/data"', PAGE)
        self.assertIn('fetch("/dashboard/clear"', PAGE)
        self.assertIn("setInterval(refresh, 60000)", PAGE)

    def test_data_response_uses_database_rows(self) -> None:
        from dashboard.app import app, dashboard_data
        from starlette.requests import Request

        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "traffic.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE traffic_counts ("
                "timestamp INTEGER PRIMARY KEY, car_count INTEGER NOT NULL,"
                "bus_count INTEGER NOT NULL, truck_count INTEGER NOT NULL,"
                "motorcycle_count INTEGER NOT NULL, bicycle_count INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO traffic_counts VALUES (1, 4, 1, 0, 0, 2)")
            conn.commit()
            conn.close()
            app.state.db_path = db_path

            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/dashboard/data",
                    "app": app,
                }
            )
            response = dashboard_data(request)

        self.assertEqual(response["buckets"][0]["car"], 4)
        self.assertEqual(response["buckets"][0]["bicycle"], 2)

    def test_clear_endpoint_removes_events_and_buckets(self) -> None:
        from common.events import VehicleEvent
        from dashboard.app import app, dashboard_clear
        from data.db import connect, insert_events, upsert_counts
        from starlette.requests import Request

        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "traffic.db"
            conn = connect(db_path)
            insert_events(conn, [VehicleEvent(timestamp=60, vehicle_type="car")])
            upsert_counts(conn, [(60, 1, 0, 0, 0, 0)])
            conn.close()
            app.state.db_path = db_path
            request = Request({
                "type": "http",
                "method": "POST",
                "path": "/dashboard/clear",
                "app": app,
            })

            response = dashboard_clear(request)
            remaining = connect(db_path)
            try:
                self.assertEqual(response, {
                    "status": "ok",
                    "deleted_events": 1,
                    "deleted_buckets": 1,
                })
                self.assertEqual(remaining.execute("SELECT COUNT(*) FROM traffic_events").fetchone()[0], 0)
                self.assertEqual(remaining.execute("SELECT COUNT(*) FROM traffic_counts").fetchone()[0], 0)
            finally:
                remaining.close()


if __name__ == "__main__":
    unittest.main()
