"""Five-minute traffic forecasts for a database collecting one camera."""

import sqlite3
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from data.db import fetch_forecast_ranges, fetch_forecasts, fetch_minute_totals, insert_forecast
from data.prophet_model import predict_prophet

DEFAULT_CAMERA = "cam1"
DEFAULT_TIME_ZONE = "Europe/Budapest"
DEFAULT_FORECAST_MODEL = "prophet"
FORECAST_MODELS = ("prophet", "ridge_ar")
MINUTE = 60
BUCKET = 5 * MINUTE
LOOKBACK = 15 * MINUTE
HORIZON = 30 * MINUTE
LAGS = LOOKBACK // BUCKET
TRAINING_HISTORY = 30 * 24 * 60 * MINUTE
MIN_TRAINING_EXAMPLES = 6
RIDGE_PENALTY = 1.0
PROPHET_HISTORY = 4 * 60 * MINUTE
PROPHET_MIN_BUCKETS = 9
PROPHET_REPLAY_LIMIT = 24


def forecast_cutoff(now: int) -> int:
    return now - now % BUCKET


def observed_total(totals: dict[int, int], start: int, end: int) -> int | None:
    try:
        return sum(totals[minute] for minute in range(start, end, MINUTE))
    except KeyError:
        return None


def complete_buckets(totals: dict[int, int]) -> dict[int, int]:
    buckets = {}
    for start in totals:
        if start % BUCKET == 0:
            count = observed_total(totals, start, start + BUCKET)
            if count is not None:
                buckets[start] = count
    return buckets


def recent_counts(buckets: dict[int, int], cutoff: int) -> list[int] | None:
    try:
        return [buckets[start] for start in range(cutoff - LOOKBACK, cutoff, BUCKET)]
    except KeyError:
        return None


