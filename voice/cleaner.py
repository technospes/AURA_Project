"""
VOICE INPUT CLEANER
===================
Runs BEFORE intent parsing. Normalises raw Whisper transcription into
a clean command string. Handles the most common Whisper artefacts.

Problems solved:
  1. Duplicate phrases   — "open youtube open youtube" → "open youtube"
  2. Filler words        — "uh Jarvis um open youtube" → "open youtube"
  3. Wake-word residue   — "jarvis open youtube" → "open youtube"
  4. Trailing noise      — "open youtube please thanks" → "open youtube"
  5. Punctuation noise   — "Open YouTube." → "open youtube"
  6. Repeated words      — "open open youtube" → "open youtube"
  7. Multi-command split — "open youtube and play Starboy" → two commands

Usage (standalone):
    cleaner = InputCleaner()
    clean, commands = cleaner.process("uh jarvis open youtube open youtube")
    # clean    = "open youtube"
    # commands = ["open youtube"]

Usage (multi-command):
    clean, commands = cleaner.process("open youtube and play Starboy")
    # commands = ["open youtube", "play Starboy"]
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── WORD LISTS ─────────────────────────────────────────────────────────────

WAKE_WORDS = {
    "jarvis", "hey jarvis", "ok jarvis", "yo jarvis", "hello jarvis",
    "jarves", "jarvish", "jarvi", "garvis", "harvis",'jarvis',
            'jarvis,',  # With comma
            'hey jarvis',
            'ok jarvis',
            'yo jarvis',
            'listen jarvis',
            'hello jarvis',
            'jarvis listen',
            
            # Common mispronunciations/misrecognitions
            'jarviz',    
            'jarvas',
            'jarvus',
            'jarvez',
            'jervis',
            'jerviz',
            'jerwis',
            'jarvish',
            'jarvys',
            'jarvus',
            'jarvas',
            
            # Short/partial detections
            'jarvi',
            'jarv',
            'jarvy',
            'jerv',
            'jervy',
            
            # With different phonetics
            'jar vis',  # Two words
            'jar-vis',  # Hyphenated
            'jar ves',
            'jarves',
            'jharvis',
            'jharvish',
            
            # Accent variations
            'jaavis',  # Rolling 'r' omission
            'javvis',  # Double 'v'
            'jarfis',  # 'v' -> 'f' confusion
            'charvis',  # 'j' -> 'ch' confusion
            'yarvis',  # 'j' -> 'y' confusion
            'zharvis',  # 'j' -> 'zh' confusion
            
            # Whisper/TTS misrecognitions
            'jarvis\'s',
            'jarvis is',
            'jarvis the',
            'jarvis can',
            
            # AI model common hallucinations
            'garvis',  # 'j' -> 'g' confusion
            'carvis',  # 'j' -> 'c' confusion
            'harvis',  # 'j' -> 'h' confusion
            'marvis',  # 'j' -> 'm' confusion
            
            # Phonetic variations for non-English speakers
            'jarwiz',
            'jarwish',
            'jarwez',
            'jarweez',
            'jarveez',
            'jerveez',
            
            # Sound-alike names
            'harvis',  # Like "Harvis"
            'marvis',  # Like "Marvis"
            'garvis',  # Like "Garvis"
            
            # Quick/slurred speech
            'jarvisss',  # Extended 's'
            'jarvis\'',  # With apostrophe
            'jarvis?',  # With question mark
            
            # With filler words
            'um jarvis',
            'ah jarvis',
            'like jarvis',
            'so jarvis',
            
            # Whispered/silent versions
            'j...arvis',  # Pause in middle
            'ja...rvis',  # Pause in middle
}

FILLER_WORDS = {
    "uh", "um", "ah", "er", "hmm", "hm", "mm", "mhm", "like",
    "you know", "i mean", "so", "well", "right", "okay so",
}

# Words at the END of a command that are polite but meaningless to intent
TRAILING_NOISE = {
    "please", "thanks", "thank you", "cheers", "okay", "alright",
    "now", "for me", "if you can", "if possible", "would you",
}

# Conjunctions that split multi-commands
COMMAND_SPLITTERS = [
    r"\band\b(?:\s+also\b)?",   # "open X and play Y"
    r"\bthen\b",                 # "open X then play Y"
    r"\bafter\s+that\b",         # "open X after that play Y"
    r"\balso\b",                 # "open X also play Y"
]

_SPLITTER_RE = re.compile(
    "|".join(COMMAND_SPLITTERS),
    re.IGNORECASE
)


class InputCleaner:
    """
    Cleans a raw Whisper transcript into one or more normalised commands.
    Thread-safe — no mutable state.
    """

    def process(self, raw: str) -> Tuple[str, List[str]]:
        """
        Main entry point.

        Args:
            raw: Raw transcription from Whisper

        Returns:
            (primary_command, list_of_all_commands)
            primary_command is the first (or only) command.
        """
        if not raw or not raw.strip():
            return "", []

        text = raw.strip()

        # ── STEP 1: Lowercase + strip punctuation ─────────────────────────
        text = self._normalize_punctuation(text)

        # ── STEP 2: Remove wake word ──────────────────────────────────────
        text = self._strip_wake_word(text)

        # ── STEP 3: Remove leading fillers ───────────────────────────────
        text = self._strip_fillers(text)

        # ── STEP 4: Remove trailing noise words ──────────────────────────
        text = self._strip_trailing_noise(text)

        # ── STEP 5: De-duplicate repeated adjacent words ──────────────────
        # "open open youtube" → "open youtube"
        text = self._dedup_words(text)

        # ── STEP 6: De-duplicate repeated phrases ────────────────────────
        # "open youtube open youtube" → "open youtube"
        text = self._dedup_phrases(text)

        # ── STEP 7: Collapse whitespace ──────────────────────────────────
        text = " ".join(text.split())

        if not text:
            return "", []

        # ── STEP 8: Split into multiple commands ──────────────────────────
        commands = self._split_commands(text)

        primary = commands[0] if commands else text

        if len(commands) > 1:
            logger.info(f"🔀 Multi-command split: {commands}")
        else:
            logger.debug(f"🧹 Cleaned: '{raw}' → '{primary}'")

        return primary, commands

    # ── PRIVATE ────────────────────────────────────────────────────────────

    def _normalize_punctuation(self, text: str) -> str:
        """Lowercase and strip non-alphanumeric punctuation at boundaries."""
        text = text.lower()
        # Keep apostrophes (it's, don't), hyphens in compound words
        # Remove sentence-ending punctuation
        text = re.sub(r"[.!?]+$", "", text)
        text = re.sub(r"[,;:]+", " ", text)
        return text.strip()

    def _strip_wake_word(self, text: str) -> str:
        """Remove Jarvis wake-word variants from the start."""
        for wake in sorted(WAKE_WORDS, key=len, reverse=True):
            # Match at start, optionally followed by comma/space
            pattern = re.compile(
                rf"^{re.escape(wake)}\s*[,.]?\s*",
                re.IGNORECASE
            )
            cleaned = pattern.sub("", text).strip()
            if cleaned != text:
                logger.debug(f"Wake word stripped: '{wake}'")
                return cleaned
        return text

    def _strip_fillers(self, text: str) -> str:
        """Remove filler words from the START of the command only."""
        changed = True
        while changed:
            changed = False
            for filler in sorted(FILLER_WORDS, key=len, reverse=True):
                pattern = re.compile(
                    rf"^{re.escape(filler)}\s+",
                    re.IGNORECASE
                )
                new = pattern.sub("", text).strip()
                if new != text:
                    text = new
                    changed = True
                    break
        return text

    def _strip_trailing_noise(self, text: str) -> str:
        """Remove polite trailing words that don't affect intent."""
        changed = True
        while changed:
            changed = False
            for noise in sorted(TRAILING_NOISE, key=len, reverse=True):
                pattern = re.compile(
                    rf"\s+{re.escape(noise)}\s*$",
                    re.IGNORECASE
                )
                new = pattern.sub("", text).strip()
                if new != text:
                    text = new
                    changed = True
                    break
        return text

    def _dedup_words(self, text: str) -> str:
        """Remove immediately repeated words: 'open open youtube' → 'open youtube'."""
        words = text.split()
        result = []
        for i, w in enumerate(words):
            if i == 0 or w != words[i - 1]:
                result.append(w)
        return " ".join(result)

    def _dedup_phrases(self, text: str) -> str:
        """
        Detect and remove duplicated full phrases.

        Strategy: try splitting the string at every midpoint;
        if both halves are similar, keep only the first half.
        This handles "open youtube open youtube" reliably.
        """
        words = text.split()
        n = len(words)

        for half in range(2, n // 2 + 1):
            first = words[:half]
            second = words[half:half * 2]
            if first == second:
                logger.debug(f"Duplicate phrase removed: '{' '.join(second)}'")
                return " ".join(first)

        # Fallback: sentence-level dedup (handles longer repetitions)
        # Split on period/exclamation that Whisper may have injected mid-string
        sentences = re.split(r"[.!?]\s+", text)
        if len(sentences) > 1:
            seen = []
            for s in sentences:
                s = s.strip()
                if s and s not in seen:
                    seen.append(s)
            return ". ".join(seen)

        return text

    def _split_commands(self, text: str) -> List[str]:
        """
        Split a multi-command string into individual commands.

        "open youtube and play Starboy" → ["open youtube", "play starboy"]

        Only splits if BOTH parts look like real commands (>= 2 words each,
        or match a known command verb).
        """
        parts = _SPLITTER_RE.split(text)
        cleaned = [p.strip() for p in parts if p and p.strip()]

        if len(cleaned) <= 1:
            return cleaned or [text]

        # Validate each part looks like a real command
        valid = []
        for part in cleaned:
            part = part.strip()
            if not part:
                continue
            words = part.split()
            if len(words) >= 2 or self._looks_like_command(part):
                valid.append(part)
            else:
                # Too short — merge back to previous
                if valid:
                    valid[-1] = valid[-1] + " and " + part
                else:
                    valid.append(part)

        return valid if valid else [text]

    def _looks_like_command(self, text: str) -> bool:
        """Heuristic: does this fragment start with a known command verb?"""
        COMMAND_VERBS = {
            "open", "close", "play", "pause", "stop", "search", "type",
            "scroll", "click", "read", "research", "remember", "recall",
            "send", "call", "message", "lock", "screenshot", "shutdown",
            "restart", "resume", "next", "previous", "skip", "volume",
        }
        first_word = text.split()[0].lower() if text.split() else ""
        return first_word in COMMAND_VERBS


# ── CONVENIENCE FUNCTION ────────────────────────────────────────────────────

_default_cleaner = InputCleaner()


def clean_input(raw: str) -> Tuple[str, List[str]]:
    """
    Module-level convenience function.
    Returns (primary_command, all_commands).
    """
    return _default_cleaner.process(raw)
