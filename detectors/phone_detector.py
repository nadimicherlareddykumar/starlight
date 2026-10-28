"""
detectors/phone_detector.py
============================
YOLO-based phone detection — strict conf + area/aspect ratio filters.
"""

import logging
from dataclasses import dataclass, field

import cv2

logger = logging.getLogger(__name__)


@dataclass
class PhoneDetector:
    """
    Detects cell phones using YOLOv8.
    Runs every `skip_frames` frames for performance.
    """
    model_path:    str   = "yolov8n.pt"
    skip_frames:   int   = 4
    phone_conf:    float = 0.70
    phone_max_area: float = 0.25
    phone_min_aspect: float = 0.4   # bh/bw — very wide box → not a phone

    confirmation_frames: int = 2
    detected:    bool = field(default=False, init=False)
    _model:      object = field(default=None, init=False, repr=False)
    _frame_cnt:  int  = field(default=0, init=False)
    _boxes:      list = field(default_factory=list, init=False)
    _candidate_hits: int = field(default=0, init=False)

    def __post_init__(self):
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)
            logger.info(f"[PhoneDetector] YOLO model loaded: {self.model_path}")
        except Exception as exc:
            self._model = None
            logger.warning(f"[PhoneDetector] Disabled: {exc}")

    # ------------------------------------------------------------------
    def update(self, frame, inference_frame=None) -> bool:
        """
        Call every frame. Returns True if phone currently detected.
        Annotates frame in-place with bounding boxes.
        """
        self._frame_cnt += 1
        if self._model is None:
            self.detected = False
            self._boxes = []
            return False
        if self._frame_cnt % max(1, self.skip_frames) != 0:
            # Re-draw boxes from last detection even on skipped frames
            self._draw_boxes(frame)
            return self.detected

        model_frame = inference_frame if inference_frame is not None else frame
        H, W = model_frame.shape[:2]
        frame_area = H * W
        candidate_boxes = []

        for result in self._model(model_frame, verbose=False):
            for box in result.boxes:
                name = self._model.names[int(box.cls[0])]
                conf = float(box.conf[0])

                if name != "cell phone":
                    continue
                if conf < self.phone_conf:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                bw = x2 - x1
                bh = y2 - y1
                box_area = bw * bh

                if box_area / frame_area > self.phone_max_area:
                    continue                         # too large → false positive
                if bw > 0 and (bh / bw) < self.phone_min_aspect:
                    continue                         # too wide → not a phone

                candidate_boxes.append((x1, y1, x2, y2, conf))

        if candidate_boxes:
            self._candidate_hits += 1
            self._boxes = candidate_boxes
            self.detected = self._candidate_hits >= max(1, self.confirmation_frames)
        else:
            self._candidate_hits = 0
            self.detected = False
            self._boxes = []

        self._draw_boxes(frame)
        return self.detected

    # ------------------------------------------------------------------
    def _draw_boxes(self, frame):
        if not self.detected:
            return
        for x1, y1, x2, y2, conf in self._boxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 210), 2)
            cv2.putText(
                frame, f"PHONE {conf:.0%}",
                (x1, y1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 210), 2,
            )
