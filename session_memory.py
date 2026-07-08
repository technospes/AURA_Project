"""
SESSION MEMORY v3 — Smart Rolling Context, Token-Safe, Page-Aware
==================================================================
FIX LOG (v2 → v3):

  FIX 1 — inject_into_messages() was never wired into any LLM call.
    The session was recording turns faithfully but the history was never
    sent to Groq. Every call was stateless. Every "what did I just say?"
    or "what is the venue?" failed because the LLM had zero history.
    FIXED: inject_into_messages() is now the canonical way every LLM
    call in core.py / conversation.py builds its message list.
    Also exposed as a module-level helper: build_messages().

  FIX 2 — Page content after "read aloud" was lost.
    When Jarvis read a page, the text was spoken and thrown away.
    The next question ("what is the venue?") had no content to reason over.
    FIXED: set_page_context(text, url) stores the current page's full
    text in the session. inject_into_messages() injects it as a system
    note. Questions like "what is the venue of the hackathon?" now work
    because the LLM sees the page text in context.

  FIX 3 — No user profile (email, bookmarks).
    Jarvis had no awareness of the user's email address, saved bookmarks
    or other profile facts unless they were in the long-term MemoryStore
    — but nothing populated that store at startup and the retrieval
    keyword match was often too weak.
    FIXED: UserProfile dataclass with explicit typed fields. Persisted in
    JSON next to the memory file. Loaded at startup, injected into every
    system prompt as structured facts (never keyword-dependent).

  FIX 4 — Token explosion from v1 still partially present.
    max_turns kept at 5 (verbatim) + summary of older turns.
    PAGE CONTEXT capped at 3 000 chars to avoid blowing the budget.
    Profile facts capped at 10 entries.

Architecture:
  Tier 1 (this file): Short-Term Session Buffer
    Rolling window of last 5 turns + compressed summary + page context.
    Injected into every LLM call via inject_into_messages() / build_messages().

  Tier 2 (memory/store.py): Long-Term Persistent Memory
    Survives restarts. General facts, preferences. Keyword-recalled.

  Tier 3 (this file): UserProfile
    Survives restarts. Email, bookmarks, name, prefs. Always injected.
"""

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── CONSTANTS ──────────────────────────────────────────────────────────────
MAX_HISTORY_TOKENS   = 700      # ~2 800 chars of history per LLM call
SUMMARIZE_AFTER_SEC  = 1800.0   # 30 min — older turns get compressed
MAX_PAGE_CONTEXT_CHARS = 3000   # cap injected page text to ~750 tokens

_EPHEMERAL_INTENTS = frozenset({
    "open_app", "close_app", "play_media", "take_screenshot",
    "lock", "shutdown", "restart", "scroll", "new_tab", "close_tab",
})


