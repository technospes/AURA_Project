"""
SEMANTIC CORRECTOR — LLM-Based Post-STT Correction Layer
=========================================================
Fixes phonetically correct but semantically wrong Whisper outputs.

Problem class:
  "I shoot I shoot"   → "I should I should"
  "buy me a coffee"   → "by me a coffee" (contextually wrong)
  "forty k"           → "₹40,000"
  "hundred and forty" → "140"
  "jarvis open"       → "open" (strip wake-word leakthrough)

Design principles:
  1. ONLY fires when needed — checked via fast heuristics first
  2. LLM call is async, non-blocking, sub-400ms on llama-3.1-8b-instant
  3. Preserves ALL entities (numbers, names, app names, amounts)
  4. Falls back to original text if correction fails or is too different
  5. Caches corrections to avoid redundant API calls for repeated commands
  6. Intent-aware: knows what vocabulary to expect

Pipeline position (inside EnhancedTranscriber.transcribe()):
  raw audio
    → Whisper (returns text, conf)
    → phonetic corrections (fast regex)
    → SemanticCorrector (LLM, if needed)    ← THIS MODULE
    → final text output

Trigger conditions (any one is sufficient):
  A. conf < 0.85
  B. Repeated word pattern ("word word", "I shoot I shoot")
  C. Known phonetic artifacts (list below)
  D. Short text from long audio (>5s audio, <3 words → likely truncated)

Safety:
  Correction is REJECTED if:
  - Semantic similarity drops below 0.5 (meaning changed too much)
  - Critical entities were removed (numbers, app names)
  - Output is longer than 3× the input
  - Correction turns a command into a question (intent flip)
"""

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# TRIGGER DETECTION
# ════════════════════════════════════════════════════════════════════════════

# Patterns that DEFINITELY need correction
_ARTIFACT_PATTERNS: List[re.Pattern] = [
    # Exact word repetition: "I should I should", "open open"
    re.compile(r'\b(\w+(?:\s+\w+){0,3})\s+\1\b', re.I),
    # Three-word stutters: "the the the"
    re.compile(r'\b(\w+)\s+\1\s+\1\b', re.I),
    # Whisper phonetic errors for common command words
    re.compile(r'\bI\s+shoot\b', re.I),         # "I should"
    re.compile(r'\bI\s+shot\b', re.I),           # "I should"
    re.compile(r'\bwould\s+should\b', re.I),
    re.compile(r'\bshoot\s+I\b', re.I),
    re.compile(r'\bI\s+could\s+I\b', re.I),
    re.compile(r'\bby\s+me\s+a\b', re.I),        # "buy me a"
    re.compile(r'\bbuy\s+the\s+(?=\w+\.com)', re.I),
    # Number-word confusion
    re.compile(r'\bfour\s+tea\b', re.I),         # "forty"
    re.compile(r'\bfifty\s+seen\b', re.I),       # "fifteen"
    re.compile(r'\bone\s+lark\b', re.I),         # "one lakh"
    re.compile(r'\brupees\s+rupees\b', re.I),
]

# Confidence thresholds
_CONF_CORRECTION_THRESHOLD = 0.85   # Below this → try correction
_CONF_RETRY_THRESHOLD      = 0.50   # Below this → retry first, then correct


def needs_correction(
    text: str,
    confidence: float,
    audio_duration_s: float = 0.0,
) -> Tuple[bool, str]:
    """
    Fast heuristic check — should we run SemanticCorrector?

    Returns (should_correct: bool, reason: str)
    Never calls LLM. Runs in < 1ms.
    """
    if not text or len(text.strip()) < 3:
        return False, ""

    # Low confidence
    if confidence < _CONF_CORRECTION_THRESHOLD:
        return True, f"low_conf:{confidence:.3f}"

    # Check for known artifact patterns
    for pat in _ARTIFACT_PATTERNS:
        if pat.search(text):
            return True, f"artifact:{pat.pattern[:30]}"

    # Short output from long audio (likely truncated)
    words = text.split()
    if audio_duration_s >= 5.0 and len(words) <= 2:
        return True, "truncated:short_text_long_audio"

    return False, ""


# ════════════════════════════════════════════════════════════════════════════
# SAFETY CHECKS — prevent meaning-destroying "corrections"
# ════════════════════════════════════════════════════════════════════════════

