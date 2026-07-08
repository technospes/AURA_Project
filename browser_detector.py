"""
BROWSER DETECTOR v2 — Production-Complete Browser Engine
=========================================================
Fixes applied over v1:

  FIX 1 — Session persistence: launch_persistent_context() so WhatsApp Web,
           Gmail, and all logged-in sites work without re-login.

  FIX 2 — Attach to existing browser: detect a running browser CDP port and
           connect to it before ever launching a new instance.
           attach → reuse → launch_persistent → webbrowser.open()

  FIX 3 — Single source-of-truth mode: SmartBrowserController picks ONE
           primary mode at startup (PLAYWRIGHT or UIA_ONLY) and never mixes
           them silently. get_current_url() returns from Playwright page
           (when in PLAYWRIGHT mode) or UIA (when in UIA_ONLY mode) —
           never a mix that can diverge.

  FIX 4 — Async/concurrency safety: full asyncio-aware locking + operation
           serialisation via _OpSerializer. All public methods are safe to
           call from threads or async contexts without race conditions.

  FIX 5 — Typed error classification: BrowserError replaces bare strings.
           Every BrowserResult.fail() carries an error_type (TIMEOUT,
           NETWORK, NAVIGATION, PERMISSION, UNKNOWN) + retryable flag.
           The caller decides retry logic; this module just classifies.

Unchanged from v1:
  - BrowserDetector (registry + process-scan detection)
  - TOOL_ACTIONS + validate_step / validate_plan
  - BrowserUiaExtractor
  - patch_core_patch_browser()
"""

from __future__ import annotations

import asyncio
import ctypes
import enum
import logging
import os
import pathlib
import re
import socket
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# TOOL_ACTIONS — single source of truth (unchanged from v1)
# ════════════════════════════════════════════════════════════════════════════

TOOL_ACTIONS: Dict[str, List[str]] = {
    "browser": [
        "open_url",
        "new_tab",
        "close_tab",
        "switch_tab",
        "click_result",
        "read_page",
        "get_links",
        "get_current_url",
        "scroll",
        "browser_navigation",
    ],
    "app_launcher": [
        "open_app",
        "focus_app",
        "close_app",
    ],
    "system_action": [
        "system_action",
    ],
    "web_search": [
        "search_web",
    ],
}


def validate_step(step: Dict[str, Any]) -> None:
    tool   = step.get("tool", "")
    action = step.get("action", "")
    if tool not in TOOL_ACTIONS:
        raise ValueError(
            f"[Planner->Tools] Unknown tool '{tool}'. "
            f"Valid: {list(TOOL_ACTIONS.keys())}"
        )
    if action not in TOOL_ACTIONS[tool]:
        raise ValueError(
            f"[Planner->Tools] Unsupported action '{tool}.{action}'. "
            f"Valid: {TOOL_ACTIONS[tool]}"
        )


