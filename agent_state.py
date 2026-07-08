"""
AGENT STATE v1 — Single Source of Truth for Jarvis Runtime State
================================================================
Replaces scattered global variables across service.py, core.py, main.py.

Provides:
  - CentralAgentState   : runtime state container (FSM, bg tasks, TTS queue, context)
  - CommandRouter       : single entry point after STT — routes to correct handler
  - ExecutionResult     : mandatory contract for every engine response
  - TTS Queue           : all speech goes through state.tts_queue, never direct speak()

Architecture:
  STT → CommandRouter.route(text)
          ├── FSM continuation → AgenticOrchestrator.handle_response()
          ├── FSM override     → reset FSM, route as new command
          ├── Advisor answer   → GuidedAdvisor.start_or_continue()
          └── New command      → JarvisAgentCore.process()

  Every engine returns ExecutionResult(success, spoken_response, requires_followup)
  spoken_response MUST be non-empty or a default is injected.

TTS:
  All speech: state.tts_queue.put(text)
  Worker thread drains queue → calls actual TTS engine
  This fixes: missing follow-ups, missing background task speech
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# EXECUTION RESULT CONTRACT
# Every engine (FSM, planner, core, advisor) MUST return this.
# spoken_response is NEVER empty — a fallback is injected if empty.
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionResult:
    success:           bool
    spoken_response:   str
    requires_followup: bool = False
    full_response:     str  = ""
    intent:            str  = ""
    error:             Optional[str] = None

    def __post_init__(self):
        # Contract enforcement: spoken_response must never be empty
        if not self.spoken_response or not self.spoken_response.strip():
            if self.success:
                self.spoken_response = "Done, Sir."
            else:
                self.spoken_response = self.error or "Something went wrong, Sir."

        if not self.full_response:
            self.full_response = self.spoken_response


# ════════════════════════════════════════════════════════════════════════════
# CENTRAL AGENT STATE — Single Source of Truth
# ════════════════════════════════════════════════════════════════════════════

class FSMState(Enum):
    IDLE       = auto()
    EMAIL      = auto()
    CALL       = auto()
    MESSAGE    = auto()
    ADVISOR    = auto()


@dataclass
class BackgroundTaskInfo:
    task_id:    str
    name:       str
    started_at: float = field(default_factory=time.time)
    done:       bool  = False
    result:     str   = ""


class CentralAgentState:
    """
    Single source of truth. One instance, shared across all components.

    Fields:
      fsm_state        : What multi-turn FSM (if any) is active
      fsm_session_data : Slots collected by the active FSM
      background_tasks : All submitted bg tasks (keyed by task_id)
      tts_queue        : All speech goes here; never call speak() directly
      session_context  : Rolling context (last app, last contact, etc.)
    """

    def __init__(self):
        # FSM state
        self.fsm_state: FSMState                  = FSMState.IDLE
        self.fsm_session_data: Dict[str, Any]     = {}

        # Background tasks
        self._bg_tasks: Dict[str, BackgroundTaskInfo] = {}
        self._bg_lock  = threading.Lock()

        # TTS queue — ALL speech goes here
        self.tts_queue: queue.Queue = queue.Queue(maxsize=50)

        # Session context (last entity, last app, etc.)
        self.session_context: Dict[str, Any] = {}

        # Advisor session handle (set by CommandRouter)
        self._advisor_active = False

    # ── FSM helpers ──────────────────────────────────────────────────────

    def start_fsm(self, fsm: FSMState, initial_data: Dict = None):
        self.fsm_state        = fsm
        self.fsm_session_data = initial_data or {}
        logger.info(f"[State] FSM started: {fsm.name}")

    def reset_fsm(self):
        if self.fsm_state != FSMState.IDLE:
            logger.info(f"[State] FSM reset: {self.fsm_state.name} → IDLE")
        self.fsm_state        = FSMState.IDLE
        self.fsm_session_data = {}

    @property
    def fsm_active(self) -> bool:
        return self.fsm_state != FSMState.IDLE

    # ── Background task helpers ──────────────────────────────────────────

    def register_bg_task(self, task_id: str, name: str):
        with self._bg_lock:
            self._bg_tasks[task_id] = BackgroundTaskInfo(task_id=task_id, name=name)
        logger.info(f"[State] BG task registered: {task_id} '{name}'")

    def mark_bg_task_done(self, task_id: str, result: str):
        with self._bg_lock:
            t = self._bg_tasks.get(task_id)
            if t:
                t.done   = True
                t.result = result
        # Push result to TTS queue so it's spoken immediately
        if result:
            self.speak(result)
        logger.info(f"[State] BG task done: {task_id}")

    def get_bg_status(self) -> str:
        with self._bg_lock:
            running = [t for t in self._bg_tasks.values() if not t.done]
            done    = [t for t in self._bg_tasks.values() if t.done]
        parts = []
        if running:
            parts.append(f"{len(running)} task(s) running: " +
                         ", ".join(t.name for t in running))
        if done:
            parts.append(f"{len(done)} completed.")
        return " | ".join(parts) if parts else "No background tasks running, Sir."

    # ── TTS queue ────────────────────────────────────────────────────────

    def speak(self, text: str):
        """All speech goes through here. NEVER call speak_fn() directly."""
        if text and text.strip():
            try:
                self.tts_queue.put_nowait(text.strip())
            except queue.Full:
                logger.warning("[State] TTS queue full — dropping speech")

    # ── Context helpers ──────────────────────────────────────────────────

    def update_context(self, **kwargs):
        self.session_context.update(kwargs)

    def get_context(self, key: str, default: Any = None) -> Any:
        return self.session_context.get(key, default)


# ════════════════════════════════════════════════════════════════════════════
# COMMAND ROUTER — Single entry point after STT
# ════════════════════════════════════════════════════════════════════════════

# Independent commands that MUST override any active FSM
_INDEPENDENT_INTENT_PATTERNS = re.compile(
    r"^\s*(?:"
    r"open\s+\w|"
    r"close\s+(?!\w+\s+tab)\w|"
    r"play\s+\w|"
    r"launch\s+\w|"
    r"search\s+(?:for\s+)?\w|"
    r"change\s+(?:my\s+)?(?:desktop|screen|display|resolution|volume|brightness)|"
    r"set\s+(?:volume|brightness|resolution|display)|"
    r"take\s+(?:a\s+)?screenshot|"
    r"(?:shut\s?down|restart|reboot|lock)\s*(?:the\s+)?(?:computer|pc|system|screen)?"
    r")",
    re.IGNORECASE,
)

# Short answers / affirmatives — always treated as FSM continuation
_CONTINUATION_WORDS = frozenset({
    "yes", "no", "yeah", "nah", "nope", "sure", "okay", "ok",
    "go ahead", "send it", "do it", "proceed", "confirm", "yep",
    "whatsapp", "discord", "telegram",   # Platform choices
    "cancel", "stop", "abort",
})

_INDEPENDENT_INTENTS = frozenset({
    "open_app", "close_app", "play_media", "pause_media",
    "resume_media", "next_track", "previous_track",
    "search_web", "open_website", "smart_open",
    "take_screenshot", "lock", "shutdown", "restart",
    "system_action", "deep_research",
    "close_tab", "new_tab", "scroll", "type_text",
})


def is_independent_command(text: str, intent: Optional[str] = None) -> bool:
    """
    True when the input represents a brand-new command that should
    override (cancel) any active FSM session.
    """
    if intent and intent in _INDEPENDENT_INTENTS:
        return True
    stripped = text.strip().rstrip(".")
    if stripped.lower() in _CONTINUATION_WORDS:
        return False
    if len(stripped.split()) <= 3:
        return False
    return bool(_INDEPENDENT_INTENT_PATTERNS.match(stripped))


class CommandRouter:
    """
    The single gateway between STT output and the agent backend.

    Routing priority:
      1. Active orchestrator FSM → continuation or cancel
      2. Active advisor session  → answer or cancel
      3. New orchestrator task   → email/call/message trigger
      4. Normal agent pipeline   → everything else

    All spoken responses are pushed to state.tts_queue.
    requires_followup triggers the microphone automatically.
    """

    def __init__(
        self,
        state:        CentralAgentState,
        agent_core,                          # JarvisAgentCore
        orchestrator,                        # AgenticOrchestrator
        agent_loop:   asyncio.AbstractEventLoop,
    ):
        self._state        = state
        self._agent        = agent_core
        self._orch         = orchestrator
        self._loop         = agent_loop
        self._advisor      = None            # set lazily

    def set_advisor(self, advisor):
        self._advisor = advisor

    def route(self, text: str) -> ExecutionResult:
        """
        Main entry point. Called synchronously from the voice thread.
        Returns ExecutionResult — never raises.
        """
        try:
            return self._route_inner(text)
        except Exception as e:
            logger.error(f"[Router] Unhandled error: {e}", exc_info=True)
            result = ExecutionResult(success=False, spoken_response="", error=str(e))
            self._state.speak(result.spoken_response)
            return result

    def _route_inner(self, text: str) -> ExecutionResult:
        text = text.strip()
        if not text:
            return ExecutionResult(success=True, spoken_response="")

        logger.info(f"[Router] ▶ '{text}'")

        # ── Route 1: Active FSM ──────────────────────────────────────────
        if self._orch.has_active_task():
            if is_independent_command(text):
                logger.info("[Router] Independent command — cancelling FSM")
                self._orch.reset()
                self._state.reset_fsm()
                # Fall through to route as new command
            elif text.strip().lower().rstrip(".") in {"cancel", "stop", "abort", "never mind"}:
                self._orch.reset()
                self._state.reset_fsm()
                result = ExecutionResult(
                    success=True, spoken_response="Cancelled, Sir."
                )
                self._state.speak(result.spoken_response)
                return result
            else:
                # Continue FSM
                spoken = self._run_async(self._orch.handle_response(text, self._state.speak))
                result = ExecutionResult(
                    success=True,
                    spoken_response=spoken or "",
                    requires_followup=True,
                )
                if spoken:
                    self._state.speak(spoken)
                return result

        # ── Route 2: Active advisor session ─────────────────────────────
        if self._advisor and self._advisor.has_active_session():
            if is_independent_command(text):
                logger.info("[Router] Independent command — abandoning advisor")
                self._advisor.abandon()
                # Fall through
            else:
                result_text = self._run_async(
                    self._advisor.start_or_continue(
                        user_text=text,
                        speak_fn=self._state.speak,
                        task_manager=getattr(self._agent, 'task_manager', None),
                    )
                )
                if result_text == "[BACKGROUND_TASK_STARTED]":
                    return ExecutionResult(
                        success=True,
                        spoken_response="",
                        requires_followup=False,
                    )
                if result_text:
                    spoken = result_text[:300]
                    self._state.speak(spoken)
                    return ExecutionResult(
                        success=True, spoken_response=spoken
                    )
                # Advisor asked next question (already spoken via speak_fn)
                return ExecutionResult(
                    success=True, spoken_response="", requires_followup=True
                )
            
        # ── WhatsApp fast-path: skip orchestrator, use unified_comm ─────
        if any(w in text.lower() for w in ("whatsapp", "wp", "wapp")):
            # Let the normal agent pipeline handle it
            # The planner will generate search_contact → confirmation
            logger.info("[Router] WhatsApp detected — routing to agent pipeline")
            return self._agent_pipeline(text)

        # ── Route 3: New agentic task ────────────────────────────────────
        task_type = _detect_agentic_trigger(text)
        if task_type:
            from task_orchestrator import extract_initial_slots
            initial = extract_initial_slots(task_type, text)
            context = {"active_app": self._state.get_context("active_app", "desktop")}
            self._run_async(
                self._orch.start_task(task_type, context, self._state.speak, initial)
            )
            return ExecutionResult(success=True, spoken_response="", requires_followup=True)

        # ── Route 4: Normal agent pipeline ──────────────────────────────
        return self._agent_pipeline(text)

    def _agent_pipeline(self, text: str) -> ExecutionResult:
        """Run JarvisAgentCore.process() and convert AgentTurn → ExecutionResult."""
        try:
            turn = self._run_async(self._agent.process(text))
        except Exception as e:
            logger.error(f"[Router] Agent pipeline error: {e}", exc_info=True)
            return ExecutionResult(success=False, spoken_response="", error=str(e))

        spoken = (turn.spoken_response or "").strip()

        # If no spoken text but execution succeeded, build a fallback
        if not spoken and turn.success and turn.execution_results:
            spoken = _build_fallback_response(turn)

        if spoken:
            self._state.speak(spoken)

        requires_followup = getattr(turn, "requires_followup", False)
        return ExecutionResult(
            success=turn.success,
            spoken_response=spoken,
            requires_followup=requires_followup,
            intent=turn.intent.get("intent", "") if turn.intent else "",
            error=turn.error,
        )

    def _run_async(self, coro) -> Any:
        """Run a coroutine on the agent event loop from any thread."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=60.0)
        except Exception as e:
            logger.error(f"[Router] Async error: {e}", exc_info=True)
            return None


