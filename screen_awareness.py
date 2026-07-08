"""
SCREEN AWARENESS — Production-Complete Event-Driven OS Context Daemon
======================================================================
What this solves:
  Jarvis can now understand ANY window the user is looking at — not just
  what Jarvis opened via Playwright/browser automation.

  When you say "read this" or "what's on screen", Jarvis reads the actual
  active window content via Windows UI Automation (UIA), no screenshot needed.

Architecture:
  EventDrivenScreenDaemon
    ├── HWND watcher thread (50ms poll, O(1) native OS call)
    ├── UIA traversal (depth-limited to 3, only on window change)
    ├── OCR fallback (Tesseract, only when UIA yields nothing)
    └── Context injector → session_memory + context.tracker

Wiring in main.py:
    from screen_awareness import screen_daemon
    screen_daemon.start(context_updater=lambda d: agent_state.update_context(**d))
    # Or directly update session_memory:
    screen_daemon.start(session_memory=session_memory)

Safety:
  - UIA traversal is depth-limited (max 3 levels) to prevent hangs on
    complex apps like Chrome or Visual Studio
  - 200ms UIA timeout per window (fails fast)
  - OCR only runs when UIA yields < 20 chars
  - All text is capped at 1500 chars before injection
"""

import ctypes
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# SCREEN CONTEXT DATA
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ScreenContext:
    active_window:    str  = ""
    active_app_class: str  = ""
    screen_text:      str  = ""
    source:           str  = ""   # "uia" | "ocr" | "none"
    captured_at:      float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.captured_at

    @property
    def has_content(self) -> bool:
        return bool(self.screen_text.strip())


# ════════════════════════════════════════════════════════════════════════════
# APP BLACKLIST — don't UIA-traverse these (hang risk / privacy)
# ════════════════════════════════════════════════════════════════════════════

_UIA_BLACKLIST_CLASSES: Set[str] = {
    "MozillaWindowClass",     # Firefox
    "Chrome_WidgetWin_1",     # Chrome (too deep DOM)
    "ApplicationFrameWindow", # UWP shell wrapper — Jarvis windows themselves
}

_UIA_BLACKLIST_TITLES: List[str] = [
    "jarvis", "task manager", "registry editor",
]

_UI_NOISE_SET = frozenset({
    "ok", "cancel", "close", "minimize", "maximize", "yes", "no",
    "apply", "reset", "back", "next", "finish", "submit query",
    "file", "edit", "view", "help", "window", "tools",
    "type here to search", "search", "address bar",
})


# ════════════════════════════════════════════════════════════════════════════
# SEMANTIC DEDUPLICATION
# ════════════════════════════════════════════════════════════════════════════

def _deduplicate_text(texts: List[str]) -> str:
    """Remove duplicate lines and UI noise, keep only meaningful text."""
    seen: Set[str] = set()
    kept: List[str] = []
    noise_re = re.compile(
        r'^(?:ok|cancel|close|minimize|maximize|button|checkbox|'
        r'menu|file|edit|view|help|window|yes|no|apply|\d{1,2}[:/]\d{2})$',
        re.IGNORECASE,
    )
    for t in texts:
        t = t.strip()
        key = t.lower()[:80]
        if not t or len(t) < 3:
            continue
        if key in seen:
            continue
        if noise_re.match(t):
            continue
        seen.add(key)
        kept.append(t)
    return " | ".join(kept)[:1500]


# ════════════════════════════════════════════════════════════════════════════
# UIA EXTRACTOR
# ════════════════════════════════════════════════════════════════════════════

