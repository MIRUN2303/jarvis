import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import logging
import warnings
warnings.filterwarnings("ignore", message="Setting the shape")
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

import asyncio
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types
from ui import RookiUI
from core.speech_manager import (
    SpeechState, SpeechStateManager,
)
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
try:
    from voice_auth import VoiceAuthenticator
except ImportError:
    class VoiceAuthenticator:
        def __init__(self, *args, **kwargs):
            self.enabled = False
        def verify(self, data):
            return True

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather           import weather as weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.cursor_control    import cursor_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from core.audio_pipeline       import AudioPipeline
from core.keyword_spotter      import KeywordSpotter
from core import google_auth
from actions.music_player     import music_player
from actions.vision_automation import vision_automation
from actions.timer            import timer_control
from actions.weather          import weather as weather_action
from actions.briefing         import briefing
from actions.smart_lists      import smart_lists
from actions.translator       import translator
from dataclasses import dataclass, field
from enum import Enum


class TurnStatus(Enum):
    COMPLETED   = "completed"
    INTERRUPTED = "interrupted"
    IN_FLIGHT   = "in_flight"


@dataclass
class ConversationTurn:
    user_input:      str = ""
    assistant_output: str = ""
    status:          TurnStatus = TurnStatus.IN_FLIGHT
    timestamp:       float = 0.0


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Rooki, a helpful AI assistant named Rooki. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens a locally installed desktop application. "
            "Use for apps like Chrome, Spotify, Discord, Word, Notepad etc. "
            "Do NOT use for YouTube, Netflix, or websites — use youtube_video or browser_control instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the desktop application (e.g. 'Chrome', 'Spotify', 'Notepad')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Gets current weather and forecast for any city. "
            "Use: 'what's the weather in Istanbul?', 'forecast for London'. "
            "Actions: current (get current weather), forecast (get 3-day forecast). "
            "No API key needed — free."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "current | forecast (default: current)"},
                "city":   {"type": "STRING", "description": "City name or leave empty for auto-detect"},
                "days":   {"type": "INTEGER", "description": "Forecast days 1-3"},
            },
            "required": []
        }
    },
    {
        "name": "briefing",
        "description": (
            "Daily briefing: weather + news + calendar in one command. "
            "Actions: morning (full briefing), quick (weather only). "
            "Use: 'good morning', 'give me a briefing', 'quick briefing', "
            "'what's my day look like?', 'morning report'. "
            "Reads news headlines and calendar events automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "morning | quick (default: morning)"},
                "city":   {"type": "STRING", "description": "City for weather (optional)"},
                "news":   {"type": "BOOLEAN", "description": "Include news (default: true)"},
                "calendar": {"type": "BOOLEAN", "description": "Include calendar (default: true)"},
            },
            "required": []
        }
    },
    {
        "name": "smart_lists",
        "description": (
            "Persistent shopping, to-do, and custom lists. "
            "Actions: create, delete, add, remove, done, undone, list, show, clear_done. "
            "Use: 'add milk to shopping list', 'show my TODO list', "
            "'mark item 3 as done', 'what's on my list?', "
            "'create a reading list', 'clear done items from shopping'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "create | delete | add | remove | done | undone | list | show | clear_done"},
                "name":   {"type": "STRING", "description": "List name (default: shopping)"},
                "item":   {"type": "STRING", "description": "Item text to add"},
                "text":   {"type": "STRING", "description": "Item text to find for remove/done/undone"},
                "index":  {"type": "INTEGER", "description": "Item number to remove/done/undone"},
            },
            "required": []
        }
    },
    {
        "name": "translator",
        "description": (
            "Translates text between languages using Google Translate (free). "
            "Actions: translate (default), detect (detect language), languages (list supported). "
            "Use: 'translate hello to Turkish', 'how do you say thank you in Spanish?', "
            "'what language is this?', 'translate this paragraph to French', "
            "'say good morning in German'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "translate | detect | languages (default: translate)"},
                "text":   {"type": "STRING", "description": "Text to translate"},
                "to":     {"type": "STRING", "description": "Target language (default: english)"},
                "from":   {"type": "STRING", "description": "Source language (optional, auto-detected if omitted)"},
            },
            "required": []
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Opens YouTube and plays/search YouTube videos. "
            "Use this for: 'open YouTube', 'play a video', 'search YouTube for...', "
            "'summarize a video', 'get video info', 'show trending'. "
            "This is the ONLY tool for YouTube — do NOT use open_app for YouTube."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "CAPTURES a live snapshot of the user's screen or camera. "
            "MUST be called for ANY visual question: what is on screen, what you see, "
            "look at camera, analyze my screen, is something showing? "
            "Use this to read content from any app (WhatsApp chats, browser, documents). "
            "CHAIN with cursor_control: first screen_process to see where things are, "
            "then cursor_control(screen_click) to click, then screen_process again to read. "
            "Call it MULTIPLE TIMES for changes (e.g. 'what about now?', 'did it work?'). "
            "After capture the image is sent directly to you — describe what you see."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "Available: volume_up, volume_down, mute, toggle_wifi, close (close tab/window), close_app (Alt+F4), close_window/close_tab (Ctrl+W), new_tab, next_tab, prev_tab, fullscreen, minimize, maximize, dark_mode, screenshot, lock_screen, restart, shutdown, type_text, press_key, scroll_up, scroll_down, reload, toggle_wifi, brightness_up, brightness_down, sleep_display, copy, paste, open_settings, file_explorer"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously. "
            "IMPORTANT: To change videos/ pages on the SAME site (e.g. YouTube), use go_to with the new URL "
            "— do NOT use new_tab. Use list_tabs first to see all open tabs before closing a specific one."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | list_tabs | new_tab | close_tab | switch_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll; next | prev for switch_tab"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "index":       {"type": "INTEGER", "description": "Tab index for close_tab (use list_tabs first to see indices)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "Direct computer control: type, click, press keys, scroll, screenshots. "
            "Use for: typing text, pressing keys (Enter, Tab, Escape), "
            "hotkeys (ctrl+c, ctrl+v), scrolling, clicking at coordinates, "
            "or screen_find to locate elements by their visible text name."
            "CHAIN this with screen_process to see what's on screen before/after."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cursor_control",
        "description": (
            "Controls the mouse cursor: move, click, or find & click anything on screen. "
            "Use for ANY cursor/mouse command — especially: "
            "'move cursor to X', 'click there', 'go to top right', 'scroll down', "
            "'click the search button', 'find the login field', 'where is my mouse?' "
            "Named positions: center, top-left, top-right, bottom-left, bottom-right, "
            "top-center, middle-left, middle-right, bottom-center. "
            "MOST IMPORTANTLY: use screen_click when the user describes what to click "
            "by name or text (e.g. 'click the submit button', 'click the search icon', "
            "'click the blue button'). It scans live UI elements on screen (no screenshot), "
            "matches by text/type, and precisely clicks the described element."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "move_to | move_by | click | left_click | right_click | double_click | screen_find | screen_click | scroll | drag | where"},
                "x":           {"type": "INTEGER", "description": "Absolute X coordinate (pixels)"},
                "y":           {"type": "INTEGER", "description": "Absolute Y coordinate (pixels)"},
                "position":    {"type": "STRING",  "description": "Named position: center, top-left, top-right, bottom-left, bottom-right, top-center, middle-left, middle-right, bottom-center"},
                "dx":          {"type": "INTEGER", "description": "Relative horizontal move (pixels, negative=left)"},
                "dy":          {"type": "INTEGER", "description": "Relative vertical move (pixels, negative=up)"},
                "button":      {"type": "STRING",  "description": "left | right (default: left)"},
                "clicks":      {"type": "INTEGER", "description": "Number of clicks (default: 1)"},
                "direction":   {"type": "STRING",  "description": "up | down (for scroll)"},
                "description": {"type": "STRING",  "description": "Natural language description of an on-screen element to find/click (e.g. 'the search button', 'OK button', 'username field')"},
                "x1":        {"type": "INTEGER", "description": "Drag start X"},
                "y1":        {"type": "INTEGER", "description": "Drag start Y"},
                "x2":        {"type": "INTEGER", "description": "Drag end X"},
                "y2":        {"type": "INTEGER", "description": "Drag end Y"},
                "duration":  {"type": "NUMBER",  "description": "Animation duration in seconds (default: 0.3)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_rooki",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Rooki. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "connect_google",
        "description": (
            "Connects ROOKI to your Google account. "
            "Opens a browser for you to sign in to Google. "
            "After connection, ROOKI can read your profile, calendar, Gmail, and contacts. "
            "REQUIRES google_client_id and google_client_secret in config/api_keys.json. "
            "Use this when user says: 'connect Google', 'link my account', 'sign in to Google'."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "get_google_profile",
        "description": (
            "Returns your Google profile info: name, email, locale. "
            "Use when user asks: 'who am I?', 'what's my Google info?', 'tell me about myself'. "
            "Requires Google account to be connected first."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "get_calendar",
        "description": (
            "Reads your upcoming Google Calendar events. "
            "Use when user asks: 'what's on my calendar?', 'what are my events today?', "
            "'what's my schedule?', 'what meetings do I have?' "
            "Requires Google account to be connected first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "max_results": {"type": "INTEGER", "description": "Number of events to fetch (default: 5)"}
            },
            "required": []
        }
    },
    {
        "name": "read_gmail",
        "description": (
            "Reads your recent Gmail messages. "
            "Use when user asks: 'check my email', 'read my emails', "
            "'any new messages?', 'show emails from X' "
            "Requires Google account to be connected first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Optional Gmail search filter (e.g. 'from:john', 'subject:meeting')"},
                "max_results": {"type": "INTEGER", "description": "Number of emails to fetch (default: 5)"}
            },
            "required": []
        }
    },
    {
        "name": "get_contacts",
        "description": (
            "Lists your Google Contacts. "
            "Use when user asks: 'show my contacts', 'who are my contacts?', "
            "'list my saved contacts' "
            "Requires Google account to be connected first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "max_results": {"type": "INTEGER", "description": "Number of contacts to fetch (default: 10)"}
            },
            "required": []
        }
    },
    {
        "name": "music_player",
        "description": (
            "Plays music by searching YouTube and opening in Brave browser (ad-free). "
            "Controls: play, pause, resume, stop, next, previous, volume (0-100), "
            "volume_up, volume_down, now_playing, search, fullscreen, like. "
            "Use for: 'play [song]', 'skip this song', 'next track', 'pause music', "
            "'volume up', 'what's playing?', 'search for [song]'. "
            "For YouTube videos that the user wants to WATCH, use youtube_video tool instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | pause | resume | stop | next | previous | volume | volume_up | volume_down | now_playing | search | fullscreen | like"},
                "query":  {"type": "STRING", "description": "Song name or search query"},
                "level":  {"type": "INTEGER", "description": "Volume level 0-100"},
                "step":   {"type": "INTEGER", "description": "Volume change step (default: 10)"},
                "max_results": {"type": "INTEGER", "description": "Number of search results"},
            },
            "required": []
        }
    },
    {
        "name": "vision_automation",
        "description": (
            "SEES what's on the user's screen and CLICKS buttons like a human. "
            "Uses OCR to find text elements and click them. "
            "Actions: find_click (find text and click it), describe (read all text on screen), "
            "type (type text), press (press a key), scroll, app_skill (get navigation help). "
            "Use for: 'click the skip button', 'find the search bar and type X', "
            "'what's on my screen?', 'scroll down', 'press enter'. "
            "CHAIN with screen_process for complex tasks: first screen_process to see the UI, "
            "then vision_automation(find_click) to interact with it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "find_click | describe | type | press | scroll | app_skill"},
                "label":    {"type": "STRING", "description": "Text label to find and click (for find_click action)"},
                "text":     {"type": "STRING", "description": "Text to type (for type action)"},
                "key":      {"type": "STRING", "description": "Key to press: enter, esc, tab, space, etc."},
                "clicks":   {"type": "INTEGER", "description": "Scroll clicks, negative=down, positive=up (default: -3)"},
                "app":      {"type": "STRING", "description": "App name for app_skill (e.g. 'youtube', 'brave')"},
            },
            "required": []
        }
    },
    {
        "name": "timer_control",
        "description": (
            "Sets timers, alarms, and stopwatches. "
            "Actions: timer (countdown), alarm (specific time), cancel, cancel_all, "
            "list, check, stopwatch (start), lap, stop (stopwatch). "
            "Use for: 'set a 10 minute timer', 'set an alarm for 7 AM', "
            "'show my timers', 'cancel timer', 'start stopwatch', 'record lap'. "
            "When a timer expires, ROOKI will announce it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "timer | alarm | cancel | cancel_all | list | check | stopwatch | lap | stop"},
                "duration": {"type": "STRING", "description": "Duration for timer: '5 minutes', '1 hour 30 min', '30 seconds'"},
                "time":     {"type": "STRING", "description": "Time for alarm: '7:00 AM', '14:30'"},
                "label":    {"type": "STRING", "description": "Optional label for the timer/alarm"},
                "id":       {"type": "INTEGER", "description": "Timer ID for cancel/check"},
            },
            "required": []
        }
    },
]

