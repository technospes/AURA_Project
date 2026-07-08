"""
DYNAMIC TASK PLANNER v2 — Production-Hardened, Goal-Oriented Multi-Turn Dialogue
==================================================================================
v2 changes over v1 (all 9 production fixes applied):

  FIX 1 — EXECUTION INTENT MAPPING
    LLM-generated execution_intent strings are mapped through INTENT_MAP to
    canonical IntentEngine names. Unknown strings fall back to "quick_answer".
    PlanResult.execution_intent is always a valid engine-known intent.

  FIX 2 — FAST-PATH BYPASS
    _needs_planning() gates every request. Only PLANNER_ELIGIBLE_INTENTS or
    "unknown" can start a new session. High-confidence (≥0.90) fully-specified
    commands bypass entirely — zero LLM calls, zero latency.

  FIX 3 — HYBRID CONFIRMATION DETECTION
    _detect_confirmation_fast() uses a rule-based whitelist in <1ms BEFORE
    any LLM call. LLM is only consulted when the fast path returns None.

  FIX 4 — SLOT VALIDATION LAYER
    SlotValidator.validate(slot, value, hint) checks known slot patterns
    (resolution, refresh_rate, aspect_ratio, volume_level, platform, …)
    via regex. Invalid values are rejected; slot re-queued as missing.

  FIX 5 — EXPLICIT ACTIVE SESSION TRACKING
    _active_session_id tracks the one live session by ID (O(1) lookup).
    No more dict iteration — prevents stale-session resumption bugs.

  FIX 6 — ASYNC EXECUTION FIX
    asyncio.coroutine() replaced with asyncio.iscoroutinefunction() + proper
    await / loop.run_in_executor(). Fully compatible with Python 3.11+.

  FIX 7 — SCHEMA SAFETY CONSTRAINTS
    decompose_goal() prompt caps slots at 4 required / 2 optional.
    Post-processing enforces MAX_REQUIRED_SLOTS / MAX_OPTIONAL_SLOTS even
    if the LLM ignores the instruction.

  FIX 8 — ROBUST FALLBACK HANDLING
    _safe_question_result() always returns a usable dict. Outer try/except in
    process() catches any unhandled error and returns a passthrough READY
    result so the voice loop never blocks.

  FIX 9 — SCHEMA CACHED PER SESSION
    GoalSchema built once in _start_session(), stored in ActiveTaskContext.
    Subsequent turns reuse it — no redundant LLM decomposition calls.
    _call() uses asyncio.get_running_loop() (not deprecated get_event_loop()).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

AUTO_FILL_THRESHOLD = 0.85   # Confidence required to auto-fill a slot silently
SUGGEST_THRESHOLD   = 0.65   # Confidence required to suggest + ask confirmation
SESSION_TIMEOUT_SEC = 300    # 5 min inactivity → session expires
MAX_CLARIFY_TURNS   = 5      # Hard cap on clarification turns per task
MAX_REQUIRED_SLOTS  = 4      # FIX 7: safety cap on required slots per schema
MAX_OPTIONAL_SLOTS  = 2      # FIX 7: safety cap on optional slots per schema

GROQ_MODEL = "llama-3.3-70b-versatile"


# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 — EXECUTION INTENT MAPPING
# Normalises any string the LLM might produce → canonical IntentEngine name.
# Keys sourced from engine.py _make_registry() + intent_engine.py INTENT_CATALOGUE.
# ══════════════════════════════════════════════════════════════════════════════

INTENT_MAP: Dict[str, str] = {
    # Display / system settings (LLM often invents these)
    "change_display_settings":  "system_action",
    "change_resolution":        "system_action",
    "set_resolution":           "system_action",
    "configure_display":        "system_action",
    "display_settings":         "system_action",
    "system_settings":          "system_action",
    "system_action":            "system_action",
    "set_brightness":           "system_action",
    "set_volume":               "system_action",
    "adjust_volume":            "system_action",
    # Apps
    "open_app":                 "open_app",
    "launch_app":               "open_app",
    "start_app":                "open_app",
    "close_app":                "close_app",
    "focus_app":                "focus_app",
    # Media
    "play_media":               "play_media",
    "play_music":               "play_media",
    "play_video":               "play_media",
    "pause_media":              "pause_media",
    "resume_media":             "resume_media",
    "next_track":               "next_track",
    "previous_track":           "previous_track",
    "skip_track":               "next_track",
    # Web / browser
    "search_web":               "search_web",
    "web_search":               "search_web",
    "open_website":             "open_website",
    "open_url":                 "open_website",
    "smart_open":               "smart_open",
    "close_tab":                "close_tab",
    "new_tab":                  "new_tab",
    "scroll":                   "scroll",
    "read_page":                "read_page",
    "page_summary":             "page_summary",
    # Communication
    "send_message":             "send_message",
    "make_call":                "make_call",
    "compose_email":            "compose_email",
    "call":                     "make_call",
    "message":                  "send_message",
    # Research / AI
    "deep_research":            "deep_research",
    "research":                 "deep_research",
    "quick_answer":             "quick_answer",
    "answer_question":          "answer_question",
    "summarize":                "summarize",
    # System
    "take_screenshot":          "take_screenshot",
    "screenshot":               "take_screenshot",
    "lock":                     "lock",
    "shutdown":                 "shutdown",
    "restart":                  "restart",
    "type_text":                "type_text",
    "set_reminder":             "set_reminder",
    "reminder":                 "set_reminder",
    # Memory
    "remember_fact":            "remember_fact",
    "recall_fact":              "recall_fact",
    # Conversational
    "conversation":             "conversation",
    "greet":                    "greet",
    "thank":                    "thank",
    "cancel":                   "cancel",
    "guided_recommendation":    "guided_recommendation",
    "unknown":                  "quick_answer",
}

_FALLBACK_INTENT = "quick_answer"


def map_execution_intent(raw: str) -> str:
    """
    FIX 1: Normalise any LLM-generated intent string to a known canonical name.
    Safe — never raises, always returns a valid value.
    """
    if not raw:
        return _FALLBACK_INTENT
    normalised = raw.lower().strip().replace(" ", "_").replace("-", "_")
    if normalised in INTENT_MAP:
        return INTENT_MAP[normalised]
    # Partial-match fallback: find longest key that appears in the raw string
    best: Optional[str] = None
    for key in INTENT_MAP:
        if key in normalised and (best is None or len(key) > len(best)):
            best = key
    return INTENT_MAP[best] if best else _FALLBACK_INTENT


# ══════════════════════════════════════════════════════════════════════════════
# FIX 2 — FAST-PATH BYPASS LOGIC
# ══════════════════════════════════════════════════════════════════════════════

# Intents that are always one-shot: never need multi-turn planning
PLANNER_BYPASS_INTENTS: frozenset = frozenset({
    "greet", "thank", "cancel",
    "pause_media", "resume_media", "next_track", "previous_track",
    "take_screenshot", "lock", "shutdown", "restart",
    "scroll", "new_tab", "close_tab",
    "conversation", "recall_fact", "remember_fact",
    "read_page", "page_summary", "answer_question",
    "save_file", "click_element",
    "guided_recommendation",   # has its own multi-turn advisor
    "deep_research",            # topic always extracted directly by IntentEngine
})

# Intents that MAY benefit from multi-turn clarification
PLANNER_ELIGIBLE_INTENTS: frozenset = frozenset({
    "system_action",   # e.g. change resolution, brightness, volume
    "open_app",        # when app name is vague
    "play_media",      # missing song or platform
    "search_web",      # complex / multi-part queries
    "open_website",    # URL needs inference
    "send_message",    # contact + platform + body
    "make_call",       # contact + platform
    "compose_email",   # recipient + subject + body
    "set_reminder",    # time + reminder text
    "type_text",       # what to type
    "unknown",         # always needs clarification
})


def _needs_planning(intent_name: str, confidence: float, entities: Dict) -> bool:
    """
    FIX 2: True only when the planner should engage.

    Bypass rules (in priority order):
    1. Intent is on the bypass list → always False
    2. High confidence (≥0.90) + has entity data → command is already complete
    3. Intent is eligible AND data is missing → True
    4. Unknown intent → True (planner will decompose)
    """
    if intent_name in PLANNER_BYPASS_INTENTS:
        return False
    if confidence >= 0.90 and entities and intent_name not in {"unknown", "conversation"}:
        return False
    if intent_name in PLANNER_ELIGIBLE_INTENTS:
        return True
    if intent_name in {"unknown", "conversation"}:
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# FIX 3 — HYBRID CONFIRMATION DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_YES_WORDS: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "correct",
    "right", "exactly", "definitely", "absolutely", "of course",
    "go ahead", "do it", "proceed", "confirm", "affirmative",
    "that's right", "sounds good", "perfect", "great", "fine",
    "works for me", "sounds right", "that works",
})
_NO_WORDS: frozenset = frozenset({
    "no", "nope", "nah", "different", "wrong", "incorrect",
    "not that", "change it", "other", "another", "something else",
    "not really", "negative", "don't", "no thanks", "not quite",
    "actually no", "never mind",
})


def _detect_confirmation_fast(text: str) -> Optional[bool]:
    """
    FIX 3: Sub-millisecond yes/no detection.
    Returns True (confirm), False (reject), or None (ambiguous → use LLM).
    """
    clean = text.lower().strip().rstrip(".,!?")
    if clean in _YES_WORDS:
        return True
    if clean in _NO_WORDS:
        return False
    first = clean.split()[0] if clean.split() else ""
    if first in {"yes", "yeah", "yep", "yup", "sure"}:
        return True
    if first in {"no", "nope", "nah"}:
        return False
    return None   # Ambiguous — fall through to LLM


# ══════════════════════════════════════════════════════════════════════════════
# FIX 4 — SLOT VALIDATION LAYER
# ══════════════════════════════════════════════════════════════════════════════

class SlotValidator:
    """
    FIX 4: Regex-based slot validation with normalisation.
    Prevents LLM hallucinations (e.g. resolution="banana") from reaching
    the executor. Invalid values cause the slot to be re-queued as missing.
    """

    _PATTERNS: Dict[str, re.Pattern] = {
        "resolution": re.compile(
            r"^\d{3,4}[pPkK]$|^\d{3,4}\s*[xX×]\s*\d{3,4}$"
            r"|^(1080p|1440p|2160p|4K|2K|720p|480p|UHD|FHD|QHD|WQHD)$",
            re.IGNORECASE,
        ),
        "refresh_rate": re.compile(
            r"^\d{2,3}(\s*(hz|hertz))?$", re.IGNORECASE
        ),
        "aspect_ratio": re.compile(
            r"^\d{1,2}\s*[:/]\s*\d{1,2}$|^(16:9|4:3|21:9|16:10|1:1|32:9)$",
            re.IGNORECASE,
        ),
        "volume_level": re.compile(r"^(100|\d{1,2})(%)?$"),
        "brightness":   re.compile(r"^(100|\d{1,2})(%)?$"),
        "time_delay":   re.compile(
            r"^\d+\s*(second|minute|hour|sec|min|hr)s?$", re.IGNORECASE
        ),
        "platform": re.compile(
            r"^(spotify|youtube|soundcloud|discord|whatsapp|telegram"
            r"|teams|zoom|netflix|prime|tidal|deezer)$",
            re.IGNORECASE,
        ),
    }

    _NORMALISE: Dict[str, Callable[[str], str]] = {
        "refresh_rate": lambda v: re.sub(r"\s*(hz|hertz)", "", v, flags=re.IGNORECASE).strip(),
        "volume_level": lambda v: v.rstrip("%").strip(),
        "brightness":   lambda v: v.rstrip("%").strip(),
        "platform":     lambda v: v.lower().strip(),
        "resolution":   lambda v: v.upper().strip().replace(" ", ""),
        "aspect_ratio": lambda v: v.replace(" ", "").replace("×", ":"),
    }

    def validate(self, slot: str, value: Any, validator_hint: str = "") -> Tuple[bool, Any]:
        """
        Returns (is_valid, normalised_value).
        Slots without a known pattern pass through unchanged.
        """
        if value is None:
            return False, None
        str_val = str(value).strip()
        if not str_val:
            return False, None

        pattern = self._PATTERNS.get(slot)
        if pattern:
            if not pattern.match(str_val):
                logger.warning(
                    f"[Validator] '{slot}' rejected '{str_val}' "
                    f"(hint: {validator_hint or 'none'})"
                )
                return False, None
            norm_fn = self._NORMALISE.get(slot)
            normalised = norm_fn(str_val) if norm_fn else str_val
            return True, normalised

        # No known pattern for this slot → pass through
        return True, str_val


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

class PlanPhase(Enum):
    GATHERING  = "gathering"    # Collecting missing slots
    CONFIRMING = "confirming"   # Waiting for yes/no on a suggestion
    READY      = "ready"        # All required slots resolved
    CANCELLED  = "cancelled"


@dataclass
class SlotValue:
    value: Any
    confidence: float    # 0.0 – 1.0
    source: str          # "user_explicit" | "llm_inferred" | "suggested" | "partial"
    raw_text: str = ""

    @property
    def is_confident(self) -> bool:
        return self.confidence >= AUTO_FILL_THRESHOLD

    @property
    def needs_confirmation(self) -> bool:
        return SUGGEST_THRESHOLD <= self.confidence < AUTO_FILL_THRESHOLD


@dataclass
class GoalSchema:
    """
    Slot requirements for a goal.
    Built once per session (FIX 9) — never re-generated.
    execution_intent is always a canonical IntentEngine name (FIX 1).
    """
    goal_id: str
    goal_description: str
    required_slots: List[str]    # Capped at MAX_REQUIRED_SLOTS (FIX 7)
    optional_slots: List[str]    # Capped at MAX_OPTIONAL_SLOTS (FIX 7)
    slot_descriptions: Dict[str, str]
    slot_validators: Dict[str, str]
    execution_intent: str        # Canonical — already through map_execution_intent()


@dataclass
class ActiveTaskContext:
    """
    Persists across voice turns for one goal.
    schema is cached here — never re-built (FIX 9).
    """
    session_id: str
    schema: GoalSchema
    filled_slots: Dict[str, SlotValue] = field(default_factory=dict)
    conversation_history: List[Dict]   = field(default_factory=list)
    phase: PlanPhase                   = PlanPhase.GATHERING
    clarify_turns: int                 = 0
    pending_suggestion: Optional[Dict] = None   # {"slot": str, "value": Any}
    created_at: float                  = field(default_factory=time.time)
    last_activity: float               = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > SESSION_TIMEOUT_SEC

    def touch(self):
        self.last_activity = time.time()

    @property
    def missing_required_slots(self) -> List[str]:
        return [
            s for s in self.schema.required_slots
            if s not in self.filled_slots or not self.filled_slots[s].is_confident
        ]

    @property
    def all_slots_resolved(self) -> bool:
        return len(self.missing_required_slots) == 0

    def slot_summary(self) -> Dict[str, Any]:
        return {k: v.value for k, v in self.filled_slots.items()}

    def add_turn(self, role: str, text: str):
        self.conversation_history.append({
            "role": role, "content": text, "ts": time.time()
        })

    def recent_history_str(self, n: int = 6) -> str:
        recent = self.conversation_history[-n:]
        return "\n".join(f"  [{h['role']}]: {h['content']}" for h in recent)


@dataclass
class PlanResult:
    """
    Returned by TaskPlanner.process() to the voice loop.
    The caller only needs to check these fields — no slot internals exposed.
    execution_intent is always a valid canonical intent (FIX 1).
    """
    session_id: str
    phase: PlanPhase

    # GATHERING / CONFIRMING
    clarification_question: str = ""
    suggestion_value: str = ""

    # READY
    goal: str = ""
    execution_intent: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)

    # Meta
    confidence: float = 0.0
    reasoning: str = ""

    @property
    def needs_clarification(self) -> bool:
        return self.phase in (PlanPhase.GATHERING, PlanPhase.CONFIRMING)

    @property
    def ready_to_execute(self) -> bool:
        return self.phase == PlanPhase.READY


# ══════════════════════════════════════════════════════════════════════════════
# LLM INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

class PlannerLLM:
    """
    Groq LLM wrapper for the three planning tasks.
    FIX 9: asyncio.get_running_loop() — no deprecation warnings.
    FIX 8: every call returns a usable dict; callers must handle {}.
    """

    def __init__(self, groq_api_key: str):
        self._key    = groq_api_key
        self._client = None

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._key)
        return self._client

    async def _call(self, prompt: str, max_tokens: int = 600) -> Dict:
        """
        JSON-mode LLM call.
        FIX 9: get_running_loop() instead of deprecated get_event_loop().
        FIX 8: Returns {} on any failure — never raises.
        """
        client = self._get_client()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        def _sync():
            return client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.15,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )

        try:
            resp = await loop.run_in_executor(None, _sync)
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError as e:
            logger.error(f"[PlannerLLM] JSON decode error: {e}")
            return {}
        except Exception as e:
            logger.error(f"[PlannerLLM] API call failed: {e}")
            return {}

    # ── GOAL DECOMPOSITION (called once per session — FIX 9) ──────────────

    async def decompose_goal(self, user_input: str, system_context: Dict) -> Dict:
        """
        FIX 7: Prompt instructs LLM to cap at 4 required / 2 optional slots.
        FIX 1: Prompts LLM to use canonical intent names from INTENT_MAP.
        """
        safe_ctx = {
            k: v for k, v in system_context.items()
            if k in ("active_app", "last_app", "last_song", "active_window_title")
        }
        ctx_str     = json.dumps(safe_ctx, default=str)
        known_intents = ", ".join(sorted(set(INTENT_MAP.values())))

        prompt = f"""You are the planning brain of JARVIS, a voice assistant.

