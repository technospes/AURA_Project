"""
EXECUTION VALIDATOR — Smarter Fallback + Input Sanitisation
============================================================
GAP 2: The original fallback guessed `{name}.com` for any failed app.
This caused bugs like:
  - "open notepad" failing → opening "notepad.com" (a random website)
  - "play Starboy" failing → building "starboy.com"

This module:
  1. Validates app names against a known registry before launching
  2. Builds correct web fallbacks per category (not just `.com`)
  3. Provides platform-correct URL builders for media/search/communication
  4. Validates execution success beyond just "did the process start?"

Usage (in runner.py):
    from executor.validator import ExecutionValidator
    validator = ExecutionValidator()

    # Before launching:
    clean_name, category = validator.resolve_app("spotify")
    # → ("Spotify", "media")

    # Build fallback URL:
    url = validator.build_fallback_url("spotify", "media", song="Starboy")
    # → "https://open.spotify.com/search/Starboy"

    # Validate launch success:
    ok = validator.verify_app_launched("spotify")
"""

import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# APP REGISTRY
# ══════════════════════════════════════════════════════════════════════════

# Maps normalised name → (display name, process name, category, web_url)
APP_REGISTRY: Dict[str, Tuple[str, str, str, str]] = {
    # Media
    "spotify":     ("Spotify",      "Spotify.exe",     "media",   "https://open.spotify.com"),
    "vlc":         ("VLC",          "vlc.exe",         "media",   "https://www.videolan.org"),
    "youtube":     ("YouTube",      "",                "media",   "https://www.youtube.com"),
    "netflix":     ("Netflix",      "ApplicationFrameHost.exe", "media", "https://www.netflix.com"),
    "prime":       ("Prime Video",  "",                "media",   "https://www.primevideo.com"),
    "prime video": ("Prime Video",  "",                "media",   "https://www.primevideo.com"),

    # Browsers
    "chrome":      ("Google Chrome","chrome.exe",      "browser", ""),
    "firefox":     ("Firefox",      "firefox.exe",     "browser", ""),
    "edge":        ("Microsoft Edge","msedge.exe",     "browser", ""),
    "brave":       ("Brave",        "brave.exe",       "browser", ""),

    # Communication
    "discord":     ("Discord",      "Discord.exe",     "communication", "https://discord.com/app"),
    "whatsapp":    ("WhatsApp",     "WhatsApp.exe",    "communication", "https://web.whatsapp.com"),
    "telegram":    ("Telegram",     "Telegram.exe",    "communication", "https://web.telegram.org"),
    "teams":       ("Microsoft Teams","Teams.exe",     "communication", "https://teams.microsoft.com"),
    "zoom":        ("Zoom",         "Zoom.exe",        "communication", "https://app.zoom.us"),
    "slack":       ("Slack",        "slack.exe",       "communication", "https://app.slack.com"),
    "gmail":       ("Gmail",        "",                "communication", "https://mail.google.com"),

    # Productivity
    "notepad":     ("Notepad",      "notepad.exe",     "utility", ""),
    "notepad++":   ("Notepad++",    "notepad++.exe",   "utility", ""),
    "vscode":      ("VS Code",      "Code.exe",        "dev",     ""),
    "code":        ("VS Code",      "Code.exe",        "dev",     ""),
    "word":        ("Microsoft Word","WINWORD.EXE",    "office",  "https://office.com"),
    "excel":       ("Microsoft Excel","EXCEL.EXE",     "office",  "https://office.com"),
    "powerpoint":  ("PowerPoint",   "POWERPNT.EXE",   "office",  "https://office.com"),
    "office":      ("Microsoft Office","",             "office",  "https://office.com"),
    "calculator":  ("Calculator",   "CalculatorApp.exe","utility",""),
    "paint":       ("Paint",        "mspaint.exe",     "utility", ""),
    "explorer":    ("File Explorer","explorer.exe",    "utility", ""),
    "task manager":("Task Manager", "Taskmgr.exe",     "system",  ""),

    # Games / other
    "steam":       ("Steam",        "steam.exe",       "games",   "https://store.steampowered.com"),
    "epic":        ("Epic Games",   "EpicGamesLauncher.exe","games","https://www.epicgames.com"),
}

# Media platform → search URL template
MEDIA_SEARCH_URLS: Dict[str, str] = {
    "spotify":  "https://open.spotify.com/search/{query}",
    "youtube":  "https://www.youtube.com/results?search_query={query}",
    "soundcloud": "https://soundcloud.com/search?q={query}",
    "apple music": "https://music.apple.com/search?term={query}",
}

# Search engine URL templates
SEARCH_URLS: Dict[str, str] = {
    "google":  "https://www.google.com/search?q={query}",
    "bing":    "https://www.bing.com/search?q={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
    "reddit":  "https://www.reddit.com/search/?q={query}",
    "amazon":  "https://www.amazon.in/s?k={query}",
    "flipkart":"https://www.flipkart.com/search?q={query}",
}


