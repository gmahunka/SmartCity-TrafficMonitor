import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from data.db import clear_db, connect, init_db, upsert_counts
from data.forecast import (
    BUCKET,
    HORIZON,
    LOOKBACK,
    MINUTE,
    MIN_TRAINING_EXAMPLES,
    PROPHET_MIN_BUCKETS,
    ProphetTrafficModel,
    TrafficModel,
    complete_buckets,
    day_bounds,
    forecast_day,
    replay_forecasts,
    save_due_forecast,
)

ZONE = ZoneInfo("Europe/Budapest")
CAMERA = "cam1"


def add_counts(conn, start, end, count=2):
    upsert_counts(conn, [(minute, count, 0, 0, 0, 0) for minute in range(start, end, MINUTE)])


TRAINING_SPAN = LOOKBACK + MIN_TRAINING_EXAMPLES * BUCKET


class ForecastTests(unittest.TestCase):
    def test_model_projects_six_values_and_uses_no_future_observations(self):
        cutoff = 3600
        totals = {
            minute: 2 + (minute - cutoff + TRAINING_SPAN) // BUCKET
            for minute in range(cutoff - TRAINING_SPAN, cutoff, MINUTE)
        }
        model = TrafficModel(complete_buckets(totals))
        predictions = model.predict(cutoff)
        self.assertEqual(len(predictions), 6)
        self.assertGreater(predictions[-1], predictions[0])
        self.assertTrue(all(count >= 0 for count in predictions))
        self.assertIsNone(model.predict(cutoff - BUCKET))
        totals.update(dict.fromkeys(range(cutoff, cutoff + HORIZON, MINUTE), 100))
        self.assertEqual(TrafficModel(complete_buckets(totals)).predict(cutoff), predictions)
        del totals[cutoff - MINUTE]
        self.assertIsNone(TrafficModel(complete_buckets(totals)).predict(cutoff))

    def test_midnight_rollover_keeps_the_issued_values_after_restart(self):
        yesterday = date(2026, 11, 20)
        today = date(2026, 11, 21)
        midnight, _ = day_bounds(today, ZONE)
        cutoff = midnight - BUCKET

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traffic.db"
            with closing(connect(path)) as conn:
                for index, start in enumerate(range(cutoff - TRAINING_SPAN, cutoff, BUCKET)):
                    add_counts(conn, start, start + BUCKET, count=index + 1)
                self.assertTrue(save_due_forecast(conn, CAMERA, cutoff + 1))
                view = forecast_day(conn, CAMERA, ZONE, cutoff + 1, yesterday)
                self.assertEqual(view["latest"]["method"], "prophet")
                self.assertEqual([point["timestamp"] for point in view["latest"]["points"]], [cutoff])
                self.assertEqual(view["forecast_dates"], [yesterday.isoformat(), today.isoformat()])
                overview = forecast_day(conn, CAMERA, ZONE, cutoff + 1)
                self.assertEqual(len(overview["live"]["points"]), 6)
                self.assertEqual(overview["live"]["points"][-1]["timestamp"], cutoff + HORIZON - BUCKET)
                self.assertGreater(len({point["count"] for point in overview["live"]["points"]}), 1)
                next_day = forecast_day(conn, CAMERA, ZONE, cutoff + 1, today)
                self.assertEqual(next_day["actual"], [])
                self.assertEqual(len(next_day["carryover"]["points"]), 5)
                add_counts(conn, cutoff - LOOKBACK, cutoff, count=5)
                add_counts(conn, cutoff, midnight, count=8)

            with closing(connect(path)) as conn:
                self.assertTrue(save_due_forecast(conn, CAMERA, cutoff + 59))
                self.assertTrue(save_due_forecast(conn, CAMERA, midnight + 1))
                view = forecast_day(conn, CAMERA, ZONE, midnight + 1, today)
                self.assertEqual(view["latest"]["cutoff"], midnight)
                self.assertEqual(view["latest"]["method"], "prophet")
                self.assertEqual(view["carryover"], {
                    "cutoff": cutoff,
                    "method": "prophet",
                    "points": overview["live"]["points"][1:],
                })
                self.assertEqual(view["actual"], [])
                self.assertIsNone(forecast_day(conn, "other", ZONE, midnight + 1, today)["carryover"])
                clear_db(conn)
                self.assertIsNone(forecast_day(conn, CAMERA, ZONE, midnight + 1, today)["carryover"])

    def test_prophet_fits_a_recent_trend_without_future_traffic(self):
        cutoff = 3600
        buckets = {start: 10 + i for i, start in enumerate(
            range(cutoff - PROPHET_MIN_BUCKETS * BUCKET, cutoff, BUCKET)
        )}
        model = ProphetTrafficModel(buckets)
        forecast = model.predict(cutoff)
        self.assertEqual(len(forecast), 6)
        self.assertGreater(forecast[-1], forecast[0])
        self.assertIsNone(model.predict(cutoff - BUCKET))
        buckets[cutoff] = 1000
        self.assertEqual(ProphetTrafficModel(buckets).predict(cutoff), forecast)

    def test_skips_missed_cutoff_and_incomplete_input(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            init_db(conn)
            cutoff = 3600
            add_counts(conn, cutoff - TRAINING_SPAN, cutoff - MINUTE)
            self.assertFalse(save_due_forecast(conn, CAMERA, cutoff))
            add_counts(conn, cutoff - MINUTE, cutoff)
            self.assertFalse(save_due_forecast(conn, CAMERA, cutoff + MINUTE))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM traffic_forecasts").fetchone(), (0,))

    def test_replay_scores_only_complete_targets(self):
        cutoff = 3600
        totals = dict.fromkeys(range(cutoff - TRAINING_SPAN, cutoff, MINUTE), 2)
        totals.update(dict.fromkeys(range(cutoff, cutoff + BUCKET, MINUTE), 3))
        totals.update(dict.fromkeys(range(cutoff + BUCKET, cutoff + 2 * BUCKET - MINUTE, MINUTE), 100))
        replay = replay_forecasts(TrafficModel(complete_buckets(totals)), cutoff, cutoff + BUCKET, cutoff)
        self.assertEqual(replay["forecast"]["points"], [
            {"timestamp": timestamp, "count": 10}
            for timestamp in range(cutoff, cutoff + HORIZON, BUCKET)
        ])
        self.assertEqual(replay["following_day_actual"], [])
        self.assertEqual(replay["evaluated"], 1)
        self.assertEqual(replay["mean_absolute_error"], 5)
        self.assertEqual(replay["baseline_mean_absolute_error"], 5)

    def test_replay_defaults_to_more_observed_overlap_and_keeps_explicit_start(self):
        first_cutoff = 3600
        midnight = first_cutoff + 3 * BUCKET
        totals = dict.fromkeys(range(first_cutoff - TRAINING_SPAN, midnight + BUCKET, MINUTE), 2)
        model = TrafficModel(complete_buckets(totals))
        default = replay_forecasts(model, first_cutoff, midnight, None)
        self.assertEqual(default["forecast"]["cutoff"], first_cutoff)

        selected = midnight - BUCKET
        replay = replay_forecasts(model, first_cutoff, midnight, selected)
        points = replay["forecast"]["points"]
        self.assertEqual(points[0]["timestamp"], selected)
        self.assertEqual(points[-1]["timestamp"] + BUCKET, selected + HORIZON)
        self.assertEqual(replay["following_day_actual"], [{"timestamp": midnight, "count": 10}])

    def test_existing_forecast_keeps_its_original_fifteen_minute_horizon(self):
        today = date(2026, 11, 21)
        midnight, _ = day_bounds(today, ZONE)
        cutoff = midnight - BUCKET
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute(
                "CREATE TABLE traffic_forecasts (camera TEXT NOT NULL, cutoff INTEGER NOT NULL, "
                "predicted_count REAL NOT NULL, PRIMARY KEY (camera, cutoff))"
            )
            conn.execute("INSERT INTO traffic_forecasts VALUES (?, ?, ?)", (CAMERA, cutoff, 10))
            init_db(conn)
            self.assertTrue(save_due_forecast(conn, CAMERA, cutoff + 1))
            overview = forecast_day(conn, CAMERA, ZONE, cutoff + 1)
            self.assertEqual(len(overview["live"]["points"]), 3)
            self.assertEqual(overview["live"]["method"], "average")
            next_day = forecast_day(conn, CAMERA, ZONE, cutoff + 1, today)
            self.assertEqual(len(next_day["carryover"]["points"]), 2)

    def test_daylight_saving_uses_local_day_and_distinct_epoch_buckets(self):
        day = date(2026, 10, 25)
        start, end = day_bounds(day, ZONE)
        self.assertEqual(end - start, 25 * 60 * 60)
        repeated_hour = [int(datetime(2026, 10, 25, 2, tzinfo=ZONE, fold=fold).timestamp()) for fold in (0, 1)]
        with closing(sqlite3.connect(":memory:")) as conn:
            init_db(conn)
            for target in repeated_hour:
                add_counts(conn, target, target + BUCKET)
            view = forecast_day(conn, CAMERA, ZONE, end, day)
        self.assertEqual(view["actual"], [{"timestamp": target, "count": 10} for target in repeated_hour])
