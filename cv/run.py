import argparse
import asyncio
import logging
import queue
import signal
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from common.events import VEHICLE_TYPES, VehicleEvent
from data.db import DEFAULT_DB_PATH, connect, insert_events, upsert_counts
from data.forecast import (
    DEFAULT_CAMERA,
    DEFAULT_FORECAST_MODEL,
    FORECAST_MODELS,
    forecast_cutoff,
    save_due_forecast,
)

from .detector import CounterConfig, DetectorSource

log = logging.getLogger("traffic.cv")
stream_app = FastAPI(title="Traffic camera stream")
active_detector: DetectorSource | None = None


async def _mjpeg_frames(detector: DetectorSource | None):
    while detector is not None and not detector.stop_event.is_set():
        frame = detector.jpeg
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        await asyncio.sleep(0.1)


@stream_app.get("/stream")
def stream():
    return StreamingResponse(
        _mjpeg_frames(active_detector),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@stream_app.get("/health")
def health() -> JSONResponse:
    detector = active_detector
    if detector is None or not detector.is_alive() or detector.stop_event.is_set():
        return JSONResponse({"status": "detector_unavailable"}, status_code=503)

    last_processed = detector.last_processed_at
    frame_age = None if last_processed is None else time.monotonic() - last_processed
    status = "online"
    if frame_age is None:
        status = "waiting_for_frames"
    elif frame_age > detector.reader.stall_s:
        status = "stale"
    return JSONResponse(
        {
            "status": status,
            "camera": detector.camera,
            "frame_age_seconds": None if frame_age is None else round(frame_age, 1),
            "processed_fps": detector.status["fps"] if status == "online" else 0.0,
            "reconnects": detector.reader.info["reconnects"],
            "url_refreshes": detector.reader.info["url_refreshes"],
        },
        status_code=200 if status == "online" else 503,
    )


class WallClock:
    def now(self) -> float:
        return time.time()


def _flush(conn, events: list[VehicleEvent]) -> int:
    if not events:
        return 0
    buckets: dict[int, Counter] = {}
    for event in events:
        buckets.setdefault(event.minute, Counter())[event.vehicle_type] += 1
    rows = [
        (minute * 60, *(counts[vehicle_type] for vehicle_type in VEHICLE_TYPES))
        for minute, counts in sorted(buckets.items())
    ]
    with conn:
        insert_events(conn, events, commit=False)
        upsert_counts(conn, rows, commit=False)
    return len(events)


def _read_batch(events: queue.Queue[VehicleEvent], timeout: float) -> list[VehicleEvent]:
    try:
        first = events.get(timeout=timeout)
    except queue.Empty:
        return []
    # The runner is the only consumer. Limit this batch to the current backlog.
    return [first, *(events.get_nowait() for _ in range(events.qsize()))]


def run(args: argparse.Namespace, stopping: threading.Event | None = None) -> None:
    global active_detector
    stopping = threading.Event() if stopping is None else stopping
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    events: queue.Queue[VehicleEvent] = queue.Queue(maxsize=args.queue_size)
    detector = DetectorSource(
        bus=events,
        clock=WallClock(),
        url=args.url,
        camera=args.camera,
        model=args.model,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        counter_config=CounterConfig(),
        max_fps=args.max_fps,
        show=args.show,
        max_height=args.max_height,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            stream_app, host=args.stream_host, port=args.stream_port, log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True, name="stream-server")

    with closing(connect(args.db)) as conn:
        active_detector = detector
        last_checked_cutoff = None
        last_checked_at = None
        saved_cutoff = None
        counts_changed = False
        try:
            if stopping.is_set():
                return
            server_thread.start()
            detector.start()
            log.info("processing camera %s into %s (stream port %d)", args.camera, args.db, args.stream_port)
            while not stopping.is_set():
                if not detector.is_alive():
                    raise RuntimeError("Detector worker stopped unexpectedly")
                if detector.reader.ident is not None and not detector.reader.is_alive():
                    raise RuntimeError("Frame reader stopped unexpectedly")
                if not server_thread.is_alive():
                    raise RuntimeError("Stream server stopped unexpectedly")

                written = _flush(conn, _read_batch(events, timeout=1))
                counts_changed = counts_changed or written > 0
                if stopping.is_set():
                    break
                now = int(time.time())
                cutoff = forecast_cutoff(now)
                if cutoff != saved_cutoff and (
                    cutoff != last_checked_cutoff or (counts_changed and now != last_checked_at)
                ):
                    try:
                        if save_due_forecast(conn, args.camera, now, args.forecast_model):
                            saved_cutoff = cutoff
                    except (ImportError, RuntimeError):
                        log.exception("Could not issue %s forecast", args.forecast_model)
                        saved_cutoff = cutoff
                    last_checked_cutoff = cutoff
                    last_checked_at = now
                    counts_changed = False
        except KeyboardInterrupt:
            pass
        finally:
            log.info("stopping camera %s", args.camera)
            detector.stop()
            server.should_exit = True
            active_detector = None
            deadline = time.monotonic() + 10
            # The producer may still be finishing a frame or waiting for queue space.
            while detector.is_alive() and time.monotonic() < deadline:
                _flush(conn, _read_batch(events, timeout=0.1))
            _flush(conn, _read_batch(events, timeout=0))
            if detector.is_alive():
                log.warning("detector did not stop before the queue drain deadline")
            if server_thread.ident is not None:
                server_thread.join(timeout=5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO/ByteTrack traffic pipeline.")
    parser.add_argument("--url", required=True, help="Camera URL, video file, or webcam index")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--camera", default=DEFAULT_CAMERA)
    parser.add_argument("--forecast-model", choices=FORECAST_MODELS, default=DEFAULT_FORECAST_MODEL)
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default=None)
    parser.add_argument("--max-fps", type=float, default=30)
    parser.add_argument("--max-height", type=int, default=720)
    parser.add_argument("--queue-size", type=int, default=10000)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--stream-host", default="127.0.0.1")
    parser.add_argument("--stream-port", type=int, default=8002)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level)
    stopping = threading.Event()

    def request_stop(signum, frame):
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run(args, stopping)


if __name__ == "__main__":
    main()
