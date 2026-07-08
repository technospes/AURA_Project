import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import winreg
import shutil
from utils.app_locator import app_locator

def is_app_installed(app_name: str) -> bool:
    """Checks the centralized locator."""
    return app_locator.find_app(app_name) is not None
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# TOOL CAPABILITY REGISTRY
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCapability:
    """Describes what a tool can do and when it should be preferred."""
    name: str
    actions: List[str]           # Actions this tool supports
    preferred_when: List[str]    # Context conditions that favour this tool
    fallback_for: List[str]      # Other tools this can replace
    requires: List[str]          # System requirements (e.g. "spotify_installed")
    cost: float                  # 0-1: lower = faster/simpler (prefer lower)
    reliability: float           # 0-1: based on real-world failure rates


TOOL_REGISTRY: Dict[str, ToolCapability] = {

    "app_launcher": ToolCapability(
        name="app_launcher",
        actions=["open_app", "close_app", "focus_app", "minimize_app", "maximize_app"],
        preferred_when=["app_is_installed", "direct_launch_needed"],
        fallback_for=[],
        requires=[],
        cost=0.1,
        reliability=0.85,
    ),

    "browser": ToolCapability(
        name="browser",
        actions=["open_website", "close_tab", "new_tab", "search_web", "scroll", "read_page"],
        preferred_when=["active_app_is_browser", "url_provided"],
        fallback_for=["app_launcher", "media_controller"],
        requires=[],
        cost=0.2,
        reliability=0.92,
    ),

    "media_controller": ToolCapability(
        name="media_controller",
        actions=["play_media", "pause_media", "resume_media", "next_track", "previous_track", "set_volume"],
        preferred_when=["spotify_active", "vlc_active", "media_key_available"],
        fallback_for=[],
        requires=[],
        cost=0.15,
        reliability=0.80,
    ),

    "keyboard": ToolCapability(
        name="keyboard",
        actions=["type_text", "save_file", "scroll"],
        preferred_when=["text_input_focused"],
        fallback_for=[],
        requires=[],
        cost=0.05,
        reliability=0.88,
    ),

    "web_navigator": ToolCapability(
        name="web_navigator",
        actions=["search_web", "fetch_and_parse", "synthesize_research"],
        preferred_when=["deep_research_needed", "no_browser_open"],
        fallback_for=["browser"],
        requires=[],
        cost=0.6,
        reliability=0.75,
    ),

    "ai_brain": ToolCapability(
        name="ai_brain",
        actions=["answer_question", "synthesize_research"],
        preferred_when=["factual_question", "no_web_needed"],
        fallback_for=["web_navigator"],
        requires=["groq_api_key"],
        cost=0.3,
        reliability=0.95,
    ),

    "communicator": ToolCapability(
        name="communicator",
        actions=["navigate_to_contact", "type_and_send", "initiate_call"],
        preferred_when=["communication_app_open"],
        fallback_for=[],
        requires=[],
        cost=0.4,
        reliability=0.70,
    ),

    "system": ToolCapability(
        name="system",
        actions=["shutdown", "restart", "lock", "take_screenshot", "cancel_current"],
        preferred_when=[],
        fallback_for=[],
        requires=[],
        cost=0.1,
        reliability=0.95,
    ),

    "memory": ToolCapability(
        name="memory",
        actions=["store_memory", "recall_memory"],
        preferred_when=[],
        fallback_for=[],
        requires=[],
        cost=0.05,
        reliability=0.99,
    ),
}


# ── ACTION → CANDIDATE TOOLS (ordered by preference) ──────────────────────

ACTION_TOOL_MAP: Dict[str, List[str]] = {
    "open_app":          ["app_launcher", "browser"],
    "close_app":         ["app_launcher"],
    "focus_app":         ["app_launcher"],
    "play_media":        ["media_controller", "browser"],
    "pause_media":       ["media_controller"],
    "resume_media":      ["media_controller"],
    "next_track":        ["media_controller"],
    "previous_track":    ["media_controller"],
    "open_website":      ["browser"],
    "search_web":        ["browser", "web_navigator"],
    "close_tab":         ["browser"],
    "new_tab":           ["browser"],
    "scroll":            ["browser", "keyboard"],
    "read_page":         ["browser"],
    "type_text":         ["keyboard"],
    "save_file":         ["keyboard"],
    "answer_question":   ["ai_brain", "web_navigator"],
    "synthesize_research": ["ai_brain", "web_navigator"],
    "fetch_and_parse":   ["web_navigator"],
    "send_message":      ["communicator"],
    "make_call":         ["communicator"],
    "navigate_to_contact": ["communicator"],
    "shutdown":          ["system"],
    "restart":           ["system"],
    "lock":              ["system"],
    "take_screenshot":   ["system"],
    "store_memory":      ["memory"],
    "recall_memory":     ["memory"],
}