User command: "{user_input}"
System context: {ctx_str}

Decompose this into a structured goal with slots.

Strict rules:
- Required slots: ONLY those without which execution completely fails. MAX 4.
- Optional slots: nice-to-have. MAX 2.
- Extract values from the user's command and rate confidence 0.0-1.0.
- execution_intent MUST be one of: {known_intents}
- Do NOT invent slots that aren't needed for this specific goal.
- If a value like "1440p" is explicitly stated, confidence = 1.0.

Respond with ONLY this JSON (no other text):
{{
  "goal_id": "snake_case_name",
  "goal_description": "one sentence description",
  "execution_intent": "canonical_intent_name",
  "required_slots": ["slot1"],
  "optional_slots": ["slot2"],
  "slot_descriptions": {{"slot1": "what it means"}},
  "slot_validators": {{"slot1": "e.g. 1080p, 1440p, 4K"}},
  "initial_slot_values": {{
    "slot1": {{"value": "extracted_or_null", "confidence": 0.95}}
  }},
  "goal_confidence": 0.9,
  "reasoning": "one sentence"
}}"""

        return await self._call(prompt, max_tokens=700)

    # ── SLOT UPDATE ───────────────────────────────────────────────────────

    async def extract_slot_values(
        self,
        user_reply: str,
        context: ActiveTaskContext,
        target_slots: List[str],
    ) -> Dict:
        """
        FIX 3: Only called when fast-path confirmation returns None.
        FIX 8: Returns a safe default dict on failure.
        """
        schema    = context.schema
        slot_info = {
            s: {
                "description": schema.slot_descriptions.get(s, s),
                "validator":   schema.slot_validators.get(s, ""),
                "current_value": (
                    context.filled_slots[s].value if s in context.filled_slots else None
                ),
            }
            for s in target_slots
        }

        prompt = f"""You are JARVIS's slot-extraction module.