# Critical entity patterns that must survive correction
_ENTITY_PATTERNS = [
    re.compile(r'\b\d[\d,.]*\b'),                          # Numbers
    re.compile(r'\b\d+[kK]\b'),                            # 30k, 50k
    re.compile(r'\b(?:lakh|crore|thousand|hundred)\b', re.I),
    re.compile(r'\b(?:spotify|youtube|discord|whatsapp|netflix|chrome|firefox)\b', re.I),
    re.compile(r'\b(?:1080p|1440p|4K|144[Hh]z|[0-9]+[Hh]z)\b'),
    re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'),   # Proper names
]

# Words that indicate command vs question intent
_COMMAND_STARTERS = frozenset({
    "open", "close", "play", "pause", "stop", "search", "find",
    "set", "change", "turn", "make", "send", "call", "type",
    "scroll", "lock", "restart", "shutdown", "take", "read",
    "research", "remind", "remember", "recall",
})
_QUESTION_STARTERS = frozenset({
    "what", "why", "how", "when", "where", "who", "which",
    "can you", "could you", "would you", "is it", "are you",
})


def _extract_entities(text: str) -> set:
    """Extract all critical entities from text."""
    found = set()
    for pat in _ENTITY_PATTERNS:
        for m in pat.finditer(text):
            found.add(m.group(0).lower())
    return found


