"""
detectors/calibrator.py
=======================
Driver Baseline Auto-Calibration.
Measures driver's natural open-eye EAR and resting head posture during the
first 5 seconds of driving (or upon pressing 'C').
Dynamically adapts EAR thresholds and resting head-pose offsets to eliminate
false positives caused by different facial morphologies.
"""

import time
import logging
import numpy as np

logger = logging.getLogger(__name__)


class DriverCalibrator:
    """
    Calibrates personalized driver thresholds:
      - Baseline open-eye EAR (filters out blinks)
      - Personalized drowsy threshold = baseline_ear * 0.75 (clamped to [0.18, 0.32])
      - Resting head pose offsets (pitch, yaw, roll)
    """

    def __init__(self, duration_sec: float = 5.0, min_samples: int = 40):
        self.duration_sec = duration_sec
        self.min_samples = min_samples

        self.is_calibrating: bool = True
        self.is_calibrated: bool = False
        self.start_time: float = time.time()

        self._ear_samples = []
        self._pitch_samples = []
        self._yaw_samples = []
        self._roll_samples = []

        # Results
        self.baseline_ear: float = 0.30
        self.calibrated_ear_threshold: float = 0.25
        self.resting_pitch: float = 0.0
        self.resting_yaw: float = 0.0
        self.resting_roll: float = 0.0
        self.summary_text: str = ""

    def start_calibration(self):
        """Trigger or reset calibration."""
        self.is_calibrating = True
        self.is_calibrated = False
        self.start_time = time.time()
        self._ear_samples.clear()
        self._pitch_samples.clear()
        self._yaw_samples.clear()
        self._roll_samples.clear()
        self.summary_text = "Calibrating... Look forward naturally"
        logger.info("[Calibrator] Started baseline calibration...")

    def update(self, ear: float, pitch: float, yaw: float, roll: float, face_detected: bool) -> bool:
        """
        Feed current frame metrics.
        Returns True when calibration just finished on this frame.
        """
        if not self.is_calibrating:
            return False

        if not face_detected:
            return False

        # Only collect open eye samples (ignore blinks > 0.16)
        if ear > 0.16:
            self._ear_samples.append(ear)
            self._pitch_samples.append(pitch)
            self._yaw_samples.append(yaw)
            self._roll_samples.append(roll)

        elapsed = time.time() - self.start_time
        if elapsed >= self.duration_sec and len(self._ear_samples) >= self.min_samples:
            self._finalize()
            return True

        return False

    def get_progress(self) -> float:
        """Returns calibration progress percentage (0 to 100)."""
        if not self.is_calibrating:
            return 100.0 if self.is_calibrated else 0.0
        elapsed = time.time() - self.start_time
        return min(100.0, max(0.0, (elapsed / max(0.1, self.duration_sec)) * 100.0))

    def get_time_left(self) -> float:
        """Remaining calibration seconds."""
        if not self.is_calibrating:
            return 0.0
        elapsed = time.time() - self.start_time
        return max(0.0, self.duration_sec - elapsed)

    def _finalize(self):
        self.is_calibrating = False
        self.is_calibrated = True

        if self._ear_samples:
            # 65th percentile represents natural open-eye state without micro-closures
            self.baseline_ear = float(np.percentile(self._ear_samples, 65))
            # 75% of baseline is standard ophthalmological drowsy ratio
            raw_threshold = self.baseline_ear * 0.75
            # Clamp between 0.18 and 0.32 to ensure safety limits
            self.calibrated_ear_threshold = round(float(np.clip(raw_threshold, 0.18, 0.32)), 3)

        if self._pitch_samples:
            self.resting_pitch = round(float(np.median(self._pitch_samples)), 1)
            self.resting_yaw = round(float(np.median(self._yaw_samples)), 1)
            self.resting_roll = round(float(np.median(self._roll_samples)), 1)

        self.summary_text = (
            f"CALIBRATED: EAR {self.calibrated_ear_threshold:.3f} "
            f"(Base: {self.baseline_ear:.3f})"
        )
        logger.info(
            f"[Calibrator] Done! Baseline EAR={self.baseline_ear:.3f}, "
            f"Threshold={self.calibrated_ear_threshold:.3f}, "
            f"Resting Head=(P:{self.resting_pitch}, Y:{self.resting_yaw}, R:{self.resting_roll})"
        )
