# translator.py — Free text translation via Google Translate (deep-translator)
try:
    from deep_translator import GoogleTranslator
    _TRANSLATOR = GoogleTranslator
    _GT_OK = True
except ImportError as e:
    print(f"[Translate] deep-translator not installed: {e}")
    _TRANSLATOR = None
    _GT_OK = False

# Common language names → codes (ISO 639-1)
_LANG_MAP = {
    "turkish": "tr", "turkce": "tr", "turk": "tr",
    "english": "en", "ingilizce": "en",
    "spanish": "es", "ispanyolca": "es",
    "french": "fr", "fransizca": "fr",
    "german": "de", "almanca": "de",
    "italian": "it", "italyanca": "it",
    "portuguese": "pt", "portekizce": "pt",
    "russian": "ru", "rusca": "ru",
    "japanese": "ja", "japonca": "ja",
    "chinese": "zh-cn", "cince": "zh-cn",
    "korean": "ko", "korece": "ko",
    "arabic": "ar", "arapca": "ar",
    "dutch": "nl", "hollandaca": "nl",
    "swedish": "sv", "isvecce": "sv",
    "danish": "da", "danimarkaca": "da",
    "finnish": "fi", "fince": "fi",
    "polish": "pl", "polonyaca": "pl",
    "czech": "cs", "cekce": "cs",
    "greek": "el", "yunanca": "el",
    "hindi": "hi", "hintce": "hi",
    "thai": "th", "tayca": "th",
    "vietnamese": "vi", "vietnamca": "vi",
    "romanian": "ro", "rumence": "ro",
    "ukrainian": "uk", "ukraynaca": "uk",
    "hebrew": "he", "ibranice": "he",
    "indonesian": "id", "endonezce": "id",
    "malay": "ms", "malezyaca": "ms",
}

# Reverse map code → English name
_CODE_TO_NAME = {v: k for k, v in _LANG_MAP.items() if k.isascii()}


def _resolve_lang(name: str) -> str:
    """Convert a language name or code to ISO 639-1 code."""
    name = name.strip().lower()
    if len(name) == 2 or (len(name) == 5 and "-" in name):
        return name
    return _LANG_MAP.get(name, name)


def _code_to_name(code: str) -> str:
    """Convert ISO code to English name."""
    return _CODE_TO_NAME.get(code, code)


def translate(text: str, to_lang: str = "english", from_lang: str = None) -> str:
    """Translate text to a target language."""
    if not _GT_OK:
        return "Translation unavailable. Run: pip install deep-translator"

    if not text.strip():
        return "What would you like me to translate, sir?"

    try:
        target = _resolve_lang(to_lang)
        source = _resolve_lang(from_lang) if from_lang else "auto"

        t = GoogleTranslator(source=source, target=target)
        result = t.translate(text)

        lines = [f"Translation to {to_lang}: {result}"]
        if source == "auto":
            # Detect source by doing a reverse translate
            try:
                detected = GoogleTranslator(source="auto", target="en").translate(text[:50])
                if detected and detected.lower() != text[:min(50, len(text))].lower():
                    lines[0] += " (language auto-detected)"
            except Exception:
                pass

        return "\n".join(lines)
    except Exception as e:
        print(f"[Translate] Error: {e}")
        return f"Translation failed, sir: {e}"


def detect_language(text: str) -> str:
    """Detect the language of given text using script heuristics."""
    if not _GT_OK:
        return "Translation unavailable."
    if not text.strip():
        return "No text provided."

    # Check for non-Latin scripts
    import unicodedata
    ranges = {
        "Cyrillic": (0x0400, 0x04FF), "Arabic": (0x0600, 0x06FF),
        "Hebrew": (0x0590, 0x05FF), "Devanagari": (0x0900, 0x097F),
        "Thai": (0x0E00, 0x0E7F), "Greek": (0x0370, 0x03FF),
        "Georgian": (0x10A0, 0x10FF), "Armenian": (0x0530, 0x058F),
        "Japanese": (0x3040, 0x30FF), "Korean": (0xAC00, 0xD7AF),
    }
    for ch in text:
        cp = ord(ch)
        for name, (lo, hi) in ranges.items():
            if lo <= cp <= hi:
                return f"Detected: {name} script (language auto-detected in translation)"
    # Latin-script: use translation round-trip as rough check
    try:
        t_en = GoogleTranslator(source="auto", target="en")
        result_en = t_en.translate(text)
        # If the translation is identical to input, might be English
        if result_en and result_en.strip().lower() == text.strip().lower():
            return "Detected: English"
        return "Detected: auto (language will be detected during translation)"
    except Exception:
        return "Detected: unknown (language will be detected during translation)"


def list_languages() -> str:
    """List supported languages."""
    langs = sorted(set(_LANG_MAP.keys()))
    english_names = [k for k in langs if k.isascii()]
    return "Supported: " + ", ".join(english_names) + "."


# ── Main entry point ────────────────────────────────────────────────────

_ACTION_MAP = {
    "translate":   lambda p, pl, sp: translate(p.get("text", ""), p.get("to", "english"), p.get("from")),
    "detect":      lambda p, pl, sp: detect_language(p.get("text", "")),
    "languages":   lambda p, pl, sp: list_languages(),
}


def translator(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "translate").lower().strip()
    handler = _ACTION_MAP.get(action)
    if not handler:
        return "Unknown action. Use: translate, detect, languages."
    try:
        result = handler(params, player, speak) or "Done."
        if speak:
            speak(result)
        return result
    except Exception as e:
        return f"Translation failed, sir: {e}"
