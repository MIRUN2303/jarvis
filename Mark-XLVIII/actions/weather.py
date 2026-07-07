#weather.py — Free weather via wttr.in (no API key needed)
import subprocess
import json
import re
from urllib.request import urlopen, Request

_WTTR_URL = "https://wttr.in/{city}?format=j1"
_USER_AGENT = "ROOKI-Assistant/1.0"


def _fetch_json(city: str) -> dict | None:
    """Fetch weather data from wttr.in as JSON."""
    try:
        url = _WTTR_URL.format(city=city.replace(" ", "+"))
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Weather] Fetch failed: {e}")
        return None


def _get_emoji(code: int) -> str:
    """Map weather code to simple text icon (no emoji — Windows safe)."""
    mapping = {
        113: "[Sunny]", 116: "[Cloudy]", 119: "[Cloudy]", 122: "[Cloudy]",
        143: "[Foggy]", 176: "[Rain]", 179: "[Snow]", 182: "[Snow]",
        185: "[Rain]", 200: "[Storm]", 227: "[Snow]", 230: "[Snow]",
        248: "[Foggy]", 260: "[Foggy]", 263: "[Rain]", 266: "[Rain]",
        281: "[Rain]", 284: "[Rain]", 293: "[Rain]", 296: "[Rain]",
        299: "[Rain]", 302: "[Rain]", 305: "[Rain]", 308: "[Rain]",
        311: "[Rain]", 314: "[Rain]", 317: "[Rain]", 320: "[Snow]",
        323: "[Snow]", 326: "[Snow]", 329: "[Snow]", 332: "[Snow]",
        335: "[Snow]", 338: "[Snow]", 350: "[Hail]", 353: "[Rain]",
        356: "[Rain]", 359: "[Rain]", 362: "[Rain]", 365: "[Rain]",
        368: "[Snow]", 371: "[Snow]", 374: "[Rain]", 377: "[Rain]",
        386: "[Storm]", 389: "[Storm]", 392: "[Snow]", 395: "[Snow]",
    }
    return mapping.get(code, "[?]")


def get_weather(city: str = "", forecast_days: int = 0) -> str:
    """Get current weather for a city. forecast_days: 0=current, 1-3=forecast."""
    city = city.strip() or "auto:ip"  # auto-detect location

    data = _fetch_json(city)
    if not data:
        return f"Could not fetch weather for '{city}', sir."

    try:
        cc = data.get("current_condition", [{}])[0]
        temp = cc.get("temp_C", "?")
        feels = cc.get("FeelsLikeC", "?")
        desc = cc.get("weatherDesc", [{}])[0].get("value", "?")
        code = int(cc.get("weatherCode", 0))
        wind = cc.get("windspeedKmph", "?")
        humid = cc.get("humidity", "?")
        icon = _get_emoji(code)

        loc = data.get("nearest_area", [{}])[0]
        area = loc.get("areaName", [{}])[0].get("value", city)
        region = loc.get("region", [{}])[0].get("value", "")

        lines = [f"Weather in {area}, {region}: {icon} {desc}, {temp}°C (feels {feels}°C)"]
        lines.append(f"Wind: {wind} km/h, Humidity: {humid}%")

        # Forecast
        if forecast_days > 0:
            forecast = data.get("weather", [])
            for i in range(min(forecast_days, len(forecast))):
                day = forecast[i]
                date = day.get("date", "")
                maxt = day.get("maxtempC", "?")
                mint = day.get("mintempC", "?")
                desc_day = day.get("hourly", [{}])[0].get("weatherDesc", [{}])[0].get("value", "?")
                code_day = int(day.get("hourly", [{}])[0].get("weatherCode", 0))
                icon_day = _get_emoji(code_day)
                lines.append(f"  {date}: {icon_day} {desc_day}, {mint}-{maxt}°C")

        return "\n".join(lines)
    except Exception as e:
        print(f"[Weather] Parse failed: {e}")
        return f"Weather data unavailable for '{city}', sir."


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "current": lambda p, pl, sp: get_weather(p.get("city", "")),
    "forecast": lambda p, pl, sp: get_weather(p.get("city", ""), forecast_days=p.get("days", 3)),
}

def weather(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "current").lower().strip()
    handler = _ACTION_MAP.get(action)
    if not handler:
        return "Unknown weather action. Use: current or forecast."
    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        return f"Weather failed, sir: {e}"
