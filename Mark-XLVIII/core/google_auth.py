"""
Google Account Integration — OAuth 2.0 for ROOKI.

Allows ROOKI to access your Google profile, calendar, Gmail, and contacts.

SETUP:
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Create a new OAuth 2.0 Client ID (Desktop App type)
  3. Add the client_id and client_secret to config/api_keys.json:
     {
         "google_client_id":     "...",
         "google_client_secret": "..."
     }
  4. Say "connect my Google account" — ROOKI will open a browser for you to sign in.
"""
import os
import json
import sys
import time
import pickle
import threading
import webbrowser
from pathlib import Path
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# ── Scopes (what ROOKI can access) ────────────────────────────────────
_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
]

# ── Paths ─────────────────────────────────────────────────────────────
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR     = _get_base_dir()
CONFIG_FILE  = BASE_DIR / "config" / "api_keys.json"
TOKEN_FILE   = BASE_DIR / "config" / "google_token.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _has_client_credentials() -> bool:
    cfg = _load_config()
    return bool(cfg.get("google_client_id") and cfg.get("google_client_secret"))


def _get_client_config() -> dict:
    cfg = _load_config()
    return {
        "installed": {
            "client_id":     cfg["google_client_id"],
            "client_secret": cfg["google_client_secret"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080"],
        }
    }


# ── Token management ──────────────────────────────────────────────────

def _load_token() -> Optional[Credentials]:
    if not TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), _SCOPES)
        return creds
    except Exception:
        return None


def _save_token(creds: Credentials) -> None:
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"[GoogleAuth] Token saved to {TOKEN_FILE}")


def _refresh_token(creds: Credentials) -> Optional[Credentials]:
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception as e:
            print(f"[GoogleAuth] Token refresh failed: {e}")
            return None
    return creds


# ── OAuth Flow ────────────────────────────────────────────────────────

def _start_oauth_flow() -> Optional[Credentials]:
    """Run the OAuth 2.0 desktop flow — opens browser for user to sign in."""
    if not _has_client_credentials():
        raise RuntimeError(
            "Google OAuth not configured. Add 'google_client_id' and "
            "'google_client_secret' to config/api_keys.json"
        )

    client_config = _get_client_config()
    flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)

    # Use local server for redirect
    creds = flow.run_local_server(
        port=8080,
        host="localhost",
        open_browser=True,
    )
    _save_token(creds)
    return creds


# ── Public API ────────────────────────────────────────────────────────

def get_credentials() -> Optional[Credentials]:
    """Get valid credentials — tries token, refresh, or (if needed) new OAuth."""
    creds = _load_token()
    if creds and creds.valid:
        return creds
    if creds and creds.expired:
        creds = _refresh_token(creds)
        if creds and creds.valid:
            return creds
    # Token missing or can't refresh
    return None


def is_connected() -> bool:
    """Check if Google account is connected with valid credentials."""
    return get_credentials() is not None


def connect() -> str:
    """Initiate full OAuth flow — returns user info on success."""
    creds = _start_oauth_flow()
    if not creds:
        return "Google account connection failed, sir."

    # Fetch profile info
    try:
        import requests
        info_resp = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        if info_resp.status_code == 200:
            info = info_resp.json()
            name  = info.get("name", "Unknown")
            email = info.get("email", "Unknown")
            print(f"[GoogleAuth] Connected as {name} <{email}>")
            return (
                f"Successfully connected to Google as {name} ({email}), sir. "
                f"I can now access your calendar, read your Gmail when asked, "
                f"and look up your contacts."
            )
    except Exception as e:
        print(f"[GoogleAuth] Profile fetch error: {e}")

    return "Google account connected, sir. But I couldn't fetch your profile."


# ── Google API Service Builders ───────────────────────────────────────

def _build_service(api_name: str, api_version: str):
    from googleapiclient.discovery import build
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Google account not connected. Say 'connect my Google account' first.")
    return build(api_name, api_version, credentials=creds)


def get_profile() -> str:
    """Return the user's Google profile as a readable string."""
    creds = get_credentials()
    if not creds:
        return "Google account not connected, sir."
    import requests
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    if resp.status_code != 200:
        return "Could not fetch profile, sir."
    info = resp.json()
    lines = [
        f"Name:  {info.get('name', 'N/A')}",
        f"Email: {info.get('email', 'N/A')}",
    ]
    if info.get("locale"):
        lines.append(f"Locale: {info['locale']}")
    return "\n".join(lines)


def get_calendar_events(max_results: int = 5) -> str:
    """Read upcoming calendar events."""
    try:
        from googleapiclient.errors import HttpError
        service = _build_service("calendar", "v3")
        import datetime
        now     = datetime.datetime.utcnow().isoformat() + "Z"
        events  = service.events().list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        items = events.get("items", [])
        if not items:
            return "No upcoming events found, sir."

        lines = ["Upcoming events:"]
        for ev in items:
            start = ev["start"].get("dateTime", ev["start"].get("date", "?"))
            title = ev.get("summary", "Untitled")
            lines.append(f"  - {start}: {title}")
        return "\n".join(lines)

    except HttpError as e:
        return f"Calendar access error: {e}"
    except Exception as e:
        return f"Calendar error: {e}"


def read_emails(query: str = "", max_results: int = 5) -> str:
    """Read recent Gmail messages, optionally filtered by query."""
    try:
        from googleapiclient.errors import HttpError
        from email.utils import parsedate_to_datetime
        import base64
        service = _build_service("gmail", "v1")

        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return f"No emails found{' for: ' + query if query else ''}, sir."

        lines = []
        for msg_data in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_data["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(No subject)")
            sender  = headers.get("From", "Unknown")
            lines.append(f"  From: {sender} | Subject: {subject}")

        return "Recent emails:\n" + "\n".join(lines)

    except HttpError as e:
        return f"Gmail access error: {e}"
    except Exception as e:
        return f"Gmail error: {e}"


def search_contacts(query: str = "", max_results: int = 10) -> str:
    """Search Google Contacts."""
    try:
        from googleapiclient.errors import HttpError
        service = _build_service("people", "v1")

        results = service.people().connections().list(
            resourceName="people/me",
            personFields="names,emailAddresses,phoneNumbers",
            pageSize=max_results,
        ).execute()

        connections = results.get("connections", [])
        if not connections:
            return "No contacts found, sir."

        lines = ["Your contacts:"]
        for person in connections:
            name = person.get("names", [{}])[0].get("displayName", "Unknown")
            lines.append(f"  - {name}")
        return "\n".join(lines)

    except HttpError as e:
        return f"Contacts access error: {e}"
    except Exception as e:
        return f"Contacts error: {e}"
