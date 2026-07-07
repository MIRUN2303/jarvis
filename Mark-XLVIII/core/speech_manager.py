import enum
import threading
import time
import numpy as np
from typing import Callable, Optional


class SpeechState(enum.Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"


class SpeechStateManager:
    def __init__(self, on_state_change: Optional[Callable[[SpeechState, SpeechState], None]] = None):
        self._lock = threading.Lock()
        self._state = SpeechState.IDLE
        self._listeners: list[Callable[[SpeechState, SpeechState], None]] = []
        self._event_log: list[str] = []
        self._max_log = 200
        if on_state_change:
            self._listeners.append(on_state_change)

    def get(self) -> SpeechState:
        with self._lock:
            return self._state

    def set(self, new_state: SpeechState, reason: str = ""):
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state

        log = f"[Speech] {old.value} -> {new_state.value}"
        if reason:
            log += f" ({reason})"
        print(log)
        self._event_log.append(log)
        if len(self._event_log) > self._max_log:
            self._event_log.pop(0)
        for listener in self._listeners:
            try:
                listener(old, new_state)
            except Exception:
                pass

    def is_speaking(self) -> bool:
        return self.get() == SpeechState.SPEAKING

    def is_listening(self) -> bool:
        return self.get() == SpeechState.LISTENING


class SileroVAD:
    def __init__(self):
        self._model = None
        self._ready = False
        self._get_speech_timestamps = None
        self._init_thread = threading.Thread(target=self._init, daemon=True)
        self._init_thread.start()

    def _init(self):
        try:
            import silero_vad
            self._get_speech_timestamps = silero_vad.get_speech_timestamps
            self._model = True
            self._ready = True
            print("[VAD] Silero VAD loaded")
        except Exception as e:
            print(f"[VAD] Silero init failed: {e}")

    def is_ready(self) -> bool:
        return self._ready

    def is_speech(self, audio_chunk: np.ndarray, sample_rate: int = 16000) -> bool:
        if not self._ready:
            return False
        try:
            audio_float = audio_chunk.astype(np.float32) / 32768.0
            if audio_float.ndim > 1:
                audio_float = audio_float.squeeze(-1)
            ts = self._get_speech_timestamps(audio_float, sampling_rate=sample_rate)
            return len(ts) > 0
        except Exception:
            return False

