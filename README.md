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

Detections (car / bike / bus) are aggregated into per-minute counts and
stored in SQLite at `data/traffic.db`. The database is cleared on every
fresh run.

### Live mode (HTTP bridge, simulates Person 2 -> Person 3)

Terminal 1 — start the ingest service (clears the DB on startup):

```powershell
.\venv\Scripts\python.exe -m data.ingest_service --port 8000
```

Terminal 2 — start the mock sender (emits 0–5 random detections every 5 s
and POSTs them to the service):

```powershell
.\venv\Scripts\python.exe -m data.mock_sender
```

The service writes to SQLite and answers each POST only after the commit;
the sender prints that ack (minute bucket + counts written), so a printed
ack means "DB write done for that timestamp". Inspect the stored table at
any time:

```powershell
curl.exe http://127.0.0.1:8000/counts
```

Options:

- `data.ingest_service`: `--host` (default 127.0.0.1), `--port` (default 8000), `--db` (default `data/traffic.db`)
- `data.mock_sender`: `--url`, `--interval` (default 5 s), `--seed`, `--ticks N` (stop after N ticks, 0 = run forever)

### Batch mode (offline, one-shot, stdlib only)

Generate mock events, aggregate, and write them in a single run:

```powershell
.\venv\Scripts\python.exe -m data.ingest --count 200 --seed 42 --db data\traffic.db
```

- `--count` — number of mock events to generate (default: 200)
- `--seed` — RNG seed for reproducible type distribution (default: 42)
- `--db` — SQLite database path (default: `data/traffic.db`)

The script prints a per-minute count table and totals per vehicle type.