class TrafficModel:
    """Regularized autoregression on consecutive five-minute vehicle counts."""

    method = "ridge_ar"
    minimum_training_examples = MIN_TRAINING_EXAMPLES

    def __init__(self, buckets: dict[int, int]):
        self.buckets = buckets
        ends = []
        examples = []
        for target in sorted(buckets):
            inputs = recent_counts(buckets, target)
            if inputs is not None:
                ends.append(target + BUCKET)
                examples.append([*inputs, buckets[target]])
        self.example_ends = np.asarray(ends, dtype=np.int64)
        self.examples = np.asarray(examples, dtype=float).reshape(-1, LAGS + 1)

    def training_data(self, cutoff: int) -> np.ndarray:
        first_complete_example = cutoff - TRAINING_HISTORY + LOOKBACK + BUCKET
        start = self.example_ends.searchsorted(first_complete_example)
        end = self.example_ends.searchsorted(cutoff, side="right")
        return self.examples[start:end]

    def training_count(self, cutoff: int) -> int:
        return len(self.training_data(cutoff))

    def predict(self, cutoff: int) -> list[float] | None:
        recent = recent_counts(self.buckets, cutoff)
        training = self.training_data(cutoff)
        if recent is None or len(training) < MIN_TRAINING_EXAMPLES:
            return None

        inputs = training[:, :LAGS]
        targets = training[:, LAGS]
        center = inputs.mean(axis=0)
        scale = inputs.std(axis=0)
        scale[scale == 0] = 1
        features = (inputs - center) / scale
        intercept = targets.mean()
        weights = np.linalg.solve(
            features.T @ features + RIDGE_PENALTY * np.eye(LAGS),
            features.T @ (targets - intercept),
        )

        history = list(map(float, recent))
        predictions = []
        for _ in range(HORIZON // BUCKET):
            features_next = (np.asarray(history[-LAGS:]) - center) / scale
            count = max(0.0, float(intercept + features_next @ weights))
            predictions.append(count)
            history.append(count)
        return predictions


class ProphetTrafficModel:
    method = "prophet"
    minimum_training_examples = PROPHET_MIN_BUCKETS

    def __init__(self, buckets: dict[int, int]):
        self.buckets = buckets
        self.timestamps = sorted(buckets)

    def training_range(self, cutoff: int) -> tuple[int, int]:
        return bisect_left(self.timestamps, cutoff - PROPHET_HISTORY), bisect_left(self.timestamps, cutoff)

    def training_count(self, cutoff: int) -> int:
        start, end = self.training_range(cutoff)
        return end - start

    def predict(self, cutoff: int) -> list[float] | None:
        start, end = self.training_range(cutoff)
        if end - start < self.minimum_training_examples or recent_counts(self.buckets, cutoff) is None:
            return None
        training = tuple((timestamp, self.buckets[timestamp]) for timestamp in self.timestamps[start:end])
        return list(predict_prophet(training, tuple(range(cutoff, cutoff + HORIZON, BUCKET))))


def model_history(method: str) -> int:
    return PROPHET_HISTORY if method == "prophet" else TRAINING_HISTORY


def build_model(method: str, buckets: dict[int, int]) -> TrafficModel | ProphetTrafficModel:
    return ProphetTrafficModel(buckets) if method == "prophet" else TrafficModel(buckets)


def save_due_forecast(
    conn: sqlite3.Connection,
    camera: str,
    now: int,
    method: str = DEFAULT_FORECAST_MODEL,
) -> bool:
    cutoff = forecast_cutoff(now)
    if now >= cutoff + MINUTE:
        return False
    if fetch_forecasts(conn, camera, cutoff, cutoff + 1):
        return True
    if len(fetch_minute_totals(conn, cutoff - LOOKBACK, cutoff)) < LOOKBACK // MINUTE:
        return False
    start = cutoff - model_history(method)
    totals = fetch_minute_totals(conn, start, cutoff)
    predictions = build_model(method, complete_buckets(totals)).predict(cutoff)
    if predictions is None:
        return False
    with conn:
        # Fit without a write lock, then reject a snapshot changed by late data or a clear.
        conn.execute("BEGIN IMMEDIATE")
        if fetch_minute_totals(conn, start, cutoff) != totals:
            return False
        insert_forecast(conn, camera, cutoff, predictions, method)
    return True


def day_bounds(day: date, zone: ZoneInfo) -> tuple[int, int]:
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return int(start.timestamp()), int(end.timestamp())


def projection(cutoff: int, counts: list[float], method: str, start: int, end: int) -> dict:
    return {
        "cutoff": cutoff,
        "method": method,
        "points": [
            {"timestamp": target, "count": count}
            for target, count in zip(range(cutoff, cutoff + len(counts) * BUCKET, BUCKET), counts)
            if start <= target < end
        ],
    }


def replay_forecasts(
    model: TrafficModel | ProphetTrafficModel, start: int, end: int, selected_cutoff: int | None,
) -> dict:
    predictions = {}
    errors = []
    baseline_errors = []
    best_cutoff = None
    best_overlap = 0
    cutoffs = [
        cutoff for cutoff in range(start + (-start % BUCKET), end, BUCKET)
        if recent_counts(model.buckets, cutoff) is not None
        and model.training_count(cutoff) >= model.minimum_training_examples
    ]
    evaluation_cutoffs = cutoffs
    if model.method == "prophet" and len(cutoffs) > PROPHET_REPLAY_LIMIT:
        evaluation_cutoffs = [
            cutoffs[i * (len(cutoffs) - 1) // (PROPHET_REPLAY_LIMIT - 1)]
            for i in range(PROPHET_REPLAY_LIMIT)
        ]
    for cutoff in evaluation_cutoffs:
        upcoming = model.predict(cutoff)
        if upcoming is None:
            continue
        predictions[cutoff] = upcoming
        recent_average = sum(model.buckets[timestamp]
                             for timestamp in range(cutoff - LOOKBACK, cutoff, BUCKET)) / LAGS
        overlap = 0
        for step, count in enumerate(upcoming):
            actual = model.buckets.get(cutoff + step * BUCKET)
            if actual is not None:
                errors.append(abs(actual - count))
                baseline_errors.append(abs(actual - recent_average))
                overlap += 1
        if overlap > best_overlap:
            best_cutoff, best_overlap = cutoff, overlap

    if selected_cutoff not in cutoffs:
        selected_cutoff = best_cutoff if best_cutoff is not None else next(iter(predictions), None)
    forecast = None
    if selected_cutoff is not None:
        upcoming = predictions.get(selected_cutoff) or model.predict(selected_cutoff)
        if upcoming is not None:
            forecast = projection(
                selected_cutoff, upcoming, model.method, selected_cutoff, selected_cutoff + HORIZON,
            )
    return {
        "cutoffs": cutoffs,
        "forecast": forecast,
        "following_day_actual": [
            {"timestamp": point["timestamp"], "count": model.buckets[point["timestamp"]]}
            for point in forecast["points"]
            if point["timestamp"] >= end and point["timestamp"] in model.buckets
        ] if forecast is not None else [],
        "mean_absolute_error": sum(errors) / len(errors) if errors else None,
        "baseline_mean_absolute_error": sum(baseline_errors) / len(baseline_errors) if baseline_errors else None,
        "evaluated": len(errors),
    }


def forecast_day(
    conn: sqlite3.Connection,
    camera: str,
    zone: ZoneInfo,
    now: int,
    day: date | None = None,
    replay_cutoff: int | None = None,
    live_method: str = DEFAULT_FORECAST_MODEL,
    replay_method: str = DEFAULT_FORECAST_MODEL,
) -> dict:
    today = datetime.fromtimestamp(now, zone).date()
    overview = day is None
    day = day or today
    start, end = day_bounds(day, zone)
    minute = now - now % MINUTE
    cutoff = forecast_cutoff(now)
    method = replay_method if day < today else live_method
    with conn:
        # Release the read snapshot before running any Prophet fits.
        conn.execute("BEGIN")
        totals = fetch_minute_totals(conn, start - model_history(method), min(end + HORIZON, minute))
        saved = fetch_forecasts(conn, camera, start - HORIZON + BUCKET, min(end, cutoff + 1))
        forecast_ranges = fetch_forecast_ranges(conn, camera)
    buckets = complete_buckets(totals)
    latest_run = next((row for row in reversed(saved) if row[0] >= start), None)
    carryover_run = next((
        row for row in reversed(saved) if row[0] < start <= row[0] + (len(row[1]) - 1) * BUCKET
    ), None)
    latest = projection(*latest_run, start, end) if latest_run is not None else None
    carryover = projection(*carryover_run, start, end) if carryover_run is not None else None
    live = projection(*latest_run, start, end + HORIZON) if overview and latest_run is not None else None
    actual = [
        {"timestamp": target, "count": buckets[target]}
        for target in sorted(buckets) if start <= target < end
    ]

    status = "historical" if day < today else "upcoming"
    consecutive_minutes = 0
    training_examples = 0
    minimum_examples = PROPHET_MIN_BUCKETS if method == "prophet" else MIN_TRAINING_EXAMPLES
    replay = None
    if day == today:
        model = build_model(method, buckets)
        training_examples = model.training_count(cutoff)
        for timestamp in range(minute - MINUTE, minute - LOOKBACK - MINUTE, -MINUTE):
            if timestamp not in totals:
                break
            consecutive_minutes += 1
        if latest is not None and latest["cutoff"] == cutoff and latest["method"] == method:
            status = "current"
        elif recent_counts(buckets, cutoff) is None:
            status = "insufficient"
        elif training_examples < minimum_examples:
            status = "training"
        else:
            status = "awaiting"
    elif day < today:
        replay = replay_forecasts(build_model(method, buckets), start, end, replay_cutoff)

    forecast_dates = {
        datetime.fromtimestamp(target, zone).date()
        for issued_at, horizon_buckets in forecast_ranges
        for target in (issued_at, issued_at + (horizon_buckets - 1) * BUCKET)
    }
    return {
        "date": day.isoformat(),
        "today": today.isoformat(),
        "timezone": zone.key,
        "forecast_dates": sorted(day.isoformat() for day in forecast_dates),
        "actual": actual,
        "latest": latest,
        "carryover": carryover,
        "live": live,
        "status": status,
        "consecutive_minutes": consecutive_minutes,
        "input_minutes": LOOKBACK // MINUTE,
        "training_examples": training_examples,
        "minimum_training_examples": minimum_examples,
        "model": method,
        "replay": replay,
    }

