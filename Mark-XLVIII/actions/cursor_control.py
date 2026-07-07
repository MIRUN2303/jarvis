import re
import time
import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.05

HAS_UIA = False
try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    pass

_POSITIONS = {
    "top-left":       lambda w, h: (0, 0),
    "top-center":     lambda w, h: (w // 2, 0),
    "top-right":      lambda w, h: (w - 1, 0),
    "middle-left":    lambda w, h: (0, h // 2),
    "center":         lambda w, h: (w // 2, h // 2),
    "middle-right":   lambda w, h: (w - 1, h // 2),
    "bottom-left":    lambda w, h: (0, h - 1),
    "bottom-center":  lambda w, h: (w // 2, h - 1),
    "bottom-right":   lambda w, h: (w - 1, h - 1),
}

_CONTROL_TYPES = {
    50000: "Window", 50001: "Pane", 50002: "Button", 50003: "Edit",
    50004: "Text", 50005: "Image", 50008: "ComboBox", 50010: "CheckBox",
    50011: "RadioButton", 50012: "Hyperlink", 50020: "ListItem",
    50021: "List", 50022: "Tab", 50033: "ToolBar", 50034: "Menu",
    50035: "MenuItem", 50041: "Slider", 50043: "ProgressBar",
    50044: "ScrollBar", 50051: "Tree", 50052: "TreeItem",
}

def _named_position(name: str) -> tuple[int, int] | None:
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key in _POSITIONS:
        w, h = pyautogui.size()
        return _POSITIONS[key](w, h)
    if re.match(r"^\d+[.,]?\d*\s*[x,]\s*\d+", name):
        parts = re.split(r"\s*[x,]\s*", name)
        return int(parts[0]), int(parts[1])
    return None

def _move(x: int | None = None, y: int | None = None, position: str | None = None, duration: float = 0.3) -> str:
    if position:
        coords = _named_position(position)
        if coords:
            x, y = coords
        else:
            return f"Unknown position: '{position}'"
    if x is None or y is None:
        return "Specify x,y coordinates or a named position."
    x, y = int(x), int(y)
    w, h = pyautogui.size()
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    pyautogui.moveTo(x, y, duration=duration)
    return f"Moved cursor to ({x}, {y})"

def _move_relative(dx: int, dy: int, duration: float = 0.2) -> str:
    cx, cy = pyautogui.position()
    tx = cx + dx
    ty = cy + dy
    w, h = pyautogui.size()
    tx = max(0, min(tx, w - 1))
    ty = max(0, min(ty, h - 1))
    pyautogui.moveRel(tx - cx, ty - cy, duration=duration)
    return f"Moved cursor relative: ({cx},{cy}) \u2192 ({tx},{ty}) [\u0394 {dx:+d}, {dy:+d}]"

def _click(x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> str:
    if x is not None and y is not None:
        pyautogui.click(int(x), int(y), button=button, clicks=clicks)
        label = "Double-clicked" if clicks == 2 else "Clicked"
        return f"{label} ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    label = "Double-clicked" if clicks == 2 else "Clicked"
    return f"{label} at current position [{button}]"

def _scroll(clicks: int, x: int | None = None, y: int | None = None) -> str:
    if x is not None and y is not None:
        pyautogui.scroll(clicks, x=int(x), y=int(y))
    else:
        pyautogui.scroll(clicks)
    direction = "up" if clicks > 0 else "down"
    return f"Scrolled {direction} \u00d7{abs(clicks)}"

def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    pyautogui.moveTo(int(x1), int(y1), duration=0.2)
    pyautogui.dragTo(int(x2), int(y2), duration=duration, button="left")
    return f"Dragged ({x1},{y1}) \u2192 ({x2},{y2})"

def _get_position() -> str:
    x, y = pyautogui.position()
    w, h = pyautogui.size()
    return f"Cursor at ({x}, {y}) on {w}x{h} screen"


def _uia_collect(control, depth: int = 0, max_depth: int = 10) -> list[dict]:
    elements = []
    if depth > max_depth:
        return elements

    for child in control.GetChildren():
        try:
            if child.IsOffscreen:
                continue
            rect = child.BoundingRectangle
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                elements.extend(_uia_collect(child, depth + 1, max_depth))
                continue

            name = (child.Name or "").strip()
            ctype = _CONTROL_TYPES.get(child.ControlType, f"Control_{child.ControlType}")

            if name and child.IsEnabled:
                elements.append({
                    "name": name,
                    "type": ctype,
                    "left": rect.left, "top": rect.top,
                    "right": rect.right, "bottom": rect.bottom,
                })

            elements.extend(_uia_collect(child, depth + 1, max_depth))
        except Exception:
            continue

    return elements


def _uia_read_screen() -> list[dict]:
    if not HAS_UIA:
        return []
    try:
        root = auto.GetRootControl()
        fg = auto.GetForegroundControl()
        fg_children = _uia_collect(fg) if fg else []
        all_children = _uia_collect(root)
        seen = set()
        merged = []
        for e in fg_children + all_children:
            key = (e["left"], e["top"], e["right"], e["bottom"], e["name"])
            if key not in seen:
                seen.add(key)
                merged.append(e)
        return merged[:200]
    except Exception as e:
        print(f"[CursorControl] UIA read failed: {e}")
    return []


def _uia_find(description: str) -> tuple[int, int, str] | None:
    elements = _uia_read_screen()
    if not elements:
        return None

    lines = []
    for i, e in enumerate(elements):
        cx = (e["left"] + e["right"]) // 2
        cy = (e["top"] + e["bottom"]) // 2
        lines.append(f"[{i}] {e['type']} \"{e['name']}\" at ({cx},{cy})")

    screen_text = "\n".join(lines[:150])
    prompt = (
        f"Here is the current state of the user's screen (all visible UI elements):\n"
        f"{screen_text}\n\n"
        f"The user wants to click on: \"{description}\"\n"
        f"Reply with ONLY the index number of the matching element. If nothing matches, reply: NOT_FOUND"
    )

    from core.openrouter_client import ask_llm
    text = ask_llm(prompt, max_tokens=32) or ""
    text = text.strip()
    if "NOT_FOUND" in text.upper():
        return None

    match = re.search(r"\[?(\d+)\]?", text)
    if match:
        idx = int(match.group(1))
        if 0 <= idx < len(elements):
            e = elements[idx]
            cx = (e["left"] + e["right"]) // 2
            cy = (e["top"] + e["bottom"]) // 2
            return cx, cy, e["name"]

    return None


def screen_find(description: str) -> tuple[int, int] | None:
    if HAS_UIA:
        result = _uia_find(description)
        if result:
            return result[0], result[1]
        return None

    try:
        import io
        from core.openrouter_client import ask_llm_multimodal
        w, h = pyautogui.size()
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        prompt = (
            f"This is a screenshot of a {w}x{h} pixel screen. "
            f"Locate the UI element described as: '{description}'. "
            f"Reply with ONLY the center coordinates as: x,y "
            f"If the element is not visible, reply: NOT_FOUND"
        )

        text = ask_llm_multimodal(prompt, images=[image_bytes], max_tokens=128)
        text = (text or "").strip()
        if "NOT_FOUND" in text.upper():
            return None

        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))

    except Exception as e:
        print(f"[CursorControl] screen_find (screenshot fallback) failed: {e}")

    return None


def cursor_control(parameters: dict, response=None, player=None) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified."

    if player:
        player.write_log(f"[Cursor] {action}")

    print(f"[CursorControl] >> {action}  {params}")

    try:
        if action == "move_to":
            return _move(
                x=params.get("x"),
                y=params.get("y"),
                position=params.get("position"),
                duration=float(params.get("duration", 0.3)),
            )

        if action == "move_by":
            return _move_relative(
                dx=int(params.get("dx", 0)),
                dy=int(params.get("dy", 0)),
            )

        if action in ("click", "left_click"):
            return _click(
                x=params.get("x"),
                y=params.get("y"),
                button="left",
                clicks=params.get("clicks", 1),
            )

        if action == "right_click":
            return _click(
                x=params.get("x"),
                y=params.get("y"),
                button="right",
            )

        if action == "double_click":
            return _click(
                x=params.get("x"),
                y=params.get("y"),
                button="left",
                clicks=2,
            )

        if action == "scroll":
            clicks = int(params.get("clicks", 3))
            direction = params.get("direction", "down").lower()
            if direction in ("up", "left"):
                clicks = abs(clicks)
            else:
                clicks = -abs(clicks)
            return _scroll(
                clicks=clicks,
                x=params.get("x"),
                y=params.get("y"),
            )

        if action == "drag":
            return _drag(
                x1=int(params.get("x1", 0)),
                y1=int(params.get("y1", 0)),
                x2=int(params.get("x2", 0)),
                y2=int(params.get("y2", 0)),
            )

        if action == "screen_find":
            coords = screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            desc = params.get("description", "")
            button = params.get("button", "left")

            result = _uia_find(desc) if HAS_UIA else None
            if result:
                cx, cy, name = result
                time.sleep(0.2)
                pyautogui.click(cx, cy, button=button)
                return f"Clicked \"{name}\" at ({cx}, {cy})"

            coords = screen_find(desc)
            if coords:
                time.sleep(0.2)
                pyautogui.click(coords[0], coords[1], button=button)
                return f"Clicked '{desc}' at ({coords[0]}, {coords[1]})"
            return f"Element not found on screen: '{desc}'"

        if action == "where":
            return _get_position()

        return f"Unknown cursor action: '{action}'"

    except Exception as e:
        print(f"[CursorControl] ❌ {action}: {e}")
        return f"cursor_control '{action}' failed: {e}"
