"""
Lightweight audio processing pipeline:
  NLMS echo cancellation (needs loopback reference from playback)
  + spectral noise suppression
  + adaptive voice activity threshold
  + faster end-of-speech detection

Thread-safe: add_loopback() from play thread, process_frame() from mic thread.
"""

import numpy as np
from collections import deque
from typing import Optional

# ─── IIR high-pass filter (biquad, 40 Hz) ──────────────────────────────
# Direct-form I, computed with scalar recurrence per sample.
# This avoids scipy.signal dependency.
_EPS = 1e-10

# 2nd-order Butterworth high-pass at 40 Hz, 16 kHz
_HPF_B = np.array([0.98703662, -1.97407325,  0.98703662], dtype=np.float64)
_HPF_A = np.array([1.0       , -1.97405986,  0.97410683], dtype=np.float64)

# ─── helpers (float domain: [-1, 1]) ──────────────────────────────────

def db_from_rms(rms_float: float) -> float:
    """Convert float-domain RMS (0..1) to dBFS."""
    if rms_float < 1e-8:
        return -100.0
    return float(max(-100.0, 20.0 * np.log10(rms_float)))


class AudioPipeline:
    """
    Mic processing pipeline with AEC + noise suppression + adaptive VAD.

    Integration:
      - Play thread calls `add_loopback(chunk)` with every chunk written to speakers.
      - Mic callback calls `process_frame(frame)`, which reads the last N play samples
        as echo reference and returns cleaned audio.

    The reference buffer stores play audio resampled to 16 kHz so it matches
    the mic sample rate. Play is 24 kHz → mic is 16 kHz (3:2 ratio).
    """

    def __init__(self, sample_rate: int = 16000, frame_size: int = 1024):
        self.sr    = sample_rate
        self.fs    = frame_size

        # ── NLMS echo canceller ───────────────────────────────────────
        self._filter_order = 384   # 24 ms at 16 kHz — enough for desktop echo
        self._mu = 0.08            # Normalized step size
        self._w  = np.zeros(self._filter_order, dtype=np.float64)  # adaptive taps
        # Ring buffer of recent play audio (16 kHz) — thread-safe via deque
        self._ref_buf = deque(maxlen=self._filter_order + self.fs + 128)

        # ── Noise suppression (spectral subtraction) ──────────────────
        self.ns_enabled = True
        self._noise_floor = np.ones(self.fs // 2 + 1, dtype=np.float64) * 1e-3
        self._alpha_noise = 0.92    # smoothing for noise estimate
        self._noise_adapt = True    # freeze noise floor when True
        self._ns_floor_gain = 0.15  # min gain (-16 dB) — prevents artifacts
        # Speech probability tracker (smoothed energy ratio)
        self._speech_prob = 0.0

        # ── HPF state (Direct-Form I: x[n-1], x[n-2], y[n-1], y[n-2]) ─
        self._hpf_x = np.zeros(2, dtype=np.float64)
        self._hpf_y = np.zeros(2, dtype=np.float64)

        # ── Amplification (reduced: AEC works better with clean signal) ──
        self._gain_db = 12.0
        self._gain_lin = 10.0 ** (self._gain_db / 20.0)

        # ── Spectral VAD (noise/voice frequency separation) ────────
        # Fan noise is low-frequency (0–200 Hz), voice is 300–3400 Hz.
        # We compute FFT energy per band and use spectral ratio.
        # FFT bins at 16k/1024 = 15.625 Hz/bin:
        #   Low band (noise): bins  0-12  (0–200 Hz)
        #   Voice band:       bins 20–217 (312–3390 Hz)
        #   High band (noise): bins 218–512 (3406–8000 Hz)
        self._nb_low   = slice(0, 13)     # 0–200 Hz
        self._nb_voice = slice(20, 218)   # 312–3390 Hz
        self._nb_high  = slice(218, 513)  # 3406–8000 Hz
        # Noise floor for voice-band energy (always-tracking)
        self._voice_noise_floor = 1e-6
        # VAD threshold: voice energy must exceed noise floor by this margin
        self._vad_margin_db = 3.0
        # Minimum voice energy fraction of total (rejects broadband hiss)
        self._vad_min_voice_frac = 0.08
        # Maximum noise/voice ratio (rejects low-frequency rumble)
        self._vad_max_noise_ratio = 12.0
        # Debounce
        self._vad_debounce = 3
        self._vad_debounce_ct = 0

        # ── State machine ─────────────────────────────────────────────
        self._voice_active = False
        self._silence_frames = 0
        self._eos_threshold = 10        # ~640 ms at 1024/16000
        self._eos_cooldown = 0

    # ── public API ──────────────────────────────────────────────────

    def add_loopback(self, chunk: np.ndarray):
        """
        Feed a playout audio chunk (int16, 24 kHz) as echo reference.
        Called from the play thread.
        """
        # Resample 24 kHz → 16 kHz (keep ratio 3:2)
        # Simple linear interpolation — fast, good enough for echo ref
        if not isinstance(chunk, np.ndarray):
            return
        samples_24k = chunk.ravel().astype(np.float64)
        target_len = len(samples_24k) * 2 // 3
        if target_len < 1:
            return

        # Linear interpolation: map 24k indices to 16k indices
        # out[i] = in[ floor(i * 3/2) ]
        idx_24k = np.linspace(0, len(samples_24k) - 1, target_len, dtype=np.float64)
        idx_lo  = idx_24k.astype(np.intp)
        idx_hi  = np.minimum(idx_lo + 1, len(samples_24k) - 1)
        frac    = idx_24k - idx_lo
        resampled = samples_24k[idx_lo] * (1 - frac) + samples_24k[idx_hi] * frac
        resampled = np.clip(resampled / 32768.0, -1.0, 1.0)

        # Push into ring buffer
        self._ref_buf.extend(resampled.tolist())

    def process_frame(self, frame: np.ndarray) -> dict:
        """
        Process one mic frame (int16, 1024 samples, 16 kHz).

        Pipeline: HPF → AEC (NLMS) → noise suppression → VAD + state machine

        Returns:
          processed:    int16 — cleaned audio
          is_voice:     bool  — frame-level VAD
          voice_active: bool  — utterance tracking (first voice → EOS)
          speech_ended: bool  — one-shot end-of-speech signal
          rms:          float — RMS of processed audio
          db:           float — dBFS of processed audio
        """
        # ── 1. Float conversion + HPF ─────────────────────────────
        x = frame.ravel().astype(np.float64) / 32768.0
        x = self._hpf(x)

        # ── 2. Amplify (moderate gain — not the full 24 dB) ──────
        x = x * self._gain_lin
        x = np.clip(x, -1.0, 1.0)

        # ── 3. Echo cancellation ──────────────────────────────────
        if len(self._ref_buf) >= self._filter_order + self.fs:
            x = self._nlms_aec(x)
        # else: not enough reference yet — skip AEC

        # ── 4. Noise suppression ──────────────────────────────────
        if self.ns_enabled:
            x = self._suppress_noise(x)

        # ── 5. Measure level (float domain) ───────────────────────
        rms = float(np.sqrt(np.mean(x ** 2)) + _EPS)
        db  = db_from_rms(rms)

        # ── 6. Spectral VAD (frequency-separated) ─────────────────
        # Compute FFT and split energy into noise bands vs voice band.
        # Fan noise (0–200 Hz + broadband hiss) is separated from voice (300–3400 Hz).
        window = np.hanning(self.fs)
        X = np.fft.rfft(x * window)
        mag_sq = np.abs(X) ** 2
        total_energy = float(np.sum(mag_sq) + _EPS)
        voice_energy = float(np.sum(mag_sq[self._nb_voice]) + _EPS)
        low_energy   = float(np.sum(mag_sq[self._nb_low]) + _EPS)
        high_energy  = float(np.sum(mag_sq[self._nb_high]) + _EPS)

        # Always-tracking noise floor — but ONLY during silence (voice_frac < 0.08).
        # This prevents ROOKI's own speech/echo from contaminating the noise floor.
        voice_frac = voice_energy / total_energy
        if voice_frac < 0.08:  # only update during confirmed noise/silence
            if voice_energy > self._voice_noise_floor:
                self._voice_noise_floor = (
                    0.95 * self._voice_noise_floor + 0.05 * voice_energy
                )
            else:
                self._voice_noise_floor = (
                    0.9998 * self._voice_noise_floor + 0.0002 * voice_energy
                )
        self._voice_noise_floor = max(self._voice_noise_floor, 1e-7)

        # Spectral ratio tests
        noise_ratio = (low_energy + high_energy) / max(voice_energy, _EPS)
        margin_lin = 10.0 ** (self._vad_margin_db / 20.0)

        is_voice = (
            voice_energy > self._voice_noise_floor * margin_lin
            and noise_ratio <= self._vad_max_noise_ratio
        )

        # ── 7. State machine with debounce ────────────────────────
        if self._eos_cooldown > 0:
            self._eos_cooldown -= 1
        elif not self._voice_active:
            if is_voice:
                self._vad_debounce_ct += 1
                if self._vad_debounce_ct >= self._vad_debounce:
                    self._voice_active = True
                    self._silence_frames = 0
            else:
                self._vad_debounce_ct = 0

        speech_ended = False
        if self._voice_active and not is_voice:
            self._silence_frames += 1
            if self._silence_frames >= self._eos_threshold:
                speech_ended = True
                self._voice_active = False
                self._silence_frames = 0
                self._vad_debounce_ct = 0
                self._eos_cooldown = 45  # ~3s debounce before next utterance

        # Convert back to int16
        processed = np.clip(x * 32768.0, -32768, 32767).astype(np.int16)

        return {
            "processed":    processed,
            "is_voice":     is_voice,
            "voice_active": self._voice_active,
            "speech_ended": speech_ended,
            "rms":          rms,
            "db":           db,
            "spectral": {
                "voice_frac":  round(voice_frac, 4),
                "noise_ratio": round(noise_ratio, 1),
                "vnf":         round(self._voice_noise_floor, 8),
            },
        }

    def reset(self):
        """Reset voice state + noise floor for a new utterance."""
        self._voice_active = False
        self._silence_frames = 0
        self._eos_cooldown = 0
        self._vad_debounce_ct = 0
        self._voice_noise_floor = 1e-6   # fresh noise floor per utterance
        self._w = np.zeros(self._filter_order, dtype=np.float64)  # reset AEC taps

    def flush_loopback(self):
        """Clear the reference buffer (e.g., after session reconnect)."""
        self._ref_buf.clear()

    # ── internal processing ──────────────────────────────────────────

    def _hpf(self, x: np.ndarray) -> np.ndarray:
        """2nd-order IIR high-pass at 40 Hz (direct-form I transposed)."""
        out = np.empty_like(x)
        x1, x2 = self._hpf_x
        y1, y2 = self._hpf_y
        b0, b1, b2 = _HPF_B
        a1, a2 = _HPF_A[1], _HPF_A[2]
        for i in range(len(x)):
            y = b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1; x1 = x[i]
            y2 = y1; y1 = y
            out[i] = y
        self._hpf_x = np.array([x1, x2], dtype=np.float64)
        self._hpf_y = np.array([y1, y2], dtype=np.float64)
        return out

    def _nlms_aec(self, mic: np.ndarray) -> np.ndarray:
        """
        Block NLMS echo cancellation.
        mic: float array (1024 samples, [-1, 1])
        Uses self._ref_buf as reference.
        """
        ref = np.array(self._ref_buf, dtype=np.float64)
        # The last (filter_order + fs) samples of ref are the reference for this frame
        # But since the ref_buf grows as play audio comes in, we need the most
        # recent samples that correspond to this mic frame.
        L = self._filter_order
        N = self.fs

        if len(ref) < L + N:
            return mic  # not enough reference

        # Take the most recent L+N samples
        ref = ref[-(L + N):]

        out = np.empty(N, dtype=np.float64)
        w = self._w.copy()

        # Per-sample NLMS (vectorized dot products)
        for i in range(N):
            x_i = ref[i:i + L]
            # Estimate echo
            echo_est = np.dot(w, x_i)
            # Residual = mic - echo
            e = mic[i] - echo_est
            out[i] = e
            # NLMS update
            nx = np.dot(x_i, x_i) + _EPS
            w += (self._mu / nx) * e * x_i

        self._w = w
        return out

    def _suppress_noise(self, x: np.ndarray) -> np.ndarray:
        """
        Wiener-filter noise suppression in frequency domain.
        x: float (1024 samples, [-1, 1])
        """
        N = len(x)
        N_fft = N  # 1024 → 513 bins

        # Windowed FFT
        window = np.hanning(N)
        X = np.fft.rfft(x * window)
        mag = np.abs(X)
        phase = np.angle(X)

        # Speech probability from voice-band energy vs total (spectral VAD)
        mag_sq = mag ** 2
        total_e  = float(np.sum(mag_sq) + _EPS)
        voice_e  = float(np.sum(mag_sq[self._nb_voice]) + _EPS)
        is_speech = (voice_e / total_e) > 0.10 and voice_e > 1e-4

        # Update noise floor during silence
        self._speech_prob = 0.95 * self._speech_prob + 0.05 * (1.0 if is_speech else 0.0)

        if self._noise_adapt and self._speech_prob < 0.3:
            # Smooth noise estimate
            self._noise_floor = (
                self._alpha_noise * self._noise_floor
                + (1 - self._alpha_noise) * mag
            )
        elif is_speech:
            # During speech, only let the noise floor go down (never up)
            self._noise_floor = np.minimum(self._noise_floor, mag)

        # Wiener gain: |S|^2 / (|S|^2 + |N|^2)
        # |S|^2 ≈ max(0, |X|^2 - |N|^2)
        mag_sq = mag ** 2
        noise_sq = self._noise_floor ** 2
        signal_sq = np.maximum(0.0, mag_sq - noise_sq)
        gain = signal_sq / (signal_sq + noise_sq + _EPS)

        # Apply floor to prevent musical noise
        gain = np.maximum(gain, self._ns_floor_gain)

        # Reconstruct
        Y = X * gain
        y = np.fft.irfft(Y, n=N)

        # Apply window again + overlap compensation
        y = y * window * 2.0

        return y

    @staticmethod
    def db_from_rms(rms_float: float) -> float:
        return db_from_rms(rms_float)
