"""
AURA Configuration Module (V4.1 - Buttery Smooth)
"""

import os
import sys
from dataclasses import dataclass
from enum import Enum, auto

# ============================================================================
# PATHS - VERIFIED MODEL LOCATIONS
# ============================================================================
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class ModelType(Enum):
    ASR_ENGLISH = auto()
    ASR_HINDI = auto()

# Critical: Verify these paths exist on your system
MODEL_REGISTRY = {
    ModelType.ASR_ENGLISH: {
        'path': os.path.join(ROOT_DIR, "models", "english"),
        'name': "English (Whisper-large-v3-turbo)",
        'beam_size': 5
    },
    ModelType.ASR_HINDI: {
        'path': os.path.join(ROOT_DIR, "models", "hindi"),
        'name': "Hindi (Whisper-hindi-finetuned)",
        'beam_size': 5
    }
}

# Compatibility export for main.py checks
MODEL_PATHS = {
    "asr_english": MODEL_REGISTRY[ModelType.ASR_ENGLISH]['path'],
    "asr_hindi": MODEL_REGISTRY[ModelType.ASR_HINDI]['path']
}

# ============================================================================
# SYSTEM SETTINGS (The Missing Part)
# ============================================================================
CAM_WIDTH, CAM_HEIGHT = 640, 480
FPS = 60

# ============================================================================
# ACCURACY OPTIMIZATION SETTINGS
# ============================================================================
@dataclass
class AccuracyOptimization:
    """Settings to maximize speech recognition accuracy"""
    # Audio preprocessing
    noise_reduction: bool = True
    vad_threshold: float = 0.5
    min_speech_duration: float = 0.3
    
    # Model inference
    temperature: float = 0.0
    best_of: int = 5
    beam_size: int = 5
    patience: float = 1.0
    
    # Post-processing
    suppress_common_noise: bool = True
    capitalize_first_word: bool = True
    remove_trailing_period: bool = True

# ============================================================================
# VOCABULARY - EXTENDED & HINDI-FOCUSED
# ============================================================================
ENGLISH_VOCAB = [
    "jarvis", "hey jarvis", "ok jarvis", "computer",
    "open", "close", "launch", "start", "run", "exit", "quit",
    "type", "write", "enter", "press",
    "scroll", "up", "down", "left", "right",
    "stop", "pause", "resume", "continue",
    "firefox", "chrome", "mozilla", "browser", "edge",
    "discord", "slack", "teams",
    "notepad", "notepad++", "text editor",
    "calculator", "calc",
    "spotify", "music", "vlc", "media player", "player",
    "youtube", "netflix", "prime video",
    "google", "search", "bing",
    "vscode", "vs", "code", "visual", "studio", "pycharm", "intellij", "unity",
    "word", "excel", "powerpoint", "office", "terminal", "powershell", "cmd",
    "visual studio", "vs code", "pycharm", "terminal", "cmd",
    "unity", "blender", "photoshop",
    "play", "pause", "mute", "unmute", "volume", "sound", "audio",
    "louder", "quieter", "increase", "decrease", "max", "min",
    "next", "previous", "forward", "backward", "skip", "rewind",
    "shutdown", "restart", "sleep", "lock", "screenshot",
    "select", "all", "copy", "paste", "cut", "delete", "undo", "redo",
    "save", "find", "replace",
    "new tab", "close tab", "reload", "refresh", "go back", "go forward",
    "bookmark", "download",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "hundred", "thousand",
    "turn", "off", "pc", "computer", "shutdown", # Shutdown commands
    "yes", "no", "confirm", "cancel", "okay", "correct",
    "call", "called", # Added for call feature
    "[unk]", "[pause]", "[noise]"
]

HINDI_VOCAB = [
    "jarvis", "जार्विस", "हे जार्विस", 
    "kholo", "खोलो", "open",
    "band karo", "बंद करो", "close",
    "shuru karo", "शुरू करो", "start",
    "ruk jao", "रुक जाओ", "stop",
    "type karo", "टाइप करो", "likho", "लिखो", "write",
    "enter dabao", "एंटर दबाओ", "enter",
    "upar scroll karo", "ऊपर स्क्रॉल करो",
    "niche scroll karo", "नीचे स्क्रॉल करो",
    "scroll upar", "scroll niche",
    "firefox", "फायरफॉक्स",
    "computer", "band",
    "chrome", "क्रोम",
    "discord", "डिस्कॉर्ड",
    "notepad", "नोटपैड",
    "youtube", "यूट्यूब",
    "google", "गूगल",
    "spotify", "स्पोटिफाई",
    "vlc", "वीएलसी",
    "whatsapp", "व्हाट्सएप",
    "calculator", "कैलकुलेटर",
    "play karo", "प्ले करो", "gaana chalao", "गाना चलाओ",
    "pause karo", "पॉज करो", "ruk jao", "रुक जाओ",
    "volume badhao", "वॉल्यूम बढ़ाओ", "aawaz badhao", "आवाज बढ़ाओ",
    "volume kam karo", "वॉल्यूम कम करो", "aawaz kam karo", "आवाज कम करो",
    "next", "नेक्स्ट", "agla", "अगला",
    "previous", "पिछला", "pichla", "पिछला",
    "screenshot lo", "स्क्रीनशॉट लो",
    "shutdown karo", "शटडाउन करो",
    "lock karo", "लॉक करो",
    "naya tab", "नया टैब", "new tab",
    "tab band karo", "टैब बंद करो", "close tab",
    "refresh karo", "रिफ्रेश करो",
    "ek", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस",
    "haan", "हाँ", "yes",
    "nahi", "नहीं", "no",
    "theek hai", "ठीक है", "okay",
    "[unk]", "[shant]", "[awaz]"
]