# ════════════════════════════════════════════════════════════════════════════
# TTS QUEUE WORKER
# Drains state.tts_queue and calls the real TTS engine.
# Run as a daemon thread at startup.
# ════════════════════════════════════════════════════════════════════════════

class TTSQueueWorker:
    """
    Reads from state.tts_queue and calls tts_fn(text).
    Guarantees:
      - Background task results are spoken as soon as they arrive
      - Follow-up questions are spoken in order
      - No component calls tts_fn() directly
    """

    def __init__(self, state: CentralAgentState, tts_fn: Callable[[str], None]):
        self._state   = state
        self._tts_fn  = tts_fn
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="jarvis-tts-worker"
        )
        self._thread.start()

    def _run(self):
        while self._running:
            try:
                text = self._state.tts_queue.get(timeout=0.1)
                if text:
                    try:
                        self._tts_fn(text)
                    except Exception as e:
                        logger.error(f"[TTS Worker] Error: {e}")
            except queue.Empty:
                continue

    def stop(self):
        self._running = False
        self._thread.join(timeout=2.0)


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

_AGENTIC_TRIGGERS: Dict[str, List[str]] = {
    "compose_email": [
        "write an email", "write a mail", "compose an email", "compose a mail",
        "send an email", "send a mail", "draft an email", "draft a mail",
    ],
    "compose_message": [
        "send a message", "send a whatsapp", "send a text",
        "message someone", "write a message",
    ],
    "make_call": [
        "make a call", "make a phone call", "call someone",
        "video call", "ring someone",
        "call on whatsapp", "call on discord",
    ],
}


