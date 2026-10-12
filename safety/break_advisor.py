"""
safety/break_advisor.py
=======================
Continuous Driving Break Advisor (Rule of 2 Hours).
Monitors continuous driving duration and provides progressive rest reminders
aligned with automotive fatigue regulations (e.g. EU Regulation (EC) 561/2006
and commercial driver safety guidelines).
"""

import time
import logging

logger = logging.getLogger(__name__)


class BreakAdvisor:
    """
    Tracks uninterrupted driving stint and triggers rest break advisories.
    Default: 120 minutes (7200s) maximum continuous driving stint,
    with a pre-warning at 90 minutes (5400s).
    """

    def __init__(
        self,
        max_drive_minutes: float = 120.0,
        warning_minutes: float = 90.0,
        repeat_interval_minutes: float = 15.0,
    ):
        self.max_drive_sec = max_drive_minutes * 60.0
        self.warning_sec = warning_minutes * 60.0
        self.repeat_interval_sec = repeat_interval_minutes * 60.0

        self.start_time: float = time.time()
        self._warning_fired: bool = False
        self._last_alert_time: float = 0.0
        self.break_due: bool = False

    def reset(self):
        """Resets the continuous driving clock when driver rests."""
        self.start_time = time.time()
        self._warning_fired = False
        self._last_alert_time = 0.0
        self.break_due = False
        logger.info("[BreakAdvisor] Continuous driving timer reset.")

    def update(self) -> str:
        """
        Check continuous driving time.
        Returns alert message string if an alert should be triggered, otherwise empty string.
        """
        now = time.time()
        elapsed = now - self.start_time

        # Milestone 1: Pre-warning (e.g., at 90 minutes)
        if elapsed >= self.warning_sec and not self._warning_fired:
            self._warning_fired = True
            mins = int(elapsed // 60)
            logger.info(f"[BreakAdvisor] 90-min fatigue advisory triggered ({mins} min driving).")
            return f"DRIVE TIME: {mins}m — Plan a rest stop soon"

        # Milestone 2: Max continuous driving reached (e.g., at 120 minutes)
        if elapsed >= self.max_drive_sec:
            self.break_due = True
            if (now - self._last_alert_time) >= self.repeat_interval_sec:
                self._last_alert_time = now
                hrs = round(elapsed / 3600.0, 1)
                logger.warning(f"[BreakAdvisor] Continuous driving limit reached ({hrs} hrs)!")
                return f"REST BREAK DUE! Continuous driving {hrs}h"

        return ""

    def get_status(self) -> dict:
        """Returns telemetry dict for HUD and web dashboard."""
        elapsed = time.time() - self.start_time
        remaining = max(0.0, self.max_drive_sec - elapsed)

        hrs_el = int(elapsed // 3600)
        mins_el = int((elapsed % 3600) // 60)
        secs_el = int(elapsed % 60)
        elapsed_str = f"{hrs_el:02d}:{mins_el:02d}:{secs_el:02d}" if hrs_el > 0 else f"{mins_el:02d}:{secs_el:02d}"

        hrs_rem = int(remaining // 3600)
        mins_rem = int((remaining % 3600) // 60)
        rem_str = f"{hrs_rem:02d}h {mins_rem:02d}m" if hrs_rem > 0 else f"{mins_rem:02d}m"

        return {
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_str": elapsed_str,
            "remaining_seconds": round(remaining, 1),
            "remaining_str": rem_str,
            "break_due": self.break_due,
            "progress_percent": min(100.0, round((elapsed / max(1.0, self.max_drive_sec)) * 100.0, 1)),
        }
