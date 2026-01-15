"""
AURA Main Controller (V21.0 - PRODUCTION READY)
Features: Robust error handling, proper multiprocessing, clean shutdown
"""
import sys
import os
import time
import signal
from multiprocessing import Process, Value, Queue, Array, freeze_support
from ctypes import c_char

# Ensure proper imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from src.config import validate_config
    from src.voice_service import voice_process_loop
    print("[Aura] ✓ Core modules loaded")
except Exception as e:
    print(f"[Aura] ✗ Import error: {e}")
    sys.exit(1)

# Try to import vision service (optional)
VISION_AVAILABLE = False
try:
    from src.vision_service import vision_process_loop
    VISION_AVAILABLE = True
    print("[Aura] ✓ Vision module loaded")
except ImportError as e:
    print(f"[Aura] ⚠ Vision not available: {e}")
except Exception as e:
    print(f"[Aura] ⚠ Vision import error: {e}")

class SharedState:
    """Thread-safe shared state between processes"""
    
    def __init__(self):
        self.system_active = Value('b', True)
        self.voice_enabled = Value('b', True)
        self.vision_enabled = Value('b', True)
        
        # Communication
        self.command_queue = Queue()
        
        # Shared buffers
        self.last_app = Array(c_char, 100)
        self.mode = Array(c_char, 20)
        self.context = Array(c_char, 200)
        
        # Initialize
        self.set_last_app("")
        self.set_mode("desktop")
        self.set_context("")
    
    def set_last_app(self, app_name: str):
        """Thread-safe setter"""
        encoded = app_name.encode('utf-8')[:99]
        with self.last_app.get_lock():
            self.last_app.value = encoded
    
    def get_last_app(self) -> str:
        """Thread-safe getter"""
        with self.last_app.get_lock():
            return self.last_app.value.decode('utf-8', errors='ignore').strip()
    
    def set_mode(self, mode: str):
        """Thread-safe setter"""
        encoded = mode.encode('utf-8')[:19]
        with self.mode.get_lock():
            self.mode.value = encoded
    
    def get_mode(self) -> str:
        """Thread-safe getter"""
        with self.mode.get_lock():
            return self.mode.value.decode('utf-8', errors='ignore').strip()
    
    def set_context(self, context: str):
        """Thread-safe setter"""
        encoded = context.encode('utf-8')[:199]
        with self.context.get_lock():
            self.context.value = encoded
    
    def get_context(self) -> str:
        """Thread-safe getter"""
        with self.context.get_lock():
            return self.context.value.decode('utf-8', errors='ignore').strip()

class AuraSystem:
    """Main system controller"""
    
    def __init__(self):
        self.shared_state = SharedState()
        self.voice_process = None
        self.vision_process = None
        self.running = False
        
        # Signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print("\n[System] Shutdown signal received...")
        self.shutdown()
        sys.exit(0)
    
    def startup(self, enable_vision=True):
        """Start all system components"""
        print("\n" + "="*60)
        print("   A U R A   I N T E R F A C E   V 2 1 . 0")
        print("   PRODUCTION READY - Voice Assistant")
        print("="*60)
        
        # Validate configuration
        if not validate_config():
            print("[FATAL] Configuration validation failed!")
            return False
        
        print("[System] Configuration validated.")
        
        # Start Voice Process
        print("[System] Starting Voice Recognition...")
        try:
            self.voice_process = Process(
                target=voice_process_loop,
                args=(self.shared_state,),
                name="Aura_Voice",
                daemon=False
            )
            self.voice_process.start()
            time.sleep(1.5)  # Wait for initialization
            
            if not self.voice_process.is_alive():
                print("[Error] Voice process failed to start!")
                return False
            
            print("[System] ✓ Voice recognition online")
        
        except Exception as e:
            print(f"[Error] Failed to start voice: {e}")
            return False
        
        # Start Vision Process (optional)
        if enable_vision and VISION_AVAILABLE:
            print("[System] Starting Vision System...")
            try:
                self.vision_process = Process(
                    target=vision_process_loop,
                    args=(self.shared_state,),
                    name="Aura_Vision",
                    daemon=False
                )
                self.vision_process.start()
                time.sleep(0.5)
                
                if self.vision_process.is_alive():
                    print("[System] ✓ Vision system online")
                else:
                    print("[Warning] Vision process failed to start")
                    self.vision_process = None
            
            except Exception as e:
                print(f"[Warning] Vision not available: {e}")
                self.vision_process = None
        
        self.running = True
        
        # Print instructions
        print("\n" + "="*60)
        print(">> System Online")
        print(">> Say 'Jarvis' followed by your command")
        print(">>")
        print(">> Example Commands:")
        print("   • 'Jarvis, open notepad'")
        print("   • 'Jarvis, close spotify'")
        print("   • 'Jarvis, play weekend on spotify'")
        print("   • 'Jarvis, play Starboy on youtube'")
        print("   • 'Jarvis, search Python tutorial'")
        print("   • 'Jarvis, open youtube'")
        print("   • 'Jarvis, close this tab'")
        print("   • 'Jarvis, type hello world'")
        print(">>")
        print(">> Press Ctrl+C to exit")
        print("="*60 + "\n")
        
        return True
    
    def monitor(self):
        """Monitor process health"""
        try:
            while self.running and self.shared_state.system_active.value:
                # Check voice process
                if self.voice_process and not self.voice_process.is_alive():
                    print("[Error] Voice process died. Restarting...")
                    try:
                        self.voice_process = Process(
                            target=voice_process_loop,
                            args=(self.shared_state,),
                            name="Aura_Voice",
                            daemon=False
                        )
                        self.voice_process.start()
                        time.sleep(1.5)
                    except Exception as e:
                        print(f"[Error] Failed to restart voice: {e}")
                
                # Check vision process
                if self.vision_process and not self.vision_process.is_alive():
                    print("[Warning] Vision process died")
                    self.vision_process = None
                
                time.sleep(1.0)
        
        except KeyboardInterrupt:
            print("\n[System] Keyboard interrupt received")
        except Exception as e:
            print(f"[Error] Monitor error: {e}")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Clean shutdown"""
        if not self.running:
            return
        
        self.running = False
        self.shared_state.system_active.value = False
        print("\n[System] Shutting down...")
        
        # Stop voice process
        if self.voice_process:
            print("[System] Stopping voice recognition...")
            try:
                self.voice_process.terminate()
                self.voice_process.join(timeout=3)
                if self.voice_process.is_alive():
                    print("[System] Force killing voice process")
                    self.voice_process.kill()
                    self.voice_process.join(timeout=1)
            except Exception as e:
                print(f"[Warning] Voice shutdown error: {e}")
        
        # Stop vision process
        if self.vision_process:
            print("[System] Stopping vision system...")
            try:
                self.vision_process.terminate()
                self.vision_process.join(timeout=3)
                if self.vision_process.is_alive():
                    print("[System] Force killing vision process")
                    self.vision_process.kill()
                    self.vision_process.join(timeout=1)
            except Exception as e:
                print(f"[Warning] Vision shutdown error: {e}")
        
        print("[System] Goodbye!\n")

def main():
    """Main entry point"""
    freeze_support()
    
    # Parse arguments
    enable_vision = "--no-vision" not in sys.argv
    
    # Create and start system
    system = AuraSystem()
    
    if system.startup(enable_vision=enable_vision):
        try:
            system.monitor()
        except Exception as e:
            print(f"[Fatal] System error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            system.shutdown()
    else:
        print("[Fatal] System startup failed")
        sys.exit(1)
if __name__ == "__main__":
    main()