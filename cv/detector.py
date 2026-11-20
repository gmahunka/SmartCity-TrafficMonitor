"""
Camera source: YOLO detection + ByteTrack tracking on every processed frame of a live
stream (RTSP / HTTP / webcam index / video file / YouTube link), producing one
VehicleEvent per vehicle - not one per frame.

Counting
    a track is counted once it has been seen in --min-hits frames
The class of a track is the majority vote over its frames (a car briefly seen as a
truck stays a car).

Live streams are read by a background thread that keeps only the newest frame, so a
slow detector skips frames instead of falling minutes behind. Files are paced at their
own frame rate to behave like a live feed. Lost streams are reopened with back-off.
"""
from __future__ import annotations

import os
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from common.events import VehicleEvent
from .youtube import is_youtube, refresh_due
from .youtube import resolve as resolve_youtube

log = logging.getLogger("traffic.cv.detector")

COCO_TO_CLASS = {2: "car", 5: "bus", 7: "truck", 3: "motorcycle", 1: "bicycle"}

COLORS = {'car': (255, 160, 60), 'bus': (40, 200, 255), 'truck': (60, 120, 255),
          'motorcycle': (200, 90, 220), 'bicycle': (90, 210, 120)}


# FFmpeg: give up on a dead network read after 15 s instead of hanging forever.
# YouTube may move a signed URL between googlevideo.com hosts; reusing the
# previous HTTP connection then causes FFmpeg's "Cannot reuse HTTP connection"
# error.
os.environ.setdefault(
    'OPENCV_FFMPEG_CAPTURE_OPTIONS',
    'rw_timeout;15000000|timeout;15000000|http_persistent;0',
)


class FrameReader(threading.Thread):
    """Keeps the newest frame of a live stream; paces files / recorded videos at their FPS.

    YouTube: the signed stream URL is re-requested 10 min before it expires and after
    every failure; if no frame arrives for `stall_s` seconds the stream is reopened.
    """

    def __init__(self, url: str, loop_file: bool = True, max_height: int = 720,
                 stall_s: float = 20.0):
        super().__init__(daemon=True, name='frame-reader')
        self.url = url
        self.youtube = is_youtube(url)
        self.is_file = not (url.isdigit() or '://' in url)
        self.loop_file, self.max_height, self.stall_s = loop_file, max_height, stall_s
        self.lock = threading.Lock()
        self.frame: Optional[np.ndarray] = None
        self.seq = 0
        self.last_frame_t = 0.0
        self.stop_event = threading.Event()
        self.error = None
        self.fps = 0.0
        self.info = {'title': url, 'live': None, 'reconnects': 0, 'url_refreshes': 0}
        self._stream = None

    def _open(self):
        paced = self.is_file
        if self.url.isdigit():
            cap = cv2.VideoCapture(int(self.url))
        elif self.youtube:
            self._stream = resolve_youtube(self.url, self.max_height)
            self.info.update(title=self._stream.title, live=self._stream.is_live,
                             height=self._stream.height)
            paced = not self._stream.is_live          # a recorded video: play at its own speed
            cap = cv2.VideoCapture(self._stream.url, cv2.CAP_FFMPEG)
        else:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG) if '://' in self.url \
                else cv2.VideoCapture(self.url)
        if not paced:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap, paced

    def run(self):
        backoff = 2.0
        while not self.stop_event.is_set():
            try:
                cap, paced = self._open()
            except Exception as e:                        # yt-dlp / network problem
                self.error = f'cannot resolve stream: {e} (retry in {backoff:.0f} s)'
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 120)
                continue
            if not cap.isOpened():
                self.error = f'cannot open stream, retry in {backoff:.0f} s'
                cap.release()
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 120)
                continue
            self.error, backoff = None, 2.0
            self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if self.fps > 120:                            # some HLS streams report 1000
                self.fps = 30.0
            period = 1.0 / self.fps if paced else 0.0
            next_t = time.time()
            planned = False
            while not self.stop_event.is_set():
                if self._stream is not None and refresh_due(self._stream):
                    self.info['url_refreshes'] += 1       # signed URL about to expire
                    planned = True
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                with self.lock:
                    self.frame, self.seq = frame, self.seq + 1
                    self.last_frame_t = time.time()
                if period:
                    next_t += period
                    time.sleep(max(0.0, next_t - time.time()))
            cap.release()
            if self.is_file and not self.loop_file:
                self.error = 'end of file'
                return
            if planned:
                continue                                  # fresh URL right away, no pause
            if not self.stop_event.is_set():
                self.info['reconnects'] += 1
                self.error = 'stream interrupted, reconnecting'
                self.stop_event.wait(1.0)

    def stalled(self) -> bool:
        return self.seq > 0 and time.time() - self.last_frame_t > self.stall_s

    def latest(self) -> Tuple[int, Optional[np.ndarray]]:
        with self.lock:
            return self.seq, (None if self.frame is None else self.frame.copy())

    def stop(self):
        self.stop_event.set()


