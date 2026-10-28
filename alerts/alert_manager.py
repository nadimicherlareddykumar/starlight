"""
alerts/alert_manager.py  — v4
==============================
win32com.client se direct Windows SAPI TTS.
pyttsx3 hataya — 64-bit Python pe silently fail hota tha.
Queue-based dedicated thread — OpenCV loop se koi conflict nahi.
"""

import threading
import time
import platform
import logging
import queue

logger = logging.getLogger(__name__)

_tts_queue        = queue.Queue()
_tts_thread_started = False


def _tts_worker():
    """
    Dedicated background thread — queue se text uthao aur bolo.
    win32com SAPI directly use karta hai — pyttsx3 se zyada reliable.
    """
    try:
        import win32com.client
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Rate  = 0    # -10 (slow) to 10 (fast), 0 = normal
        speaker.Volume = 100
        logger.info("[TTS] win32com SAPI ready.")
        use_sapi = True
    except Exception as e:
        logger.warning(f"[TTS] win32com failed: {e}. Falling back to pyttsx3.")
        use_sapi = False
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 150)
        except Exception as e2:
            logger.warning(f"[TTS] pyttsx3 also failed: {e2}")
            engine = None

    while True:
        text = _tts_queue.get() 
        if text is None:
            break
        try:
            if use_sapi:
                speaker.Speak(text)
            elif engine:
                engine.say(text)
                engine.runAndWait()
            else:
                print(f"[ALERT VOICE] {text}")
        except Exception as e:
            logger.warning(f"[TTS] Speak error: {e}")
            print(f"[ALERT VOICE] {text}")
        finally:
            _tts_queue.task_done()


def _ensure_tts_thread():
    global _tts_thread_started
    if not _tts_thread_started:
        t = threading.Thread(target=_tts_worker, daemon=True)
        t.start()
        _tts_thread_started = True
        logger.info("[TTS] Worker thread started.")


def _beep_windows(tones: list):
    """tones: list of (freq, dur_ms) or single (freq, dur)"""
    try:
        import winsound
        for freq, dur in tones:
            winsound.Beep(max(37, min(32767, int(freq))), max(10, int(dur)))
    except Exception as e:
        logger.warning(f"[Beep] {e}")


def _beep_fallback(tones: list):
    print("\a", end="", flush=True)


# ── AlertManager ──────────────────────────────────────────────────────────────
class AlertManager:

    PATTERNS = {
        "urgent": [(1800, 140), (2400, 160), (1800, 140), (2400, 180)],
        "warning": [(1200, 120), (1600, 140)],
        "break": [(900, 150), (1200, 150), (1500, 250)],
        "calibrated": [(600, 100), (900, 120), (1400, 180)],
    }

    def __init__(self, cooldown: float = 6.0, tts_rate: int = 150):
        self.cooldown    = cooldown
        self.tts_rate    = tts_rate
        self._last_alert: dict = {}
        self._is_windows = platform.system() == "Windows"
        _ensure_tts_thread()

    def play_pattern(self, pattern_name: str):
        """Plays a specific multi-tone acoustic alert asynchronously."""
        tones = self.PATTERNS.get(pattern_name, [(1000, 300)])
        self._play_tones(tones)

    def trigger(
        self,
        alert_id: str,
        beep_freq: int  = 1000,
        beep_dur:  int  = 700,
        speech:    str  = "",
        force:     bool = False,
    ) -> bool:
        now  = time.time()
        last = self._last_alert.get(alert_id, 0.0)

        if not force and (now - last) < self.cooldown:
            return False

        self._last_alert[alert_id] = now

        # Use dual-tone emergency buzzer for severe threats
        if alert_id in ("drowsy", "critical", "emergency"):
            self.play_pattern("urgent")
        elif alert_id in ("phone", "head", "gaze", "yawn", "driver_missing", "camera_lost"):
            self.play_pattern("warning")
        else:
            self._play_tones([(beep_freq, beep_dur)])

        if speech:
            if _tts_queue.qsize() < 2:
                _tts_queue.put(speech)

        logger.info(f"[Alert] {alert_id}: {speech}")
        return True

    def reset_cooldown(self, alert_id: str):
        self._last_alert.pop(alert_id, None)

    def _play_tones(self, tones: list):
        target = _beep_windows if self._is_windows else _beep_fallback
        threading.Thread(target=target, args=(tones,), daemon=True).start()