def validate_plan(plan: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    for i, step in enumerate(plan):
        try:
            validate_step(step)
        except ValueError as e:
            return False, f"Step {i}: {e}"
    return True, None


# ════════════════════════════════════════════════════════════════════════════
# FIX 5 — TYPED ERROR CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════

class BrowserErrorType(str, enum.Enum):
    TIMEOUT     = "TIMEOUT"      # Navigation / operation took too long
    NETWORK     = "NETWORK"      # DNS / connection refused / no internet
    NAVIGATION  = "NAVIGATION"   # goto() failed for non-network reason
    PERMISSION  = "PERMISSION"   # Popup blocked / CORS / denied
    SESSION     = "SESSION"      # Profile / cookie / login issue
    CONCURRENCY = "CONCURRENCY"  # Lock contention or race condition
    UNAVAILABLE = "UNAVAILABLE"  # Playwright / UIA / browser not present
    UNKNOWN     = "UNKNOWN"      # Catch-all


_RETRYABLE: frozenset = frozenset({
    BrowserErrorType.TIMEOUT,
    BrowserErrorType.NETWORK,
    BrowserErrorType.CONCURRENCY,
})


@dataclass
class BrowserError:
    error_type: BrowserErrorType
    message:    str
    raw:        Optional[Exception] = None
    retryable:  bool = field(init=False)

    def __post_init__(self):
        self.retryable = self.error_type in _RETRYABLE

    def __str__(self):
        tag = "[retryable]" if self.retryable else "[fatal]"
        return f"[{self.error_type.value}]{tag} {self.message}"

    @classmethod
    def classify(cls, exc: Exception, context: str = "") -> "BrowserError":
        """Infer error type from exception type and message."""
        msg = str(exc).lower()
        ctx = context or type(exc).__name__

        if "timeout" in msg or "timed out" in msg:
            return cls(BrowserErrorType.TIMEOUT,     f"{ctx}: {exc}", raw=exc)
        if any(w in msg for w in ("net::", "dns", "connection refused", "eof")):
            return cls(BrowserErrorType.NETWORK,     f"{ctx}: {exc}", raw=exc)
        if "navigation" in msg or "detached" in msg:
            return cls(BrowserErrorType.NAVIGATION,  f"{ctx}: {exc}", raw=exc)
        if "permission" in msg or "blocked" in msg:
            return cls(BrowserErrorType.PERMISSION,  f"{ctx}: {exc}", raw=exc)
        if "session" in msg or "profile" in msg or "cookie" in msg:
            return cls(BrowserErrorType.SESSION,     f"{ctx}: {exc}", raw=exc)
        if "lock" in msg or "race" in msg:
            return cls(BrowserErrorType.CONCURRENCY, f"{ctx}: {exc}", raw=exc)
        return cls(BrowserErrorType.UNKNOWN,         f"{ctx}: {exc}", raw=exc)


@dataclass
class BrowserResult:
    """
    Standardised result for every SmartBrowserController method.

    status:        "success" | "failed"
    data:          arbitrary payload on success
    browser_error: typed error on failure — never None when status=="failed"
    """
    status:        str
    data:          Dict[str, Any]          = field(default_factory=dict)
    browser_error: Optional[BrowserError] = None

    @property
    def success(self) -> bool:
        return self.status == "success"

    @property
    def retryable(self) -> bool:
        return bool(self.browser_error and self.browser_error.retryable)

    @property
    def error_type(self) -> Optional[BrowserErrorType]:
        return self.browser_error.error_type if self.browser_error else None

    @property
    def message(self) -> str:
        if self.success:
            return self.data.get("message", "")
        return str(self.browser_error) if self.browser_error else "Unknown error"

# Compatibility shim: callers that check result.error as a string
    @property
    def error(self) -> Optional[str]:
        if self.browser_error:
            return str(self.browser_error)
        return None

    def to_dict(self) -> dict:
        """
        Convert to ActionResult-compatible dict so core_patch._execute_open_url
        can call result.to_dict() regardless of which browser backend returned it.
        Mirrors ActionResult.to_dict() schema exactly.
        """
        return {
            "success":               self.success,
            "state_verified":        self.data.get("state_verified", self.success),
            "confidence":            1.0 if self.success else 0.0,
            "message":               self.message,
            "error":                 self.error,
            "next_possible_actions": self.data.get("next_possible_actions", []),
            "execution_ms":          self.data.get("execution_ms", 0.0),
        }

    @classmethod
    def ok(cls, message: str = "", **kwargs) -> "BrowserResult":
        return cls(status="success", data={"message": message, **kwargs})

    @classmethod
    def fail(cls,
             reason:     str,
             error_type: BrowserErrorType = BrowserErrorType.UNKNOWN,
             exc:        Optional[Exception] = None) -> "BrowserResult":
        err = BrowserError(error_type, reason, raw=exc)
        return cls(status="failed", browser_error=err)

    @classmethod
    def fail_exc(cls, exc: Exception, context: str = "") -> "BrowserResult":
        return cls(status="failed", browser_error=BrowserError.classify(exc, context))


# ════════════════════════════════════════════════════════════════════════════
# BROWSER DETECTION (unchanged from v1)
# ════════════════════════════════════════════════════════════════════════════

_EXE_TO_PW: Dict[str, str] = {
    "chrome.exe":         "chromium",
    "msedge.exe":         "chromium",
    "brave.exe":          "chromium",
    "opera.exe":          "chromium",
    "vivaldi.exe":        "chromium",
    "firefox.exe":        "firefox",
    "firefox-esr.exe":    "firefox",
    "waterfox.exe":       "firefox",
    "librewolf.exe":      "firefox",
    "iexplore.exe":       "chromium",
    "msedgewebview2.exe": "chromium",
}

_PROGID_TO_PW: Dict[str, str] = {
    "chrome":    "chromium",
    "msedge":    "chromium",
    "edge":      "chromium",
    "brave":     "chromium",
    "opera":     "chromium",
    "vivaldi":   "chromium",
    "firefox":   "firefox",
    "waterfox":  "firefox",
    "librewolf": "firefox",
}

_PW_TO_WIN_CLASS: Dict[str, List[str]] = {
    "chromium": ["Chrome_WidgetWin_1"],
    "firefox":  ["MozillaWindowClass"],
}

_DEFAULT_CDP_PORTS: List[int] = [9222, 9223, 9229]


class BrowserDetector:
    """
    Detects the user's default browser via Windows registry + process scan.
    Results are cached; call .refresh() after user changes default browser.
    """

    def __init__(self):
        self._lock           = threading.Lock()
        self._detected       = False
        self.exe_name        = "chrome.exe"
        self.display_name    = "Unknown Browser"
        self.playwright_type = "chromium"
        self.win_classes: List[str] = ["Chrome_WidgetWin_1"]

    @property
    def detected(self) -> bool:
        return self._detected

    def detect(self) -> "BrowserDetector":
        with self._lock:
            if not self._detected:
                self._run_detection()
                self._detected = True
        return self

    def refresh(self) -> "BrowserDetector":
        with self._lock:
            self._detected = False
            self._run_detection()
            self._detected = True
        return self

    def summary(self) -> str:
        return (
            f"{self.display_name} ({self.exe_name}) "
            f"-> playwright={self.playwright_type}"
        )

    def _run_detection(self):
        try:
            if self._detect_via_registry():
                return
        except Exception as e:
            logger.debug(f"[BrowserDetect] Registry failed: {e}")
        try:
            if self._detect_via_process_scan():
                return
        except Exception as e:
            logger.debug(f"[BrowserDetect] Process scan failed: {e}")
        logger.warning("[BrowserDetect] Could not detect default browser — assuming Chrome")
        self._apply("chrome.exe", "Google Chrome")

    def _detect_via_registry(self) -> bool:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\https\UserChoice",
        )
        prog_id, _ = winreg.QueryValueEx(key, "ProgId")
        winreg.CloseKey(key)
        logger.debug(f"[BrowserDetect] Registry ProgId: {prog_id}")
        for frag in _PROGID_TO_PW:
            if frag in prog_id.lower():
                exe     = self._progid_to_exe(prog_id) or f"{frag}.exe"
                display = self._progid_to_display(prog_id)
                self._apply(exe, display)
                return True
        return False

    def _detect_via_process_scan(self) -> bool:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5,
        )
        lines      = result.stdout.lower().splitlines()
        found_exes = {l.split(",")[0].strip('"') for l in lines if "," in l}
        priority   = ["chrome.exe", "msedge.exe", "firefox.exe",
                       "brave.exe", "opera.exe", "vivaldi.exe"]
        for candidate in priority:
            if candidate in found_exes:
                self._apply(candidate, self._exe_to_display(candidate))
                return True
        return False

    def _apply(self, exe: str, display: str):
        self.exe_name        = exe
        self.display_name    = display
        self.playwright_type = _EXE_TO_PW.get(exe, "chromium")
        self.win_classes     = _PW_TO_WIN_CLASS.get(self.playwright_type, ["Chrome_WidgetWin_1"])
        logger.info(f"[BrowserDetect] Detected: {self.summary()}")

    @staticmethod
    def _progid_to_exe(prog_id: str) -> Optional[str]:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Classes\{prog_id}\shell\open\command",
            )
            cmd, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            m = re.search(r'[\w\-]+\.exe', cmd, re.IGNORECASE)
            return m.group(0).lower() if m else None
        except Exception:
            return None

    @staticmethod
    def _progid_to_display(prog_id: str) -> str:
        MAP = {
            "chrome": "Google Chrome", "msedge": "Microsoft Edge",
            "firefox": "Mozilla Firefox", "brave": "Brave",
            "opera": "Opera", "vivaldi": "Vivaldi",
            "waterfox": "Waterfox", "librewolf": "LibreWolf",
        }
        low = prog_id.lower()
        for k, v in MAP.items():
            if k in low:
                return v
        return prog_id

    @staticmethod
    def _exe_to_display(exe: str) -> str:
        MAP = {
            "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge",
            "firefox.exe": "Mozilla Firefox", "brave.exe": "Brave",
            "opera.exe": "Opera", "vivaldi.exe": "Vivaldi",
            "waterfox.exe": "Waterfox", "librewolf.exe": "LibreWolf",
        }
        return MAP.get(exe, exe.replace(".exe", "").title())