@dataclass(frozen=True)
class CounterConfig:
    min_hits: int = 2
    forget_after: float = 3.0
    min_move: float = 0.02
    roi: Optional[list[tuple[float, float]]] = None


class TrackCounter:
    """Turns per-frame tracked boxes into one count per vehicle.

    A track counts once it was seen in `min_hits` frames. The movement threshold
    is disabled by default so low-FPS streams can still register stable tracks.
    `roi`: optional polygon [(x, y), ...] in 0..1; detections outside it are ignored.
    """

    def __init__(self, config: "CounterConfig | None" = None):
        config = config or CounterConfig()
        self.min_hits = config.min_hits
        self.forget_after = config.forget_after
        self.min_move = config.min_move
        self.roi = np.array(config.roi, np.float32).reshape(-1, 1, 2) if config.roi else None
        self.tracks = {}

    def in_roi(self, x: float, y: float) -> bool:
        return self.roi is None or cv2.pointPolygonTest(self.roi, (float(x), float(y)), False) >= 0

    def update(self, now: float, dets, frame_w: int, frame_h: int):
        """dets: [(track_id, vclass, conf, cx, cy)] -> [(track_id, vclass, conf, direction)]"""
        counted = []
        for tid, vclass, conf, cx, cy in dets:
            cur = (cx / frame_w, cy / frame_h)
            if not self.in_roi(*cur):
                continue
            t = self.tracks.setdefault(tid, {'hits': 0, 'votes': Counter(), 'conf': [],
                                     'first': cur, 'counted': False})
            t['hits'] += 1
            t['votes'][vclass] += 1
            t['conf'].append(conf)
            t['last'] = now
            if not t['counted']:
                moved = np.hypot(cur[0] - t['first'][0], cur[1] - t['first'][1]) / np.sqrt(2)
                fire = t['hits'] >= self.min_hits and moved >= self.min_move
                if fire:
                    t['counted'] = True
                    counted.append((tid, t['votes'].most_common(1)[0][0],
                                    float(np.mean(t['conf'])), None))
        for tid in [k for k, v in self.tracks.items() if now - v['last'] > self.forget_after]:
            del self.tracks[tid]
        return counted


