#vision_automation.py — ROOKI's eyes & hands: see screen, find elements, click like a human
import os
import time
import threading
import numpy as np
from dataclasses import dataclass
from typing import Callable

import mss
import cv2
import pyautogui
from PIL import Image

# ── OCR (lazy init — first call downloads model) ───────────────────────
_OCR_READER = None
_OCR_OK = None
_OCR_LOCK = threading.Lock()

def _get_ocr():
    """Initialize EasyOCR on first use (downloads model once)."""
    global _OCR_READER, _OCR_OK
    if _OCR_OK is not None:
        return _OCR_READER if _OCR_OK else None
    with _OCR_LOCK:
        if _OCR_OK is not None:  # double-check
            return _OCR_READER if _OCR_OK else None
        try:
            import easyocr
            print("[VisionAuto] Downloading OCR model (one-time)...")
            _OCR_READER = easyocr.Reader(["en"], gpu=False, verbose=False)
            _OCR_OK = True
            print("[VisionAuto] OCR ready")
        except Exception as e:
            print(f"[VisionAuto] OCR failed: {e}")
            _OCR_OK = False
            _OCR_READER = None
    return _OCR_READER if _OCR_OK else None


@dataclass
class Element:
    """A found UI element on screen."""
    label: str          # text label or "image_match"
    x: int              # center X
    y: int              # center Y
    w: int              # width
    h: int              # height
    confidence: float   # 0-1
    bbox: tuple = None  # (left, top, right, bottom)


# ── Screen capture ──────────────────────────────────────────────────────

_sct = mss.mss()

def _screen_np() -> np.ndarray:
    """Capture screen as numpy array (BGR for OpenCV)."""
    monitor = _sct.monitors[1]  # primary monitor
    img = _sct.grab(monitor)
    return cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)


def _screen_pil() -> Image.Image:
    """Capture screen as PIL Image."""
    monitor = _sct.monitors[1]
    img = _sct.grab(monitor)
    return Image.frombytes("RGB", img.size, img.rgb)


# ── OCR: Find text on screen ────────────────────────────────────────────

def find_text(label: str, min_confidence: float = 0.4) -> Element | None:
    """
    Find a UI element by its text label on screen.
    E.g.: find_text("Skip") → Element at Skip button coordinates
    Uses EasyOCR for text detection.
    """
    if not _OCR_OK:
        return None

    img = _screen_np()
    results = _OCR_READER.readtext(img)

    for bbox, text, conf in results:
        if conf < min_confidence:
            continue
        if label.lower() in text.lower():
            # bbox = [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            left, top = int(min(xs)), int(min(ys))
            right, bottom = int(max(xs)), int(max(ys))
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            return Element(
                label=text,
                x=cx, y=cy,
                w=right - left, h=bottom - top,
                confidence=conf,
                bbox=(left, top, right, bottom),
            )

    return None


def find_all_text(min_confidence: float = 0.4) -> list[Element]:
    """Find ALL text elements visible on screen."""
    if not _OCR_OK:
        return []

    img = _screen_np()
    results = _OCR_READER.readtext(img)
    elements = []
    for bbox, text, conf in results:
        if conf < min_confidence:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        left, top = int(min(xs)), int(min(ys))
        right, bottom = int(max(xs)), int(max(ys))
        elements.append(Element(
            label=text,
            x=(left+right)//2, y=(top+bottom)//2,
            w=right-left, h=bottom-top,
            confidence=conf,
            bbox=(left, top, right, bottom),
        ))
    return elements


# ── Template matching: Find image on screen ─────────────────────────────

def find_image(template_path: str, threshold: float = 0.8) -> Element | None:
    """
    Find an image on screen using OpenCV template matching.
    template_path: path to the template image file
    threshold: matching threshold (0-1)
    """
    if not os.path.exists(template_path):
        return None

    screen = _screen_np()
    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if template is None:
        return None

    # If template has alpha, use it as mask
    method = cv2.TM_CCOEFF_NORMED
    if template.shape[2] == 4:
        result = cv2.matchTemplate(screen, template[:, :, :3], method, mask=template[:, :, 3])
    else:
        result = cv2.matchTemplate(screen, template, method)

    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None

    h, w = template.shape[:2]
    left, top = max_loc
    return Element(
        label="image_match",
        x=left + w // 2, y=top + h // 2,
        w=w, h=h,
        confidence=max_val,
        bbox=(left, top, left + w, top + h),
    )


# ── Color/region finding ────────────────────────────────────────────────

def find_color(target_color: tuple[int, int, int], tolerance: int = 30) -> list[Element]:
    """
    Find all regions on screen matching a specific color.
    target_color: (B, G, R) tuple for OpenCV
    Returns list of Element for each connected region found.
    """
    screen = _screen_np()
    lower = np.array([max(0, c - tolerance) for c in target_color], dtype=np.uint8)
    upper = np.array([min(255, c + tolerance) for c in target_color], dtype=np.uint8)
    mask = cv2.inRange(screen, lower, upper)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    elements = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 10 or h < 10:  # skip tiny noise
            continue
        elements.append(Element(
            label="color_match",
            x=x + w // 2, y=y + h // 2,
            w=w, h=h,
            confidence=1.0,
            bbox=(x, y, x + w, y + h),
        ))
    return elements


# ── Clicking ─────────────────────────────────────────────────────────────

def click(element: Element, button: str = "left") -> bool:
    """Move mouse to element center and click."""
    try:
        pyautogui.moveTo(element.x, element.y, duration=0.2)
        time.sleep(0.1)
        pyautogui.click(button=button)
        return True
    except Exception as e:
        print(f"[VisionAuto] Click failed: {e}")
        return False


