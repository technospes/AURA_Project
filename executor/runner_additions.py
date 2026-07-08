"""
RUNNER ADDITIONS — Smart Open + Page Context Tools
===================================================
These tools require agent-level wiring (TTS callback, full config)
that the basic ToolRegistry cannot provide at creation time.

They are instantiated in agent/core.py._init_modules() and injected
into the ToolRegistry so runner.py's _create_tool() is bypassed.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseTool:
    async def execute(self, action: str, params: Dict,
                      intent: Dict, context: Dict, step_results: List) -> Any:
        raise NotImplementedError


# ── SMART OPEN TOOL ────────────────────────────────────────────────────────

class SmartOpenTool(BaseTool):
    """Find and open a website/app by name with web fallback."""

    def __init__(self, config: Dict):
        self.config = config

    async def execute(self, action, params, intent, context, step_results):
        if action != "smart_open":
            raise ValueError(f"SmartOpenTool: unknown action {action}")

        query = params.get("query", "").strip()
        if not query:
            return {"success": False, "error": "No query provided."}

        # Try local app first
        from utils.app_locator import app_locator
        launched = await asyncio.to_thread(app_locator.launch, query)
        if launched:
            return {
                "success": True,
                "title": query,
                "message": f"Opening {query}, Sir."
            }

        # Fallback to web search
        import webbrowser
        import urllib.parse
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        webbrowser.open(url)
        return {
            "success": True,
            "title": query,
            "opened": url,
            "message": f"I couldn't find {query} locally, so I searched the web, Sir."
        }


# ── PAGE CONTEXT TOOL ──────────────────────────────────────────────────────

class PageContextTool(BaseTool):
    """
    Extract and speak/summarize the current browser page.

    Two actions:
      read_page    — grab page text, LLM-summarize, speak it
      page_summary — same but return structured dict for memory storage
    """

    def __init__(self, config: Dict):
        self.config = config
        self._groq_key = config.get("groq_api_key", "")
        self._speak_fn = None   # set by core.py after TTS is ready

    def set_speak_fn(self, fn):
        self._speak_fn = fn

    def _speak(self, text: str):
        """Safe speak: calls bound fn, falls back to logger."""
        if self._speak_fn:
            try:
                self._speak_fn(text)
            except Exception as e:
                logger.error(f"[PageContextTool] speak failed: {e}")
        else:
            logger.warning(f"[PageContextTool] No speak_fn bound — text: {text[:80]}")

    # ── CLIPBOARD EXTRACTION ───────────────────────────────────────────────

    def _grab_clipboard(self) -> str:
        """Ctrl+A → Ctrl+C → read clipboard. Returns raw text or empty string."""
        try:
            import pyautogui
            import pyperclip

            try:
                old = pyperclip.paste()
            except Exception:
                old = ""

            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.35)

            text = pyperclip.paste()

            # Restore previous clipboard contents
            try:
                if old:
                    pyperclip.copy(old)
            except Exception:
                pass

            return text if (text and text != old) else ""
        except Exception as e:
            logger.warning(f"[PageContextTool] clipboard grab failed: {e}")
            return ""

    # ── TEXT CLEANING ──────────────────────────────────────────────────────

    def _clean(self, raw: str, max_chars: int = 4000) -> str:
        """
        Line-by-line filter to remove nav/UI boilerplate before sending to LLM.
        Removes: nav labels, button text, repeated items, lines < 4 chars.
        """
        _SKIP = re.compile(
            r'^(cookie|privacy policy|terms|sign in|log in|subscribe|'
            r'newsletter|advertisement|loading|skip to|navigation|'
            r'menu|search|home|about|contact|copyright|©|'
            r'accept|reject|allow|deny|close|dismiss|'
            r'[\[\]<>|•·▶►])',
            re.IGNORECASE
        )

        lines = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        kept = []
        seen: set = set()

        for ln in lines:
            ln = ln.strip()
            if not ln or len(ln) < 4:
                continue
            if _SKIP.match(ln):
                continue
            key = ln.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            kept.append(ln)

        return '\n'.join(kept)[:max_chars]

    # ── LLM SUMMARIZER ─────────────────────────────────────────────────────

    def _llm_summarize(self, cleaned_text: str, mode: str = "spoken") -> str:
        """
        Call Groq synchronously (called via run_in_executor in execute()).
        mode: "spoken" → 3-5 conversational sentences
              "full"   → structured brief with sections
        """
        if not self._groq_key:
            logger.warning("[PageContextTool] No groq_api_key — returning truncated text")
            return cleaned_text[:300]

        try:
            from groq import Groq
            client = Groq(api_key=self._groq_key)

            if mode == "spoken":
                system = "You are Jarvis. Be concise and natural. No bullet points."
                prompt = (
                    "The user asked you to read aloud what is on their screen. "
                    "Summarize the MAIN CONTENT in 3-5 natural spoken sentences. "
                    "Skip all navigation menus, button labels, and repeated UI text — "
                    "those have already been filtered. Focus only on the core article, "
                    "product, event, or page subject matter.\n\n"
                    f"PAGE CONTENT:\n{cleaned_text}"
                )
                max_tokens = 220
            else:
                system = "You are Jarvis, a precise analyst."
                prompt = (
                    "Summarize this webpage content in 2-3 concise sentences "
                    "covering: what it is, what it's about, and who it's for.\n\n"
                    f"PAGE CONTENT:\n{cleaned_text}"
                )
                max_tokens = 180

            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"[PageContextTool] LLM summarize failed: {e}")
            return cleaned_text[:300]

    # ── MAIN EXECUTE ───────────────────────────────────────────────────────

    async def execute(self, action, params, intent, context, step_results):
        loop = asyncio.get_event_loop()

        # Step 1: grab page text
        raw = await loop.run_in_executor(None, self._grab_clipboard)

        if not raw or len(raw.strip()) < 50:
            msg = "I couldn't read the page content. Please make sure a browser window is focused, Sir."
            if action == "read_page":
                self._speak(msg)
            return {"success": False, "error": msg}

        # Step 2: clean
        cleaned = self._clean(raw)

        if action == "read_page":
            # Summarize then speak
            spoken = await loop.run_in_executor(None, self._llm_summarize, cleaned, "spoken")
            self._speak(spoken)
            return {
                "success": True,
                "spoken": spoken,
                "raw_length": len(raw),
            }

        elif action == "page_summary":
            summary = await loop.run_in_executor(None, self._llm_summarize, cleaned, "full")
            spoken = f"Here's a summary: {summary}"
            self._speak(spoken)
            return {
                "success": True,
                "full_summary": summary,
                "spoken_summary": spoken,
                "url": context.get("active_url", ""),
            }

        raise ValueError(f"PageContextTool: unknown action '{action}'")


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