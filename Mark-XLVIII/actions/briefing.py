#briefing.py — Daily briefing: weather + news + calendar in one command
from datetime import datetime
from actions.weather import get_weather
from actions.smart_lists import list_lists, show_list


def _get_news_headlines(max_items: int = 5) -> str:
    """Fetch latest news headlines via RSS (free, no API key)."""
    try:
        import feedparser
        feeds = [
            ("https://feeds.bbci.co.uk/news/rss.xml", "BBC"),
            ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "NYT"),
        ]
        headlines = []
        seen = set()
        for url, source in feeds:
            try:
                f = feedparser.parse(url)
                for entry in f.entries[:5]:
                    title = entry.get("title", "").strip()
                    if title and title not in seen:
                        seen.add(title)
                        headlines.append(f"  {title} ({source})")
                        if len(headlines) >= max_items:
                            break
            except Exception:
                continue
            if len(headlines) >= max_items:
                break
        if not headlines:
            return ""
        return "News headlines:\n" + "\n".join(headlines[:max_items])
    except ImportError:
        return ""  # feedparser not installed


def _get_calendar_events(max_items: int = 3) -> str:
    """Try to get Google Calendar events if connected."""
    try:
        from core.google_auth import is_connected, get_calendar_events
        if is_connected():
            events = get_calendar_events(max_items)
            if events and events != "[]" and "not connected" not in events.lower():
                return f"Calendar:\n  {events}"
    except Exception:
        pass
    return ""


def morning_briefing(city: str = "", include_news: bool = True, include_calendar: bool = True) -> str:
    """Generate a complete morning briefing."""
    now = datetime.now()
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    
    parts = [f"{greeting}, sir. Here's your briefing for {now.strftime('%A, %B %d')}:"]
    
    # Weather
    weather = get_weather(city, forecast_days=1)
    parts.append(f"\nWeather:\n  {weather}")
    
    # Calendar
    if include_calendar:
        cal = _get_calendar_events()
        if cal:
            parts.append(f"\n{cal}")
    
    # News
    if include_news:
        news = _get_news_headlines(5)
        if news:
            parts.append(f"\n{news}")
    
    # Active lists reminder
    lists_info = list_lists()
    if "No lists yet" not in lists_info:
        parts.append(f"\nYour lists:\n  {lists_info}")
    
    return "\n".join(parts)


def quick_briefing(city: str = "") -> str:
    """Quick weather-only briefing."""
    weather = get_weather(city)
    return f"Here's your quick briefing, sir:\n{weather}"


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "morning":  lambda p, pl, sp: morning_briefing(p.get("city", ""), p.get("news", True), p.get("calendar", True)),
    "quick":    lambda p, pl, sp: quick_briefing(p.get("city", "")),
}

def briefing(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "morning").lower().strip()
    handler = _ACTION_MAP.get(action)
    if not handler:
        return "Unknown briefing type. Use: morning or quick."
    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        return f"Briefing failed, sir: {e}"