def double_click(element: Element) -> bool:
    """Double click an element."""
    try:
        pyautogui.moveTo(element.x, element.y, duration=0.2)
        time.sleep(0.1)
        pyautogui.doubleClick()
        return True
    except Exception as e:
        print(f"[VisionAuto] Double-click failed: {e}")
        return False


def right_click(element: Element) -> bool:
    """Right click an element."""
    return click(element, button="right")


def type_text(text: str, interval: float = 0.02):
    """Type text at current cursor position."""
    pyautogui.write(text, interval=interval)


def press_key(key: str):
    """Press a keyboard key (enter, tab, esc, etc.)."""
    pyautogui.press(key)


def scroll(clicks: int = -3):
    """Scroll. Negative = down, Positive = up."""
    pyautogui.scroll(clicks)


# ── High-level find-and-click ───────────────────────────────────────────

def find_and_click(label: str, min_confidence: float = 0.4, button: str = "left") -> str:
    """
    Find a UI element by text label and click it.
    Returns a human-readable result string.
    """
    el = find_text(label, min_confidence)
    if not el:
        return f"Could not find '{label}' on screen, sir."

    if click(el, button):
        return f"Found and clicked '{el.label}' on screen, sir."
    return f"Found '{label}' but couldn't click it, sir."


def find_and_click_image(template_path: str, threshold: float = 0.8) -> str:
    """Find an image on screen and click it."""
    el = find_image(template_path, threshold)
    if not el:
        return f"Could not find the image on screen, sir."
    if click(el):
        return f"Found and clicked the image on screen, sir."
    return "Found the image but couldn't click it, sir."


# ── Waiting ─────────────────────────────────────────────────────────────

def wait_for_text(label: str, timeout: float = 10.0, min_confidence: float = 0.4) -> Element | None:
    """Wait until a text label appears on screen (polling)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        el = find_text(label, min_confidence)
        if el:
            return el
        time.sleep(0.3)
    return None


def wait_for_image(template_path: str, timeout: float = 10.0, threshold: float = 0.8) -> Element | None:
    """Wait until an image appears on screen."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        el = find_image(template_path, threshold)
        if el:
            return el
        time.sleep(0.3)
    return None


# ── App-aware navigation ────────────────────────────────────────────────

# Skill: YouTube in Brave - how to navigate
_YOUTUBE_SKILL = """
YouTube in Brave navigation:
- Search: Click search bar at top, type query, press Enter
- Click video: First result is centered, ~30% from top
- Skip/Next: 'Skip' button at bottom center of player
- Pause/Play: Center of video player or spacebar
- Fullscreen: 'Full screen' button bottom-right or F11
- Volume: Slider bottom-left, or use keyboard arrows
- Back to search: Click YouTube logo top-left
- Like: Thumbs up button below video
- Library: 'Library' link in left sidebar
- Trending: 'Trending' link in left sidebar
"""

_BRAVE_SKILL = """
Brave browser navigation:
- Address bar: Click top of window, Ctrl+L to focus, type URL, press Enter
- New tab: Ctrl+T
- Close tab: Ctrl+W
- Switch tab: Ctrl+Tab
- Bookmarks: Click star icon in address bar
- History: Ctrl+H
- Downloads: Ctrl+J
- Back: Alt+Left Arrow or click ← button top-left
"""


def get_app_skill(app_name: str) -> str:
    """Get navigation flow for a known app."""
    skills = {
        "youtube": _YOUTUBE_SKILL,
        "brave": _BRAVE_SKILL,
        "browser": _BRAVE_SKILL,
    }
    return skills.get(app_name.lower(), "")


# ── Screenshot for analysis ─────────────────────────────────────────────

def capture_for_gemini() -> str:
    """
    Capture screen and save to a temp file for Gemini vision analysis.
    Returns the file path.
    """
    img = _screen_pil()
    path = os.path.join(os.environ.get("TEMP", "."), "rooki_screen.png")
    img.save(path, "PNG")
    return path


def describe_screen() -> str:
    """
    Get a text description of what's on screen (all text elements).
    Useful for understanding the current app state.
    """
    elements = find_all_text()
    if not elements:
        return "No text elements found on screen, sir."

    lines = ["What I see on screen:"]
    for el in sorted(elements, key=lambda e: (e.y, e.x)):
        lines.append(f"  - '{el.label}' at ({el.x}, {el.y})")
    return "\n".join(lines)


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "find_click":     lambda p, pl, sp: find_and_click(p.get("label", ""), p.get("confidence", 0.4), p.get("button", "left")),
    "find_click_img": lambda p, pl, sp: find_and_click_image(p.get("template", ""), p.get("threshold", 0.8)),
    "describe":       lambda p, pl, sp: describe_screen(),
    "type":           lambda p, pl, sp: (type_text(p.get("text", ""), p.get("interval", 0.02)), "Typed, sir.")[1],
    "press":          lambda p, pl, sp: (press_key(p.get("key", "enter")), f"Pressed {p.get('key', 'enter')}, sir.")[1],
    "scroll":         lambda p, pl, sp: (scroll(p.get("clicks", -3)), "Scrolled, sir.")[1],
    "app_skill":      lambda p, pl, sp: get_app_skill(p.get("app", "")),
}


def vision_automation(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "describe").lower().strip()

    if player:
        player.write_log(f"[VisionAuto] Action: {action}")

    handler = _ACTION_MAP.get(action)
    if not handler:
        return (
            f"Unknown action: '{action}'. "
            "Available: find_click, find_click_img, describe, type, press, scroll, app_skill."
        )

    try:
        result = handler(params, player, speak) or "Done."
        if speak and action not in ("type", "press", "scroll"):
            speak(result)
        return result
    except Exception as e:
        print(f"[VisionAuto] Error in {action}: {e}")
        return f"Vision {action} failed, sir: {e}"
