"""
INTENT REGISTRY — Single Source of Truth for All Intents
=========================================================
[NEW: Phase 1 Architecture Fix]

All intent definitions live here. Import INTENTS or get_intent() everywhere
instead of duplicating intent sets across core_patch.py, decision.py, etc.
"""

from typing import Any, Dict, FrozenSet, List, Optional

# ════════════════════════════════════════════════════════════════════════════
# INTENT DEFINITIONS
# ════════════════════════════════════════════════════════════════════════════

# [NEW: Phase 1 Architecture Fix] — Full intent catalogue
INTENTS: Dict[str, Dict[str, Any]] = {
    # ── Conversational ────────────────────────────────────────────────────
    "greet": {
        "description": "User greets Jarvis",
        "slots": [],
        "category": "conversational",
        "direct_answer": True,
    },
    "thank": {
        "description": "User thanks Jarvis",
        "slots": [],
        "category": "conversational",
        "direct_answer": True,
    },
    "cancel": {
        "description": "User cancels current action",
        "slots": [],
        "category": "conversational",
        "direct_answer": True,
    },
    "introduce_self": {
        "description": "User introduces themselves",
        "slots": ["name"],
        "category": "conversational",
        "direct_answer": True,
    },
    "express_preference": {
        "description": "User states a preference",
        "slots": ["fact", "preference"],
        "category": "conversational",
        "direct_answer": True,
    },

    # ── Knowledge / Q&A ───────────────────────────────────────────────────
    "answer_question": {
        "description": "User asks a general question",
        "slots": ["query"],
        "category": "knowledge",
        "direct_answer": False,
        "context_free": True,
    },
    "quick_answer": {
        "description": "Simple factual answer from AI brain, no browser needed",
        "slots": ["query"],
        "category": "knowledge",
        "direct_answer": True,
    },
    "recall_fact": {
        "description": "Look up something stored in memory",
        "slots": ["query"],
        "category": "knowledge",
        "direct_answer": True,
    },
    "guided_recommendation": {
        "description": "User wants a guided product/service recommendation",
        "slots": ["query", "budget", "brand", "category"],
        "category": "knowledge",
        "direct_answer": False,
        "context_free": True,
    },
    "deep_research": {
        "description": "Multi-step web research task",
        "slots": ["query"],
        "category": "knowledge",
        "direct_answer": False,
    },
    "conversation": {
        "description": "Open-ended conversation",
        "slots": [],
        "category": "knowledge",
        "direct_answer": False,
    },

    # ── App Control ───────────────────────────────────────────────────────
    "open_app": {
        "description": "Open a desktop or web application",
        "slots": ["name", "app", "app_name"],
        "required_slots": ["app"],
        "category": "system",
        "direct_answer": False,
    },
    "close_app": {
        "description": "Close a running application",
        "slots": ["name", "app", "app_name"],
        "required_slots": ["app"],
        "category": "system",
        "direct_answer": False,
    },
    "focus_app": {
        "description": "Bring an app to foreground",
        "slots": ["name", "app"],
        "category": "system",
        "direct_answer": False,
    },
    "minimize_app": {
        "description": "Minimize an app window",
        "slots": ["name", "app"],
        "category": "system",
        "direct_answer": False,
    },
    "maximize_app": {
        "description": "Maximize an app window",
        "slots": ["name", "app"],
        "category": "system",
        "direct_answer": False,
    },
    "smart_open": {
        "description": "Intelligently open an app or URL",
        "slots": ["target"],
        "category": "system",
        "direct_answer": False,
    },

    # ── Media ─────────────────────────────────────────────────────────────
    "play_media": {
        "description": "Play a song, video, or playlist",
        "slots": ["song", "platform", "artist"],
        "required_slots": ["song"],
        "category": "media",
        "direct_answer": False,
    },
    "pause_media": {
        "description": "Pause current media",
        "slots": [],
        "category": "media",
        "direct_answer": False,
    },
    "resume_media": {
        "description": "Resume paused media",
        "slots": [],
        "category": "media",
        "direct_answer": False,
    },
    "next_track": {
        "description": "Skip to next track",
        "slots": [],
        "category": "media",
        "direct_answer": False,
    },
    "previous_track": {
        "description": "Go to previous track",
        "slots": [],
        "category": "media",
        "direct_answer": False,
    },

    # ── Browser ───────────────────────────────────────────────────────────
    "search_web": {
        "description": "Perform a web search",
        "slots": ["query"],
        "category": "browser",
        "direct_answer": False,
    },
    "open_website": {
        "description": "Open a specific website",
        "slots": ["url", "site"],
        "category": "browser",
        "direct_answer": False,
    },
    "open_url": {
        "description": "Open a specific URL",
        "slots": ["url"],
        "category": "browser",
        "direct_answer": False,
    },
    "click_result": {
        "description": "Click a search result",
        "slots": ["index"],
        "category": "browser",
        "direct_answer": False,
    },
    "browser_navigation": {
        "description": "Navigate the browser (back, forward, refresh)",
        "slots": ["action"],
        "category": "browser",
        "direct_answer": False,
    },
    "close_tab": {
        "description": "Close the current browser tab",
        "slots": [],
        "category": "browser",
        "direct_answer": False,
        "context_free": True,
    },
    "new_tab": {
        "description": "Open a new browser tab",
        "slots": [],
        "category": "browser",
        "direct_answer": False,
        "context_free": True,
    },
    "read_page": {
        "description": "Read the content of the current page",
        "slots": [],
        "category": "browser",
        "direct_answer": False,
        "context_free": True,
    },
    "page_summary": {
        "description": "Summarize the current page",
        "slots": [],
        "category": "browser",
        "direct_answer": False,
        "context_free": True,
    },
    "scroll": {
        "description": "Scroll the current page",
        "slots": ["direction", "amount"],
        "category": "browser",
        "direct_answer": False,
    },
    "type_text": {
        "description": "Type text into focused field",
        "slots": ["text"],
        "category": "browser",
        "direct_answer": False,
    },

    # ── Communication ─────────────────────────────────────────────────────
    "send_message": {
        "description": "Send a message to a contact",
        "slots": ["contact", "message_content"],
        "required_slots": ["contact", "message_content"],
        "category": "communication",
        "direct_answer": False,
    },
    "make_call": {
        "description": "Call a contact",
        "slots": ["contact"],
        "required_slots": ["contact"],
        "category": "communication",
        "direct_answer": False,
    },
    "compose_email": {
        "description": "Compose and send an email",
        "slots": ["contact", "subject", "body"],
        "category": "communication",
        "direct_answer": False,
    },

    # ── System ────────────────────────────────────────────────────────────
    "system_action": {
        "description": "Perform a system-level action (volume, brightness, etc.)",
        "slots": ["action_type", "setting", "value", "target"],
        "category": "system",
        "direct_answer": False,
    },
    "set_reminder": {
        "description": "Set a reminder",
        "slots": ["reminder_text", "time"],
        "required_slots": ["reminder_text", "time"],
        "category": "system",
        "direct_answer": False,
    },
    "take_screenshot": {
        "description": "Take a screenshot",
        "slots": [],
        "category": "system",
        "direct_answer": False,
    },
    "lock": {
        "description": "Lock the system",
        "slots": [],
        "category": "system",
        "direct_answer": False,
    },
    "shutdown": {
        "description": "Shut down the system",
        "slots": [],
        "category": "system",
        "direct_answer": False,
    },
    "restart": {
        "description": "Restart the system",
        "slots": [],
        "category": "system",
        "direct_answer": False,
    },

    # ── Notepad / Writing ─────────────────────────────────────────────────
    "open_notepad_write": {
        "description": "Open notepad and write text",
        "slots": ["text"],
        "required_slots": ["text"],
        "category": "system",
        "direct_answer": False,
    },

    # ── Fallback ──────────────────────────────────────────────────────────
    "unknown": {
        "description": "Intent could not be determined",
        "slots": [],
        "category": "fallback",
        "direct_answer": False,
    },
}