Goal: {schema.goal_description}
Conversation:
{context.recent_history_str()}

User just said: "{user_reply}"

Extract values for these slots:
{json.dumps(slot_info, indent=2)}

Confidence guide:
  1.0 = user stated it explicitly and clearly
  0.85-0.95 = clearly implied
  0.65-0.80 = reasonable inference
  <0.65 = uncertain

Respond with ONLY this JSON:
{{
  "slot_updates": {{
    "slot_name": {{"value": "extracted_or_null", "confidence": 0.0, "source": "user_explicit|llm_inferred"}}
  }},
  "is_confirmation": false,
  "is_rejection": false,
  "reasoning": "brief"
}}"""

        result = await self._call(prompt, max_tokens=400)
        # FIX 8: safe fallback
        if not result or "slot_updates" not in result:
            return {"slot_updates": {}, "is_confirmation": False, "is_rejection": False}
        return result

    # ── QUESTION GENERATION ───────────────────────────────────────────────

    async def generate_next_question(
        self,
        context: ActiveTaskContext,
        missing_slots: List[str],
    ) -> Dict:
        """
        Decide: suggest a value (action=suggest), ask (action=ask), or
        declare done (action=ready).
        FIX 8: _safe_question_result() always returns a usable dict.
        """
        schema    = context.schema
        filled    = context.slot_summary()
        slot_meta = {
            s: {
                "description": schema.slot_descriptions.get(s, s),
                "validator":   schema.slot_validators.get(s, ""),
            }
            for s in missing_slots
        }

        prompt = f"""You are JARVIS's conversational planner.

