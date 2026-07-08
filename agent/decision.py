"""
DECISION ENGINE — The Intelligence Gate
========================================
Runs AFTER intent understanding, BEFORE planning.

Jarvis decides:
  EXECUTE    → go ahead with the plan
  CLARIFY    → ask a follow-up question
  ANSWER     → respond directly (no tools needed)
  IGNORE     → input is noise / not a real command
  REFLECT    → think harder before acting (complex/ambiguous)

This prevents dumb execution of unclear commands and eliminates
the need for hardcoded routing rules — the engine reasons about
what to do based on intent confidence, context, and memory.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from agent.intent_registry import INTENTS, get_intent, get_required_slots, CONTEXT_FREE_INTENTS
logger = logging.getLogger(__name__)


class Decision(Enum):
    EXECUTE  = "execute"    # Run the plan
    CLARIFY  = "clarify"    # Ask follow-up
    ANSWER   = "answer"     # Respond directly (no tools)
    IGNORE   = "ignore"     # Not a real command
    REFLECT  = "reflect"    # Re-analyze (complex/ambiguous)


@dataclass
class DecisionResult:
    decision: Decision
    reason: str
    clarification_question: Optional[str] = None
    direct_answer: Optional[str] = None
    reflection_prompt: Optional[str] = None
    confidence: float = 1.0

    def should_execute(self) -> bool:
        return self.decision == Decision.EXECUTE

    def needs_response(self) -> bool:
        return self.decision in (Decision.CLARIFY, Decision.ANSWER, Decision.IGNORE)


# ── DIRECT-ANSWER INTENTS ─────────────────────────────────────────────────
# These never need tool execution — answer from knowledge or templates

DIRECT_ANSWER_INTENTS = {
    "greet", "thank", "cancel",
    "quick_answer",   # handled by AI brain but no browser/app needed
    "recall_fact",    # memory lookup only
    "introduce_self", # just store + confirm
    "express_preference",  # just store + confirm
}

# ── INTENTS THAT ALWAYS NEED CLARIFICATION ────────────────────────────────
ALWAYS_CLARIFY = {
    "play_media":    ("song",),        # need what to play
    "make_call":     ("contact",),     # need who to call
    "send_message":  ("contact", "message_content"),
    "set_reminder":  ("reminder_text", "time"),
    "open_notepad_write": ("text",),
}
# CONTEXT_FREE_INTENTS = {
#             "close_tab", "new_tab", "read_page", "page_summary", 
#             "answer_question", "guided_recommendation"
#         }
# ── NOISE PATTERNS ────────────────────────────────────────────────────────
_NOISE_PATTERNS = [
    re.compile(r'^(um+|uh+|ah+|hmm+|mm+|oh+)$', re.I),
    re.compile(r'^[^a-zA-Z]{0,5}$'),
    re.compile(r'^(yes|no|ok|okay|sure|right|yep|nope)$', re.I),
]

# ── AMBIGUITY INDICATORS ──────────────────────────────────────────────────
_AMBIGUOUS_PHRASES = [
    "do something", "help me", "fix this", "make it work",
    "do it", "go ahead", "what should i do", "i need help",
    "figure it out", "handle it", "take care of it",
]


class DecisionEngine:
    """
    Decides what Jarvis should do with a understood intent.

    Call order (from agent/core.py):
        result = decision_engine.decide(intent, context, memory_context)
        if result.decision == Decision.EXECUTE:
            → run planner + executor
        elif result.decision == Decision.CLARIFY:
            → ask clarification_question
        elif result.decision == Decision.ANSWER:
            → return direct_answer
        elif result.decision == Decision.REFLECT:
            → re-run intent understanding with reflection_prompt
        elif result.decision == Decision.IGNORE:
            → silently skip or minimal ack
    """

    def __init__(self, config: Dict):
        self.config = config
        self.min_confidence_execute = config.get("min_confidence_execute", 0.45)
        self.min_confidence_clarify = config.get("min_confidence_clarify", 0.25)

    def decide(
        self,
        intent: Dict,
        context: Dict,
        memory_context: Dict
    ) -> DecisionResult:
        """
        Main decision method. Deterministic — no LLM calls here.

        Returns a DecisionResult with the chosen decision and reason.
        """
        intent_name  = intent.get("intent", "unknown")
        confidence   = intent.get("confidence", 0.5)
        entities     = intent.get("entities", {})
        original_text = intent.get("original_text", "")

        logger.info(f" Deciding: intent={intent_name} conf={confidence:.2f}")

        # ── 1. NOISE CHECK ─────────────────────────────────────────────────
        noise_result = self._check_noise(original_text)
        if noise_result:
            return noise_result

        # ── 2. VERY LOW CONFIDENCE → REFLECT ──────────────────────────────
        if confidence < self.min_confidence_clarify:
            return DecisionResult(
                decision=Decision.REFLECT,
                reason=f"Very low confidence ({confidence:.2f}) — need deeper analysis",
                reflection_prompt=(
                    f"The user said: '{original_text}'. "
                    f"Initial understanding was '{intent_name}' with confidence {confidence:.2f}. "
                    f"Think step-by-step about what the user really wants. "
                    f"Context: {self._summarize_context(context)}"
                ),
                confidence=confidence
            )

        # ── 3. UNKNOWN INTENT ──────────────────────────────────────────────
        if intent_name == "unknown":
            if confidence < 0.3:
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason="Intent unknown and confidence too low",
                    clarification_question=(
                        "I'm not sure I understood that, Sir. "
                        "Could you rephrase or be more specific?"
                    ),
                    confidence=confidence
                )
            # Medium confidence unknown → try to answer as a question
            return DecisionResult(
                decision=Decision.ANSWER,
                reason="Unknown intent but reasonable confidence — treat as question",
                direct_answer=None,  # Will be filled by ResponseEngine using AI
                confidence=confidence
            )

        # ── THE FIX: FAST PATH / HARD BYPASS FOR CONTEXT-FREE INTENTS ──────
        
        if intent_name in CONTEXT_FREE_INTENTS:
            return DecisionResult(
                decision=Decision.EXECUTE,
                reason="Context-free action — executing directly.",
                confidence=confidence
            )

        if intent_name in ("open_app", "close_app"):
            app_name = entities.get("app") or entities.get("name")
            
            if not app_name:
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason="open_app/close_app with no app name resolved",
                    clarification_question="Which application would you like me to open, Sir?",
                    confidence=0.3
                )
            if intent_name == "close_app":
                if self._looks_like_website(app_name):
                    intent["intent"] = "close_tab"
                    intent["entities"] = {}
                    logger.info(f"[Decision] '{app_name}' is a website — closing tab instead")
                    return DecisionResult(
                        decision=Decision.EXECUTE,
                        reason=f"Website detected — closing browser tab",
                        confidence=0.9
                    )
                return DecisionResult(
                    decision=Decision.EXECUTE,
                    reason=f"Closing app: {app_name}",
                    confidence=confidence
                )

            # OPEN APP: check local first, then try web
            try:
                from utils.app_locator import app_locator
                
                app_path = app_locator.find_app(app_name)
                
                if app_path:
                    # Found locally — execute
                    return DecisionResult(
                        decision=Decision.EXECUTE,
                        reason=f"App found locally: {app_path}",
                        confidence=confidence
                    )
                
                # [NEW: Phase 1 Architecture Fix] — Web fallback
                # Not found locally. Try resolving as website.
                website_url = self._resolve_as_website(app_name)
                if website_url:
                    # Re-route to open_url intent
                    intent["intent"] = "open_url"
                    intent["entities"] = {"url": website_url}
                    logger.info(f"[Decision]  App not local — opening {website_url} in browser")
                    return DecisionResult(
                        decision=Decision.EXECUTE,
                        reason=f"App not local — opening {website_url} in browser",
                        confidence=0.85
                    )
                
                # Neither local nor web — ask user
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason=f"'{app_name}' not found as app or website",
                    clarification_question=(
                        f"I couldn't find {app_name} on your system or as a website, Sir. "
                        f"Would you like me to search for it online?"
                    ),
                    confidence=0.5
                )
                
            except Exception as e:
                logger.error(f"App validation failed: {e}")
                # Fall through to web fallback even on error
                website_url = self._resolve_as_website(app_name)
                if website_url:
                    intent["intent"] = "open_url"
                    intent["entities"] = {"url": website_url}
                    return DecisionResult(
                        decision=Decision.EXECUTE,
                        reason=f"App lookup failed — opening {website_url} in browser",
                        confidence=0.7
                    )
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason="App validation system unavailable",
                    clarification_question="I couldn't verify the application. Could you repeat that, Sir?",
                    confidence=0.5
                )

        # ── 4. AMBIGUITY CHECK ─────────────────────────────────────────────
        ambiguity_result = self._check_ambiguity(original_text, intent_name, entities, context)
        if ambiguity_result:
            return ambiguity_result

        # ── 5. DIRECT-ANSWER INTENTS ───────────────────────────────────────
        if intent_name in DIRECT_ANSWER_INTENTS:
            direct = self._build_direct_answer(intent_name, entities, memory_context, context)
            return DecisionResult(
                decision=Decision.ANSWER,
                reason=f"Intent '{intent_name}' never needs tools",
                direct_answer=direct,
                confidence=confidence
            )

        # ── 6. REQUIRED SLOT CHECK ─────────────────────────────────────────
        slot_result = self._check_required_slots(intent_name, entities, memory_context)
        if slot_result:
            return slot_result

        # ── 7. CONFIDENCE THRESHOLD ────────────────────────────────────────
        if confidence < self.min_confidence_execute:
            return DecisionResult(
                decision=Decision.CLARIFY,
                reason=f"Confidence {confidence:.2f} below execution threshold",
                clarification_question=self._build_confidence_clarification(
                    intent_name, entities, original_text
                ),
                confidence=confidence
            )

        # ── 8. CONTEXT CONFLICT CHECK ──────────────────────────────────────
        conflict_result = self._check_context_conflicts(intent_name, entities, context)
        if conflict_result:
            return conflict_result

        # ── 9. EXECUTE ─────────────────────────────────────────────────────
        return DecisionResult(
            decision=Decision.EXECUTE,
            reason=f"All checks passed — executing '{intent_name}'",
            confidence=confidence
        )
    
    def _looks_like_website(self, name: str) -> bool:
        """Heuristic: does this name look like a website, not a desktop app?"""
        name_lower = name.lower().strip()
        # Contains a TLD
        if any(name_lower.endswith(tld) for tld in ['.com', '.org', '.net', '.io', '.in']):
            return True
        # Known web services (heuristic: common web brands)
        web_brands = {'youtube', 'gmail', 'netflix', 'twitter', 'facebook', 
                      'instagram', 'reddit', 'github', 'amazon', 'twitch'}
        if name_lower in web_brands:
            return True
        return False
    
    # [NEW: Phase 1 Architecture Fix] — Web resolution heuristic
    def _resolve_as_website(self, name: str) -> Optional[str]:
        """
        Convert an app name to its canonical website URL without hardcoding.
        Uses heuristics only — no hardcoded dict of websites.
        """
        name_lower = name.lower().strip()
        
        # Heuristic 1: Already looks like a domain
        if '.' in name_lower and ' ' not in name_lower:
            url = f"https://{name_lower}"
            logger.info(f"[Decision] Resolved as domain: {url}")
            return url
        
        # Heuristic 2: Single word, alphanumeric → try .com
        if ' ' not in name_lower and name_lower.replace('-', '').replace('_', '').isalnum():
            url = f"https://www.{name_lower}.com"
            logger.info(f"[Decision] Resolved as website: {url}")
            return url
        
        # Heuristic 3: Multi-word → use search engine
        from urllib.parse import quote_plus
        url = f"https://duckduckgo.com/?q={quote_plus(name)}"
        logger.info(f"[Decision] Resolved as search: {url}")
        return url
    # ── PRIVATE CHECKS ─────────────────────────────────────────────────────

    def _check_noise(self, text: str) -> Optional[DecisionResult]:
        """Detect if the input is noise / not a real command."""
        stripped = text.strip().lower()
        if len(stripped) < 2:
            return DecisionResult(
                decision=Decision.IGNORE,
                reason="Input too short to be a command",
                confidence=1.0
            )
        for pattern in _NOISE_PATTERNS:
            if pattern.match(stripped):
                return DecisionResult(
                    decision=Decision.IGNORE,
                    reason="Input matches noise pattern",
                    confidence=1.0
                )
        return None

    def _check_ambiguity(
        self, text: str, intent_name: str, entities: Dict, context: Dict
    ) -> Optional[DecisionResult]:
        """Detect genuinely ambiguous commands."""
        text_lower = text.lower().strip()
        # ── THE FIX: Exempt tab/page/questions from needing context resolution ──
        exempt_intents = {
            "close_tab", "new_tab", "read_page", "page_summary", 
            "answer_question", "guided_recommendation"
        }
        for phrase in _AMBIGUOUS_PHRASES:
            if phrase in text_lower and not entities:
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason=f"Ambiguous command: '{phrase}'",
                    clarification_question=(
                        f"I want to help, Sir. Could you be more specific? "
                        f"What would you like me to do?"
                    ),
                    confidence=0.3
                )

        # Implicit reference but no context to resolve it
        implicit_words = ["it", "that", "this", "again"]
        has_implicit = any(w in text_lower.split() for w in implicit_words)
        if has_implicit and not any([
            context.get("last_app"),
            context.get("last_song"),
            context.get("last_url"),
            context.get("last_entity"),
        ]):
            return DecisionResult(
                decision=Decision.CLARIFY,
                reason="Implicit reference but no context to resolve",
                clarification_question=(
                    "I'm not sure what you're referring to, Sir. "
                    "Could you be more specific?"
                ),
                confidence=0.4
            )

        return None

    def _check_required_slots(
    self, intent_name: str, entities: Dict, memory_context: Dict
    ) -> Optional[DecisionResult]:
        """
        Check if required slots are missing.
        But FIRST check memory for defaults — e.g. preferred platform.
        """
        # [NEW: Phase 1 Architecture Fix] — Use IntentRegistry instead of hardcoded dict
        required = get_required_slots(intent_name)
        if not required:
            return None

        prefs = {p["key"]: p["value"] for p in memory_context.get("preferences", [])}

        missing = []
        for slot in required:
            if entities.get(slot):
                continue  # Slot is filled

            # Try to fill from memory
            if slot == "platform":
                preferred = (
                    prefs.get("preferred_music_platform") or
                    prefs.get("preferred_platform")
                )
                if preferred:
                    entities[slot] = preferred
                    logger.info(f" Filled slot '{slot}' from memory: {preferred}")
                    continue

            missing.append(slot)

        if not missing:
            return None

        questions = {
            "song":            "What would you like me to play, Sir?",
            "contact":         "Who should I contact, Sir?",
            "platform":        "Which platform — Spotify or YouTube, Sir?",
            "message_content": "What should I say, Sir?",
            "time":            "At what time, Sir?",
            "reminder_text":   "What should I remind you about, Sir?",
            "text":            "What should I write, Sir?",
        }
        from agent.clarifier import get_clarification
        memory_prefs = {p["key"]: p["value"] for p in memory_context.get("preferences", [])}
        question, still_missing = get_clarification(
            intent_name, entities, missing, memory_prefs
        )

        return DecisionResult(
            decision=Decision.CLARIFY,
            reason=f"Missing required slots: {missing}",
            clarification_question=question,
            confidence=0.9
        )

    def _check_context_conflicts(
        self, intent_name: str, entities: Dict, context: Dict
    ) -> Optional[DecisionResult]:
        """Detect conflicts between intent and current context."""
        # Example: user says "close spotify" but spotify isn't open
        from agent.world_model import world
        if intent_name == "close_app":
            app = entities.get("app", "").lower()
            active = world.active_app.lower() or context.get("active_app", "").lower()
            last = context.get("last_app", "")

        if intent_name in ("next_track", "previous_track", "pause_media"):
            last_song = world.last_song or context.get("last_song")
            active_app = world.active_app or context.get("active_app", "")
            if not last_song and context.get("active_app") not in ("spotify", "vlc", "chrome"):
                return DecisionResult(
                    decision=Decision.CLARIFY,
                    reason="Media control requested but nothing appears to be playing",
                    clarification_question=(
                        "I don't think anything is playing, Sir. "
                        "Would you like me to start something?"
                    ),
                    confidence=0.6
                )

        return None

    def _build_direct_answer(
        self, intent_name: str, entities: Dict, memory: Dict, context: Dict
    ) -> Optional[str]:
        """
        Build a direct answer for intents that don't need tools.
        Returns None if the ResponseEngine should handle it.
        """
        prefs = {p["key"]: p["value"] for p in memory.get("preferences", [])}
        personal = {p["key"]: p["value"] for p in memory.get("personal", [])}

        if intent_name == "greet":
            name = personal.get("user_name", "")
            greeting = f"Good to see you{', ' + name if name else ''}, Sir. All systems operational."
            return greeting

        if intent_name == "thank":
            return "Always a pleasure, Sir."

        if intent_name == "cancel":
            return "Understood, Sir."

        if intent_name == "express_preference":
            fact = entities.get("fact", entities.get("preference", ""))
            return f"Noted, Sir. I'll remember that." if fact else "Got it, Sir."

        if intent_name == "introduce_self":
            name = entities.get("name", "")
            return f"Pleased to meet you{', ' + name if name else ''}, Sir. I'll remember you."

        # Let ResponseEngine + memory handle recall_fact and quick_answer
        return None

    def _build_confidence_clarification(
        self, intent_name: str, entities: Dict, original_text: str
    ) -> str:
        """Generate a natural clarification for low-confidence intents."""
        # Try to reference what we thought we heard
        entity_str = ""
        if entities:
            first_val = next(iter(entities.values()), "")
            if first_val:
                entity_str = f" about '{first_val}'"

        return (
            f"I think you want me to {intent_name.replace('_', ' ')}{entity_str}, "
            f"but I'm not entirely sure, Sir. Could you confirm?"
        )

    def _summarize_context(self, context: Dict) -> str:
        parts = []
        if context.get("last_app"):
            parts.append(f"last_app={context['last_app']}")
        if context.get("last_song"):
            parts.append(f"last_song={context['last_song']}")
        if context.get("active_app"):
            parts.append(f"active={context['active_app']}")
        return ", ".join(parts) if parts else "no prior context"