# ════════════════════════════════════════════════════════════════════════════
# COMPUTED SETS (replaces per-file frozensets)
# ════════════════════════════════════════════════════════════════════════════

# [NEW: Phase 1 Architecture Fix] — Derived from INTENTS, always in sync
ALL_INTENT_NAMES: FrozenSet[str] = frozenset(INTENTS.keys())

DIRECT_ANSWER_INTENTS: FrozenSet[str] = frozenset(
    k for k, v in INTENTS.items() if v.get("direct_answer")
)

CONTEXT_FREE_INTENTS: FrozenSet[str] = frozenset(
    k for k, v in INTENTS.items() if v.get("context_free")
)

SYSTEM_INTENTS: FrozenSet[str] = frozenset(
    k for k, v in INTENTS.items() if v.get("category") == "system"
)

BROWSER_INTENTS: FrozenSet[str] = frozenset(
    k for k, v in INTENTS.items() if v.get("category") == "browser"
)

MEDIA_INTENTS: FrozenSet[str] = frozenset(
    k for k, v in INTENTS.items() if v.get("category") == "media"
)

# Intents that always need specific slots before execution
ALWAYS_CLARIFY: Dict[str, tuple] = {
    k: tuple(v["required_slots"])
    for k, v in INTENTS.items()
    if v.get("required_slots")
}


# ════════════════════════════════════════════════════════════════════════════
# LOOKUP HELPERS
# ════════════════════════════════════════════════════════════════════════════

def get_intent(name: str) -> Optional[Dict[str, Any]]:
    """
    Return the intent definition dict for a given name.
    Returns None if not found.

    Usage:
        from agent.intent_registry import get_intent
        defn = get_intent("open_app")
        slots = defn["slots"]  # ["name", "app", "app_name"]
    """
    return INTENTS.get(name)


def is_known(name: str) -> bool:
    """Return True if the intent name is registered."""
    return name in INTENTS


def get_required_slots(intent_name: str) -> List[str]:
    """Return required slots for an intent, or empty list."""
    defn = INTENTS.get(intent_name, {})
    return list(defn.get("required_slots", []))


def get_category(intent_name: str) -> Optional[str]:
    """Return the category of an intent."""
    return INTENTS.get(intent_name, {}).get("category")