# ── USER PROFILE ───────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """
    Persistent user profile. Fields are always injected into every LLM call.
    This is the right place for: email, name, bookmarks, phone, preferences.

    Add new fields here — they auto-save and auto-inject.
    """
    name:       str = ""
    email:      str = ""
    phone:      str = ""
    location:   str = ""
    # key → URL  (e.g. {"my portfolio": "https://mysite.com"})
    bookmarks:  Dict[str, str] = field(default_factory=dict)
    # free-form key→value preferences
    preferences: Dict[str, str] = field(default_factory=dict)

    def to_prompt_lines(self) -> List[str]:
        """Return lines to inject into the system prompt."""
        lines = []
        if self.name:
            lines.append(f"User name: {self.name}")
        if self.email:
            lines.append(f"User email: {self.email}")
        if self.phone:
            lines.append(f"User phone: {self.phone}")
        if self.location:
            lines.append(f"User location: {self.location}")
        for k, v in list(self.preferences.items())[:6]:
            lines.append(f"Preference — {k}: {v}")
        for label, url in list(self.bookmarks.items())[:6]:
            lines.append(f"Bookmark — {label}: {url}")
        return lines

    def update(self, key: str, value: str):
        """Set a profile field or preference by name."""
        key_l = key.lower().replace(" ", "_")
        if key_l == "name":
            self.name = value
        elif key_l in ("email", "email_address", "email_id"):
            self.email = value
        elif key_l in ("phone", "phone_number", "mobile"):
            self.phone = value
        elif key_l in ("location", "city", "address"):
            self.location = value
        elif key_l.startswith("bookmark_"):
            label = key_l[len("bookmark_"):]
            self.bookmarks[label] = value
        else:
            self.preferences[key] = value

    def add_bookmark(self, label: str, url: str):
        self.bookmarks[label.lower()] = url

    def get_bookmark(self, label: str) -> Optional[str]:
        return self.bookmarks.get(label.lower())


class UserProfileStore:
    """Loads/saves UserProfile from disk. Thread-safe."""

    def __init__(self, path: str = "data/user_profile.json"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.profile = self._load()

    def _load(self) -> UserProfile:
        if not self._path.exists():
            return UserProfile()
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return UserProfile(**data)
        except Exception as e:
            logger.warning(f"[Profile] Load failed: {e}")
            return UserProfile()

    def save(self):
        with self._lock:
            try:
                tmp = self._path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(asdict(self.profile), f, indent=2, ensure_ascii=False)
                tmp.replace(self._path)
            except Exception as e:
                logger.error(f"[Profile] Save failed: {e}")

    def update(self, key: str, value: str):
        with self._lock:
            self.profile.update(key, value)
        self.save()

    def add_bookmark(self, label: str, url: str):
        with self._lock:
            self.profile.add_bookmark(label, url)
        self.save()


# ── TURN ──────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    role:      str
    content:   str
    intent:    str = ""
    entities:  dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    success:   bool = True
    token_est: int = 0

    def __post_init__(self):
        self.token_est = max(1, len(self.content) // 4)

    def to_message(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}

    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def is_ephemeral(self) -> bool:
        return self.intent in _EPHEMERAL_INTENTS


# ── SYSTEM PROMPT TEMPLATE ────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are Jarvis, a professional AI assistant running on {user_name}'s PC.
Tone: concise, efficient, butler-like. Never verbose. Never say "I'm not sure what you're referring to" when conversation history or page context is available.
Time: {time}  |  Active app: {active_app}
{profile_lines}
{memory_facts}
{page_context_block}
Conversation history (most recent {n_turns} turns shown{summary_note}):\
"""


# ── SESSION MEMORY ────────────────────────────────────────────────────────

