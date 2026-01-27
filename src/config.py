"""
AURA Configuration (V22.0 - Complete Hybrid System)
Supports: Vosk wake word + Groq AI + Gesture control
"""
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Any, Set
from dotenv import load_dotenv

# ============================================================================
# LOAD .ENV FILE FIRST
# ============================================================================
# Load from project root
PROJECT_ROOT = Path(__file__).parent.parent
dotenv_path = PROJECT_ROOT / '.env'
print(f"[Config] Loading .env from: {dotenv_path}")
load_dotenv(dotenv_path=dotenv_path)

# Debug: Check if environment variable is loaded
groq_key = os.getenv("GROQ_API_KEY", "")
print(f"[Config] GROQ_API_KEY loaded: {'Yes' if groq_key else 'No'}")
if groq_key:
    print(f"[Config] Key length: {len(groq_key)}")
    print(f"[Config] Key starts with: {groq_key[:10]}...")

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
    'asr_english': str(MODELS_DIR / "english"),
}
MODEL_REGISTRY = MODEL_PATHS

# ============================================================================
# API CONFIGURATION (for Groq)
# ============================================================================
@dataclass
class APIConfig:
    """API Configuration for Groq"""
    # Use the correct attribute name
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    model: str = "llama-3.3-70b-versatile"
    whisper_model: str = "whisper-large-v3-turbo"
    max_tokens: int = 1024
    temperature: float = 0.7
    
    def __post_init__(self):
        # Double-check environment variable
        env_key = os.getenv("GROQ_API_KEY", "")
        if env_key and not self.groq_api_key:
            self.groq_api_key = env_key
        
        # Debug output
        if self.groq_api_key:
            print(f"[APIConfig] API Key loaded successfully")
            print(f"[APIConfig] Key length: {len(self.groq_api_key)}")
        else:
            print("[APIConfig] WARNING: No API key found")
            print(f"[APIConfig] Environment has GROQ_API_KEY: {'GROQ_API_KEY' in os.environ}")
    
    @property
    def is_configured(self) -> bool:
        """Check if API is properly configured"""
        return bool(self.groq_api_key and len(self.groq_api_key) > 30)

# Create instance (only once!)
API_CONFIG = APIConfig()

# Legacy GROQ_CONFIG for compatibility with older modules
GROQ_CONFIG = {
    "api_key": API_CONFIG.groq_api_key,  # Use the same key
    "model": API_CONFIG.model,
    "whisper_model": API_CONFIG.whisper_model,
    "max_tokens": API_CONFIG.max_tokens,
    "temperature": API_CONFIG.temperature
}
# ============================================================================
# INTENT SYSTEM (from your old config)
# ============================================================================
class IntentCategory(Enum):
    APP = "app"
    SEARCH = "search"
    MEDIA = "media"
    SYSTEM = "system"
    INPUT = "input"
    NAV = "navigate"
    FILE = "file"
    WEB = "web"
    TAB = "tab"
    CONTEXT = "context"
    UNKNOWN = "unknown"

@dataclass
class Intent:
    category: IntentCategory
    action: str
    entities: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source_text: str = ""

    def to_command(self) -> Dict:
        return {
            "intent": self.category.value,
            "action": self.action,
            "payload": self.entities,
            "confidence": self.confidence,
            "source_text": self.source_text
        }
    
    def to_dict(self) -> Dict:
        """Compatibility method"""
        return self.to_command()

