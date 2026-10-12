"""
detectors/gaze_detector.py
===========================
Pupil / Iris Gaze Tracking using MediaPipe refined landmarks (468-477).
Detects whether the driver is looking forward (CENTER), looking down (at a phone/lap),
or looking left/right (mirrors/windows).
"""

import logging
from dataclasses import dataclass, field
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# Iris & eye corner indices (MediaPipe refine_landmarks=True)
LEFT_IRIS_CENTER = 468
LEFT_EYE_INNER = 133
LEFT_EYE_OUTER = 33
LEFT_EYE_TOP = 159
LEFT_EYE_BOTTOM = 145

RIGHT_IRIS_CENTER = 473
RIGHT_EYE_INNER = 362
RIGHT_EYE_OUTER = 263
RIGHT_EYE_TOP = 386
RIGHT_EYE_BOTTOM = 374


@dataclass
class GazeDetector:
    """Estimates driver gaze direction from iris position within eye boundaries."""
    gaze_direction: str = field(default="CENTER", init=False)
    horizontal_ratio: float = field(default=0.5, init=False)
    vertical_ratio: float = field(default=0.5, init=False)
    looking_down_frames: int = field(default=0, init=False)
    is_looking_down: bool = field(default=False, init=False)
    down_threshold_frames: int = 25  # ~0.8s @ 30fps

    def update(self, lm, w: int, h: int) -> Tuple[str, float, float]:
        """
        Calculates gaze direction from face mesh landmarks.
        Returns: (gaze_direction, horizontal_ratio, vertical_ratio)
        """
        if len(lm) < 478:
            self.gaze_direction = "UNKNOWN"
            return self.gaze_direction, 0.5, 0.5

        try:
            # Left Eye
            l_iris_x = lm[LEFT_IRIS_CENTER].x * w
            l_iris_y = lm[LEFT_IRIS_CENTER].y * h
            l_inner_x = lm[LEFT_EYE_INNER].x * w
            l_outer_x = lm[LEFT_EYE_OUTER].x * w
            l_top_y = lm[LEFT_EYE_TOP].y * h
            l_bot_y = lm[LEFT_EYE_BOTTOM].y * h

            # Right Eye
            r_iris_x = lm[RIGHT_IRIS_CENTER].x * w
            r_iris_y = lm[RIGHT_IRIS_CENTER].y * h
            r_inner_x = lm[RIGHT_EYE_INNER].x * w
            r_outer_x = lm[RIGHT_EYE_OUTER].x * w
            r_top_y = lm[RIGHT_EYE_TOP].y * h
            r_bot_y = lm[RIGHT_EYE_BOTTOM].y * h

            # Horizontal ratios (0.0 to 1.0)
            l_width = abs(l_outer_x - l_inner_x)
            r_width = abs(r_outer_x - r_inner_x)

            l_h_ratio = (l_iris_x - min(l_inner_x, l_outer_x)) / l_width if l_width > 0 else 0.5
            r_h_ratio = (r_iris_x - min(r_inner_x, r_outer_x)) / r_width if r_width > 0 else 0.5
            h_ratio = (l_h_ratio + r_h_ratio) / 2.0

            # Vertical ratios (0.0 to 1.0)
            l_height = abs(l_bot_y - l_top_y)
            r_height = abs(r_bot_y - r_top_y)

            l_v_ratio = (l_iris_y - l_top_y) / l_height if l_height > 0 else 0.5
            r_v_ratio = (r_iris_y - r_top_y) / r_height if r_height > 0 else 0.5
            v_ratio = (l_v_ratio + r_v_ratio) / 2.0

            self.horizontal_ratio = round(float(h_ratio), 2)
            self.vertical_ratio = round(float(v_ratio), 2)

            # Classify direction
            if v_ratio > 0.68:
                direction = "LOOKING DOWN"
                self.looking_down_frames += 1
            else:
                self.looking_down_frames = max(0, self.looking_down_frames - 1)
                if h_ratio < 0.38:
                    direction = "LOOKING RIGHT"
                elif h_ratio > 0.62:
                    direction = "LOOKING LEFT"
                else:
                    direction = "CENTER"

            self.is_looking_down = self.looking_down_frames >= self.down_threshold_frames
            self.gaze_direction = direction
            return self.gaze_direction, self.horizontal_ratio, self.vertical_ratio

        except Exception as e:
            logger.debug(f"[GazeDetector] Calculation error: {e}")
            self.gaze_direction = "CENTER"
            return "CENTER", 0.5, 0.5

    def reset(self):
        self.gaze_direction = "CENTER"
        self.looking_down_frames = 0
        self.is_looking_down = False