# --- Plugin system ---


class _SessionReconnect(BaseException):
    """Raised inside receive loop to trigger a clean session reconnect."""
    pass


class RookiLive:

    def __init__(self, ui: RookiUI):
        self.ui             = ui
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._vad_noise_floor      = None   # minimum amplified RMS observed (for HUD meter)
        self._vad_voice_hits       = 0       # consecutive voice frame counter (hysteresis)
        self._last_interrupt_time  = 0.0
        self._last_speech_end      = 0.0     # Cooldown tracker for room echo
        self._interrupt_rms_cd     = 0.0     # RMS interrupt cooldown (monotonic timestamp)
        self._last_bargein         = 0.0     # Barge-in VAD cooldown timestamp
        self._voice_auth           = VoiceAuthenticator(threshold=-999)

        self.speech_state = SpeechStateManager(on_state_change=self._on_speech_state_change)
        self._interrupted          = False   # True while draining audio after user interrupt
        self._speaking_since       = 0.0
        self._is_speaking          = False
        self._speaking_lock        = threading.Lock()
        self._out_buf: list[str] = []   # current turn's assistant output fragments (accessed by interrupt())
        self._in_buf:  list[str] = []   # current turn's user input fragments

        self._keyword_spotter = KeywordSpotter()
        self._keyword_spotter.set_callback(self.interrupt)
        self._keyword_spotter.set_partial_callback(self._on_partial_transcript)
        self._keyword_spotter.start()  # runs continuously for live STT
        self._audio_pipeline = AudioPipeline(sample_rate=SEND_SAMPLE_RATE)

        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance

        # ── Conversation history (interruption-aware) ────────────────
        self._chat_history: list[ConversationTurn] = []
        self._current_turn = ConversationTurn(timestamp=time.monotonic())

        # ── Session freshness — reconnect every N turns to prevent server-side degradation ──
        self._turns_since_reconnect = 0
        self._max_turns_per_session = 5  # reconnect every 5 turns to prevent slowdown

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _on_speech_state_change(self, old: SpeechState, new: SpeechState):
        if new == SpeechState.SPEAKING:
            self.ui.set_state("SPEAKING")
        elif new == SpeechState.LISTENING:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        elif new == SpeechState.INTERRUPTED:
            self.ui.set_state("LISTENING")
        elif new == SpeechState.THINKING:
            self.ui.set_state("THINKING")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was = self._is_speaking
            self._is_speaking = value
        if value:
            if not was:
                # First frame of speech — start the timer
                self._speaking_since = time.monotonic()
            self.speech_state.set(SpeechState.SPEAKING, "tts_start")
        else:
            self._last_speech_end = time.monotonic()
            self.speech_state.set(SpeechState.LISTENING, "tts_end")

    def interrupt(self) -> None:
        """Stop ROOKI mid-speech: drain queued audio and save interrupted turn."""
        # Only interrupt while ROOKI is actually speaking — prevents infinite cycle
        if not self.speech_state.is_speaking():
            return
        # ── Save partial output as INTERRUPTED turn ──
        partial_out = " ".join(self._out_buf).strip()
        if partial_out:
            self._current_turn.assistant_output = partial_out
            self._current_turn.status = TurnStatus.INTERRUPTED
            self._current_turn.timestamp = time.monotonic()
            self._chat_history.append(self._current_turn)
            # Keep history bounded
            if len(self._chat_history) > 20:
                self._chat_history = self._chat_history[-20:]
            print(f"[History] Interrupted turn saved ({len(partial_out)} chars)")
        self._out_buf.clear()
        self._in_buf.clear()

        self._interrupted = True
        self.speech_state.set(SpeechState.INTERRUPTED, "interrupt")
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[ROOKI] Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        self.speech_state.set(SpeechState.LISTENING, "ready")
        if self._turn_done_event:
            self._turn_done_event.clear()

        # Start a fresh turn for what the user says next
        self._current_turn = ConversationTurn(timestamp=time.monotonic())
        self.ui.write_log("SYS: Interrupted — listening...")

    def _on_partial_transcript(self, text: str):
        """Called from VOSK thread with partial transcript — update UI live."""
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(
                    lambda: self.ui.set_live_transcript(text)
                )
        except Exception:
            pass

    async def _finish_listening(self):
        """Send turn_complete to Gemini when end-of-speech detected."""
        now = time.monotonic()
        if now - getattr(self, '_last_eos_time', 0.0) < 1.5:
            return
        self._last_eos_time = now
        if not self.session:
            return

        # ── Drain the out_queue before turn_complete ──
        # Ensures all audio chunks reach Gemini before signaling end-of-input
        if self.out_queue:
            for _ in range(40):  # max ~2s wait
                if self.out_queue.empty():
                    break
                await asyncio.sleep(0.05)

        try:
            await self.session.send_client_content(
                turns={"parts": [{"text": ""}]},
                turn_complete=True
            )
        except Exception as e:
            print(f"[ROOKI] turn_complete error: {e}")
            return

        self.ui.write_log("SYS: Speech ended — processing...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _format_history_for_prompt(self) -> str:
        """Format recent conversation history, highlighting interrupted turns."""
        if not self._chat_history:
            return ""
        lines = ["[RECENT CONVERSATION HISTORY]"]
        for turn in self._chat_history[-6:]:  # last 6 turns
            tag = "COMPLETED" if turn.status == TurnStatus.COMPLETED else "INTERRUPTED"
            if turn.user_input:
                lines.append(f"User ({tag}): {turn.user_input}")
            if turn.assistant_output:
                lines.append(f"ROOKI ({tag}): {turn.assistant_output}")
        lines.append("[END HISTORY]")
        return "\n".join(lines)

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[ROOKI] Tool: {name}  {args}")
        self.speech_state.set(SpeechState.THINKING, f"tool:{name}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Weather delivered."

            elif name == "briefing":
                r = await loop.run_in_executor(None, lambda: briefing(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Briefing delivered."

            elif name == "smart_lists":
                r = await loop.run_in_executor(None, lambda: smart_lists(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "translator":
                r = await loop.run_in_executor(None, lambda: translator(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "music_player":
                r = await loop.run_in_executor(None, lambda: music_player(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "vision_automation":
                r = await loop.run_in_executor(None, lambda: vision_automation(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "timer_control":
                r = await loop.run_in_executor(None, lambda: timer_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE natural sentence in the user's language "
                        f"(e.g. 'Looking at your {_stall} now, sir' / "
                        f"'{'Kameraya' if _stall == 'camera' else 'Ekrana'} bakıyorum efendim'). "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "cursor_control":
                r = await loop.run_in_executor(None, lambda: cursor_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_rooki":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            # ── Google Account Integration ─────────────────────────
            elif name == "connect_google":
                result = await loop.run_in_executor(None, google_auth.connect)

            elif name == "get_google_profile":
                if not google_auth.is_connected():
                    result = "Google account not connected, sir. Say 'connect my Google account' first."
                else:
                    result = await loop.run_in_executor(None, google_auth.get_profile)

            elif name == "get_calendar":
                if not google_auth.is_connected():
                    result = "Google account not connected, sir. Say 'connect my Google account' first."
                else:
                    max_r = args.get("max_results", 5)
                    result = await loop.run_in_executor(None, lambda: google_auth.get_calendar_events(max_r))

            elif name == "read_gmail":
                if not google_auth.is_connected():
                    result = "Google account not connected, sir. Say 'connect my Google account' first."
                else:
                    q = args.get("query", "")
                    max_r = args.get("max_results", 5)
                    result = await loop.run_in_executor(None, lambda: google_auth.read_emails(q, max_r))

            elif name == "get_contacts":
                if not google_auth.is_connected():
                    result = "Google account not connected, sir. Say 'connect my Google account' first."
                else:
                    max_r = args.get("max_results", 10)
                    result = await loop.run_in_executor(None, lambda: google_auth.search_contacts("", max_r))

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[ROOKI] Response: {name} -> {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            try:
                await self.session.send_realtime_input(media=msg)
            except Exception as e:
                print(f"[ROOKI] send_realtime error: {e}")
                # Re-queue so it's not lost on reconnect
                if "RESET" in str(e).upper():
                    return  # session dead, let reconnect handle it

    async def _listen_audio(self):
        print("[ROOKI] Mic started")
        loop = asyncio.get_event_loop()

        _voice_active = [False]  # True while user is speaking
        _auth = {
            "status": None,
            "buffer": [],
            "last_voice_time": 0.0
        }

        def _set_level(rms, noise_floor, threshold, voice):
            try:
                self.ui._win.hud.set_level(rms, noise_floor, threshold, voice)
            except Exception:
                pass

        def _safe_put(q, item):
            try:
                q.put_nowait(item)
            except Exception:
                try:
                    q.get_nowait()
                    q.put_nowait(item)
                except Exception:
                    pass

        def callback(indata, frames, time_info, status):
            try:
                with self._speaking_lock:
                    rooki_speaking = self._is_speaking

                in_cooldown = not rooki_speaking and (time.monotonic() - getattr(self, '_last_speech_end', 0.0) < 1.2)

                # --- Audio Pipeline (AEC + noise suppression + adaptive VAD) ---
                pipe_result = self._audio_pipeline.process_frame(indata.copy())
                processed = pipe_result["processed"]
                is_voice = pipe_result["is_voice"]
                speech_ended = pipe_result["speech_ended"]
                rms = pipe_result["rms"]
                db  = pipe_result["db"]

                # Track noise floor for HUD level meter (convert float RMS to int16 scale)
                rms_int16 = rms * 32768.0
                if not rooki_speaking and not in_cooldown:
                    if self._vad_noise_floor is None or rms_int16 < self._vad_noise_floor:
                        self._vad_noise_floor = rms_int16

                nf_db = max(-100.0, 20.0 * np.log10((self._vad_noise_floor or 1.0) / 32767.0))
                _set_level(rms_int16, self._vad_noise_floor or 0.0, nf_db + 10.0, is_voice)

                # --- Voice state: pipeline is the sole source of truth ---
                _voice_active[0] = pipe_result["voice_active"]
                send_audio = _voice_active[0]

                # --- VOICE AUTHENTICATION ---
                if self._voice_auth.enabled:
                    if is_voice and _auth["status"] is None:
                        _auth["status"] = "BUFFERING"
                        _auth["buffer"] = []

                    if _auth["status"] == "BUFFERING":
                        _auth["buffer"].append(processed.copy())
                        total_samples = sum(b.shape[0] for b in _auth["buffer"])
                        if total_samples >= 12800:
                            _auth["status"] = "VERIFYING"
                            def _verify_task(audio_concat):
                                is_user = self._voice_auth.verify(audio_concat)
                                def _on_result():
                                    if is_user:
                                        _auth["status"] = True
                                        if not self.speech_state.is_speaking() and not self.ui.muted and not self._phone_active:
                                            for b in _auth["buffer"]:
                                                _safe_put(self.out_queue, {"data": b.tobytes(), "mime_type": "audio/pcm"})
                                    else:
                                        _auth["status"] = False
                                    _auth["buffer"].clear()
                                loop.call_soon_threadsafe(_on_result)
                            audio_concat = np.concatenate(_auth["buffer"], axis=0)
                            threading.Thread(target=_verify_task, args=(audio_concat,), daemon=True).start()

                    elif _auth["status"] == "VERIFYING":
                        _auth["buffer"].append(processed.copy())

                    _auth["last_voice_time"] = time.monotonic() if is_voice else _auth["last_voice_time"]
                else:
                    _auth["status"] = True

                if not is_voice and time.monotonic() - _auth["last_voice_time"] > 3.0:
                    _auth["status"] = None
                    _auth["buffer"].clear()

                # --- SPEAKING MODE: detect barge-in via VAD + VOSK keywords ---
                # --- VOSK: feed every frame for live transcription + keyword spotting ---
                self._keyword_spotter.feed(processed)

                if rooki_speaking:
                    # NOTE: VAD barge-in removed — echo from speakers trips is_voice even
                    # with cooldown, causing self-interrupt loops. Use VOSK keyword barge-in
                    # only while speaking (handled below via self._keyword_spotter).

                    # Safety timeout — stop speaking if queue empty for 15s
                    if self.audio_in_queue is not None and self.audio_in_queue.empty():
                        speak_elapsed = time.monotonic() - self._speaking_since
                        if speak_elapsed > 15.0:
                            self._speaking_since = time.monotonic()
                            loop.call_soon_threadsafe(self.set_speaking, False)
                    return  # don't send audio to Gemini while speaking (avoids echo)
                elif in_cooldown:
                    return
                else:
                    self._speaking_since = time.monotonic()

                # --- Send processed audio to Gemini (only while voice is active) ---
                if send_audio and not self.ui.muted and not self._phone_active:
                    if self._voice_auth.enabled:
                        # Auth gate: only send if verified OR interrupted (barge-in bypass)
                        if _auth["status"] is True or self._interrupted:
                            data = processed.tobytes()
                            msg = {"data": data, "mime_type": "audio/pcm"}
                            loop.call_soon_threadsafe(_safe_put, self.out_queue, msg)
                    else:
                        # Auth disabled: send all user audio freely
                        data = processed.tobytes()
                        msg = {"data": data, "mime_type": "audio/pcm"}
                        loop.call_soon_threadsafe(_safe_put, self.out_queue, msg)

                # --- End-of-speech: signal Gemini turn complete ---
                if speech_ended:
                    self._audio_pipeline.reset()
                    asyncio.run_coroutine_threadsafe(self._finish_listening(), loop)

            except Exception as e:
                print(f"[Mic] callback error: {e}")
                import traceback
                traceback.print_exc()

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[ROOKI] Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[ROOKI] Mic error: {e}")
            raise

    async def _receive_audio(self):
        print("[ROOKI] Recv started")
        self._out_buf, self._in_buf = [], []
        # Fresh conversation history on each session
        self._chat_history.clear()
        self._current_turn = ConversationTurn(timestamp=time.monotonic())

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            self.set_speaking(True)  # block mic immediately
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (self._out_buf[-1] if self._out_buf else ""):
                                self._out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                self._in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, the interrupt
                            # handler already saved the partial turn. Just clear the flag and buffers.
                            if self._interrupted:
                                self._interrupted = False
                                self._in_buf  = []
                                self._out_buf = []
                                continue

                            # ── Save COMPLETED turn to history ──
                            full_in = " ".join(self._in_buf).strip()
                            full_out = " ".join(self._out_buf).strip()
                            self._current_turn.user_input = full_in
                            self._current_turn.assistant_output = full_out
                            self._current_turn.status = TurnStatus.COMPLETED
                            self._current_turn.timestamp = time.monotonic()
                            self._chat_history.append(self._current_turn)
                            if len(self._chat_history) > 20:
                                self._chat_history = self._chat_history[-20:]
                            self._current_turn = ConversationTurn(timestamp=time.monotonic())

                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            self._in_buf = []

                            if full_out:
                                self.ui.write_log(f"Rooki: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "rooki",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            self._out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] Send {len(img_b):,} bytes (angle={angle}) to main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until ROOKI finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                            # ── Session freshness: reconnect after N turns ──
                            self._turns_since_reconnect += 1
                            if self._turns_since_reconnect >= self._max_turns_per_session:
                                print(f"[ROOKI] Reconnecting after {self._turns_since_reconnect} turns")
                                self._turns_since_reconnect = 0
                                raise _SessionReconnect()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ROOKI] Call: {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except _SessionReconnect:
            raise  # clean reconnect signal — propagate to outer loop
        except Exception as e:
            print(f"[ROOKI] Recv error: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[ROOKI] Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            latency="low"
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                    # Feed loopback audio for echo cancellation
                    try:
                        ref = np.frombuffer(chunk, dtype=np.int16)
                        self._audio_pipeline.add_loopback(ref)
                    except Exception:
                        pass
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[ROOKI] Play error: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing for instant perceived response:
          Phase 1 — immediate greeting (no tools, no fetch) → Rooki speaks in <2s
          Phase 2 — news fetched in background, injected after greeting finishes
        """
        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── memory ───────────────────────────────────────────────────────────
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")

        from datetime import datetime
        time_str = datetime.now().strftime("%H:%M")

        # ── Phase 1: instant greeting — one simple sentence ──────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, and say you are fetching today's news headlines now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fetch news in background, deliver after greeting plays ───
        async def _guarded_news():
            try:
                await self._briefing_news_phase(lang)
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing news phase failed: {e}")
        asyncio.create_task(_guarded_news())

    async def _briefing_news_phase(self, lang: str) -> None:
        """
        Sends phase-2 (news) to Gemini ~1.5 s after phase-1 is dispatched so
        Gemini starts working on it while phase-1 audio is still playing.
        """
        lang_str = f" Respond in {lang}." if lang else ""

        # 1.5 s is enough for Gemini to finish generating phase-1 audio on its
        # side (turn_complete) while the greeting is still being played locally.
        await asyncio.sleep(1.5)

        if not self.session:
            return

        p2 = (
            "[BRIEFING] Call web_search with mode='news' and query='top world news today' "
            "to find actual recent news articles with real event headlines (not just website names). "
            "After the search, say ONE specific news event from the results in one sentence, "
            f"then say the full list is displayed on screen.{lang_str}"
        )

        await self.session.send_client_content(
            turns={"parts": [{"text": p2}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 2 (news) sent.")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            if self.speech_state.is_speaking():
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            if not self.speech_state.is_speaking() and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[ROOKI] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False
                    self._audio_pipeline.reset()
                    self._audio_pipeline.flush_loopback()
                    self.speech_state.set(SpeechState.LISTENING, "connected")

                    print("[ROOKI] Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: Rooki online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch
                    if not self._briefing_sent:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).

                # Clean reconnect — no error logging, no backoff
                if isinstance(e, _SessionReconnect) or \
                   (hasattr(e, "exceptions") and any(isinstance(x, _SessionReconnect) for x in e.exceptions)):
                    print("[ROOKI] Reconnecting (fresh session)...")
                    self.ui.write_log("SYS: Reconnecting for performance...")
                    continue

                err_str = str(e)
                print(f"[ROOKI] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[ROOKI] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[ROOKI] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    ui = RookiUI("face.png")

    def runner():
        ui.wait_for_api_key()
        rooki = RookiLive(ui)
        try:
            asyncio.run(rooki.run())
        except KeyboardInterrupt:
            print("\nShutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()