# ============================================================================
# COMMAND PATTERNS (from your old config)
# ============================================================================
COMMAND_PATTERNS = {
    # PRIORITY 1: Type command
    r'^(?:type|write|enter|input|dictate)\s+(.+)$': 
        {"category": IntentCategory.INPUT, "action": "type", "priority": 1},
    
    # PRIORITY 2: Desktop app close
    r'^(?:close|quit|exit|kill|terminate)\s+(?:the\s+)?(\w+)(?:\s+app(?:lication)?)?$':
        {"category": IntentCategory.APP, "action": "close", "priority": 2},
    
    # PRIORITY 3: Tab management
    r'^close\s+(?:this|current|active|the)?\s*tab$':
        {"category": IntentCategory.TAB, "action": "close_current", "priority": 3},
    r'^(?:new|open)\s+tab$':
        {"category": IntentCategory.TAB, "action": "new_tab", "priority": 3},
    
    # PRIORITY 4: Website opening
    r'^(?:open|go\s+to|visit|launch)\s+(.+)$':
        {"category": IntentCategory.WEB, "action": "open_site", "priority": 4},
    
    # PRIORITY 5: Play with platform
    r'^play\s+(.+?)\s+on\s+(spotify|youtube|netflix|soundcloud)$':
        {"category": IntentCategory.MEDIA, "action": "play", "priority": 5},
    
    # PRIORITY 6: Generic play
    r'^play\s+(.+)$': 
        {"category": IntentCategory.MEDIA, "action": "play", "priority": 6},
    
    # PRIORITY 7: Search
    r'^(?:search|google|find|lookup)\s+(?:for\s+)?(.+)$': 
        {"category": IntentCategory.SEARCH, "action": "search", "priority": 7},
}
EXACT_PATTERNS = COMMAND_PATTERNS  # Alias for compatibility

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
        "starwaves", "darvesh", "charvis", "jarvisa", "jarvice",
        "hey jarvis", "ok jarvis", "yo jarvis",
        "jaarvis", "jaarves",
        "jvis", "jarvi"
    ])
    wake_word_confidence: float = 0.75
    wake_word_timeout: float = 5.0
    
    # AI Mode Settings
    ai_mode_enabled: bool = True
    ai_keywords: List[str] = field(default_factory=lambda: [
        'ask', 'tell me', 'what is', 'who is', 'how to', 
        'explain', 'why', 'can you', 'could you'
    ])
    
    # Audio Settings
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 2400
    
    # Speech Detection
    min_speech_energy: float = 300.0
    silence_threshold: int = 20
    min_speech_duration: float = 0.3
    
    # AGC (Automatic Gain Control)
    agc_enabled: bool = True
    
    # Command Confirmation
    confirm_destructive: bool = True
    critical_actions: List[str] = field(default_factory=lambda: [
        'shutdown', 'restart', 'delete', 'power off', 'turn off'
    ])
    fuzzy_confirm_words: List[str] = field(default_factory=lambda: [
        'yes', 'confirm', 'sure', 'ok', 'okay', 'yeah', 'yep', 'yup'
    ])
    fuzzy_cancel_words: List[str] = field(default_factory=lambda: [
        'no', 'cancel', 'stop', 'nevermind', 'nope', 'nah'
    ])
    
    # Legacy compatibility
    audio_gain: float = 4.0
    pause_threshold: float = 0.3
    min_command_length: int = 2
    command_buffer_time: float = 1.5
    confidence_threshold: float = 0.85
    queue_size: int = 3
    processing_timeout: float = 0.1
    noise_reduction: bool = True

