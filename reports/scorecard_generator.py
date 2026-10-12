"""
reports/scorecard_generator.py
==============================
Generates an executive, visual Post-Trip Driver Safety Scorecard & HTML Report.
Calculates safety score (0-100) and automotive safety grade (A+ to F),
with distraction/drowsiness breakdowns and personalized driving recommendations.
"""

import os
import logging

logger = logging.getLogger(__name__)


class ScorecardGenerator:
    """Generates a standalone, styled HTML Scorecard for a driving session."""

    @staticmethod
    def calculate_score(s: dict) -> tuple:
        """
        Calculates (safety_score: float, grade: str, grade_title: str, color: str).
        """
        total_frames = max(1, int(s.get("total_frames", 1)))
        drowsy_pct = float(s.get("drowsy_percent", 0.0))
        distract_pct = float(s.get("distracted_percent", 0.0))
        phone_frames = int(s.get("phone_frames", 0))
        avg_fatigue = float(s.get("avg_fatigue_score", 0.0))

        # Penalty calculations
        drowsy_penalty = min(35.0, drowsy_pct * 3.5)
        distract_penalty = min(25.0, distract_pct * 2.0)
        phone_penalty = min(25.0, (phone_frames / total_frames) * 150.0)
        fatigue_penalty = min(25.0, (avg_fatigue / 100.0) * 20.0)

        total_penalty = drowsy_penalty + distract_penalty + phone_penalty + fatigue_penalty
        score = round(max(0.0, min(100.0, 100.0 - total_penalty)), 1)

        if score >= 92.0:
            return score, "A+", "EXCELLENT — Exemplary Road Alertness", "#3fb950"
        elif score >= 85.0:
            return score, "A", "GOOD — Attentive & Safe Driver", "#2ea043"
        elif score >= 75.0:
            return score, "B", "SATISFACTORY — Minor Fatigue / Focus Lapses", "#d29922"
        elif score >= 65.0:
            return score, "C", "CAUTION — Moderate Distraction & Tiredness", "#f0883e"
        elif score >= 50.0:
            return score, "D", "HIGH RISK — Repeated Drowsiness Episodes", "#f85149"
        else:
            return score, "F", "CRITICAL RISK — Severe Sleep Deprivation", "#da3633"

    @classmethod
    def generate_html(cls, summary: dict, output_dir: str = "reports/sessions") -> str:
        """Generates and writes <session_id>_scorecard.html."""
        session_id = summary.get("session_id", "session")
        score, grade, grade_title, color = cls.calculate_score(summary)

        # Generate personalized recommendations
        tips = []
        drowsy_pct = float(summary.get("drowsy_percent", 0.0))
        distract_pct = float(summary.get("distracted_percent", 0.0))
        phone_frames = int(summary.get("phone_frames", 0))

        if drowsy_pct > 2.0:
            tips.append("Take a 15–20 minute power nap or break before long highway drives.")
        if distract_pct > 4.0:
            tips.append("Avoid turning head away from forward road view for extended durations.")
        if phone_frames > 0:
            tips.append("Store mobile phones in a console or activate 'Do Not Disturb While Driving'.")
        if float(summary.get("yawn_count", 0)) >= 3:
            tips.append("Frequent yawning indicates early stage micro-sleep vulnerability.")
        if not tips:
            tips.append("Outstanding road focus and alertness maintained throughout the entire drive!")

        duration = summary.get("duration_seconds", 0)
        mins = int(float(duration) // 60)
        secs = int(float(duration) % 60)
        duration_formatted = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        circ = 2 * 3.14159 * 54
        offset = circ - (score / 100.0) * circ

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DriveGuard — Safety Scorecard ({session_id})</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@500;600;700&family=Orbitron:wght@700;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #080c10; --card: #0d1117; --card-border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --cyan: #00d4ff;
    --grade-col: {color};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text); font-family: 'Rajdhani', sans-serif;
    min-height: 100vh; padding: 30px 16px; display: flex; justify-content: center;
  }}
  .card-container {{
    max-width: 820px; width: 100%; background: var(--card);
    border: 1px solid var(--card-border); border-radius: 16px; padding: 32px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.6); position: relative;
  }}
  .header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--card-border); padding-bottom: 18px; margin-bottom: 24px;
  }}
  .brand {{
    font-family: 'Orbitron', monospace; font-size: 22px; font-weight: 900;
    color: var(--cyan); letter-spacing: 2px;
  }}
  .badge-session {{
    font-family: 'Share Tech Mono', monospace; font-size: 12px;
    color: var(--muted); border: 1px solid var(--card-border);
    padding: 4px 10px; border-radius: 6px;
  }}
  /* Hero Grade Display */
  .hero {{
    display: flex; align-items: center; gap: 30px; margin-bottom: 28px;
    background: #161b22; border-radius: 14px; padding: 24px;
    border: 1px solid rgba(255,255,255,0.06);
  }}
  .ring-wrap {{ position: relative; width: 140px; height: 140px; flex-shrink: 0; }}
  svg.score-ring {{ width: 140px; height: 140px; transform: rotate(-90deg); }}
  .ring-bg {{ fill: none; stroke: #21262d; stroke-width: 10; }}
  .ring-val {{
    fill: none; stroke: var(--grade-col); stroke-width: 10;
    stroke-linecap: round; stroke-dasharray: {circ:.1f};
    stroke-dashoffset: {offset:.1f};
  }}
  .ring-text {{
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
  }}
  .score-num {{ font-family: 'Orbitron', monospace; font-size: 32px; font-weight: 900; color: var(--grade-col); }}
  .score-lbl {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; }}
  .grade-info {{ flex: 1; }}
  .grade-badge {{
    display: inline-block; font-family: 'Orbitron', monospace; font-size: 16px;
    font-weight: 900; color: var(--grade-col); border: 1px solid var(--grade-col);
    background: rgba(255,255,255,0.05); padding: 4px 14px; border-radius: 8px; margin-bottom: 8px;
  }}
  .grade-title {{ font-size: 18px; font-weight: 700; color: #fff; margin-bottom: 6px; }}
  .grade-desc {{ color: var(--muted); font-size: 14px; line-height: 1.4; }}
  
  /* Metrics Grid */
  .metrics-grid {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px;
  }}
  .metric-box {{
    background: #161b22; border: 1px solid var(--card-border); border-radius: 10px;
    padding: 14px; text-align: center;
  }}
  .metric-val {{ font-family: 'Orbitron', monospace; font-size: 22px; font-weight: 700; color: var(--cyan); }}
  .metric-lbl {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; margin-top: 4px; }}

  /* Key Stats List */
  .details-box {{
    background: #161b22; border: 1px solid var(--card-border); border-radius: 12px;
    padding: 18px; margin-bottom: 24px;
  }}
  .detail-row {{
    display: flex; justify-content: space-between; padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 14px;
  }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-lbl {{ color: var(--muted); }}
  .detail-val {{ font-family: 'Share Tech Mono', monospace; font-weight: 600; }}

  /* Recommendations */
  .tips-box {{
    background: rgba(0, 212, 255, 0.04); border: 1px solid rgba(0, 212, 255, 0.2);
    border-radius: 12px; padding: 20px; margin-bottom: 24px;
  }}
  .tips-title {{
    font-family: 'Orbitron', monospace; font-size: 13px; font-weight: 700;
    color: var(--cyan); letter-spacing: 1px; margin-bottom: 12px;
  }}
  .tips-list {{ list-style: none; }}
  .tips-list li {{
    position: relative; padding-left: 20px; margin-bottom: 8px; font-size: 14px; line-height: 1.4;
  }}
  .tips-list li::before {{
    content: "▶"; position: absolute; left: 0; color: var(--cyan); font-size: 10px; top: 3px;
  }}

  /* Actions */
  .actions {{ display: flex; justify-content: flex-end; gap: 12px; }}
  .btn {{
    background: var(--cyan); color: #000; border: none; padding: 10px 20px;
    border-radius: 8px; font-family: 'Rajdhani', sans-serif; font-weight: 700;
    font-size: 14px; letter-spacing: 1px; cursor: pointer; text-decoration: none;
    transition: opacity 0.2s;
  }}
  .btn:hover {{ opacity: 0.9; }}
  .btn-outline {{
    background: transparent; color: var(--muted); border: 1px solid var(--card-border);
  }}
  .btn-outline:hover {{ color: #fff; border-color: #fff; }}

  @media print {{
    body {{ background: #fff; color: #000; }}
    .actions {{ display: none; }}
    .card-container {{ border: none; box-shadow: none; }}
  }}
</style>
</head>
<body>
<div class="card-container">
  <div class="header">
    <div class="brand">DRIVEGUARD <span>SCORECARD</span></div>
    <div class="badge-session">{summary.get('session_date', 'Date')} | {session_id}</div>
  </div>

  <div class="hero">
    <div class="ring-wrap">
      <svg class="score-ring" viewBox="0 0 120 120">
        <circle class="ring-bg" cx="60" cy="60" r="54" />
        <circle class="ring-val" cx="60" cy="60" r="54" />
      </svg>
      <div class="ring-text">
        <div class="score-num">{score:.0f}</div>
        <div class="score-lbl">SCORE</div>
      </div>
    </div>
    <div class="grade-info">
      <div class="grade-badge">GRADE {grade}</div>
      <div class="grade-title">{grade_title}</div>
      <div class="grade-desc">
        Overall road alertness index based on eye closures, facial yawning rates, sustained head-pose alignment, and distraction latency.
      </div>
    </div>
  </div>

  <div class="metrics-grid">
    <div class="metric-box">
      <div class="metric-val">{duration_formatted}</div>
      <div class="metric-lbl">DRIVE TIME</div>
    </div>
    <div class="metric-box">
      <div class="metric-val">{summary.get('blink_count', 0)}</div>
      <div class="metric-lbl">TOTAL BLINKS</div>
    </div>
    <div class="metric-box">
      <div class="metric-val">{summary.get('yawn_count', 0)}</div>
      <div class="metric-lbl">YAWN EVENTS</div>
    </div>
    <div class="metric-box">
      <div class="metric-val">{summary.get('head_alert_count', 0)}</div>
      <div class="metric-lbl">HEAD ALERTS</div>
    </div>
  </div>

  <div class="details-box">
    <div class="detail-row">
      <span class="detail-lbl">Drowsiness Duration (% of session)</span>
      <span class="detail-val" style="color:{'#f85149' if drowsy_pct > 2 else '#3fb950'}">{summary.get('drowsy_percent', 0)}%</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Distracted Driving (% of session)</span>
      <span class="detail-val" style="color:{'#f85149' if distract_pct > 5 else '#3fb950'}">{summary.get('distracted_percent', 0)}%</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Phone Usage Frame Count</span>
      <span class="detail-val">{summary.get('phone_frames', 0)} frames</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Average Fatigue Score</span>
      <span class="detail-val">{summary.get('avg_fatigue_score', 0)} / 100</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Peak Fatigue Level</span>
      <span class="detail-val">{summary.get('max_fatigue_score', 0)} / 100 ({summary.get('risk_level', 'LOW')})</span>
    </div>
  </div>

  <div class="tips-box">
    <div class="tips-title">SAFETY INSIGHTS & RECOMMENDATIONS</div>
    <ul class="tips-list">
      {''.join(f'<li>{t}</li>' for t in tips)}
    </ul>
  </div>

  <div class="actions">
    <button class="btn btn-outline" onclick="window.print()">Print / Save PDF</button>
    <a href="/history" class="btn">View All Sessions</a>
  </div>
</div>
</body>
</html>
"""
        os.makedirs(output_dir, exist_ok=True)
        filename = f"{session_id}_scorecard.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"[Scorecard] HTML scorecard generated: {filepath}")
        return filepath
