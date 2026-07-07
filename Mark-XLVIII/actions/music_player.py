#music_player.py — ROOKI music player: searches YouTube, opens in Brave, controls via vision
import json
import os
import subprocess
import threading
import time
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

from actions.vision_automation import (
    find_text, find_and_click, describe_screen,
    type_text, press_key, scroll, wait_for_text, click,
    Element,
)

_BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

# ── Player state ────────────────────────────────────────────────────────
_current = {
    "state": "stopped",   # playing | paused | stopped
    "title": "",
    "url": "",
    "volume": 50,
    "tab_handle": None,   # placeholder for future tab tracking
}


def _open_in_brave(url: str) -> bool:
    """Open a URL in Brave browser (avoids blank tabs)."""
    try:
        if os.path.exists(_BRAVE_PATH):
            subprocess.Popen([_BRAVE_PATH, "--new-tab", url], shell=False)
            return True
        # Brave not found — try other browsers directly (avoids blank-tab shell issue)
        _FALLBACK_BROWSERS = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        for browser in _FALLBACK_BROWSERS:
            if os.path.exists(browser):
                subprocess.Popen([browser, url], shell=False)
                return True
        # Last resort: os.startfile — safer than webbrowser.open / cmd start
        os.startfile(url)
        return True
    except Exception as e:
        print(f"[Music] Browser open failed: {e}")
        return False


