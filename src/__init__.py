"""
AURA Voice Assistant - Core Module (V10.0 Production)
Zero-hallucination personal assistant system
"""

__version__ = "10.0.0"
__author__ = "Aura Development Team"

# ============================================================================
# SHARED STATE FOR MULTIPROCESSING
# ============================================================================
class SharedState:
    """Shared state between processes"""
    def __init__(self):
        # Use multiprocessing Value for thread-safe boolean
        try:
            from multiprocessing import Value
            self.system_active = Value('b', True)
        except:
            # Fallback for testing
            self.system_active = type('obj', (object,), {'value': True})()
        
        self.context = {}
    
    def get_context(self):
        """Get current context"""
        return self.context.copy()
    
    def update_context(self, key, value):
        """Update context"""
        self.context[key] = value

# ============================================================================
# CORE IMPORTS
# ============================================================================
try:
    from .config import (
        VOICE_CONFIG,
        ASR_VOCABULARY,
        IntentCategory,
        Intent,
        CONVERSATION_CONTEXT,
        MOUSE_PHYSICS,
        GESTURE_CONFIG,
        CAM_WIDTH,
        CAM_HEIGHT,
        MODEL_REGISTRY,
        ModelType,
        EXACT_PATTERNS,
        validate_config,
        get_config_summary
    )
    
    from .intent_parser import (
        parse_intent,
        validate_intent,
        get_supported_commands,
        is_command_supported
    )
    
    from .native_opener import (
        execute_intent,
        open_app,
        close_app,
        search_web,
        refresh_registry
    )
    
    from .voice_service import (
        AuraVoiceAssistant,
        voice_process_loop
    )
    
    print("[Aura] ✓ All modules loaded successfully")

except ImportError as e:
    print(f"[Aura] Warning: Import failed: {e}")
    
    # Provide fallbacks for missing imports
    VOICE_CONFIG = None
    ASR_VOCABULARY = None
    IntentCategory = None
    Intent = None
    CONVERSATION_CONTEXT = None
    MOUSE_PHYSICS = None
    GESTURE_CONFIG = None
    MODEL_REGISTRY = None
    ModelType = None

# ============================================================================
# PUBLIC API
# ============================================================================
__all__ = [
    # Core classes
    'SharedState',
    'AuraVoiceAssistant',
    
    # Configuration
    'VOICE_CONFIG',
    'ASR_VOCABULARY',
    'CONVERSATION_CONTEXT',
    'MODEL_REGISTRY',
    'ModelType',
    'IntentCategory',
    'Intent',
    
    # Vision (for future integration)
    'MOUSE_PHYSICS',
    'GESTURE_CONFIG',
    'CAM_WIDTH',
    'CAM_HEIGHT',
    
    # Functions
    'voice_process_loop',
    'parse_intent',
    'validate_intent',
    'execute_intent',
    'open_app',
    'close_app',
    'search_web',
    'refresh_registry',
    'validate_config',
    'get_config_summary',
    'get_supported_commands',
    'is_command_supported',
]

# ============================================================================
# VERSION INFO
# ============================================================================
def get_version_info():
    """Get version information"""
    return {
        "version": __version__,
        "author": __author__,
        "features": [
            "Zero-hallucination ASR",
            "Exact command matching",
            "Production-grade error handling",
            "Multi-threaded processing",
            "Context awareness",
            "Wake word detection"
        ]
    }

# ============================================================================
# INITIALIZATION CHECK
# ============================================================================
def check_dependencies():
    """Check if all required dependencies are installed"""
    dependencies = {
        "vosk": False,
        "sounddevice": False,
        "numpy": False,
        "pyautogui": False
    }
    
    try:
        import vosk
        dependencies["vosk"] = True
    except ImportError:
        pass
    
    try:
        import sounddevice
        dependencies["sounddevice"] = True
    except ImportError:
        pass
    
    try:
        import numpy
        dependencies["numpy"] = True
    except ImportError:
        pass
    
    try:
        import pyautogui
        dependencies["pyautogui"] = True
    except ImportError:
        pass
    
    return dependencies

def print_status():
    """Print system status"""
    print("\n" + "="*60)
    print("AURA VOICE ASSISTANT - SYSTEM STATUS")
    print("="*60)
    print(f"Version: {__version__}")
    
    deps = check_dependencies()
    print("\nDependencies:")
    for name, installed in deps.items():
        status = "✓" if installed else "✗"
        print(f"  {status} {name}")
    
    missing = [name for name, installed in deps.items() if not installed]
    if missing:
        print(f"\nMissing dependencies: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
    else:
        print("\n✓ All dependencies installed")
    
    print("="*60 + "\n")

# Auto-check on import
if __name__ != "__main__":
    # Only print brief status when imported
    deps = check_dependencies()
    missing = [name for name, installed in deps.items() if not installed]
    if missing:
        print(f"[Aura] Missing: {', '.join(missing)}")