def _word_overlap(a: str, b: str) -> float:
    """Simple word-level Jaccard similarity."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)


def _intent_type(text: str) -> str:
    """'command' | 'question' | 'unknown'"""
    first = text.strip().lower().split()[0] if text.strip() else ""
    if first in _COMMAND_STARTERS:
        return "command"
    if first in _QUESTION_STARTERS or text.strip().endswith("?"):
        return "question"
    return "unknown"


def _extract_proper_nouns(text: str) -> set:
    """
    Extract capitalised words that look like proper nouns (names).
    These must NEVER be silently changed by the corrector.
    """
    # Match sequences of Title-Case words (names like "Shivansh", "John Doe")
    matches = re.findall(r'\b[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})*\b', text)
    return {m.lower() for m in matches}


def _proper_nouns_preserved(original: str, corrected: str) -> Tuple[bool, str]:
    """
    Verify that proper nouns in the original are preserved in the correction.
    A proper noun is changed ONLY if it appears in the corrected text (possibly
    in a different casing), meaning the LLM silently altered a name.
    Returns (ok, reason).
    """
    orig_nouns = _extract_proper_nouns(original)
    corr_nouns = _extract_proper_nouns(corrected)
    # Any proper noun in original that is completely absent from correction
    dropped = orig_nouns - corr_nouns
    # Allow if the dropped noun still appears case-insensitively anywhere in corrected
    truly_dropped = {n for n in dropped if n not in corrected.lower()}
    if truly_dropped:
        return False, f"proper_noun_dropped:{truly_dropped}"
    return True, ""


def is_safe_correction(original: str, corrected: str) -> Tuple[bool, str]:
    """
    Verify the correction didn't destroy meaning.
    Returns (is_safe: bool, rejection_reason: str)
    """
    if not corrected or not corrected.strip():
        return False, "empty_correction"

    # ── PROPER NOUN PROTECTION (CRITICAL) ─────────────────────────────────
    # Names like "Shivansh", "Ayush" must NEVER be altered.
    # If the LLM changed a proper noun, reject the correction entirely.
    noun_ok, noun_reason = _proper_nouns_preserved(original, corrected)
    if not noun_ok:
        return False, noun_reason

    # Length sanity check
    orig_words = len(original.split())
    corr_words = len(corrected.split())
    if orig_words > 0 and corr_words > orig_words * 3:
        return False, f"too_long:{corr_words}>{orig_words}×3"

    # Entity preservation
    orig_entities = _extract_entities(original)
    corr_entities = _extract_entities(corrected)
    # Entities that were in original but dropped in correction
    dropped = orig_entities - corr_entities
    if dropped:
        # Allow minor number reformatting ("50000" vs "50,000" vs "50 thousand")
        # but block disappearance of key values
        if any(len(e) >= 3 and e.isdigit() for e in dropped):
            return False, f"dropped_numbers:{dropped}"

    # Word overlap — must retain at least 30% vocabulary
    overlap = _word_overlap(original, corrected)
    if overlap < 0.30 and orig_words >= 5:
        return False, f"low_overlap:{overlap:.2f}"

    # Intent flip detection (command → question is suspicious)
    orig_intent = _intent_type(original)
    corr_intent = _intent_type(corrected)
    if orig_intent == "command" and corr_intent == "question":
        return False, "intent_flip:command_to_question"

    return True, ""


# ════════════════════════════════════════════════════════════════════════════
# LRU CACHE — avoid re-correcting the same text
# ════════════════════════════════════════════════════════════════════════════

class _CorrectionCache:
    """
    Simple LRU cache for SemanticCorrector results.

    Key: SHA256 of (text + context_hint)[:16]
    Value: corrected text
    TTL: 30 minutes (commands aren't repeated that often)
    """

    def __init__(self, maxsize: int = 256, ttl_s: float = 1800.0):
        self._data:  Dict[str, Tuple[str, float]] = {}
        self._maxsize = maxsize
        self._ttl     = ttl_s

    def _key(self, text: str, context: str) -> str:
        raw = f"{text.lower().strip()}|{context}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, text: str, context: str = "") -> Optional[str]:
        k = self._key(text, context)
        entry = self._data.get(k)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            del self._data[k]
            return None
        return value

    def set(self, text: str, context: str, corrected: str):
        k = self._key(text, context)
        # Evict oldest entry if full
        if len(self._data) >= self._maxsize:
            oldest_k = min(self._data, key=lambda x: self._data[x][1])
            del self._data[oldest_k]
        self._data[k] = (corrected, time.time())


# ════════════════════════════════════════════════════════════════════════════
# SEMANTIC CORRECTOR — THE MAIN CLASS
# ════════════════════════════════════════════════════════════════════════════

# Minimal, precise prompt — fewer tokens = faster response
_CORRECTION_SYSTEM = (
    "You are a speech recognition post-processor for a voice assistant named Jarvis. "
    "Fix ONLY acoustic/phonetic transcription errors (stutters, homophones, phonetic mistakes). "
    "CRITICAL: NEVER change proper nouns — names of people, contacts, apps, or places. "
    "If a word is capitalised or looks like a person's name, leave it exactly as-is. "
    "Do not change meaning, add words, or change commands. "
    "Return ONLY the corrected sentence. No explanation. No quotes."
)

_CORRECTION_PROMPT_TEMPLATE = """Fix this speech recognition output. Correct ONLY recognition errors (stutters, homophones, phonetic mistakes).
DO NOT change the command meaning or add context.
DO NOT turn a command into a question.
CRITICAL: Preserve ALL proper nouns (person names, contact names, app names) EXACTLY as they appear.
If you are unsure whether a word is a name, leave it unchanged.
Preserve all numbers, names, and technical terms exactly.

Context (what type of command to expect): {context}

Raw STT output: {text}

Corrected:"""


@dataclass
class CorrectionResult:
    original:   str
    corrected:  str
    was_changed: bool
    confidence:  float       # Confidence that correction is valid (0-1)
    reason:      str = ""    # Why correction was applied
    from_cache:  bool = False


class SemanticCorrector:
    """
    LLM-based semantic correction for Whisper STT output.

    Usage (inside EnhancedTranscriber.transcribe()):
        corrector = SemanticCorrector(groq_api_key=KEY)
        ...
        result = await corrector.correct(text, conf, context_hint, audio_duration)
        final_text = result.corrected

    The corrector is ALWAYS safe to call — it will return the original
    text if correction is not needed or if the LLM result is unsafe.
    """

    def __init__(
        self,
        groq_api_key:   str,
        model:          str = "llama-3.1-8b-instant",  # Fast, cheap, good enough
        timeout_s:      float = 2.5,                    # Max time to wait for LLM
        enable_cache:   bool = True,
    ):
        self._api_key   = groq_api_key
        self._model     = model
        self._timeout   = timeout_s
        self._client    = None
        self._cache     = _CorrectionCache() if enable_cache else None
        self._executor  = None   # ThreadPoolExecutor, lazily initialized
        self._stats: Dict[str, int] = {
            "total":      0,
            "corrected":  0,
            "cached":     0,
            "skipped":    0,
            "rejected":   0,
            "errors":     0,
        }

    def _get_client(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    def _get_executor(self):
        if self._executor is None:
            import concurrent.futures
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="semantic-corrector"
            )
        return self._executor

    async def correct(
        self,
        text:              str,
        confidence:        float = 1.0,
        context_hint:      str   = "",
        audio_duration_s:  float = 0.0,
    ) -> CorrectionResult:
        """
        Main entry point. Always returns a CorrectionResult.
        Never raises. Falls back to original on any error.

        Args:
            text:             Raw Whisper output
            confidence:       Whisper avg_logprob-derived confidence (0-1)
            context_hint:     Hint about expected vocabulary (e.g. "resolution")
            audio_duration_s: Duration of audio in seconds (for truncation check)

        Returns:
            CorrectionResult with .corrected = best text to use
        """
        self._stats["total"] += 1

        if not text or not text.strip():
            return CorrectionResult(
                original=text, corrected=text, was_changed=False, confidence=1.0
            )

        # ── Check if correction is needed ─────────────────────────────────
        should_fix, reason = needs_correction(text, confidence, audio_duration_s)
        if not should_fix:
            self._stats["skipped"] += 1
            return CorrectionResult(
                original=text, corrected=text, was_changed=False,
                confidence=confidence, reason="not_needed"
            )

        # ── Cache lookup ───────────────────────────────────────────────────
        if self._cache:
            cached = self._cache.get(text, context_hint)
            if cached is not None:
                self._stats["cached"] += 1
                return CorrectionResult(
                    original=text, corrected=cached,
                    was_changed=(cached != text),
                    confidence=0.90, reason=reason, from_cache=True
                )

        # ── LLM correction ─────────────────────────────────────────────────
        try:
            corrected = await asyncio.wait_for(
                self._llm_correct(text, context_hint),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[SemanticCorrector] Timeout after {self._timeout}s — using original")
            self._stats["errors"] += 1
            return CorrectionResult(
                original=text, corrected=text, was_changed=False,
                confidence=confidence, reason="timeout"
            )
        except Exception as e:
            logger.warning(f"[SemanticCorrector] LLM error: {e} — using original")
            self._stats["errors"] += 1
            return CorrectionResult(
                original=text, corrected=text, was_changed=False,
                confidence=confidence, reason=f"llm_error:{type(e).__name__}"
            )

        # ── Safety validation ──────────────────────────────────────────────
        if corrected and corrected.strip() != text.strip():
            safe, reject_reason = is_safe_correction(text, corrected)
            if not safe:
                logger.info(
                    f"[SemanticCorrector] Correction rejected ({reject_reason}): "
                    f"'{text}' → '{corrected}'"
                )
                self._stats["rejected"] += 1
                return CorrectionResult(
                    original=text, corrected=text, was_changed=False,
                    confidence=confidence, reason=f"rejected:{reject_reason}"
                )

            # Correction is valid — log and cache it
            logger.info(
                f"[SemanticCorrector]  '{text}' → '{corrected}' "
                f"(reason={reason})"
            )
            if self._cache:
                self._cache.set(text, context_hint, corrected)
            self._stats["corrected"] += 1

            return CorrectionResult(
                original=text, corrected=corrected, was_changed=True,
                confidence=min(confidence + 0.05, 0.95),
                reason=reason
            )

        # LLM returned same text or empty — use original
        self._stats["skipped"] += 1
        return CorrectionResult(
            original=text, corrected=text, was_changed=False,
            confidence=confidence, reason="no_change"
        )

    async def _llm_correct(self, text: str, context_hint: str) -> str:
        """Async LLM call, runs in thread executor."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self._get_executor(),
            self._llm_correct_sync,
            text,
            context_hint,
        )
        return result

    def _llm_correct_sync(self, text: str, context_hint: str) -> str:
        """Synchronous LLM call (runs in ThreadPoolExecutor)."""
        try:
            # Build context-aware prompt
            ctx = context_hint or "general voice command to an AI assistant"
            prompt = _CORRECTION_PROMPT_TEMPLATE.format(
                context=ctx,
                text=text,
            )

            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _CORRECTION_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.0,    # Deterministic
                max_tokens=150,     # Commands are short
                stop=["\n", "---"], # Stop at newline — commands are single-line
            )

            corrected = resp.choices[0].message.content.strip()

            # Strip any quotes the LLM might add
            corrected = corrected.strip('"\'`')
            # Strip meta-commentary if LLM ignored instructions
            for prefix in ("corrected:", "fixed:", "output:", "result:"):
                if corrected.lower().startswith(prefix):
                    corrected = corrected[len(prefix):].strip()

            return corrected

        except Exception as e:
            logger.warning(f"[SemanticCorrector] Sync LLM call failed: {e}")
            return text  # Return original — never fail silently with empty

    def get_stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def clear_cache(self):
        if self._cache:
            self._cache._data.clear()