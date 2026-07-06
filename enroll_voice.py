import sounddevice as sd
import wave
import time
import os

FS = 16000  # Sample rate
DURATION = 10  # Seconds
OUTPUT_FILE = "reference_voice.wav"

def record_voice():
    print("=" * 50)
    print("🎙️ JARVIS SPEAKER VERIFICATION ENROLLMENT 🎙️")
    print("=" * 50)
    print(f"\nThis will record your voice for {DURATION} seconds.")
    print("Please read the following text clearly in your normal speaking voice:\n")
    print('"Hello Jarvis. This is my voice. I am setting up my voice print so that you can recognize me. You should only listen to my commands and ignore everything else. I am testing the microphone now."')
    print("\nPress ENTER when you are ready to start recording...")
    input()
    
    print("\n🔴 RECORDING (Speak now!)...")
    
    # Record audio
    recording = sd.rec(int(DURATION * FS), samplerate=FS, channels=1, dtype='int16')
    
    for i in range(DURATION, 0, -1):
        print(f"Time remaining: {i} seconds...", end='\r')
        time.sleep(1)
        
    sd.wait()  # Wait until recording is finished
    print("\n\n✅ Recording complete!")
    
    # Save as WAV file
    # sounddevice returns data as shape (frames, channels)
    with wave.open(OUTPUT_FILE, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(FS)
        wf.writeframes(recording.tobytes())
        
    print(f"Your voice print has been saved to: {os.path.abspath(OUTPUT_FILE)}")
    print("\nJarvis will now use this file to verify your voice!")

if __name__ == "__main__":
    record_voice()
