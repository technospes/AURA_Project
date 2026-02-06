"""
AURA Configuration - AI AGENT VERSION  
Pure AI-driven system with no hardcoded patterns
"""
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Any
from dotenv import load_dotenv

# ============================================================================
# LOAD ENVIRONMENT
# ============================================================================
PROJECT_ROOT = Path(__file__).parent.parent
dotenv_path = PROJECT_ROOT / '.env'
load_dotenv(dotenv_path=dotenv_path)

# ============================================================================
# PROJECT PATHS
# ============================================================================
ROOT_DIR = PROJECT_ROOT
MODELS_DIR = PROJECT_ROOT / "models"
SRC_DIR = PROJECT_ROOT / "src"

# ============================================================================
# MODEL PATHS
# ============================================================================
MODEL_PATHS = {
    'wake_word': str(MODELS_DIR / "vosk-model-small-en-us-0.15"),  
    'asr_english': str(MODELS_DIR / "vosk-model-small-en-us-0.15"),
}
MODEL_REGISTRY = MODEL_PATHS

# ============================================================================
# API CONFIGURATION
# ============================================================================
@dataclass
class APIConfig:
    """API Configuration for Groq"""
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    model: str = "llama-3.1-8b-instant"
    whisper_model: str = "whisper-large-v3-turbo"
    max_tokens: int = 1024
    temperature: float = 0.7
    
    @property
    def is_configured(self) -> bool:
        return bool(self.groq_api_key and len(self.groq_api_key) > 30)

API_CONFIG = APIConfig()

# Legacy GROQ_CONFIG for compatibility
GROQ_CONFIG = {
    "api_key": API_CONFIG.groq_api_key,
    "model": API_CONFIG.model,
    "whisper_model": API_CONFIG.whisper_model,
    "max_tokens": API_CONFIG.max_tokens,
    "temperature": API_CONFIG.temperature
}

# ============================================================================
# VOICE CONFIGURATION
# ============================================================================
@dataclass
class VoiceConfig:
    """Voice recognition and synthesis configuration"""
    
    # Wake Word Settings
    wake_word: str = "jarvis"
    wake_words: List[str] = field(default_factory=lambda: [
        "jarvis", "jarves", "jarvish", "jarbes", "jarvas", "jarvus",
        "hey jarvis", "ok jarvis", "yo jarvis",
    ])
    wake_word_confidence: float = 0.75
    wake_word_timeout: float = 5.0
    
    # Audio Settings
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 2400
    
    # Speech Detection
    min_speech_energy: float = 300.0
    silence_threshold: int = 20
    min_speech_duration: float = 0.3
    
    # AGC
    agc_enabled: bool = True
    
    # Confirmation settings
    confirm_destructive: bool = False
    critical_actions: List[str] = field(default_factory=lambda: [
        'shutdown', 'restart', 'delete', 'remove', 'uninstall'
    ])
    
    # Fuzzy matching for confirmations
    fuzzy_confirm_words: List[str] = field(default_factory=lambda: [
        'yes', 'confirm', 'do it', 'proceed', 'affirmative', 'go ahead'
    ])
    fuzzy_cancel_words: List[str] = field(default_factory=lambda: [
        'no', 'cancel', 'abort', 'stop', 'negative', 'never mind'
    ])

# ============================================================================
# VOCABULARY - HINTS FOR AI (NOT ROUTING!)
# ============================================================================
class ASRVocabulary:
    """Vocabulary hints for AI understanding (NOT hardcoded routing)"""
    
    # Common action verbs (hints for AI)
    ACTION_VERBS = {
        'open', 'close', 'launch', 'start', 'stop', 'quit', 'exit',
        'play', 'pause', 'search', 'find', 'type', 'write', 'research'
    }
    
    # Known desktop apps (hints for AI)
    DESKTOP_APPS = {
        'chrome', 'firefox', 'edge', 'safari',
        'spotify', 'discord', 'slack', 'teams',
        'notepad', 'calculator', 'explorer',
        'code', 'vscode', 'pycharm'
    }
    
    # Known websites (hints for URL normalization)
    KNOWN_WEBSITES = {
        'youtube': 'https://www.youtube.com',
        'google': 'https://www.google.com',
        'gmail': 'https://mail.google.com',
        'github': 'https://github.com',
        'netflix': 'https://www.netflix.com',
        'twitter': 'https://twitter.com',
        'facebook': 'https://www.facebook.com',
        'reddit': 'https://www.reddit.com',
        'linkedin': 'https://www.linkedin.com',
    }
    
    @classmethod
    def get_vocabulary_hints(cls) -> set:
        """Get all vocabulary words as hints for AI"""
        words = set()
        words.update(cls.ACTION_VERBS)
        words.update(cls.DESKTOP_APPS)
        words.update(cls.KNOWN_WEBSITES.keys())
        return words
    
    @classmethod
    def normalize_command(cls, text: str) -> str:
        """Basic text normalization for AI input"""
        return text.strip()
    
    @classmethod
    def is_desktop_app(cls, name: str) -> bool:
        """Check if name is a known desktop app"""
        return name.lower() in cls.DESKTOP_APPS
    
    @classmethod
    def get_known_website(cls, name: str) -> str:
        """Get URL for known website"""
        return cls.KNOWN_WEBSITES.get(name.lower())
    
    @classmethod
    def is_url_like(cls, text: str) -> bool:
        """Check if text looks like a URL"""
        return '.' in text or text.startswith(('http://', 'https://'))
    
    @classmethod
    def extract_url(cls, text: str) -> str:
        """Extract/format URL from text"""
        text_lower = text.lower().strip()
        
        # Check known websites
        if text_lower in cls.KNOWN_WEBSITES:
            return cls.KNOWN_WEBSITES[text_lower]
        
        # Format URL if needed
        if not text_lower.startswith(('http://', 'https://')):
            if '.' in text_lower:
                return f'https://{text_lower}'
            else:
                return f'https://www.{text_lower}.com'
        
        return text_lower

