"""Quick microphone test — shows volume levels when you speak."""
import sounddevice as sd
import numpy as np

last_print = [0]

def callback(indata, frames, time_info, status):
    volume = np.sqrt(np.mean(indata.astype(np.float64) ** 2))
    # Print max once per second
    now = time_info.currentTime
    if now - last_print[0] > 0.3:
        if volume > 0.0001:
            print(f" SPEECH DETECTED — Volume: {volume:.6f}")
        else:
            print(f" silence — Volume: {volume:.6f}")
        last_print[0] = now

print("\n Testing microphone for 8 seconds...")
print("   Speak 'Jarvis' at normal volume NOW!\n")

try:
    with sd.InputStream(callback=callback, channels=1, samplerate=16000, device=1):
        sd.sleep(8000)
except Exception as e:
    print(f"Error with device 1: {e}")
    print("Trying default device...")
    with sd.InputStream(callback=callback, channels=1, samplerate=16000):
        sd.sleep(8000)

print("\n Test complete.")
print("Look at the numbers above when you spoke.")
print("If you see 'SPEECH DETECTED' with volume > 0.001, your mic works.")
print("If you only see 'silence', your mic gain is too low.")