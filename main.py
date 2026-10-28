"""
main.py — Driver Safety System v5 Final
=========================================
Run: python main.py
     python main.py --no-web
     python main.py --no-tts
     python main.py --camera 1
     python main.py --evaluate video.mp4
"""

import argparse
import logging
import sys
import time
import webbrowser
from datetime import timedelta

import cv2
import mediapipe as mp
import yaml

from alerts    import AlertManager
from detectors import (EARDetector, MARDetector, HeadPoseDetector,
                       PhoneDetector, FatigueScoreCalculator, NightEnhancer,
                       GazeDetector, DriverCalibrator)
from ui        import HUDRenderer
from reports   import SessionReporter
from emergency import EmergencyAlertSystem
from safety    import CriticalStateMonitor, BreakAdvisor
from detectors.face_detectors import LEFT_EYE, RIGHT_EYE

import io
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(
            io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        ),
        logging.FileHandler("driver_safety.log", encoding='utf-8'),
    ],
)
logger = logging.getLogger("main")
MOUTH_LANDMARKS = [78, 308, 13, 14, 82, 87, 312, 317]


def load_config(path: str) -> dict:
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
        logger.info(f"Config loaded: {path}")
        return cfg
    except FileNotFoundError:
        logger.warning(f"Config not found: {path}. Defaults used.")
        return {}


class FPSCounter:
    def __init__(self, window=30):
        self._times = []
        self._window = window

    def tick(self) -> float:
        now = time.time()
        self._times.append(now)
        if len(self._times) > self._window:
            self._times.pop(0)
        if len(self._times) < 2:
            return 0.0
        return (len(self._times)-1) / (self._times[-1]-self._times[0])


