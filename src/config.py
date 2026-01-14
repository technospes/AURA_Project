"""
AURA CONFIGURATION (V21.0 - DYNAMIC & PRODUCTION READY)
Features: Dynamic paths, improved intent routing, better accuracy
"""
import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Any, Set
from pathlib import Path

# ============================================================================
# DYNAMIC PATHS
# ============================================================================
# Automatically detect project root
ROOT_DIR = Path(__file__).parent.parent.resolve()

# VISION SETTINGS
CAM_WIDTH = 640
CAM_HEIGHT = 480
CAM_FPS = 60
CAM_ID = 0

# MODEL SETTINGS
class ModelType(Enum):
    ASR_ENGLISH = auto()

# Dynamic model path detection
def find_model_path():
    """Dynamically find the ASR model path"""
    possible_paths = [
        ROOT_DIR / "models" / "english",
        ROOT_DIR / "models" / "vosk-model-en-us",
        Path("E:/Aura_Project/models/english"),  # Fallback to your specific path
        Path(os.environ.get("AURA_MODEL_PATH", "")),  # Environment variable
    ]
    
    for path in possible_paths:
        if path.exists() and path.is_dir():
            return str(path)
    
    raise FileNotFoundError("ASR model not found. Please set AURA_MODEL_PATH environment variable.")

MODEL_PATHS = {"asr_english": find_model_path()}
MODEL_REGISTRY = MODEL_PATHS # Added alias to fix import error

# ============================================================================
# INTENTS
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

