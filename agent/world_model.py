"""
WORLD MODEL — Shared Runtime State for Jarvis
==============================================
[NEW: Phase 1 Architecture Fix]

Single source of truth for what Jarvis currently "knows" about the world:
  - Which app is active
  - What URL is open
  - What the last entity/action was
  - Page context (title + text)

All modules read from `world` instead of passing context dicts around.
Updates are atomic (lock-protected).

Usage:
    from agent.world_model import world

    # Update after an action:
    world.update(active_app="spotify", last_song="Blinding Lights")

    # Read in decision engine:
    ctx = world.snapshot()
    if ctx["active_app"] == "spotify":
        ...

    # Check before implicit-reference resolution:
    if world.has_page_context():
        text = world.current_page_text
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ════════════════════════════════════════════════════════════════════════════
# WORLD MODEL DATACLASS
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class WorldModel:
    """
    [NEW: Phase 1 Architecture Fix]
    Mutable singleton representing Jarvis's current understanding of the world.
    All fields are optional — unknown state is represented as None / "".
    Thread-safe via internal RLock.
    """

    # ── App state ─────────────────────────────────────────────────────────
    active_app:   str = "desktop"   # e.g. "spotify", "chrome", "desktop"
    last_app:     str = ""

    # ── Browser / web state ───────────────────────────────────────────────
    current_url:        str = ""
    current_page_title: str = ""
    current_page_text:  str = ""

    # ── Media state ───────────────────────────────────────────────────────
    last_song:     str = ""
    last_artist:   str = ""
    is_playing:    bool = False

    # ── Entity tracking ───────────────────────────────────────────────────
    last_entity:   str = ""    # last-mentioned entity (contact, app, song, …)
    last_intent:   str = ""

    # ── Screen awareness ──────────────────────────────────────────────────
    screen_text:   str = ""
    screen_source: str = ""  # "uia" | "ocr" | "none"

    # ── Timestamps ────────────────────────────────────────────────────────
    last_updated: float = field(default_factory=time.time)

    # ── Internal lock (excluded from snapshot) ────────────────────────────
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    # ════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ════════════════════════════════════════════════════════════════════════

    def update(self, **kwargs: Any) -> None:
        """
        [NEW: Phase 1 Architecture Fix]
        Atomically update one or more world-state fields.

        Unknown keys are logged and ignored (never crash on bad update).

        Example:
            world.update(active_app="chrome", current_url="https://youtube.com")
        """
        _valid = {f for f in self.__dataclass_fields__ if not f.startswith("_")}
        with self._lock:
            for key, value in kwargs.items():
                if key in _valid:
                    object.__setattr__(self, key, value)
            object.__setattr__(self, "last_updated", time.time())

    def snapshot(self) -> Dict[str, Any]:
        """
        [NEW: Phase 1 Architecture Fix]
        Return a plain dict copy of the current world state.
        Safe to pass to LLM prompts, decision engine, planner, etc.

        Returns:
            dict with all public fields (no _lock, no methods)
        """
        with self._lock:
            return {
                "active_app":          self.active_app,
                "last_app":            self.last_app,
                "current_url":         self.current_url,
                "current_page_title":  self.current_page_title,
                "current_page_text":   self.current_page_text,
                "last_song":           self.last_song,
                "last_artist":         self.last_artist,
                "is_playing":          self.is_playing,
                "last_entity":         self.last_entity,
                "last_intent":         self.last_intent,
                "screen_text":         self.screen_text,
                "screen_source":       self.screen_source,
                "last_updated":        self.last_updated,
            }

    def has_page_context(self) -> bool:
        """
        [NEW: Phase 1 Architecture Fix]
        Returns True if there is meaningful page/screen content available.
        Used by decision engine to resolve implicit references like "read this".
        """
        with self._lock:
            has_url  = bool(self.current_url.strip())
            has_page = bool(self.current_page_text.strip())
            has_screen = bool(self.screen_text.strip())
            return has_url or has_page or has_screen

    def resolve_implicit(self, word: str) -> Optional[str]:
        """
        [NEW: Phase 1 Architecture Fix]
        Resolve an implicit pronoun/reference to a concrete entity.

        Examples:
            "it"   → last_entity or last_app
            "this" → current_page_title or active_app
            "that" → last_entity
            "again"→ last_intent

        Returns the resolved string, or None if context is unavailable.
        """
        with self._lock:
            word = word.lower().strip()

            if word in ("it", "that"):
                return (
                    self.last_entity or
                    self.last_app or
                    self.last_song or
                    None
                )

            if word == "this":
                return (
                    self.current_page_title or
                    self.active_app or
                    self.last_entity or
                    None
                )

            if word == "again":
                return self.last_intent or None

        return None


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ════════════════════════════════════════════════════════════════════════════

# [NEW: Phase 1 Architecture Fix] — Global singleton, initialized at desktop
world = WorldModel()