# ── KNOWN OFFICIAL URLS ────────────────────────────────────────────────────
# When an app is genuinely not installed, open the official site directly.
# DuckDuckGo !ducky bang is used for unknown apps — it redirects to the
# top result which is almost always the official page.

_KNOWN_OFFICIAL_URLS: Dict[str, str] = {
    "spotify":       "https://open.spotify.com",
    "discord":       "https://discord.com/app",
    "whatsapp":      "https://web.whatsapp.com",
    "telegram":      "https://web.telegram.org",
    "youtube":       "https://www.youtube.com",
    "netflix":       "https://www.netflix.com",
    "twitch":        "https://www.twitch.tv",
    "steam":         "https://store.steampowered.com",
    "epic games":    "https://www.epicgames.com",
    "epic":          "https://www.epicgames.com",
    "notion":        "https://www.notion.so",
    "slack":         "https://app.slack.com",
    "zoom":          "https://zoom.us/join",
    "teams":         "https://teams.microsoft.com",
    "figma":         "https://www.figma.com",
    "github":        "https://github.com",
    "gmail":         "https://mail.google.com",
    "outlook":       "https://outlook.live.com",
    "reddit":        "https://www.reddit.com",
    "twitter":       "https://twitter.com",
    "x":             "https://x.com",
    "instagram":     "https://www.instagram.com",
    "facebook":      "https://www.facebook.com",
    "linkedin":      "https://www.linkedin.com",
    "amazon":        "https://www.amazon.in",
    "flipkart":      "https://www.flipkart.com",
    "prime":         "https://www.primevideo.com",
    "hotstar":       "https://www.hotstar.com",
    "claude":        "https://claude.ai",
    "chatgpt":       "https://chat.openai.com",
    "gemini":        "https://gemini.google.com",
    "maps":          "https://maps.google.com",
    "drive":         "https://drive.google.com",
    "docs":          "https://docs.google.com",
    "sheets":        "https://sheets.google.com",
    "calendar":      "https://calendar.google.com",
    "canva":         "https://www.canva.com",
    "trello":        "https://trello.com",
    "jira":          "https://www.atlassian.com/software/jira",
    "obsidian":      "https://obsidian.md",
    "valorant":      "https://playvalorant.com",
    "minecraft":     "https://www.minecraft.net",
    "roblox":        "https://www.roblox.com",
    "fortnite":      "https://www.fortnite.com",
}


def _get_official_url(app_name: str) -> str:
    """
    Return the best URL for an app that isn't installed locally.
    Preference order:
      1. Exact match in known URL table
      2. Partial match in known URL table
      3. DuckDuckGo !ducky bang → redirects straight to the official site
         (far better UX than a Google search results page)
    """
    import urllib.parse
    key = app_name.lower().strip()
    if key in _KNOWN_OFFICIAL_URLS:
        return _KNOWN_OFFICIAL_URLS[key]
    for known, url in _KNOWN_OFFICIAL_URLS.items():
        if known in key or key in known:
            return url
    # Unknown app: DuckDuckGo instant redirect to top result
    return f"https://duckduckgo.com/?q=!ducky+{urllib.parse.quote(app_name)}"

# ══════════════════════════════════════════════════════════════════════════
# TOOL SELECTOR
# ══════════════════════════════════════════════════════════════════════════

