"""
AURA PRODUCTION ORCHESTRATOR (V3.0)
Architecture: Multiprocessing (True Parallelism)
Target: 60 FPS Vision | <50ms Voice Latency
"""
import multiprocessing
import time
import sys
import os

# Ensure src is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.shared import SharedState
from src.voice_service import voice_process_loop
from src.vision_service import vision_process_loop
from src.config import validate_config, VOICE_CONFIG, MODEL_PATHS

def print_banner():
    print(r"""
    =============================================
       A U R A   I N T E R F A C E   V 3 . 0
    =============================================
    [Vision] Core 1 | 60 FPS Target | High Precision
    [Voice]  Core 2 | Gigaspeech AI | Zero-Blocking
    =============================================
    """)

def verify_setup():
    """Pre-flight checks before launching heavy processes"""
    print("[System] Running pre-flight checks...")
    
    # 1. Validate Config & Paths
    if not validate_config():
        print("[System] Config validation failed. Exiting.")
        sys.exit(1)

    # 2. Check for English Model (Critical)
    if not os.path.exists(MODEL_PATHS["asr_english"]):
        print(f"[System] CRITICAL: English model missing at {MODEL_PATHS['asr_english']}")
        print("Please download 'vosk-model-en-us-0.42-gigaspeech' and extract to 'models/english'")
        sys.exit(1)

    print("[System] All systems nominal.")

if __name__ == "__main__":
    # Windows requires freeze_support for multiprocessing
    multiprocessing.freeze_support()
    
    print_banner()
    verify_setup()
    
    # 1. Initialize Shared Memory
    # This object lives in shared RAM, accessible by both processes
    state = SharedState()
    
    try:
        # 2. Spawn Voice Process (Background Service)
        # This runs on a separate CPU core entirely.
        print("[System] Spawning Voice Process...")
        p_voice = multiprocessing.Process(
            name="Aura_Voice",
            target=voice_process_loop, 
            args=(state,)
        )
        p_voice.start()
        
        # 3. Spawn Vision Process (Foreground Service)
        # This runs the camera and GUI. It gets 100% of one CPU core.
        print("[System] Spawning Vision Process...")
        p_vision = multiprocessing.Process(
            name="Aura_Vision",
            target=vision_process_loop,
            args=(state,)
        )
        p_vision.start()
        
        print(f">> System Online. Say '{VOICE_CONFIG.wake_word}' or use gestures.")
        print(">> Press 'ESC' on the Camera Window to exit safely.")
        
        # 4. Main Process Monitor
        # The main script now just watches the children.
        while state.system_active.value:
            if not p_vision.is_alive():
                print("[System] Vision process died unexpectedly.")
                state.system_active.value = False
                break
            time.sleep(1.0) # Low CPU usage monitor

    except KeyboardInterrupt:
        print("\n[System] Keyboard Interrupt received.")
        state.system_active.value = False

    finally:
        print("[System] Shutting down services...")
        
        # Graceful shutdown signal
        state.system_active.value = False
        
        # Give processes 2 seconds to close files/cameras
        p_vision.join(timeout=2.0)
        p_voice.join(timeout=2.0)
        
        # Force kill if stuck
        if p_vision.is_alive():
            print("[System] Force killing Vision...")
            p_vision.terminate()
        if p_voice.is_alive():
            print("[System] Force killing Voice...")
            p_voice.terminate()
            
        print("[System] Shutdown Complete. Goodbye.")