class SessionMemory:
    """
    Rolling short-term session buffer with:
    - Full conversation history injected into every LLM call
    - Current page context (for "read aloud" follow-up questions)
    - User profile always present in system prompt
    Thread-safe.
    """

    def __init__(
        self,
        max_turns:           int   = 5,
        max_age_seconds:     float = 3600.0,
        summarize_after_sec: float = SUMMARIZE_AFTER_SEC,
        profile_path:        str   = "data/user_profile.json",
    ):
        self._turns: Deque[Turn] = deque(maxlen=max_turns * 2)
        self._summary: str = ""
        self._max_age         = max_age_seconds
        self._summarize_after = summarize_after_sec
        self._lock            = threading.Lock()

        # Page context — set when user says "read this page"
        self._page_text: str  = ""   # cleaned full text
        self._page_url:  str  = ""
        self._page_title: str = ""
        self._page_ts:   float = 0.0  # when it was set

        # User profile
        self._profile_store = UserProfileStore(profile_path)

    # ── PROFILE SHORTCUTS ──────────────────────────────────────────────────

    @property
    def profile(self) -> UserProfile:
        return self._profile_store.profile

    def update_profile(self, key: str, value: str):
        """Update a profile field and persist."""
        self._profile_store.update(key, value)
        logger.info(f"[Profile] {key} = {value!r}")

    def add_bookmark(self, label: str, url: str):
        self._profile_store.add_bookmark(label, url)
        logger.info(f"[Profile] Bookmark added: {label!r} → {url}")

    # ── PAGE CONTEXT ───────────────────────────────────────────────────────

    def set_page_context(self, text: str, url: str = "", title: str = ""):
        """
        Store the current page's content after a 'read aloud' or 'page summary'.
        This is injected into every subsequent LLM call so that follow-up
        questions like 'what is the venue?' work correctly.
        Automatically expires after 30 minutes of inactivity.
        """
        with self._lock:
            self._page_text  = text[:MAX_PAGE_CONTEXT_CHARS]
            self._page_url   = url
            self._page_title = title
            self._page_ts    = time.time()
        logger.info(f"[Session] Page context set: {len(text)} chars | {url[:60]}")

    def clear_page_context(self):
        with self._lock:
            self._page_text = ""
            self._page_url  = ""
            self._page_title = ""
            self._page_ts   = 0.0

    def has_page_context(self) -> bool:
        with self._lock:
            if not self._page_text:
                return False
            # Auto-expire after 30 min
            if time.time() - self._page_ts > 1800:
                self._page_text = ""
                return False
        return True

    # ── TURN MANAGEMENT ───────────────────────────────────────────────────

    def add_user_turn(self, text: str, intent: str = "", entities: dict = None):
        with self._lock:
            self._turns.append(Turn(
                role="user", content=text,
                intent=intent, entities=entities or {},
            ))
            self._maybe_compress()

    def add_assistant_turn(self, text: str, success: bool = True):
        if not text:
            return
        with self._lock:
            stored = text[:400] + "…" if len(text) > 400 else text
            self._turns.append(Turn(role="assistant", content=stored, success=success))
            self._maybe_compress()

    # ── MESSAGE BUILDER — THE CORE FIX ────────────────────────────────────

    def inject_into_messages(
        self,
        new_messages: List[Dict],
        user_name:    str = "Sir",
        active_app:   str = "desktop",
        memory_facts: List[str] = None,
    ) -> List[Dict]:
        """
        Build a COMPLETE message list for one LLM call.

        Structure:
          [system prompt with profile + memory + page context]
          [history turns (budget-capped)]
          [new_messages]

        This is the ONLY function that should be used to build messages
        for Groq/OpenAI calls in this project.
        """
        import datetime
        now_str = datetime.datetime.now().strftime("%a %d %b %Y, %I:%M %p")

        # ── Profile lines ──────────────────────────────────────────────
        profile_lines_text = ""
        plines = self.profile.to_prompt_lines()
        if plines:
            profile_lines_text = "User profile:\n" + "\n".join(f"  {l}" for l in plines[:10])

        # ── Long-term memory facts ─────────────────────────────────────
        facts_text = ""
        if memory_facts:
            facts_text = "Long-term memory: " + " | ".join(memory_facts[:5])

        # ── Page context block ─────────────────────────────────────────
        page_block = ""
        with self._lock:
            if self._page_text and (time.time() - self._page_ts < 1800):
                title_line = f" ({self._page_title})" if self._page_title else ""
                url_line   = f" — {self._page_url}" if self._page_url else ""
                page_block = (
                    f"CURRENT PAGE CONTENT{title_line}{url_line}:\n"
                    f"{self._page_text[:MAX_PAGE_CONTEXT_CHARS]}\n"
                    f"[END PAGE CONTENT — answer follow-up questions using the above text]"
                )

        # ── History turns (budget-enforced) ───────────────────────────
        with self._lock:
            fresh = self._fresh_turns()

        budget = MAX_HISTORY_TOKENS
        kept: List[Turn] = []
        for t in reversed(fresh):
            if budget >= t.token_est:
                kept.insert(0, t)
                budget -= t.token_est
            else:
                break

        summary_note = ""
        summary_text = ""
        if self._summary:
            summary_note = "; earlier context summarized"
            summary_text = f"\n[Earlier context]: {self._summary}"

        # ── Assemble system prompt ─────────────────────────────────────
        system_content = _SYSTEM_TEMPLATE.format(
            user_name=user_name or "Sir",
            time=now_str,
            active_app=active_app,
            profile_lines=profile_lines_text,
            memory_facts=facts_text,
            page_context_block=page_block,
            n_turns=len(kept),
            summary_note=summary_note,
        )
        system_content += summary_text

        messages = [{"role": "system", "content": system_content}]
        messages += [t.to_message() for t in kept]
        messages += new_messages
        return messages

    # ── CONTEXT SNAPSHOT (for intent resolver) ────────────────────────

    def get_context_snapshot(self) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {}
        with self._lock:
            turns = list(self._turns)

        for turn in reversed(turns):
            if turn.role != "user" or not turn.entities:
                continue
            if turn.intent == "open_app" and "last_app" not in ctx:
                ctx["last_app"] = turn.entities.get("app", "")
            if turn.intent == "play_media":
                if "last_song" not in ctx:
                    ctx["last_song"] = turn.entities.get("song", "")
                if "last_platform" not in ctx:
                    ctx["last_platform"] = turn.entities.get("platform", "")
            if turn.intent == "open_website" and "last_url" not in ctx:
                ctx["last_url"] = turn.entities.get("url", "")
            if turn.intent in ("send_message", "make_call") and "last_contact" not in ctx:
                ctx["last_contact"] = turn.entities.get("contact", "")

        for turn in reversed(turns):
            if turn.role == "assistant":
                ctx["last_response"] = turn.content
                break

        # Include page context flags so intent resolver can know
        ctx["has_page_context"] = self.has_page_context()
        ctx["page_url"] = self._page_url
        ctx["page_title"] = self._page_title

        return ctx

    def resolve_pronoun(self, text: str) -> Optional[str]:
        pronouns = {"it", "that", "this", "same", "the app", "the song"}
        if not (set(text.lower().split()) & pronouns):
            return None
        with self._lock:
            turns = list(self._turns)
        for turn in reversed(turns):
            if turn.role == "user" and turn.entities:
                for key in ("app", "song", "contact", "query"):
                    val = turn.entities.get(key, "")
                    if val:
                        logger.debug(f"[SESSION] Pronoun '{text}' → '{val}'")
                        return val
        return None

    def clear(self):
        with self._lock:
            self._turns.clear()
            self._summary = ""

    def __len__(self) -> int:
        return len(self._turns)

    # ── INTERNAL ─────────────────────────────────────────────────────

    def _fresh_turns(self) -> List[Turn]:
        now = time.time()
        return [t for t in self._turns if (now - t.timestamp) < self._max_age]

    def _maybe_compress(self):
        """Compress old/ephemeral turns into summary. Called with lock held."""
        now = time.time()
        to_keep, to_squash = [], []

        for turn in self._turns:
            age = now - turn.timestamp
            if age > self._summarize_after or (age > 300 and turn.is_ephemeral):
                to_squash.append(turn)
            else:
                to_keep.append(turn)

        if not to_squash:
            return

        phrases = []
        for t in to_squash:
            if t.role == "user" and t.content:
                phrases.append(f"User: {t.content[:60]}")
            elif t.role == "assistant" and t.content:
                phrases.append(f"Jarvis: {t.content[:60]}")

        if phrases:
            self._summary = ("; ".join(phrases[-6:]))[:500]
            logger.debug(f"[SESSION] Compressed {len(to_squash)} old turns")

        self._turns = deque(to_keep, maxlen=self._turns.maxlen)


# ── MODULE-LEVEL HELPER ───────────────────────────────────────────────────

def build_messages(
    new_messages: List[Dict],
    user_name:    str = "Sir",
    active_app:   str = "desktop",
    memory_facts: List[str] = None,
) -> List[Dict]:
    """
    Convenience wrapper: call session.inject_into_messages() via module-level.
    Usage in any file:
        from session_memory import build_messages
        messages = build_messages([{"role": "user", "content": user_text}])
    """
    return session.inject_into_messages(
        new_messages, user_name=user_name,
        active_app=active_app, memory_facts=memory_facts,
    )


# ── GLOBAL SINGLETON ──────────────────────────────────────────────────────
session = SessionMemory(max_turns=5, max_age_seconds=3600.0)