# ══════════════════════════════════════════════════════════════════════════
# EXECUTION VALIDATOR
# ══════════════════════════════════════════════════════════════════════════

class ExecutionValidator:
    """
    Validates inputs and builds correct fallbacks for execution.
    Used by ExecutionRunner before/after each tool call.
    """

    def resolve_app(self, raw_name: str) -> Tuple[Optional[str], str]:
        """
        Normalise an app name to its registry entry.

        Returns:
            (canonical_name_or_None, category)
            If None → app not in registry (unknown, might still exist)
        """
        key = self._normalise(raw_name)

        # Direct match
        if key in APP_REGISTRY:
            display, process, category, _ = APP_REGISTRY[key]
            return display, category

        # Fuzzy: check if any key is contained in the input or vice versa
        for reg_key, (display, process, category, _) in APP_REGISTRY.items():
            if reg_key in key or key in reg_key:
                return display, category

        return None, "unknown"

    def build_fallback_url(
        self,
        app_or_query: str,
        category: str,
        song: str = "",
        platform: str = "",
        query: str = "",
    ) -> str:
        """
        Build the correct fallback URL when a native app launch fails.

        Unlike the original runner.py which blindly did `{name}.com`,
        this builds a sensible URL per category.
        """
        from urllib.parse import quote_plus

        key = self._normalise(app_or_query)

        # If in registry, use its web URL
        if key in APP_REGISTRY:
            _, _, cat, web_url = APP_REGISTRY[key]
            if web_url:
                # For media apps, append search if we have a song
                if cat == "media" and song:
                    tmpl = MEDIA_SEARCH_URLS.get(key)
                    if tmpl:
                        return tmpl.format(query=quote_plus(song))
                return web_url

        # Media platform with a song
        if category == "media" and (song or query):
            search_term = song or query
            plat = self._normalise(platform) if platform else "youtube"
            tmpl = MEDIA_SEARCH_URLS.get(plat, MEDIA_SEARCH_URLS["youtube"])
            return tmpl.format(query=quote_plus(search_term))

        # Search
        if category == "search" and query:
            engine = self._normalise(platform) if platform else "google"
            tmpl = SEARCH_URLS.get(engine, SEARCH_URLS["google"])
            return tmpl.format(query=quote_plus(query))

        # Communication
        if category == "communication":
            if key in APP_REGISTRY:
                return APP_REGISTRY[key][3]

        # Generic last resort — only for real website names
        if "." in app_or_query:
            url = app_or_query if app_or_query.startswith("http") else f"https://{app_or_query}"
            return url

        # Use Google search as final fallback (much better than guessing .com)
        search_q = song or query or app_or_query
        return SEARCH_URLS["google"].format(query=quote_plus(search_q))

    def verify_app_launched(self, app_name: str, timeout: float = 3.0) -> bool:
        """
        Verify an app actually launched by checking process list.
        More reliable than just checking if open_app returned without error.
        """
        import time
        key = self._normalise(app_name)

        process_name = ""
        if key in APP_REGISTRY:
            _, process_name, _, _ = APP_REGISTRY[key]

        if not process_name:
            # Can't verify — assume success
            return True

        process_name_lower = process_name.lower()

        # Poll for up to timeout
        end = time.time() + timeout
        while time.time() < end:
            if self._is_process_running(process_name_lower):
                return True
            time.sleep(0.3)

        return False

    def verify_app_closed(self, app_name: str) -> bool:
        """Verify an app closed successfully."""
        key = self._normalise(app_name)
        if key not in APP_REGISTRY:
            return True  # Can't verify → assume OK
        _, process_name, _, _ = APP_REGISTRY[key]
        if not process_name:
            return True
        return not self._is_process_running(process_name.lower())

    def clean_url(self, url: str) -> str:
        """Ensure a URL is properly formed."""
        url = url.strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            if "." in url:
                url = "https://" + url
            else:
                # Not a URL — treat as search
                from urllib.parse import quote_plus
                url = SEARCH_URLS["google"].format(query=quote_plus(url))
        return url

    def sanitize_search_query(self, query: str) -> str:
        """Clean a search query — remove double spaces, dangerous chars."""
        query = query.strip()
        query = re.sub(r"\s+", " ", query)
        # Remove characters that break URLs
        query = re.sub(r"[<>\"{}|\\^`\[\]]", "", query)
        return query[:500]  # Cap length

    def is_valid_app(self, name: str) -> bool:
        """Check if an app name is in the registry."""
        return self._normalise(name) in APP_REGISTRY

    # ── PRIVATE ────────────────────────────────────────────────────────────

    def _normalise(self, name: str) -> str:
        """Lowercase, strip punctuation for consistent lookup."""
        return name.lower().strip().rstrip(".")

    def _is_process_running(self, process_name: str) -> bool:
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                pname = (proc.info.get("name") or "").lower()
                if process_name in pname or pname in process_name:
                    return True
            return False
        except Exception:
            return True  # If we can't check, assume running
