"""
RUNNER ADDITIONS — New Tool Classes
=====================================
Add these to executor/runner.py (or import from here).

Adds:
  1. SmartOpenTool   — "open Notion" → finds URL → opens it
  2. PageContextTool — "what is this site" / "read aloud" → extracts + summarizes page
  3. PATCH BrowserTool.close_tab → handles tab_name parameter

HOW TO INTEGRATE:
  Option A (simple): Copy these classes into runner.py and register them.
  Option B (clean):  Import this file in runner.py:
      from executor.runner_additions import SmartOpenTool, PageContextTool
      and register in ExecutionRunner._register_tools().

REGISTER IN ExecutionRunner._register_tools():
    self.registry["smart_open"] = SmartOpenTool(config)
    self.registry["page_context"] = PageContextTool(config)
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SmartOpenTool:
    """
    Opens a website by name when the exact URL is unknown.
    "open Notion" → searches DDG → finds notion.so → opens it
    "open Jarvis on GitHub" → finds github.com/... → opens it
    """

    def __init__(self, config: Dict):
        self.config = config

    async def execute(
        self, action: str, params: Dict,
        intent: Dict, context: Dict, step_results: List
    ) -> Dict:
        if action != "smart_open":
            raise ValueError(f"SmartOpenTool: unknown action {action}")

        query = params.get("query", "") or intent.get("entities", {}).get("query", "")
        if not query:
            raise ValueError("smart_open: no query provided")

        from agent.page_context import smart_open
        url, title = await smart_open(query)

        logger.info(f"🔗 Opened: {url} ({title})")
        return {
            "opened": url,
            "title": title,
            "query": query,
        }


class PageContextTool:
    """
    Screen/page aware tool.
    Supports:
      - read_page:     read current page aloud
      - page_summary:  "what is this site about"
      - extract_text:  raw extraction for other tools
    """

    def __init__(self, config: Dict):
        self.config = config
        self._groq_key = config.get("groq_api_key", "")
        self._speak_fn = None  # Set by core.py after init

    def set_speak_fn(self, fn):
        self._speak_fn = fn

    async def execute(
        self, action: str, params: Dict,
        intent: Dict, context: Dict, step_results: List
    ) -> Dict:
        from agent.page_context import (
            summarize_current_page, read_page_aloud, extract_page_text
        )

        if action == "page_summary":
            spoken, full = await summarize_current_page(
                groq_api_key=self._groq_key,
                context=context
            )
            return {
                "spoken_summary": spoken,
                "full_summary": full,
                "action": "page_summary"
            }

        elif action == "read_page":
            speak_fn = self._speak_fn or (lambda t: logger.info(f"[READ] {t}"))
            await read_page_aloud(
                speak_fn=speak_fn,
                groq_api_key=self._groq_key,
                context=context
            )
            return {"read": True, "action": "read_page"}

        elif action == "extract_page_text":
            text = await extract_page_text(max_chars=5000)
            return {"text": text, "action": "extract_page_text"}

        raise ValueError(f"PageContextTool: unknown action {action}")


# ── PATCHED BrowserTool CLOSE TAB ─────────────────────────────────────────
# Replace the close_tab section in your BrowserTool.execute() with this:

async def browser_close_tab_patched(params: Dict) -> Dict:
    """
    Handle close_tab with optional tab_name.
    
    No tab_name: Ctrl+W (closes current tab) - always works
    tab_name given: try to switch to that tab first, then Ctrl+W
    """
    import pyautogui
    import time

    tab_name = params.get("tab_name", "").strip().lower()

    if not tab_name:
        # No name — close current active tab
        pyautogui.hotkey("ctrl", "w")
        return {"closed": "current_tab"}

    # Named tab: try Ctrl+Tab cycling to find it
    # Strategy: use Ctrl+L (address bar) to check URL, cycle up to 8 tabs
    MAX_TABS = 8
    found = False

    for _ in range(MAX_TABS):
        # Get current tab URL/title
        try:
            import pyperclip
            pyautogui.hotkey("ctrl", "l")  # Focus address bar
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
            current_url = pyperclip.paste().lower()
            pyautogui.key("escape")  # Close address bar

            if tab_name in current_url:
                # Found it — close this tab
                pyautogui.hotkey("ctrl", "w")
                found = True
                break
        except Exception:
            pass

        # Move to next tab
        pyautogui.hotkey("ctrl", "tab")
        time.sleep(0.15)

    if not found:
        # Couldn't find named tab — close current
        pyautogui.hotkey("ctrl", "w")
        return {
            "closed": "current_tab",
            "note": f"Couldn't find {tab_name} tab, closed current"
        }

    return {"closed": tab_name}


# ── PLANNER ADDITIONS ─────────────────────────────────────────────────────
# Add these to planner/engine.py's intent → plan mapping:

ADDITIONAL_PLAN_TEMPLATES = {
    "smart_open": [
        {
            "tool": "smart_open",
            "action": "smart_open",
            "description": "Find and open website",
            "params": {},  # query filled from entities
            "retry_policy": {"max_retries": 1, "fallback": "search_web"}
        }
    ],
    "page_summary": [
        {
            "tool": "page_context",
            "action": "page_summary",
            "description": "Summarize current page",
            "params": {}
        }
    ],
    "read_page": [
        {
            "tool": "page_context",
            "action": "read_page",
            "description": "Read current page aloud",
            "params": {}
        }
    ],
}


# ── RESPONSE ENGINE ADDITIONS ─────────────────────────────────────────────
# Add to response/engine.py's SUCCESS_TEMPLATES and handling:

ADDITIONAL_RESPONSE_TEMPLATES = {
    "smart_open":   "Opening {title}",
    "page_summary": "Here's what I found, Sir",
    "read_page":    "Reading now",
    "guided_recommendation": "Here are my recommendations, Sir",
}

# In ResponseEngine.generate(), add this handling:
"""
if intent_name == "page_summary":
    for r in results:
        out = r.get("output", {})
        if isinstance(out, dict) and "spoken_summary" in out:
            spoken = out["spoken_summary"]
            full   = out.get("full_summary", spoken)
            return {"full_response": full, "spoken_response": spoken}

if intent_name == "read_page":
    return {"full_response": "Reading complete.", "spoken_response": ""}

if intent_name == "smart_open":
    for r in results:
        out = r.get("output", {})
        if isinstance(out, dict) and "title" in out:
            msg = f"Opening {out['title']}, Sir."
            return {"full_response": msg, "spoken_response": msg}
"""
