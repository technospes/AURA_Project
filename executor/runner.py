"""
EXECUTION RUNNER — Step-by-Step Executor with Retry + Verification
===================================================================
Runs each plan step using the appropriate tool.
If a step fails, applies the retry policy and fallback strategy.
If verification fails, reports failure clearly.

Tools available:
  app_launcher    → open/close apps
  browser         → tabs, URLs, scrolling
  media_controller → play/pause/skip
  keyboard        → typing, hotkeys
  web_navigator   → fetch + parse pages
  ai_brain        → answer questions, synthesize
  communicator    → calls, messages
  system          → screenshot, shutdown, lock
  memory          → store/recall facts
  tts             → speak text aloud
  responder       → greeting, cancel
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Lazy-loads tool implementations on first use."""

    def __init__(self, config: Dict):
        self.config = config
        self._tools: Dict[str, Any] = {}

    def get(self, tool_name: str) -> Any:
        if tool_name not in self._tools:
            self._tools[tool_name] = self._create_tool(tool_name)
        return self._tools[tool_name]

    def _create_tool(self, name: str) -> Any:
        if name == "app_launcher":
            return AppLauncherTool()
        elif name == "system_controller":
            return SystemControllerTool()
        elif name == "browser":
            return BrowserTool()
        elif name == "media_controller":
            return MediaControllerTool()
        elif name == "keyboard":
            return KeyboardTool()
        elif name == "web_navigator":
            return WebNavigatorTool()
        elif name == "ai_brain":
            return AIBrainTool(self.config.get("groq_api_key", ""))
        elif name == "communicator":
            return CommunicatorTool()
        elif name == "system":
            return SystemTool()
        elif name == "memory":
            return MemoryTool()
        elif name == "tts":
            return TTSTool()
        elif name == "responder":
            return ResponderTool()
        elif name == "browser_reader":
            return BrowserReaderTool()
        else:
            raise ValueError(f"Unknown tool: {name}")


