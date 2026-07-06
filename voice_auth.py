import os
import numpy as np
import threading
import queue

try:
    import torch
    import torchaudio
    from speechbrain.inference.speaker import SpeakerRecognition
    HAS_SPEECHBRAIN = True
except ImportError:
    HAS_SPEECHBRAIN = False

class VoiceAuthenticator:
    def __init__(self, reference_file="reference_voice.wav", threshold=0.25):
        self.enabled = HAS_SPEECHBRAIN and os.path.exists(reference_file)
        self.reference_file = reference_file
        self.threshold = threshold
        self.ready = False
        self.model = None
        self.ref_emb = None
        
        if self.enabled:
            print("[VoiceAuth] Starting model initialization in background...")
            threading.Thread(target=self._init_model, daemon=True).start()
        else:
            if not HAS_SPEECHBRAIN:
                print("[VoiceAuth] Missing speechbrain/torch. Speaker Verification disabled.")
            elif not os.path.exists(reference_file):
                print(f"[VoiceAuth] Reference voice not found at {reference_file}. Please run enroll_voice.py.")

    def _init_model(self):
        try:
            print("[VoiceAuth] Downloading/Loading Speaker Verification model (this may take a moment)...")
            
            # 1) Safely download the model without using Windows symlinks
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id="speechbrain/spkrec-ecapa-voxceleb", local_dir="tmp_model")
            
            # 2) Load from the local directory
            self.model = SpeakerRecognition.from_hparams(
                source="tmp_model", 
                savedir=None
            )
            # Load and process reference voice once
            signal, fs = torchaudio.load(self.reference_file)
            if fs != 16000:
                resampler = torchaudio.transforms.Resample(orig_freq=fs, new_freq=16000)
                signal = resampler(signal)
            
            # Extract embeddings for the reference voice
            self.ref_emb = self.model.encode_batch(signal)
            self.ready = True
            print("[VoiceAuth] [SUCCESS] Speaker Verification ready!")
        except Exception as e:
            print(f"[VoiceAuth] [ERROR] Failed to load model: {e}")
            self.enabled = False

    def verify(self, audio_data: np.ndarray) -> bool:
        """
        Takes raw 16kHz int16 audio data, converts to tensor, and checks if it matches reference.
        audio_data: 1D numpy array of int16
        """
        if not self.enabled or not self.ready:
            return True # Fallback to accepting all audio if not setup or still loading
            
        try:
            # Convert to float tensor and normalize
            audio_float = audio_data.astype(np.float32) / 32768.0
            signal = torch.from_numpy(audio_float).unsqueeze(0)
            
            # Get embedding for mic audio
            mic_emb = self.model.encode_batch(signal)
            
            # Compute cosine similarity
            score = torch.nn.functional.cosine_similarity(self.ref_emb, mic_emb)
            
            print(f"[VoiceAuth] Match score: {score.item():.3f} (Threshold: {self.threshold})")
            return score.item() > self.threshold
            
        except Exception as e:
            print(f"[VoiceAuth] Verification error: {e}")
            return True # Fail-open so we don't break Jarvis on error