def _extract_uia(hwnd: int) -> Optional[ScreenContext]:
    """
    Extract readable text from a window via Windows UI Automation.
    Returns None if UIA is unavailable or extraction fails.
    Bounded to depth 3 — never hangs.
    """
    try:
        import uiautomation as auto
        auto.SetGlobalSearchTimeout(0.2)

        window = auto.ControlFromHandle(hwnd)
        if not window:
            return None

        title     = window.Name or ""
        app_class = window.ClassName or ""

        # Skip blacklisted app classes
        if app_class in _UIA_BLACKLIST_CLASSES:
            return None
        if any(bl in title.lower() for bl in _UIA_BLACKLIST_TITLES):
            return None

        ALLOWED = {
            auto.ControlType.TextControl,
            auto.ControlType.DocumentControl,
            auto.ControlType.EditControl,
            auto.ControlType.StaticControl,
        }

        texts: List[str] = []
        try:
            for control, depth in auto.WalkControl(window, maxDepth=3):
                if depth > 3:
                    break
                if control.ControlType in ALLOWED and control.Name:
                    texts.append(control.Name.strip())
        except Exception:
            pass

        cleaned = _deduplicate_text(texts)
        return ScreenContext(
            active_window=title,
            active_app_class=app_class,
            screen_text=cleaned,
            source="uia",
        )

    except ImportError:
        logger.debug("[ScreenDaemon] uiautomation not installed — UIA extraction unavailable")
        return None
    except Exception as e:
        logger.debug(f"[ScreenDaemon] UIA extraction failed: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# OCR FALLBACK
# ════════════════════════════════════════════════════════════════════════════

def _extract_ocr(hwnd: int) -> Optional[str]:
    """
    Capture the foreground window and run Tesseract OCR on it.
    Only called when UIA yields less than 20 useful characters.
    Requires: pip install pytesseract Pillow
              Tesseract installed at C:/Program Files/Tesseract-OCR/tesseract.exe
    """
    try:
        import ctypes
        import io
        from PIL import ImageGrab
        import pytesseract

        # Capture screen
        img = ImageGrab.grab()
        if img is None:
            return None

        text = pytesseract.image_to_string(img, lang="eng", config="--psm 3")
        if not text or not text.strip():
            return None

        # Clean OCR noise
        lines = [ln.strip() for ln in text.split("\n") if len(ln.strip()) > 4]
        return " | ".join(lines)[:1500]

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[ScreenDaemon] OCR fallback failed: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# MAIN DAEMON
# ════════════════════════════════════════════════════════════════════════════

class EventDrivenScreenDaemon:
    """
    Production event-driven screen daemon.

    Usage:
        from screen_awareness import screen_daemon
        screen_daemon.start(
            on_context_change=lambda ctx: my_update_fn(ctx)
        )
        # Get latest context anywhere:
        ctx = screen_daemon.current
    """

    def __init__(self):
        self._running      = False
        self._thread: Optional[threading.Thread] = None
        self._last_hwnd    = 0
        self._current: ScreenContext = ScreenContext()
        self._lock         = threading.Lock()
        self._on_change: Optional[Callable[[ScreenContext], None]] = None
        self._user32       = None
        self.context_updater = None
        self._ui_map: Dict[int, Dict] = {}

    def start(
            self,
            on_context_change: Optional[Callable[[ScreenContext], None]] = None,
            context_updater: Optional[Callable[[Dict], None]] = None
            ):
        """
        Start the daemon.

        Args:
            on_context_change: Called whenever screen context changes.
                               Receives a ScreenContext object.
        """
        self.context_updater = context_updater
        self._on_change = on_context_change
        if self._running:
            return
        self._on_change = on_context_change
        self._running   = True
        try:
            self._user32 = ctypes.windll.user32
        except Exception:
            logger.warning("[ScreenDaemon] user32 not available — screen awareness disabled")
            return

        self._thread = threading.Thread(
            target=self._watcher_loop,
            daemon=True,
            name="screen-daemon",
        )
        self._thread.start()
        logger.info("[ScreenDaemon]  Event-Driven Screen Awareness started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    @property
    def current(self) -> ScreenContext:
        with self._lock:
            return self._current
        
    @property
    def ui_map(self) -> Dict[int, Dict]:
        """Return a copy of the current UI element map."""
        with self._lock:
            return dict(self._ui_map)

    def get_text(self) -> str:
        """Convenience: return current screen text."""
        return self.current.screen_text

    def get_summary(self) -> str:
        """Return a short summary for LLM injection."""
        ctx = self.current
        if not ctx.has_content:
            return ""
        window = ctx.active_window[:60] if ctx.active_window else "unknown"
        text   = ctx.screen_text[:300]
        return f"[Screen: {window}] {text}"

    def inject_into_session(self, session_memory) -> bool:
        """
        Inject current screen context into session memory's page context
        so follow-up questions about screen content work.
        Returns True if content was injected.
        """
        ctx = self.current
        if not ctx.has_content:
            return False
        if ctx.age_seconds > 30:
            return False
        try:
            session_memory.set_page_context(
                text=ctx.screen_text,
                url="",
                title=ctx.active_window,
            )
            return True
        except Exception as e:
            logger.debug(f"[ScreenDaemon] session inject failed: {e}")
            return False

    # ── INTERNAL ──────────────────────────────────────────────────────────

    def _watcher_loop(self):
        while self._running:
            try:
                hwnd = self._user32.GetForegroundWindow()
                if hwnd and hwnd != self._last_hwnd:
                    self._last_hwnd = hwnd
                    self._process_window(hwnd)
            except Exception:
                pass
            time.sleep(0.05)  # 50ms — imperceptible to user, ~0% CPU

    def _process_window(self, hwnd: int):
        """Extract context with time-bounded deep UIA traversal + full rect storage."""
        try:
            import uiautomation as auto
            auto.SetGlobalSearchTimeout(0.15)
            
            window = auto.ControlFromHandle(hwnd)
            if not window:
                return
            
            title = window.Name or ""
            app_class = window.ClassName or ""
            
            # Skip blacklisted app classes
            if app_class in _UIA_BLACKLIST_CLASSES:
                return
            if any(bl in title.lower() for bl in _UIA_BLACKLIST_TITLES):
                return
            
            # ── TIME-BOUNDED DEEP UI ELEMENT TAGGING ──────────────────────
            texts = []
            ui_map = {}
            element_id = 1
            
            import time as _time_module
            start_time = _time_module.perf_counter()
            MAX_WALK_TIME = 0.15  # STRICT 150ms budget — never freeze the daemon
            
            # Increased maxDepth=12 for Electron/Chromium apps (Discord, Spotify, VS Code)
            # but TIME-BOUNDED so it bails out before freezing
            for control, depth in auto.WalkControl(window, maxDepth=12):
                if _time_module.perf_counter() - start_time > MAX_WALK_TIME:
                    break  # Bail out if tree is too massive
                
                if control.ControlType in {
                    auto.ControlType.ButtonControl,
                    auto.ControlType.HyperlinkControl,
                    auto.ControlType.ListItemControl,
                    auto.ControlType.EditControl,
                    auto.ControlType.TabItemControl,
                    auto.ControlType.MenuItemControl,
                    auto.ControlType.RadioButtonControl,
                    auto.ControlType.CheckBoxControl,
                } and control.Name:
                    
                    clean_name = control.Name.strip()
                    if 1 < len(clean_name) < 100:
                        rect = control.BoundingRectangle
                        if rect.width() > 0 and rect.height() > 0:
                            # Store FULL bounding rect — not just center
                            # This enables DPI-safe native clicking
                            ui_map[element_id] = {
                                "name": clean_name,
                                "left": rect.left,
                                "top": rect.top,
                                "right": rect.right,
                                "bottom": rect.bottom,
                                "type": control.ControlTypeName
                            }
                            texts.append(f"[{element_id}] {clean_name}")
                            element_id += 1
            
            # Also capture static text for context (what page is visible)
            for control, depth in auto.WalkControl(window, maxDepth=2):
                if _time_module.perf_counter() - start_time > MAX_WALK_TIME:
                    break
                if depth > 2:
                    break
                if control.ControlType in {
                    auto.ControlType.TextControl,
                    auto.ControlType.StaticControl,
                } and control.Name:
                    name = control.Name.strip()
                    if len(name) > 3 and name.lower() not in _UI_NOISE_SET:
                        texts.append(name[:80])
            
            screen_text = " | ".join(texts)[:2000] if texts else ""
            
            ctx = ScreenContext(
                active_window=title,
                active_app_class=app_class,
                screen_text=screen_text,
                source="uia_tagged",
            )
            
            with self._lock:
                self._current = ctx
                self._ui_map = ui_map
            
            if ctx.has_content and self._on_change:
                try:
                    self._on_change(ctx)
                except Exception as e:
                    logger.debug(f"[ScreenDaemon] on_change callback error: {e}")
            
            if ctx.has_content and self.context_updater:
                try:
                    self.context_updater({
                        "active_window": ctx.active_window,
                        "screen_text": ctx.screen_text,
                        "source": ctx.source,
                        "ui_map": ui_map
                    })
                except Exception as e:
                    logger.debug(f"[ScreenDaemon] context_updater error: {e}")
            
            logger.debug(
                f"[ScreenDaemon] Window: '{ctx.active_window[:50]}' | "
                f"Elements: {len(ui_map)} tagged in {(_time_module.perf_counter()-start_time)*1000:.0f}ms"
            )
            
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"[ScreenDaemon] Process window error: {e}")


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ════════════════════════════════════════════════════════════════════════════

screen_daemon = EventDrivenScreenDaemon()
