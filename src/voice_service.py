"""
AURA Voice Service - Production Fixed (V5.1)
Robust, Direct-Feed Audio Pipeline.
"""

import sys
import os
import json
import queue
import time
import sounddevice as sd
import vosk
import numpy as np
from src.config import VOICE_CONFIG, MODEL_REGISTRY, ModelType

# ============================================================================
# TEXT CLEANING & INTENT PARSING
# ============================================================================
def text_cleaner(text):
    """Normalizes speech text for consistent parsing"""
    t = text.lower().strip()
    t = t.replace(" dot ", ".").replace(" dot", ".")
    t = t.replace(" point ", ".")
    t = t.replace(" com", ".com").replace(" in", ".in")
    # Common mishearings
    t = t.replace("jar vis", "jarvis")
    t = t.replace("open app", "open")
    return t

def parse_intent(clean_cmd):
    """
    Determines Action and Payload from the cleaned command string.
    Returns: (Intent, Payload) or (None, None)
    """
    intent = None
    payload = None

    # 1. PLAY MEDIA (Youtube/Spotify)
    if "play" in clean_cmd:
        intent = "PLAY_MEDIA"
        raw = clean_cmd.replace("play", "").strip()
        # Check for specific platform request
        if " on " in raw:
            try:
                parts = raw.split(" on ")
                payload = {"song": parts[0].strip(), "platform": parts[1].strip()}
            except:
                payload = {"song": raw, "platform": "youtube"}
        else:
            payload = {"song": raw, "platform": "youtube"}

    # 2. SEARCH WEB
    elif "search" in clean_cmd:
        intent = "SEARCH_WEB"
        raw = clean_cmd.replace("search", "").strip()
        if " on " in raw:
            parts = raw.split(" on ")
            payload = {"query": parts[0].strip(), "platform": parts[1].strip()}
        else:
            payload = {"query": raw, "platform": "google"}

    # 3. OPEN APP
    elif "open" in clean_cmd:
        intent = "OPEN_APP"
        payload = clean_cmd.replace("open", "").strip()

    # 4. CLOSE APP
    elif "close" in clean_cmd:
        intent = "CLOSE_APP"
        payload = clean_cmd.replace("close", "").strip()

    # 5. TYPE
    elif "type" in clean_cmd:
        intent = "TYPE"
        payload = clean_cmd.replace("type", "").strip()

    # 6. SYSTEM COMMANDS
    elif "turn off" in clean_cmd and "pc" in clean_cmd:
        intent = "SYSTEM_SHUTDOWN"
        payload = "pc"
    elif "shutdown" in clean_cmd:
        intent = "SYSTEM_SHUTDOWN"
        payload = "pc"

    # 7. CALL (Discord)
    elif "call" in clean_cmd:
        intent = "CALL"
        payload = clean_cmd.replace("call", "").strip()

    # 8. SCROLL (Voice fallback)
    elif "scroll" in clean_cmd:
        intent = "SCROLL"
        if "down" in clean_cmd: payload = "down"
        elif "up" in clean_cmd: payload = "up"
        else: payload = "down"

    return intent, payload

# ============================================================================
# MAIN LOOP
# ============================================================================
def voice_process_loop(shared_state):
    # 1. Load Model
    model_path = MODEL_REGISTRY[ModelType.ASR_ENGLISH]['path']
    if not os.path.exists(model_path):
        print(f"[Voice] CRITICAL: Model not found at {model_path}")
        return

    try:
        vosk.SetLogLevel(-1) # Silence internal logs
        model = vosk.Model(model_path)
        rec = vosk.KaldiRecognizer(model, VOICE_CONFIG.sample_rate)
    except Exception as e:
        print(f"[Voice] Init Error: {e}")
        return

    print(f"[Voice] Ready. Wake Word: '{VOICE_CONFIG.wake_word}'")
    
    # Audio Queue
    q = queue.Queue()
    
    # GAIN SETTING: Increases microphone sensitivity
    # If it's too sensitive (hearing noise), lower this to 2.0 or 1.0
    GAIN_MULTIPLIER = 4.0 

    def callback(indata, frames, time_info, status):
        """Audio callback running in background thread"""
        if status:
            print(status, file=sys.stderr)
        
        # 1. Convert to numpy
        audio_data = np.frombuffer(indata, dtype=np.int16)
        
        # 2. Apply Digital Gain (Boost Volume)
        # Convert to float for math, apply gain, clip, convert back
        boosted = audio_data.astype(np.float32) * GAIN_MULTIPLIER
        boosted = np.clip(boosted, -32768, 32767).astype(np.int16)
        
        q.put(boosted.tobytes())

    # State variables
    last_partial = ""
    last_exec_time = 0
    COOLDOWN = 1.0  # Seconds between commands
    
    # Open Stream
    try:
        with sd.RawInputStream(samplerate=VOICE_CONFIG.sample_rate, 
                               blocksize=VOICE_CONFIG.streaming_buffer_size, 
                               device=None, 
                               dtype='int16', 
                               channels=1, 
                               callback=callback):
            
            while shared_state.system_active.value:
                try:
                    data = q.get(timeout=1.0)
                except queue.Empty:
                    continue

                if rec.AcceptWaveform(data):
                    # --- FINAL RESULT (Sentence Finished) ---
                    res = json.loads(rec.Result())
                    text = res.get("text", "")
                    
                    if text:
                        # Process full sentence
                        process_voice_input(text, shared_state, last_exec_time)
                        last_exec_time = time.time()
                        last_partial = ""
                        
                else:
                    # --- PARTIAL RESULT (Fast Track) ---
                    partial = json.loads(rec.PartialResult())
                    current_text = partial.get("partial", "")
                    
                    if current_text and current_text != last_partial:
                        last_partial = current_text
                        
                        # Check for instant wake word execution
                        if VOICE_CONFIG.wake_word in current_text:
                            # If we have a valid command in the partial text, do it now!
                            if time.time() - last_exec_time > COOLDOWN:
                                success = process_voice_input(current_text, shared_state, last_exec_time)
                                if success:
                                    last_exec_time = time.time()
                                    rec.Reset() # Clear buffer
                                    last_partial = ""

    except Exception as e:
        print(f"[Voice] Stream Error: {e}")

def process_voice_input(text, shared_state, last_exec_time):
    """
    Analyzes text, finds Wake Word, extracts Intent, sends to Queue.
    Returns True if a command was successfully executed.
    """
    clean_text = text_cleaner(text)
    wake_word = VOICE_CONFIG.wake_word

    # 1. Check for Wake Word
    if wake_word not in clean_text:
        return False

    # 2. Extract Command part
    # "jarvis open notepad" -> "open notepad"
    try:
        command_part = clean_text.split(wake_word, 1)[1].strip()
    except IndexError:
        return False # Just heard "jarvis"

    if len(command_part) < 2: 
        return False # Noise

    # 3. Parse Intent
    intent, payload = parse_intent(command_part)

    if intent and payload:
        print(f"[Voice] Executing: {intent} -> {payload}")
        shared_state.command_queue.put({
            "intent": intent, 
            "payload": payload
        })
        return True
    
    return False