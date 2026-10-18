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
            "bike_count INTEGER NOT NULL, bus_count INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO traffic_counts VALUES (?, ?, ?, ?)",
            [(599, 1, 0, 0), (600, 2, 1, 0), (660, 3, 0, 1)],
        )

        self.assertEqual(
            fetch_completed_counts(conn, current_timestamp=619),
            [(599, 1, 0, 0)],
        )
        conn.close()


class DashboardPageTests(unittest.TestCase):
    def test_dashboard_page_contains_graph_and_polling(self) -> None:
        from dashboard.app import PAGE

        self.assertIn("<canvas", PAGE)
        self.assertIn('fetch("/dashboard/data"', PAGE)
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
                "bike_count INTEGER NOT NULL, bus_count INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO traffic_counts VALUES (1, 4, 2, 1)")
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
        self.assertEqual(response["buckets"][0]["bike"], 2)


if __name__ == "__main__":
    unittest.main()