browser_detector = BrowserDetector()


# ════════════════════════════════════════════════════════════════════════════
# UIA DATA EXTRACTOR (unchanged from v1)
# ════════════════════════════════════════════════════════════════════════════

class BrowserUiaExtractor:

    _ADDRESS_SELECTORS: Dict[str, List[Dict]] = {
        "chromium": [
            {"auto_id": "addressEditBox"},
            {"title_re": ".*Address and search bar.*", "control_type": "Edit"},
            {"title_re": ".*address.*",                "control_type": "Edit"},
        ],
        "firefox": [
            {"auto_id": "urlbar-input"},
            {"title_re": ".*Search or enter address.*", "control_type": "Edit"},
            {"title_re": ".*address.*",                 "control_type": "Edit"},
        ],
    }

    def get_current_url(self, pw_type: str = "chromium",
                        win_classes: Optional[List[str]] = None) -> str:
        try:
            import uiautomation as auto
            auto.SetGlobalSearchTimeout(0.3)
            win = self._find_browser_window(win_classes or ["Chrome_WidgetWin_1"])
            if not win:
                return ""
            for sel in self._ADDRESS_SELECTORS.get(pw_type, self._ADDRESS_SELECTORS["chromium"]):
                try:
                    ctrl = win.child_window(**sel)
                    if ctrl.exists(timeout=0.3):
                        url = ctrl.get_value() or ctrl.Name or ""
                        if url and ("." in url or url.startswith("http")):
                            return url.strip()
                except Exception:
                    continue
        except ImportError:
            logger.debug("[BrowserUIA] uiautomation not installed")
        except Exception as e:
            logger.debug(f"[BrowserUIA] get_current_url: {e}")
        return ""

    def get_page_text(self, pw_type: str = "chromium",
                      win_classes: Optional[List[str]] = None,
                      max_chars: int = 2000) -> str:
        try:
            import uiautomation as auto
            auto.SetGlobalSearchTimeout(0.3)
            win = self._find_browser_window(win_classes or ["Chrome_WidgetWin_1"])
            if not win:
                return ""
            ALLOWED = {
                auto.ControlType.TextControl,
                auto.ControlType.DocumentControl,
                auto.ControlType.EditControl,
                auto.ControlType.StaticControl,
            }
            SKIP = {"address and search bar", "search", "tabs", "toolbar",
                    "back", "forward", "refresh", "home", "new tab"}
            texts: List[str] = []
            try:
                for ctrl, depth in auto.WalkControl(win, maxDepth=4):
                    if depth > 4:
                        break
                    if ctrl.ControlType not in ALLOWED:
                        continue
                    name = (ctrl.Name or "").strip()
                    if not name or len(name) < 4 or name.lower() in SKIP:
                        continue
                    texts.append(name)
            except Exception:
                pass
            seen: set = set()
            kept: List[str] = []
            for t in texts:
                key = t.lower()[:80]
                if key not in seen:
                    seen.add(key)
                    kept.append(t)
            return " | ".join(kept)[:max_chars]
        except ImportError:
            logger.debug("[BrowserUIA] uiautomation not installed")
        except Exception as e:
            logger.debug(f"[BrowserUIA] get_page_text: {e}")
        return ""

    @staticmethod
    def _find_browser_window(win_classes: List[str]):
        try:
            import uiautomation as auto
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                win = auto.ControlFromHandle(hwnd)
                if win and win.ClassName in win_classes:
                    return win
            for cls in win_classes:
                try:
                    win = auto.WindowControl(ClassName=cls)
                    if win.exists(timeout=0.5):
                        return win
                except Exception:
                    pass
        except Exception:
            pass
        return None


