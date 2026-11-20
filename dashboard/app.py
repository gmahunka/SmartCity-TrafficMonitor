import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from data.db import DEFAULT_DB_PATH, clear_db, connect, fetch_completed_rich_counts
from data.forecast import DEFAULT_CAMERA, DEFAULT_FORECAST_MODEL, DEFAULT_TIME_ZONE, FORECAST_MODELS, forecast_day

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Traffic monitor</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { background: #111827; color: #f3f4f6; margin: 0; }
    main { margin: 0 auto; max-width: 1100px; padding: 32px 20px; }
    h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 6px; }
    h2 { font-size: 1.125rem; margin: 0 0 12px; }
    p { color: #9ca3af; margin: 0 0 24px; }
    .panel { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 20px; }
    .panel + .panel { margin-top: 20px; }
    .meta { color: #9ca3af; font-size: .875rem; margin-bottom: 12px; }
    .actions { display: flex; justify-content: flex-end; margin-bottom: 20px; }
    button { background: #b91c1c; border: 0; border-radius: 6px; color: white; cursor: pointer; padding: 9px 14px; }
    button:disabled { cursor: wait; opacity: .6; }
    .stream { background: #111827; border-radius: 8px; display: block; margin-bottom: 20px; max-height: 560px; object-fit: contain; width: 100%; }
    .history-control { align-items: center; display: flex; font-size: .875rem; gap: 8px; margin-bottom: 12px; }
    .history-control[hidden] { display: none; }
    select { background: #111827; border: 1px solid #4b5563; border-radius: 6px; color: inherit; font: inherit; padding: 6px 8px; }
    canvas { display: block; height: 420px; max-width: 100%; width: 100%; }
    canvas[hidden] { display: none; }
    .legend { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 14px; }
    .legend span::before { background: var(--color); border-radius: 50%; content: ""; display: inline-block; height: 9px; margin-right: 7px; width: 9px; }
    .empty { color: #9ca3af; padding: 80px 0; text-align: center; }
    #forecast-chart { height: 280px; }
    .forecast-legend span::before { background: none; border-radius: 0; border-top: 2px dashed var(--color); height: 4px; width: 15px; }
    .forecast-legend .observed::before { border-top-style: solid; }
    .legend[hidden] { display: none; }
  </style>
</head>
<body>
  <main>
    <h1>Traffic monitor</h1>
    <section class="panel">
      <div class="meta" id="status">Loading…</div>
      <div class="actions"><button id="clear-db" type="button">Clear database</button></div>
      <img class="stream" src="STREAM_URL" alt="Live annotated traffic stream">
      <div class="history-control">
        <label for="history-date">Date</label>
        <select id="history-date" disabled><option value="">All dates</option></select>
      </div>
      <div class="meta" id="main-forecast-status" hidden></div>
      <div id="empty" class="empty" hidden>No completed minute data yet.</div>
      <canvas id="chart" aria-label="Vehicle counts per minute" role="img"></canvas>
      <div class="legend">
        <span style="--color:#60a5fa">Cars</span>
        <span style="--color:#fbbf24">Buses</span>
        <span style="--color:#f87171">Trucks</span>
        <span style="--color:#c084fc">Motorcycles</span>
        <span style="--color:#34d399">Bicycles</span>
      </div>
      <div id="main-forecast-legend" class="legend forecast-legend" hidden></div>
    </section>
    <section class="panel" aria-labelledby="forecast-heading">
      <h2 id="forecast-heading">30-minute traffic forecast</h2>
      <div class="meta" id="forecast-status" role="status">Loading…</div>
      <div id="forecast-model-picker" class="history-control" hidden>
        <label for="forecast-model">Replay model</label>
        <select id="forecast-model">
          <option value="prophet">Prophet</option>
          <option value="ridge_ar">Ridge regression</option>
        </select>
      </div>
      <div id="forecast-replay" class="history-control" hidden>
        <label for="replay-cutoff">Forecast start</label>
        <select id="replay-cutoff"></select>
      </div>
      <div class="meta" id="replay-window" role="status" hidden></div>
      <canvas id="forecast-chart" aria-label="Observed and forecast total vehicles per five minutes" role="img" hidden></canvas>
      <div id="forecast-legend" class="legend forecast-legend"></div>
    </section>
  </main>
  <script>
    const canvas = document.getElementById("chart");
    const status = document.getElementById("status");
    const empty = document.getElementById("empty");
    const clearButton = document.getElementById("clear-db");
    const historyDate = document.getElementById("history-date");
    const mainForecastStatus = document.getElementById("main-forecast-status");
    const mainForecastLegend = document.getElementById("main-forecast-legend");
    const forecastCanvas = document.getElementById("forecast-chart");
    const forecastStatus = document.getElementById("forecast-status");
    const forecastLegend = document.getElementById("forecast-legend");
    const forecastReplay = document.getElementById("forecast-replay");
    const replayWindowLabel = document.getElementById("replay-window");
    const forecastModelPicker = document.getElementById("forecast-model-picker");
    const forecastModel = document.getElementById("forecast-model");
    const replayCutoff = document.getElementById("replay-cutoff");
    const timeZone = FORECAST_TIME_ZONE;
    const dateFormat = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", timeZone });
    const calendarFormat = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
    const dateKeyFormat = new Intl.DateTimeFormat("en", { year: "numeric", month: "2-digit", day: "2-digit", timeZone });
    const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone });
    const issueFormat = new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short", timeZone
    });
    const bucketsByDate = new Map();
    let allBuckets = [];
    let forecast = null;
    let forecastSelection = "";
    let selectedReplayCutoff = "";
    let selectedReplayModel = "prophet";
    let forecastRequest = 0;
    let historyRequest = 0;
    const series = [
      { key: "car", color: "#60a5fa" },
      { key: "bus", color: "#fbbf24" },
      { key: "truck", color: "#f87171" },
      { key: "motorcycle", color: "#c084fc" },
      { key: "bicycle", color: "#34d399" }
    ];

    function drawSelection() {
      const buckets = historyDate.value ? bucketsByDate.get(historyDate.value) ?? [] : allBuckets;
      const lines = series.map(line => ({...line, buckets}));
      const matchesSelection = forecast && forecastSelection === historyDate.value;
      const projections = matchesSelection ? savedProjections(forecast.live ?? forecast.latest) : [];
      const mainForecastLines = projections.map(projection => forecastLine(projection, 1));
      if (projections.length) {
        const totals = buckets.map(bucket => ({
          timestamp: bucket.timestamp,
          count: series.reduce((count, vehicle) => count + bucket[vehicle.key], 0)
        }));
        mainForecastLines.unshift({key: "count", color: "#e5e7eb", buckets: totals, label: "Observed total"});
      }
      empty.hidden = buckets.length !== 0 || projections.length !== 0;
      drawChart(canvas, [...lines, ...mainForecastLines], 60);
      drawLegend(mainForecastLegend, mainForecastLines);
      mainForecastStatus.hidden = !matchesSelection || forecast.date !== forecast.today;
      if (!mainForecastStatus.hidden) {
        mainForecastStatus.textContent = forecast.status === "current"
          ? "Forecast shown as total vehicles per minute" : forecastMessage();
      }
      drawForecast();
    }

    function dayKey(timestamp) {
      const parts = Object.fromEntries(dateKeyFormat.formatToParts(timestamp * 1000).map(part => [part.type, part.value]));
      return `${parts.year}-${parts.month}-${parts.day}`;
    }

    function updateDates() {
      const available = new Set([...bucketsByDate.keys(), ...(forecast?.forecast_dates ?? [])]);
      if (forecast) available.add(forecast.today);
      const dates = [...available].sort().reverse();
      const selection = historyDate.value;
      historyDate.replaceChildren(new Option("All dates", ""));
      for (const day of dates) {
        const label = calendarFormat.format(new Date(day + "T00:00:00Z"));
        historyDate.add(new Option(day === forecast?.today ? `Today · ${label}` : label, day));
      }
      historyDate.value = dates.includes(selection) ? selection : "";
      historyDate.disabled = dates.length === 0;
      drawSelection();
    }

    function setHistory(buckets) {
      allBuckets = buckets;
      bucketsByDate.clear();
      for (const bucket of buckets) {
        const day = dayKey(bucket.timestamp);
        if (!bucketsByDate.has(day)) bucketsByDate.set(day, []);
        bucketsByDate.get(day).push(bucket);
      }
      updateDates();
    }

    function drawChart(canvas, lines, interval, replayWindow = null) {
      const visible = lines.filter(line => line.buckets.length);
      canvas.hidden = visible.length === 0;
      if (!visible.length) return;
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);
      const pad = { top: 20, right: 18, bottom: 38, left: 42 };
      const chartWidth = width - pad.left - pad.right;
      const chartHeight = height - pad.top - pad.bottom;
      const start = Math.min(...visible.map(line => line.buckets[0].timestamp));
      const end = Math.max(...visible.map(line => (
        line.buckets[line.buckets.length - 1].timestamp + (line.step ? interval : 0)
      )));
      const duration = end - start;
      const highest = visible.reduce((max, line) => (
        line.buckets.reduce((max, bucket) => Math.max(max, bucket[line.key]), max)
      ), 1);
      const scaleStep = Math.ceil(highest / 4);
      const max = scaleStep * 4;
      const x = timestamp => pad.left + chartWidth * (duration ? (timestamp - start) / duration : 0.5);
      const y = value => pad.top + chartHeight - value / max * chartHeight;
      if (replayWindow) {
        const left = x(replayWindow.start);
        ctx.fillStyle = "rgba(192, 132, 252, 0.10)";
        ctx.fillRect(left, pad.top, x(replayWindow.end) - left, chartHeight);
        ctx.strokeStyle = "#c084fc";
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        ctx.moveTo(left, pad.top);
        ctx.lineTo(left, pad.top + chartHeight);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.font = "12px system-ui";
      ctx.strokeStyle = "#374151";
      ctx.fillStyle = "#9ca3af";
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const value = scaleStep * i;
        const lineY = y(value);
        ctx.beginPath(); ctx.moveTo(pad.left, lineY); ctx.lineTo(width - pad.right, lineY); ctx.stroke();
        ctx.fillText(value, 8, lineY + 4);
      }
      visible.forEach(s => {
        ctx.strokeStyle = s.color;
        ctx.lineWidth = s.width ?? 2;
        ctx.setLineDash(s.dash ?? []);
        ctx.beginPath();
        s.buckets.forEach((bucket, i) => {
          const previous = s.buckets[i - 1];
          if (previous && bucket.timestamp - previous.timestamp === interval) {
            ctx.lineTo(x(bucket.timestamp), y(bucket[s.key]));
          } else {
            ctx.moveTo(x(bucket.timestamp), y(bucket[s.key]));
          }
          if (s.step) ctx.lineTo(x(bucket.timestamp + interval), y(bucket[s.key]));
        });
        ctx.stroke();
        ctx.fillStyle = s.color;
        for (let i = 0; i < s.buckets.length; i += s.markEvery ?? 1) {
          const bucket = s.buckets[i];
          ctx.beginPath();
          ctx.arc(x(bucket.timestamp), y(bucket[s.key]), s.radius ?? 3, 0, Math.PI * 2);
          ctx.fill();
        }
      });
      ctx.setLineDash([]);
      ctx.fillStyle = "#9ca3af";
      const spansDates = dateFormat.format(start * 1000) !== dateFormat.format(end * 1000);
      const labelFormat = spansDates ? (duration < 24 * 60 * 60 ? issueFormat : dateFormat) : timeFormat;
      const labelSpacing = spansDates ? 145 : 65;
      const intervals = duration ? Math.max(1, Math.min(5, Math.floor(chartWidth / labelSpacing))) : 0;
      for (let i = 0; i <= intervals; i++) {
        const timestamp = start + (intervals ? duration * i / intervals : 0);
        ctx.textAlign = !intervals ? "center" : i === 0 ? "left" : i === intervals ? "right" : "center";
        ctx.fillText(labelFormat.format(timestamp * 1000), x(timestamp), height - 12);
      }
    }

    function savedProjections(latest) {
      return [
        {value: forecast.carryover, color: "#fbbf24", dash: [3, 4], width: 6, radius: 5, prefix: "Previous-day"},
        {value: latest, color: "#38bdf8", dash: [8, 4], prefix: ""}
      ].filter(projection => projection.value);
    }

    function forecastLine({value, prefix, ...style}, minutesPerPoint) {
      const pointsPerBucket = 5 / minutesPerPoint;
      const buckets = value.points.flatMap(point => Array.from({length: pointsPerBucket}, (_, i) => ({
        timestamp: point.timestamp + i * minutesPerPoint * 60,
        count: point.count / pointsPerBucket
      })));
      const method = modelName(value.method) + " forecast";
      const label = [prefix, method].filter(Boolean).join(" ");
      return {...style, key: "count", buckets, step: true, markEvery: pointsPerBucket,
        label: `${label} · ${issueFormat.format(value.cutoff * 1000)}`};
    }

    function modelName(method) {
      return {prophet: "Prophet", ridge_ar: "Ridge regression", average: "Average"}[method];
    }

    function drawLegend(legend, lines) {
      legend.replaceChildren();
      for (const line of lines) {
        if (!line.buckets.length) continue;
        const label = document.createElement("span");
        label.style.setProperty("--color", line.color);
        label.classList.toggle("observed", !line.dash);
        label.textContent = line.label;
        legend.append(label);
      }
      legend.hidden = !legend.childElementCount;
    }

    function forecastMessage() {
      const waiting = "Waiting for the next five-minute forecast from the counter";
      const labels = {
        current: "Vehicles per 5 minutes",
        insufficient: forecast.consecutive_minutes >= forecast.input_minutes ? waiting
          : `Waiting for continuous data · ${forecast.consecutive_minutes}/${forecast.input_minutes} completed minutes`,
        training: `Waiting for ${modelName(forecast.model)} training data · ${forecast.training_examples}/${forecast.minimum_training_examples} usable ${forecast.model === "prophet" ? "buckets" : "examples"}`,
        awaiting: waiting,
        historical: "Vehicles per 5 minutes",
        upcoming: "Forecast for the selected date"
      };
      return labels[forecast.status];
    }

    function drawForecast() {
      replayWindowLabel.hidden = true;
      if (forecastSelection !== historyDate.value) {
        drawLegend(forecastLegend, []);
        forecastModelPicker.hidden = true;
        forecastReplay.hidden = true;
        forecastCanvas.hidden = true;
        forecastStatus.textContent = "Loading forecast…";
        return;
      }
      forecastModelPicker.hidden = forecast?.status !== "historical";
      forecastReplay.hidden = !forecast?.replay?.cutoffs.length;
      if (!forecast) {
        drawLegend(forecastLegend, []);
        forecastCanvas.hidden = true;
        return;
      }
      let message = forecastMessage();
      const observed = [...forecast.actual, ...(forecast.replay?.following_day_actual ?? [])];
      const lines = [{key: "count", color: "#e5e7eb", buckets: observed, step: true, label: "Observed"}];
      lines.push(...savedProjections(forecast.latest).map(projection => forecastLine(projection, 5)));
      let replayWindow = null;
      if (forecast.replay?.forecast) {
        const replay = forecast.replay.forecast;
        const last = replay.points[replay.points.length - 1];
        replayWindow = {start: replay.cutoff, end: last.timestamp + 300};
        const displayEnd = dayKey(replayWindow.start) === dayKey(replayWindow.end) ? timeFormat : issueFormat;
        replayWindowLabel.textContent = `Forecast window ${timeFormat.format(replayWindow.start * 1000)}–${displayEnd.format(replayWindow.end * 1000)} · 30 minutes`;
        replayWindowLabel.hidden = false;
        lines.push(forecastLine({
          value: replay, color: "#c084fc", dash: [8, 4], prefix: "Replay"
        }, 5));
        replayCutoff.replaceChildren(...forecast.replay.cutoffs.map(cutoff => (
          new Option(issueFormat.format(cutoff * 1000), cutoff)
        )));
        replayCutoff.value = selectedReplayCutoff;
        const error = forecast.replay.mean_absolute_error;
        message = error === null ? "Replay · No complete target buckets to score"
          : `Replay · Mean absolute error per 5 minutes · ${modelName(forecast.replay.forecast.method)} ${error.toFixed(1)} · Average baseline ${forecast.replay.baseline_mean_absolute_error.toFixed(1)} · ${forecast.replay.evaluated} predictions scored`;
      } else if (forecast.status === "historical" && lines.length === 1) {
        message = "No forecast for this date · Insufficient training or recent data";
      }
      forecastStatus.textContent = `${forecast.date} · ${forecast.timezone} · ${message}`;
      drawLegend(forecastLegend, lines);
      drawChart(forecastCanvas, lines, 300, replayWindow);
    }

    async function refreshForecast(selection = historyDate.value, cutoff = "") {
      const request = ++forecastRequest;
      try {
        const params = new URLSearchParams();
        if (selection) params.set("date", selection);
        params.set("model", selectedReplayModel);
        if (cutoff) params.set("replay_cutoff", cutoff);
        const response = await fetch(`/dashboard/forecast?${params}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (request !== forecastRequest) return;
        forecast = data;
        forecastSelection = selection;
        selectedReplayCutoff = data.replay?.forecast ? String(data.replay.forecast.cutoff) : "";
        updateDates();
      } catch (error) {
        if (request === forecastRequest) forecastStatus.textContent = `Unable to load forecast: ${error.message}`;
      }
    }

    async function refresh() {
      const request = ++historyRequest;
      try {
        const response = await fetch("/dashboard/data", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (request !== historyRequest) return;
        setHistory(data.buckets);
        status.textContent = `Updated ${new Date().toLocaleTimeString()} · ${data.buckets.length} completed minutes`;
      } catch (error) {
        if (request !== historyRequest) return;
        status.textContent = `Unable to load data: ${error.message}`;
      }
      await refreshForecast(historyDate.value, selectedReplayCutoff);
    }
    clearButton.addEventListener("click", async () => {
      if (!window.confirm("Clear all stored traffic events, minute counts, and forecasts?")) return;
      clearButton.disabled = true;
      try {
        const response = await fetch("/dashboard/clear", { method: "POST" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        status.textContent = `Database cleared · ${data.deleted_events} events and ${data.deleted_buckets} minute buckets removed`;
        ++historyRequest;
        ++forecastRequest;
        forecast = null;
        forecastSelection = "";
        selectedReplayCutoff = "";
        setHistory([]);
        forecastStatus.textContent = "Insufficient recent data for a new forecast";
      } catch (error) {
        status.textContent = `Unable to clear database: ${error.message}`;
      } finally {
        clearButton.disabled = false;
      }
    });
    historyDate.addEventListener("change", () => {
      selectedReplayCutoff = "";
      drawSelection();
      return refreshForecast();
    });
    replayCutoff.addEventListener("change", () => {
      selectedReplayCutoff = replayCutoff.value;
      return refreshForecast(historyDate.value, selectedReplayCutoff);
    });
    forecastModel.addEventListener("change", () => {
      selectedReplayModel = forecastModel.value;
      return refreshForecast(historyDate.value, selectedReplayCutoff);
    });
    window.addEventListener("resize", drawSelection);
    refresh();
    setInterval(refresh, 60000);
  </script>
</body>
</html>"""

app = FastAPI(title="Traffic dashboard")
app.state.db_path = DEFAULT_DB_PATH
app.state.stream_url = "http://127.0.0.1:8002/stream"
app.state.camera = DEFAULT_CAMERA
app.state.time_zone = ZoneInfo(DEFAULT_TIME_ZONE)
app.state.forecast_model = DEFAULT_FORECAST_MODEL


def format_datetime(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return PAGE.replace("STREAM_URL", app.state.stream_url).replace(
        "FORECAST_TIME_ZONE", json.dumps(app.state.time_zone.key),
    )


@app.get("/dashboard/data")
def dashboard_data(request: Request) -> dict:
    conn = connect(request.app.state.db_path)
    try:
        rows = fetch_completed_rich_counts(conn)
    finally:
        conn.close()
    return {
        "buckets": [
            {
                "timestamp": timestamp,
                "date": format_datetime(timestamp),
                "car": car,
                "bus": bus,
                "truck": truck,
                "motorcycle": motorcycle,
                "bicycle": bicycle,
            }
            for timestamp, car, bus, truck, motorcycle, bicycle in rows
        ]
    }


@app.get("/dashboard/forecast")
def dashboard_forecast(
    request: Request,
    day: date | None = Query(default=None, alias="date"),
    replay_cutoff: int | None = None,
    model: Literal["prophet", "ridge_ar"] = DEFAULT_FORECAST_MODEL,
) -> dict:
    state = request.app.state
    conn = connect(state.db_path)
    try:
        return forecast_day(
            conn, state.camera, state.time_zone, int(time.time()), day, replay_cutoff,
            live_method=state.forecast_model, replay_method=model,
        )
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail="Unsupported forecast date") from error
    except (ImportError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail="Forecast model unavailable") from error
    finally:
        conn.close()


@app.post("/dashboard/clear")
def dashboard_clear(request: Request) -> dict:
    conn = connect(request.app.state.db_path)
    try:
        deleted_events = conn.execute("SELECT COUNT(*) FROM traffic_events").fetchone()[0]
        deleted_buckets = conn.execute("SELECT COUNT(*) FROM traffic_counts").fetchone()[0]
        clear_db(conn)
    finally:
        conn.close()
    return {"status": "ok", "deleted_events": deleted_events, "deleted_buckets": deleted_buckets}


def main() -> None:
    parser = argparse.ArgumentParser(description="Traffic monitoring dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--camera", default=DEFAULT_CAMERA)
    parser.add_argument("--forecast-model", choices=FORECAST_MODELS, default=DEFAULT_FORECAST_MODEL)
    parser.add_argument("--timezone", default=DEFAULT_TIME_ZONE)
    parser.add_argument("--stream-url", default="http://127.0.0.1:8002/stream")
    args = parser.parse_args()
    app.state.db_path = args.db
    app.state.stream_url = args.stream_url
    app.state.camera = args.camera
    app.state.forecast_model = args.forecast_model
    try:
        app.state.time_zone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error(f"Unknown time zone: {args.timezone}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