def _search_ytdlp(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube via yt-dlp."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--no-download",
                "--default-search", "ytsearch",
                "--playlist-items", str(max_results),
                "--no-playlist",
                f"ytsearch:{query}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        entries = []
        for line in result.stdout.strip().splitlines():
            if line:
                data = json.loads(line)
                entries.append({
                    "title": data.get("title", "Unknown"),
                    "url": data.get("webpage_url", ""),
                    "duration": data.get("duration", 0),
                    "channel": data.get("channel", ""),
                    "id": data.get("id", ""),
                })
        return entries
    except Exception as e:
        print(f"[Music] yt-dlp search failed: {e}")
        return []


# ── Public API ──────────────────────────────────────────────────────────

def play(query: str) -> str:
    """Play music: search YouTube, open in Brave."""
    global _current
    if not query.strip():
        return "What would you like to listen to, sir?"

    # ── Debounce: ignore repeated play calls within 8 s (prevents audio-echo loop) ──
    now = time.monotonic()
    if now - _current.get("_last_play_time", 0.0) < 8.0 and _current["state"] == "playing":
        print(f"[Music] Debounced duplicate play call for: {query}")
        return f"Already playing {_current['title']}, sir."

    entries = _search_ytdlp(query, max_results=1)
    if not entries:
        search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        _open_in_brave(search_url)
        _current["_last_play_time"] = time.monotonic()
        return f"Searching for '{query}' in Brave, sir."

    entry = entries[0]
    title = entry["title"]
    video_url = entry["url"]
    _current["title"] = title
    _current["url"] = video_url
    _current["state"] = "playing"
    _current["_last_play_time"] = time.monotonic()

    # Open directly in Brave — YouTube autoplays via direct URL, no spacebar needed.
    # Removed _auto_play spacebar: it fired after 3.5 s and PAUSED the video if
    # autoplay had already started, causing the play/pause loop glitch.
    _open_in_brave(video_url)

    return f"Playing {title} in Brave, sir."


def next_track() -> str:
    """Skip to next track by finding and clicking the Next button in YouTube."""
    el = find_text("Skip")
    if not el:
        el = find_text("Next")
    if not el:
        # Try keyboard shortcut
        try:
            import pyautogui
            pyautogui.press("nexttrack")  # media key
            press_key("nexttrack")
            return "Skipped to next track, sir."
        except Exception:
            pass
        return "Could not find the Next button, sir. Try saying 'describe screen'."

    click(el)
    _current["state"] = "playing"
    return "Skipped to next track, sir."


def previous_track() -> str:
    """Go to previous track."""
    el = find_text("Previous")
    if not el:
        try:
            import pyautogui
            pyautogui.press("prevtrack")
            return "Went to previous track, sir."
        except Exception:
            pass
        return "Could not find the Previous button, sir."
    click(el)
    return "Went to previous track, sir."


def toggle_play_pause() -> str:
    """Toggle play/pause — sends spacebar only to the Brave/YouTube window."""
    global _current

    sent = False
    try:
        import pygetwindow as gw
        import pyautogui
        # Find a Brave or YouTube window and focus it before pressing space
        wins = [
            w for w in gw.getAllWindows()
            if w.title and any(k in w.title.lower() for k in ("brave", "youtube", "you tube"))
        ]
        if wins:
            wins[0].activate()
            time.sleep(0.3)  # let OS finish the focus switch
            pyautogui.press("k")  # YouTube's play/pause key (safer than space)
            sent = True
    except Exception:
        pass

    if not sent:
        # No browser window found — use media key as last resort (no random focus)
        try:
            import pyautogui
            pyautogui.press("playpause")
        except Exception:
            pass

    if _current["state"] == "playing":
        _current["state"] = "paused"
        return "Paused, sir."
    else:
        _current["state"] = "playing"
        return "Resumed, sir."


def pause() -> str:
    return toggle_play_pause()


def resume() -> str:
    return toggle_play_pause()


def stop() -> str:
    """Stop and close the YouTube tab."""
    global _current
    try:
        import pyautogui
        pyautogui.hotkey("ctrl", "w")
    except Exception:
        pass
    _current["state"] = "stopped"
    _current["title"] = ""
    return "Playback stopped and tab closed, sir."


def set_volume(level: int) -> str:
    """Set system volume level via Windows API."""
    global _current
    level = max(0, min(100, level))
    _current["volume"] = level
    try:
        # Windows volume control
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    except Exception as e:
        # Fallback: keyboard volume keys
        try:
            import pyautogui
            if level > _current["volume"]:
                for _ in range((level - _current["volume"]) // 2):
                    pyautogui.press("volumeup")
            else:
                for _ in range((_current["volume"] - level) // 2):
                    pyautogui.press("volumedown")
        except Exception:
            pass
    return f"Volume set to {level}, sir."


def volume_up(step: int = 10) -> str:
    return set_volume(_current["volume"] + step)


def volume_down(step: int = 10) -> str:
    return set_volume(_current["volume"] - step)


def now_playing() -> str:
    if _current["state"] == "playing" and _current["title"]:
        return f"Now playing: {_current['title']}, sir."
    elif _current["state"] == "paused" and _current["title"]:
        return f"Paused: {_current['title']}, sir."
    return "Nothing is playing right now, sir."


def search(query: str, max_results: int = 5) -> str:
    """Search YouTube and list results."""
    entries = _search_ytdlp(query, max_results)
    if not entries:
        return f"No results found for '{query}', sir."
    lines = ["Here's what I found on YouTube:"]
    for i, e in enumerate(entries, 1):
        mins = e["duration"] // 60 if e["duration"] else 0
        secs = e["duration"] % 60 if e["duration"] else 0
        dur = f"{mins}:{secs:02d}" if mins else "?"
        lines.append(f"{i}. {e['title']} — {e['channel']} ({dur})")
    lines.append("Say 'play number X' to play one, sir.")
    return "\n".join(lines)


def search_and_play(query: str, index: int = 1) -> str:
    """Search YouTube, then play the Nth result."""
    entries = _search_ytdlp(query, max_results=index)
    if len(entries) < index:
        return f"Could not find result #{index} for '{query}', sir."
    entry = entries[index - 1]
    return play(entry["title"])


def fullscreen() -> str:
    """Toggle fullscreen in YouTube."""
    try:
        import pyautogui
        pyautogui.press("f")
        return "Toggled fullscreen, sir."
    except Exception:
        return "Could not toggle fullscreen, sir."


def like() -> str:
    """Like the current video."""
    el = find_text("Like")
    if not el:
        # Try finding thumbs up icon via color
        return "Could not find Like button, sir."
    click(el)
    return "Liked, sir."


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "play":          lambda p, pl, sp: play(p.get("query", "")),
    "pause":         lambda p, pl, sp: pause(),
    "resume":        lambda p, pl, sp: resume(),
    "stop":          lambda p, pl, sp: stop(),
    "next":          lambda p, pl, sp: next_track(),
    "previous":      lambda p, pl, sp: previous_track(),
    "volume":        lambda p, pl, sp: set_volume(p.get("level", 50)),
    "volume_up":     lambda p, pl, sp: volume_up(p.get("step", 10)),
    "volume_down":   lambda p, pl, sp: volume_down(p.get("step", 10)),
    "now_playing":   lambda p, pl, sp: now_playing(),
    "search":        lambda p, pl, sp: search(p.get("query", ""), p.get("max_results", 5)),
    "fullscreen":    lambda p, pl, sp: fullscreen(),
    "like":          lambda p, pl, sp: like(),
}

def music_player(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    if player:
        player.write_log(f"[Music] Action: {action}")

    handler = _ACTION_MAP.get(action)
    if not handler:
        return (
            f"Unknown music action: '{action}'. "
            "Available: play, pause, resume, stop, next, previous, "
            "volume, volume_up, volume_down, now_playing, search, fullscreen, like."
        )

    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        print(f"[Music] Error in {action}: {e}")
        return f"Music {action} failed, sir: {e}"
