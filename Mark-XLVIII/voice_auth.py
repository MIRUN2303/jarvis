import os
import numpy as np
import threading

try:
    import torch
    from speechbrain.inference.speaker import SpeakerRecognition
    HAS_SPEECHBRAIN = True
except ImportError:
    HAS_SPEECHBRAIN = False

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")
EMBEDDING_PATH = os.path.join(PROFILES_DIR, "owner", "voice_embedding.npy")
MODEL_CACHE = os.path.join(os.path.dirname(__file__), "models", "speechbrain_model")


def normalize_volume(audio: np.ndarray, target_rms: float = 0.05) -> np.ndarray:
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-6:
        return audio
    gain = target_rms / rms
    return np.clip(audio * gain, -1.0, 1.0)


class VoiceAuthenticator:
    def __init__(self, threshold=0.15):
        self.enabled = HAS_SPEECHBRAIN and os.path.exists(EMBEDDING_PATH)
        self.threshold = threshold
        self.ready = False
        self.model = None
        self.ref_emb = None

        if self.enabled:
            print("[VoiceAuth] Found voice embedding. Loading model...")
            threading.Thread(target=self._init_model, daemon=True).start()
        else:
            if not HAS_SPEECHBRAIN:
                print("[VoiceAuth] speechbrain/torch not available. Disabled.")
            elif not os.path.exists(EMBEDDING_PATH):
                print("[VoiceAuth] No voice profile at profiles/owner/voice_embedding.npy")
                print("[VoiceAuth] Run enroll_voice.py then generate_embedding.py to enroll.")

    def _init_model(self):
        try:
            from huggingface_hub import snapshot_download
            os.makedirs(os.path.dirname(MODEL_CACHE), exist_ok=True)
            if not os.path.exists(os.path.join(MODEL_CACHE, "hyperparams.yaml")):
                print("[VoiceAuth] Downloading model...")
                snapshot_download(repo_id="speechbrain/spkrec-ecapa-voxceleb", local_dir=MODEL_CACHE)
            self.model = SpeakerRecognition.from_hparams(source=MODEL_CACHE, savedir=None)
            self.ref_emb = torch.from_numpy(np.load(EMBEDDING_PATH))
            self.ready = True
            print("[VoiceAuth] Speaker verification ready")
        except Exception as e:
            print(f"[VoiceAuth] Init error: {e}")
            self.enabled = False

    def verify(self, audio_data: np.ndarray) -> bool:
        if not self.enabled or not self.ready:
            return True
        try:
            audio = audio_data.astype(np.float32) / 32768.0
            if audio.ndim > 1:
                audio = audio.squeeze(-1)
            if audio.size < 8000:
                return True
            audio = normalize_volume(audio, target_rms=0.05)
            in_rms = np.sqrt(np.mean(audio ** 2))
            signal = torch.from_numpy(audio).unsqueeze(0)
            mic_emb = self.model.encode_batch(signal)
            score_tensor = self.model.similarity(self.ref_emb, mic_emb)
            score = score_tensor.item()
            return score > self.threshold
        except Exception as e:
            print(f"[VoiceAuth] Verify error: {e}")
            return True
