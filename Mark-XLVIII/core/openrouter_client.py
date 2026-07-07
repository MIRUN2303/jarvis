# openrouter_client.py → REPLACED with Gemini backend
# All action files keep their imports — same ask_llm / ask_llm_multimodal API.
import json
import base64
import sys
from pathlib import Path
from typing import Optional

from google import genai as _genai
from google.genai import types

_DEFAULT_MODEL = "gemini-2.5-flash"
# Map old OpenRouter model names to Gemini equivalents
_MODEL_MAP = {
    "openrouter/free":          _DEFAULT_MODEL,
    "nvidia/nemotron-3-super-120b-a12b:free": _DEFAULT_MODEL,
    "google/gemini-2.5-flash":  _DEFAULT_MODEL,
    "google/gemini-2.0-flash":  "gemini-2.0-flash",
}


def _resolve_model(model: str) -> str:
    """Map legacy OpenRouter model names to Gemini models."""
    if model.startswith("openrouter/"):
        return _MODEL_MAP.get(model, _DEFAULT_MODEL)
    # Already a valid Gemini model name
    return model


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = _get_base_dir()
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"


def _get_gemini_key() -> str:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data.get("gemini_api_key") or ""
    except Exception:
        return ""


def _gemini_client():
    return _genai.Client(api_key=_get_gemini_key())


def _build_config(system: Optional[str] = None, max_tokens: int = 256, temperature: float = 0.1):
    """Build a GenerateContentConfig from the common OpenRouter-style parameters."""
    cfg_kw = {}
    if system:
        cfg_kw["system_instruction"] = system
    cfg_kw["max_output_tokens"] = max_tokens
    cfg_kw["temperature"] = temperature
    return types.GenerateContentConfig(**cfg_kw)


def ask_llm(
    prompt: str,
    system: Optional[str] = None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 128,
    temperature: float = 0.0,
) -> str:
    """Replacement for OpenRouter ask_llm — uses Gemini."""
    client = _gemini_client()
    config = _build_config(system, max_tokens, temperature)
    resolved = _resolve_model(model)
    try:
        response = client.models.generate_content(
            model=resolved,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()
    except Exception as e:
        raise RuntimeError(f"Gemini ask_llm failed: {e}")


def ask_llm_multimodal(
    prompt: str,
    system: Optional[str] = None,
    images: Optional[list[bytes]] = None,
    audio: Optional[list[bytes]] = None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 256,
    temperature: float = 0.0,
) -> str:
    """Replacement for OpenRouter ask_llm_multimodal — uses Gemini."""
    client = _gemini_client()
    config = _build_config(system, max_tokens, temperature)
    resolved = _resolve_model(model)

    parts = [types.Part.from_text(text=prompt)]
    if images:
        for img in images:
            parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
    if audio:
        for aud in audio:
            parts.append(types.Part.from_bytes(data=aud, mime_type="audio/wav"))

    try:
        response = client.models.generate_content(
            model=resolved,
            contents=types.Content(role="user", parts=parts),
            config=config,
        )
        return (response.text or "").strip()
    except Exception as e:
        raise RuntimeError(f"Gemini ask_llm_multimodal failed: {e}")


# ── Legacy stubs (kept so old code that calls these directly doesn't break) ──
def get_openrouter_key() -> Optional[str]:
    return _get_gemini_key() or None


def save_openrouter_key(key: str) -> None:
    pass  # no-op — no OpenRouter key to save anymore
