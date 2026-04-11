"""
PLANNING ENGINE v2 — Memory-Driven, Preference-Aware
======================================================
Converts intent → ordered executable steps.

KEY UPGRADE: Memory context ACTIVELY shapes every plan decision.

Examples:
  User said "I prefer Spotify" yesterday
  → "play music" → platform auto-set to "spotify"

  User said "I always use Chrome"
  → open_website → launch chrome explicitly

  User prefers YouTube for music videos
  → play_media → platform = "youtube" (overrides default)

  User's name is Ayush
  → greet response includes their name

Memory injection points:
  1. Platform selection (spotify vs youtube vs soundcloud)
  2. App preference (which browser, which IDE)
  3. Search engine preference
  4. Output format preference (spoken vs file)
  5. Communication platform (discord vs whatsapp)
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def create_plan_with_thinking(
    self,
    intent: Dict,
    memory_context: Dict,
    context: Dict,
    think_hints: Optional[Dict] = None
) -> List[Dict]:
    """
    PATCHED create_plan that accepts ThinkResult hints.
 
    If think_hints has subtasks, convert them to plan steps directly.
    This avoids redundant re-decomposition for complex intents.
 
    For simple intents (1 subtask), use normal planning.
    For complex intents (3+ subtasks), use ThinkResult subtasks as the plan base.
    """
    # If thinking produced explicit hints, attach them to intent
    if think_hints:
        intent["subtasks"] = think_hints.get("subtasks", [])
        if think_hints.get("subtask_count", 1) > 1:
            # ThinkResult already decomposed this — trust it
            intent["_think_goal"] = think_hints.get("goal")
            intent["_think_criteria"] = think_hints.get("success_criteria")
 
    # Continue with normal plan creation (memory prefs injection still happens)
    # Return None signals to use the normal create_plan flow
    return None


# ── STEP BUILDERS ─────────────────────────────────────────────────────────

def _step(action: str, tool: str, params: Dict, description: str,
          retries: int = 1, verify=None, duration_ms: int = 1000,
          depends_on: Optional[List[int]] = None, fallback: Optional[str] = None) -> Dict:
    """Helper to build a clean step dict."""
    s = {
        "action": action,
        "tool": tool,
        "params": params,
        "description": description,
        "retry_policy": {"max_retries": retries, "fallback": fallback},
        "verify": verify,
        "expected_duration_ms": duration_ms,
    }
    if depends_on:
        s["depends_on"] = depends_on
    return s


# ── PLAN GENERATORS ───────────────────────────────────────────────────────

def _plan_open_app(intent: Dict, prefs: Dict) -> List[Dict]:
    explicit_app = intent["entities"].get("app") or intent["entities"].get("app_name") or ""
    final_app = explicit_app if explicit_app else prefs.get(f"preferred_{explicit_app}_variant", explicit_app)
    return [_step(
        "open_app", None, {"name": final_app},  # ── THE FIX: 'None' allows ToolSelector to work its magic
        f"Open {final_app}",
        retries=0, 
        duration_ms=2000
    )]

def _plan_close_app(intent: Dict, prefs: Dict) -> List[Dict]:
    app = intent["entities"].get("app", "")
    
    # If the user said "close this tab" or "close it"
    if app in ("current_window", "it", "this", "the", "tab"):
        return [_step("close_current", "system", {}, "Close current window/tab", retries=0)]
        
    return [_step(
        "close_app", "app_launcher", {"name": app},
        f"Close {app}",
        retries=1, fallback="force_kill",
        verify={"type": "process_not_running", "name": app}
    )]


def _plan_play_media(intent: Dict, prefs: Dict) -> List[Dict]:
    song = intent.get("entities", {}).get("song", "")
    platform = intent.get("entities", {}).get("platform", "")

    # 1. Memory-aware platform selection
    if not platform:
        platform = (
            prefs.get("preferred_music_platform") or
            prefs.get("preferred_platform") or
            "youtube"
        )

    import urllib.parse
    safe_song = urllib.parse.quote(song)

    # 2. Agentic Routing (Native OS protocol deep links)
    if platform == "spotify":
        fast_uri = f"spotify:search:{safe_song}"
    else:
        fast_uri = f"https://www.youtube.com/results?search_query={safe_song}"

    # 3. Return the Hybrid Plan step
    return [{
        "action": "play_hybrid",
        "tool": "media_controller",
        "params": {
            "query": song,
            "platform": platform,
            "fast_uri": fast_uri
        },
        "description": f"Agentic Play: Fast URI → Verify → GUI Fallback for '{song}'",
        "retry_policy": {"max_retries": 2, "fallback": "search_web"},
        "verify": None,
        "duration_ms": 4000
    }]


def _plan_smart_open(intent: Dict, prefs: Dict) -> List[Dict]:
    """Plan for smart_open intent — finds and opens apps/files intelligently."""
    entities = intent.get("entities", {})
    raw_text = intent.get("original_text", "")
    query = entities.get("query", entities.get("app", raw_text))
    
    return [{
        "tool": "smart_open",
        "action": "smart_open",
        "description": f"Find and open: {query}",
        "params": {"query": query},
        "retry_policy": {"max_retries": 1}
    }]


def _plan_page_summary(intent: Dict, prefs: Dict) -> List[Dict]:
    """Plan for page_summary intent — summarizes current page."""
    return [{
        "tool": "page_context",
        "action": "page_summary",
        "description": "Summarize current page",
        "params": {}
    }]


def _plan_read_page(intent: Dict, prefs: Dict) -> List[Dict]:
    """Plan for read_page intent — reads current page aloud."""
    return [{
        "tool": "page_context",
        "action": "read_page",
        "description": "Read current page aloud",
        "params": {}
    }]


def _infer_platform_from_song(song: str, prefs: Dict) -> Optional[str]:
    """Infer platform from song type if known."""
    song_lower = song.lower()
    # Podcasts → Spotify
    if any(w in song_lower for w in ["podcast", "episode", "show"]):
        return "spotify"
    # Music videos → YouTube
    if any(w in song_lower for w in ["music video", "official video", "mv"]):
        return "youtube"
    # Albums → Spotify
    if any(w in song_lower for w in ["album", "playlist"]):
        return prefs.get("preferred_music_platform", "spotify")
    return None


def _plan_search_web(intent: Dict, prefs: Dict) -> List[Dict]:
    query    = intent["entities"].get("query", "")
    platform = intent["entities"].get("platform", "")

    # ── MEMORY-DRIVEN SEARCH ENGINE ───────────────────────────────────────
    if not platform:
        platform = prefs.get("preferred_search_engine", "google")
        logger.info(f"📝 Search engine from memory: '{platform}'")

    return [_step(
        "search_web", "browser",
        {"query": query, "platform": platform},
        f"Search '{query[:30]}' on {platform}",
        retries=1
    )]


def _plan_open_website(intent: Dict, prefs: Dict) -> List[Dict]:
    url = intent["entities"].get("url", "")
    # Memory: check if user prefers a specific browser
    browser = prefs.get("preferred_browser", "default")
    return [_step(
        "open_website", "browser",
        {"url": url, "browser": browser},
        f"Open {url}",
        retries=1, verify={"type": "browser_opened"},
        duration_ms=2000
    )]


def _plan_type_text(intent: Dict, prefs: Dict) -> List[Dict]:
    text = intent["entities"].get("text", "")
    return [_step("type_text", "keyboard", {"text": text}, f"Type: '{text[:40]}'")]


def _plan_close_tab(intent: Dict, prefs: Dict) -> List[Dict]:
    tab = intent["entities"].get("tab_name")
    return [_step(
        "close_tab", "browser", {"tab_name": tab},
        f"Close {'tab: '+tab if tab else 'current tab'}"
    )]


def _plan_deep_research(intent: Dict, prefs: Dict) -> List[Dict]:
    topic  = intent["entities"].get("topic", intent.get("original_text", ""))
    fmt    = intent["entities"].get("output_format", "spoken")
    # Memory: user might prefer detailed reports vs spoken summaries
    fmt    = prefs.get("preferred_research_format", fmt)
    num    = int(prefs.get("preferred_research_depth", 3))
    return [
        _step("search_web", "web_navigator",
              {"query": topic, "platform": "google", "num_results": num},
              f"Search: '{topic}'", retries=2, duration_ms=3000),
        _step("fetch_and_parse", "web_navigator",
              {"max_pages": num}, "Fetch and parse top results",
              retries=1, duration_ms=5000, depends_on=[0]),
        _step("synthesize_research", "ai_brain",
              {"topic": topic, "output_format": fmt},
              "Synthesize findings", retries=1, duration_ms=3000, depends_on=[0, 1]),
    ]


def _plan_make_call(intent: Dict, prefs: Dict) -> List[Dict]:
    contact  = intent["entities"].get("contact", "")
    platform = intent["entities"].get("platform")

    # ── MEMORY-DRIVEN COMMUNICATION PLATFORM ─────────────────────────────
    if not platform:
        platform = (
            prefs.get("preferred_call_platform") or
            prefs.get(f"preferred_platform_for_{contact.lower().replace(' ','_')}") or
            "discord"
        )
        logger.info(f"📝 Call platform from memory: '{platform}'")

    return [
        _step("open_app", "app_launcher", {"name": platform},
              f"Open {platform}", retries=2, fallback="open_website",
              verify={"type": "process_running", "name": platform}, duration_ms=3000),
        _step("initiate_call", "communicator", {"contact": contact, "platform": platform},
              f"Call {contact}", retries=1, duration_ms=5000, depends_on=[0]),
    ]


def _plan_send_message(intent: Dict, prefs: Dict) -> List[Dict]:
    contact  = intent["entities"].get("contact", "")
    platform = intent["entities"].get("platform")
    content  = intent["entities"].get("message_content", "")

    if not platform:
        platform = (
            prefs.get("preferred_message_platform") or
            prefs.get(f"preferred_platform_for_{contact.lower().replace(' ','_')}") or
            "whatsapp"
        )
        logger.info(f"📝 Message platform from memory: '{platform}'")

    return [
        _step("open_app", "app_launcher", {"name": platform},
              f"Open {platform}", retries=1, fallback="open_website",
              verify={"type": "process_running", "name": platform}, duration_ms=3000),
        _step("navigate_to_contact", "communicator", {"contact": contact, "platform": platform},
              f"Find {contact}", retries=1, duration_ms=2000, depends_on=[0]),
        _step("type_and_send", "keyboard", {"text": content},
              f"Send: '{content[:30]}'", retries=1, duration_ms=1000, depends_on=[0, 1]),
    ]


def _plan_quick_answer(intent: Dict, prefs: Dict) -> List[Dict]:
    query = intent["entities"].get("query", intent.get("original_text", ""))
    # Memory: user might prefer concise vs detailed answers
    detail = prefs.get("preferred_answer_length", "concise")
    return [_step(
        "answer_question", "ai_brain",
        {"query": query, "detail_level": detail},
        f"Answer: '{query[:40]}'",
        retries=1, fallback="search_web", duration_ms=2000
    )]


def _plan_notepad_write(intent: Dict, prefs: Dict) -> List[Dict]:
    content  = intent["entities"].get("text", intent["entities"].get("content", ""))
    filename = intent["entities"].get("filename", "note.txt")
    return [
        _step("open_app", "app_launcher", {"name": "notepad"}, "Open Notepad",
              retries=1, verify={"type": "process_running", "name": "notepad"}, duration_ms=1500),
        _step("type_text", "keyboard", {"text": content}, "Type content",
              retries=1, duration_ms=500, depends_on=[0]),
        _step("save_file", "keyboard", {"filename": filename}, f"Save as {filename}",
              retries=0, duration_ms=500, depends_on=[0, 1]),
    ]


def _plan_screenshot(intent: Dict, prefs: Dict) -> List[Dict]:
    fname = intent["entities"].get("filename")
    save_loc = prefs.get("preferred_screenshot_location", None)
    return [_step(
        "take_screenshot", "system",
        {"filename": fname, "location": save_loc},
        "Take screenshot", retries=1
    )]


def _plan_system(intent: Dict, prefs: Dict, action: str) -> List[Dict]:
    return [_step(action, "system", {}, action.replace("_", " ").title(), retries=0)]


def _plan_scroll(intent: Dict, prefs: Dict) -> List[Dict]:
    direction = intent["entities"].get("direction", "down")
    amount    = int(prefs.get("preferred_scroll_amount", 500))
    return [_step("scroll", "keyboard", {"direction": direction, "amount": amount},
                  f"Scroll {direction}")]


def _plan_media_control(action: str) -> List[Dict]:
    return [_step(action, "media_controller", {}, action.replace("_", " ").title(), retries=0, duration_ms=200)]


def _plan_guided_recommendation(intent: Dict, prefs: Dict) -> List[Dict]:
    """Plan for guided_recommendation intent — handled by advisor in core."""
    category = intent["entities"].get("category", "general")
    return [_step(
        "guided_recommendation", "advisor",
        {"category": category, "query": intent.get("original_text", "")},
        f"Guide recommendation for {category}",
        retries=0, duration_ms=100
    )]


# ── INTENT → PLANNER MAP ─────────────────────────────────────────────────

def _make_registry():
    return {
        "open_app":         lambda i, p: _plan_open_app(i, p),
        "close_app":        lambda i, p: _plan_close_app(i, p),
        "focus_app":        lambda i, p: _plan_open_app(i, p),
        "minimize_app":     lambda i, p: [_step("minimize_app", "system", {}, "Minimize window", retries=0)],
        "maximize_app":     lambda i, p: [_step("maximize_app", "system", {}, "Maximize window", retries=0)],
        "play_media":       lambda i, p: _plan_play_media(i, p),
        "pause_media":      lambda i, p: _plan_media_control("pause_media"),
        "resume_media":     lambda i, p: _plan_media_control("resume_media"),
        "next_track":       lambda i, p: _plan_media_control("next_track"),
        "previous_track":   lambda i, p: _plan_media_control("previous_track"),
        "set_volume":       lambda i, p: [_step("set_volume", "system", {"level": i["entities"].get("level", 50)}, "Set volume", retries=0)],
        "search_web":       lambda i, p: _plan_search_web(i, p),
        "open_website":     lambda i, p: _plan_open_website(i, p),
        "close_tab":        lambda i, p: _plan_close_tab(i, p),
        "new_tab":          lambda i, p: [_step("new_tab", "browser", {}, "Open new tab", retries=0, duration_ms=300)],
        "scroll":           lambda i, p: _plan_scroll(i, p),
        "type_text":        lambda i, p: _plan_type_text(i, p),
        "open_notepad_write": lambda i, p: _plan_notepad_write(i, p),
        "save_file":        lambda i, p: [_step("save_file", "keyboard", {}, "Save file", retries=0)],
        "read_page":        lambda i, p: _plan_read_page(i, p),
        "deep_research":    lambda i, p: _plan_deep_research(i, p),
        "quick_answer":     lambda i, p: _plan_quick_answer(i, p),
        "make_call":        lambda i, p: _plan_make_call(i, p),
        "send_message":     lambda i, p: _plan_send_message(i, p),
        "take_screenshot":  lambda i, p: _plan_screenshot(i, p),
        "lock":             lambda i, p: _plan_system(i, p, "lock"),
        "shutdown":         lambda i, p: _plan_system(i, p, "shutdown"),
        "restart":          lambda i, p: _plan_system(i, p, "restart"),
        "click_element":    lambda i, p: [_step("click_element", "browser", {"target": i["entities"].get("target","")}, "Click element", retries=1)],
        "greet":            lambda i, p: [_step("greet", "responder", {}, "Greet", retries=0, duration_ms=100)],
        "thank":            lambda i, p: [_step("acknowledge_thanks", "responder", {}, "Acknowledge thanks", retries=0, duration_ms=100)],
        "cancel":           lambda i, p: [_step("cancel_current", "system", {}, "Cancel", retries=0, duration_ms=100)],
        "unknown":          lambda i, p: _plan_quick_answer(i, p),
        
        # ── NEW INTENTS ───────────────────────────────────────────────────
        "smart_open":       lambda i, p: _plan_smart_open(i, p),
        "page_summary":     lambda i, p: _plan_page_summary(i, p),
        "guided_recommendation": lambda i, p: _plan_guided_recommendation(i, p),
    }


class PlanningEngine:
    """
    Memory-driven planning engine.

    Memory is injected at plan creation time — preferences actively
    shape platform choices, search engines, output formats, etc.
    """

    def __init__(self, config: Dict):
        self.config   = config
        self._registry = _make_registry()

    async def create_plan(self, intent, memory_context, context, think_hints=None) -> List[Dict]:
        """
        Create execution plan for the intent.
        Memory context MUST influence the plan.
        """
        intent_name = intent.get("intent", "unknown")

        # ── EXTRACT PREFERENCES FROM MEMORY ──────────────────────────────
        # This dict is passed to every plan builder — memory → planning
        prefs = self._extract_preferences(memory_context)
        logger.debug(f"Planning with {len(prefs)} memory preferences")

        # ── HANDLE THINKING HINTS ─────────────────────────────────────────
        if think_hints:
            intent["subtasks"] = think_hints.get("subtasks", [])
            if think_hints.get("subtask_count", 1) > 1:
                intent["_think_goal"] = think_hints.get("goal")
                intent["_think_criteria"] = think_hints.get("success_criteria")

        # ── BUILD PLAN VIA REGISTRY ───────────────────────────────────────
        builder = self._registry.get(intent_name)
        if builder:
            steps = builder(intent, prefs)
        else:
            logger.warning(f"No plan builder for: {intent_name} — falling back to quick_answer")
            steps = _plan_quick_answer(intent, prefs)

        # ── GLOBAL FALLBACK ───────────────────────────────────────────────
        if not steps:
            logger.warning(f"Empty plan for: {intent_name} — falling back to quick_answer")
            steps = _plan_quick_answer(intent, prefs)

        return steps

    def _extract_preferences(self, memory_context: Dict) -> Dict:
        """
        Flatten memory context into a simple preferences dict.
        This is what plan builders query.
        """
        prefs = {}

        for item in memory_context.get("preferences", []):
            key = item.get("key", "")
            val = item.get("value", "")
            if key and val:
                prefs[key] = val

        for item in memory_context.get("personal", []):
            key = item.get("key", "")
            val = item.get("value", "")
            if key and val:
                prefs[key] = val

        # Also extract from facts that look like preferences
        for item in memory_context.get("facts", []):
            key = item.get("key", "")
            val = item.get("value", "")
            if key and val and ("prefer" in key or "favorite" in key or "use" in key):
                prefs[key] = val

        return prefs