# ============================================================================
# INTENT CATEGORIES (for AI agent)
# ============================================================================
class IntentCategory:
    """Intent categories - used by AI agent"""
    ACTION = "action"
    QUESTION = "question"
    CONVERSATION = "conversation"
    APP = "app"
    WEB = "web"
    SEARCH = "search"
    MEDIA = "media"
    INPUT = "input"
    TAB = "tab"
    SYSTEM = "system"
    NAV = "navigation"
    FILE = "file"
    UNKNOWN = "unknown"

class Intent:
    """Intent data structure - used by AI agent"""
    def __init__(self, category, action=None, target=None, params=None, 
                 entities=None, confidence=1.0, source_text=""):
        self.category = category
        self.action = action
        self.target = target
        self.params = params or {}
        self.entities = entities or {}
        self.confidence = confidence
        self.source_text = source_text

class ModelType:
    """Model types"""
    WAKE_WORD = "wake_word"
    ASR_ENGLISH = "asr_english"

CONVERSATION_CONTEXT = {
    "last_command": None,
    "last_result": None,
    "session_start": None
}

# Placeholder for vision (if you have vision features)
MOUSE_PHYSICS = {"enabled": False}
GESTURE_CONFIG = {"enabled": False}
CAM_WIDTH = 640
CAM_HEIGHT = 480

# ============================================================================
# GLOBAL INSTANCES
# ============================================================================
VOICE_CONFIG = VoiceConfig()
ASR_VOCABULARY = ASRVocabulary()

# ============================================================================
# VALIDATION
# ============================================================================
def validate_config() -> bool:
    """Validate configuration before startup"""
    print("[Config] Validating configuration...")
    errors = []
    
    # Check Groq API Key
    if not API_CONFIG.groq_api_key:
        print("[Config] ⚠ GROQ_API_KEY not set - AI features disabled")
    else:
        print("[Config] ✓ Groq API Key found")
    
    # Check Vosk Models
    for model_name, model_path in MODEL_PATHS.items():
        if not os.path.exists(model_path):
            errors.append(f"✗ {model_name} model not found: {model_path}")
            print(f"[Config] {errors[-1]}")
            if model_name == "asr_english":
                print(f"[Config]   Download: https://alphacephei.com/vosk/models")
                print(f"[Config]   Extract to: {MODELS_DIR}")
        else:
            print(f"[Config] ✓ {model_name} model found")
    
    if errors:
        print("\n[Config] ✗ VALIDATION FAILED")
        return False
    
    print("[Config] ✓ All checks passed")
    return True

def get_system_info() -> dict:
    """Get system configuration info - compatible with both old and new main.py"""
    wake_word_exists = os.path.exists(MODEL_PATHS.get('wake_word', ''))
    asr_exists = os.path.exists(MODEL_PATHS.get('asr_english', ''))
    has_groq = bool(API_CONFIG.groq_api_key)
    
    return {
        # New keys (AI agent mode)
        'wake_word_model': wake_word_exists,
        'asr_model': asr_exists,
        'groq_api': has_groq,
        'wake_word': VOICE_CONFIG.wake_word,
        'models_dir': str(MODELS_DIR),
        'project_root': str(PROJECT_ROOT),
        'mode': 'Pure AI Agent (No Hardcoded Patterns)',
        
        # Legacy keys for compatibility with existing main.py
        'vosk_model': wake_word_exists or asr_exists,  # True if either model exists
        'ai_mode': has_groq  # True if Groq API key is configured
    }

def get_config_summary():
    """Alias for print_config_summary"""
    return print_config_summary()

def print_config_summary():
    """Print configuration summary"""
    info = get_system_info()
    
    print("\n" + "="*60)
    print("JARVIS AI AGENT - CONFIGURATION")
    print("="*60)
    print(f"Wake Word Model: {'✓ Available' if info['wake_word_model'] else '✗ Missing'}")
    print(f"ASR Model:       {'✓ Available' if info['asr_model'] else '✗ Missing'}")
    print(f"Groq API:        {'✓ Configured' if info['groq_api'] else '✗ Not Set'}")
    print(f"Wake Word:       {info['wake_word']}")
    print(f"Mode:            {info['mode']}")
    print(f"Models Dir:      {info['models_dir']}")
    print("="*60 + "\n")