Goal: {schema.goal_description}
Known slots: {json.dumps(filled)}
Missing required slots: {json.dumps(slot_meta)}
Recent conversation:
{context.recent_history_str(4)}

Choose what to say next to fill ONE missing slot.

A) "suggest" — You have a strong contextual guess (confidence > 0.65).
   Example: "For gaming, 144Hz is recommended. Should I use that?"

B) "ask" — No confident guess. Ask a short, natural question.
   Example: "What refresh rate would you like, Sir?"

C) "ready" — All missing slots have sensible defaults; proceed now.

Rules:
- Address ONE slot only. Keep it short (voice output).
- Mention what you already know to sound natural.

Respond with ONLY this JSON:
{{
  "action": "suggest|ask|ready",
  "slot": "slot_name",
  "question": "the text to speak",
  "suggested_value": "value_or_null",
  "suggestion_confidence": 0.0,
  "reasoning": "brief"
}}"""

        result = await self._call(prompt, max_tokens=300)
        return _safe_question_result(result, missing_slots)


def _safe_question_result(result: Dict, missing_slots: List[str]) -> Dict:
    """
    FIX 8: Guarantee generate_next_question always returns a usable dict.
    Called as a module-level function so it's easy to unit-test.
    """
    if not result or "action" not in result:
        slot = missing_slots[0] if missing_slots else "details"
        return {
            "action": "ask",
            "slot": slot,
            "question": f"Could you tell me the {slot.replace('_', ' ')}, Sir?",
            "suggested_value": None,
            "suggestion_confidence": 0.0,
            "reasoning": "fallback — LLM response empty or malformed",
        }
    # Clamp confidence
    result["suggestion_confidence"] = max(
        0.0, min(1.0, float(result.get("suggestion_confidence", 0.0)))
    )
    # Ensure question text exists
    if not result.get("question"):
        slot = result.get("slot") or (missing_slots[0] if missing_slots else "details")
        result["question"] = f"What {slot.replace('_', ' ')} would you like, Sir?"
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TASK PLANNER — Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

class TaskPlanner:
    """
    Goal-oriented multi-turn planner.

    FIX 5: Single active session tracked by _active_session_id (O(1) lookup).
    FIX 7: Enforces schema slot caps post-LLM-decomposition.
    FIX 8: Outer try/except in process() ensures voice loop never blocks.
    """

    def __init__(self, groq_api_key: str):
        self._llm                 = PlannerLLM(groq_api_key)
        self._validator           = SlotValidator()
        self._sessions: Dict[str, ActiveTaskContext] = {}
        self._active_session_id: Optional[str] = None   # FIX 5

    # ── PUBLIC API ────────────────────────────────────────────────────────

    @property
    def has_active_session(self) -> bool:
        return self.get_active_session() is not None

    def get_active_session(self) -> Optional[ActiveTaskContext]:
        """FIX 5: O(1) direct lookup — no dict iteration."""
        if not self._active_session_id:
            return None
        session = self._sessions.get(self._active_session_id)
        if session is None or session.is_expired:
            self._active_session_id = None
            return None
        if session.phase not in (PlanPhase.GATHERING, PlanPhase.CONFIRMING):
            self._active_session_id = None
            return None
        return session

    def cancel_active_session(self) -> bool:
        session = self.get_active_session()
        if not session:
            return False
        session.phase = PlanPhase.CANCELLED
        self._active_session_id = None
        logger.info(f"[Planner] Session {session.session_id} cancelled")
        return True

    async def process(
        self,
        user_input: str,
        system_context: Dict,
        intent_result: Optional[Dict] = None,
    ) -> PlanResult:
        """
        Main entry point — one call per voice turn.
        FIX 8: Outer try/except — never raises, never stalls the voice loop.
        """
        try:
            return await self._process_inner(user_input, system_context, intent_result)
        except Exception as e:
            logger.error(f"[Planner] Unhandled error: {e}", exc_info=True)
            self._active_session_id = None
            intent = (intent_result or {}).get("intent", "unknown")
            return PlanResult(
                session_id="",
                phase=PlanPhase.READY,
                goal=intent,
                execution_intent=map_execution_intent(intent),
                slots=(intent_result or {}).get("entities", {}),
                confidence=0.4,
                reasoning="planner error — passthrough",
            )

    # ── INNER PROCESSING ─────────────────────────────────────────────────

    async def _process_inner(
        self,
        user_input: str,
        system_context: Dict,
        intent_result: Optional[Dict],
    ) -> PlanResult:
        self._gc_sessions()

        # Cancel command — handle before anything else
        cancel_words = {"cancel", "stop", "never mind", "forget it", "abort"}
        if any(w in user_input.lower() for w in cancel_words) and self.has_active_session:
            self.cancel_active_session()
            return PlanResult(
                session_id="",
                phase=PlanPhase.CANCELLED,
                clarification_question="Understood, Sir. Task cancelled.",
            )

        intent_name = (intent_result or {}).get("intent", "unknown")
        confidence  = float((intent_result or {}).get("confidence", 0.5))
        entities    = (intent_result or {}).get("entities", {})

        # FIX 5: Check for active session FIRST (multi-turn continuation)
        active = self.get_active_session()
        if active:
            logger.info(
                f"[Planner] Resuming {active.session_id} "
                f"phase={active.phase.value} turn={active.clarify_turns}"
            )
            return await self._continue_session(active, user_input)

        # FIX 2: Fast-path bypass
        if not _needs_planning(intent_name, confidence, entities):
            logger.debug(f"[Planner] Fast-path: {intent_name} conf={confidence:.2f}")
            return PlanResult(
                session_id="",
                phase=PlanPhase.READY,
                goal=intent_name,
                execution_intent=map_execution_intent(intent_name),
                slots=entities,
                confidence=confidence,
                reasoning="fast-path bypass — fully specified",
            )

        return await self._start_session(user_input, system_context, intent_result)

    # ── SESSION LIFECYCLE ─────────────────────────────────────────────────

    async def _start_session(
        self,
        user_input: str,
        system_context: Dict,
        intent_result: Optional[Dict],
    ) -> PlanResult:
        """
        Decompose goal and create a new planning session.
        FIX 7: Enforce slot caps after LLM response.
        FIX 9: Schema built once, stored in session.
        FIX 8: Passthrough fallback if decomposition fails.
        """
        logger.info(f"[Planner] New session: '{user_input}'")
        decomp = await self._llm.decompose_goal(user_input, system_context)

        # FIX 8: Fallback when decomposition fails entirely
        if not decomp or "goal_id" not in decomp:
            logger.warning("[Planner] Decomposition failed — passthrough")
            intent = (intent_result or {}).get("intent", "unknown")
            return PlanResult(
                session_id="",
                phase=PlanPhase.READY,
                goal=intent,
                execution_intent=map_execution_intent(intent),
                slots=(intent_result or {}).get("entities", {}),
                confidence=0.5,
                reasoning="decomposition failed — passthrough",
            )

        # FIX 1: Canonicalise execution intent immediately
        canonical_intent = map_execution_intent(decomp.get("execution_intent", ""))

        # FIX 7: Enforce slot caps
        required_slots = decomp.get("required_slots", [])[:MAX_REQUIRED_SLOTS]
        optional_slots = decomp.get("optional_slots", [])[:MAX_OPTIONAL_SLOTS]

        schema = GoalSchema(
            goal_id=decomp.get("goal_id", "unnamed_goal"),
            goal_description=decomp.get("goal_description", user_input),
            required_slots=required_slots,
            optional_slots=optional_slots,
            slot_descriptions=decomp.get("slot_descriptions", {}),
            slot_validators=decomp.get("slot_validators", {}),
            execution_intent=canonical_intent,
        )

        session_id = str(uuid.uuid4())[:8]
        session    = ActiveTaskContext(session_id=session_id, schema=schema)
        session.add_turn("user", user_input)

        # Seed initial values from decomposition
        all_slots = required_slots + optional_slots
        for slot, sv in decomp.get("initial_slot_values", {}).items():
            if slot not in all_slots:
                continue
            raw_val = sv.get("value")
            conf    = max(0.0, min(1.0, float(sv.get("confidence", 0.0))))
            if raw_val is None or conf < 0.3:
                continue
            # FIX 4: Validate before storing
            valid, normed = self._validator.validate(
                slot, raw_val, schema.slot_validators.get(slot, "")
            )
            if not valid:
                logger.warning(f"[Planner] Seed '{slot}'='{raw_val}' failed validation — skipped")
                continue
            src = "user_explicit" if conf >= AUTO_FILL_THRESHOLD else "llm_inferred"
            session.filled_slots[slot] = SlotValue(
                value=normed, confidence=conf, source=src, raw_text=user_input
            )
            logger.info(f"[Planner] Seeded '{slot}' = {normed} (conf={conf:.2f})")

        self._sessions[session_id]  = session
        self._active_session_id     = session_id   # FIX 5

        # If already fully resolved, execute immediately
        if not schema.required_slots or session.all_slots_resolved:
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        return await self._ask_next(session)

    async def _continue_session(
        self,
        session: ActiveTaskContext,
        user_input: str,
    ) -> PlanResult:
        """Handle a follow-up turn within an existing session."""
        session.touch()
        session.add_turn("user", user_input)
        session.clarify_turns += 1

        # Hard cap — best-effort execution
        if session.clarify_turns > MAX_CLARIFY_TURNS:
            logger.warning("[Planner] Max clarification turns — executing best-effort")
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        # Handle CONFIRMING phase
        if session.phase == PlanPhase.CONFIRMING and session.pending_suggestion:
            return await self._handle_confirmation(session, user_input)

        # Normal slot extraction
        missing = session.missing_required_slots
        update  = await self._llm.extract_slot_values(user_input, session, missing[:3])
        self._apply_slot_updates(session, update, user_input)

        if session.all_slots_resolved:
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        return await self._ask_next(session)

    async def _handle_confirmation(
        self,
        session: ActiveTaskContext,
        user_input: str,
    ) -> PlanResult:
        """
        Process yes/no to a suggested value.
        FIX 3: Fast rule-based detection first — LLM only if ambiguous.
        FIX 4: Validate confirmed value before storing.
        """
        suggestion = session.pending_suggestion
        session.pending_suggestion = None
        session.phase = PlanPhase.GATHERING

        # FIX 3: Hybrid detection
        fast = _detect_confirmation_fast(user_input)

        if fast is None:
            # Ambiguous — ask LLM
            update = await self._llm.extract_slot_values(
                user_input, session, [suggestion["slot"]]
            )
            if update.get("is_confirmation"):
                fast = True
            elif update.get("is_rejection"):
                fast = False
            else:
                fast = True   # Default to accepting when truly ambiguous

        slot = suggestion["slot"]

        if fast:   # Confirmed
            val = suggestion["value"]
            valid, normed = self._validator.validate(
                slot, val, session.schema.slot_validators.get(slot, "")
            )   # FIX 4
            if valid:
                session.filled_slots[slot] = SlotValue(
                    value=normed, confidence=0.95,
                    source="suggested", raw_text=user_input
                )
                logger.info(f"[Planner] Confirmed: {slot} = {normed}")
            else:
                # Confirmed but invalid — re-ask
                logger.warning(f"[Planner] Suggested value '{val}' invalid for '{slot}' — re-asking")
                fast = False

        if not fast:   # Rejected (or confirmed-but-invalid)
            desc = session.schema.slot_descriptions.get(slot, slot.replace("_", " "))
            question = f"Alright. What {desc} would you prefer, Sir?"
            session.add_turn("assistant", question)
            return PlanResult(
                session_id=session.session_id,
                phase=PlanPhase.GATHERING,
                clarification_question=question,
            )

        if session.all_slots_resolved:
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        return await self._ask_next(session)

    async def _ask_next(self, session: ActiveTaskContext) -> PlanResult:
        """
        Generate the next clarification turn.
        FIX 8: _safe_question_result() guarantees a usable response.
        """
        missing = session.missing_required_slots
        if not missing:
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        decision = await self._llm.generate_next_question(session, missing)
        action   = decision.get("action", "ask")
        question = decision.get("question", "")
        slot     = decision.get("slot", missing[0])

        session.add_turn("assistant", question)

        if action == "ready":
            session.phase = PlanPhase.READY
            self._active_session_id = None
            return self._build_ready_result(session)

        if action == "suggest":
            suggested = decision.get("suggested_value")
            s_conf    = decision.get("suggestion_confidence", 0.0)
            if suggested and s_conf >= SUGGEST_THRESHOLD:
                session.phase = PlanPhase.CONFIRMING
                session.pending_suggestion = {"slot": slot, "value": suggested}
                logger.info(f"[Planner] Suggesting '{suggested}' for '{slot}' (conf={s_conf:.2f})")
                return PlanResult(
                    session_id=session.session_id,
                    phase=PlanPhase.CONFIRMING,
                    clarification_question=question,
                    suggestion_value=str(suggested),
                )

        session.phase = PlanPhase.GATHERING
        return PlanResult(
            session_id=session.session_id,
            phase=PlanPhase.GATHERING,
            clarification_question=question,
        )

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _apply_slot_updates(
        self,
        session: ActiveTaskContext,
        update: Dict,
        raw_text: str,
    ):
        """
        Write LLM extraction results into session.filled_slots.
        FIX 4: Every value is validated before storage.
        FIX 8: Handles malformed / empty update dict gracefully.
        """
        if not update:
            return
        all_slots = session.schema.required_slots + session.schema.optional_slots

        for slot, sv in update.get("slot_updates", {}).items():
            if slot not in all_slots:
                logger.debug(f"[Planner] Ignoring hallucinated slot: '{slot}'")
                continue

            value = sv.get("value")
            conf  = max(0.0, min(1.0, float(sv.get("confidence", 0.0))))
            src   = sv.get("source", "llm_inferred")

            if value is None:
                continue

            # FIX 4: Validate before any storage
            valid, normed = self._validator.validate(
                slot, value, session.schema.slot_validators.get(slot, "")
            )
            if not valid:
                logger.warning(f"[Planner] Rejected invalid value: '{slot}' = '{value}'")
                continue

            if conf >= AUTO_FILL_THRESHOLD:
                session.filled_slots[slot] = SlotValue(
                    value=normed, confidence=conf, source=src, raw_text=raw_text
                )
                logger.info(f"[Planner] Auto-filled '{slot}' = {normed} (conf={conf:.2f})")
            elif conf >= SUGGEST_THRESHOLD:
                # Partial confidence — stored so _ask_next can propose it
                session.filled_slots[slot] = SlotValue(
                    value=normed, confidence=conf, source="partial", raw_text=raw_text
                )
                logger.debug(f"[Planner] Partial fill '{slot}' = {normed} (conf={conf:.2f})")

    def _build_ready_result(self, session: ActiveTaskContext) -> PlanResult:
        avg_conf = (
            sum(v.confidence for v in session.filled_slots.values())
            / max(len(session.filled_slots), 1)
        )
        logger.info(
            f"[Planner] READY session={session.session_id} "
            f"goal={session.schema.goal_id} slots={session.slot_summary()}"
        )
        return PlanResult(
            session_id=session.session_id,
            phase=PlanPhase.READY,
            goal=session.schema.goal_id,
            execution_intent=session.schema.execution_intent,   # FIX 1: already canonical
            slots=session.slot_summary(),
            confidence=avg_conf,
        )

    def _gc_sessions(self):
        """Remove expired sessions and clear the active pointer if stale."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            if sid == self._active_session_id:
                self._active_session_id = None   # FIX 5
            del self._sessions[sid]
            logger.debug(f"[Planner] GC'd expired session {sid}")