class ToolSelector:
    """
    Selects the best tool for each action based on context + history.

    Call select() for a single action.
    Call select_for_plan() to annotate an entire plan at once.
    """

    def __init__(self):
        self._failed_tools: Dict[str, int] = {}   # tool → consecutive failure count
        self._success_history: Dict[str, int] = {} # tool → success count

    def select(
        self,
        action: str,
        entities: Dict,
        context: Dict,
        step_results: Optional[List[Dict]] = None,
    ) -> Tuple[str, Dict]:
        """
        Select the best tool for an action.

        Args:
            action:       Action name (e.g. "play_media")
            entities:     Intent entities
            context:      Current context snapshot
            step_results: Previous step results in this turn

        Returns:
            (tool_name, adjusted_params)
        """
        step_results = step_results or []
        candidates = ACTION_TOOL_MAP.get(action, ["system"])

        if not candidates:
            logger.warning(f"No tool mapping for action: {action}")
            return "system", entities

        # Score each candidate
        scored = []
        for tool_name in candidates:
            cap = TOOL_REGISTRY.get(tool_name)
            if not cap:
                continue
            score = self._score_tool(tool_name, cap, action, entities, context, step_results)
            scored.append((score, tool_name, cap))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return candidates[0], entities

        best_score, best_tool, best_cap = scored[0]

        # Adjust params based on selected tool
        adjusted = self._adjust_params(action, best_tool, entities, context)

        logger.debug(
            f" Tool selected: {best_tool} for '{action}' "
            f"(score={best_score:.2f})"
        )

        if len(scored) > 1:
            runner_up = scored[1][1]
            logger.debug(f"   Runner-up: {runner_up} ({scored[1][0]:.2f})")

        return best_tool, adjusted

    def select_for_plan(self, plan: List[Dict], context: Dict) -> List[Dict]:
        """Annotate every step in a plan with the best tool."""
        step_results: List[Dict] = []

        for step in plan:
            action = step.get("action", "")
            entities = step.get("params", {})
            existing_tool = step.get("tool")

            # Get the best tool and any param adjustments (including OS App Checks!)
            best_tool, adjusted_params = self.select(action, entities, context, step_results)

            # ── THE FIX: Forcefully apply Action/Tool Overrides ──
            if "action_override" in adjusted_params:
                new_action = adjusted_params.pop("action_override")
                step["action"] = new_action
                
                # Automatically switch the tool to match the new action
                if new_action == "smart_open":
                    step["tool"] = "smart_open"
                elif new_action == "open_website":
                    step["tool"] = "browser"
            else:
                # Keep the planner's tool unless it failed previously or was set to None
                if not existing_tool or self._failed_tools.get(existing_tool, 0) > 0:
                    step["tool"] = best_tool
                    
            step["params"] = adjusted_params

        return plan

    def record_result(self, tool_name: str, success: bool):
        """
        Record a tool execution result so future selections can
        avoid recently-failed tools.
        """
        if success:
            self._failed_tools[tool_name] = 0
            self._success_history[tool_name] = self._success_history.get(tool_name, 0) + 1
        else:
            self._failed_tools[tool_name] = self._failed_tools.get(tool_name, 0) + 1
            logger.info(
                f" Tool failure recorded: {tool_name} "
                f"(consecutive failures: {self._failed_tools[tool_name]})"
            )

    # ── SCORING ────────────────────────────────────────────────────────────

    def _score_tool(
        self,
        tool_name: str,
        cap: ToolCapability,
        action: str,
        entities: Dict,
        context: Dict,
        step_results: List[Dict],
    ) -> float:
        """Score a tool candidate (0-1, higher = better)."""

        # ── DETERMINISTIC ROUTING GATE ─────────────────────────────────────
        # High-stakes actions are routed by LOGIC, not probability scores.
        # Scoring only applies to ambiguous cases where multiple tools
        # are genuinely interchangeable (e.g. search_web, play_media).
        #
        # Rule: if there is ONE correct tool for an action+context pair,
        # return 1.0 for it and 0.0 for all others. No scoring needed.

        if action == "open_app":
            app_name = (
                entities.get("name") or entities.get("app") or
                entities.get("app_name") or ""
            ).strip()
            app_found = bool(app_name) and is_app_installed(app_name)

            if tool_name == "app_launcher":
                # DETERMINISTIC: always launch locally-found apps.
                # If not found, still route through launcher — it returns a
                # structured fallback dict that _execute_step handles.
                return 1.0  # launcher ALWAYS wins for open_app

            if tool_name == "browser":
                # Browser NEVER wins for open_app at selection time.
                # The fallback path is handled in _execute_step, not here.
                return 0.0

        if action == "open_website":
            # Browser always and only handles open_website. No scoring.
            if tool_name == "browser":
                return 1.0
            return 0.0

        if action in ("close_app", "focus_app", "minimize_app", "maximize_app"):
            # App launcher always handles app window management.
            if tool_name == "app_launcher":
                return 1.0
            return 0.0

        if action in ("shutdown", "restart", "lock", "take_screenshot", "cancel_current",
                      "minimize_app", "maximize_app", "set_volume"):
            if tool_name == "system":
                return 1.0
            return 0.0

        if action in ("store_memory", "recall_memory"):
            if tool_name == "memory":
                return 1.0
            return 0.0

        if action in ("send_message", "make_call", "navigate_to_contact"):
            if tool_name == "communicator":
                return 1.0
            return 0.0

        # ── Generic scoring for all other actions ─────────────────────────
        score = 0.5  # Base score

        # ── RELIABILITY ────────────────────────────────────────────────────
        score += cap.reliability * 0.2

        # ── COST (lower cost = faster = better) ───────────────────────────
        score -= cap.cost * 0.1

        # ── FAILURE PENALTY ────────────────────────────────────────────────
        failures = self._failed_tools.get(tool_name, 0)
        score -= min(failures * 0.15, 0.45)   # Max -0.45 penalty

        # ── CONTEXT BONUSES ────────────────────────────────────────────────
        active_app = context.get("active_app", "").lower()

        # Browser open → prefer browser tool for media (YouTube)
        if tool_name == "browser" and active_app in ("chrome", "firefox", "edge", "brave"):
            if action in ("search_web", "open_website", "play_media"):
                score += 0.15

        # Spotify active → prefer media_controller
        if tool_name == "media_controller" and active_app == "spotify":
            score += 0.20

        # Platform hint from entities
        platform = entities.get("platform", "").lower()
        if platform == "spotify" and tool_name == "media_controller":
            score += 0.15
        elif platform in ("youtube", "chrome") and tool_name == "browser":
            score += 0.15

        # ── PREVIOUS STEP CONTEXT ─────────────────────────────────────────
        # If previous step successfully opened a browser, prefer browser
        for r in step_results:
            if r.get("success") and r.get("action") in ("open_website", "search_web"):
                if tool_name == "browser":
                    score += 0.10
                    break

        # ── SUCCESS HISTORY BONUS ─────────────────────────────────────────
        successes = self._success_history.get(tool_name, 0)
        score += min(successes * 0.01, 0.1)   # Max +0.1 for reliable tools

        return max(0.0, min(score, 1.0))

    def _adjust_params(
        self,
        action: str,
        tool_name: str,
        entities: Dict,
        context: Dict,
    ) -> Dict:
        """
        Adjust entity params for the selected tool.
        E.g. if browser is selected for play_media (Spotify fallback),
        build a YouTube search URL instead of passing raw song name.
        """
        params = dict(entities)

        if action == "play_media" and tool_name == "browser":
            song = params.get("song", "")
            platform = params.get("platform", "youtube")
            if platform == "spotify":
                # Spotify failed — redirect to YouTube
                from urllib.parse import quote_plus
                params["url"] = f"https://www.youtube.com/results?search_query={quote_plus(song)}"
                params["action"] = "open_website"
                logger.info(f" Redirected Spotify play to YouTube: {song}")
            elif platform == "youtube":
                from urllib.parse import quote_plus
                params["url"] = f"https://www.youtube.com/results?search_query={quote_plus(song)}"

        if action == "open_app":
            app_name = params.get("name", params.get("app", params.get("app_name", ""))).strip()
            if app_name:
                if is_app_installed(app_name):
                    # App is confirmed on this machine — always use launcher,
                    # never touch the params, never redirect to the web.
                    # Normalize the key so AppLauncherTool.execute() always
                    # finds it regardless of which key the planner used.
                    params["name"] = app_name
                    logger.debug(f"[SELECTOR] '{app_name}' found locally → app_launcher")
                else:
                    # Genuinely not installed anywhere on this system.
                    # Build the best possible web fallback URL.
                    url = _get_official_url(app_name)
                    params["url"] = url
                    params["action_override"] = "open_website"
                    logger.info(
                        f"[SELECTOR] '{app_name}' not found on any drive "
                        f"→ web fallback: {url}"
                    )

        if action == "search_web":
            # Normalise platform name
            platform = params.get("platform", "google").lower()
            params["platform"] = platform

        return params


# ── MODULE-LEVEL SINGLETON ─────────────────────────────────────────────────

_selector = ToolSelector()


def select_tool(
    action: str,
    entities: Dict,
    context: Dict,
    step_results: Optional[List[Dict]] = None,
) -> Tuple[str, Dict]:
    """Convenience function — uses the shared ToolSelector instance."""
    return _selector.select(action, entities, context, step_results)


def record_tool_result(tool_name: str, success: bool):
    """Record a result on the shared selector so it learns from failures."""
    _selector.record_result(tool_name, success)