# ============================================================================
# COMMAND MAPPING
# ============================================================================
HYBRID_COMMAND_MAP = {
    "firefox kholo": "open firefox",
    "chrome kholo": "open chrome",
    "youtube kholo": "open youtube",
    "discord kholo": "open discord",
    "open firefox": "firefox kholo",
    "open chrome": "chrome kholo",
    "chrome band karo": "close chrome",
    "firefox band karo": "close firefox",
    "gaana play karo": "play music",
    "video play karo": "play video",
}

# ============================================================================
# OPTIMIZED SETTINGS
# ============================================================================
@dataclass
class ModelSettings:
    model_size: str = "large-v3"
    language: str = None
    task: str = "transcribe"
    fp16: bool = True
    suppress_tokens: list = None
    
    def __post_init__(self):
        if self.suppress_tokens is None:
            self.suppress_tokens = [-1]

@dataclass
class AudioConfig:
    sample_rate: int = 16000
    chunk_size: int = 2048
    channels: int = 1
    device_index: int = None
    energy_threshold: int = 300
    dynamic_energy_threshold: bool = True
    pause_threshold: float = 0.8

@dataclass
class VoiceConfig:
    wake_word: str = "jarvis"
    primary_language: str = "hybrid"
    auto_detect_language: bool = True
    wake_word_confidence: float = 0.85
    command_confidence: float = 0.70
    
    # Audio config parameters flattened for direct access
    sample_rate: int = 16000
    streaming_buffer_size: int = 4096
    
    audio_config: AudioConfig = None
    
    def __post_init__(self):
        if self.audio_config is None:
            self.audio_config = AudioConfig()
    
    @property
    def active_vocabulary(self):
        if self.primary_language == "english":
            return ENGLISH_VOCAB
        elif self.primary_language == "hindi":
            return HINDI_VOCAB
        elif self.primary_language == "hybrid":
            return list(set(ENGLISH_VOCAB + HINDI_VOCAB))
        else:
            return ENGLISH_VOCAB

@dataclass
class MousePhysics:
    smoothing_cutoff_hz: float = 4.0
    deadzone_radius_active: float = 2.0
    magnetic_strength: float = 0.65
    magnetic_zones: dict = None
    
    def __post_init__(self): 
        if self.magnetic_zones is None:
            self.magnetic_zones = {'default': 50}

@dataclass
class GestureConfig:
    pinch_threshold: int = 30
    release_threshold: int = 50
    hold_time_click: float = 0.2

# ============================================================================
# INSTANTIATE CONFIGURATION
# ============================================================================
ACCURACY_OPT = AccuracyOptimization()
MODEL_SETTINGS = ModelSettings()
VOICE_CONFIG = VoiceConfig() #primary_language="hybrid"
MOUSE_PHYSICS = MousePhysics()
GESTURE_CONFIG = GestureConfig()

# Export for compatibility
SMOOTHING_CUTOFF = MOUSE_PHYSICS.smoothing_cutoff_hz

# ============================================================================
# VALIDATION
# ============================================================================
def validate_config(): # Legacy validation wrapper
    return validate_and_diagnose()

def validate_and_diagnose():
    print("=" * 60)
    print("AURA CONFIGURATION DIAGNOSTICS")
    print("=" * 60)
    
    all_ok = True
    
    print("\n1. MODEL PATHS:")
    for model_type, info in MODEL_REGISTRY.items():
        path = info['path']
        exists = os.path.exists(path)
        status = "✓" if exists else "✗"
        print(f"   {status} {model_type.name}: {path}")
        if not exists:
            all_ok = False
            print(f"      WARNING: Model not found! Download to: {path}")
            
    print(f"\n2. LANGUAGE SETTINGS:")
    print(f"   Primary language: {VOICE_CONFIG.primary_language}")
    print(f"   Auto-detect: {VOICE_CONFIG.auto_detect_language}")
    print(f"   Vocabulary size: {len(VOICE_CONFIG.active_vocabulary)} words")
    
    if all_ok:
        print("\nCONFIGURATION VALID ✓")
    else:
        print("\nCONFIGURATION ISSUES DETECTED ✗")
        
    return all_ok

if __name__ == "__main__":
    validate_and_diagnose()