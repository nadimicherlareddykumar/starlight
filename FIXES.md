# DriveGuard Fix Log

## Calibration and live-feed UI

- Fixed calibration so it receives the measured EAR and raw head-pose values **after** face landmarks are processed. Previously it received zeros, collected no valid samples, and could remain in calibration indefinitely.
- Applied the calibrated neutral head pose to head-distraction detection. A driver who is looking forward with a camera mounted slightly off-centre is now measured relative to their own baseline.
- Reset transient detector counters after calibration, preventing an alert accumulated during setup from firing immediately afterwards.
- Hid gaze, yawn, drowsiness, and head-distraction alerts while calibration is running.
- Fixed the dashboard calibration banner's conflicting inline `display` styles. It is hidden by default, appears only while calibration is active, and is explicitly cleared when a session ends.
- Exported the dashboard session-ending function so the main application can correctly change the web UI from live to ended.

## Detection reliability

- Preserved sub-pixel eye landmarks for steadier EAR values and made the drowsiness frame threshold inclusive.
- Corrected the default MAR/yawn threshold from `0.65` to `0.35`, including the fatigue-score calculation, matching the MAR values produced by the current MediaPipe landmark formula.
- Kept configured thresholds intact and only applies small, explicit low-light adjustments instead of overwriting them with unrelated hard-coded defaults.
- Added two-pass confirmation for YOLO phone detections to reduce single-frame false positives.
- Draws confirmed phone boxes on the visible camera feed rather than only on the hidden enhanced inference frame.
- Makes phone detection fail safely when the YOLO model cannot load, allowing all face-safety features to continue.
- Added a sustained driver-presence alarm: if no face is visible for two seconds, the video HUD, dashboard, beep, and voice alert report that the driver is not detected. If the camera stops producing frames for two seconds, a separate camera-feed-lost alert is sent.

## Data and dashboard improvements

- Fixed CSV frame flushing so the final report no longer duplicates the last 30 frame rows.
- Made dashboard EAR/MAR threshold labels and warning colors follow the live configured thresholds.
- Added the phone-confirmation setting to `config.yaml` and updated the primary README configuration example for the corrected MAR threshold.

## Verification completed

- Ran `python -m compileall -q DriveGuard-Driver-Safety-System` successfully after the changes.
- Ran a focused session-report test; it verified that 31 logged frames produce exactly 31 CSV rows.
- The current workspace Python environment does not have the project's NumPy, OpenCV, or Flask dependencies installed, so camera and dashboard runtime checks require installing the project dependencies first:

  ```powershell
  cd DriveGuard-Driver-Safety-System
  python -m pip install -r requirements.txt
  python main.py
  ```
