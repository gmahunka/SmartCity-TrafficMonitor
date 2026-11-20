# SmartCity-TrafficMonitor
Smart city project for autonomous traffic  monitoring at the Budapest University of Technology and Economics

## Setup

From the repository root, using the provided venv:

```powershell
.\venv\Scripts\python.exe -m pip install -e .
```

or install the runtime dependencies directly:

```powershell
.\venv\Scripts\python.exe -m pip install fastapi uvicorn
```

## Running the data ingestion

Detections (car / bus / truck / motorcycle / bicycle) are aggregated into per-minute counts and
stored in SQLite at `data/traffic.db`. The database is cleared on every
fresh run.

## Running the YouTube CV pipeline

The integrated CV runner resolves a YouTube stream with `yt-dlp`, keeps only
the newest decoded frame, runs YOLO with ByteTrack tracking, and writes each
counted vehicle into the same SQLite database used by the dashboard. The
included `yolo26n.pt` model is used by default.

Install the CV dependencies after installing the project:

```powershell
.\venv\Scripts\python.exe -m pip install -e .
```

Then start the pipeline with a YouTube URL:

```powershell
.\venv\Scripts\python.exe -m cv.run `
  --url "https://www.youtube.com/watch?v=sTF-6_xinUU" `
  --db data\traffic.db
```

The CV runner also serves the annotated live stream at
`http://127.0.0.1:8002/stream`. The dashboard embeds this stream automatically.
On the same computer, start the dashboard without `--stream-url` so it uses
the local stream:

```powershell
.\venv\Scripts\python.exe -m dashboard.app --port 8001
```

Open these local links:

- Dashboard: <http://127.0.0.1:8001/dashboard>
- Annotated stream: <http://127.0.0.1:8002/stream>
- Dashboard data: <http://127.0.0.1:8001/dashboard/data>

If the dashboard runs on another machine, replace `CAMERA-HOST` with the
actual hostname or IP address of the computer running `cv.run`:

```powershell
.\venv\Scripts\python.exe -m dashboard.app `
  --stream-url "http://CAMERA-HOST:8002/stream"
```

Tracked vehicles are counted once after `--min-hits` frames. Use `--show` for
an OpenCV debug window. YouTube extraction may require the
Deno JavaScript runtime for current `yt-dlp` versions.
If FFmpeg logs a `Cannot reuse HTTP connection for different host` warning,
the runner disables HTTP connection reuse automatically because YouTube can
redirect signed media URLs between different `googlevideo.com` hosts.

The dashboard displays only completed minute buckets and includes separate
series for cars, buses, trucks, motorcycles, and bicycles.

### Batch mode (offline, one-shot, stdlib only)

Generate mock events, aggregate, and write them in a single run:

```powershell
.\venv\Scripts\python.exe -m data.ingest --count 200 --seed 42 --db data\traffic.db
```

- `--count` — number of mock events to generate (default: 200)
- `--seed` — RNG seed for reproducible type distribution (default: 42)
- `--db` — SQLite database path (default: `data/traffic.db`)

The script prints a per-minute count table and totals per vehicle type.

## Running the dashboard

The dashboard reads the same SQLite database and preserves it during normal
operation.
Start it separately from the ingest service:

```powershell
.\venv\Scripts\python.exe -m dashboard.app --port 8001
```

Open <http://127.0.0.1:8001/dashboard> in a browser. The page polls the
database every minute and displays cars, buses, trucks, motorcycles, and bicycles on a live
graph. Only completed minute buckets are shown, so the current in-progress
minute appears after the minute ends.

Use the **Clear database** button to explicitly remove all stored raw events and
aggregate minute buckets. The action requires confirmation and does not remove
the database schema.

The `tests/` folder contains small regression tests for the minute-boundary
query and dashboard response. They prevent changes to the live update behavior
from silently showing incomplete data.

Options:

- `dashboard.app`: `--host` (default `127.0.0.1`), `--port` (default `8001`),
  `--db` (default `data/traffic.db`)

### Traffic forecasting

The CV runner uses Prophet to forecast the next 30 minutes every five minutes.
It needs nine complete five-minute buckets from the preceding four hours,
including the most recent three buckets. Prophet fits a local trend with daily
and weekly seasonality disabled while traffic history is sparse. The main graph overlays the
forecast as total vehicles per minute. The forecast chart shows totals per five
minutes. Both charts keep the previous day's forecast points visible if they
extend past midnight. Select **Today** to focus the main graph on today's traffic.
Choose a recorded date and a **Replay model** to compare Prophet or ridge
regression with the recent-average baseline on complete target buckets. Prophet
replay scores up to 24 forecast times per day. All eligible five-minute starts
can be selected. The highlighted window runs from the chosen start through the
following 30 minutes, including any portion after midnight. Saved forecasts keep their
originally issued values and are labeled by method.

Restart both processes after updating the project to start issuing and displaying
forecasts. Keep the runner and dashboard on the same database and pass the same
`--camera` if you use a camera ID other than `cam1`. Use one camera per database
for this version. The dashboard groups dates in `Europe/Budapest` by default.
Use `--timezone` to select a different IANA time zone.
To issue live ridge forecasts instead, pass `--forecast-model ridge_ar` to both
the CV runner and the dashboard.

The dashboard's **Clear database** action also removes saved forecasts.
