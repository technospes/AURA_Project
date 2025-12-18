import speech_recognition as sr
import queue
import threading
import sys

class VoiceEngine:
    def __init__(self):
        self.q = queue.Queue()
        self.running = True
        self.recognizer = sr.Recognizer()
        
        # KEY SETTINGS FOR ACCURACY
        self.recognizer.energy_threshold = 4000  # Only listen to clear speech, ignore hum
        self.recognizer.dynamic_energy_threshold = True # Auto-adjust for background noise
        self.recognizer.pause_threshold = 0.5    # Fast response (don't wait long after speaking)

        try:
            self.mic = sr.Microphone()
            # Warmup: Adjust for ambient noise
            print("Calibrating microphone for background noise... (Please be quiet)")
            with self.mic as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("Microphone Calibrated.")
        except Exception as e:
            print(f"Mic Error: {e}")
            sys.exit(1)
        
        # Start background listener (Non-blocking)
        self.stop_listening = self.recognizer.listen_in_background(self.mic, self.callback)

    def callback(self, recognizer, audio):
        try:
            # Usage: recognize_google (Free API)
            # You can switch to 'recognize_whisper' here if you install openai-whisper later
            text = recognizer.recognize_google(audio).lower()
            if text:
                print(f"[debug] Heard: {text}")
                self.q.put(text)
        except sr.UnknownValueError:
            pass # Silence/Noise
        except sr.RequestError:
            print("API Unavailable (Internet down?)")

    def get_command(self):
        if not self.q.empty():
            return self.q.get()
        return None

    def stop(self):
        self.stop_listening(wait_for_stop=False)