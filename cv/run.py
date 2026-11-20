import argparse
import logging
import queue
import threading
import time
from collections import Counter
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

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


def _mjpeg_frames():
    while active_detector is not None and not active_detector.stop_event.is_set():
        frame = active_detector.jpeg
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.1)


@stream_app.get("/stream")
def stream():
    return StreamingResponse(
        _mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


class WallClock:
    def now(self) -> float:
        return time.time()


def _flush(conn, events: list) -> int:
    if not events:
        return 0
    insert_events(conn, events)
    buckets: dict[int, Counter] = {}
    for event in events:
        buckets.setdefault(event.minute, Counter())[event.vehicle_type] += 1
    rows = [
        (
            minute * 60,
            counts["car"],
            counts["bus"],
            counts["truck"],
            counts["motorcycle"],
            counts["bicycle"],
        )
        for minute, counts in sorted(buckets.items())
    ]
    upsert_counts(conn, rows)
    return len(events)


def run(args: argparse.Namespace) -> None:
    global active_detector
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db)
    events: queue.Queue = queue.Queue(maxsize=args.queue_size)
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
    active_detector = detector
    server = uvicorn.Server(
        uvicorn.Config(stream_app, host=args.stream_host, port=args.stream_port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True, name="stream-server").start()
    detector.start()
    log.info("processing %s into %s; stream at http://%s:%d/stream",
             args.url, args.db, args.stream_host, args.stream_port)
    last_checked_cutoff = None
    last_checked_at = None
    saved_cutoff = None
    counts_changed = False
    try:
        while detector.is_alive():
            batch = []
            try:
                batch.append(events.get(timeout=1))
            except queue.Empty:
                pass
            while True:
                try:
                    batch.append(events.get_nowait())
                except queue.Empty:
                    break
            written = _flush(conn, batch)
            if written:
                log.info("stored %d vehicle events", written)
                counts_changed = True
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
        log.info("stopping detector")
    finally:
        detector.stop()
        detector.join(timeout=10)
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        _flush(conn, remaining)
        conn.close()
        active_detector = None
        server.should_exit = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube YOLO/ByteTrack traffic pipeline.")
    parser.add_argument("--url", required=True, help="YouTube live stream URL")
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
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(parse_args())