# ============================================================================
# VOCABULARY (OPTIMIZED)
# ============================================================================
class ASRVocabulary:
    VERB_NORMALIZATION = {
        # play
        "play": "play",
        "plays": "play",
        "playing": "play",
        "played": "play",

        # close
        "close": "close",
        "closes": "close",
        "closing": "close",
        "closed": "close",

        # pause
        "pause": "pause",
        "pauses": "pause",
        "paused": "pause",

        # resume
        "resume": "resume",
        "resumes": "resume",
        "resumed": "resume",

        # open
        "open": "open",
        "opens": "open",
        "opening": "open",
        "opened": "open",

        # type
        "type": "type",
        "types": "type",
        "typing": "type",
    }

    # Known websites with proper URLs
    KNOWN_WEBSITES = {
        # Indian sites
        'irctc': 'https://www.irctc.co.in',
        'paytm': 'https://paytm.com',
        'phonepe': 'https://www.phonepe.com',
        'phone pay': 'https://www.phonepe.com',
        'gpay': 'https://pay.google.com',
        'google pay': 'https://pay.google.com',
        'makemytrip': 'https://www.makemytrip.com',
        'make my trip': 'https://www.makemytrip.com',
        'flipkart': 'https://www.flipkart.com',
        'amazon': 'https://www.amazon.in',
        'myntra': 'https://www.myntra.com',
        'swiggy': 'https://www.swiggy.com',
        'zomato': 'https://www.zomato.com',
        'bookmyshow': 'https://in.bookmyshow.com',
        'book my show': 'https://in.bookmyshow.com',
        
        # Global sites
        'netflix': 'https://www.netflix.com',
        'youtube': 'https://www.youtube.com',
        'facebook': 'https://www.facebook.com',
        'twitter': 'https://www.twitter.com',
        'instagram': 'https://www.instagram.com',
        'reddit': 'https://www.reddit.com',
        'github': 'https://www.github.com',
        'stackoverflow': 'https://stackoverflow.com',
        'stack overflow': 'https://stackoverflow.com',
        'linkedin': 'https://www.linkedin.com',
        'gmail': 'https://mail.google.com',
        'outlook': 'https://outlook.live.com',
        'google': 'https://www.google.com',
        'bing': 'https://www.bing.com',
        'yahoo': 'https://www.yahoo.com',
    }
    
    # Applications that should NEVER be treated as websites
    DESKTOP_APPS = {
        'spotify', 'discord', 'slack', 'teams', 'zoom', 'skype',
        'notepad', 'calculator', 'calc', 'cmd', 'powershell',
        'chrome', 'firefox', 'edge', 'brave', 'opera', 'safari',
        'code', 'vscode', 'visual studio', 'pycharm', 'sublime',
        'photoshop', 'illustrator', 'premiere', 'after effects',
        'word', 'excel', 'powerpoint', 'outlook',
        'steam', 'epic', 'vlc', 'obs', 'audacity',
    }
    
    @classmethod
    def get_all_words(cls) -> Set[str]:
        return {
            # Core Actions
            "open", "close", "launch", "start", "quit", "exit", "terminate", "kill", "run", "shutdown",
            "search", "find", "google", "lookup", "query", "web", "internet", "browse", "go",
            "play", "pause", "stop", "resume", "next", "previous", "skip", "stream", 
            "music", "song", "video", "audio", "track", "album", "artist",
            "restart", "reboot", "lock", "sleep", "hibernate", "pc", "computer", "system", "machine",
            "type", "write", "enter", "input", "dictate", "text", "message",
            "press", "hit", "key", "button", "tap",
            "scroll", "move", "swipe", "navigate", "page", "up", "down", "left", "right", "top", "bottom",
            "save", "file", "document", "folder", "export", "backup",
            "undo", "redo", "cut", "copy", "paste", "delete", "remove",
            "yes", "no", "confirm", "cancel", "okay", "ok", "sure", "abort", "proceed",
            
            # Tab and window management
            "tab", "tabs", "window", "windows", "current", "active", "this", "that", "new",
            "website", "site", "page", "browser",
            
            # Context
            "it", "them", "again", "same", "last", "previous", "now",
            
            # Common apps
            "notepad", "chrome", "firefox", "edge", "brave", "safari", "opera",
            "spotify", "youtube", "discord", "slack", "teams", "zoom", "skype",
            "calculator", "calc", "cmd", "terminal", "powershell", "explorer",
            "word", "excel", "powerpoint", "outlook", "onedrive", "dropbox",
            "photoshop", "illustrator", "premiere",
            "code", "visual", "studio", "pycharm", "sublime",
            "steam", "epic", "games", "vlc", "obs",
            
            # Media platforms
            "netflix", "hulu", "amazon", "prime", "soundcloud",
            
            # Indian services
            "irctc", "paytm", "phonepe", "phone", "pay", "gpay", "makemytrip", "make", "trip",
            "flipkart", "myntra", "swiggy", "zomato", "ola", "uber", "bookmyshow", "book", "show",
            
            # Wake words
            "jarvis", "hey", "hi", "hello", "assistant",
            
            # Common phrases
            "how", "are", "you", "what", "where", "when", "why", "who", "which",
            "the", "a", "an", "this", "that", "these", "those",
            "for", "on", "in", "at", "to", "from", "with", "without", "about",
            "and", "or", "but", "so", "because",
            "my", "your", "his", "her", "its", "our", "their",
            "is", "am", "are", "was", "were", "be", "been", "being",
            "do", "does", "did", "have", "has", "had", "will", "would", "should", "could",
            "can", "may", "might", "must", "shall",
            "not", "never", "always", "sometimes", "usually",
            "please", "thank", "thanks", "sorry", "excuse",
            
            # Web/URL
            "dot", "com", "org", "net", "www", "http", "https",
            
            # Numbers
            "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "zero", "hundred", "thousand",
            "first", "second", "third", "fourth", "fifth",
            
            # Common adjectives
            "new", "old", "current", "recent", "latest", "next", "last", "previous",
            "all", "some", "any", "none", "few", "many", "much", "more", "less",
            "good", "bad", "best", "worst", "better", "worse",
            "big", "small", "large", "tiny", "huge",
            "fast", "slow", "quick",
            
            # Time
            "now", "today", "tomorrow", "yesterday", "later", "soon",
            "here", "there",
        }

    @classmethod
    def normalize_command(cls, text: str) -> str:
        text = text.lower().strip()

        # Remove fillers
        fillers = ["please", "could you", "can you", "would you"]
        for filler in fillers:
            text = re.sub(r'\b' + filler + r'\b', '', text)

        # 🔥 NEW: normalize verb forms
        tokens = text.split()
        normalized_tokens = []
        for token in tokens:
            normalized_tokens.append(
                cls.VERB_NORMALIZATION.get(token, token)
            )

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
        text_lower = re.sub(r'\bwebsite\b|\bsite\b', '', text_lower).strip()
        
        # Direct match
        if text_lower in cls.KNOWN_WEBSITES:
            return cls.KNOWN_WEBSITES[text_lower]
        
        # Partial match
        for site, url in cls.KNOWN_WEBSITES.items():
            if site in text_lower or text_lower in site:
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
            r'\b\w+\s*dot\s*org\b',
            r'\b\w+\s*dot\s*net\b',
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
        text = re.sub(r'\bopen\s+|\bgo\s+to\s+|\bsearch\s+', '', text).strip()
        
        if '.' not in text:
            for common in ['youtube', 'google', 'facebook', 'twitter', 'instagram']:
                if common in text:
                    text = common + '.com'
                    break
        
        if not text.startswith('http'):
            text = 'https://' + text
        
        return text

