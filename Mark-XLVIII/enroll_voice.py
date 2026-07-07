import sounddevice as sd
import numpy as np
import wave
import time
import os
import json
import sys

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")
FS = 16000
CHUNK_SEC = 2
NUM_SENTENCES = 10

SENTENCES = [
    "Hello Rooki, this is my voice profile enrollment.",
    "I am setting up my voice print so you can recognize me.",
    "Please listen only to my commands and ignore others.",
    "I am testing the microphone for voice verification.",
    "My name is Mirun and I will be your primary user.",
    "Speaker verification helps keep our conversation secure.",
    "Reading these sentences creates my unique voice signature.",
    "This system uses speech brain for voice recognition.",
    "I should speak clearly and naturally for best results.",
    "Voice enrollment is now complete, thank you Rooki.",
]


def get_or_create_profile_dir() -> str:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    profile_dir = os.path.join(PROFILES_DIR, "owner")
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def record_chunk(duration: float, text: str, chunk_index: int) -> np.ndarray:
    print(f"\n--- Sentence {chunk_index + 1} of {NUM_SENTENCES} ---")
    print(f"  Speak: \"{text}\"")
    print(f"  Recording for {duration:.0f} seconds...")
    rec = sd.rec(int(duration * FS), samplerate=FS, channels=1, dtype="int16")
    remaining = int(duration)
    while remaining > 0:
        print(f"  {remaining}s...", end="\r")
        time.sleep(1)
        remaining -= 1
    sd.wait()
    print("  [OK]" + " " * 20)
    return rec


def save_chunk_wav(audio: np.ndarray, path: str):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(FS)
        wf.writeframes(audio.tobytes())


def check_microphone() -> bool:
    try:
        devices = sd.query_devices()
        default = sd.default.device[0]
        if default is None:
            print("[Error] No input device found")
            return False
        info = sd.query_devices(default)
        print(f"  Mic: {info['name']} (channels={info['max_input_channels']}, samplerate={int(info['default_samplerate'])})")
        return True
    except Exception as e:
        print(f"[Error] Mic check failed: {e}")
        return False


def run_enrollment():
    print("=" * 55)
    print("  ROOKI VOICE ENROLLMENT WIZARD")
    print("=" * 55)

    if not check_microphone():
        sys.exit(1)

    profile_dir = get_or_create_profile_dir()
    chunks_dir = os.path.join(profile_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)

    audio_chunks = []
    for i, sentence in enumerate(SENTENCES):
        try:
            chunk = record_chunk(CHUNK_SEC, sentence, i)
            audio_chunks.append(chunk)
            path = os.path.join(chunks_dir, f"chunk_{i:02d}.wav")
            save_chunk_wav(chunk, path)
        except Exception as e:
            print(f"  [Skip] {e}")

    combined = np.concatenate(audio_chunks, axis=0)
    combined_path = os.path.join(profile_dir, "enrollment_audio.wav")
    save_chunk_wav(combined, combined_path)
    print(f"\n  Saved combined audio ({len(combined) / FS:.1f}s)")

    manifest = {
        "speaker": "owner",
        "num_sentences": len(audio_chunks),
        "duration_seconds": round(len(combined) / FS, 1),
        "sample_rate": FS,
    }
    with open(os.path.join(profile_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n  Voice enrollment complete!")
    print(f"  Profile: {profile_dir}")
    print(f"\n  Next step: Launch Rooki to generate the voice embedding")
    print(f"  from your enrollment audio and enable speaker verification.\n")


if __name__ == "__main__":
    run_enrollment()
