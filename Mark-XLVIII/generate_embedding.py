import os
import sys
import numpy as np
import torch
from speechbrain.inference.speaker import SpeakerRecognition
from huggingface_hub import snapshot_download
import wave


PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")
ENROLLMENT_WAV = os.path.join(PROFILES_DIR, "owner", "enrollment_audio.wav")
EMBEDDING_OUT = os.path.join(PROFILES_DIR, "owner", "voice_embedding.npy")
MODEL_CACHE = os.path.join(os.path.dirname(__file__), "models", "speechbrain_model")


def load_wav(path: str, target_sr: int = 16000) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)
    if sr != target_sr:
        import scipy.signal
        ratio = target_sr / sr
        new_len = int(len(audio) * ratio)
        audio = scipy.signal.resample(audio, new_len).astype(np.int16)
    return audio


def main():
    if not os.path.exists(ENROLLMENT_WAV):
        print(f"[Error] Enrollment audio not found at {ENROLLMENT_WAV}")
        print("  Run enroll_voice.py first.")
        sys.exit(1)

    print("[Embedding] Loading SpeechBrain model...")
    os.makedirs(os.path.dirname(MODEL_CACHE), exist_ok=True)
    if not os.path.exists(os.path.join(MODEL_CACHE, "hyperparams.yaml")):
        print("[Embedding] Downloading model...")
        snapshot_download(repo_id="speechbrain/spkrec-ecapa-voxceleb", local_dir=MODEL_CACHE)

    model = SpeakerRecognition.from_hparams(source=MODEL_CACHE, savedir=None)

    print(f"[Embedding] Loading enrollment audio from {ENROLLMENT_WAV}")
    audio = load_wav(ENROLLMENT_WAV)

    from voice_auth import normalize_volume
    print(f"[Embedding] Generating embedding ({len(audio) / 16000:.1f}s audio)...")
    audio_float = audio.astype(np.float32) / 32768.0
    if audio_float.ndim > 1:
        audio_float = audio_float.squeeze(-1)
    audio_float = normalize_volume(audio_float, target_rms=0.05)
    signal = torch.from_numpy(audio_float).unsqueeze(0)
    embedding = model.encode_batch(signal)

    os.makedirs(os.path.dirname(EMBEDDING_OUT), exist_ok=True)
    np.save(EMBEDDING_OUT, embedding.cpu().numpy())
    print(f"[Embedding] Saved to {EMBEDDING_OUT}")
    print(f"[Embedding] Shape: {embedding.shape}")
    print("[Embedding] Voice verification ready!")


if __name__ == "__main__":
    main()