# ============================================================================
# VOCABULARY
# ============================================================================
class ASRVocabulary:
    """Vocabulary for speech recognition"""
    
    VERB_NORMALIZATION = {
        "play": "play", "plays": "play", "playing": "play", "played": "play",
        "close": "close", "closes": "close", "closing": "close", "closed": "close",
        "pause": "pause", "pauses": "pause", "paused": "pause",
        "resume": "resume", "resumes": "resume", "resumed": "resume",
        "open": "open", "opens": "open", "opening": "open", "opened": "open",
        "type": "type", "types": "type", "typing": "type",
    }
    
    KNOWN_WEBSITES = {
        'irctc': 'https://www.irctc.co.in',
        'paytm': 'https://paytm.com',
        'phonepe': 'https://www.phonepe.com',
        'gpay': 'https://pay.google.com',
        'makemytrip': 'https://www.makemytrip.com',
        'flipkart': 'https://www.flipkart.com',
        'amazon': 'https://www.amazon.in',
        'myntra': 'https://www.myntra.com',
        'swiggy': 'https://www.swiggy.com',
        'zomato': 'https://www.zomato.com',
        'netflix': 'https://www.netflix.com',
        'youtube': 'https://www.youtube.com',
        'facebook': 'https://www.facebook.com',
        'twitter': 'https://www.twitter.com',
        'instagram': 'https://www.instagram.com',
        'reddit': 'https://www.reddit.com',
        'github': 'https://www.github.com',
        'gmail': 'https://mail.google.com',
        'google': 'https://www.google.com',
    }
    
    DESKTOP_APPS = {
        'spotify', 'discord', 'slack', 'teams', 'zoom', 'skype',
        'notepad', 'calculator', 'calc', 'cmd', 'powershell',
        'chrome', 'firefox', 'edge', 'brave', 'opera', 'safari',
        'code', 'vscode', 'visual studio', 'pycharm', 'sublime',
        'word', 'excel', 'powerpoint', 'outlook',
        'steam', 'epic', 'vlc', 'obs',
    }
    
    @classmethod
    def get_all_words(cls) -> Set[str]:
        """Get comprehensive word list"""
        words = set()
        
        # Actions
        actions = [
            'open', 'close', 'start', 'stop', 'launch', 'quit', 'exit',
            'play', 'pause', 'resume', 'next', 'previous', 'skip',
            'search', 'find', 'google', 'browse',
            'type', 'write', 'enter', 'delete',
            'volume', 'mute', 'unmute',
            'tab', 'new', 'refresh',
        ]
        
        # Common apps
        apps = [
            'chrome', 'firefox', 'edge', 'notepad', 'calculator',
            'spotify', 'youtube', 'discord', 'code'
        ]
        
        # Common words
        common = [
            'this', 'that', 'the', 'a', 'an',
            'on', 'off', 'up', 'down',
            'please', 'thank', 'you',
            'what', 'where', 'when', 'why', 'how',
            'my', 'your', 'his', 'her'
        ]
        
        # AI query words
        ai_words = [
            'ask', 'tell', 'explain', 'describe',
            'what', 'who', 'where', 'when', 'why', 'how'
        ]
        
        for word_list in [actions, apps, common, ai_words]:
            words.update(word_list)
        
        return words
    
    @classmethod
    def normalize_command(cls, text: str) -> str:
        """Normalize command text"""
        text = text.lower().strip()
        fillers = ["please", "could you", "can you", "would you"]
        for filler in fillers:
            text = re.sub(r'\b' + filler + r'\b', '', text)
        
        tokens = text.split()
        normalized_tokens = [cls.VERB_NORMALIZATION.get(t, t) for t in tokens]
        text = " ".join(normalized_tokens)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    @classmethod
    def is_desktop_app(cls, name: str) -> bool:
        """Check if name refers to a desktop application"""
        name_lower = name.lower().strip()
        return any(app in name_lower for app in cls.DESKTOP_APPS)
    
    @classmethod
    def get_known_website(cls, text: str) -> str:
        """Get known website URL from text"""
        text_lower = text.lower().strip()
        text_lower = re.sub(r'\bopen\s+|\bgo\s+to\s+|\bvisit\s+', '', text_lower)
        
        if text_lower in cls.KNOWN_WEBSITES:
            return cls.KNOWN_WEBSITES[text_lower]
        
        for site, url in cls.KNOWN_WEBSITES.items():
            if site in text_lower:
                return url
        return None
    
    @classmethod
    def is_url_like(cls, text: str) -> bool:
        """Check if text looks like a URL"""
        text = text.lower()
        if cls.get_known_website(text):
            return True
        
        url_patterns = [
            r'\b\w+\s*dot\s*com\b',
            r'https?://',
            r'www\s*dot',
        ]
        return any(re.search(pattern, text) for pattern in url_patterns)
    
    @classmethod
    def extract_url(cls, text: str) -> str:
        """Extract and normalize URL from text"""
        known_url = cls.get_known_website(text)
        if known_url:
            return known_url
        
        text = text.lower().strip()
        text = re.sub(r'\s*dot\s*', '.', text)
        
        if not text.startswith('http'):
            text = 'https://' + text
        
        return text

# ============================================================================
# VISION CONFIG (for compatibility)
# ============================================================================
CAM_WIDTH = 640
CAM_HEIGHT = 480
CAM_FPS = 60
CAM_ID = 0

@dataclass
class MousePhysics:
    smoothing_cutoff_hz: float = 4.0
    deadzone_radius_active: float = 2.0
    magnetic_strength: float = 0.65
    magnetic_zones: dict = field(default_factory=lambda: {'default': 50})