# ══════════════════════════════════════════════════════════════════════════════
# VOICE LOOP INTEGRATION HELPER
# ══════════════════════════════════════════════════════════════════════════════

class PlannerIntegration:
    """
    Drop-in integration shim — wires TaskPlanner into the existing voice loop
    without touching service.py or runner.py directly.

    FIX 2: Fast-path gate mirrors _needs_planning() — trivial commands skip
           the planner with zero LLM calls.
    FIX 6: Proper async execution — asyncio.coroutine() removed entirely,
           replaced with asyncio.iscoroutinefunction() + run_in_executor.
           Fully compatible with Python 3.10 / 3.11 / 3.12+.

    Usage in service.py / core.py:

        self._planner = PlannerIntegration(groq_api_key=KEY, speak_fn=self.speak)

        handled = await self._planner.handle(
            user_text, ctx_snapshot, intent_result, execute_fn
        )
        if handled:
            return
    """

    def __init__(self, groq_api_key: str, speak_fn: Callable[[str], None]):
        self.planner = TaskPlanner(groq_api_key)
        self._speak  = speak_fn

    async def handle(
        self,
        user_text: str,
        context: Dict,
        intent_result: Dict,
        execute_fn: Callable,
    ) -> bool:
        """
        Process one voice turn.

        Returns True  → planner handled it (caller should return immediately).
        Returns False → planner passed through (caller handles normally).

        execute_fn may be sync or async — both are handled correctly (FIX 6).
        """
        intent_name = intent_result.get("intent", "unknown")
        confidence  = float(intent_result.get("confidence", 0.5))
        entities    = intent_result.get("entities", {})

        # FIX 2: fast-path gate — zero cost for simple commands
        if not self.planner.has_active_session and not _needs_planning(
            intent_name, confidence, entities
        ):
            logger.debug(f"[PlannerInteg] Fast-path bypass: {intent_name}")
            return False

        result = await self.planner.process(user_text, context, intent_result)

        if result.phase == PlanPhase.CANCELLED:
            if result.clarification_question:
                self._speak(result.clarification_question)
            return True

        if result.needs_clarification:
            self._speak(result.clarification_question)
            return True

        if result.ready_to_execute and result.goal:
            logger.info(
                f"[PlannerInteg] Executing intent={result.execution_intent} "
                f"slots={result.slots}"
            )
            # FIX 6: asyncio.coroutine() is removed; proper async dispatch below
            if asyncio.iscoroutinefunction(execute_fn):
                await execute_fn(result.execution_intent, result.slots)
            else:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None, execute_fn, result.execution_intent, result.slots
                    )
                except RuntimeError:
                    execute_fn(result.execution_intent, result.slots)
            return True

        return False


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION GUIDE
# ══════════════════════════════════════════════════════════════════════════════
#
# ── OPTION A: Direct TaskPlanner (maximum control) ────────────────────────────
#
#   from task_planner import TaskPlanner, PlanResult, PlanPhase, map_execution_intent
#   self._planner = TaskPlanner(groq_api_key=GROQ_API_KEY)
#
#   # In voice processing, BEFORE dispatcher:
#   intent_result = await self._intent_engine.understand(text, ctx)
#
#   planner_result = await self._planner.process(
#       user_input=text,
#       system_context=ctx_snapshot,
#       intent_result=intent_result,
#   )
#
#   if planner_result.needs_clarification:
#       self.speak(planner_result.clarification_question)
#       return                          # wait for next turn
#
#   if planner_result.ready_to_execute:
#       intent_result["entities"].update(planner_result.slots)
#       intent_result["intent"] = planner_result.execution_intent
#       # fall through to engine / runner
#
# ── OPTION B: PlannerIntegration (zero boilerplate) ───────────────────────────
#
#   from task_planner import PlannerIntegration
#   self._planner = PlannerIntegration(groq_api_key=KEY, speak_fn=self.speak)
#
#   handled = await self._planner.handle(
#       user_text=text,
#       context=ctx_snapshot,
#       intent_result=intent_result,
#       execute_fn=self._execute,       # your existing execution function
#   )
#   if handled:
#       return
#
# ── CANCEL ────────────────────────────────────────────────────────────────────
#
#   if intent_result["intent"] == "cancel":
#       self._planner.cancel_active_session()
#
# ══════════════════════════════════════════════════════════════════════════════