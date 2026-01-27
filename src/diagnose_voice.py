"""
Diagnostic Script - Identify Voice Recognition Issues
Run this to test voice service in isolation
"""
import sys
import os
import time
import signal
from multiprocessing import Process, Value

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.voice_service import voice_process_loop

class MockSharedState:
    """Mock shared state for testing"""
    def __init__(self):
        self.system_active = Value('b', True)

def test_voice_service():
    """Test voice service in isolation"""
    print("\n" + "="*60)
    print("VOICE SERVICE DIAGNOSTIC TEST")
    print("="*60)
    print("\nThis will test the voice service WITHOUT the vision system.")
    print("Commands to test:")
    print("  1. Jarvis, open notepad")
    print("  2. Jarvis, type hello world")
    print("  3. Jarvis, search python")
    print("\nPress Ctrl+C to stop.\n")
    print("="*60 + "\n")
    
    # Create mock shared state
    shared_state = MockSharedState()
    
    # Create voice process
    voice_process = Process(target=voice_process_loop, args=(shared_state,), name="Voice_Diagnostic")
    
    # Start process
    print("[Test] Starting voice process...")
    voice_process.start()
    
    try:
        # Keep main thread alive
        while voice_process.is_alive():
            time.sleep(0.5)
    
    except KeyboardInterrupt:
        print("\n[Test] Shutdown requested...")
        shared_state.system_active.value = False
        voice_process.join(timeout=3)
        
        if voice_process.is_alive():
            print("[Test] Force terminating...")
            voice_process.terminate()
            voice_process.join()
    
    print("[Test] Voice service stopped.")
    print("="*60)

def test_voice_inline():
    """Test voice service in same process (for debugging)"""
    print("\n" + "="*60)
    print("INLINE VOICE SERVICE TEST")
    print("="*60)
    print("\nRunning voice service in main thread for debugging.")
    print("This helps identify if multiprocessing is causing issues.\n")
    print("Press Ctrl+C to stop.\n")
    print("="*60 + "\n")
    
    # Import after path setup
    from src.voice_service import AuraVoiceAssistant
    
    # Create mock shared state
    shared_state = MockSharedState()
    
    # Create assistant in main thread
    assistant = AuraVoiceAssistant(shared_state)
    
    try:
        assistant.start()
    except KeyboardInterrupt:
        print("\n[Test] Shutdown requested...")
        assistant.stop()
    
    print("="*60)

def test_audio_device():
    """Test audio input device"""
    print("\n" + "="*60)
    print("AUDIO DEVICE TEST")
    print("="*60)
    
    try:
        import sounddevice as sd
        import numpy as np
        
        print("\nAvailable audio devices:")
        print(sd.query_devices())
        
        print("\nDefault input device:")
        default_device = sd.query_devices(kind='input')
        print(f"  Name: {default_device['name']}")
        print(f"  Channels: {default_device['max_input_channels']}")
        print(f"  Sample Rate: {default_device['default_samplerate']}")
        
        print("\nTesting microphone (speak for 3 seconds)...")
        recording = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype='int16')
        sd.wait()
        
        # Analyze recording
        audio = recording.flatten()
        rms = np.sqrt(np.mean(audio**2))
        max_val = np.max(np.abs(audio))
        
        print(f"\nAudio Analysis:")
        print(f"  RMS Energy: {rms:.0f}")
        print(f"  Peak Level: {max_val}")
        print(f"  Status: ", end="")
        
        if rms < 100:
            print("❌ TOO QUIET - Increase microphone volume or gain")
        elif rms < 500:
            print("⚠️  QUIET - May need AGC boost")
        elif rms < 2000:
            print("✅ GOOD - Normal speech level")
        elif rms < 8000:
            print("✅ LOUD - Optimal for recognition")
        else:
            print("⚠️  VERY LOUD - May cause distortion")
        
    except Exception as e:
        print(f"\n❌ Audio device error: {e}")
    
    print("="*60)

def test_model_loading():
    """Test if ASR model loads correctly"""
    print("\n" + "="*60)
    print("ASR MODEL LOADING TEST")
    print("="*60)
    
    try:
        from vosk import Model
        from src.config import MODEL_PATHS
        
        model_path = MODEL_PATHS['asr_english']
        print(f"\nModel path: {model_path}")
        print(f"Exists: {os.path.exists(model_path)}")
        
        if not os.path.exists(model_path):
            print("❌ MODEL NOT FOUND!")
            print(f"Please ensure the GigaSpeech model is at: {model_path}")
            return False
        
        print("\nLoading model (this may take 10-20 seconds)...")
        start_time = time.time()
        model = Model(model_path)
        load_time = time.time() - start_time
        
        print(f"✅ Model loaded successfully in {load_time:.1f}s")
        return True
    
    except Exception as e:
        print(f"\n❌ Model loading error: {e}")
        return False
    
    finally:
        print("="*60)

def main():
    """Main diagnostic menu"""
    print("\n" + "="*70)
    print(" "*20 + "AURA VOICE DIAGNOSTIC TOOL")
    print("="*70)
    
    print("\nSelect test to run:")
    print("  1. Test Model Loading")
    print("  2. Test Audio Device")
    print("  3. Test Voice Service (Separate Process)")
    print("  4. Test Voice Service (Inline - Best for Debugging)")
    print("  5. Run All Tests")
    print("  0. Exit")
    
    choice = input("\nEnter choice (0-5): ").strip()
    
    if choice == "1":
        test_model_loading()
    elif choice == "2":
        test_audio_device()
    elif choice == "3":
        test_voice_service()
    elif choice == "4":
        test_voice_inline()
    elif choice == "5":
        if test_model_loading():
            test_audio_device()
            
            print("\nWhich voice service test?")
            print("  1. Separate Process (like main.py)")
            print("  2. Inline (better debugging)")
            sub_choice = input("Choice (1-2): ").strip()
            
            if sub_choice == "1":
                test_voice_service()
            else:
                test_voice_inline()
    elif choice == "0":
        print("Exiting...")
        return
    else:
        print("Invalid choice")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDiagnostic interrupted.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()