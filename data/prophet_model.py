from functools import lru_cache


@lru_cache(maxsize=128)
def predict_prophet(
    history: tuple[tuple[int, int], ...], targets: tuple[int, ...],
) -> tuple[float, ...]:
    import pandas as pd
    from prophet import Prophet

    timestamps, counts = zip(*history)
    # Prophet expects timezone-naive timestamps. Use UTC consistently for both frames.
    training = pd.DataFrame({"ds": pd.to_datetime(timestamps, unit="s"), "y": counts})
    future = pd.DataFrame({"ds": pd.to_datetime(targets, unit="s")})
    model = Prophet(
        daily_seasonality=True,
        weekly_seasonality=False,
        yearly_seasonality=False,
        n_changepoints=min(3, (len(history) - 1) // 12),
        uncertainty_samples=0,
    )
    model.fit(training, seed=42)
    return tuple(max(0.0, float(value)) for value in model.predict(future)["yhat"])