def _detect_agentic_trigger(text: str) -> Optional[str]:
    t = text.lower().strip()
    for task_type, triggers in _AGENTIC_TRIGGERS.items():
        if any(tr in t for tr in triggers):
            return task_type
    return None


def _build_fallback_response(turn) -> str:
    """Build a spoken response from execution results when spoken_response is empty."""
    if not turn.execution_results:
        return ""

    intent  = turn.intent.get("intent", "") if turn.intent else ""
    ents    = turn.intent.get("entities", {}) if turn.intent else {}

    _SIMPLE = {
        "pause_media":    "Paused, Sir.",
        "resume_media":   "Resuming, Sir.",
        "next_track":     "Next track, Sir.",
        "previous_track": "Previous track, Sir.",
        "lock":           "Locking the screen, Sir.",
        "shutdown":       "Shutting down, Sir.",
        "restart":        "Restarting, Sir.",
        "take_screenshot":"Screenshot taken, Sir.",
        "close_tab":      "Tab closed, Sir.",
        "new_tab":        "New tab opened, Sir.",
    }
    if intent in _SIMPLE:
        return _SIMPLE[intent]

    if intent == "open_app":
        app = ents.get("app") or ents.get("name") or ""
        return f"Opening {app}, Sir." if app else "Done, Sir."

    if intent == "play_media":
        song     = ents.get("song", "")
        platform = ents.get("platform", "")
        if song and platform:
            return f"Playing {song} on {platform}, Sir."
        if song:
            return f"Playing {song}, Sir."

    for r in turn.execution_results:
        out = r.get("output", {})
        if isinstance(out, dict):
            msg = out.get("message") or out.get("spoken") or out.get("spoken_summary")
            if msg:
                return str(msg)

    return "Done, Sir." if turn.success else "That didn't work, Sir."
