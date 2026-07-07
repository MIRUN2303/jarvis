#timer.py — Countdown timer, stopwatch, and alarm for ROOKI
import threading
import time
from datetime import datetime, timedelta

# ── Active timers ───────────────────────────────────────────────────────
_timers: list[dict] = []  # {"id": int, "label": str, "end": float, "duration": int, "type": "timer|alarm|stopwatch"}
_next_id = 0
_lock = threading.Lock()


def _timer_thread(t: dict, on_fire: callable):
    """Background thread that waits for timer to complete."""
    remaining = t["end"] - time.time()
    if remaining > 0:
        time.sleep(remaining)
    with _lock:
        if t in _timers:
            _timers.remove(t)
    t["fired"] = True
    if on_fire:
        on_fire(t)


def _parse_duration(text: str) -> int | None:
    """Parse a duration string like '5 minutes', '1 hour 30 min', '30s' into seconds."""
    text = text.lower().strip()
    total = 0
    patterns = [
        (r"(\d+)\s*(hour|hr|h|saat)", 3600),
        (r"(\d+)\s*(minute|min|m|dakika)", 60),
        (r"(\d+)\s*(second|sec|s|saniye)", 1),
    ]
    import re
    for pattern, multiplier in patterns:
        for match in re.finditer(pattern, text):
            total += int(match.group(1)) * multiplier
    return total if total > 0 else None


def _format_duration(seconds: int) -> str:
    """Format seconds into human readable string."""
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    parts = []
    if h: parts.append(f"{h} hour{'s' if h>1 else ''}")
    if m: parts.append(f"{m} minute{'s' if m>1 else ''}")
    if s: parts.append(f"{s} second{'s' if s>1 else ''}")
    return " ".join(parts) if parts else "0 seconds"


# ── Public API ──────────────────────────────────────────────────────────

def set_timer(duration_text: str, label: str = "Timer", on_fire: callable = None) -> str:
    """
    Set a countdown timer.
    duration_text: e.g. "5 minutes", "1 hour 30 min", "30 seconds"
    label: optional name for the timer
    """
    global _next_id

    seconds = _parse_duration(duration_text)
    if not seconds:
        return (
            f"Could not understand '{duration_text}', sir. "
            "Try: '5 minutes', '1 hour', '30 seconds'"
        )

    with _lock:
        tid = _next_id
        _next_id += 1
        t = {
            "id": tid,
            "label": label,
            "end": time.time() + seconds,
            "duration": seconds,
            "type": "timer",
            "fired": False,
        }
        _timers.append(t)

    threading.Thread(target=_timer_thread, args=(t, on_fire), daemon=True).start()

    return f"Timer set for {_format_duration(seconds)}, sir. I'll let you know when it's done."


def set_alarm(time_text: str, label: str = "Alarm", on_fire: callable = None) -> str:
    """
    Set an alarm for a specific time.
    time_text: e.g. "7:00 AM", "14:30", "in 10 minutes"
    """
    global _next_id

    # Parse "in X minutes/hours"
    if time_text.lower().startswith("in "):
        seconds = _parse_duration(time_text[3:])
        if seconds:
            return set_timer(_format_duration(seconds), label, on_fire)

    # Parse specific time
    try:
        import re
        now = datetime.now()
        # Try HH:MM AM/PM
        match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?", time_text)
        if match:
            h, m, ampm = int(match.group(1)), int(match.group(2)), match.group(3)
            if ampm and ampm.upper() == "PM" and h < 12:
                h += 12
            if ampm and ampm.upper() == "AM" and h == 12:
                h = 0
            alarm_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if alarm_time <= now:
                alarm_time += timedelta(days=1)
            seconds = (alarm_time - now).total_seconds()
            return set_timer(_format_duration(int(seconds)), label, on_fire)
    except Exception:
        pass

    return f"Could not understand '{time_text}', sir. Try '7:00 AM' or 'in 10 minutes'."


