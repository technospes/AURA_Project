"""
VOICE UX — Instant Acknowledgments + Execution Feedback
=========================================================
CRITICAL FIX: The old ux.py called say() which called speak() which
made a network round-trip to edge_tts. This caused the 1-2 second pause
after "Jarvis" that was killing the UX.

THIS version:
  1. Uses only pre-cached phrases (already in JarvisVoice._cache)
  2. Never makes a network call for acknowledgments
  3. ack_intent() fires BEFORE the plan executes (parallel with planning)
  4. All timing is < 20ms

The pre-warm list in voice_io.py covers all ack phrases.
"""

import logging
import random
import threading
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── ACK PHRASES ───────────────────────────────────────────────────────────
# KEEP THESE SHORT. Every char = latency.
# These MUST be in voice_io._PREWARM_PHRASES or they won't be instant.

WAKE_ACKS: List[str] = [
    "Yes?",
    "Listening.",
    "Go ahead.",
]

# Intent-specific acks — spoken BEFORE execution starts
EXECUTION_ACKS: Dict[str, List[str]] = {
    "open_app":          ["Opening.", "On it."],
    "close_app":         ["Closing."],
    "play_media":        ["Playing."],
    "search_web":        ["Searching."],
    "deep_research":     ["On it. This may take a moment."],
    "type_text":         ["Typing."],
    "send_message":      ["Sending."],
    "make_call":         ["Calling."],
    "take_screenshot":   ["Done."],
    "lock":              ["Locking."],
    "shutdown":          ["Shutting down."],
    "restart":           ["Restarting."],
    "scroll":            ["Scrolling."],
    "new_tab":           ["Done."],
    "close_tab":         ["Done."],
    "read_page":         ["Reading."],
    "set_reminder":      ["Reminder set."],
    "quick_answer":      ["One moment."],
    "recall_fact":       ["One moment."],

    # These respond immediately with full content — no separate ack
    "greet":             [],
    "thank":             [],
    "cancel":            [],
    "express_preference": [],
    "remember_fact":     [],
    "introduce_self":    [],

    "_default":          ["On it."],
}

# Progress acks for long-running tasks
PROGRESS_ACKS: List[str] = [
    "Still working.",
    "Almost there.",
    "Processing.",
    "Nearly done.",
]

FAILURE_ACKS: List[str] = [
    "That didn't work.",
    "I ran into a problem.",
    "Failed.",
]


# ── UX ENGINE ─────────────────────────────────────────────────────────────

class UXFeedback:
    def get_wake_ack(self) -> str:
        return random.choice(WAKE_ACKS)

    def get_execution_ack(self, intent_name: str, entities: Optional[Dict] = None) -> str:
        entities = entities or {}
        phrases = EXECUTION_ACKS.get(intent_name) or EXECUTION_ACKS["_default"]
        if not phrases:
            return ""
        phrase = random.choice(phrases)
        # Basic entity fill
        try:
            phrase = phrase.format(
                app=entities.get("app", ""),
                song=entities.get("song", ""),
                query=entities.get("query", ""),
                contact=entities.get("contact", ""),
                direction=entities.get("direction", ""),
            )
        except KeyError:
            pass
        return phrase.strip()

    def get_progress_ack(self, elapsed: float) -> str:
        idx = min(int(elapsed / 8), len(PROGRESS_ACKS) - 1)
        return PROGRESS_ACKS[idx]

    def get_failure_ack(self) -> str:
        return random.choice(FAILURE_ACKS)


class AckSpeaker:
    """Non-blocking TTS fire-and-forget."""

    def __init__(self, tts_fn: Optional[Callable[[str], None]] = None):
        self._fn = tts_fn

    def set_tts(self, fn: Callable[[str], None]):
        self._fn = fn

    def say(self, text: str, priority: bool = False):
        if not text:
            return
        if not self._fn:
            print(f"[Jarvis] {text}")
            return
        # Non-blocking — TTS runs in its own thread already
        self._fn(text)


class ProgressReporter:
    """Periodic progress updates for long tasks."""

    def __init__(self, speaker: AckSpeaker, interval: float = 8.0, max_updates: int = 3):
        self._speaker = speaker
        self._interval = interval
        self._max = max_updates
        self._stop = threading.Event()
        self._feedback = UXFeedback()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)

    def _run(self):
        import time
        updates = 0
        start = time.time()
        while not self._stop.wait(timeout=self._interval) and updates < self._max:
            self._speaker.say(self._feedback.get_progress_ack(time.time() - start))
            updates += 1


# ── MODULE SINGLETONS ─────────────────────────────────────────────────────

_feedback = UXFeedback()
_speaker  = AckSpeaker()


def init_ux(tts_fn: Callable[[str], None]):
    """Call once at startup with your TTS function."""
    _speaker.set_tts(tts_fn)


def say(text: str, priority: bool = False):
    _speaker.say(text, priority)


def ack_wake():
    """Instant wake acknowledgment. Plays from cache (<10ms)."""
    _speaker.say(_feedback.get_wake_ack(), priority=True)


def ack_intent(intent_name: str, entities: Optional[Dict] = None):
    """
    Fire this IMMEDIATELY after intent is parsed, BEFORE execution.
    Makes Jarvis feel instant — user hears "Opening." while Spotify launches.
    """
    phrase = _feedback.get_execution_ack(intent_name, entities)
    if phrase:
        _speaker.say(phrase, priority=False)


def ack_failure(detail: str = ""):
    prefix = _feedback.get_failure_ack()
    msg = f"{prefix} {detail}".strip() if detail else prefix
    _speaker.say(msg)