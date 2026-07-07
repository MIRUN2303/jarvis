import os
import json
import threading
import time
import numpy as np
from typing import Callable, Optional

HAS_VOSK = False
try:
    import vosk
    HAS_VOSK = True
except ImportError:
    pass

MODEL_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "vosk", "vosk-model-small-en-us-0.15"
)

STOP_KEYWORDS = ["stop", "wait", "no", "halt", "enough", "silence", "hey", "rooki", "exit", "cancel", "nevermind", "forget it", "that's enough", "hold on"]


class KeywordSpotter:
    def __init__(self):
        self._model = None
        self._rec = None
        self._ready = False
        self._callback: Optional[Callable[[], None]] = None
        self._partial_cb: Optional[Callable[[str], None]] = None   # live transcript
        self._active = False
        self._lock = threading.Lock()
        self._buf: list[np.ndarray] = []
        self._frame_count = 0
        self._last_hit = 0.0
        self._cooldown = 1.5
        self._debug_buf: list[str] = []

        # Throttle partial updates to max 1 per THROTTLE_MS
        self._THROTTLE_MS = 0.25          # 250 ms between UI updates
        self._last_partial_ts = 0.0

        # Accumulate this many frames (~64ms each at 1024/16000) before VOSK process
        self._ACCUM_FRAMES = 8            # ~500ms chunks — faster partials

        if HAS_VOSK and os.path.exists(MODEL_PATH):
            try:
                vosk.SetLogLevel(-1)
                self._model = vosk.Model(MODEL_PATH)
                self._rec = vosk.KaldiRecognizer(self._model, 16000)
                self._ready = True
                print("[KeywordSpotter] Ready — keywords:", STOP_KEYWORDS)
            except Exception as e:
                print(f"[KeywordSpotter] Init error: {e}")
        else:
            print(f"[KeywordSpotter] Model not found at {MODEL_PATH}")

    def set_callback(self, cb: Callable[[], None]):
        self._callback = cb

    def set_partial_callback(self, cb: Callable[[str], None]):
        """Callback fired with partial transcript text (real-time STT)."""
        self._partial_cb = cb

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self):
        with self._lock:
            self._active = True
            self._buf = []
            self._frame_count = 0
            if self._rec is not None:
                self._rec.Reset()

    def stop(self):
        with self._lock:
            self._active = False
            self._buf = []
            self._frame_count = 0

    def feed(self, audio: np.ndarray):
        if not self._ready:
            return
        with self._lock:
            if not self._active:
                return
            self._buf.append(audio)
            self._frame_count += 1
            if self._frame_count < self._ACCUM_FRAMES:
                return
            chunk = np.concatenate(self._buf, axis=0)
            self._buf = []
            self._frame_count = 0
            rec = self._rec
            cb = self._callback
            partial_cb = self._partial_cb

        rec.AcceptWaveform(chunk.tobytes())
        partial = rec.PartialResult()
        try:
            data = json.loads(partial)
            text = data.get("partial", "").strip()
            if text:
                self._debug_buf.append(text)
                if len(self._debug_buf) > 50:
                    self._debug_buf.pop(0)

                # ── Throttled partial transcript callback ──
                now = time.monotonic()
                if partial_cb and (now - self._last_partial_ts) >= self._THROTTLE_MS:
                    self._last_partial_ts = now
                    partial_cb(text)

                # ── Keyword detection (independent of partial cb) ──
                if cb:
                    text_lower = text.lower()
                    for kw in STOP_KEYWORDS:
                        if kw in text_lower:
                            if now - self._last_hit < self._cooldown:
                                return
                            self._last_hit = now
                            print(f"[KW] *** DETECTED '{kw}' — interrupting!")
                            cb()
                            return
        except Exception:
            pass