def cancel_timer(timer_id: int = None, label: str = None) -> str:
    """Cancel a timer by ID or label."""
    global _timers
    with _lock:
        for t in list(_timers):
            if (timer_id is not None and t["id"] == timer_id) or \
               (label and label.lower() in t["label"].lower()):
                _timers.remove(t)
                return f"Cancelled timer '{t['label']}', sir."
    return "No matching timer found, sir."


def cancel_all_timers() -> str:
    """Cancel all active timers."""
    global _timers
    count = len(_timers)
    with _lock:
        _timers.clear()
    return f"Cancelled {count} timer{'s' if count!=1 else ''}, sir."


def list_timers() -> str:
    """List all active timers."""
    with _lock:
        if not _timers:
            return "No active timers, sir."
        lines = ["Active timers:"]
        for t in _timers:
            remaining = max(0, int(t["end"] - time.time()))
            lines.append(f"  #{t['id']}: {t['label']} — {_format_duration(remaining)} remaining")
        return "\n".join(lines)


def check_timer(timer_id: int = None) -> str:
    """Check remaining time on a specific timer."""
    with _lock:
        for t in _timers:
            if timer_id is None or t["id"] == timer_id:
                remaining = max(0, int(t["end"] - time.time()))
                return f"Timer '{t['label']}': {_format_duration(remaining)} remaining, sir."
    return "No matching timer found, sir."


def stopwatch_start() -> str:
    """Start a lap stopwatch."""
    global _next_id
    with _lock:
        tid = _next_id
        _next_id += 1
        t = {
            "id": tid,
            "label": "Stopwatch",
            "start": time.time(),
            "end": 0,
            "duration": 0,
            "type": "stopwatch",
            "fired": False,
            "laps": [],
        }
        _timers.append(t)
    return f"Stopwatch started, sir."


def stopwatch_lap() -> str:
    """Record a lap on the stopwatch."""
    with _lock:
        for t in _timers:
            if t["type"] == "stopwatch" and not t["fired"]:
                elapsed = int(time.time() - t["start"])
                t.setdefault("laps", []).append(elapsed)
                return f"Lap {len(t['laps'])}: {_format_duration(elapsed)}, sir."
    return "No active stopwatch, sir. Say 'start stopwatch' first."


def stopwatch_stop() -> str:
    """Stop the stopwatch."""
    with _lock:
        for t in _timers:
            if t["type"] == "stopwatch" and not t["fired"]:
                elapsed = int(time.time() - t["start"])
                t["fired"] = True
                _timers.remove(t)
                return f"Stopwatch stopped at {_format_duration(elapsed)}, sir."
    return "No active stopwatch, sir."


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "timer":       lambda p, pl, sp: set_timer(p.get("duration", "5 minutes"), p.get("label", "Timer")),
    "alarm":       lambda p, pl, sp: set_alarm(p.get("time", "7:00 AM"), p.get("label", "Alarm")),
    "cancel":      lambda p, pl, sp: cancel_timer(p.get("id"), p.get("label")),
    "cancel_all":  lambda p, pl, sp: cancel_all_timers(),
    "list":        lambda p, pl, sp: list_timers(),
    "check":       lambda p, pl, sp: check_timer(p.get("id")),
    "stopwatch":   lambda p, pl, sp: stopwatch_start(),
    "lap":         lambda p, pl, sp: stopwatch_lap(),
    "stop":        lambda p, pl, sp: stopwatch_stop(),
}

def timer_control(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "timer").lower().strip()

    if player:
        player.write_log(f"[Timer] Action: {action}")

    handler = _ACTION_MAP.get(action)
    if not handler:
        return (
            f"Unknown timer action: '{action}'. "
            "Available: timer, alarm, cancel, cancel_all, list, check, stopwatch, lap, stop."
        )

    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        print(f"[Timer] Error in {action}: {e}")
        return f"Timer {action} failed, sir: {e}"
