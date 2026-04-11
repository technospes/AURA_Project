import os
import shutil
import string
import ctypes
import winreg
import logging
import threading
import subprocess
from difflib import get_close_matches

logger = logging.getLogger(__name__)


class AppLocator:
    """
    Centralized, cached, fuzzy-matching OS Application Locator.

    Lookup priority (fastest → most thorough):
      1. Per-query cache          — O(1), instant
      2. UWP protocol map         — O(n) on known_uwp dict, ~µs
      3. System PATH              — shutil.which, ~ms
      4. Registry App Paths       — ~ms
      5. Start Menu shortcuts     — ~ms
      6. Disk index               — O(1) after one-time background scan

    The disk index is built ONCE in a background thread the first time
    find_app() falls through to stage 6.  Every subsequent call hits the
    pre-built dict — no repeated drive walking, zero CPU spike.
    """

    # Install roots to index — never scan an entire drive root
    _INSTALL_SUBDIRS = [
        "Program Files",
        "Program Files (x86)",
        "Programs",
        "Apps",
        "Applications",
        "Games",
        r"Steam\steamapps\common",
        "Riot Games",
        "Epic Games",
        "GOG Games",
        r"Users\Public\Desktop",
    ]

    def __init__(self):
        self._cache: dict = {}          # per-query resolved result cache
        self._disk_index: dict = {}     # exe_stem_lower → full_path (built once)
        self._indexed: bool = False     # True after _build_index() completes
        self._index_lock = threading.Lock()
        self._index_thread: threading.Thread | None = None

        self._known_uwp = {
            "whatsapp":       "whatsapp:",
            "spotify":        "spotify:",
            "discord":        "discord:",
            "calculator":     "calculator:",
            "notepad":        "notepad:",
            "settings":       "ms-settings:",
            "ms settings":    "ms-settings:",
            "windows store":  "ms-windows-store:",
            "store":          "ms-windows-store:",
            "xbox":           "xbox:",
            "mail":           "outlookmail:",
            "calendar app":   "outlookcal:",
            "maps app":       "bingmaps:",
            "camera":         "microsoft.windows.camera:",
            "photos":         "ms-photos:",
            "paint 3d":       "ms-paint:",
            "snip":           "ms-screenclip:",
            "snipping tool":  "ms-screenclip:",
            "weather":        "bingweather:",
            "cortana":        "ms-cortana:",
            "edge":           "microsoft-edge:",
            "microsoft edge": "microsoft-edge:",
        }

    # ── PUBLIC API ────────────────────────────────────────────────────────

    def clear_cache(self):
        """Clear per-query cache only — keep the disk index intact."""
        self._cache.clear()

    def rebuild_index(self):
        """Force a fresh disk scan (e.g. after installing new software)."""
        with self._index_lock:
            self._indexed = False
            self._disk_index.clear()
            self._index_thread = None
        self._ensure_index()

    def find_app(self, app_name: str) -> str | None:
        """Return executable path or URI if found, else None."""
        app_name = app_name.lower().strip()
        if not app_name:
            return None

        # ── 1. Per-query cache ─────────────────────────────────────────
        if app_name in self._cache:
            logger.info(f"[LOCATOR] CACHE: {self._cache[app_name]}")
            return self._cache[app_name]

        # ── 2. UWP protocols ───────────────────────────────────────────
        # Match if the app_name equals the key, contains it as a whole word,
        # or if the key is a multi-word phrase contained in app_name.
        # E.g. "discord" matches "open discord" and "discord app".
        app_words = set(app_name.split())
        for key, uri in self._known_uwp.items():
            key_words = key.split()
            if (
                app_name == key                          # exact
                or key in app_name                       # substring ("discord" in "open discord")
                or all(w in app_words for w in key_words)# all words present
            ):
                self._store(app_name, uri)
                logger.info(f"[LOCATOR] UWP PROTOCOL: {uri}")
                return uri

        candidates = [app_name, app_name.replace(" ", "")]

        # ── 3. System PATH ─────────────────────────────────────────────
        for cand in candidates:
            path = shutil.which(cand) or shutil.which(f"{cand}.exe")
            if path:
                self._store(app_name, path)
                logger.info(f"[LOCATOR] PATH: {path}")
                return path

        # ── 4. Registry App Paths ──────────────────────────────────────
        result = self._search_registry(candidates)
        if result:
            self._store(app_name, result)
            logger.info(f"[LOCATOR] REGISTRY: {result}")
            return result

        # ── 5. Start Menu shortcuts ────────────────────────────────────
        result = self._search_start_menu(app_name, candidates)
        if result:
            self._store(app_name, result)
            logger.info(f"[LOCATOR] START MENU: {result}")
            return result

        # ── 6. Disk index (built once, O(1) lookups forever after) ────
        self._ensure_index()   # no-op if already indexed; blocks if scan is running
        result = self._query_disk_index(candidates)
        if result:
            self._store(app_name, result)
            logger.info(f"[LOCATOR] DISK INDEX: {result}")
            return result

        logger.info(f"[LOCATOR] Not found: {app_name}")
        return None

    def launch(self, app_name: str) -> bool:
        """Find and launch the app. Returns True on success."""
        path = self.find_app(app_name)
        if not path:
            return False
        try:
            if path.endswith(":"):
                os.startfile(path)
            else:
                subprocess.Popen(f'start "" "{path}"', shell=True)
            return True
        except Exception as e:
            logger.error(f"[LOCATOR] Launch failed for {path}: {e}")
            return False

    # ── INTERNAL: REGISTRY ────────────────────────────────────────────────

    def _search_registry(self, candidates: list) -> str | None:
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        best, best_score = None, 0
        for hkey, reg_path in reg_paths:
            try:
                with winreg.OpenKey(hkey, reg_path) as key:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            subkey_lower = subkey_name.lower()
                            for cand in candidates:
                                if cand in subkey_lower:
                                    score = len(cand)
                                    if cand == subkey_lower.replace(".exe", ""):
                                        score += 100
                                    if score > best_score:
                                        with winreg.OpenKey(key, subkey_name) as sk:
                                            target = winreg.QueryValue(sk, None)
                                            if target and os.path.exists(target):
                                                best_score = score
                                                best = target
                            i += 1
                        except OSError:
                            break
            except FileNotFoundError:
                pass
        return best

    # ── INTERNAL: START MENU ──────────────────────────────────────────────

    def _search_start_menu(self, app_name: str, candidates: list) -> str | None:
        sm_dirs = [
            os.path.join(os.environ.get("APPDATA", ""),     r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        ]
        shortcuts: dict = {}
        for sm_dir in sm_dirs:
            if not os.path.isdir(sm_dir):
                continue
            for root, _, files in os.walk(sm_dir):
                for f in files:
                    if f.endswith(".lnk"):
                        shortcuts[f.lower()[:-4]] = os.path.join(root, f)

        # Substring scoring
        best, best_score = None, 0
        for s_name, s_path in shortcuts.items():
            for cand in candidates:
                if cand in s_name:
                    score = len(cand) + (100 if cand == s_name else 0)
                    if score > best_score:
                        best_score, best = score, s_path
        if best:
            return best

        # Fuzzy fallback (strict cutoff)
        matches = get_close_matches(app_name, list(shortcuts.keys()), n=1, cutoff=0.75)
        return shortcuts[matches[0]] if matches else None

    # ── INTERNAL: DISK INDEX ──────────────────────────────────────────────

    def _ensure_index(self):
        """
        Guarantee the disk index exists before returning.

        - First call: starts scan in background thread, then waits for it.
          The wait only happens once in the program's lifetime.
        - All subsequent calls: instant no-op (_indexed is True).
        """
        if self._indexed:
            return

        with self._index_lock:
            if self._indexed:   # Double-checked locking
                return
            if self._index_thread is None or not self._index_thread.is_alive():
                self._index_thread = threading.Thread(
                    target=self._build_index,
                    daemon=True,
                    name="Jarvis-DiskIndexer",
                )
                self._index_thread.start()

        logger.info("[LOCATOR] First-time disk scan — waiting for index…")
        self._index_thread.join()
        logger.info(f"[LOCATOR] Disk index ready ({len(self._disk_index)} executables)")

    def _build_index(self):
        """
        Walk install directories ONCE across all drives.
        Stores: self._disk_index = { exe_stem_lower: full_path }

        Rules:
          - Shallowest path wins (main launcher, not sub-process helpers)
          - Depth capped at 4 levels per root (fast, covers all real installs)
          - Indexes both "my app" and "myapp" (no-space variant)
        """
        index: dict = {}

        drives = self._get_drive_letters()

        # User-specific absolute paths (no drive prefix needed)
        abs_roots = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("APPDATA", ""),
        ]

        roots_to_scan = list(abs_roots)
        for drive in drives:
            for subdir in self._INSTALL_SUBDIRS:
                roots_to_scan.append(os.path.join(drive, subdir))

        for root_dir in roots_to_scan:
            if not os.path.isdir(root_dir):
                continue
            try:
                for root, dirs, files in os.walk(root_dir):
                    depth = root[len(root_dir):].count(os.sep)
                    if depth > 4:
                        dirs.clear()   # prune — don't descend further
                        continue

                    for file in files:
                        if not file.lower().endswith(".exe"):
                            continue
                        stem = file.lower()[:-4]       # "discord" from "Discord.exe"
                        full = os.path.join(root, file)

                        if stem not in index:          # shallowest path wins
                            index[stem] = full

                        no_space = stem.replace(" ", "")
                        if no_space != stem and no_space not in index:
                            index[no_space] = full

            except PermissionError:
                continue
            except Exception as e:
                logger.debug(f"[LOCATOR] Index error in {root_dir}: {e}")

        self._disk_index = index
        self._indexed = True

    def _query_disk_index(self, candidates: list) -> str | None:
        """Pure O(1) dict lookup. Falls back to partial match if needed."""
        # Exact stem match
        for cand in candidates:
            hit = self._disk_index.get(cand)
            if hit and os.path.exists(hit):
                return hit

        # Partial match (e.g. "chrome" inside "googlechrome")
        best, best_score = None, 0
        for cand in candidates:
            for stem, path in self._disk_index.items():
                if cand in stem and len(cand) > best_score:
                    best_score = len(cand)
                    best = path
        return best

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _store(self, app_name: str, path: str):
        self._cache[app_name] = path

    @staticmethod
    def _get_drive_letters() -> list[str]:
        drives = []
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    drives.append(f"{letter}:\\")
        except Exception:
            drives = ["C:\\"]   # safe fallback on non-Windows
        return drives


# ── Global singleton ───────────────────────────────────────────────────────
app_locator = AppLocator()

# Pre-warm the disk index immediately in the background.
# By the time the user speaks their first command (~3-5s after startup),
# the index will already be fully built — zero first-call latency.
def _prewarm_disk_index():
    import threading
    t = threading.Thread(
        target=app_locator._ensure_index,
        daemon=True,
        name="Jarvis-DiskPrewarm",
    )
    t.start()

_prewarm_disk_index()