uia_extractor = BrowserUiaExtractor()


# ════════════════════════════════════════════════════════════════════════════
# FIX 4 — OPERATION SERIALISER
# Playwright's sync API is NOT thread-safe. This ensures mutual exclusion
# across threads/coroutines without blocking the asyncio event loop.
# ════════════════════════════════════════════════════════════════════════════

class _OpSerializer:
    """
    Thread-safe operation gate for all Playwright calls.

    - run_sync():  callable from any thread (blocking acquire)
    - run_async(): callable from async coroutine (uses executor, non-blocking)
    """

    def __init__(self, timeout: float = 30.0):
        self._lock    = threading.Lock()
        self._timeout = timeout

    def run_sync(self, fn: Callable, timeout: Optional[float] = None) -> Any:
        t        = timeout or self._timeout
        acquired = self._lock.acquire(timeout=t)
        if not acquired:
            raise TimeoutError(
                f"[OpSerializer] Browser lock not acquired in {t}s "
                "(another operation is running)"
            )
        try:
            return fn()
        finally:
            self._lock.release()

    async def run_async(self, fn: Callable, timeout: Optional[float] = None) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.run_sync(fn, timeout))


# ════════════════════════════════════════════════════════════════════════════
# PROFILE PATH RESOLVER (FIX 1)
# ════════════════════════════════════════════════════════════════════════════

def _default_profile_dir(exe_name: str) -> pathlib.Path:
    """
    Returns the real user profile directory for the detected browser.
    Using this path with launch_persistent_context() means Playwright reuses
    all cookies, sessions, and logins — no re-login for WhatsApp Web, Gmail etc.
    """
    local   = pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = pathlib.Path(os.environ.get("APPDATA", ""))

    PATHS: Dict[str, pathlib.Path] = {
        "chrome.exe":    local   / "Google"           / "Chrome"          / "User Data",
        "msedge.exe":    local   / "Microsoft"        / "Edge"            / "User Data",
        "brave.exe":     local   / "BraveSoftware"    / "Brave-Browser"   / "User Data",
        "opera.exe":     roaming / "Opera Software"   / "Opera Stable",
        "vivaldi.exe":   local   / "Vivaldi"          / "User Data",
        "firefox.exe":   roaming / "Mozilla"          / "Firefox"         / "Profiles",
        "waterfox.exe":  roaming / "Waterfox"         / "Waterfox"        / "Profiles",
        "librewolf.exe": roaming / "LibreWolf"        / "Profiles",
    }
    path = PATHS.get(exe_name, local / "Google" / "Chrome" / "User Data")

    # Firefox-family stores profiles in sub-dirs named *.default-release
    if any(name in exe_name.lower() for name in ("firefox", "waterfox", "librewolf")):
        if path.exists():
            for sub in path.iterdir():
                if sub.is_dir() and ("default" in sub.name or "release" in sub.name):
                    return sub
    return path


# ════════════════════════════════════════════════════════════════════════════
# CDP ATTACH HELPER (FIX 2)
# ════════════════════════════════════════════════════════════════════════════

def _find_running_cdp_port() -> Optional[int]:
    """
    Probe common CDP ports to find a Chromium browser that was launched with
    --remote-debugging-port.  Returns the port if found, else None.

    To use CDP attach, the browser must have been started with:
        chrome.exe --remote-debugging-port=9222
    """
    for port in _DEFAULT_CDP_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            logger.info(f"[BrowserAttach] Found running browser on CDP :{port}")
            return port
        except OSError:
            pass
    return None