@dataclass
class GestureConfig:
    pinch_threshold: int = 30
    release_threshold: int = 50

# ============================================================================
# GLOBAL INSTANCES
# ============================================================================
API_CONFIG = APIConfig()
VOICE_CONFIG = VoiceConfig()
ASR_VOCABULARY = ASRVocabulary()
MOUSE_PHYSICS = MousePhysics()
GESTURE_CONFIG = GestureConfig()
CONVERSATION_CONTEXT = {}  # For context awareness

# ============================================================================
# VALIDATION
# ============================================================================
def validate_config() -> bool:
    """
    Validate configuration before startup
    Returns True if all required components are available
    """
    print("[Config] Validating configuration...")
    errors = []
    warnings = []
    
    # 1. Check Groq API Key
    if not API_CONFIG.groq_api_key:
        warnings.append("⚠ GROQ_API_KEY not set - AI features will be disabled")
        warnings.append("  Set it in environment: GROQ_API_KEY=your_key_here")
        print(f"[Config] {warnings[-2]}")
        print(f"[Config] {warnings[-1]}")
    else:
        print(f"[Config] ✓ Groq API Key found")
    
    # 2. Check Vosk Model
    vosk_model_path = MODEL_PATHS.get('asr_english', '')
    if not os.path.exists(vosk_model_path):
        errors.append(f"✗ Vosk model not found at: {vosk_model_path}")
        errors.append(f"  Download: https://alphacephei.com/vosk/models")
        errors.append(f"  Extract to: {MODELS_DIR}")
        print(f"[Config] {errors[-3]}")
        print(f"[Config] {errors[-2]}")
        print(f"[Config] {errors[-1]}")
    else:
        model_files = ['am/final.mdl', 'graph/HCLG.fst']
        missing_files = []
        for file in model_files:
            if not os.path.exists(os.path.join(vosk_model_path, file)):
                missing_files.append(file)
        
        if missing_files:
            errors.append(f"✗ Vosk model incomplete - missing: {', '.join(missing_files)}")
            print(f"[Config] {errors[-1]}")
        else:
            print(f"[Config] ✓ Vosk model validated")
    
    # 3. Check models directory
    if not MODELS_DIR.exists():
        warnings.append(f"⚠ Models directory not found: {MODELS_DIR}")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[Config] Created models directory")
    
    # 4. Summary
    if errors:
        print("\n[Config] ✗ VALIDATION FAILED")
        return False
    
    if warnings:
        print("\n[Config] ⚠ Validation completed with warnings")
    else:
        print("\n[Config] ✓ All checks passed")
    
    return True

def check_ai_available() -> bool:
    """Check if AI features can be enabled"""
    return bool(API_CONFIG.groq_api_key)

def get_system_info() -> dict:
    """Get system configuration info"""
    return {
        'vosk_model': os.path.exists(MODEL_PATHS.get('asr_english', '')),
        'groq_api': bool(API_CONFIG.groq_api_key),
        'ai_mode': VOICE_CONFIG.ai_mode_enabled and check_ai_available(),
        'wake_word': VOICE_CONFIG.wake_word,
        'models_dir': str(MODELS_DIR),
        'project_root': str(PROJECT_ROOT)
    }

def print_config_summary():
    """Print configuration summary"""
    info = get_system_info()
    
    print("\n" + "="*60)
    print("SYSTEM CONFIGURATION SUMMARY")
    print("="*60)
    print(f"Vosk Model (Wake Word): {'✓ Available' if info['vosk_model'] else '✗ Missing'}")
    print(f"Groq API (AI Brain):    {'✓ Configured' if info['groq_api'] else '✗ Not Set'}")
    print(f"AI Mode:                {'✓ Enabled' if info['ai_mode'] else '✗ Disabled'}")
    print(f"Wake Word:              {info['wake_word']}")
    print(f"Models Directory:       {info['models_dir']}")
    print("="*60 + "\n")

def get_config_summary() -> Dict:
    """Legacy compatibility"""
    return {
        "model_path": MODEL_PATHS['asr_english'],
        "sample_rate": VOICE_CONFIG.sample_rate,
        "wake_word": VOICE_CONFIG.wake_word,
        "root_dir": str(PROJECT_ROOT),
    }