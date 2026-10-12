"""
safety/incident_recorder.py
============================
Rolling Dashcam Buffer: Auto-records 10-second video clips (5s pre-trigger + 5s post-trigger)
whenever a risk alert (drowsiness, phone use, critical fatigue, distracted gaze) fires.
Saves clips to reports/incidents/ in background threads without blocking detection.
"""

import os
import time
import json
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class IncidentClipRecorder:
    def __init__(
        self,
        output_dir: str = "reports/incidents",
        buffer_seconds: float = 5.0,
        post_trigger_seconds: float = 5.0,
        fps: float = 25.0,
        cooldown_seconds: float = 15.0,
    ):
        self.output_dir = output_dir
        self.fps = fps
        self.buffer_size = int(buffer_seconds * fps)
        self.post_trigger_frames = int(post_trigger_seconds * fps)
        self.cooldown_seconds = cooldown_seconds

        os.makedirs(output_dir, exist_ok=True)
        self._frame_buffer = deque(maxlen=self.buffer_size)
        self._lock = threading.Lock()

        # Recording state
        self._is_recording = False
        self._active_incident_type = ""
        self._post_frames = []
        self._pre_frames = []
        self._last_trigger_time = 0.0

    def push_frame(self, frame: np.ndarray):
        """Pushes a copy of current frame into rolling buffer (low-res copy for memory efficiency)."""
        with self._lock:
            # Resize slightly to 640x480 if large to save memory
            h, w = frame.shape[:2]
            if w > 640:
                small = cv2.resize(frame, (640, int(h * 640 / w)))
            else:
                small = frame.copy()

            if self._is_recording:
                self._post_frames.append(small)
                if len(self._post_frames) >= self.post_trigger_frames:
                    # Clip recording complete! Dispatch save in background thread
                    pre = list(self._pre_frames)
                    post = list(self._post_frames)
                    inc_type = self._active_incident_type

                    self._is_recording = False
                    self._post_frames = []
                    self._pre_frames = []

                    threading.Thread(
                        target=self._save_clip_worker,
                        args=(pre, post, inc_type),
                        daemon=True,
                    ).start()
            else:
                self._frame_buffer.append(small)

    def trigger(self, incident_type: str) -> bool:
        """
        Triggers an incident recording if not currently recording or in cooldown.
        Returns True if recording started.
        """
        now = time.time()
        with self._lock:
            if self._is_recording:
                return False
            if now - self._last_trigger_time < self.cooldown_seconds:
                return False

            self._last_trigger_time = now
            self._is_recording = True
            self._active_incident_type = incident_type
            self._pre_frames = list(self._frame_buffer)
            self._post_frames = []
            logger.info(f"[Dashcam] Incident triggered: {incident_type}. Recording clip...")
            return True

    def _save_clip_worker(self, pre_frames: list, post_frames: list, incident_type: str):
        """Encodes frames to MP4 video in background."""
        all_frames = pre_frames + post_frames
        if not all_frames:
            return

        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_type = incident_type.replace(" ", "_").replace(":", "_").upper()
        filename = f"{timestamp_str}_{safe_type}.mp4"
        filepath = os.path.join(self.output_dir, filename)

        h, w = all_frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, self.fps, (w, h))

        try:
            for f in all_frames:
                # Add stamp watermark
                stamped = f.copy()
                cv2.putText(
                    stamped,
                    f"INCIDENT: {safe_type} | {timestamp_str}",
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 255),
                    2,
                )
                writer.write(stamped)
            writer.release()
            logger.info(f"[Dashcam] Incident clip saved: {filepath} ({len(all_frames)} frames)")

            # Update incidents registry JSON for web dashboard
            self._update_registry(filename, timestamp_str, incident_type, len(all_frames) / self.fps)
        except Exception as e:
            logger.error(f"[Dashcam] Error saving incident clip: {e}")
            if writer:
                writer.release()

    def _update_registry(self, filename: str, timestamp: str, incident_type: str, duration: float):
        registry_path = os.path.join(self.output_dir, "incidents.json")
        try:
            records = []
            if os.path.exists(registry_path):
                with open(registry_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            records.insert(0, {
                "filename": filename,
                "timestamp": timestamp,
                "incident_type": incident_type,
                "duration_seconds": round(duration, 1),
            })
            with open(registry_path, "w", encoding="utf-8") as f:
                json.dump(records[:100], f, indent=2)
        except Exception as e:
            logger.debug(f"[Dashcam] Registry update error: {e}")
