"""
COMMAND SPLITTER + INTELLIGENT CLARIFIER
=========================================
GAP 3: Multi-command support — splits compound voice commands into a
        sequential list for the agent to execute one by one.

GAP 6: Intent-specific clarification trees — instead of generic
        "I need more info", Jarvis asks exactly the right follow-up
        based on what's missing and what the intent is.

Usage (splitter):
    splitter = CommandSplitter()
    commands = splitter.split("open spotify and play Starboy then search for lyrics")
    # → ["open spotify", "play Starboy", "search for lyrics"]

Usage (clarifier):
    clarifier = SmartClarifier()
    q = clarifier.get_question("play_media", {"platform": "spotify"}, missing=["song"])
    # → "What would you like me to play on Spotify, Sir?"
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — COMMAND SPLITTER
# ══════════════════════════════════════════════════════════════════════════

# Patterns that signal a new command is starting within a sentence
_SPLIT_PATTERNS = [
    r"\band\s+also\b",
    r"\band\s+then\b",
    r"\band\b",
    r"\bthen\b",
    r"\bafter\s+that\b",
    r"\bafterwards\b",
    r"\balso\b",
    r"\bnext\s+(?=open|close|play|search|type|scroll|send|call|lock|screenshot)\b",
]

_SPLIT_RE = re.compile(
    "|".join(_SPLIT_PATTERNS),
    re.IGNORECASE
)

# Command-starting verbs — used to validate each split fragment
COMMAND_VERBS = {
    "open", "close", "play", "pause", "stop", "search", "type",
    "scroll", "click", "read", "research", "remember", "recall",
    "send", "call", "message", "lock", "screenshot", "shutdown",
    "restart", "resume", "next", "previous", "skip", "volume",
    "write", "create", "delete", "find", "show", "tell", "what",
    "who", "when", "where", "why", "how",
}


class CommandSplitter:
    """
    Splits compound voice commands into individual sequential commands.

    "open spotify and play Starboy then search for lyrics"
    → ["open spotify", "play Starboy", "search for lyrics"]

    Safe: if no valid split is found, returns the original as a single command.
    """

    def split(self, text: str) -> List[str]:
        """Split text into a list of individual commands."""
        if not text or not text.strip():
            return []

        text = text.strip()

        # Quick exit: no splitter keywords present
        if not _SPLIT_RE.search(text):
            return [text]

        parts = _SPLIT_RE.split(text)
        validated = self._validate_parts(parts)

        if len(validated) > 1:
            logger.info(f" Split {len(validated)} commands: {validated}")

        return validated if validated else [text]

    def _validate_parts(self, parts: List[str]) -> List[str]:
        """
        Validate and clean split fragments.
        Fragments that are too short or don't start with a verb are
        merged back into the previous command.
        """
        result: List[str] = []

        for raw_part in parts:
            part = raw_part.strip()
            if not part:
                continue

            words = part.split()

            # Looks like a real command
            if len(words) >= 2 and words[0].lower() in COMMAND_VERBS:
                result.append(part)

            # Single word that's a command verb — too ambiguous, skip
            elif len(words) == 1 and words[0].lower() in COMMAND_VERBS:
                continue

            # Fragment doesn't start with a verb — attach to previous
            elif result:
                result[-1] = result[-1] + " " + part

            else:
                result.append(part)

        return result


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — SMART CLARIFIER (Intent-specific question trees)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ClarificationTree:
    """Defines what to ask for a given intent when slots are missing."""
    # slot → question template (supports {entity} placeholders)
    questions: Dict[str, str]
    # Which slots MUST be filled before execution (in order)
    required_order: List[str]
    # Slots that can be filled from memory/defaults (don't ask about these)
    auto_fillable: List[str]


# ── INTENT-SPECIFIC QUESTION TREES ────────────────────────────────────────

CLARIFICATION_TREES: Dict[str, ClarificationTree] = {

    "play_media": ClarificationTree(
        questions={
            "song":     "What would you like me to play{on_platform}, Sir?",
            "platform": "Which platform would you like — Spotify or YouTube, Sir?",
            "artist":   "Did you have a specific artist in mind, Sir?",
        },
        required_order=["song", "platform"],
        auto_fillable=["platform"],  # Fill from memory: preferred_music_platform
    ),

    "make_call": ClarificationTree(
        questions={
            "contact":  "Who would you like me to call, Sir?",
            "platform": "Should I call via Discord or WhatsApp, Sir?",
        },
        required_order=["contact", "platform"],
        auto_fillable=["platform"],
    ),

    "send_message": ClarificationTree(
        questions={
            "contact":         "Who should I send the message to, Sir?",
            "message_content": "What would you like me to say, Sir?",
            "platform":        "Which platform — WhatsApp, Discord, or email, Sir?",
        },
        required_order=["contact", "message_content", "platform"],
        auto_fillable=["platform"],
    ),

    "set_reminder": ClarificationTree(
        questions={
            "reminder_text": "What should I remind you about, Sir?",
            "time":          "When should I remind you — in how many minutes, Sir?",
        },
        required_order=["reminder_text", "time"],
        auto_fillable=[],
    ),

    "open_notepad_write": ClarificationTree(
        questions={
            "text": "What would you like me to write, Sir?",
        },
        required_order=["text"],
        auto_fillable=[],
    ),

    "deep_research": ClarificationTree(
        questions={
            "topic":   "What topic would you like me to research, Sir?",
            "purpose": "Is this for a quick summary or a deep dive, Sir?",
            "budget":  "Do you have a budget constraint in mind, Sir?",
        },
        required_order=["topic"],
        auto_fillable=["purpose", "budget"],
    ),

    "search_web": ClarificationTree(
        questions={
            "query":    "What would you like me to search for, Sir?",
            "platform": "Any preferred platform — Google, YouTube, or Reddit, Sir?",
        },
        required_order=["query"],
        auto_fillable=["platform"],
    ),

    "open_app": ClarificationTree(
        questions={
            "app": "Which application would you like me to open, Sir?",
        },
        required_order=["app"],
        auto_fillable=[],
    ),

    "close_app": ClarificationTree(
        questions={
            "app": "Which application should I close, Sir?",
        },
        required_order=["app"],
        auto_fillable=[],
    ),

    "type_text": ClarificationTree(
        questions={
            "text": "What would you like me to type, Sir?",
        },
        required_order=["text"],
        auto_fillable=[],
    ),

    "create_file": ClarificationTree(
        questions={
            "filename": "What should the file be named, Sir?",
            "content":  "What would you like me to write in it, Sir?",
        },
        required_order=["filename", "content"],
        auto_fillable=[],
    ),

    "open_website": ClarificationTree(
        questions={
            "url": "Which website would you like me to open, Sir?",
        },
        required_order=["url"],
        auto_fillable=[],
    ),
}


class SmartClarifier:
    """
    Generates context-aware, intent-specific clarification questions.

    Unlike a generic "I need more info, Sir", this produces natural
    questions that reference what Jarvis already knows.

    Example:
        entities = {"platform": "spotify"}
        missing  = ["song"]
        → "What would you like me to play on Spotify, Sir?"
    """

    def get_question(
        self,
        intent_name: str,
        entities: Dict,
        missing: List[str],
        memory_prefs: Optional[Dict] = None,
    ) -> Tuple[str, List[str]]:
        """
        Get the best clarification question for this situation.

        Args:
            intent_name:  Intent string (e.g. "play_media")
            entities:     Already-known entities
            missing:      List of missing required slots
            memory_prefs: User preferences from memory store

        Returns:
            (question_str, still_missing_slots)
            still_missing after auto-filling from memory.
        """
        memory_prefs = memory_prefs or {}

        tree = CLARIFICATION_TREES.get(intent_name)
        if not tree:
            return self._generic_question(intent_name, missing), missing

        # Try to auto-fill slots from memory before asking
        still_missing = []
        for slot in missing:
            if slot in tree.auto_fillable:
                filled = self._try_fill_from_memory(slot, entities, memory_prefs)
                if filled:
                    entities[slot] = filled
                    logger.info(f" Auto-filled '{slot}' from memory: {filled}")
                    continue
            still_missing.append(slot)

        if not still_missing:
            return "", []

        # Ask about the first missing slot, in the defined order
        ordered_missing = [s for s in tree.required_order if s in still_missing]
        if not ordered_missing:
            ordered_missing = still_missing

        slot_to_ask = ordered_missing[0]
        template = tree.questions.get(slot_to_ask, f"Could you provide the {slot_to_ask}, Sir?")
        question = self._render_template(template, entities, intent_name)

        return question, still_missing

    def get_research_questions(
        self,
        topic: str,
        intent_name: str = "deep_research"
    ) -> List[str]:
        """
        Generate a sequence of follow-up questions for research intent.

        For "best laptop" this produces:
          ["What is your budget, Sir?",
           "What will you primarily use it for, Sir?",
           "Do you prefer Windows or a specific OS, Sir?"]
        """
        RESEARCH_QUESTION_SETS = {
            "laptop":       ["What is your budget, Sir?",
                             "What will you use it for — gaming, work, or general use, Sir?",
                             "Do you have a brand preference, Sir?"],
            "phone":        ["What is your budget, Sir?",
                             "Do you prefer Android or iOS, Sir?",
                             "Any must-have features, Sir?"],
            "restaurant":   ["What cuisine are you in the mood for, Sir?",
                             "Any location preference, Sir?",
                             "Dining in or delivery, Sir?"],
            "hotel":        ["What are your travel dates, Sir?",
                             "Any location or area preference, Sir?",
                             "What is your budget per night, Sir?"],
            "movie":        ["Any genre preference, Sir?",
                             "Are you looking for something recent or a classic, Sir?"],
            "investment":   ["What is your investment horizon, Sir?",
                             "What is your risk tolerance, Sir?",
                             "Any sectors you want to focus on, Sir?"],
        }

        topic_lower = topic.lower()
        for keyword, questions in RESEARCH_QUESTION_SETS.items():
            if keyword in topic_lower:
                return questions

        # Generic research follow-ups
        return [
            f"Could you narrow that down a bit — what aspect of '{topic}' interests you most, Sir?",
            "Is this for personal use or professional research, Sir?",
        ]

    def _try_fill_from_memory(
        self, slot: str, entities: Dict, prefs: Dict
    ) -> Optional[str]:
        """Try to fill a slot from memory preferences."""
        MEMORY_MAPPING = {
            "platform": ["preferred_music_platform", "preferred_platform"],
            "contact":  ["last_contact", "frequent_contact"],
        }
        keys = MEMORY_MAPPING.get(slot, [])
        for k in keys:
            val = prefs.get(k)
            if val:
                return val
        return None

    def _render_template(
        self, template: str, entities: Dict, intent_name: str
    ) -> str:
        """Fill template placeholders with known entity values."""
        platform = entities.get("platform", "")
        song = entities.get("song", "")
        contact = entities.get("contact", "")
        app = entities.get("app", "")

        on_platform = f" on {platform.title()}" if platform else ""
        to_contact  = f" to {contact}" if contact else ""
        on_app      = f" on {app}" if app else ""

        try:
            return template.format(
                on_platform=on_platform,
                to_contact=to_contact,
                on_app=on_app,
                platform=platform,
                song=song,
                contact=contact,
                app=app,
            )
        except KeyError:
            return template

    def _generic_question(self, intent_name: str, missing: List[str]) -> str:
        """Fallback for intents without a defined question tree."""
        slot = missing[0] if missing else "information"
        readable = slot.replace("_", " ")
        intent_readable = intent_name.replace("_", " ")
        return (
            f"I'd like to {intent_readable}, but I need the {readable} first, Sir."
        )


# ── MODULE-LEVEL SINGLETONS ────────────────────────────────────────────────

_splitter  = CommandSplitter()
_clarifier = SmartClarifier()


def split_commands(text: str) -> List[str]:
    """Split a raw command string into individual commands."""
    return _splitter.split(text)


def get_clarification(
    intent_name: str,
    entities: Dict,
    missing: List[str],
    memory_prefs: Optional[Dict] = None,
) -> Tuple[str, List[str]]:
    """Get a clarification question for missing slots."""
    return _clarifier.get_question(intent_name, entities, missing, memory_prefs)
