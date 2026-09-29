# AGENTS.md

Smart city traffic monitoring: stream ingestion → CV detection/tracking/counting → persistence/analytics/dashboard. BME semester project, split across 3 people. Repo is currently greenfield (README only); the structure below is the agreed plan, not yet existing code.

## Division of work (do not cross boundaries casually)

- **Person 1 — Ingestion/infra**: `cv2.VideoCapture` + streamlink/yt-dlp capture loop, threaded ring-buffer frame queue (drop stale frames when inference lags), reconnect/backoff, Dockerfile + docker-compose.yml (CUDA/TensorRT or OpenVINO/ONNX runtime), `.env`/`config.yaml` parsing (feed URLs, buffer sizes, log levels).
- **Person 2 — CV pipeline**: YOLOv8/YOLO11 nano/small inference; keep only classes car/truck/bus/motorcycle above confidence threshold; ByteTrack for persistent track IDs; configurable virtual tripwires/polygon zones with vector intersection → direction (inbound/outbound); `--visualize` debug overlay (boxes, trails, count lines) usable on sample MP4s.
- **Person 3 — Data/UI**: storage layer (SQLite); schema for raw crossing events + aggregates; analytics (vehicles/min/hour, density peaks, class distribution, per-lane flow); Streamlit/Dash or FastAPI stats endpoint; optional congestion alerting.

## Hard interface contracts (integration points — changing these breaks another person's work)

- Person 1 exposes exactly: `get_latest_frame() -> (timestamp, numpy_array)` (generator or queue).
- Person 2 emits structured events:
  `VehicleEvent(timestamp=1727104800, track_id=42, vehicle_type="car", direction="inbound", confidence=0.88)` (dataclass or dict).
- Each person must be testable standalone with mock data (e.g., local MP4 instead of live stream, fake frame source instead of real ingestion) before integration.

## Conventions

- Python; shared types (VehicleEvent, config schema) live in one common module so all three areas import the same definitions.
- Merge-conflict minimization: keep the three areas in separate top-level directories/packages; don't refactor another person's area.
- Dev environment is Windows (win32, PowerShell 5.1); production runtime is Docker/compose.

No build/test/lint tooling exists yet — verify before assuming commands like pytest or ruff are configured.
