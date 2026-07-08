"""
HYBRID FAST ROUTER — Production-Complete Zero-Latency Local Intent Classifier
==============================================================================
Replaces the Groq LLM call for ~80% of commands that are simple system actions.

Architecture:
  STT text
    ↓
  Tier 1: O(1) exact hash lookup          → < 0.01ms
  Tier 2: O(N) keyword scan               → < 0.5ms
  Tier 3: Regex compiled-pattern match    → < 1ms   (replaces spaCy — no install needed)
  Tier 4: → Groq LLM (slow path)          → 300-800ms

The router returns a fully-formed intent dict identical to what IntentEngine
would produce, so the rest of the pipeline is unchanged.

Result: ~80% of commands execute in <10ms without any network call.

Wiring in main.py / agent_state.py CommandRouter.route():
    from fast_router import fast_router
    result = fast_router.classify(text)
    if result:
        # skip LLM, go straight to execution
        turn.intent = result
    else:
        # slow path: call IntentEngine (Groq)
        turn.intent = await self.intent_engine.understand(text, ...)
"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# METRICS
# ════════════════════════════════════════════════════════════════════════════

class _RouterMetrics:
    def __init__(self):
        self.total   = 0
        self.hits    = {1: 0, 2: 0, 3: 0}  # tier → count
        self.misses  = 0
        self.latency: List[float] = []

    def record(self, tier: Optional[int], latency_ms: float):
        self.total += 1
        if tier:
            self.hits[tier] = self.hits.get(tier, 0) + 1
        else:
            self.misses += 1
        self.latency.append(latency_ms)
        if len(self.latency) > 500:
            self.latency = self.latency[-500:]

    @property
    def hit_rate(self) -> float:
        if not self.total:
            return 0.0
        return sum(self.hits.values()) / self.total

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latency) / max(len(self.latency), 1)

    def summary(self) -> str:
        return (
            f"FastRouter: {self.total} total | "
            f"hit={self.hit_rate:.0%} "
            f"(T1={self.hits.get(1,0)} T2={self.hits.get(2,0)} T3={self.hits.get(3,0)} miss={self.misses}) | "
            f"avg={self.avg_latency_ms:.1f}ms"
        )


_metrics = _RouterMetrics()


# ════════════════════════════════════════════════════════════════════════════
# INTENT BUILDER — produces dict identical to IntentEngine output
# ════════════════════════════════════════════════════════════════════════════

def _intent(name: str, entities: Dict, confidence: float, text: str) -> Dict:
    return {
        "intent":        name,
        "entities":      entities,
        "confidence":    confidence,
        "original_text": text,
        "timestamp":     time.time(),
        "source":        "fast_router",
    }


# ════════════════════════════════════════════════════════════════════════════
# TIER 1 — EXACT HASH MATCHES  (O(1), compiled at import time)
# ════════════════════════════════════════════════════════════════════════════

_EXACT: Dict[str, Tuple[str, Dict]] = {
    # Media controls
    "pause":              ("pause_media",    {}),
    "stop":               ("pause_media",    {}),
    "resume":             ("resume_media",   {}),
    "play":               ("resume_media",   {}),
    "next":               ("next_track",     {}),
    "next track":         ("next_track",     {}),
    "skip":               ("next_track",     {}),
    "previous":           ("previous_track", {}),
    "previous track":     ("previous_track", {}),
    "go back":            ("previous_track", {}),
    # System
    "take screenshot":    ("take_screenshot", {}),
    "screenshot":         ("take_screenshot", {}),
    "lock":               ("lock",            {}),
    "lock screen":        ("lock",            {}),
    "lock the screen":    ("lock",            {}),
    "shutdown":           ("shutdown",        {}),
    "shut down":          ("shutdown",        {}),
    "restart":            ("restart",         {}),
    "reboot":             ("restart",         {}),
    # Browser
    "close tab":          ("close_tab",  {}),
    "new tab":            ("new_tab",    {}),
    "open new tab":       ("new_tab",    {}),
    "scroll up":          ("scroll",     {"direction": "up"}),
    "scroll down":        ("scroll",     {"direction": "down"}),
    # Conversation
    "cancel":             ("cancel",    {}),
    "never mind":         ("cancel",    {}),
    "stop that":          ("cancel",    {}),
    "abort":              ("cancel",    {}),
}


# ════════════════════════════════════════════════════════════════════════════
# TIER 2 — KEYWORD SCAN  (O(N), ~100 entries)
# ════════════════════════════════════════════════════════════════════════════

# Format: (keyword, intent_name)
# Ordered: most specific first
_KEYWORDS: List[Tuple[str, str]] = [
    ("screenshot",  "take_screenshot"),
    ("lock screen", "lock"),
    ("shut down",   "shutdown"),
    ("close tab",   "close_tab"),
    ("new tab",     "new_tab"),
    ("scroll up",   "scroll"),
    ("scroll down", "scroll"),
    ("pause",       "pause_media"),
    ("resume",      "resume_media"),
    ("next track",  "next_track"),
    ("prev track",  "previous_track"),
    ("mute",        "system_action"),
]


# ════════════════════════════════════════════════════════════════════════════
# TIER 3 — COMPILED REGEX PATTERNS  (no spaCy dependency)
# ════════════════════════════════════════════════════════════════════════════
# Each entry: (compiled_pattern, intent_name, entity_extractor_fn)

def _ext_app(m: re.Match) -> Dict:
    return {"app": m.group(1).strip().rstrip(".")}

def _ext_song_platform(m: re.Match) -> Dict:
    return {"song": m.group(1).strip(), "platform": m.group(2).lower()}

def _ext_song(m: re.Match) -> Dict:
    return {"song": m.group(1).strip()}

def _ext_query(m: re.Match) -> Dict:
    return {"query": m.group(1).strip()}

def _ext_url(m: re.Match) -> Dict:
    return {"url": m.group(1).strip()}

def _ext_contact_platform(m: re.Match) -> Dict:
    return {"contact": m.group(1).strip(), "platform": m.group(2).lower()}

def _ext_contact(m: re.Match) -> Dict:
    return {"contact": m.group(1).strip()}

def _ext_scroll(m: re.Match) -> Dict:
    return {"direction": m.group(1).lower()}

def _ext_vol_sys(m: re.Match) -> Dict:
    return {"action_type": "set_volume", "setting": "volume", "value": m.group(1).strip()}

def _ext_brightness(m: re.Match) -> Dict:
    return {"action_type": "set_brightness", "setting": "brightness", "value": m.group(1).strip()}

def _ext_resolution(m: re.Match) -> Dict:
    return {"action_type": "change_resolution", "setting": "resolution", "value": (m.group(1) or "").strip()}

def _ext_refresh(m: re.Match) -> Dict:
    return {"action_type": "change_refresh_rate", "setting": "refresh_rate", "value": (m.group(1) or "").strip()}

def _ext_type(m: re.Match) -> Dict:
    return {"text": m.group(1).strip()}

def _ext_remind(m: re.Match) -> Dict:
    return {"reminder_text": m.group(1).strip(), "time": f"{m.group(2)} {m.group(3)}s"}


_PATTERNS: List[Tuple[re.Pattern, str, Callable, float]] = [
    # (pattern, intent, entity_fn, confidence)

    # ── App control ──────────────────────────────────────────────────────
    (re.compile(r'^(?:open|launch|start)\s+(.+)', re.I),           "open_app",  _ext_app,  0.95),
    (re.compile(r'^close\s+(?!(?:this|the|current|that)\s+tab)(.+)', re.I), "close_app", _ext_app, 0.93),

    # ── Media ─────────────────────────────────────────────────────────────
    (re.compile(r'^play\s+(.+?)\s+on\s+(youtube|spotify|soundcloud)', re.I), "play_media", _ext_song_platform, 0.97),
    (re.compile(r'^play\s+(.+)',  re.I),                             "play_media",    _ext_song,    0.94),
    (re.compile(r'^pause(?:\s|$)', re.I),                            "pause_media",   lambda m: {}, 0.99),
    (re.compile(r'^resume(?:\s|$)', re.I),                           "resume_media",  lambda m: {}, 0.99),
    (re.compile(r'^(?:next|skip)(?:\s+track)?', re.I),               "next_track",    lambda m: {}, 0.99),
    (re.compile(r'^(?:previous|back|prev)(?:\s+track)?', re.I),      "previous_track",lambda m: {}, 0.99),

    # ── System actions ────────────────────────────────────────────────────
    (re.compile(r'^(?:set|change)\s+(?:system\s+|pc\s+)?volume\s+(?:to\s+)?(\d+\s*%?)', re.I), "system_action", _ext_vol_sys, 0.97),
    (re.compile(r'^(?:set|change|increase|decrease|lower|raise)\s+(?:screen\s+|display\s+)?brightness\s+(?:to\s+)?(\d+\s*%?)?', re.I), "system_action", _ext_brightness, 0.96),
    (re.compile(r'^(?:change|set|switch)\s+(?:my\s+)?(?:desktop\s+|screen\s+)?resolution\s+(?:to\s+)?(\w+)?', re.I), "system_action", _ext_resolution, 0.96),
    (re.compile(r'^(?:change|set)\s+(?:my\s+)?(?:display\s+)?refresh\s+rate\s+(?:to\s+)?(\d+\s*(?:hz)?)?', re.I), "system_action", _ext_refresh, 0.96),
    (re.compile(r'^take\s+(?:a\s+)?screenshot', re.I),               "take_screenshot",lambda m: {}, 0.99),
    (re.compile(r'^(?:lock|lock\s+(?:the\s+)?(?:screen|computer|pc))', re.I), "lock", lambda m: {}, 0.99),
    (re.compile(r'^(?:shut\s*down|turn\s+off\s+(?:the\s+)?(?:pc|computer))', re.I), "shutdown", lambda m: {}, 0.99),
    (re.compile(r'^restart(?:\s+(?:the\s+)?(?:pc|computer))?', re.I), "restart",       lambda m: {}, 0.99),

    # ── Browser ───────────────────────────────────────────────────────────
    (re.compile(r'^close\s+(?:this\s+|the\s+|current\s+)?tab', re.I), "close_tab",   lambda m: {}, 0.99),
    (re.compile(r'^(?:open\s+)?new\s+tab', re.I),                      "new_tab",     lambda m: {}, 0.99),
    (re.compile(r'^scroll\s+(up|down)', re.I),                          "scroll",      _ext_scroll,  0.99),

    # Direct URL open — catches "open youtube.com", "open github.com/user"
    (re.compile(
        r'^(?:open|go\s+to|visit|navigate\s+to)\s+'
        r'(?P<url>(?:https?://)?'
        r'[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?'
        r'(?:\.[a-zA-Z]{2,6})+'
        r'(?:/\S*)?)',
        re.I),
     "open_url",
     lambda m: {"url": m.group("url").strip().rstrip(".")},
     0.97),

    # Click/open search result by index — "open the first link", "click second result"
    (re.compile(
        r'^(?:open|click|go\s+to|select)\s+(?:the\s+)?'
        r'(?P<index>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|[1-5])\s*'
        r'(?:link|result|option|one)?',
        re.I),
     "click_result",
     lambda m: {"index": m.group("index").strip().lower()},
     0.97),

    # Browser navigation — "use this tab", "switch to this window"
    (re.compile(
        r'^(?:use|switch\s+to|focus(?:\s+on)?|go\s+to)\s+'
        r'(?:this|the\s+(?:current|active))\s+(?:tab|window|page|browser)',
        re.I),
     "browser_navigation",
     lambda m: {"action": "focus_current"},
     0.97),

    (re.compile(r'^(?:go\s+to|visit|open)\s+(https?://\S+)', re.I),    "open_website", _ext_url,    0.97),
    (re.compile(r'^search\s+(?:for\s+)?(?!.*\b(?:and|then|also)\b)(.+?)(?:\s+on\s+google)?$', re.I), "search_web", _ext_query, 0.94),

    # ── Communication ─────────────────────────────────────────────────────
    (re.compile(r'^(?:call|ring)\s+(.+?)\s+on\s+(whatsapp|discord|telegram)', re.I), "make_call", _ext_contact_platform, 0.97),
    (re.compile(r'^(?:call|ring)\s+(.+)', re.I),                        "make_call",  _ext_contact, 0.92),

    # ── Typing ────────────────────────────────────────────────────────────
    (re.compile(r'^type\s+(.+)', re.I),                                 "type_text",  _ext_type,    0.95),

    # ── Reminders ─────────────────────────────────────────────────────────
    (re.compile(r'^remind\s+me\s+(?:to\s+)?(.+?)\s+in\s+(\d+)\s*(minute|hour|second)s?', re.I), "set_reminder", _ext_remind, 0.96),

    # ── Cancel ────────────────────────────────────────────────────────────
    (re.compile(r'^(?:cancel|stop|abort|never\s+mind|forget\s+it)', re.I), "cancel",  lambda m: {}, 0.99),

    # ── Social ────────────────────────────────────────────────────────────
    (re.compile(r'^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))', re.I), "greet", lambda m: {}, 0.98),
    (re.compile(r'^(?:thanks|thank\s+you|cheers)', re.I),               "thank",      lambda m: {}, 0.98),
]

# Compile all patterns at import time
_COMPILED = _PATTERNS  # already compiled above


# ════════════════════════════════════════════════════════════════════════════
# MAIN ROUTER CLASS
# ════════════════════════════════════════════════════════════════════════════

class HybridFastRouter:
    """
    Production-complete 3-tier local intent classifier.
    Zero network calls. Sub-millisecond for Tiers 1-2. ~1ms for Tier 3.

    Usage:
        from fast_router import fast_router
        result = fast_router.classify(text)
        if result:
            # use result as intent dict, skip LLM
        else:
            # fall through to Groq
    """

    def __init__(self, confidence_threshold: float = 0.90):
        self._threshold = confidence_threshold
        self._callbacks: Dict[str, Callable] = {}
        # Register barge-in / interrupt handlers
        self._interrupt_intents = frozenset({"cancel", "pause_media"})

    # ── PUBLIC API ────────────────────────────────────────────────────────

    def classify(self, text: str, is_partial: bool = False) -> Optional[Dict]:
        """
        Classify text locally. Returns intent dict or None (→ slow path).

        Args:
            text:       Raw STT transcript
            is_partial: If True, only run Tier 1 + Tier 2 (for barge-in)

        Returns:
            Intent dict (like IntentEngine output) or None
        """
        t0 = time.perf_counter()
        stripped = text.strip().lower().rstrip(".,!?;:")

        if not stripped:
            _metrics.record(None, 0.0)
            return None

        # ── Tier 1: O(1) Exact match ─────────────────────────────────────
        match = _EXACT.get(stripped)
        if match:
            intent_name, entities = match
            result = _intent(intent_name, entities, 0.99, text)
            _metrics.record(1, (time.perf_counter() - t0) * 1000)
            logger.debug(f"[FastRouter T1] '{text}' → {intent_name}")
            return result

        # ── Tier 2: Keyword scan ──────────────────────────────────────────
        _KEYWORD_ENTITIES = {
            "mute":        {"action_type": "mute",    "setting": "volume", "value": "0"},
            "scroll up":   {"direction": "up"},
            "scroll down": {"direction": "down"},
        }
        for kw, intent_name in _KEYWORDS:
            if kw in stripped:
                entities = _KEYWORD_ENTITIES.get(kw, {})
                if intent_name == "scroll" and kw not in _KEYWORD_ENTITIES:
                    entities = {"direction": "down" if "down" in stripped else "up"}
                result = _intent(intent_name, entities, 0.95, text)
                _metrics.record(2, (time.perf_counter() - t0) * 1000)
                logger.debug(f"[FastRouter T2] '{text}' → {intent_name} (kw={kw!r})")
                return result

        # ── Skip Tier 3 for partial transcripts (barge-in only needs T1/T2)
        if is_partial:
            _metrics.record(None, (time.perf_counter() - t0) * 1000)
            return None
        
        # ── MULTI-STEP DETECTION (before Tier 3 regex) ──────────────────
        # If the command contains multiple actions joined by "and" or "then",
        # it's an autonomous multi-step task. Let the full intent engine handle it.
        _multi_step_markers = [
            r'\band\s+(?:then\s+)?(?:summarize|find|open|apply|send|download|save|play|read|search|get|make|tell|show)',
            r'\bthen\s+(?:summarize|find|open|apply|send|download|save|play|read|search|get|make)',
            r'\balso\s+(?:summarize|find|open|apply|send)',
            r'(?:find|search|look\s+up).+\b(?:and|then)\b.+\b(?:summarize|tell|show|read|explain)\b',
            r'(?:open|go\s+to).+\b(?:and|then)\b.+\b(?:search|find|play|read)\b',
            r'\bapply\s+to\s+\d+',
            r'\bmonitor\b.+\b(?:and|then)\b.+\b(?:alert|notify|tell)\b',
            r'\bkeep\s+(?:checking|trying|looking)\b.+\buntil\b',
        ]
        
        import re as _re
        for marker in _multi_step_markers:
            if _re.search(marker, stripped):
                logger.debug(f"[FastRouter] Multi-step detected: '{text[:60]}' → delegating to full intent engine")
                _metrics.record(None, (time.perf_counter() - t0) * 1000)
                return None  # Let IntentEngine handle this

        # ── Tier 3: Compiled regex ────────────────────────────────────────
        original = text.strip()
        for pattern, intent_name, extractor, confidence in _COMPILED:
            m = pattern.match(original)
            if m:
                if confidence >= self._threshold:
                    try:
                        entities = extractor(m)
                    except Exception:
                        entities = {}
                    result = _intent(intent_name, entities, confidence, text)
                    _metrics.record(3, (time.perf_counter() - t0) * 1000)
                    logger.debug(f"[FastRouter T3] '{text}' → {intent_name} (conf={confidence})")
                    return result

        _metrics.record(None, (time.perf_counter() - t0) * 1000)
        return None

    def is_interrupt(self, text: str) -> bool:
        """Check if partial transcript is a barge-in interrupt command."""
        result = self.classify(text, is_partial=True)
        return result is not None and result.get("intent") in self._interrupt_intents

    def register_callback(self, intent_name: str, fn: Callable):
        """Register a callback for when an intent is classified (optional hook)."""
        self._callbacks[intent_name] = fn

    def get_metrics(self) -> str:
        return _metrics.summary()

    def route(self, text: str, is_partial: bool = False) -> Tuple[bool, Optional[Dict]]:
        """
        Legacy API compatibility with the original HybridFastRouter.
        Returns (handled, result_dict_or_None).
        """
        result = self.classify(text, is_partial=is_partial)
        if result:
            return True, result
        return False, None


# ════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON
# ════════════════════════════════════════════════════════════════════════════

fast_router = HybridFastRouter(confidence_threshold=0.90)