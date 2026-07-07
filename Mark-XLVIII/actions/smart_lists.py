#smart_lists.py — Persistent shopping, to-do, and custom lists (JSON-backed)
import json
import os
import time
from pathlib import Path
from datetime import datetime

_LISTS_FILE = Path(__file__).resolve().parent.parent / "data" / "smart_lists.json"


def _load() -> dict:
    """Load all lists from JSON file."""
    try:
        _LISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _LISTS_FILE.exists():
            return json.loads(_LISTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Lists] Load error: {e}")
    return {}


def _save(data: dict):
    """Save all lists to JSON file."""
    try:
        _LISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LISTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Lists] Save error: {e}")


# ── Public API ──────────────────────────────────────────────────────────

def create_list(name: str) -> str:
    """Create a new empty list."""
    data = _load()
    name = name.strip().lower()
    if name in data:
        return f"List '{name}' already exists, sir."
    data[name] = {"items": [], "created": datetime.now().isoformat()}
    _save(data)
    return f"Created '{name}' list, sir."


def delete_list(name: str) -> str:
    """Delete a list entirely."""
    data = _load()
    name = name.strip().lower()
    if name not in data:
        return f"No list named '{name}', sir."
    del data[name]
    _save(data)
    return f"Deleted '{name}' list, sir."


def add_item(list_name: str, item: str) -> str:
    """Add an item to a list."""
    data = _load()
    key = list_name.strip().lower()
    
    # Auto-create list if it doesn't exist
    if key not in data:
        data[key] = {"items": [], "created": datetime.now().isoformat()}
    
    data[key]["items"].append({"text": item.strip(), "done": False, "added": datetime.now().isoformat()})
    _save(data)
    return f"Added '{item}' to {list_name}, sir."


def remove_item(list_name: str, index: int = None, text: str = None) -> str:
    """Remove an item by index or text match."""
    data = _load()
    key = list_name.strip().lower()
    if key not in data:
        return f"No list named '{list_name}', sir."
    
    items = data[key]["items"]
    if index is not None and 0 <= index < len(items):
        removed = items.pop(index)
        _save(data)
        return f"Removed '{removed['text']}' from {list_name}, sir."
    
    if text:
        for i, item in enumerate(items):
            if text.lower() in item["text"].lower():
                removed = items.pop(i)
                _save(data)
                return f"Removed '{removed['text']}' from {list_name}, sir."
    
    return f"Item not found in {list_name}, sir."


def mark_done(list_name: str, index: int = None, text: str = None) -> str:
    """Mark an item as done."""
    data = _load()
    key = list_name.strip().lower()
    if key not in data:
        return f"No list named '{list_name}', sir."
    
    items = data[key]["items"]
    if index is not None and 0 <= index < len(items):
        items[index]["done"] = True
        _save(data)
        return f"Marked '{items[index]['text']}' as done, sir."
    
    if text:
        for item in items:
            if text.lower() in item["text"].lower():
                item["done"] = True
                _save(data)
                return f"Marked '{item['text']}' as done, sir."
    
    return f"Item not found in {list_name}, sir."


def unmark_done(list_name: str, index: int = None, text: str = None) -> str:
    """Mark an item as not done."""
    data = _load()
    key = list_name.strip().lower()
    if key not in data:
        return f"No list '{list_name}', sir."
    items = data[key]["items"]
    if index is not None and 0 <= index < len(items):
        items[index]["done"] = False
        _save(data)
        return f"Unmarked '{items[index]['text']}', sir."
    if text:
        for item in items:
            if text.lower() in item["text"].lower():
                item["done"] = False
                _save(data)
                return f"Unmarked '{item['text']}', sir."
    return f"Item not found, sir."


def list_lists() -> str:
    """Show all list names."""
    data = _load()
    if not data:
        return "No lists yet, sir. Say 'create shopping list' to start one."
    lines = ["Your lists:"]
    for name, content in data.items():
        total = len(content["items"])
        done = sum(1 for i in content["items"] if i["done"])
        lines.append(f"  {name}: {done}/{total} done")
    return "\n".join(lines)


def show_list(name: str) -> str:
    """Show all items in a list."""
    data = _load()
    key = name.strip().lower()
    if key not in data:
        return f"No list named '{name}', sir."
    
    items = data[key]["items"]
    if not items:
        return f"'{name}' list is empty, sir."
    
    lines = [f"{name} list:"]
    for i, item in enumerate(items, 1):
        status = "[x]" if item["done"] else "[ ]"
        lines.append(f"  {i}. {status} {item['text']}")
    return "\n".join(lines)


def clear_done(list_name: str) -> str:
    """Remove all done items from a list."""
    data = _load()
    key = list_name.strip().lower()
    if key not in data:
        return f"No list '{list_name}', sir."
    before = len(data[key]["items"])
    data[key]["items"] = [i for i in data[key]["items"] if not i["done"]]
    removed = before - len(data[key]["items"])
    _save(data)
    return f"Cleared {removed} done item{'s' if removed!=1 else ''} from {list_name}, sir."


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "create":    lambda p, pl, sp: create_list(p.get("name", "")),
    "delete":    lambda p, pl, sp: delete_list(p.get("name", "")),
    "add":       lambda p, pl, sp: add_item(p.get("name", "shopping"), p.get("item", "")),
    "remove":    lambda p, pl, sp: remove_item(p.get("name", "shopping"), p.get("index"), p.get("text")),
    "done":      lambda p, pl, sp: mark_done(p.get("name", "shopping"), p.get("index"), p.get("text")),
    "undone":    lambda p, pl, sp: unmark_done(p.get("name", "shopping"), p.get("index"), p.get("text")),
    "list":      lambda p, pl, sp: list_lists(),
    "show":      lambda p, pl, sp: show_list(p.get("name", "shopping")),
    "clear_done": lambda p, pl, sp: clear_done(p.get("name", "shopping")),
}

def smart_lists(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "show").lower().strip()
    handler = _ACTION_MAP.get(action)
    if not handler:
        return (
            f"Unknown action: '{action}'. "
            "Available: create, delete, add, remove, done, undone, list, show, clear_done."
        )
    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        return f"List {action} failed, sir: {e}"
