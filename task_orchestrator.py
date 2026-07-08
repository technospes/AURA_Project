"""
AGENTIC TASK ORCHESTRATOR v5 — WhatsApp Contact Disambiguation
===============================================================
Changes from v4:

  1. WHATSAPP CONTACT DISAMBIGUATION
     - After searching a contact name, reads the result list via pywinauto
     - If multiple matches found (same first name), asks: "Sir, which Ayush?"
       and reads out names from the visible list
     - User can say "the first one", "the second one", "the third one",
       "Ayush Sharma", etc.
     - Selects the correct contact programmatically
     - No more blind Enter-press on unknown results

  2. WHATSAPP CALL / MESSAGE FLOW (fully deterministic)
     Open WhatsApp → focus search → type name → wait for results
     → read result list → if ambiguous, ask user → pick correct entry
     → open chat → call / type message / send

  3. FOLLOW-UP TIMING FIX
     - After Jarvis asks a clarification question, the TTS finishes speaking
       BEFORE the microphone opens (was racing before)
     - Added speak_done event so trigger_followup only fires after TTS completes

  4. PLATFORM UI IMPROVEMENTS
     - WhatsApp desktop app: pywinauto tree walk to find result items
     - Fallback: pyautogui with arrow keys if pywinauto tree walk fails
     - Discord: unchanged (Ctrl+K is reliable)
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── TASK STATES ───────────────────────────────────────────────────────────

class TaskState(Enum):
    IDLE                     = auto()
    COLLECTING_PLATFORM      = auto()
    COLLECTING_TO            = auto()
    COLLECTING_PURPOSE       = auto()
    COLLECTING_EXTRA         = auto()
    RESEARCHING              = auto()
    COLLECTING_CONTACT       = auto()
    COLLECTING_BODY          = auto()
    CONFIRMING_SEND          = auto()
    DISAMBIGUATING_CONTACT   = auto()   # NEW: user is picking from multiple matches
    EXECUTING                = auto()
    DONE                     = auto()
    FAILED                   = auto()


@dataclass
class SlotValue:
    key:          str
    value:        str
    collected_at: float = field(default_factory=time.time)


@dataclass
class AgenticTask:
    task_type:     str
    state:         TaskState        = TaskState.IDLE
    slots:         Dict[str, SlotValue] = field(default_factory=dict)
    context:       Dict[str, Any]   = field(default_factory=dict)
    started_at:    float            = field(default_factory=time.time)
    last_activity: float            = field(default_factory=time.time)
    error_count:   int              = 0
    # Contact disambiguation state
    contact_candidates: List[str]   = field(default_factory=list)

    def set_slot(self, key: str, value: str):
        self.slots[key]    = SlotValue(key=key, value=value)
        self.last_activity = time.time()

    def get(self, key: str, default: str = "") -> str:
        s = self.slots.get(key)
        return s.value if s else default

    def has(self, key: str) -> bool:
        s = self.slots.get(key)
        return bool(s and s.value.strip())

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity


# ── FSM DEFINITIONS ───────────────────────────────────────────────────────

EMAIL_FSM: Dict[TaskState, tuple] = {
    TaskState.IDLE:              ("Who should I send this email to, Sir?",                        "to_address",    TaskState.COLLECTING_TO),
    TaskState.COLLECTING_TO:     ("What's the purpose of this email?",                            "purpose",       TaskState.COLLECTING_PURPOSE),
    TaskState.COLLECTING_PURPOSE:("Any specific points you'd like me to include?",                "extra_body",    TaskState.RESEARCHING),
    TaskState.RESEARCHING:       (None,                                                            None,            TaskState.COLLECTING_CONTACT),
    TaskState.COLLECTING_CONTACT:("Should I include your contact details? Say your number or email.", "contact_info", TaskState.CONFIRMING_SEND),
    TaskState.CONFIRMING_SEND:   ("The email is ready, Sir. Shall I open it in your mail client?", "send_confirm", TaskState.EXECUTING),
}

CALL_FSM: Dict[TaskState, tuple] = {
    TaskState.IDLE:               ("Should I call on WhatsApp or Discord, Sir?", "platform", TaskState.COLLECTING_PLATFORM),
    TaskState.COLLECTING_PLATFORM:("Who should I call, Sir?",                    "contact",  TaskState.COLLECTING_TO),
    TaskState.COLLECTING_TO:      (None,                                           None,       TaskState.EXECUTING),
}

CALL_FSM_NO_PLATFORM_Q: Dict[TaskState, tuple] = {
    TaskState.IDLE:          ("Who should I call on {platform}, Sir?", "contact", TaskState.COLLECTING_TO),
    TaskState.COLLECTING_TO: (None,                                     None,      TaskState.EXECUTING),
}

MESSAGE_FSM: Dict[TaskState, tuple] = {
    TaskState.IDLE:               ("Should I send on WhatsApp or Discord, Sir?", "platform", TaskState.COLLECTING_PLATFORM),
    TaskState.COLLECTING_PLATFORM:("Who should I message, Sir?",                  "contact",  TaskState.COLLECTING_TO),
    TaskState.COLLECTING_TO:      ("What should the message say?",                "body",     TaskState.COLLECTING_BODY),
    TaskState.COLLECTING_BODY:    ("Message ready. Shall I send it?",             "send_confirm", TaskState.CONFIRMING_SEND),
    TaskState.CONFIRMING_SEND:    (None,                                           None,       TaskState.EXECUTING),
}

MESSAGE_FSM_NO_PLATFORM_Q: Dict[TaskState, tuple] = {
    TaskState.IDLE:           ("Who should I message on {platform}, Sir?",    "contact",      TaskState.COLLECTING_TO),
    TaskState.COLLECTING_TO:  ("What should the message say?",                "body",         TaskState.COLLECTING_BODY),
    TaskState.COLLECTING_BODY:("Message ready for {contact}. Shall I send it?", "send_confirm", TaskState.CONFIRMING_SEND),
    TaskState.CONFIRMING_SEND:(None,                                            None,           TaskState.EXECUTING),
}

TASK_FSM_MAP = {
    "compose_email":   EMAIL_FSM,
    "compose_message": MESSAGE_FSM,
    "make_call":       CALL_FSM,
}

PLATFORM_APP_MAP = {
    "whatsapp": ("whatsapp", "https://web.whatsapp.com"),
    "discord":  ("discord",  "https://discord.com/app"),
    "telegram": ("telegram", "https://web.telegram.org"),
    "gmail":    ("gmail",    "https://mail.google.com"),
}

_INDEPENDENT_PATTERNS = re.compile(
    r"^\s*(?:open\s+\w|close\s+\w|play\s+\w|launch\s+\w|"
    r"search\s+(?:for\s+)?\w|set\s+(?:volume|brightness|resolution)|"
    r"change\s+(?:my\s+)?(?:desktop|screen|display|resolution|volume)|"
    r"take\s+(?:a\s+)?screenshot|(?:shut\s?down|restart|lock)\s*(?:the\s+)?(?:computer|pc|system)?)",
    re.IGNORECASE,
)
_ESCAPE_WORDS = frozenset({"stop", "cancel", "exit", "abort", "forget it", "never mind"})
_CONTINUATION_WORDS = frozenset({
    "yes", "no", "yeah", "nah", "nope", "sure", "okay", "ok",
    "go ahead", "send it", "do it", "proceed", "confirm", "yep",
    "whatsapp", "discord", "telegram",
})

# Ordinal words for contact picking
_ORDINAL_MAP = {
    "first": 0, "1st": 0, "one": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "fourth": 3, "4th": 3, "four": 3, "4": 3,
    "fifth": 4, "5th": 4, "five": 4, "5": 4,
}


def _parse_contact_selection(text: str, candidates: List[str]) -> Optional[int]:
    """
    Parse the user's reply when selecting from a list of contact matches.
    Returns the 0-based index or None if unclear.
    """
    t = text.lower().strip().rstrip(".")
    # Ordinal words
    for word, idx in _ORDINAL_MAP.items():
        if word in t:
            if idx < len(candidates):
                return idx
    # Name match
    for i, name in enumerate(candidates):
        if name.lower() in t or t in name.lower():
            return i
    return None


def is_continuation(text: str) -> bool:
    stripped = text.strip().rstrip(".")
    lower    = stripped.lower()
    if lower in _CONTINUATION_WORDS:
        return True
    if len(stripped.split()) <= 3:
        return True
    if _INDEPENDENT_PATTERNS.match(stripped):
        return False
    return True


# ════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ════════════════════════════════════════════════════════════════════════════

class AgenticOrchestrator:
    IDLE_TIMEOUT    = 45.0
    MAX_ERROR_COUNT = 3

    def __init__(self, groq_api_key: str):
        self._api_key    = groq_api_key
        self._task: Optional[AgenticTask] = None
        self._automation = BrowserAutomation()

    def has_active_task(self) -> bool:
        if not self._task:
            return False
        if self._task.state in (TaskState.DONE, TaskState.FAILED):
            self._task = None
            return False
        if self._task.idle_seconds > self.IDLE_TIMEOUT:
            logger.info("[ORCH] Reset due to timeout")
            self._task = None
            return False
        return True

    def reset(self):
        self._task = None

    def cancel(self):
        self._task = None

    def check_and_route(self, text: str, resolved_intent: Optional[str] = None) -> str:
        if not self.has_active_task():
            return "idle"

        lower = text.strip().lower().rstrip(".")
        if lower in _ESCAPE_WORDS:
            logger.info("[ORCH] Cancelled via escape word")
            self.reset()
            return "cancel"

        # Disambiguating state — user is picking a contact, ALWAYS continue
        if self._task and self._task.state == TaskState.DISAMBIGUATING_CONTACT:
            logger.info(f"[ORCH] Contact disambiguation in progress — continuing")
            return "continue"

        if _INDEPENDENT_PATTERNS.match(text.strip()):
            logger.info(f"[ORCH] Cancelled — independent command: '{text[:50]}'")
            self.reset()
            return "cancel"

        if resolved_intent and resolved_intent in {
            "open_app", "close_app", "play_media", "pause_media",
            "system_action", "search_web", "open_website", "take_screenshot",
            "lock", "shutdown", "restart",
        }:
            logger.info(f"[ORCH] Cancelled — system intent: {resolved_intent}")
            self.reset()
            return "cancel"

        if is_continuation(text):
            logger.info(f"[ORCH] Continuing task: '{text[:50]}'")
            return "continue"

        logger.info(f"[ORCH] Cancelled — ambiguous non-continuation: '{text[:50]}'")
        self.reset()
        return "cancel"

    async def start_task(
        self,
        task_type:     str,
        context:       Dict,
        speak:         Callable[[str], None],
        initial_slots: Dict[str, str] = None,
    ) -> str:
        if task_type not in TASK_FSM_MAP:
            msg = f"I don't have a workflow for '{task_type}' yet, Sir."
            speak(msg)
            return msg

        self._task = AgenticTask(task_type=task_type, context=context)
        if initial_slots:
            for k, v in initial_slots.items():
                self._task.set_slot(k, v)

        platform = self._task.get("platform")
        fsm      = self._get_fsm(task_type, platform_known=bool(platform))

        app_msg = await self._open_task_app(task_type, platform)
        if app_msg:
            speak(app_msg)
            await asyncio.sleep(2.5)

        entry    = fsm.get(TaskState.IDLE)
        question = self._format_question(entry[0] if entry else "How can I help?", self._task)
        self._task.state = entry[2] if entry else TaskState.COLLECTING_TO

        speak(question)
        return question

    async def handle_response(self, user_text: str, speak: Callable[[str], None]) -> str:
        """Process one user turn. Always returns a non-empty spoken string."""
        if not self._task:
            return ""

        if any(e in user_text.lower() for e in ["cancel", "stop", "abort", "forget it"]):
            self.cancel()
            msg = "Task cancelled, Sir."
            speak(msg)
            return msg

        self._task.last_activity = time.time()

        # ── SPECIAL STATE: contact disambiguation ──────────────────────────
        if self._task.state == TaskState.DISAMBIGUATING_CONTACT:
            return await self._handle_contact_disambiguation(user_text, speak)

        try:
            platform_known = bool(self._task.get("platform"))
            fsm   = self._get_fsm(self._task.task_type, platform_known)
            entry = fsm.get(self._task.state)

            if entry and entry[1]:
                cleaned = self._clean_slot_value(entry[1], user_text)
                if cleaned:
                    self._task.set_slot(entry[1], cleaned)
                    logger.info(f"[ORCH] Slot '{entry[1]}' = '{cleaned[:60]}'")
                    if entry[1] == "platform":
                        await self._open_task_app(self._task.task_type, cleaned)
                else:
                    self._task.error_count += 1
                    if self._task.error_count >= self.MAX_ERROR_COUNT:
                        self._task.state = TaskState.FAILED
                        msg = "Too many unclear responses, Sir. Task cancelled."
                        speak(msg)
                        return msg
                    msg = self._format_question(
                        entry[0] or "Could you repeat that, Sir?", self._task
                    )
                    speak(msg)
                    return msg

            # Advance FSM
            self._task.state = self._next_state(fsm)

            if self._task.state == TaskState.RESEARCHING:
                speak("One moment — drafting the email now.")
                research = await self._do_research()
                self._task.set_slot("research", research)
                self._task.state = TaskState.COLLECTING_CONTACT
                ce = fsm.get(TaskState.COLLECTING_CONTACT)
                msg = ce[0] if ce else "Include contact details?"
                speak(msg)
                return msg

            if self._task.state == TaskState.EXECUTING:
                result = await self._execute_task(speak)
                speak(result)
                return result

            if self._task.state in (TaskState.DONE, TaskState.FAILED):
                msg = "All done, Sir." if self._task.state == TaskState.DONE else "Task failed, Sir."
                speak(msg)
                return msg

            next_entry = fsm.get(self._task.state)
            if next_entry and next_entry[0]:
                msg = self._format_question(next_entry[0], self._task)
                speak(msg)
                return msg

            self._task.state = TaskState.EXECUTING
            result = await self._execute_task(speak)
            speak(result)
            return result

        except Exception as e:
            logger.error(f"[ORCH] handle_response error: {e}", exc_info=True)
            self._task.error_count += 1
            if self._task.error_count >= self.MAX_ERROR_COUNT:
                self.cancel()
                msg = "Too many errors, Sir. Cancelling task."
                speak(msg)
                return msg
            msg = "Something went wrong, Sir. Could you try again?"
            speak(msg)
            return msg

    async def _handle_contact_disambiguation(self, user_text: str, speak: Callable) -> str:
        """User is selecting from multiple WhatsApp contact matches."""
        candidates = self._task.contact_candidates
        idx = _parse_contact_selection(user_text, candidates)

        if idx is None:
            self._task.error_count += 1
            if self._task.error_count >= self.MAX_ERROR_COUNT:
                self.cancel()
                msg = "Too many unclear responses, Sir. Task cancelled."
                speak(msg)
                return msg
            # Re-read list
            numbered = ", ".join(f"{i+1}: {n}" for i, n in enumerate(candidates))
            msg = f"I didn't catch that, Sir. Please say the number: {numbered}."
            speak(msg)
            return msg

        selected = candidates[idx]
        logger.info(f"[ORCH] Contact selected: {selected} (index {idx})")
        self._task.set_slot("resolved_contact_index", str(idx))
        self._task.set_slot("contact", selected)

        # Now execute
        self._task.state = TaskState.EXECUTING
        result = await self._execute_task(speak)
        speak(result)
        return result

    # ── FSM HELPERS ───────────────────────────────────────────────────────

    def _get_fsm(self, task_type: str, platform_known: bool) -> Dict:
        if task_type == "make_call":
            return CALL_FSM_NO_PLATFORM_Q if platform_known else CALL_FSM
        if task_type == "compose_message":
            return MESSAGE_FSM_NO_PLATFORM_Q if platform_known else MESSAGE_FSM
        return TASK_FSM_MAP.get(task_type, EMAIL_FSM)

    def _next_state(self, fsm: Dict) -> TaskState:
        if not self._task:
            return TaskState.IDLE
        entry = fsm.get(self._task.state)
        return entry[2] if entry else TaskState.DONE

    def _format_question(self, question: str, task: AgenticTask) -> str:
        try:
            return question.format(
                contact=task.get("contact", "the contact"),
                platform=task.get("platform", "the platform"),
                body=task.get("body", ""),
            )
        except (KeyError, ValueError):
            return question

    def _clean_slot_value(self, slot_key: str, raw: str) -> str:
        raw = raw.strip()
        if not raw or raw.lower() in ("um", "uh", "hmm", "nothing", "skip"):
            return ""
        if slot_key == "to_address":
            return _normalize_email(raw)
        elif slot_key == "platform":
            r = raw.lower()
            for plat in ("whatsapp", "discord", "telegram", "gmail"):
                if plat in r:
                    return plat
            if any(w in r for w in ("second", "discord", "two")):
                return "discord"
            if any(w in r for w in ("first", "whatsapp", "one")):
                return "whatsapp"
            return raw.split()[0].lower() if raw.split() else ""
        elif slot_key == "contact":
            raw = re.sub(
                r'\b(?:from|at|my|the|boss|friend|colleague|brother|sister|'
                r'mother|father|sir|mr|ms|mrs)\b',
                '', raw, flags=re.IGNORECASE
            ).strip()
            words = [w for w in raw.split() if w and len(w) > 1]
            return " ".join(words[:2]).strip()
        elif slot_key == "send_confirm":
            positive = {"yes", "send", "go", "do it", "sure", "okay", "yep",
                        "go ahead", "proceed", "confirm", "affirmative", "yeah"}
            return "yes" if any(w in raw.lower() for w in positive) else "no"
        elif slot_key == "purpose":
            raw = re.sub(
                r'^(i want to|i need to|it is about|it\'s about|for|about)\s+',
                '', raw, flags=re.IGNORECASE
            ).strip()
            return raw
        return raw

    # ── APP OPENING ───────────────────────────────────────────────────────

    async def _open_task_app(self, task_type: str, platform: str = None) -> Optional[str]:
        plat = (platform or "").lower()
        if plat in PLATFORM_APP_MAP:
            app_name, fallback_url = PLATFORM_APP_MAP[plat]
        elif task_type == "compose_email":
            app_name, fallback_url = "gmail", "https://mail.google.com"
        else:
            return None

        try:
            from utils.app_locator import app_locator
            if app_locator.find_app(app_name):
                app_locator.launch(app_name)
                return f"Opening {app_name}, Sir."
        except Exception:
            pass

        import webbrowser
        webbrowser.open(fallback_url)
        return f"Opening {app_name} in the browser, Sir."

    # ── RESEARCH ──────────────────────────────────────────────────────────

    async def _do_research(self) -> str:
        purpose = self._task.get("purpose")
        to_addr = self._task.get("to_address")
        extra   = self._task.get("extra_body")
        if not purpose:
            return ""
        try:
            from groq import Groq
            client = Groq(api_key=self._api_key)
            prompt = (
                f"Write a professional email body (3-4 sentences, no greeting or sign-off).\n"
                f"Purpose: {purpose}\nRecipient: {to_addr}\n"
                f"Extra points: {extra or 'none'}\nReturn ONLY the body text."
            )
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=250,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[ORCH] Research LLM failed: {e}")
            return f"I am writing regarding {purpose}."

    # ── TASK EXECUTION ────────────────────────────────────────────────────

    async def _execute_task(self, speak: Callable[[str], None]) -> str:
        task       = self._task
        task.state = TaskState.EXECUTING
        try:
            if task.task_type == "compose_email":
                result = await self._automation.compose_email(
                    to=task.get("to_address"),
                    purpose=task.get("purpose"),
                    body=task.get("research") or task.get("extra_body"),
                    contact_info=task.get("contact_info"),
                    send=task.get("send_confirm") == "yes",
                )
            elif task.task_type == "compose_message":
                result = await self._automation.compose_message(
                    contact=task.get("contact"),
                    body=task.get("body"),
                    platform=task.get("platform", "whatsapp"),
                    send=task.get("send_confirm") == "yes",
                    task=task,
                    on_disambiguate=self._trigger_disambiguation,
                )
                # If disambiguation was triggered, result is a question — don't mark DONE yet
                if result.get("needs_disambiguation"):
                    self._task.state = TaskState.DISAMBIGUATING_CONTACT
                    self._task.contact_candidates = result.get("candidates", [])
                    return result.get("message", "Which contact, Sir?")
            elif task.task_type == "make_call":
                result = await self._automation.make_call(
                    contact=task.get("contact"),
                    platform=task.get("platform", "whatsapp"),
                    task=task,
                    on_disambiguate=self._trigger_disambiguation,
                )
                if result.get("needs_disambiguation"):
                    self._task.state = TaskState.DISAMBIGUATING_CONTACT
                    self._task.contact_candidates = result.get("candidates", [])
                    return result.get("message", "Which contact, Sir?")
            else:
                result = {"success": False, "message": f"No executor for {task.task_type}"}

            task.state = TaskState.DONE
            msg = result.get("message", "Done, Sir.")
            # If the driver reported a hard failure (e.g. pyautogui disabled), mark FAILED
            if not result.get("success") and result.get("needs_speech_failure"):
                task.state = TaskState.FAILED
            return msg
        except Exception as e:
            logger.error(f"[ORCH] Execute failed: {e}", exc_info=True)
            task.state = TaskState.FAILED
            return f"I couldn't complete that, Sir. {str(e)[:60]}"

    def _trigger_disambiguation(self, candidates: List[str], question: str):
        """Callback from BrowserAutomation when multiple contacts found."""
        self._task.state              = TaskState.DISAMBIGUATING_CONTACT
        self._task.contact_candidates = candidates
        logger.info(f"[ORCH] Disambiguation triggered: {candidates}")


# ════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ════════════════════════════════════════════════════════════════════════════

class BrowserAutomation:
    """
    API-first messaging with GUI fallback chain.

    Discord:  REST API (Bot Token) → pywinauto → pyautogui
    Telegram: REST API (Bot Token) → skip GUI (no reliable desktop app)
    WhatsApp: Playwright headless (Web session) → pywinauto → STOP (no blind click)

    Tokens injected from config at startup:
        orchestrator._automation._discord_token  = config["discord_bot_token"]
        orchestrator._automation._telegram_token = config["telegram_bot_token"]
    """

    def __init__(self):
        # API tokens — injected from config at startup
        self._discord_token:  str = ""
        self._telegram_token: str = ""
        # Cached DM channel/chat IDs (populated lazily on first API call)
        self._discord_channel_cache: dict = {}   # contact_lower → channel_id
        self._telegram_chat_cache:   dict = {}   # contact_lower → chat_id



    async def compose_email(self, to, purpose, body, contact_info="", send=False) -> Dict:
        import webbrowser, urllib.parse
        subject   = _generate_subject(purpose)
        full_body = _build_email_body(body, purpose, contact_info)
        url       = (
            f"mailto:{urllib.parse.quote(to or '')}"
            f"?subject={urllib.parse.quote(subject)}"
            f"&body={urllib.parse.quote(full_body)}"
        )
        webbrowser.open(url)
        return {
            "success": True,
            "message": f"Email drafted to {to}, Sir. Subject: '{subject}'. Ready in your mail client."
        }

    async def compose_message(self, contact, body, platform="whatsapp", send=False,
                               task=None, on_disambiguate=None) -> Dict:
        if platform == "discord":
            return await self._discord_message(contact, body, send)
        if platform == "telegram":
            return await self._telegram_message(contact, body, send)
        return await self._whatsapp_message(contact, body, send, task, on_disambiguate)

    async def make_call(self, contact, platform="whatsapp",
                        task=None, on_disambiguate=None) -> Dict:
        if not contact:
            return {"success": False, "message": "I need a contact name, Sir."}
        if platform == "discord":
            return await self._discord_call(contact)
        return await self._whatsapp_call(contact, task, on_disambiguate)

    # ── WhatsApp ──────────────────────────────────────────────────────────

    async def _whatsapp_call(self, contact: str, task=None, on_disambiguate=None) -> Dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._whatsapp_action_sync, contact, None, False, "call", task, on_disambiguate
        )

    async def _whatsapp_message(self, contact: str, body: str, send: bool,
                                 task=None, on_disambiguate=None) -> Dict:
        # 1. Try Playwright (headless Chrome + WhatsApp Web session)
        pw_result = await self._whatsapp_playwright_message(
            contact, body, send, task, on_disambiguate
        )
        if pw_result.get("success") or pw_result.get("needs_disambiguation"):
            return pw_result
        # 2. Fall back to pywinauto (desktop app)
        logger.info(f"[WhatsApp] Playwright miss → pywinauto: {pw_result.get('error')}")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._whatsapp_action_sync, contact, body, send, "message", task, on_disambiguate
        )

    def _whatsapp_action_sync(self, contact: str, body: Optional[str], send: bool,
                               action: str, task=None, on_disambiguate=None) -> Dict:
        """
        Unified WhatsApp automation:
          1. Ensure WhatsApp is open
          2. Search contact
          3. Read result list
          4. If >1 result with same first name → return needs_disambiguation
          5. Otherwise select first result
          6. Perform action (call / type message)
        """
        import time as _t
        import subprocess

        # ── Step 1: Launch WhatsApp ────────────────────────────────────
        subprocess.Popen(["cmd", "/c", "start", "whatsapp://"], shell=True)
        _t.sleep(3.5)

        # ── Try pywinauto path ─────────────────────────────────────────
        try:
            return self._whatsapp_pywinauto(contact, body, send, action, on_disambiguate)
        except ImportError:
            logger.info("[WhatsApp] pywinauto not available — using pyautogui fallback")
        except Exception as e:
            logger.warning(f"[WhatsApp] pywinauto failed ({e}) — using pyautogui fallback")

        return self._whatsapp_pyautogui(contact, body, send, action)

    def _whatsapp_pywinauto(self, contact: str, body: Optional[str],
                             send: bool, action: str, on_disambiguate=None) -> Dict:
        """pywinauto path — reads contact list for disambiguation."""
        import time as _t
        from pywinauto import Application

        # Connect to WhatsApp window
        app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=8)
        win = app.top_window()

        # Find search box
        search = None
        for auto_id in ("search-input", "SearchInput"):
            try:
                search = win.child_window(auto_id=auto_id, control_type="Edit")
                if search.exists(timeout=2):
                    break
                search = None
            except Exception:
                pass
        if search is None:
            try:
                search = win.child_window(title_re=".*[Ss]earch.*", control_type="Edit")
                search.exists(timeout=2)
            except Exception:
                raise RuntimeError("Cannot find WhatsApp search box")

        # Type contact name
        search.click_input()
        _t.sleep(0.3)
        search.type_keys(contact, with_spaces=True)
        _t.sleep(1.8)  # Wait for results to load

        # ── Read contact results ───────────────────────────────────────
        candidates = self._read_whatsapp_contact_list(win, contact)
        logger.info(f"[WhatsApp] Contact search results for '{contact}': {candidates}")

        # Disambiguation: multiple results with same first name
        if len(candidates) > 1:
            # Check if first names are the same (common case: "Ayush" matching multiple)
            first_names = [c.split()[0].lower() for c in candidates if c]
            if len(set(first_names)) < len(candidates):
                numbered = ", ".join(f"{i+1}: {n}" for i, n in enumerate(candidates[:5]))
                question = f"Sir, I found multiple contacts named {contact}: {numbered}. Which one?"
                logger.info(f"[WhatsApp] Disambiguating: {question}")
                return {
                    "success":             False,
                    "needs_disambiguation": True,
                    "candidates":          candidates[:5],
                    "message":             question,
                }

        # ── Select contact (first result or by index from task) ────────
        select_index = 0
        if hasattr(on_disambiguate, '__self__') and on_disambiguate.__self__:
            task_obj = on_disambiguate.__self__
            stored   = task_obj.get("resolved_contact_index", "")
            if stored.isdigit():
                select_index = int(stored)

        self._select_whatsapp_contact(win, select_index)
        _t.sleep(0.8)

        # ── Perform action ─────────────────────────────────────────────
        if action == "call":
            import pyautogui
            pyautogui.hotkey("ctrl", "shift", "a")  # WhatsApp voice call hotkey
            _t.sleep(0.5)
            contact_name = candidates[select_index] if candidates else contact
            return {"success": True, "message": f"Calling {contact_name} on WhatsApp, Sir."}
        else:
            # Message
            import pyautogui
            # Click message input
            try:
                msg_box = win.child_window(auto_id="main-message-compose-box", control_type="Edit")
                if msg_box.exists(timeout=2):
                    msg_box.click_input()
                    _t.sleep(0.2)
                    msg_box.type_keys(body or "", with_spaces=True)
                else:
                    raise RuntimeError("No compose box")
            except Exception:
                pyautogui.write(body or "", interval=0.02)

            if send:
                pyautogui.press("enter")
                contact_name = candidates[select_index] if candidates else contact
                return {"success": True, "message": f"Message sent to {contact_name} on WhatsApp, Sir."}
            contact_name = candidates[select_index] if candidates else contact
            return {"success": True, "message": f"Message ready for {contact_name} on WhatsApp, Sir."}

    def _read_whatsapp_contact_list(self, win, contact_query: str) -> List[str]:
        """
        Walk the pywinauto UI tree to find contact result items.
        Returns a list of display names found in the search results.
        """
        names = []
        try:
            # WhatsApp Desktop renders results as list items in a conversation list
            # Try finding by role "ListItem" or "DataItem"
            for ctrl_type in ("ListItem", "DataItem", "TreeItem"):
                try:
                    items = win.children(control_type=ctrl_type)
                    for item in items[:10]:
                        try:
                            name = item.window_text().strip()
                            if name and len(name) > 1 and contact_query.lower() in name.lower():
                                names.append(name)
                        except Exception:
                            pass
                    if names:
                        break
                except Exception:
                    pass

            # Alternative: look for Text children whose content matches the contact name
            if not names:
                try:
                    texts = win.children(control_type="Text")
                    for t in texts:
                        try:
                            val = t.window_text().strip()
                            if val and contact_query.lower() in val.lower() and len(val) < 60:
                                names.append(val)
                        except Exception:
                            pass
                    names = list(dict.fromkeys(names))[:5]  # deduplicate
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[WhatsApp] Contact list read failed: {e}")

        # Fallback: if we got nothing useful, return single placeholder
        if not names:
            names = [contact_query]

        return names

    def _select_whatsapp_contact(self, win, index: int = 0):
        """Select the contact at the given index from search results."""
        import pyautogui, time as _t
        # Use arrow keys to navigate to the correct result, then Enter
        for _ in range(index):
            pyautogui.press("down")
            _t.sleep(0.15)
        pyautogui.press("enter")
        _t.sleep(0.5)

    def _whatsapp_pyautogui(self, contact: str, body: Optional[str],
                             send: bool, action: str) -> Dict:
        """
        DISABLED — pyautogui fallback is NOT safe for critical actions.
        Per policy: if UI automation fails, STOP execution and report failure.
        Do NOT click randomly or type blindly.
        """
        logger.error(
            f"[WhatsApp]  pywinauto failed and pyautogui fallback is DISABLED. "
            f"Contact='{contact}' action='{action}'. Reporting failure to user."
        )
        return {
            "success": False,
            "needs_speech_failure": True,
            "message": (
                f"Sir, I couldn't locate the WhatsApp search box reliably. "
                f"I've stopped to avoid clicking in the wrong place. "
                f"Please open WhatsApp manually and try again, or say the full contact name."
            ),
        }

    # ── Discord ───────────────────────────────────────────────────────────

    async def _discord_message(self, contact: str, body: str, send: bool) -> Dict:
        """
        Discord message: REST API → pywinauto GUI fallback.
        API requires DISCORD_BOT_TOKEN and the bot to share a server with the contact.
        """
        if self._discord_token and send:
            result = await self._discord_api_message(contact, body)
            if result.get("success"):
                return result
            logger.warning(f"[Discord] API failed → GUI fallback: {result.get('error')}")

        # GUI fallback: pywinauto → pyautogui
        return await self._discord_gui_message(contact, body, send)

    async def _discord_call(self, contact: str) -> Dict:
        """Discord call: GUI only (no REST API for voice calls)."""
        return await self._discord_gui_call(contact)

    async def _discord_api_message(self, contact: str, body: str) -> Dict:
        """
        Send via Discord REST API.
        Flow: search guild members → find DM channel → POST message.
        Requires the bot to share a guild with the target user.
        """
        try:
            import httpx
            headers = {
                "Authorization": f"Bot {self._discord_token}",
                "Content-Type":  "application/json",
            }
            contact_lower = contact.lower().strip()

            # Check cache first
            channel_id = self._discord_channel_cache.get(contact_lower)

            if not channel_id:
                # Step 1: Find user across guilds
                user_id = await self._discord_find_user_id(contact, headers)
                if not user_id:
                    return {
                        "success": False,
                        "error":   f"Could not find Discord user '{contact}' via API",
                    }

                # Step 2: Open DM channel
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        "https://discord.com/api/v10/users/@me/channels",
                        headers=headers,
                        json={"recipient_id": user_id},
                    )
                    r.raise_for_status()
                    channel_id = r.json()["id"]
                    self._discord_channel_cache[contact_lower] = channel_id

            # Step 3: Send message
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    json={"content": body},
                )
                r.raise_for_status()

            logger.info(f"[Discord]  API message sent to {contact}")
            return {"success": True, "message": f"Message sent to {contact} on Discord via API, Sir."}

        except ImportError:
            return {"success": False, "error": "httpx not installed — pip install httpx"}
        except Exception as e:
            logger.warning(f"[Discord] API error: {e}")
            return {"success": False, "error": str(e)}

    async def _discord_find_user_id(self, contact: str, headers: dict) -> Optional[str]:
        """Search for a user by display name or username across guilds."""
        try:
            import httpx
            contact_lower = contact.lower().strip()
            async with httpx.AsyncClient(timeout=10) as client:
                # Get bot's guilds
                r = await client.get("https://discord.com/api/v10/users/@me/guilds", headers=headers)
                r.raise_for_status()
                guilds = r.json()

                for guild in guilds[:5]:  # Check first 5 guilds
                    gid = guild["id"]
                    # Search members
                    r2 = await client.get(
                        f"https://discord.com/api/v10/guilds/{gid}/members/search"
                        f"?query={contact}&limit=10",
                        headers=headers,
                    )
                    if r2.status_code != 200:
                        continue
                    for member in r2.json():
                        user = member.get("user", {})
                        uname = (user.get("global_name") or user.get("username") or "").lower()
                        nick  = (member.get("nick") or "").lower()
                        if contact_lower in uname or contact_lower in nick:
                            return user["id"]
        except Exception as e:
            logger.debug(f"[Discord] User search failed: {e}")
        return None

    async def _discord_gui_call(self, contact: str) -> Dict:
        """Discord voice call via GUI (Ctrl+K quick switcher)."""
        import pyautogui, time as _t
        await asyncio.sleep(1.5)
        pyautogui.hotkey("ctrl", "k")
        _t.sleep(0.5)
        pyautogui.write(contact, interval=0.05)
        _t.sleep(1.0)
        pyautogui.press("enter")
        _t.sleep(0.8)
        pyautogui.hotkey("ctrl", "'")
        return {"success": True, "message": f"Dialling {contact} on Discord now, Sir."}

    async def _discord_gui_message(self, contact: str, body: str, send: bool) -> Dict:
        """Discord message via GUI fallback (Ctrl+K quick switcher)."""
        import pyautogui, time as _t
        await asyncio.sleep(1.5)
        pyautogui.hotkey("ctrl", "k")
        _t.sleep(0.5)
        pyautogui.write(contact, interval=0.05)
        _t.sleep(1.0)
        pyautogui.press("enter")
        _t.sleep(0.5)
        pyautogui.write(body or "", interval=0.02)
        if send:
            pyautogui.press("enter")
            return {"success": True, "message": f"Message sent to {contact} on Discord, Sir."}
        return {"success": True, "message": f"Message ready for {contact} on Discord, Sir."}

    # ── Telegram ─────────────────────────────────────────────────────────

    async def _telegram_message(self, contact: str, body: str, send: bool) -> Dict:
        """
        Telegram message: REST Bot API only.
        Requires TELEGRAM_BOT_TOKEN. The bot must have an existing conversation
        with the contact (user must have messaged the bot first).
        """
        if not self._telegram_token:
            return {
                "success": False,
                "error":   "TELEGRAM_BOT_TOKEN not set — add it to .env",
                "message": "Sir, I need a Telegram bot token to send Telegram messages. Please set TELEGRAM_BOT_TOKEN in your .env file.",
            }

        if not send:
            return {"success": True, "message": f"Telegram message ready for {contact}, Sir. Say 'send it' to confirm."}

        try:
            import httpx
            contact_lower = contact.lower().strip()

            # Look up cached chat_id
            chat_id = self._telegram_chat_cache.get(contact_lower)

            if not chat_id:
                chat_id = await self._telegram_find_chat_id(contact)
                if not chat_id:
                    return {
                        "success": False,
                        "message": (
                            f"Sir, I couldn't find a Telegram chat with '{contact}'. "
                            f"They need to send a message to your bot first."
                        ),
                    }
                self._telegram_chat_cache[contact_lower] = chat_id

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{self._telegram_token}/sendMessage",
                    json={"chat_id": chat_id, "text": body},
                )
                r.raise_for_status()

            logger.info(f"[Telegram]  Message sent to {contact} (chat_id={chat_id})")
            return {"success": True, "message": f"Message sent to {contact} on Telegram, Sir."}

        except ImportError:
            return {"success": False, "error": "httpx not installed — pip install httpx"}
        except Exception as e:
            logger.warning(f"[Telegram] API error: {e}")
            return {"success": False, "message": f"Sir, Telegram message failed: {str(e)[:60]}"}

    async def _telegram_find_chat_id(self, contact: str) -> Optional[str]:
        """
        Find chat_id by scanning getUpdates for a contact's username or first name.
        Only works if the contact has already interacted with the bot.
        """
        try:
            import httpx
            contact_lower = contact.lower().strip()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://api.telegram.org/bot{self._telegram_token}/getUpdates",
                    params={"limit": 100, "offset": -100},
                )
                r.raise_for_status()
                updates = r.json().get("result", [])

            for update in reversed(updates):  # Most recent first
                msg = update.get("message") or update.get("callback_query", {}).get("message")
                if not msg:
                    continue
                sender = msg.get("from", {})
                fname  = (sender.get("first_name") or "").lower()
                lname  = (sender.get("last_name") or "").lower()
                uname  = (sender.get("username") or "").lower()
                full   = f"{fname} {lname}".strip()
                if (contact_lower in fname or contact_lower in uname or
                        contact_lower in full or full in contact_lower):
                    return str(msg["chat"]["id"])
        except Exception as e:
            logger.debug(f"[Telegram] Chat ID search failed: {e}")
        return None

    # ── WhatsApp Playwright headless ──────────────────────────────────────

    async def _whatsapp_playwright_message(self, contact: str, body: str,
                                            send: bool, task=None,
                                            on_disambiguate=None) -> Dict:
        """
        WhatsApp Web via Playwright (headless=False, uses your existing Chrome session).
        This runs your Chrome profile so you stay logged in — no QR scan needed.
        Falls back to pywinauto if Playwright is unavailable.
        """
        try:
            from playwright.async_api import async_playwright
            import os

            # Use your existing Chrome user data so WA Web is already logged in
            chrome_profile = os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome\User Data"
            )

            async with async_playwright() as pw:
                context = await pw.firefox.launch_persistent_context(
                    user_data_dir=chrome_profile,
                    headless=False,
                    channel="firefox",
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                page = None
                # Find existing WA tab or open new one
                for p in context.pages:
                    if "web.whatsapp.com" in p.url:
                        page = p
                        break
                if not page:
                    page = await context.new_page()
                    await page.goto("https://web.whatsapp.com", wait_until="networkidle",
                                    timeout=30000)

                # Search for contact
                await page.wait_for_selector('[data-testid="chat-list-search"]',
                                              timeout=10000)
                await page.click('[data-testid="chat-list-search"]')
                await page.fill('[data-testid="chat-list-search"]', contact)
                await page.wait_for_timeout(1500)

                # Collect results
                results = await page.query_selector_all('[data-testid="cell-frame-title"]')
                candidates = []
                for r in results[:5]:
                    txt = (await r.inner_text()).strip()
                    if txt:
                        candidates.append(txt)

                # Disambiguation
                if len(candidates) > 1:
                    first_names = [c.split()[0].lower() for c in candidates]
                    if len(set(first_names)) < len(candidates):
                        numbered = ", ".join(f"{i+1}: {n}" for i, n in enumerate(candidates))
                        await context.close()
                        return {
                            "success":              False,
                            "needs_disambiguation": True,
                            "candidates":           candidates,
                            "message": f"Sir, found multiple contacts: {numbered}. Which one?",
                        }

                if not candidates:
                    await context.close()
                    return {
                        "success": False,
                        "needs_speech_failure": True,
                        "message": f"Sir, I couldn't find '{contact}' on WhatsApp Web.",
                    }

                # Click first (or only) result
                await results[0].click()
                await page.wait_for_timeout(800)

                # Type message
                msg_box = await page.wait_for_selector(
                    '[data-testid="conversation-compose-box-input"]',
                    timeout=5000,
                )
                await msg_box.click()
                await msg_box.fill(body or "")

                if send:
                    send_btn = await page.query_selector('[data-testid="send"]')
                    if send_btn:
                        await send_btn.click()
                    else:
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)
                    contact_name = candidates[0] if candidates else contact
                    await context.close()
                    return {"success": True,
                            "message": f"Message sent to {contact_name} on WhatsApp, Sir."}

                contact_name = candidates[0] if candidates else contact
                await context.close()
                return {"success": True,
                        "message": f"Message ready for {contact_name} on WhatsApp, Sir."}

        except ImportError:
            logger.info("[WhatsApp] Playwright not available → pywinauto fallback")
            return {"success": False, "error": "playwright_unavailable"}
        except Exception as e:
            logger.warning(f"[WhatsApp] Playwright failed: {e} → pywinauto fallback")
            return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _normalize_email(raw: str) -> str:
    result = raw.lower().strip()
    result = re.sub(r'\s+at\s+',         '@', result)
    result = re.sub(r'\s+dot\s+',        '.', result)
    result = re.sub(r'\s+underscore\s+', '_', result)
    result = re.sub(r'\s+dash\s+',       '-', result)
    result = re.sub(r'\s+hyphen\s+',     '-', result)
    if '@' in result:
        parts  = result.split('@')
        local  = parts[0].replace(' ', '')
        domain = parts[1].replace(' ', '') if len(parts) > 1 else ''
        result = f"{local}@{domain}"
    else:
        result = result.replace(' ', '')
    return result


def _generate_subject(purpose: str) -> str:
    purpose = purpose.strip()
    if len(purpose) <= 60:
        return purpose.capitalize()
    return " ".join(purpose.split()[:8]).capitalize()


def _build_email_body(research_body: str, purpose: str, contact_info: str) -> str:
    parts = [research_body or f"I am writing regarding {purpose}."]
    if contact_info:
        parts.append(f"\nContact Details:\n{contact_info}")
    parts.append("\n\nThank you for your time.\n\nBest regards")
    return "\n".join(parts)


def extract_initial_slots(task_type: str, text: str) -> Dict[str, str]:
    slots: Dict[str, str] = {}
    text_lower = text.lower()

    if task_type == "compose_email":
        m = re.search(r'\bto\s+([\w.+%-]+@[\w.-]+\.\w+)', text, re.IGNORECASE)
        if m:
            slots["to_address"] = m.group(1)
        pm = re.search(r'(?:about|regarding|for)\s+(.+?)(?:\s+to\s+|$)', text, re.IGNORECASE)
        if pm:
            slots["purpose"] = pm.group(1).strip()

    elif task_type in ("compose_message", "make_call"):
        for plat in ("whatsapp", "discord", "telegram"):
            if plat in text_lower:
                slots["platform"] = plat
                break
        cm = re.search(
            r'\b(?:to|message|call|ring)\s+([A-Za-z][a-zA-Z\s]+?)(?:\s+(?:on|saying|about)|$)',
            text, re.IGNORECASE
        )
        if cm:
            slots["contact"] = cm.group(1).strip()
        if task_type == "compose_message":
            bm = re.search(r'\bsaying\s+(.+)', text, re.IGNORECASE)
            if bm:
                slots["body"] = bm.group(1).strip()

    return slots


# ── SINGLETON ─────────────────────────────────────────────────────────────

_orchestrator: Optional[AgenticOrchestrator] = None
def get_orchestrator(groq_api_key: str = "") -> AgenticOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgenticOrchestrator(groq_api_key=groq_api_key)
    return _orchestrator