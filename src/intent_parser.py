"""
Intent Parser (V21.0 - PRODUCTION READY)
Features: Accurate app vs website detection, proper intent routing
"""
import re
from typing import Optional
from .config import Intent, IntentCategory, COMMAND_PATTERNS, ASRVocabulary

class SmartIntentParser:
    """Advanced intent parser with context awareness"""
    
    def __init__(self):
        # Sort patterns by priority
        self.sorted_patterns = sorted(
            COMMAND_PATTERNS.items(),
            key=lambda x: x[1].get("priority", 999)
        )
        # Compile regex patterns
        self.compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), info)
            for pattern, info in self.sorted_patterns
        ]
    
    def parse(self, text: str) -> Intent:
        """
        Parse command text into structured intent with smart routing.
        """
        original_text = text
        text = ASRVocabulary.normalize_command(text)
        
        if not text or len(text.split()) < 1:
            return Intent(IntentCategory.UNKNOWN, "unknown", confidence=0.0)
        
        # ============================================================
        # PRIORITY 1: TYPE COMMAND (highest priority)
        # ============================================================
        type_pattern = r'^(?:type|write|enter|input|dictate)\s+(.+)$'
        match = re.search(type_pattern, text, re.IGNORECASE)
        if match:
            text_to_type = match.group(1).strip()
            return Intent(
                category=IntentCategory.INPUT,
                action="type",
                entities={"text": text_to_type},
                confidence=0.98,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 2: CLOSE COMMANDS (app vs tab distinction)
        # ============================================================
        close_pattern = r'^(?:close|quit|exit|kill|terminate)\s+(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?$'
        match = re.search(close_pattern, text, re.IGNORECASE)
        if match:
            target = match.group(1).strip()
            
            # Check if it's "this tab" or "current tab"
            if re.search(r'\b(?:this|current|active)\s+tab\b', text, re.IGNORECASE):
                return Intent(
                    category=IntentCategory.TAB,
                    action="close_current",
                    entities={},
                    confidence=0.98,
                    source_text=original_text
                )
            
            # Check if target is a known desktop application
            if ASRVocabulary.is_desktop_app(target):
                return Intent(
                    category=IntentCategory.APP,
                    action="close",
                    entities={"app_name": target},
                    confidence=0.95,
                    source_text=original_text
                )
            
            # Check if target is a known website (close tab)
            if ASRVocabulary.get_known_website(target):
                return Intent(
                    category=IntentCategory.TAB,
                    action="close_named",
                    entities={"tab_name": target},
                    confidence=0.90,
                    source_text=original_text
                )
            
            # Default: treat as app close
            return Intent(
                category=IntentCategory.APP,
                action="close",
                entities={"app_name": target},
                confidence=0.85,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 3: TAB MANAGEMENT
        # ============================================================
        # Close current tab
        if re.search(r'^close\s+(?:this|current|active|the)?\s*tab$', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.TAB,
                action="close_current",
                entities={},
                confidence=0.98,
                source_text=original_text
            )
        
        # New tab
        if re.search(r'^(?:new|open)\s+tab$', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.TAB,
                action="new_tab",
                entities={},
                confidence=0.98,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 4: OPEN COMMANDS (website vs app)
        # ============================================================
        open_pattern = r'^(?:open|go\s+to|visit|launch)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|website|site))?$'
        match = re.search(open_pattern, text, re.IGNORECASE)
        if match:
            target = match.group(1).strip()
            
            # Check if it's a known website
            known_url = ASRVocabulary.get_known_website(target)
            if known_url:
                return Intent(
                    category=IntentCategory.WEB,
                    action="open_url",
                    entities={"url": known_url, "site_name": target},
                    confidence=0.98,
                    source_text=original_text
                )
            
            # Check if it's URL-like
            if ASRVocabulary.is_url_like(target):
                url = ASRVocabulary.extract_url(target)
                return Intent(
                    category=IntentCategory.WEB,
                    action="open_url",
                    entities={"url": url},
                    confidence=0.95,
                    source_text=original_text
                )
            
            # Check if explicitly marked as website
            if re.search(r'\b(?:website|site)\b', text, re.IGNORECASE):
                url = ASRVocabulary.extract_url(target)
                return Intent(
                    category=IntentCategory.WEB,
                    action="open_url",
                    entities={"url": url},
                    confidence=0.90,
                    source_text=original_text
                )
            
            # Default: treat as application
            return Intent(
                category=IntentCategory.APP,
                action="open",
                entities={"app_name": target},
                confidence=0.90,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 5: PLAY ON PLATFORM
        # ============================================================
        play_platform_pattern = r'^play\s+(.+?)\s+on\s+(spotify|youtube|netflix|soundcloud)$'
        match = re.search(play_platform_pattern, text, re.IGNORECASE)
        if match:
            media_name = match.group(1).strip()
            platform = match.group(2).strip().lower()
            return Intent(
                category=IntentCategory.MEDIA,
                action="play_on_platform",
                entities={"media_name": media_name, "platform": platform},
                confidence=0.98,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 6: GENERIC PLAY (defaults to YouTube)
        # ============================================================
        play_pattern = r'^play\s+(.+)$'
        match = re.search(play_pattern, text, re.IGNORECASE)
        if match:
            media_name = match.group(1).strip()
            return Intent(
                category=IntentCategory.MEDIA,
                action="play",
                entities={"media_name": media_name, "platform": "youtube"},
                confidence=0.95,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 7: SEARCH
        # ============================================================
        search_pattern = r'^(?:search|google|find|lookup)\s+(?:for\s+)?(.+)$'
        match = re.search(search_pattern, text, re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            
            # Check if platform specified in query
            platform_map = {
                'youtube': ['youtube', 'yt'],
                'spotify': ['spotify'],
                'amazon': ['amazon'],
                'reddit': ['reddit'],
            }
            
            for platform, keywords in platform_map.items():
                for keyword in keywords:
                    if keyword in query.lower():
                        # Remove platform from query
                        query_clean = re.sub(r'\s+on\s+' + keyword, '', query, flags=re.IGNORECASE)
                        query_clean = re.sub(keyword, '', query_clean, flags=re.IGNORECASE).strip()
                        
                        return Intent(
                            category=IntentCategory.SEARCH,
                            action="search",
                            entities={"query": query_clean, "platform": platform},
                            confidence=0.95,
                            source_text=original_text
                        )
            
            return Intent(
                category=IntentCategory.SEARCH,
                action="search",
                entities={"query": query, "platform": "google"},
                confidence=0.95,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 8: MEDIA CONTROLS
        # ============================================================
        # Pause/resume
        if re.search(r'^(?:pause|stop|resume)(?:\s+(?:music|playback|video))?$', text, re.IGNORECASE):
            action_word = text.split()[0].lower()
            return Intent(
                category=IntentCategory.MEDIA,
                action="control",
                entities={"command": action_word},
                confidence=0.95,
                source_text=original_text
            )
        
        # Next/previous
        if re.search(r'^(?:next|previous|skip|back)(?:\s+(?:song|track|video))?$', text, re.IGNORECASE):
            action_word = text.split()[0].lower()
            return Intent(
                category=IntentCategory.MEDIA,
                action="control",
                entities={"command": action_word},
                confidence=0.95,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 9: SYSTEM COMMANDS
        # ============================================================
        if re.search(r'^(?:shutdown|shut\s+down|turn\s+off|power\s+off)', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.SYSTEM,
                action="shutdown",
                entities={},
                confidence=0.98,
                source_text=original_text
            )
        
        if re.search(r'^(?:restart|reboot)', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.SYSTEM,
                action="restart",
                entities={},
                confidence=0.98,
                source_text=original_text
            )
        
        if re.search(r'^(?:lock|sleep)', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.SYSTEM,
                action="lock",
                entities={},
                confidence=0.98,
                source_text=original_text
            )
        
        # ============================================================
        # PRIORITY 10: NAVIGATION & FILE OPS
        # ============================================================
        # Scroll
        scroll_pattern = r'^(?:scroll|move)\s+(up|down|left|right)$'
        match = re.search(scroll_pattern, text, re.IGNORECASE)
        if match:
            direction = match.group(1).lower()
            return Intent(
                category=IntentCategory.NAV,
                action="scroll",
                entities={"direction": direction},
                confidence=0.95,
                source_text=original_text
            )
        
        # Page up/down
        page_pattern = r'^(?:page\s+)?(up|down)$'
        match = re.search(page_pattern, text, re.IGNORECASE)
        if match:
            direction = match.group(1).lower()
            return Intent(
                category=IntentCategory.NAV,
                action="page",
                entities={"direction": direction},
                confidence=0.95,
                source_text=original_text
            )
        
        # Save
        if re.search(r'^save(?:\s+(?:file|document))?$', text, re.IGNORECASE):
            return Intent(
                category=IntentCategory.FILE,
                action="save",
                entities={},
                confidence=0.95,
                source_text=original_text
            )
        
        # Edit operations
        edit_ops = ['undo', 'redo', 'copy', 'cut', 'paste', 'delete']
        for op in edit_ops:
            if re.search(r'^' + op + r'$', text, re.IGNORECASE):
                return Intent(
                    category=IntentCategory.SYSTEM,
                    action="edit",
                    entities={"operation": op},
                    confidence=0.95,
                    source_text=original_text
                )
        
        # ============================================================
        # NO MATCH
        # ============================================================
        return Intent(
            IntentCategory.UNKNOWN,
            "unknown",
            confidence=0.0,
            source_text=original_text
        )
    
    def validate_intent(self, intent: Intent) -> bool:
        """Validate intent has minimum required data"""
        if intent.confidence < 0.5:
            return False
        
        if intent.category == IntentCategory.APP:
            return bool(intent.entities.get("app_name"))
        
        elif intent.category == IntentCategory.INPUT:
            return bool(intent.entities.get("text"))
        
        elif intent.category == IntentCategory.WEB:
            return bool(intent.entities.get("url"))
        
        elif intent.category == IntentCategory.TAB:
            if intent.action in ["close_current", "new_tab"]:
                return True
            return bool(intent.entities.get("tab_name"))
        
        elif intent.category == IntentCategory.MEDIA:
            if intent.action == "control":
                return True
            return bool(intent.entities.get("media_name") or intent.entities.get("query"))
        
        return True

# ============================================================
# SINGLETON & PUBLIC API
# ============================================================
PARSER = SmartIntentParser()

def parse_intent(text: str) -> Intent:
    """Parse text into Intent object"""
    return PARSER.parse(text)

def validate_intent(intent: Intent) -> bool:
    """Validate intent is actionable"""
    return PARSER.validate_intent(intent)

def get_supported_commands():
    """Return list of supported command patterns"""
    return list(COMMAND_PATTERNS.keys())

def is_command_supported(text: str) -> bool:
    """Check if text matches any known pattern"""
    intent = parse_intent(text)
    return intent.category != IntentCategory.UNKNOWN