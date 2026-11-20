# AGENTS.md

BME semester project for smart city traffic monitoring. The integrated runner reads camera frames, detects and counts tracked vehicles, and stores events and minute counts in SQLite. A separate FastAPI dashboard displays the counts and embeds the annotated stream.

## Ownership and repository layout

- **Person 1 — Capture and infrastructure.** `FrameReader` in `cv/detector.py` uses OpenCV to read files, webcam indices, and stream URLs. It retains only the newest frame and handles reconnects. `cv/youtube.py` resolves and refreshes YouTube media URLs with `yt-dlp`. Docker Compose deployment and shared configuration are planned work.
- **Person 2 — Detection, tracking, and counting.** `DetectorSource`, `TrackCounter`, and `CounterConfig` in `cv/detector.py` use YOLO and ByteTrack. The default model is the bundled `yolo26n.pt` file. A track qualifies after two hits by default. The counter supports an optional polygon ROI and movement threshold in code. Tripwire crossing and inbound/outbound direction are planned work.
- **Person 3 — Persistence, analytics, and dashboard.** `data/db.py` owns the SQLite schema and queries. `data/mock_feed.py` generates offline test events, `data/ingest.py` is the batch mock writer, and `dashboard/app.py` serves the UI and JSON data.
- `cv/run.py` wires capture, CV, persistence, and the MJPEG stream together. Coordinate changes at these boundaries with the relevant owners and keep changes scoped to the affected components. Put cross-component types in `common/`.

## Integration contracts

- The current CV consumer calls `FrameReader.latest() -> (sequence_number, frame_or_none)`. Frames are copied NumPy arrays. The sequence number lets the detector skip frames it already processed. For a future standalone capture adapter, preserve the agreed `get_latest_frame() -> (timestamp, numpy_array)` interface and coordinate its integration with the CV owner.
- Pass `common.events.VehicleEvent` instances through the event queue. `timestamp` is an integer Unix timestamp in seconds. Use `VEHICLE_TYPES` for the five supported classes (car, bus, truck, motorcycle, bicycle). Events also carry a camera name and optional track ID, confidence, and direction. The current counter emits `direction=None`.
- The integrated runner appends raw events to `traffic_events` and adds to the per-minute totals in `traffic_counts`. Count rows are ordered `(minute_start, car, bus, truck, motorcycle, bicycle)`, with `minute_start` in Unix seconds. Preserve compatibility with existing SQLite files, including older counts tables that only have car and bus columns.
- The CV runner and dashboard default to `data/traffic.db`. Pass the same `--db` path to both when using a different database. Normal startup preserves the stored data. The dashboard's confirmed clear action calls `POST /dashboard/clear` to delete raw events and minute counts.
- Keep `GET /dashboard` and `GET /dashboard/data` available. Return only buckets whose start precedes the start of the current minute, and keep the browser's 60-second polling cadence unless UI requirements change. The dashboard defaults to embedding `http://127.0.0.1:8002/stream` and accepts a `--stream-url` override.

## Development and verification

Use Python 3.10 or later. Run these PowerShell commands from the repository root.

```powershell
.\venv\Scripts\python.exe -m pip install -e .
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

- Keep each area independently testable. Use a local MP4 for capture, synthetic detections for `TrackCounter`, and fake events with a temporary SQLite database for persistence and dashboard checks. The standard-library test suite covers counting, SQLite writes, and the dashboard's completed-minute and clear behavior.
- For a local CV and dashboard check, start the following in separate PowerShell terminals. Use an existing MP4 or a stream URL with `--url`. `--show` opens the optional OpenCV preview. The dashboard is at `http://127.0.0.1:8001/dashboard`.

```powershell
.\venv\Scripts\python.exe -m cv.run --url .\sample.mp4 --db .\data\smoke.db
.\venv\Scripts\python.exe -m dashboard.app --db .\data\smoke.db
```

- Use a disposable `--db` path for `python -m data.ingest`. The mock writer clears the selected database before inserting counts. Its current aggregation emits an extra bicycle column, so a nonempty batch fails the six-column validation.