# ============================================================================
# COMMAND PATTERNS (PRIORITY-BASED)
# ============================================================================
COMMAND_PATTERNS = {
    # PRIORITY 1: Type command (must be first)
    r'^(?:type|write|enter|input|dictate)\s+(.+)$': 
        {"category": IntentCategory.INPUT, "action": "type", "priority": 1},
    
    # PRIORITY 2: Desktop app close (before tab close)
    r'^(?:close|quit|exit|kill|terminate)\s+(?:the\s+)?(\w+)(?:\s+app(?:lication)?)?$':
        {"category": IntentCategory.APP, "action": "close", "priority": 2},
    
    # PRIORITY 3: Tab management
    r'^close\s+(?:this|current|active|the)?\s*tab$':
        {"category": IntentCategory.TAB, "action": "close_current", "priority": 3},
    r'^(?:new|open)\s+tab$':
        {"category": IntentCategory.TAB, "action": "new_tab", "priority": 3},
    
    # PRIORITY 4: Known website opening
    r'^(?:open|go\s+to|visit|launch)\s+(.+)$':
        {"category": IntentCategory.WEB, "action": "open_site", "priority": 4},
    
    # PRIORITY 5: Play with platform
    r'^play\s+(.+?)\s+on\s+(spotify|youtube|netflix|soundcloud)$':
        {"category": IntentCategory.MEDIA, "action": "play", "priority": 5},
    
    # PRIORITY 6: Generic play (defaults to YouTube)
    r'^play\s+(.+)$': 
        {"category": IntentCategory.MEDIA, "action": "play", "priority": 6},
    
    # PRIORITY 7: Search
    r'^(?:search|google|find|lookup)\s+(?:for\s+)?(.+)$': 
        {"category": IntentCategory.SEARCH, "action": "search", "priority": 7},
    
    # Media controls
    r'^(?:pause|stop|resume)(?:\s+(?:music|playback|video))?$': 
        {"category": IntentCategory.MEDIA, "action": "control", "priority": 8},
    r'^(?:next|previous|skip)(?:\s+(?:song|track|video))?$': 
        {"category": IntentCategory.MEDIA, "action": "control", "priority": 8},
    
    # System commands
    r'^(?:shutdown|shut\s+down|turn\s+off|power\s+off)(?:\s+(?:computer|pc|system))?$': 
        {"category": IntentCategory.SYSTEM, "action": "shutdown", "priority": 9},
    r'^(?:restart|reboot)(?:\s+(?:computer|pc|system))?$': 
        {"category": IntentCategory.SYSTEM, "action": "restart", "priority": 9},
    r'^(?:lock|sleep)(?:\s+(?:computer|pc|system))?$': 
        {"category": IntentCategory.SYSTEM, "action": "lock", "priority": 9},
    
    # Navigation
    r'^(?:scroll|move)\s+(up|down|left|right)$': 
        {"category": IntentCategory.NAV, "action": "scroll", "priority": 10},
    r'^(?:page\s+)?(up|down)$':
        {"category": IntentCategory.NAV, "action": "page", "priority": 10},
    
    # File operations
    r'^save(?:\s+(?:file|document))?$': 
        {"category": IntentCategory.FILE, "action": "save", "priority": 10},
    r'^(?:undo|redo|copy|cut|paste|delete)$':
        {"category": IntentCategory.SYSTEM, "action": "edit", "priority": 10},
}
EXACT_PATTERNS = COMMAND_PATTERNS
# ============================================================================
# VOICE CONFIG
# ============================================================================
@dataclass
class VoiceConfig:
    sample_rate: int = 16000
    audio_gain: float = 4.0
    pause_threshold: float = 0.3
    min_command_length: int = 2
    
    wake_word: str = "jarvis"
    wake_words: List[str] = field(default_factory=lambda: [
        "jarvis", "jarvas", "jarves", "gyrus", "darvish",
        "hey jarvis", "ok jarvis", "yo jarvis"
    ])
    wake_word_confidence: float = 0.75
    wake_word_timeout: float = 3.5
    
    min_speech_energy: float = 200.0
    silence_threshold: int = 8
    min_speech_duration: float = 0.3
    
    command_buffer_time: float = 1.5
    confidence_threshold: float = 0.85
    
    confirm_destructive: bool = True
    critical_actions: List[str] = field(default_factory=lambda: [
        "shutdown", "restart", "delete", "power off" , "turn off"
    ])
    
    fuzzy_confirm_words: List[str] = field(default_factory=lambda: [
        "confirm", "yes", "sure", "okay", "ok", "yeah", "yep", "yup", "conform"
        "correct", "right", "proceed", "go", "do it", "continue"
    ])
    fuzzy_cancel_words: List[str] = field(default_factory=lambda: [
        "cancel", "no", "stop", "abort", "nevermind", "nope", "nah", "dont", "don't"
    ])
    
    queue_size: int = 3
    processing_timeout: float = 0.1
    agc_enabled: bool = True
    noise_reduction: bool = True

# ============================================================================
# MOUSE & GESTURE CONFIG (for vision service compatibility)
# ============================================================================
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
VOICE_CONFIG = VoiceConfig()
ASR_VOCABULARY = ASRVocabulary()
MOUSE_PHYSICS = MousePhysics()
GESTURE_CONFIG = GestureConfig()
CONVERSATION_CONTEXT = None  # For future context awareness

def validate_config() -> bool:
    """Validate critical paths and configuration"""
    try:
        model_path = MODEL_PATHS['asr_english']
        if not os.path.exists(model_path):
            print(f"[Config] ERROR: Model not found at {model_path}")
            return False
        print(f"[Config] ✓ Model found at {model_path}")
        return True
    except Exception as e:
        print(f"[Config] ERROR: {e}")
        return False

def get_config_summary() -> Dict:
    return {
        "model_path": MODEL_PATHS['asr_english'],
        "sample_rate": VOICE_CONFIG.sample_rate,
        "wake_word": VOICE_CONFIG.wake_word,
        "root_dir": str(ROOT_DIR),
    }
# ============================
# GLOBAL CONTEXT (REQUIRED)
# ============================
CONVERSATION_CONTEXT = {}