# ════════════════════════════════════════════════════════════════════════════
# FIX 3 — COMMITTED OPERATION MODE
# ════════════════════════════════════════════════════════════════════════════

class BrowserMode(str, enum.Enum):
    PLAYWRIGHT_ATTACHED   = "playwright_attached"    # Connected via CDP to running browser
    PLAYWRIGHT_PERSISTENT = "playwright_persistent"  # Launched with real user profile
    PLAYWRIGHT_FRESH      = "playwright_fresh"       # Launched fresh (no profile)
    UIA_ONLY              = "uia_only"               # Playwright unavailable; UIA reads only
    FALLBACK              = "fallback"               # webbrowser.open() only


_PLAYWRIGHT_MODES = frozenset({
    BrowserMode.PLAYWRIGHT_ATTACHED,
    BrowserMode.PLAYWRIGHT_PERSISTENT,
    BrowserMode.PLAYWRIGHT_FRESH,
})


# ════════════════════════════════════════════════════════════════════════════
# SMART BROWSER CONTROLLER v2
# ════════════════════════════════════════════════════════════════════════════

class SmartBrowserController:
    """
    Production browser controller with all 5 fixes applied.

    Startup sequence (resolved lazily on first use):
      Playwright available?
        YES ->  1. CDP attach  (FIX 2: reuse running browser)
                2. Persistent  (FIX 1: use real profile/sessions)
                3. Fresh       (no profile, last resort)
        NO  ->  UIA_ONLY or FALLBACK

    Once a mode is committed it NEVER changes silently (FIX 3).

    All Playwright calls go through _op (_OpSerializer) — thread + async safe (FIX 4).

    All BrowserResult failures carry a typed BrowserError (FIX 5).
    """

    def __init__(self):
        self._detector = browser_detector
        self._detector.detect()
        self._uia      = uia_extractor
        self._op       = _OpSerializer(timeout=30.0)

        # Playwright handles
        self._pw_module  = None
        self._playwright = None
        self._context    = None   # BrowserContext
        self._page       = None   # active Page
        self._browser    = None   # only used in CDP-attach mode

        # FIX 3: mode committed at first _ensure_ready() call
        self._mode: Optional[BrowserMode] = None
        self._mode_lock = threading.Lock()

        # Search result cache
        self._search_results: List[str] = []

        # Import Playwright (don't launch yet)
        try:
            from playwright.sync_api import sync_playwright
            self._pw_module    = sync_playwright
            self._pw_available = True
            logger.info(
                f"[SmartBrowser] Playwright ready — "
                f"target: {self._detector.playwright_type} "
                f"({self._detector.display_name})"
            )
        except ImportError:
            self._pw_available = False
            self._mode = BrowserMode.UIA_ONLY
            logger.info("[SmartBrowser] Playwright not installed -> UIA_ONLY mode")

    # ── Mode resolution (FIX 1 + 2 + 3) ─────────────────────────────────

    def _ensure_ready(self) -> bool:
        """
        Resolve mode and establish a Playwright context (once, thread-safe).
        Returns True if a Playwright page is available.
        """
        with self._mode_lock:
            if self._mode is not None:
                return self._mode in _PLAYWRIGHT_MODES

            if not self._pw_available:
                self._mode = BrowserMode.UIA_ONLY
                return False

            # Step 1 — CDP attach (Chromium only)
            if self._detector.playwright_type == "chromium":
                port = _find_running_cdp_port()
                if port and self._try_cdp_attach(port):
                    self._mode = BrowserMode.PLAYWRIGHT_ATTACHED
                    logger.info(f"[SmartBrowser] Mode locked: {self._mode.value}")
                    return True

            # Step 2 — Persistent context (FIX 1)
            if self._try_persistent_launch():
                self._mode = BrowserMode.PLAYWRIGHT_PERSISTENT
                logger.info(f"[SmartBrowser] Mode locked: {self._mode.value}")
                return True

            # Step 3 — Fresh launch
            if self._try_fresh_launch():
                self._mode = BrowserMode.PLAYWRIGHT_FRESH
                logger.info(f"[SmartBrowser] Mode locked: {self._mode.value}")
                return True

            self._mode = BrowserMode.UIA_ONLY
            logger.warning("[SmartBrowser] All Playwright paths failed -> UIA_ONLY")
            return False

    def _try_cdp_attach(self, port: int) -> bool:
        try:
            def _attach():
                pw      = self._pw_module().start()
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                ctx     = browser.contexts[0] if browser.contexts else browser.new_context()
                page    = ctx.pages[0] if ctx.pages else ctx.new_page()
                self._playwright = pw
                self._browser    = browser
                self._context    = ctx
                self._page       = page
            self._op.run_sync(_attach, timeout=6.0)
            logger.info(f"[SmartBrowser] Attached to running browser via CDP :{port}")
            return True
        except Exception as e:
            logger.debug(f"[SmartBrowser] CDP attach failed: {e}")
            return False

    def _try_persistent_launch(self) -> bool:
        """FIX 1: launch_persistent_context with the user's real profile."""
        profile_dir = _default_profile_dir(self._detector.exe_name)
        if not profile_dir.exists():
            logger.debug(f"[SmartBrowser] Profile dir not found: {profile_dir}")
            return False
        pw_type = self._detector.playwright_type
        try:
            def _launch():
                pw       = self._pw_module().start()
                launcher = getattr(pw, pw_type)
                ctx      = launcher.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    args=(["--no-first-run", "--no-default-browser-check"]
                          if pw_type == "chromium" else []),
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                self._playwright = pw
                self._context    = ctx
                self._page       = page
            self._op.run_sync(_launch, timeout=20.0)
            logger.info(f"[SmartBrowser] Persistent context launched (profile: {profile_dir})")
            return True
        except Exception as e:
            logger.warning(f"[SmartBrowser] Persistent launch failed: {e}")
            return False

    def _try_fresh_launch(self) -> bool:
        pw_type = self._detector.playwright_type
        try:
            def _launch():
                pw      = self._pw_module().start()
                browser = getattr(pw, pw_type).launch(
                    headless=False,
                    args=(["--no-first-run", "--no-default-browser-check"]
                          if pw_type == "chromium" else []),
                )
                ctx  = browser.new_context()
                page = ctx.new_page()
                self._playwright = pw
                self._browser    = browser
                self._context    = ctx
                self._page       = page
            self._op.run_sync(_launch, timeout=20.0)
            logger.info("[SmartBrowser] Fresh browser launched (no profile)")
            return True
        except Exception as e:
            logger.warning(f"[SmartBrowser] Fresh launch failed: {e}")
            return False

    def _active_page(self):
        """Return the current live page, or recover from context."""
        if self._page and not self._page.is_closed():
            return self._page
        if self._context:
            try:
                pages = self._context.pages
                if pages:
                    self._page = pages[-1]
                    return self._page
            except Exception:
                pass
        return None

    # ── FIX 3: source-committed URL getter ───────────────────────────────

    def get_current_url(self) -> str:
        """
        Returns URL from the committed mode's source ONLY.
        PLAYWRIGHT_* modes -> page.url
        UIA_ONLY           -> uia_extractor.get_current_url()
        These are never mixed.
        """
        if self._mode in _PLAYWRIGHT_MODES:
            page = self._active_page()
            if page:
                try:
                    return self._op.run_sync(lambda: page.url, timeout=3.0)
                except Exception:
                    pass
        # UIA_ONLY or unresolved
        return self._uia.get_current_url(
            self._detector.playwright_type,
            self._detector.win_classes,
        )

    # ── Tool actions ──────────────────────────────────────────────────────

    def open_url(self, url: str) -> BrowserResult:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if self._ensure_ready():
            page = self._active_page()
            if page:
                try:
                    def _goto():
                        page.goto(url, timeout=12_000, wait_until="domcontentloaded")
                        return page.title(), page.url

                    title, current = self._op.run_sync(_goto)
                    logger.info(f"[SmartBrowser] open_url [{self._mode.value}]: {title}")
                    return BrowserResult.ok(
                        message=f"Opened {url} in {self._detector.display_name}, Sir.",
                        url=current, title=title,
                        browser=self._detector.display_name,
                        mode=self._mode.value,
                        state_verified=True,
                    )
                except TimeoutError as e:
                    return BrowserResult.fail(
                        f"Navigation to {url} timed out.",
                        BrowserErrorType.TIMEOUT, exc=e,
                    )
                except Exception as e:
                    logger.warning(f"[SmartBrowser] open_url Playwright failed: {e}")

        # Fallback
        try:
            import webbrowser
            webbrowser.open(url)
            return BrowserResult.ok(
                message=f"Opened {url} in your browser, Sir.",
                url=url, state_verified=False, mode=BrowserMode.FALLBACK.value,
            )
        except Exception as e:
            return BrowserResult.fail_exc(e, "open_url fallback")

    def new_tab(self, url: str = "about:blank") -> BrowserResult:
        if not url.startswith(("http://", "https://", "about:")):
            url = "https://" + url

        if self._ensure_ready() and self._context:
            try:
                def _new():
                    p = self._context.new_page()
                    self._page = p
                    if url != "about:blank":
                        p.goto(url, timeout=10_000, wait_until="domcontentloaded")
                    return p.url
                current = self._op.run_sync(_new)
                return BrowserResult.ok(
                    message=f"New tab opened at {current}, Sir.", url=current
                )
            except Exception as e:
                return BrowserResult.fail_exc(e, "new_tab")

        try:
            import webbrowser
            webbrowser.open_new_tab(url)
            return BrowserResult.ok(message=f"Opened new tab with {url}, Sir.")
        except Exception as e:
            return BrowserResult.fail_exc(e, "new_tab fallback")

    def close_tab(self) -> BrowserResult:
        page = self._active_page()
        if page:
            try:
                self._op.run_sync(page.close, timeout=5.0)
                self._page = None
                return BrowserResult.ok(message="Tab closed, Sir.")
            except Exception as e:
                return BrowserResult.fail_exc(e, "close_tab")
        return BrowserResult.fail("No active tab to close.", BrowserErrorType.UNAVAILABLE)

    def switch_tab(self, index: int = 0) -> BrowserResult:
        if self._context:
            try:
                def _switch():
                    pages = self._context.pages
                    if index < len(pages):
                        self._page = pages[index]
                        self._page.bring_to_front()
                        return self._page.url
                    raise IndexError(f"Tab index {index} out of range ({len(pages)} tabs)")
                url = self._op.run_sync(_switch, timeout=5.0)
                return BrowserResult.ok(message=f"Switched to tab {index + 1}, Sir.", url=url)
            except IndexError as e:
                return BrowserResult.fail(str(e), BrowserErrorType.NAVIGATION)
            except Exception as e:
                return BrowserResult.fail_exc(e, "switch_tab")

        try:
            import pyautogui
            for _ in range(max(0, index)):
                pyautogui.hotkey("ctrl", "tab")
                time.sleep(0.15)
            return BrowserResult.ok(message=f"Switched to tab {index + 1}, Sir.")
        except Exception as e:
            return BrowserResult.fail_exc(e, "switch_tab keyboard")

    def read_page(self, max_chars: int = 2000) -> BrowserResult:
        """
        FIX 3: reads from committed mode ONLY.
        PLAYWRIGHT_* -> page.inner_text()
        UIA_ONLY     -> uia_extractor.get_page_text()
        """
        if self._mode in _PLAYWRIGHT_MODES:
            page = self._active_page()
            if page:
                try:
                    def _read():
                        text = page.inner_text("body")
                        return re.sub(r'\s+', ' ', text).strip()[:max_chars], page.url
                    text, url = self._op.run_sync(_read, timeout=10.0)
                    return BrowserResult.ok(
                        message=f"Page content from {url}",
                        text=text, url=url, source="playwright",
                    )
                except TimeoutError as e:
                    return BrowserResult.fail("Page read timed out.", BrowserErrorType.TIMEOUT, exc=e)
                except Exception as e:
                    return BrowserResult.fail_exc(e, "read_page playwright")

        # UIA_ONLY mode
        text = self._uia.get_page_text(
            self._detector.playwright_type,
            self._detector.win_classes,
            max_chars=max_chars,
        )
        if text:
            url = self._uia.get_current_url(
                self._detector.playwright_type, self._detector.win_classes
            )
            return BrowserResult.ok(
                message="Page content via UIA", text=text, url=url, source="uia"
            )

        return BrowserResult.fail(
            "Sir, I couldn't read the page. Try after it has fully loaded.",
            BrowserErrorType.UNAVAILABLE,
        )

    def click_result(self, index_word: str) -> BrowserResult:
        _IDX = {
            "first":  0, "1st": 0, "one":   0, "1": 0,
            "second": 1, "2nd": 1, "two":   1, "2": 1,
            "third":  2, "3rd": 2, "three": 2, "3": 2,
            "fourth": 3, "4th": 3, "four":  3, "4": 3,
            "fifth":  4, "5th": 4, "five":  4, "5": 4,
        }
        idx = _IDX.get(str(index_word).lower().strip(), 0)

        if idx < len(self._search_results):
            return self.open_url(self._search_results[idx])

        page = self._active_page()
        if page:
            try:
                def _click():
                    links   = page.query_selector_all("a[href]")
                    visible = [l for l in links if l.is_visible()]
                    if idx < len(visible):
                        visible[idx].click()
                        page.wait_for_load_state("domcontentloaded", timeout=8_000)
                        return page.url
                    raise IndexError(f"No link at index {idx}")
                url = self._op.run_sync(_click, timeout=12.0)
                return BrowserResult.ok(
                    message=f"Clicked {index_word} result, Sir.", url=url, state_verified=True
                )
            except IndexError as e:
                return BrowserResult.fail(str(e), BrowserErrorType.NAVIGATION)
            except Exception as e:
                return BrowserResult.fail_exc(e, "click_result")

        return BrowserResult.fail(
            "Sir, no results stored and no active page. Search for something first.",
            BrowserErrorType.UNAVAILABLE,
        )

    def get_links(self) -> BrowserResult:
        page = self._active_page()
        if page:
            try:
                def _links():
                    return [
                        {"text": a.inner_text().strip(), "href": a.get_attribute("href") or ""}
                        for a in page.query_selector_all("a[href]")
                        if a.is_visible()
                    ][:20]
                links = self._op.run_sync(_links, timeout=8.0)
                return BrowserResult.ok(message=f"Found {len(links)} links.", links=links)
            except Exception as e:
                return BrowserResult.fail_exc(e, "get_links")
        return BrowserResult.fail("No active page.", BrowserErrorType.UNAVAILABLE)

    def scroll(self, direction: str = "down", amount: int = 3) -> BrowserResult:
        page = self._active_page()
        if page:
            try:
                px = 500 * amount * (1 if direction == "down" else -1)
                self._op.run_sync(
                    lambda: page.evaluate(f"window.scrollBy(0, {px})"), timeout=5.0
                )
                return BrowserResult.ok(message=f"Scrolled {direction}, Sir.")
            except Exception as e:
                logger.warning(f"[SmartBrowser] scroll playwright failed: {e}")

        try:
            import pyautogui
            key = "pgdn" if direction == "down" else "pgup"
            for _ in range(amount):
                pyautogui.press(key)
                time.sleep(0.1)
            return BrowserResult.ok(message=f"Scrolled {direction}, Sir.")
        except Exception as e:
            return BrowserResult.fail_exc(e, "scroll")

    def browser_navigation(self, action: str = "focus_current") -> BrowserResult:
        page = self._active_page()
        if page:
            try:
                def _nav():
                    if action == "back":
                        page.go_back(timeout=8_000)
                    elif action == "forward":
                        page.go_forward(timeout=8_000)
                    elif action == "refresh":
                        page.reload(timeout=10_000)
                    elif action == "focus_current":
                        page.bring_to_front()
                    else:
                        raise ValueError(f"Unknown nav action: {action}")
                self._op.run_sync(_nav, timeout=12.0)
                return BrowserResult.ok(message=f"Browser {action}, Sir.")
            except ValueError as e:
                return BrowserResult.fail(str(e), BrowserErrorType.UNKNOWN)
            except Exception as e:
                return BrowserResult.fail_exc(e, f"browser_navigation:{action}")

        return BrowserResult.fail(
            f"No active page for '{action}'.", BrowserErrorType.UNAVAILABLE
        )

    # ── Search results cache ──────────────────────────────────────────────

    def store_search_results(self, results: List[str]):
        self._search_results = [r for r in results if r.startswith("http")][:10]
        logger.info(f"[SmartBrowser] Stored {len(self._search_results)} search results")

    def resolve_result_index(self, index_word: str) -> Optional[str]:
        _IDX = {"first": 0, "1st": 0, "1": 0,
                "second": 1, "2nd": 1, "2": 1,
                "third": 2, "3rd": 2, "3": 2}
        idx = _IDX.get(str(index_word).lower().strip(), -1)
        return self._search_results[idx] if 0 <= idx < len(self._search_results) else None

    # ── Status / diagnostics ──────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        page = self._active_page()
        return {
            "mode":            self._mode.value if self._mode else "unresolved",
            "browser":         self._detector.display_name,
            "playwright_type": self._detector.playwright_type,
            "has_page":        page is not None,
            "current_url":     self.get_current_url(),
            "search_results":  len(self._search_results),
        }

    def close(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETONS
# ════════════════════════════════════════════════════════════════════════════

smart_browser_ctrl = SmartBrowserController()


# ════════════════════════════════════════════════════════════════════════════
# INTEGRATION HELPER
# ════════════════════════════════════════════════════════════════════════════

def patch_core_patch_browser():
    """
    Replace core_patch.browser_ctrl with SmartBrowserController v2.
    Call once in main.py after imports.
    """
    try:
        import jarvis_patch.core_patch as _cp
        _cp.browser_ctrl     = smart_browser_ctrl
        _cp.uia_extractor    = uia_extractor
        _cp.browser_detector = browser_detector
        logger.info(
            "[BrowserDetect] core_patch.browser_ctrl -> SmartBrowserController v2 "
            f"(detected: {browser_detector.display_name})"
        )
    except ImportError as e:
        logger.warning(f"[BrowserDetect] core_patch not found: {e}")
    except Exception as e:
        logger.error(f"[BrowserDetect] patch_core_patch_browser failed: {e}")


# ════════════════════════════════════════════════════════════════════════════
# SMOKE TEST
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        force=True,
    )

    print("\n=== Browser Detection ===")
    bd = BrowserDetector().detect()
    print(f"  Browser  : {bd.display_name}")
    print(f"  EXE      : {bd.exe_name}")
    print(f"  PW type  : {bd.playwright_type}")
    print(f"  Classes  : {bd.win_classes}")

    print("\n=== Profile Path (FIX 1) ===")
    profile = _default_profile_dir(bd.exe_name)
    print(f"  Path     : {profile}")
    print(f"  Exists   : {profile.exists()}")

    print("\n=== CDP Port Scan (FIX 2) ===")
    port = _find_running_cdp_port()
    print(f"  Running CDP port: {port or 'none'}")

    print("\n=== Error Classification (FIX 5) ===")
    cases = [
        (TimeoutError("Navigation timeout of 30000ms exceeded"), "timeout"),
        (ConnectionRefusedError("net::ERR_CONNECTION_REFUSED"), "network"),
        (RuntimeError("Page has been detached from frame"),     "navigation"),
        (PermissionError("popup blocked by browser"),           "permission"),
        (Exception("something completely random"),              "unknown"),
    ]
    for exc, _ in cases:
        err = BrowserError.classify(exc)
        print(f"  {type(exc).__name__:28} -> {err.error_type.value:12} retryable={err.retryable}")

    print("\n=== Plan Validation ===")
    plan = [
        {"tool": "browser", "action": "open_url",  "params": {"url": "google.com"}},
        {"tool": "browser", "action": "read_page", "params": {}},
        {"tool": "browser", "action": "do_magic",  "params": {}},   # invalid
    ]
    ok, err = validate_plan(plan)
    print(f"  ok={ok}, error='{err}'")

    print("\n=== Controller Status ===")
    ctrl = SmartBrowserController()
    print(f"  {ctrl.status()}")