class ExecutionRunner:
    """
    Executes a plan step-by-step.
    Handles dependencies, retries, fallbacks, and verification.
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.registry = ToolRegistry(config)
        self._step_results: List[Dict] = []
        from executor.validator import ExecutionValidator
        self.exec_validator = ExecutionValidator()
        
    async def run_plan(
        self,
        plan: List[Dict],
        intent: Dict,
        context: Dict
    ) -> List[Dict]:
        """
        Execute all steps in the plan.

        Returns list of result dicts, one per step.
        """
        self._step_results = []
        total = len(plan)

        for i, step in enumerate(plan):
            logger.info(f"  ▶ Step {i+1}/{total}: {step['description']}")

            # Check dependencies
            if not self._deps_satisfied(step, i):
                result = {
                    "step": i,
                    "action": step["action"],
                    "success": False,
                    "error": "Dependency step failed",
                    "output": None,
                    "duration_ms": 0
                }
                self._step_results.append(result)
                logger.warning(f"  ✗ Step {i+1} skipped (dependency failed)")
                continue

            # Execute with retry
            result = await self._execute_with_retry(step, i, intent, context)
            self._step_results.append(result)

            if result["success"]:
                logger.info(f"  ✓ Step {i+1} completed in {result['duration_ms']:.0f}ms")
            else:
                logger.warning(f"  ✗ Step {i+1} failed: {result.get('error', 'unknown')}")

        return self._step_results

    async def _execute_with_retry(
        self, step: Dict, step_idx: int, intent: Dict, context: Dict
    ) -> Dict:
        """Execute a single step with retry policy."""
        retry_policy = step.get("retry_policy", {})
        max_retries = retry_policy.get("max_retries", 1)
        fallback_action = retry_policy.get("fallback")

        last_error = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.info(f"  🔄 Retry {attempt}/{max_retries}")
                await asyncio.sleep(0.5)

            try:
                result = await self._execute_step(step, step_idx, intent, context)
                if result["success"]:
                    return result
                last_error = result.get("error", "unknown")

            except Exception as e:
                last_error = str(e)
                logger.error(f"  Step error: {e}")

        # Retries exhausted — try fallback
        if fallback_action:
            logger.info(f"  ↩ Trying fallback: {fallback_action}")
            fallback_step = dict(step)
            fallback_step["action"] = fallback_action
            fallback_step["retry_policy"] = {"max_retries": 0}

            if fallback_action == "open_website":
                fallback_step["tool"] = "browser"
                from executor.validator import ExecutionValidator
                val = ExecutionValidator()
                name = fallback_step["params"].get("name", "")
                song = fallback_step["params"].get("name", "")
                platform = fallback_step["params"].get("platform", "")
                _, category = val.resolve_app(name)
                url = val.build_fallback_url(
                    name, category, song=song, platform=platform
                )
                fallback_step["params"] = {"url": url}
                logger.info(f"🔀 Smart fallback URL: {url}")

            try:
                return await self._execute_step(fallback_step, step_idx, intent, context)
            except Exception as e:
                last_error = f"Fallback also failed: {e}"

        return {
            "step": step_idx,
            "action": step["action"],
            "success": False,
            "error": last_error,
            "output": None,
            "duration_ms": 0
        }

    def _verify_web(self, target):
        import pygetwindow as gw

        target = target.lower()
        target_words = target.split()
        titles = [t.lower() for t in gw.getAllTitles() if t.strip()]

        for t in titles:
            # strong match
            if target in t:
                return True

            # partial match (all words must match)
            if all(word in t for word in target_words):
                return True

        return False

    async def _execute_step(
        self, step: Dict, step_idx: int, intent: Dict, context: Dict
    ) -> Dict:
        """Execute one step using the appropriate tool."""
        start = time.perf_counter()
        tool_name = step.get("tool", "system")
        action = step["action"]

        # Merge step params with previous step outputs
        params = dict(step.get("params", {}))
        params = self._inject_previous_outputs(params, step_idx)

        try:
            tool = self.registry.get(tool_name)
            output = await tool.execute(action=action, params=params,
                                        intent=intent, context=context,
                                        step_results=self._step_results)

            # ── THE FIX: Respect the tool's explicit success/failure! ──
            tool_succeeded = True
            if isinstance(output, dict) and "success" in output:
                tool_succeeded = output["success"]

            # Verify if verification is specified AND the tool didn't already fail
            verified = tool_succeeded
            if verified and step.get("verify"):
                verified = await self._verify(step["verify"], output)
                if not verified:
                    logger.warning(f"  ⚠ Verification failed for: {action}")

            duration_ms = (time.perf_counter() - start) * 1000
            
            # Use the explicit error from the tool if it failed
            error_msg = None
            if not verified:
                error_msg = output.get("error", "Verification failed") if isinstance(output, dict) else "Action failed"

            return {
                "step": step_idx,
                "action": action,
                "success": verified,
                "output": output,
                "duration_ms": duration_ms,
                "error": error_msg
            }

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            return {
                "step": step_idx,
                "action": action,
                "success": False,
                "output": None,
                "duration_ms": duration_ms,
                "error": str(e)
            }

    def _deps_satisfied(self, step: Dict, current_idx: int) -> bool:
        """Check if all dependency steps succeeded."""
        deps = step.get("depends_on", [])
        for dep_idx in deps:
            if dep_idx >= len(self._step_results):
                return False
            if not self._step_results[dep_idx].get("success", False):
                return False
        return True

    def _inject_previous_outputs(self, params: Dict, step_idx: int) -> Dict:
        """Replace 'step_N_result' placeholders with actual outputs."""
        result = {}
        for k, v in params.items():
            if isinstance(v, str) and v.startswith("step_") and "result" in v:
                try:
                    ref_idx = int(v.split("_")[1])
                    if ref_idx < len(self._step_results):
                        result[k] = self._step_results[ref_idx].get("output", v)
                    else:
                        result[k] = v
                except (ValueError, IndexError):
                    result[k] = v
            else:
                result[k] = v
        return result

    async def _verify(self, verify_config: Dict, output: Any) -> bool:
        verify_type = verify_config.get("type")

        if verify_type == "process_running":
            name = verify_config.get("name", "")
            return self._is_process_running(name)

        elif verify_type == "process_not_running":
            name = verify_config.get("name", "")
            return not self._is_process_running(name)

        elif verify_type == "browser_opened":
            return self._is_browser_open()

        elif verify_type == "web_opened":
            target = verify_config.get("target", "")
            return self._verify_web(target)

        elif verify_type == "output_not_empty":
            return bool(output)

        return True

    def _is_process_running(self, name: str) -> bool:
        try:
            import psutil
            name_lower = name.lower()
            for proc in psutil.process_iter(["name"]):
                if name_lower in proc.info["name"].lower():
                    return True
            return False
        except Exception:
            return True  # Assume OK if we can't check

    def _is_browser_open(self) -> bool:
        browsers = ["chrome.exe", "firefox.exe", "msedge.exe", "brave.exe"]
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"].lower() in browsers:
                    return True
            return False
        except Exception:
            return True


# ── TOOL IMPLEMENTATIONS ────────────────────────────────────────────────────

class BaseTool:
    async def execute(self, action: str, params: Dict,
                      intent: Dict, context: Dict, step_results: List) -> Any:
        raise NotImplementedError

from utils.app_locator import app_locator
import asyncio
import logging
import os

class AppLauncherTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        app_name = params.get("name", params.get("app", "")).strip()
        logger = logging.getLogger(__name__)

        if not app_name:
            return {"success": False, "error": "No application name provided."}

        # ── 1. OPEN APP (With Self-Healing Fallback) ──
        if action == "open_app":
            logger.info(f"[app_launcher] Resolved app_name: {app_name}")
            launched = await asyncio.to_thread(app_locator.launch, app_name)
            
            if launched:
                return {"success": True, "message": f"Opening {app_name}, Sir."}
            else:
                import urllib.parse
                safe_query = urllib.parse.quote(app_name)
                # ── PRO FIX: Provide explicit fallback instructions for the Reflection Engine ──
                return {
                    "success": False, 
                    "error": f"Could not find {app_name} installed on this system.",
                    "fallback": {
                        "action": "open_website",
                        "params": {"url": f"https://www.google.com/search?q={safe_query}"}
                    }
                }

        # ── 2. CLOSE APP (psutil + taskkill) ──
        elif action in ("close_app", "force_kill"):
            logger.info(f"[app_launcher] Attempting to close app: {app_name}")
            closed = False
            app_lower = app_name.lower().replace(" ", "")

            # PRO FIX Step A: Try psutil for precise, case-insensitive process matching
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        p_name = proc.info['name'].lower()
                        if p_name and (app_lower in p_name or p_name.startswith(app_lower)):
                            proc.terminate()
                            proc.wait(timeout=3)
                            closed = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                        continue
            except ImportError:
                logger.warning("[app_launcher] 'psutil' not installed. Falling back to taskkill.")

            # PRO FIX Step B: Ultimate fallback to Windows taskkill
            if not closed:
                exe_name = f"{app_lower}.exe"
                res = os.system(f'taskkill /F /IM "{exe_name}" /T')
                
                # If exact exe fails, try the raw name
                if res != 0:
                    res = os.system(f'taskkill /F /IM "{app_name}.exe" /T')
                
                closed = (res == 0)

            if closed:
                return {"success": True, "message": f"Closed {app_name}, Sir."}
            else:
                return {"success": False, "error": f"Could not close {app_name}. It might not be running."}

        raise ValueError(f"AppLauncherTool: unknown action {action}")
    
class BrowserTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import webbrowser
        import pyautogui

        if action == "open_website":
            from executor.validator import ExecutionValidator
            url = ExecutionValidator().clean_url(params.get("url", ""))
            if not url:
                raise ValueError("No URL provided for open_website")
            webbrowser.open(url)
            return {"opened": url}

        elif action == "close_tab":
            pyautogui.hotkey("ctrl", "w")
            return {"closed": "current_tab"}

        elif action == "new_tab":
            pyautogui.hotkey("ctrl", "t")
            return {"opened": "new_tab"}

        elif action == "search_web":
            query = params.get("query", "")
            platform = params.get("platform", "google")
            from urllib.parse import quote_plus
            urls = {
                "google": f"https://www.google.com/search?q={quote_plus(query)}",
                "youtube": f"https://www.youtube.com/results?search_query={quote_plus(query)}",
                "bing": f"https://www.bing.com/search?q={quote_plus(query)}",
                "reddit": f"https://www.reddit.com/search/?q={quote_plus(query)}",
            }
            url = urls.get(platform, urls["google"])
            webbrowser.open(url)
            return {"searched": query, "platform": platform}

        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = -500 if direction == "down" else 500
            pyautogui.scroll(amount)
            return {"scrolled": direction}

        raise ValueError(f"BrowserTool: unknown action {action}")


class MediaControllerTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        if action in ("play_media", "play_hybrid", "play"):
            platform = params.get("platform", "spotify").lower()
            query = params.get("query", params.get("song", ""))

            import webbrowser
            import time
            import pyautogui

            if platform == "youtube":
                logger.info(f"[youtube] Scraping native YouTube video ID for: {query}")
                try:
                    import urllib.request
                    import urllib.parse
                    import re
                    
                    # 100% Reliable Native YouTube Scraper
                    safe_query = urllib.parse.quote(query)
                    html = urllib.request.urlopen(f"https://www.youtube.com/results?search_query={safe_query}")
                    video_ids = re.findall(r"watch\?v=(\S{11})", html.read().decode())
                    
                    if video_ids:
                        # Grabbing the first exact video ID and launching it
                        video_url = f"https://www.youtube.com/watch?v={video_ids[0]}"
                        logger.info(f"[youtube] Autoplaying: {video_url}")
                        webbrowser.open(video_url)
                        return {"status": "success", "message": f"Playing {query} on YouTube now, Sir."}
                except Exception as e:
                    logger.warning(f"Native YouTube scrape failed: {e}")
                
                # Fallback
                safe_song = query.replace(" ", "+")
                webbrowser.open(f"https://www.youtube.com/results?search_query={safe_song}")
                return {"status": "success", "message": f"I have pulled up the search results for {query} on YouTube, Sir."}

            elif platform == "spotify":
                import urllib.parse
                safe_song = urllib.parse.quote(query)
                uri = f"spotify:search:{safe_song}"
                
                logger.info(f"[spotify] Launching URI: {uri}")
                webbrowser.open(uri)
                
                # Give Spotify a little more time to fully load the search page
                time.sleep(3.5) 
                
                try:
                    # Tab twice to reach the "Top Result" Play button, then press Enter
                    pyautogui.press('tab', presses=2, interval=0.1)
                    pyautogui.press('enter')
                    return {"status": "success", "message": f"Playing {query} on Spotify now, Sir."}
                except Exception:
                    return {"status": "partial", "message": f"I opened Spotify to {query}, but couldn't force playback."}

        raise ValueError(f"MediaControllerTool: unknown action {action}")

    def _verify_audio_is_playing(self) -> bool:
        """
        Check if the system is actually outputting audio.
        (e.g., using pycaw to check Windows Audio Sessions)
        """
        # Implement your verification logic here
        return False

class SystemControllerTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        
        if action in ("close_app", "close_tab"):
            raw = intent.get("raw_input", "").lower()
            logger.info("[system] Firing close hotkeys")
            
            if "tab" in raw:
                pyautogui.hotkey('ctrl', 'w')
                return {"status": "success", "message": "Tab closed, Sir."}
            else:
                pyautogui.hotkey('alt', 'f4')
                return {"status": "success", "message": "Window closed, Sir."}

        raise ValueError(f"SystemControllerTool: unknown action {action}")

class KeyboardTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        import time as _time

        if action == "type_text":
            text = params.get("text", "")
            _time.sleep(0.2)
            pyautogui.write(text, interval=0.03)
            return {"typed": text[:50]}

        elif action == "save_file":
            filename = params.get("filename")
            pyautogui.hotkey("ctrl", "s")
            _time.sleep(0.5)
            if filename:
                pyautogui.write(filename, interval=0.03)
                pyautogui.press("enter")
            return {"saved": filename or "current_file"}

        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = -500 if direction == "down" else 500
            pyautogui.scroll(amount)
            return {"scrolled": direction}

        raise ValueError(f"KeyboardTool: unknown action {action}")


class WebNavigatorTool(BaseTool):
    def __init__(self):
        from src.web_navigation import AutonomousWebNavigator
        self._nav = AutonomousWebNavigator()

    async def execute(self, action, params, intent, context, step_results):
        loop = asyncio.get_event_loop()

        if action == "search_web":
            query = params.get("query", "")
            num = params.get("num_results", 3)
            result = await loop.run_in_executor(
                None, self._nav.search_and_navigate, query, num
            )
            return result

        elif action == "fetch_and_parse":
            # Use results from previous search step
            prev = next((r.get("output") for r in step_results if r.get("action") == "search_web"), {})
            pages = prev.get("results", []) if prev else []
            return {"pages": pages, "count": len(pages)}

        elif action == "synthesize_research":
            topic = params.get("topic", "")
            pages = []
            for r in step_results:
                if r.get("action") in ("fetch_and_parse", "search_web"):
                    out = r.get("output", {})
                    pages.extend(out.get("results", []) or out.get("pages", []))

            content = "\n\n".join(
                f"Source: {p.get('title', '')}\n{p.get('content', '')[:500]}"
                for p in pages[:5]
            )
            return {"topic": topic, "content": content, "sources": len(pages)}

        raise ValueError(f"WebNavigatorTool: unknown action {action}")

class AIBrainTool(BaseTool):
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    async def execute(self, action, params, intent, context, step_results):
        if action == "answer_question":
            query = params.get("query", "")
            loop = asyncio.get_event_loop()

            def _call():
                import time as _t
                client = self._get_client()
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are Jarvis. Answer concisely and accurately."},
                        {"role": "user", "content": query}
                    ],
                    temperature=0.3,
                    max_tokens=400
                )

            resp = await loop.run_in_executor(None, _call)
            answer = resp.choices[0].message.content.strip()
            return {"answer": answer, "query": query}

        elif action == "synthesize_research":
            topic = params.get("topic", "")
            content = params.get("content", "")
            # Pull content from step results if not provided
            if not content:
                for r in step_results:
                    out = r.get("output", {})
                    if isinstance(out, dict) and "content" in out:
                        content = out["content"]
                        break

            loop = asyncio.get_event_loop()

            def _synth():
                client = self._get_client()
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are a research analyst. Synthesize the findings into a clear, concise summary."},
                        {"role": "user", "content": f"Topic: {topic}\n\nSources:\n{content[:4000]}\n\nProvide a structured summary with key findings."}
                    ],
                    temperature=0.3,
                    max_tokens=800
                )

            resp = await loop.run_in_executor(None, _synth)
            synthesis = resp.choices[0].message.content.strip()
            return {"synthesis": synthesis, "topic": topic}

        raise ValueError(f"AIBrainTool: unknown action {action}")


class CommunicatorTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui, time as _t

        if action == "navigate_to_contact":
            contact = params.get("contact", "")
            pyautogui.hotkey("ctrl", "f")
            _t.sleep(0.3)
            pyautogui.write(contact, interval=0.05)
            _t.sleep(0.5)
            pyautogui.press("enter")
            return {"navigated_to": contact}

        elif action == "type_and_send":
            text = params.get("text", "")
            _t.sleep(0.3)
            pyautogui.write(text, interval=0.03)
            _t.sleep(0.2)
            pyautogui.press("enter")
            return {"sent": text[:50]}

        elif action == "initiate_call":
            contact = params.get("contact", "")
            platform = params.get("platform", "discord")
            # This would need platform-specific automation
            # For now, navigates to the contact search
            pyautogui.hotkey("ctrl", "f")
            _t.sleep(0.3)
            pyautogui.write(contact, interval=0.05)
            return {"call_initiated": contact, "platform": platform}

        raise ValueError(f"CommunicatorTool: unknown action {action}")


class SystemTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import subprocess, pyautogui, time as _t

        if action == "close_current":
            raw = intent.get("raw_input", "").lower()
            if "tab" in raw:
                pyautogui.hotkey("ctrl", "w")
                return {"closed": True, "message": "Tab closed, Sir."}
            else:
                pyautogui.hotkey("alt", "f4")
                return {"closed": True, "message": "Window closed, Sir."}
            
        elif action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "30"], shell=True)
            return {"shutdown_scheduled": "30s"}

        elif action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "30"], shell=True)
            return {"restart_scheduled": "30s"}

        elif action == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return {"locked": True}

        elif action == "take_screenshot":
            import datetime, os
            filename = params.get("filename") or f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            desktop = os.path.join(os.path.expanduser("~"), "Desktop", filename)
            pyautogui.screenshot(desktop)
            return {"saved_to": desktop}

        elif action == "cancel_current":
            pyautogui.press("escape")
            return {"cancelled": True}

        raise ValueError(f"SystemTool: unknown action {action}")


class MemoryTool(BaseTool):
    def __init__(self):
        self._memory = None

    async def execute(self, action, params, intent, context, step_results):
        if self._memory is None:
            from memory.store import MemoryStore
            self._memory = MemoryStore({})

        if action == "store_memory":
            fact = params.get("fact", "")
            key = params.get("key", "user_fact")
            await self._memory.store(key=key, value=fact, category="fact", importance=0.7)
            return {"stored": fact}

        elif action == "recall_memory":
            query = params.get("query", "")
            result = await self._memory.recall(query, {}, {})
            return {"recalled": result}

        raise ValueError(f"MemoryTool: unknown action {action}")

class CommunicationTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        if action == "make_call":
            contact = params.get("contact", "")
            platform = params.get("platform", "whatsapp").lower()
            
            import pyautogui
            import time
            import subprocess

            if not contact:
                return {"status": "failed", "error": "I need a contact name to make a call, Sir."}

            if platform == "whatsapp":
                logger.info(f"[communication] Automating WhatsApp call to: {contact}")
                
                # 1. Open WhatsApp Native Desktop App via URI
                subprocess.Popen(["cmd", "/c", "start", "whatsapp://"], shell=True)
                time.sleep(4.0) # Wait for UI to load
                
                # 2. Search for the contact
                pyautogui.hotkey('ctrl', 'f')
                time.sleep(0.5)
                pyautogui.write(contact, interval=0.05)
                time.sleep(1.5) # Wait for search results to populate
                
                # 3. Select the top contact
                pyautogui.press('enter')
                time.sleep(1.0)
                
                # 4. Initiate Voice Call (WhatsApp Desktop Hotkey)
                pyautogui.hotkey('ctrl', 'shift', 'a')
                
                return {"status": "success", "message": f"Calling {contact} on WhatsApp, Sir."}

            return {"status": "failed", "error": f"I don't have calling automation set up for {platform} yet."}

        raise ValueError(f"CommunicationTool: unknown action {action}")
    
class TTSTool(BaseTool):
    def __init__(self):
        self._tts = None

    async def execute(self, action, params, intent, context, step_results):
        if self._tts is None:
            try:
                from src.voice_io import JarvisVoice
                self._tts = JarvisVoice()
            except Exception:
                self._tts = None

        if action == "tts_speak":
            source = params.get("source", "")
            text = params.get("text", "")

            # Pull text from a previous step if instructed
            if not text and source.startswith("step_"):
                try:
                    idx = int(source.split("_")[1])
                    if idx < len(step_results):
                        out = step_results[idx].get("output", {})
                        if isinstance(out, dict):
                            text = out.get("text", "") or out.get("content", "")
                        else:
                            text = str(out)
                except (ValueError, IndexError):
                    pass

            if text and self._tts:
                self._tts.speak(text)
            elif text:
                print(f"[Jarvis TTS] {text}")

            return {"spoken": text[:100] if text else ""}

        raise ValueError(f"TTSTool: unknown action {action}")


class ResponderTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        if action == "greet":
            return {"response": "Good to see you, Sir. How may I assist?"}
        elif action == "acknowledge_thanks":
            return {"response": "Always a pleasure, Sir."}
        return {"response": "Understood, Sir."}


class BrowserReaderTool(BaseTool):
    """Extracts visible text from the current browser page using OCR or accessibility APIs."""

    async def execute(self, action, params, intent, context, step_results):
        if action == "extract_page_text":
            # Attempt using pyautogui + clipboard
            try:
                import pyautogui, pyperclip, time as _t
                pyautogui.hotkey("ctrl", "a")
                _t.sleep(0.2)
                pyautogui.hotkey("ctrl", "c")
                _t.sleep(0.3)
                text = pyperclip.paste()
                return {"text": text[:3000] if text else "Could not extract page text."}
            except Exception as e:
                return {"text": f"Page extraction failed: {e}"}

        raise ValueError(f"BrowserReaderTool: unknown action {action}")
from executor.runner_patch import run_plan_patched, _execute_with_retry_patched
ExecutionRunner.run_plan = run_plan_patched
ExecutionRunner._execute_with_retry = _execute_with_retry_patched