class DetectorSource(threading.Thread):
    def __init__(self, bus, clock, url: str, camera: str = 'cam1', model: str = 'yolo26n.pt',
                 conf: float = 0.35, imgsz: int = 640, device=None,
                 counter_config: CounterConfig | None = None,
                 max_fps: float = 30.0, show: bool = False,
                 loop_file: bool = True, max_height: int = 720):
        super().__init__(daemon=True, name='detector')
        from ultralytics import YOLO
        self.yolo = YOLO(model)
        self.bus, self.clock, self.camera = bus, clock, camera
        self.reader = FrameReader(url, loop_file, max_height)
        self.conf, self.imgsz, self.device = conf, imgsz, device
        self.counter_config = counter_config or CounterConfig()
        self.counter = TrackCounter(self.counter_config)
        self.roi = self.counter_config.roi
        self.max_fps, self.show = max_fps, show
        self.stop_event = threading.Event()
        self.jpeg: Optional[bytes] = None
        self.status = {'source': 'camera', 'camera': camera, 'url': url, 'model': model,
                       'mode': 'movement', 'running': False, 'fps': 0.0, 'frames': 0,
                       'detections': 0, 'tracked_detections': 0, 'events': 0,
                       'active_tracks': 0, 'error': None}

    def run(self):
        self.reader.start()
        self.status['running'] = True
        last_seq, fps, totals, t_prev = 0, 0.0, Counter(), None
        min_period = 1.0 / self.max_fps if self.max_fps else 0.0
        while not self.stop_event.is_set():
            t0 = time.time()
            seq, frame = self.reader.latest()
            self.status['error'] = self.reader.error or \
                ('no new frames - waiting for the stream' if self.reader.stalled() else None)
            self.status['stream'] = dict(self.reader.info)
            if frame is None or seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            try:
                r = self.yolo.track(frame, persist=True, conf=self.conf, imgsz=self.imgsz,
                                    classes=list(COCO_TO_CLASS), tracker='bytetrack.yaml',
                                    device=self.device, verbose=False)[0]
            except Exception as e:                                  # keep running
                self.status['error'] = f'detector: {e}'
                time.sleep(0.5)
                continue
            h, w = frame.shape[:2]
            dets, boxes = [], []
            if r.boxes is not None and len(r.boxes):
                ids = r.boxes.id.int().tolist() if r.boxes.id is not None else [None] * len(r.boxes)
                for tid, (x1, y1, x2, y2), c, s in zip(ids, r.boxes.xyxy.tolist(),
                                                       r.boxes.cls.int().tolist(),
                                                       r.boxes.conf.tolist()):
                    vclass = COCO_TO_CLASS.get(c)
                    if vclass is None:
                        continue
                    boxes.append((tid, vclass, s, x1, y1, x2, y2))
                    if tid is not None:
                        dets.append((tid, vclass, s, (x1 + x2) / 2, (y1 + y2) / 2))
            self.status['detections'] += len(boxes)
            self.status['tracked_detections'] += len(dets)
            for tid, vclass, confidence, x1, y1, x2, y2 in boxes:
                if tid is not None:
                    log.info(
                        "detected track_id=%s class=%s confidence=%.3f "
                        "box=(%.1f, %.1f, %.1f, %.1f)",
                        tid, vclass, confidence, x1, y1, x2, y2,
                    )
            now = self.clock.now()
            for tid, vclass, conf, direction in self.counter.update(now, dets, w, h):
                self.bus.put(VehicleEvent(timestamp=int(now), vehicle_type=vclass,
                                          camera=self.camera, track_id=tid,
                                          confidence=round(conf, 3), direction=direction))
                totals[vclass] += 1
                self.status['events'] += 1
            dt = time.time() - t0
            if t_prev is not None:                       # throughput, not just model speed
                period = max(time.time() - t_prev, 1e-6)
                fps = 0.9 * fps + 0.1 / period if fps else 1.0 / period
            t_prev = time.time()
            self.status.update(fps=round(fps, 1), frames=self.status['frames'] + 1,
                               active_tracks=len(self.counter.tracks))
            self._render(frame, boxes, totals, fps)
            if min_period > dt:
                time.sleep(min_period - dt)
        self.reader.stop()
        self.status['running'] = False

    def _render(self, frame, boxes, totals, fps):
        h, w = frame.shape[:2]
        for tid, vclass, s, x1, y1, x2, y2 in boxes:
            col = COLORS.get(vclass, (255, 255, 255))
            counted = tid in self.counter.tracks and self.counter.tracks[tid]['counted']
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), col, 2 if counted else 1)
            cv2.putText(frame, f'{vclass} {tid if tid is not None else ""}', (int(x1), int(y1) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        if self.roi:
            pts = np.array([[int(x * w), int(y * h)] for x, y in self.roi], np.int32)
            cv2.polylines(frame, [pts], True, (255, 200, 0), 1)
        txt = '  '.join(f'{k}:{v}' for k, v in sorted(totals.items())) or 'no vehicles yet'
        cv2.rectangle(frame, (0, 0), (w, 26), (20, 20, 20), -1)
        cv2.putText(frame, f'{fps:4.1f} FPS | counted {txt}', (8, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1, cv2.LINE_AA)
        scale = 960.0 / w if w > 960 else 1.0
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
        ok, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.jpeg = buf.tobytes()
        if self.show:
            cv2.imshow('traffic analyser', frame)
            cv2.waitKey(1)

    def stop(self):
        self.stop_event.set()