def draw_fatigue_score(frame, score: float):
    from detectors.fatigue_score import FatigueScoreCalculator as FSC
    color = FSC.color(score)
    level = FSC.level(score)
    H, W  = frame.shape[:2]
    cv2.rectangle(frame, (W-210, 0), (W, 85), (15,15,15), -1)
    cv2.putText(frame, "FATIGUE SCORE",   (W-205, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100,100,100), 1)
    cv2.putText(frame, f"{score:.0f}/100",(W-205, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9,  color, 2)
    cv2.putText(frame, level,             (W-205, 68),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
    bar_w = int(190 * score / 100)
    cv2.rectangle(frame, (W-205, 72), (W-15,      80), (45,45,45), -1)
    cv2.rectangle(frame, (W-205, 72), (W-205+bar_w,80), color,    -1)


def draw_critical_timer(frame, elapsed: float, condition: str):
    """Flashing red banner at bottom with timer + condition."""
    if elapsed <= 0:
        return
    H, W = frame.shape[:2]
    alpha = 0.25 + 0.2 * abs((elapsed % 1.0) - 0.5)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, H-95), (W, H), (0, 0, 180), -1)
    cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame)

    cv2.putText(frame, f"CRITICAL {elapsed:.0f}s — {condition}",
                (10, H-62),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 80), 2)
    cv2.putText(frame, "PLEASE STOP THE VEHICLE SAFELY NOW",
                (10, H-30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)


def format_time(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    m, s = divmod(td.seconds, 60)
    return f"{m:02d}:{s:02d}"


def run(cfg, camera_index=0, no_tts=False, no_web=False, evaluate_path=None):

    c_ear  = cfg.get("ear",  {})
    c_mar  = cfg.get("mar",  {})
    c_hp   = cfg.get("head_pose", {})
    c_yolo = cfg.get("yolo", {})
    c_al   = cfg.get("alerts", {})
    c_em   = cfg.get("emergency", {})
    c_presence = cfg.get("driver_presence", {})
    missing_face_seconds = float(c_presence.get("missing_face_seconds", 2.0))
    camera_loss_seconds = float(c_presence.get("camera_loss_seconds", 2.0))

    ear_d = EARDetector(
        threshold=c_ear.get("threshold", 0.25),
        blink_min_frames=c_ear.get("blink_min_frames", 3),
        drowsy_frames=c_ear.get("drowsy_frames", 20),
    )
    mar_d = MARDetector(
        threshold=c_mar.get("threshold", 0.65),
        yawn_min_frames=c_mar.get("yawn_min_frames", 18),
    )
    hp_d  = HeadPoseDetector(
        pitch_low=c_hp.get("pitch_low", -20),
        pitch_high=c_hp.get("pitch_high", 20),
        yaw_limit=c_hp.get("yaw_limit", 30),
        roll_limit=c_hp.get("roll_limit", 20),
        alert_frames=c_hp.get("alert_frames", 35),
    )
    phone_d = PhoneDetector(
        model_path=c_yolo.get("model", "yolov8n.pt"),
        skip_frames=c_yolo.get("skip_frames", 4),
        phone_conf=c_yolo.get("phone_conf", 0.70),
        phone_max_area=c_yolo.get("phone_max_area", 0.25),
        phone_min_aspect=c_yolo.get("phone_min_aspect", 0.4),
        confirmation_frames=c_yolo.get("confirmation_frames", 2),
    )
    base_phone_conf = phone_d.phone_conf
    base_mar_threshold = mar_d.threshold
    fatigue_calc = FatigueScoreCalculator(
        ear_threshold=c_ear.get("threshold", 0.25),
        mar_threshold=c_mar.get("threshold", 0.35),
    )
    alert_m   = AlertManager(
        cooldown=c_al.get("cooldown_seconds", 6.0),
        tts_rate=0 if no_tts else c_al.get("tts_rate", 150),
    )
    hud       = HUDRenderer()
    reporter  = SessionReporter()
    emergency = EmergencyAlertSystem(c_em)

    critical_monitor = CriticalStateMonitor(
        alert_manager=alert_m,
        emergency_system=emergency,
        rest_cooldown=120.0,
    )

    fps_ctr      = FPSCounter()
    night_enh    = NightEnhancer()
    gaze_d       = GazeDetector()
    start_time   = time.time()

    # Driver Calibration & Continuous Break Advisor
    c_cal        = cfg.get("calibration", {})
    calibrator   = DriverCalibrator(duration_sec=c_cal.get("duration_seconds", 5.0))
    calibrator.calibrated_ear_threshold = ear_d.threshold
    if not c_cal.get("auto_start", True):
        calibrator.is_calibrating = False

    c_brk        = cfg.get("break_advisor", {})
    break_adv    = BreakAdvisor(
        max_drive_minutes=c_brk.get("max_drive_minutes", 120.0),
        warning_minutes=c_brk.get("warning_minutes", 90.0),
    )

    # ── Web dashboard ─────────────────────────────────────────────────
    update_state = None
    update_frame = None
    if not no_web:
        try:
            from web import (start_server, update_state as _upd,
                             update_frame as _upd_frame, end_session as _end_session)
            update_state = _upd
            update_frame = _upd_frame
            end_web_session = _end_session
            start_server(keep_alive=False)
            import threading as _th
            def _open():
                time.sleep(1.5)
                webbrowser.open("http://127.0.0.1:5000")
            _th.Thread(target=_open, daemon=True).start()
        except ImportError as e:
            logger.warning(f"[Web] Flask missing: {e}")
    end_web_session = locals().get("end_web_session")

    # ── MediaPipe ─────────────────────────────────────────────────────
    c_mp = cfg.get("mediapipe", {})
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=c_mp.get("max_num_faces", 1),
        refine_landmarks=True,
        min_detection_confidence=c_mp.get("min_detection_confidence", 0.5),
        min_tracking_confidence=c_mp.get("min_tracking_confidence", 0.5),
    )

    src = evaluate_path if evaluate_path else camera_index
    if isinstance(src, int) and sys.platform.startswith("win"):
        cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(src)
    else:
        cap = cv2.VideoCapture(src)

    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        logger.error(f"Camera {src} could not be opened. Check device index or permissions.")
        sys.exit(1)

    print(f"\n[INFO] Driver Safety System v5 running on camera {src}...")
    print("[INFO] ESC = quit | R = reset | C = calibrate\n")

    window_name = "Driver Safety System v5"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)
    try:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass

    fatigue_score = 0.0
    retry_count = 0
    frame_count = 0
    gaze_dir = "CENTER"
    face_missing_since = None
    camera_lost_since = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if evaluate_path:
                    break
                retry_count += 1
                if camera_lost_since is None:
                    camera_lost_since = time.time()
                lost_for = time.time() - camera_lost_since
                if lost_for >= camera_loss_seconds:
                    message = "CAMERA FEED LOST - CHECK CAMERA"
                    if alert_m.trigger(
                        "camera_lost", 1500, 900,
                        "Camera feed lost. Please check the driver camera."
                    ):
                        logger.error(message)
                    if update_state:
                        update_state(
                            face_detected=False,
                            camera_connected=False,
                            alert_msg=message,
                        )
                if (cv2.waitKey(1) & 0xFF) == 27:
                    break
                time.sleep(0.10)
                continue
            retry_count = 0
            camera_lost_since = None
            frame_count += 1

            H, W         = frame.shape[:2]
            fps          = fps_ctr.tick()
            session_time = format_time(time.time() - start_time)
            alert_msg    = ""

            # ── Break Advisor ─────────────────────────────────────────
            break_msg = break_adv.update()
            if break_msg:
                alert_m.play_pattern("break")
                if alert_m.trigger("break_rest", speech=break_msg, force=True):
                    alert_msg = break_msg
            break_status = break_adv.get_status()

            # ── Night enhancement ────────────────────────────────────────
            enhanced_frame, light_mode, brightness = night_enh.enhance(frame)
            # YOLO ke liye alag aggressive enhancement
            yolo_frame = night_enh.enhance_for_yolo(frame)

            # ── Phone ─────────────────────────────────────────────────
            # Lower the confidence only before inference and preserve the
            # configured daytime threshold.
            if light_mode == "NIGHT":
                phone_d.phone_conf = max(0.55, base_phone_conf - 0.10)
            elif light_mode == "DUSK":
                phone_d.phone_conf = max(0.60, base_phone_conf - 0.05)
            else:
                phone_d.phone_conf = base_phone_conf
            phone_detected = phone_d.update(frame, inference_frame=yolo_frame)

            if phone_detected:
                hud.draw_alert(frame, "PHONE DETECTED!", H-130, (0,0,210))
                if alert_m.trigger("phone", 1300, 600,
                                   "Do not use phone while driving"):
                    alert_msg = "PHONE DETECTED"
            else:
                alert_m.reset_cooldown("phone")

            # ── Face Mesh ─────────────────────────────────────────────
            mp_frame = night_enh.enhance_for_mediapipe(frame)
            rgb = cv2.cvtColor(mp_frame, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)
            ear_val = mar_val = pitch = yaw = roll = 0.0

            if res.multi_face_landmarks:
                face_missing_since = None
                alert_m.reset_cooldown("driver_missing")
                lm = res.multi_face_landmarks[0].landmark

                hud.draw_eye_landmarks(frame, lm, W, H, LEFT_EYE, RIGHT_EYE)

                # Gaze / Iris Tracking
                gaze_dir, h_ratio, v_ratio = gaze_d.update(lm, W, H)
                if gaze_d.is_looking_down and not calibrator.is_calibrating:
                    hud.draw_alert(frame, "EYES OFF ROAD (LOOKING DOWN)!", 240, (0, 140, 255))
                    if alert_m.trigger("gaze", 1000, 600,
                                       "Please keep your eyes on the road."):
                        alert_msg = "GAZE DOWN ALERT"
                else:
                    alert_m.reset_cooldown("gaze")

                cv2.putText(frame, f"GAZE: {gaze_dir}", (W - 205, 105),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (0, 0, 255) if gaze_d.is_looking_down else (0, 255, 255), 1)

                # Calculate metrics before calibration.  Previously the
                # calibrator was given zero EAR/head values, so it never
                # gathered samples and its banner remained on screen.
                if light_mode == "NIGHT":
                    ear_d.threshold = max(0.18, calibrator.calibrated_ear_threshold - 0.02)
                    mar_d.threshold = max(0.20, base_mar_threshold - 0.05)
                elif light_mode == "DUSK":
                    ear_d.threshold = max(0.18, calibrator.calibrated_ear_threshold - 0.01)
                    mar_d.threshold = max(0.20, base_mar_threshold - 0.03)
                else:
                    ear_d.threshold = calibrator.calibrated_ear_threshold
                    mar_d.threshold = base_mar_threshold
                ear_val = ear_d.update(lm, W, H)
                mar_val = mar_d.update(lm, W, H)
                pitch, yaw, roll = hp_d.update(lm, W, H)

                if calibrator.is_calibrating:
                    done_cal = calibrator.update(
                        ear_val, hp_d.raw_pitch, hp_d.raw_yaw, hp_d.raw_roll, True
                    )
                    if done_cal:
                        ear_d.threshold = calibrator.calibrated_ear_threshold
                        hp_d.set_baseline(
                            calibrator.resting_pitch, calibrator.resting_yaw,
                            calibrator.resting_roll,
                        )
                        ear_d.reset(); mar_d.reset()
                        alert_m.play_pattern("calibrated")
                        alert_m.trigger("calibrated", speech="Driver baseline calibrated. Have a safe drive!", force=True)

                if ear_d.is_drowsy and not calibrator.is_calibrating:
                    hud.draw_alert(frame, "DROWSY ALERT!", 90)
                    if alert_m.trigger("drowsy", 1000, 900,
                                       "Warning! You are drowsy. Please take a break."):
                        alert_msg = "DROWSY ALERT"
                else:
                    alert_m.reset_cooldown("drowsy")
                hud.draw_bar(frame, ear_d.eye_frames,
                             ear_d.drowsy_frames, 270, 22, 170, 14, "Drowsy")

                # MAR / Yawn
                hud.draw_mouth_landmarks(frame, lm, W, H, MOUTH_LANDMARKS)
                if mar_d.is_yawning and not calibrator.is_calibrating:
                    hud.draw_alert(frame, "YAWNING DETECTED!", 140, (0,140,255))
                    if alert_m.trigger("yawn", 900, 600,
                                       "You are yawning. Stay alert."):
                        alert_msg = "YAWNING DETECTED"
                else:
                    alert_m.reset_cooldown("yawn")
                hud.draw_bar(frame, mar_d.yawn_frames,
                             mar_d.yawn_min_frames, 270, 50, 170, 14, "Yawn")

                # Head pose
                if hp_d.is_distracted and not calibrator.is_calibrating:
                    hud.draw_alert(frame, "HEAD DISTRACTED!", 190, (0,100,255))
                    if alert_m.trigger("head", 1100, 700,
                                       "Please keep your eyes on the road."):
                        alert_msg = "HEAD DISTRACTED"
                else:
                    alert_m.reset_cooldown("head")
                hud.draw_bar(frame, hp_d.head_frames,
                             hp_d.alert_frames, 270, 78, 170, 14, "Head")
                hud.draw_nose_arrow(frame, lm, W, H, yaw, pitch)

            else:
                if face_missing_since is None:
                    face_missing_since = time.time()
                missing_for = time.time() - face_missing_since
                if missing_for >= missing_face_seconds:
                    message = "DRIVER NOT DETECTED - CHECK CAMERA"
                    hud.draw_alert(frame, message, 115, (0, 0, 255))
                    if alert_m.trigger(
                        "driver_missing", 1500, 900,
                        "Driver not detected. Please check the camera."
                    ):
                        logger.warning(message)
                    alert_msg = message
                else:
                    seconds_left = max(0.0, missing_face_seconds - missing_for)
                    cv2.putText(frame, f"Face not detected ({seconds_left:.1f}s)", (270, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2)
                ear_d.eye_frames = mar_d.yawn_frames = hp_d.head_frames = 0
                ear_d.is_drowsy = mar_d.is_yawning = hp_d.is_distracted = False
                gaze_d.reset()
                gaze_dir = "NO FACE"

            # ── Fatigue score ─────────────────────────────────────────
            fatigue_score = fatigue_calc.calculate(
                ear=ear_val, mar=mar_val,
                is_drowsy=ear_d.is_drowsy,
                is_yawning=mar_d.is_yawning,
                is_distracted=hp_d.is_distracted,
                phone_detected=phone_detected,
                eye_frames=ear_d.eye_frames,
                drowsy_frames=ear_d.drowsy_frames,
            )
            draw_fatigue_score(frame, fatigue_score)

            # ── Critical monitor — BOTH conditions ────────────────────
            loc = critical_monitor._location   # prefetched location
            stop_alert = critical_monitor.update(
                fatigue_score=fatigue_score,
                is_drowsy=ear_d.is_drowsy,
                is_distracted=hp_d.is_distracted,
                location=loc,
            )
            if stop_alert:
                alert_msg = stop_alert

            # Draw critical timer + rest places on frame
            critical_monitor.draw_on_frame(frame)
            elapsed = critical_monitor.get_elapsed()   # for web state

            # Also fire basic emergency alert independently
            if fatigue_score >= 75:
                emergency.check_and_alert(fatigue_score)

            # ── Log frame ─────────────────────────────────────────────
            reporter.log_frame(
                ear=ear_val, mar=mar_val,
                pitch=pitch, yaw=yaw, roll=roll,
                is_drowsy=ear_d.is_drowsy,
                is_yawning=mar_d.is_yawning,
                is_distracted=hp_d.is_distracted,
                phone_detected=phone_detected,
                fatigue_score=fatigue_score,
            )

            # ── Web state & live video stream ─────────────────────────
            if update_state:
                from detectors.fatigue_score import FatigueScoreCalculator as FSC
                update_state(
                    ear=round(ear_val,3), mar=round(mar_val,3),
                    pitch=round(pitch,2), yaw=round(yaw,2), roll=round(roll,2),
                    fatigue_score=round(fatigue_score,1),
                    fatigue_level=FSC.level(fatigue_score),
                    is_drowsy=ear_d.is_drowsy,
                    is_yawning=mar_d.is_yawning,
                    is_distracted=hp_d.is_distracted,
                    phone_detected=phone_detected,
                    blink_count=ear_d.blink_count,
                    yawn_count=mar_d.yawn_count,
                    head_alerts=hp_d.alert_count,
                    fps=round(fps,1),
                    face_detected=res.multi_face_landmarks is not None,
                    camera_connected=True,
                    alert_msg=alert_msg,
                    session_time=session_time,
                    critical_elapsed=round(elapsed,1),
                    light_mode=light_mode,
                    brightness=round(brightness,1),
                    gaze_direction=gaze_dir,
                    is_looking_down=gaze_d.is_looking_down,
                    is_calibrating=calibrator.is_calibrating,
                    calibrated=calibrator.is_calibrated,
                    calibration_pct=round(calibrator.get_progress(), 1),
                    calibrated_threshold=calibrator.calibrated_ear_threshold,
                    ear_threshold=round(ear_d.threshold, 3),
                    mar_threshold=round(mar_d.threshold, 3),
                    driving_time=break_status["elapsed_str"],
                    break_due=break_status["break_due"],
                    break_remaining=break_status["remaining_str"],
                )

            # ── HUD ───────────────────────────────────────────────────
            hud.draw_panel(
                frame, ear_val, mar_val, pitch, yaw, roll,
                ear_d.blink_count, mar_d.yawn_count, hp_d.alert_count,
                ear_d.threshold, mar_d.threshold,
                hp_d.pitch_low, hp_d.pitch_high,
                hp_d.yaw_limit, hp_d.roll_limit,
            )
            hud.draw_fps(frame, fps)
            night_enh.draw_mode_indicator(frame)

            if calibrator.is_calibrating:
                hud.draw_calibration(frame, calibrator.get_progress(), calibrator.get_time_left(), calibrator.summary_text)
            hud.draw_break_status(frame, break_status["elapsed_str"], break_status["break_due"])

            if update_frame:
                update_frame(frame)

            cv2.imshow(window_name, frame)
            # Check if user closed the window with 'X' button
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            elif key == ord("c") or key == ord("C"):
                calibrator.start_calibration()
                alert_m.play_pattern("warning")
                print("[INFO] Re-starting driver calibration...")
            elif key == ord("r") or key == ord("R"):
                ear_d.reset(); mar_d.reset(); hp_d.reset(); gaze_d.reset(); break_adv.reset()
                print("[INFO] Reset done.")

    finally:
        if end_web_session:
            end_web_session()
        cap.release()
        cv2.destroyAllWindows()
        summary = reporter.finalize(
            blink_count=ear_d.blink_count,
            yawn_count=mar_d.yawn_count,
            head_alert_count=hp_d.alert_count,
        )
        print(f"\n[REPORT] Summary: {summary}")
        print(f"[REPORT] Frames:  {reporter.frames_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",   default="config.yaml")
    parser.add_argument("--camera",   type=int, default=0)
    parser.add_argument("--no-tts",   action="store_true")
    parser.add_argument("--no-web",   action="store_true")
    parser.add_argument("--evaluate", default=None)
    args = parser.parse_args()
    cfg = load_config(args.config)
    try:
        run(cfg, camera_index=args.camera,
            no_tts=args.no_tts, no_web=args.no_web,
            evaluate_path=args.evaluate)
    except KeyboardInterrupt:
        print("\n[INFO] Session ended by user.")















# # working
# """
# main.py — Driver Safety System v5 Final
# =========================================
# Run: python main.py
#      python main.py --no-web
#      python main.py --no-tts
#      python main.py --camera 1
#      python main.py --evaluate video.mp4
# """

# import argparse
# import logging
# import sys
# import time
# import webbrowser
# from datetime import timedelta

# import cv2
# import mediapipe as mp
# import yaml

# from alerts    import AlertManager
# from detectors import (EARDetector, MARDetector, HeadPoseDetector,
#                        PhoneDetector, FatigueScoreCalculator, NightEnhancer)
# from ui        import HUDRenderer
# from reports   import SessionReporter
# from emergency import EmergencyAlertSystem
# from safety    import CriticalStateMonitor
# from detectors.face_detectors import LEFT_EYE, RIGHT_EYE

# import io
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
#     handlers=[
#         logging.StreamHandler(
#             io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
#         ),
#         logging.FileHandler("driver_safety.log", encoding='utf-8'),
#     ],
# )
# logger = logging.getLogger("main")
# MOUTH_LANDMARKS = [78, 308, 13, 14, 82, 87, 312, 317]


# def load_config(path: str) -> dict:
#     try:
#         with open(path) as f:
#             cfg = yaml.safe_load(f)
#         logger.info(f"Config loaded: {path}")
#         return cfg
#     except FileNotFoundError:
#         logger.warning(f"Config not found: {path}. Defaults used.")
#         return {}


# class FPSCounter:
#     def __init__(self, window=30):
#         self._times = []
#         self._window = window

#     def tick(self) -> float:
#         now = time.time()
#         self._times.append(now)
#         if len(self._times) > self._window:
#             self._times.pop(0)
#         if len(self._times) < 2:
#             return 0.0
#         return (len(self._times)-1) / (self._times[-1]-self._times[0])


# def draw_fatigue_score(frame, score: float):
#     from detectors.fatigue_score import FatigueScoreCalculator as FSC
#     color = FSC.color(score)
#     level = FSC.level(score)
#     H, W  = frame.shape[:2]
#     cv2.rectangle(frame, (W-210, 0), (W, 85), (15,15,15), -1)
#     cv2.putText(frame, "FATIGUE SCORE",   (W-205, 18),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100,100,100), 1)
#     cv2.putText(frame, f"{score:.0f}/100",(W-205, 50),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.9,  color, 2)
#     cv2.putText(frame, level,             (W-205, 68),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
#     bar_w = int(190 * score / 100)
#     cv2.rectangle(frame, (W-205, 72), (W-15,      80), (45,45,45), -1)
#     cv2.rectangle(frame, (W-205, 72), (W-205+bar_w,80), color,    -1)


# def draw_critical_timer(frame, elapsed: float, condition: str):
#     """Flashing red banner at bottom with timer + condition."""
#     if elapsed <= 0:
#         return
#     H, W = frame.shape[:2]
#     alpha = 0.25 + 0.2 * abs((elapsed % 1.0) - 0.5)
#     overlay = frame.copy()
#     cv2.rectangle(overlay, (0, H-95), (W, H), (0, 0, 180), -1)
#     cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame)

#     cv2.putText(frame, f"CRITICAL {elapsed:.0f}s — {condition}",
#                 (10, H-62),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 80), 2)
#     cv2.putText(frame, "PLEASE STOP THE VEHICLE SAFELY NOW",
#                 (10, H-30),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)


# def format_time(seconds: float) -> str:
#     td = timedelta(seconds=int(seconds))
#     m, s = divmod(td.seconds, 60)
#     return f"{m:02d}:{s:02d}"


# def run(cfg, camera_index=0, no_tts=False, no_web=False, evaluate_path=None):

#     c_ear  = cfg.get("ear",  {})
#     c_mar  = cfg.get("mar",  {})
#     c_hp   = cfg.get("head_pose", {})
#     c_yolo = cfg.get("yolo", {})
#     c_al   = cfg.get("alerts", {})
#     c_em   = cfg.get("emergency", {})

#     ear_d = EARDetector(
#         threshold=c_ear.get("threshold", 0.25),
#         blink_min_frames=c_ear.get("blink_min_frames", 3),
#         drowsy_frames=c_ear.get("drowsy_frames", 20),
#     )
#     mar_d = MARDetector(
#         threshold=c_mar.get("threshold", 0.65),
#         yawn_min_frames=c_mar.get("yawn_min_frames", 18),
#     )
#     hp_d  = HeadPoseDetector(
#         pitch_low=c_hp.get("pitch_low", -20),
#         pitch_high=c_hp.get("pitch_high", 20),
#         yaw_limit=c_hp.get("yaw_limit", 30),
#         roll_limit=c_hp.get("roll_limit", 20),
#         alert_frames=c_hp.get("alert_frames", 35),
#     )
#     phone_d = PhoneDetector(
#         model_path=c_yolo.get("model", "yolov8n.pt"),
#         skip_frames=c_yolo.get("skip_frames", 4),
#         phone_conf=c_yolo.get("phone_conf", 0.70),
#         phone_max_area=c_yolo.get("phone_max_area", 0.25),
#         phone_min_aspect=c_yolo.get("phone_min_aspect", 0.4),
#     )
#     fatigue_calc = FatigueScoreCalculator(
#         ear_threshold=c_ear.get("threshold", 0.25),
#         mar_threshold=c_mar.get("threshold", 0.65),
#     )
#     alert_m   = AlertManager(
#         cooldown=c_al.get("cooldown_seconds", 6.0),
#         tts_rate=0 if no_tts else c_al.get("tts_rate", 150),
#     )
#     hud       = HUDRenderer()
#     reporter  = SessionReporter()
#     emergency = EmergencyAlertSystem(c_em)

#     critical_monitor = CriticalStateMonitor(
#         alert_manager=alert_m,
#         emergency_system=emergency,
#         rest_cooldown=120.0,
#     )

#     fps_ctr    = FPSCounter()
#     night_enh  = NightEnhancer()
#     start_time = time.time()

#     # ── Web dashboard ─────────────────────────────────────────────────
#     update_state = None
#     if not no_web:
#         try:
#             from web import start_server, update_state as _upd
#             update_state = _upd
#             start_server(keep_alive=True)
#             import threading as _th
#             def _open():
#                 time.sleep(1.5)
#                 webbrowser.open("http://127.0.0.1:5000")
#             _th.Thread(target=_open, daemon=True).start()
#         except ImportError as e:
#             logger.warning(f"[Web] Flask missing: {e}")

#     # ── MediaPipe ─────────────────────────────────────────────────────
#     c_mp = cfg.get("mediapipe", {})
#     face_mesh = mp.solutions.face_mesh.FaceMesh(
#         max_num_faces=c_mp.get("max_num_faces", 1),
#         refine_landmarks=True,
#         min_detection_confidence=c_mp.get("min_detection_confidence", 0.5),
#         min_tracking_confidence=c_mp.get("min_tracking_confidence", 0.5),
#     )

#     src = evaluate_path if evaluate_path else camera_index
#     cap = cv2.VideoCapture(src)
#     if not cap.isOpened():
#         logger.error("Camera could not be opened.")
#         sys.exit(1)

#     print("\n[INFO] Driver Safety System v5 running...")
#     print("[INFO] ESC = quit | R = reset\n")

#     fatigue_score = 0.0

#     try:
#         while True:
#             ret, frame = cap.read()
#             if not ret:
#                 break

#             H, W         = frame.shape[:2]
#             fps          = fps_ctr.tick()
#             session_time = format_time(time.time() - start_time)
#             alert_msg    = ""

#             # ── Night enhancement ────────────────────────────────────────
#             enhanced_frame, light_mode, brightness = night_enh.enhance(frame)

#             # ── Phone ─────────────────────────────────────────────────
#             phone_detected = phone_d.update(enhanced_frame)
#             if phone_detected:
#                 hud.draw_alert(frame, "PHONE DETECTED!", H-130, (0,0,210))
#                 if alert_m.trigger("phone", 1300, 600,
#                                    "Do not use phone while driving"):
#                     alert_msg = "PHONE DETECTED"
#             else:
#                 alert_m.reset_cooldown("phone")

#             # ── Face Mesh ─────────────────────────────────────────────
#             rgb = cv2.cvtColor(enhanced_frame, cv2.COLOR_BGR2RGB)
#             res = face_mesh.process(rgb)
#             ear_val = mar_val = pitch = yaw = roll = 0.0

#             if res.multi_face_landmarks:
#                 lm = res.multi_face_landmarks[0].landmark

#                 hud.draw_eye_landmarks(frame, lm, W, H, LEFT_EYE, RIGHT_EYE)

#                 # EAR / Drowsy
#                 # Night mein EAR threshold relax karo
#                 if light_mode == "NIGHT":
#                     ear_d.threshold = 0.22   # thoda lenient
#                 else:
#                     ear_d.threshold = 0.25   # normal
#                 ear_val = ear_d.update(lm, W, H)
#                 if ear_d.is_drowsy:
#                     hud.draw_alert(frame, "DROWSY ALERT!", 90)
#                     if alert_m.trigger("drowsy", 1000, 900,
#                                        "Warning! You are drowsy. Please take a break."):
#                         alert_msg = "DROWSY ALERT"
#                 else:
#                     alert_m.reset_cooldown("drowsy")
#                 hud.draw_bar(frame, ear_d.eye_frames,
#                              ear_d.drowsy_frames, 270, 22, 170, 14, "Drowsy")

#                 # MAR / Yawn
#                 hud.draw_mouth_landmarks(frame, lm, W, H, MOUTH_LANDMARKS)
#                 mar_val = mar_d.update(lm, W, H)
#                 if mar_d.is_yawning:
#                     hud.draw_alert(frame, "YAWNING DETECTED!", 140, (0,140,255))
#                     if alert_m.trigger("yawn", 900, 600,
#                                        "You are yawning. Stay alert."):
#                         alert_msg = "YAWNING DETECTED"
#                 else:
#                     alert_m.reset_cooldown("yawn")
#                 hud.draw_bar(frame, mar_d.yawn_frames,
#                              mar_d.yawn_min_frames, 270, 50, 170, 14, "Yawn")

#                 # Head pose
#                 pitch, yaw, roll = hp_d.update(lm, W, H)
#                 if hp_d.is_distracted:
#                     hud.draw_alert(frame, "HEAD DISTRACTED!", 190, (0,100,255))
#                     if alert_m.trigger("head", 1100, 700,
#                                        "Please keep your eyes on the road."):
#                         alert_msg = "HEAD DISTRACTED"
#                 else:
#                     alert_m.reset_cooldown("head")
#                 hud.draw_bar(frame, hp_d.head_frames,
#                              hp_d.alert_frames, 270, 78, 170, 14, "Head")
#                 hud.draw_nose_arrow(frame, lm, W, H, yaw, pitch)

#             else:
#                 cv2.putText(frame, "No face", (270,40),
#                             cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0,165,255), 2)
#                 ear_d.eye_frames = mar_d.yawn_frames = hp_d.head_frames = 0
#                 ear_d.is_drowsy = mar_d.is_yawning = hp_d.is_distracted = False

#             # ── Fatigue score ─────────────────────────────────────────
#             fatigue_score = fatigue_calc.calculate(
#                 ear=ear_val, mar=mar_val,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_yawning=mar_d.is_yawning,
#                 is_distracted=hp_d.is_distracted,
#                 phone_detected=phone_detected,
#                 eye_frames=ear_d.eye_frames,
#                 drowsy_frames=ear_d.drowsy_frames,
#             )
#             draw_fatigue_score(frame, fatigue_score)

#             # ── Critical monitor — BOTH conditions ────────────────────
#             loc = critical_monitor._location   # prefetched location
#             stop_alert = critical_monitor.update(
#                 fatigue_score=fatigue_score,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_distracted=hp_d.is_distracted,
#                 location=loc,
#             )
#             if stop_alert:
#                 alert_msg = stop_alert

#             # Draw critical timer + rest places on frame
#             critical_monitor.draw_on_frame(frame)
#             elapsed = critical_monitor.get_elapsed()   # for web state

#             # Also fire basic emergency alert independently
#             if fatigue_score >= 75:
#                 emergency.check_and_alert(fatigue_score)

#             # ── Log frame ─────────────────────────────────────────────
#             reporter.log_frame(
#                 ear=ear_val, mar=mar_val,
#                 pitch=pitch, yaw=yaw, roll=roll,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_yawning=mar_d.is_yawning,
#                 is_distracted=hp_d.is_distracted,
#                 phone_detected=phone_detected,
#                 fatigue_score=fatigue_score,
#             )

#             # ── Web state ─────────────────────────────────────────────
#             if update_state:
#                 from detectors.fatigue_score import FatigueScoreCalculator as FSC
#                 update_state(
#                     ear=round(ear_val,3), mar=round(mar_val,3),
#                     pitch=round(pitch,2), yaw=round(yaw,2), roll=round(roll,2),
#                     fatigue_score=round(fatigue_score,1),
#                     fatigue_level=FSC.level(fatigue_score),
#                     is_drowsy=ear_d.is_drowsy,
#                     is_yawning=mar_d.is_yawning,
#                     is_distracted=hp_d.is_distracted,
#                     phone_detected=phone_detected,
#                     blink_count=ear_d.blink_count,
#                     yawn_count=mar_d.yawn_count,
#                     head_alerts=hp_d.alert_count,
#                     fps=round(fps,1),
#                     face_detected=res.multi_face_landmarks is not None,
#                     alert_msg=alert_msg,
#                     session_time=session_time,
#                     critical_elapsed=round(elapsed,1),
#                     light_mode=light_mode,
#                     brightness=round(brightness,1),
#                 )

#             # ── HUD ───────────────────────────────────────────────────
#             hud.draw_panel(
#                 frame, ear_val, mar_val, pitch, yaw, roll,
#                 ear_d.blink_count, mar_d.yawn_count, hp_d.alert_count,
#                 ear_d.threshold, mar_d.threshold,
#                 hp_d.pitch_low, hp_d.pitch_high,
#                 hp_d.yaw_limit, hp_d.roll_limit,
#             )
#             hud.draw_fps(frame, fps)
#             night_enh.draw_mode_indicator(frame)

#             cv2.imshow("Driver Safety System v5", frame)
#             key = cv2.waitKey(1) & 0xFF
#             if key == 27:
#                 break
#             elif key == ord("r"):
#                 ear_d.reset(); mar_d.reset(); hp_d.reset()
#                 print("[INFO] Reset done.")

#     finally:
#         cap.release()
#         cv2.destroyAllWindows()
#         summary = reporter.finalize(
#             blink_count=ear_d.blink_count,
#             yawn_count=mar_d.yawn_count,
#             head_alert_count=hp_d.alert_count,
#         )
#         print(f"\n[REPORT] Summary: {summary}")
#         print(f"[REPORT] Frames:  {reporter.frames_path}")


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--config",   default="config.yaml")
#     parser.add_argument("--camera",   type=int, default=0)
#     parser.add_argument("--no-tts",   action="store_true")
#     parser.add_argument("--no-web",   action="store_true")
#     parser.add_argument("--evaluate", default=None)
#     args = parser.parse_args()
#     cfg = load_config(args.config)
#     run(cfg, camera_index=args.camera,
#         no_tts=args.no_tts, no_web=args.no_web,
#         evaluate_path=args.evaluate)














    



# ye wo code hai night mein detection laagne se pahle work kar rha tha . jo mein mini project mein dikhaya . agar ye chana hai to night_enhancer wali file hata do aur detector wale init file mein se naya code hataker purana code rakh do . phir main file run karo . chal jaega. 


# """
# main.py — Driver Safety System v5 Final
# =========================================
# Run: python main.py
#      python main.py --no-web
#      python main.py --no-tts
#      python main.py --camera 1
#      python main.py --evaluate video.mp4
# """

# import argparse
# import logging
# import sys
# import time
# import webbrowser
# from datetime import timedelta

# import cv2
# import mediapipe as mp
# import yaml

# from alerts    import AlertManager
# from detectors import (EARDetector, MARDetector, HeadPoseDetector,
#                        PhoneDetector, FatigueScoreCalculator)
# from ui        import HUDRenderer
# from reports   import SessionReporter
# from emergency import EmergencyAlertSystem
# from safety    import CriticalStateMonitor
# from detectors.face_detectors import LEFT_EYE, RIGHT_EYE

# import io
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
#     handlers=[
#         logging.StreamHandler(
#             io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
#         ),
#         logging.FileHandler("driver_safety.log", encoding='utf-8'),
#     ],
# )
# logger = logging.getLogger("main")
# MOUTH_LANDMARKS = [78, 308, 13, 14, 82, 87, 312, 317]


# def load_config(path: str) -> dict:
#     try:
#         with open(path) as f:
#             cfg = yaml.safe_load(f)
#         logger.info(f"Config loaded: {path}")
#         return cfg
#     except FileNotFoundError:
#         logger.warning(f"Config not found: {path}. Defaults used.")
#         return {}


# class FPSCounter:
#     def __init__(self, window=30):
#         self._times = []
#         self._window = window

#     def tick(self) -> float:
#         now = time.time()
#         self._times.append(now)
#         if len(self._times) > self._window:
#             self._times.pop(0)
#         if len(self._times) < 2:
#             return 0.0
#         return (len(self._times)-1) / (self._times[-1]-self._times[0])


# def draw_fatigue_score(frame, score: float):
#     from detectors.fatigue_score import FatigueScoreCalculator as FSC
#     color = FSC.color(score)
#     level = FSC.level(score)
#     H, W  = frame.shape[:2]
#     cv2.rectangle(frame, (W-210, 0), (W, 85), (15,15,15), -1)
#     cv2.putText(frame, "FATIGUE SCORE",   (W-205, 18),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100,100,100), 1)
#     cv2.putText(frame, f"{score:.0f}/100",(W-205, 50),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.9,  color, 2)
#     cv2.putText(frame, level,             (W-205, 68),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
#     bar_w = int(190 * score / 100)
#     cv2.rectangle(frame, (W-205, 72), (W-15,      80), (45,45,45), -1)
#     cv2.rectangle(frame, (W-205, 72), (W-205+bar_w,80), color,    -1)


# def draw_critical_timer(frame, elapsed: float, condition: str):
#     """Flashing red banner at bottom with timer + condition."""
#     if elapsed <= 0:
#         return
#     H, W = frame.shape[:2]
#     alpha = 0.25 + 0.2 * abs((elapsed % 1.0) - 0.5)
#     overlay = frame.copy()
#     cv2.rectangle(overlay, (0, H-95), (W, H), (0, 0, 180), -1)
#     cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame)

#     cv2.putText(frame, f"CRITICAL {elapsed:.0f}s — {condition}",
#                 (10, H-62),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 80), 2)
#     cv2.putText(frame, "PLEASE STOP THE VEHICLE SAFELY NOW",
#                 (10, H-30),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)


# def format_time(seconds: float) -> str:
#     td = timedelta(seconds=int(seconds))
#     m, s = divmod(td.seconds, 60)
#     return f"{m:02d}:{s:02d}"


# def run(cfg, camera_index=0, no_tts=False, no_web=False, evaluate_path=None):

#     c_ear  = cfg.get("ear",  {})
#     c_mar  = cfg.get("mar",  {})
#     c_hp   = cfg.get("head_pose", {})
#     c_yolo = cfg.get("yolo", {})
#     c_al   = cfg.get("alerts", {})
#     c_em   = cfg.get("emergency", {})

#     ear_d = EARDetector(
#         threshold=c_ear.get("threshold", 0.25),
#         blink_min_frames=c_ear.get("blink_min_frames", 3),
#         drowsy_frames=c_ear.get("drowsy_frames", 20),
#     )
#     mar_d = MARDetector(
#         threshold=c_mar.get("threshold", 0.65),
#         yawn_min_frames=c_mar.get("yawn_min_frames", 18),
#     )
#     hp_d  = HeadPoseDetector(
#         pitch_low=c_hp.get("pitch_low", -20),
#         pitch_high=c_hp.get("pitch_high", 20),
#         yaw_limit=c_hp.get("yaw_limit", 30),
#         roll_limit=c_hp.get("roll_limit", 20),
#         alert_frames=c_hp.get("alert_frames", 35),
#     )
#     phone_d = PhoneDetector(
#         model_path=c_yolo.get("model", "yolov8n.pt"),
#         skip_frames=c_yolo.get("skip_frames", 4),
#         phone_conf=c_yolo.get("phone_conf", 0.70),
#         phone_max_area=c_yolo.get("phone_max_area", 0.25),
#         phone_min_aspect=c_yolo.get("phone_min_aspect", 0.4),
#     )
#     fatigue_calc = FatigueScoreCalculator(
#         ear_threshold=c_ear.get("threshold", 0.25),
#         mar_threshold=c_mar.get("threshold", 0.65),
#     )
#     alert_m   = AlertManager(
#         cooldown=c_al.get("cooldown_seconds", 6.0),
#         tts_rate=0 if no_tts else c_al.get("tts_rate", 150),
#     )
#     hud       = HUDRenderer()
#     reporter  = SessionReporter()
#     emergency = EmergencyAlertSystem(c_em)

#     critical_monitor = CriticalStateMonitor(
#         alert_manager=alert_m,
#         emergency_system=emergency,
#         rest_cooldown=120.0,
#     )

#     fps_ctr    = FPSCounter()
#     start_time = time.time()

#     # ── Web dashboard ─────────────────────────────────────────────────
#     update_state = None
#     if not no_web:
#         try:
#             from web import start_server, update_state as _upd
#             update_state = _upd
#             start_server(keep_alive=True)
#             import threading as _th
#             def _open():
#                 time.sleep(1.5)
#                 webbrowser.open("http://127.0.0.1:5000")
#             _th.Thread(target=_open, daemon=True).start()
#         except ImportError as e:
#             logger.warning(f"[Web] Flask missing: {e}")

#     # ── MediaPipe ─────────────────────────────────────────────────────
#     c_mp = cfg.get("mediapipe", {})
#     face_mesh = mp.solutions.face_mesh.FaceMesh(
#         max_num_faces=c_mp.get("max_num_faces", 1),
#         refine_landmarks=True,
#         min_detection_confidence=c_mp.get("min_detection_confidence", 0.5),
#         min_tracking_confidence=c_mp.get("min_tracking_confidence", 0.5),
#     )

#     src = evaluate_path if evaluate_path else camera_index
#     cap = cv2.VideoCapture(src)
#     if not cap.isOpened():
#         logger.error("Camera could not be opened.")
#         sys.exit(1)

#     print("\n[INFO] Driver Safety System v5 running...")
#     print("[INFO] ESC = quit | R = reset\n")

#     fatigue_score = 0.0

#     try:
#         while True:
#             ret, frame = cap.read()
#             if not ret:
#                 break

#             H, W         = frame.shape[:2]
#             fps          = fps_ctr.tick()
#             session_time = format_time(time.time() - start_time)
#             alert_msg    = ""

#             # ── Phone ─────────────────────────────────────────────────
#             phone_detected = phone_d.update(frame)
#             if phone_detected:
#                 hud.draw_alert(frame, "PHONE DETECTED!", H-130, (0,0,210))
#                 if alert_m.trigger("phone", 1300, 600,
#                                    "Do not use phone while driving"):
#                     alert_msg = "PHONE DETECTED"
#             else:
#                 alert_m.reset_cooldown("phone")

#             # ── Face Mesh ─────────────────────────────────────────────
#             rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
#             res = face_mesh.process(rgb)
#             ear_val = mar_val = pitch = yaw = roll = 0.0

#             if res.multi_face_landmarks:
#                 lm = res.multi_face_landmarks[0].landmark

#                 hud.draw_eye_landmarks(frame, lm, W, H, LEFT_EYE, RIGHT_EYE)

#                 # EAR / Drowsy
#                 ear_val = ear_d.update(lm, W, H)
#                 if ear_d.is_drowsy:
#                     hud.draw_alert(frame, "DROWSY ALERT!", 90)
#                     if alert_m.trigger("drowsy", 1000, 900,
#                                        "Warning! You are drowsy. Please take a break."):
#                         alert_msg = "DROWSY ALERT"
#                 else:
#                     alert_m.reset_cooldown("drowsy")
#                 hud.draw_bar(frame, ear_d.eye_frames,
#                              ear_d.drowsy_frames, 270, 22, 170, 14, "Drowsy")

#                 # MAR / Yawn
#                 hud.draw_mouth_landmarks(frame, lm, W, H, MOUTH_LANDMARKS)
#                 mar_val = mar_d.update(lm, W, H)
#                 if mar_d.is_yawning:
#                     hud.draw_alert(frame, "YAWNING DETECTED!", 140, (0,140,255))
#                     if alert_m.trigger("yawn", 900, 600,
#                                        "You are yawning. Stay alert."):
#                         alert_msg = "YAWNING DETECTED"
#                 else:
#                     alert_m.reset_cooldown("yawn")
#                 hud.draw_bar(frame, mar_d.yawn_frames,
#                              mar_d.yawn_min_frames, 270, 50, 170, 14, "Yawn")

#                 # Head pose
#                 pitch, yaw, roll = hp_d.update(lm, W, H)
#                 if hp_d.is_distracted:
#                     hud.draw_alert(frame, "HEAD DISTRACTED!", 190, (0,100,255))
#                     if alert_m.trigger("head", 1100, 700,
#                                        "Please keep your eyes on the road."):
#                         alert_msg = "HEAD DISTRACTED"
#                 else:
#                     alert_m.reset_cooldown("head")
#                 hud.draw_bar(frame, hp_d.head_frames,
#                              hp_d.alert_frames, 270, 78, 170, 14, "Head")
#                 hud.draw_nose_arrow(frame, lm, W, H, yaw, pitch)

#             else:
#                 cv2.putText(frame, "No face", (270,40),
#                             cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0,165,255), 2)
#                 ear_d.eye_frames = mar_d.yawn_frames = hp_d.head_frames = 0
#                 ear_d.is_drowsy = mar_d.is_yawning = hp_d.is_distracted = False

#             # ── Fatigue score ─────────────────────────────────────────
#             fatigue_score = fatigue_calc.calculate(
#                 ear=ear_val, mar=mar_val,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_yawning=mar_d.is_yawning,
#                 is_distracted=hp_d.is_distracted,
#                 phone_detected=phone_detected,
#                 eye_frames=ear_d.eye_frames,
#                 drowsy_frames=ear_d.drowsy_frames,
#             )
#             draw_fatigue_score(frame, fatigue_score)

#             # ── Critical monitor — BOTH conditions ────────────────────
#             loc = critical_monitor._location   # prefetched location
#             stop_alert = critical_monitor.update(
#                 fatigue_score=fatigue_score,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_distracted=hp_d.is_distracted,
#                 location=loc,
#             )
#             if stop_alert:
#                 alert_msg = stop_alert

#             # Draw critical timer + rest places on frame
#             critical_monitor.draw_on_frame(frame)
#             elapsed = critical_monitor.get_elapsed()   # for web state

#             # Also fire basic emergency alert independently
#             if fatigue_score >= 75:
#                 emergency.check_and_alert(fatigue_score)

#             # ── Log frame ─────────────────────────────────────────────
#             reporter.log_frame(
#                 ear=ear_val, mar=mar_val,
#                 pitch=pitch, yaw=yaw, roll=roll,
#                 is_drowsy=ear_d.is_drowsy,
#                 is_yawning=mar_d.is_yawning,
#                 is_distracted=hp_d.is_distracted,
#                 phone_detected=phone_detected,
#                 fatigue_score=fatigue_score,
#             )

#             # ── Web state ─────────────────────────────────────────────
#             if update_state:
#                 from detectors.fatigue_score import FatigueScoreCalculator as FSC
#                 update_state(
#                     ear=round(ear_val,3), mar=round(mar_val,3),
#                     pitch=round(pitch,2), yaw=round(yaw,2), roll=round(roll,2),
#                     fatigue_score=round(fatigue_score,1),
#                     fatigue_level=FSC.level(fatigue_score),
#                     is_drowsy=ear_d.is_drowsy,
#                     is_yawning=mar_d.is_yawning,
#                     is_distracted=hp_d.is_distracted,
#                     phone_detected=phone_detected,
#                     blink_count=ear_d.blink_count,
#                     yawn_count=mar_d.yawn_count,
#                     head_alerts=hp_d.alert_count,
#                     fps=round(fps,1),
#                     face_detected=res.multi_face_landmarks is not None,
#                     alert_msg=alert_msg,
#                     session_time=session_time,
#                     critical_elapsed=round(elapsed,1),
#                 )

#             # ── HUD ───────────────────────────────────────────────────
#             hud.draw_panel(
#                 frame, ear_val, mar_val, pitch, yaw, roll,
#                 ear_d.blink_count, mar_d.yawn_count, hp_d.alert_count,
#                 ear_d.threshold, mar_d.threshold,
#                 hp_d.pitch_low, hp_d.pitch_high,
#                 hp_d.yaw_limit, hp_d.roll_limit,
#             )
#             hud.draw_fps(frame, fps)

#             cv2.imshow("Driver Safety System v5", frame)
#             key = cv2.waitKey(1) & 0xFF
#             if key == 27:
#                 break
#             elif key == ord("r"):
#                 ear_d.reset(); mar_d.reset(); hp_d.reset()
#                 print("[INFO] Reset done.")

#     finally:
#         cap.release()
#         cv2.destroyAllWindows()
#         summary = reporter.finalize(
#             blink_count=ear_d.blink_count,
#             yawn_count=mar_d.yawn_count,
#             head_alert_count=hp_d.alert_count,
#         )
#         print(f"\n[REPORT] Summary: {summary}")
#         print(f"[REPORT] Frames:  {reporter.frames_path}")


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--config",   default="config.yaml")
#     parser.add_argument("--camera",   type=int, default=0)
#     parser.add_argument("--no-tts",   action="store_true")
#     parser.add_argument("--no-web",   action="store_true")
#     parser.add_argument("--evaluate", default=None)
#     args = parser.parse_args()
#     cfg = load_config(args.config)
#     run(cfg, camera_index=args.camera,
#         no_tts=args.no_tts, no_web=args.no_web,
#         evaluate_path=args.evaluate)









