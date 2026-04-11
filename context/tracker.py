"""
CONTEXT TRACKER — Real-time Awareness of What's Happening
==========================================================
FIX LOG:
  - BUG 1: ContextTracker had TWO `set()` methods (one sync, one async).
    Python silently keeps only the second definition — the async one.
    Any synchronous caller of ctx.set() would get a coroutine object
    instead of actually setting the value. Fixed by merging into one
    sync method (set) and adding a separate async_set() for async callers.
  - BUG 2: update_from_turn() was async but called ctx.set() (now sync),
    which was fine, but the internal lock usage mixed async + threading
    incorrectly. Cleaned up.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ActiveWindowMonitor:
    """
    Polls the OS for the currently focused window.
    Windows-only (uses win32gui). Gracefully degrades on other OS.
    """

    def __init__(self, poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._active_window: str = "desktop"
        self._active_process: str = ""
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self):
        self._running = False

    @property
    def active_window(self) -> str:
        with self._lock:
            return self._active_window

    @property
    def active_process(self) -> str:
        with self._lock:
            return self._active_process

    def _poll_loop(self):
        while self._running:
            try:
                title, process = self._get_foreground()
                with self._lock:
                    self._active_window = title
                    self._active_process = process
            except Exception:
                pass
            time.sleep(self.poll_interval)

    def _get_foreground(self) -> tuple:
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd).lower()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            process_name = proc.name().lower().replace(".exe", "")
            return title, process_name
        except ImportError:
            return "desktop", "desktop"
        except Exception:
            return "desktop", "desktop"


class ContextTracker:
    """
    Maintains the full context of Jarvis's running session.

    Any module can:
      ctx.get("last_song")         → "Blinding Lights"
      ctx.get("last_app")          → "spotify"
      ctx.set("last_song", "...")  → stores it (sync)
      ctx.snapshot()               → full dict for LLM prompts
    """

    HISTORY_SIZE = 20

    def __init__(self):
        self._state: Dict[str, Any] = {
            "active_app": "desktop",
            "active_window_title": "",
            "last_app": None,
            "last_url": None,
            "last_song": None,
            "last_platform": None,
            "last_contact": None,
            "last_message_platform": None,
            "last_command": None,
            "last_intent": None,
            "last_entity": None,
            "last_result_success": None,
            "conversation_history": deque(maxlen=self.HISTORY_SIZE),
        }

        self._lock = threading.Lock()
        self._monitor = ActiveWindowMonitor()
        self._monitor.start()

        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

        logger.info("🔄 Context tracker running")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            val = self._state.get(key, default)
            if isinstance(val, deque):
                return list(val)
            return val

    def set(self, key: str, value: Any):
        """
        Synchronous set — safe to call from any thread or sync context.

        FIX: The original code defined set() twice — a sync version then
        an async version. Python kept only the async one, so sync callers
        received a coroutine object and the value was never stored.
        Now there is exactly one sync set(). Use async_set() from async code.
        """
        with self._lock:
            self._state[key] = value

    async def async_set(self, key: str, value: Any):
        """Async-compatible set — wraps the sync version."""
        self.set(key, value)

    def snapshot(self) -> Dict:
        """Return a serializable snapshot of the current context."""
        with self._lock:
            snap = {}
            for k, v in self._state.items():
                if isinstance(v, deque):
                    snap[k] = list(v)
                else:
                    snap[k] = v
            snap["active_window_title"] = self._monitor.active_window
            snap["active_process"] = self._monitor.active_process
            snap["active_app"] = self._infer_active_app(
                self._monitor.active_window,
                self._monitor.active_process
            )
        return snap

    async def update_from_turn(self, turn) -> None:
        """
        Update context after a completed agent turn.
        """
        intent = turn.intent or {}
        entities = intent.get("entities", {})
        intent_name = intent.get("intent", "")

        # Build updates then apply under a single lock acquisition
        updates: Dict[str, Any] = {
            "last_command": turn.raw_input,
            "last_intent": intent_name,
            "last_result_success": turn.success,
        }

        if entities.get("app"):
            updates["last_app"] = entities["app"]
            updates["last_entity"] = entities["app"]

        if entities.get("song"):
            updates["last_song"] = entities["song"]
            updates["last_entity"] = entities["song"]

        if entities.get("platform"):
            updates["last_platform"] = entities["platform"]

        if entities.get("url"):
            updates["last_url"] = entities["url"]
            updates["last_entity"] = entities["url"]

        if entities.get("contact"):
            updates["last_contact"] = entities["contact"]
            updates["last_entity"] = entities["contact"]

        if entities.get("message_platform"):
            updates["last_message_platform"] = entities["message_platform"]

        history_entry = {
            "timestamp": time.time(),
            "input": turn.raw_input,
            "intent": intent_name,
            "response": turn.spoken_response,
            "success": turn.success,
        }

        with self._lock:
            self._state.update(updates)
            self._state["conversation_history"].append(history_entry)

    def get_conversation_history(self, n: int = 5) -> List[Dict]:
        with self._lock:
            hist = list(self._state["conversation_history"])
        return hist[-n:]

    def _infer_active_app(self, window_title: str, process: str) -> str:
        mappings = {
            "chrome": "chrome", "firefox": "firefox", "msedge": "edge",
            "brave": "brave", "spotify": "spotify", "discord": "discord",
            "code": "vscode", "notepad": "notepad", "explorer": "explorer",
            "teams": "teams", "zoom": "zoom", "slack": "slack", "vlc": "vlc",
        }
        for key, app in mappings.items():
            if key in window_title or key in process:
                return app
        return process if process else "desktop"

    def _sync_loop(self):
        while True:
            try:
                active = self._infer_active_app(
                    self._monitor.active_window,
                    self._monitor.active_process
                )
                with self._lock:
                    self._state["active_app"] = active
                    self._state["active_window_title"] = self._monitor.active_window
            except Exception:
                pass
            time.sleep(0.5)
