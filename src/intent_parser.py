"""
Intent Parser - AI AGENT VERSION
Minimal pattern matching - AI brain handles everything else
"""
from .config import Intent, IntentCategory, ASRVocabulary

def parse_intent(text: str) -> Intent:
    """
    Simple intent parser - mostly for backward compatibility.
    The AI brain handles all actual intent understanding.
    
    This just does basic classification for the AI.
    """
    text = text.strip()
    
    if not text:
        return Intent(
            category=IntentCategory.UNKNOWN,
            action="unknown",
            confidence=0.0,
            source_text=text
        )
    
    # Return a generic intent - AI brain will handle it
    return Intent(
        category=IntentCategory.CONVERSATION,
        action="chat",
        entities={"text": text},
        confidence=1.0,
        source_text=text
    )

def validate_intent(intent: Intent) -> bool:
    """Validate intent has minimum required data"""
    return intent.confidence > 0.0

def get_supported_commands():
    """Return info about AI agent capabilities"""
    return [
        "AI Agent Mode - All commands processed by Llama Brain",
        "Examples:",
        "- 'research python programming'",
        "- 'what's the weather in Haridwar today?'",
        "- 'open Chrome'",
        "- 'search for AI news'",
        "- 'play music on Spotify'"
    ]

def is_command_supported(text: str) -> bool:
    """In AI agent mode, all commands are supported"""
    return bool(text.strip())