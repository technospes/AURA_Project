import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from core.capability_registry import registry as cap_registry
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
        """Create tool instance - checks capability registry first."""
        if cap_registry.has(name):
            if name == "ai_brain":
                return AIBrainTool(self.config.get("groq_api_key", ""))
            if name == "smart_open":
                return SmartOpenTool(self.config)
            return cap_registry.create(name)
        
        if name == "page_context":
            class DynamicPageContextTool:
                def __init__(self, config):
                    self.api_key = config.get("groq_api_key", "")
                
                async def execute(self, action: str, params: Dict, intent: Dict = None, context: Dict = None, step_results: list = None) -> Dict:
                    screen_text = context.get("screen_text", "") if context else ""
                    if not screen_text:
                        return {"success": False, "error": "I cannot see any readable text on the screen right now, Sir."}
                        
                    if action == "read_page":
                        return {"success": True, "message": f"Here is the page content: {screen_text[:1000]}"}
                        
                    # 🟢 THE STREAMING FIX: Fast Async Page Summary
                    elif action == "page_summary":
                        from groq import AsyncGroq
                        import re
                        
                        client = AsyncGroq(api_key=self.api_key)
                        prompt = f"Briefly summarize this screen content in 2-3 natural spoken sentences:\n\n{screen_text[:3000]}"
                        
                        # Dynamically grab the TTS callback to stream audio
                        tts_callback = None
                        try:
                            import __main__
                            if hasattr(__main__, "agent"): 
                                tts_callback = __main__.agent._tts_callback
                        except Exception: pass
                        
                        try:
                            stream = await client.chat.completions.create(
                                model="llama-3.1-8b-instant",
                                messages=[{"role": "user", "content": prompt}],
                                stream=True, temperature=0.3, max_tokens=150
                            )
                            
                            full_response = ""
                            buffer = ""
                            boundary_regex = re.compile(r'(?<=[.!?\n])\s+')
                            
                            async for chunk in stream:
                                token = chunk.choices[0].delta.content
                                if token:
                                    buffer += token
                                    full_response += token
                                    
                                    # Split on sentence boundaries and send to TTS immediately
                                    if any(p in buffer for p in ['. ', '? ', '! ', '\n']):
                                        splits = boundary_regex.split(buffer)
                                        if len(splits) > 1:
                                            for sentence in splits[:-1]:
                                                clean = sentence.strip()
                                                if clean and tts_callback: 
                                                    tts_callback(clean)
                                            buffer = splits[-1]
                                            
                            if buffer.strip() and tts_callback:
                                tts_callback(buffer.strip())
                                
                            # Return a blank message so core.py doesn't repeat the whole summary
                            return {"success": True, "message": " ", "summary": full_response.strip()}
                            
                        except Exception as e:
                            return {"success": False, "error": f"Failed to summarize: {str(e)}"}
                            
                    return {"success": False, "error": f"Unknown page action: {action}"}
            
            return DynamicPageContextTool(self.config)
            
        raise ValueError(f"Unknown tool: {name}")

class ExecutionRunner:
    """Executes a plan step-by-step. Handles dependencies, retries, fallbacks, and verification."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.registry = ToolRegistry(config)
        self._step_results: List[Dict] = []
        from executor.validator import ExecutionValidator
        self.exec_validator = ExecutionValidator()
        
    async def run_plan(self, plan: List[Dict], intent: Dict, context: Dict) -> List[Dict]:
        self._step_results = []
        total = len(plan)

        for i, step in enumerate(plan):
            logger.info(f"  ▶ Step {i+1}/{total}: {step['description']}")

            if not self._deps_satisfied(step, i):
                result = {
                    "step": i, "action": step["action"], "success": False,
                    "error": "Dependency step failed", "output": None, "duration_ms": 0
                }
                self._step_results.append(result)
                logger.warning(f"   Step {i+1} skipped (dependency failed)")
                continue

            result = await self._execute_with_retry(step, i, intent, context)
            self._step_results.append(result)

            if result["success"]:
                logger.info(f"   Step {i+1} completed in {result['duration_ms']:.0f}ms")
            else:
                logger.warning(f"   Step {i+1} failed: {result.get('error', 'unknown')}")

        return self._step_results

    async def _execute_with_retry(self, step: Dict, step_idx: int, intent: Dict, context: Dict) -> Dict:
        retry_policy = step.get("retry_policy", {})
        max_retries = retry_policy.get("max_retries", 1)
        fallback_action = retry_policy.get("fallback")
        last_error = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.info(f"   Retry {attempt}/{max_retries}")
                await asyncio.sleep(0.5)

            try:
                result = await self._execute_step(step, step_idx, intent, context)
                if result["success"]:
                    return result
                last_error = result.get("error", "unknown")
            except Exception as e:
                last_error = str(e)
                logger.error(f"  Step error: {e}")

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
                url = val.build_fallback_url(name, category, song=song, platform=platform)
                fallback_step["params"] = {"url": url}
                logger.info(f" Smart fallback URL: {url}")

            try:
                return await self._execute_step(fallback_step, step_idx, intent, context)
            except Exception as e:
                last_error = f"Fallback also failed: {e}"

        return {"step": step_idx, "action": step["action"], "success": False, "error": last_error, "output": None, "duration_ms": 0}

    async def run_graph(self, plan: List[Dict], intent: Dict, context: Dict) -> Dict:
        from core.task_graph import TaskGraph
        graph = TaskGraph(plan)
        self._last_graph = graph
        while not graph.is_complete():
            ready_nodes = graph.get_ready_nodes()
            if not ready_nodes:
                pending = [n for n in graph.nodes.values() if n.state.value == "pending"]
                if pending and not ready_nodes:
                    logger.warning("[TaskGraph] Stuck - no ready nodes, some still pending")
                    break
                await asyncio.sleep(0.1)
                continue
            
            for node in ready_nodes:
                node.state = "running"
                step = {
                    "action": node.action, "tool": node.tool, "params": node.params,
                    "description": node.description, "retry_policy": {"max_retries": node.max_retries}
                }
                result = await self._execute_with_retry(step, 0, intent, context)
                
                if result["success"]:
                    graph.mark_completed(node.id, result)
                else:
                    graph.mark_failed(node.id, result.get("error", "unknown"))
            
            if hasattr(self, '_goal_manager') and self._goal_id:
                self._goal_manager.update_progress(
                    self._goal_id,
                    sum(1 for n in graph.nodes.values() if n.state.value == "completed"),
                    len(graph.nodes)
                )
        
        return {"success": graph.is_successful(), "summary": graph.summary(), "progress": graph.progress()}

    def _verify_web(self, target):
        import pygetwindow as gw
        target = target.lower()
        target_words = [w for w in target.split() if len(w) > 2]
        titles = [t.lower() for t in gw.getAllTitles() if t.strip()]

        for t in titles:
            if target in t: return True
            if target_words and all(word in t for word in target_words): return True
        return False

    async def _auto_verify(self, action: str, params: dict, output: dict) -> bool:
        try:
            contract = cap_registry.get_contract(action)
            if contract and contract.verify_fn:
                try:
                    contract_ok = await contract.verify_fn(output, params)
                    if contract_ok: return True
                    else:
                        logger.warning(f"[VERIFY] Contract failed for '{action}'")
                        return False
                except Exception as e:
                    logger.debug(f"[VERIFY] Contract verify_fn error: {e}")

            if action == "open_app":
                app_name = (params.get("name") or params.get("app") or params.get("app_name") or "").strip().lower()
                if not app_name: return True
                import asyncio as _aio
                for attempt in range(5):
                    if self._is_process_running(app_name): return True
                    try:
                        import pygetwindow as gw
                        if any(app_name in t.lower() for t in gw.getAllTitles() if t.strip()): return True
                    except Exception: pass
                    if attempt < 4: await _aio.sleep(0.5)
                logger.warning(f"[VERIFY] open_app '{app_name}' — process not detected after 2.5s")
                return False

            elif action == "open_website":
                url = params.get("url", "")
                if not url: return True
                try:
                    from urllib.parse import urlparse
                    import asyncio as _aio
                    domain = urlparse(url).netloc.replace("www.", "")
                    await _aio.sleep(1.5)
                    return self._verify_web(domain)
                except Exception:
                    return True

            elif action in ("close_app", "force_kill"):
                app_name = (params.get("name") or params.get("app") or "").strip().lower()
                if not app_name: return True
                import asyncio as _aio
                await _aio.sleep(0.5)
                still_running = self._is_process_running(app_name)
                if still_running:
                    logger.warning(f"[VERIFY] close_app '{app_name}' — process still running")
                return not still_running

            elif action == "take_screenshot":
                import os
                saved_to = output.get("saved_to", "") if isinstance(output, dict) else ""
                return os.path.isfile(saved_to) if saved_to else True

            elif action in ("play_media", "pause_media", "resume_media"):
                media_procs = ["spotify", "vlc", "chrome", "msedge", "firefox"]
                try:
                    import psutil
                    running = [p.info["name"].lower() for p in psutil.process_iter(["name"])]
                    return any(mp in r for mp in media_procs for r in running)
                except Exception:
                    return True

            return True

        except Exception as e:
            logger.debug(f"[VERIFY] auto_verify error for '{action}': {e} — trusting tool")
            return True

    async def _execute_step(self, step: Dict, step_idx: int, intent: Dict, context: Dict) -> Dict:
        start = time.perf_counter()
        tool_name = step.get("tool", "system")
        action = step["action"]

        params = dict(step.get("params", {}))
        params = self._inject_previous_outputs(params, step_idx)

        _STEP_TIMEOUTS: dict = {
            "open_app": 5.0, "close_app": 4.0, "force_kill": 3.0, "open_website": 6.0,
            "search_web": 8.0, "fetch_and_parse": 12.0, "synthesize_research": 10.0,
            "play_media": 6.0, "pause_media": 3.0, "resume_media": 3.0, "click_element_id": 2.0,
            "press_key": 1.0, "take_screenshot": 4.0, "type_text": 4.0, "answer_question": 15.0,
            "navigate_to_contact": 6.0, "type_and_send": 5.0, "make_call": 8.0, 
            "page_summary": 15.0, "read_page": 10.0, "smart_open": 7.0,
        }
        step_timeout = _STEP_TIMEOUTS.get(action, 8.0)

        try:
            tool = self.registry.get(tool_name)
            try:
                output = await asyncio.wait_for(
                    tool.execute(action=action, params=params, intent=intent, context=context, step_results=self._step_results),
                    timeout=step_timeout
                )
            except asyncio.TimeoutError:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.warning(f"  ⏱ TIMEOUT: [{tool_name}.{action}] exceeded {step_timeout:.0f}s")
                return {
                    "step": step_idx, "action": action, "tool": tool_name, "success": False, "output": None,
                    "duration_ms": duration_ms, "error": f"Action timed out after {step_timeout:.0f}s. Please try again.",
                    "timed_out": True,
                }

            tool_succeeded = True
            if isinstance(output, dict):
                if "success" in output: tool_succeeded = output["success"]
                elif "status" in output: tool_succeeded = output["status"] == "success"

            verified = tool_succeeded
            if verified and step.get("verify"):
                verified = await self._verify(step["verify"], output)
                if not verified: logger.warning(f"   Verify config failed for: {action}")

            _AUTO_VERIFY_ACTIONS = {"open_app", "open_website", "close_app", "force_kill", "take_screenshot", "play_media", "pause_media", "resume_media"}
            if verified and action in _AUTO_VERIFY_ACTIONS:
                auto_ok = await self._auto_verify(action, params, output or {})
                if not auto_ok:
                    logger.warning(f"   Auto-verify FAILED: '{action}' — real-world effect not detected")
                    verified = False

            duration_ms = (time.perf_counter() - start) * 1000
            error_msg = None
            if not verified:
                if isinstance(output, dict): error_msg = output.get("error") or output.get("message") or "Verification failed"
                else: error_msg = "Action did not produce expected result"

            return {
                "step": step_idx, "action": action, "tool": tool_name, "success": verified,
                "output": output, "duration_ms": duration_ms, "error": error_msg
            }

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            err_str = str(e).lower()
            if any(k in err_str for k in ("connection", "network", "timeout")): friendly = "Network issue. Check your connection."
            elif any(k in err_str for k in ("filenotfound", "not found", "no such")): friendly = "Application or file not found."
            elif "permission" in err_str: friendly = "Permission denied."
            else: friendly = str(e)
            logger.error(f"  Tool error [{tool_name}.{action}]: {e}", exc_info=True)
            return {"step": step_idx, "action": action, "tool": tool_name, "success": False, "output": None, "duration_ms": duration_ms, "error": friendly, "raw_error": str(e)}

    def _deps_satisfied(self, step: Dict, current_idx: int) -> bool:
        deps = step.get("depends_on", [])
        for dep_idx in deps:
            if dep_idx >= len(self._step_results): return False
            if not self._step_results[dep_idx].get("success", False): return False
        return True

    def _inject_previous_outputs(self, params: Dict, step_idx: int) -> Dict:
        result = {}
        for k, v in params.items():
            if isinstance(v, str) and v.startswith("step_") and "result" in v:
                try:
                    ref_idx = int(v.split("_")[1])
                    if ref_idx < len(self._step_results): result[k] = self._step_results[ref_idx].get("output", v)
                    else: result[k] = v
                except (ValueError, IndexError): result[k] = v
            else: result[k] = v
        return result

    async def _verify(self, verify_config: Dict, output: Any) -> bool:
        verify_type = verify_config.get("type")
        if verify_type == "process_running": return self._is_process_running(verify_config.get("name", ""))
        elif verify_type == "process_not_running": return not self._is_process_running(verify_config.get("name", ""))
        elif verify_type == "browser_opened": return self._is_browser_open()
        elif verify_type == "web_opened": return self._verify_web(verify_config.get("target", ""))
        elif verify_type == "output_not_empty": return bool(output)
        return True

    def _is_process_running(self, name: str) -> bool:
        try:
            import psutil
            name_lower = name.lower()
            for proc in psutil.process_iter(["name"]):
                if name_lower in proc.info["name"].lower(): return True
            return False
        except Exception: return True 

    def _is_browser_open(self) -> bool:
        browsers = ["chrome.exe", "firefox.exe", "msedge.exe", "brave.exe"]
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"].lower() in browsers: return True
            return False
        except Exception: return True


# ── TOOL IMPLEMENTATIONS ────────────────────────────────────────────────────

class BaseTool:
    async def execute(self, action: str, params: Dict, intent: Dict, context: Dict, step_results: List) -> Any:
        raise NotImplementedError

from utils.app_locator import app_locator
import os

class AppLauncherTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        app_name = params.get("name", params.get("app", "")).strip()
        if not app_name: return {"success": False, "error": "No application name provided."}

        if action == "open_app":
            logger.info(f"[app_launcher] Resolved app_name: {app_name}")
            launched = await asyncio.to_thread(app_locator.launch, app_name)
            
            if launched:
                try:
                    from agent.world_model import world as _world
                    _world.update(last_app=app_name, last_entity=app_name, last_intent="open_app")
                except Exception: pass
                return {"success": True, "message": f"Opening {app_name}, Sir."}
            else:
                import urllib.parse
                safe_query = urllib.parse.quote(app_name)
                return {
                    "success": False, 
                    "error": f"Could not find {app_name} installed on this system.",
                    "fallback": {"action": "open_website", "params": {"url": f"https://www.google.com/search?q={safe_query}"}}
                }

        elif action in ("close_app", "force_kill"):
            logger.info(f"[app_launcher] Attempting to close app: {app_name}")
            closed = False
            app_lower = app_name.lower().replace(" ", "")

            KNOWN_PROCESSES = {
                "vscode": "code.exe", "visualstudiocode": "code.exe", "visual studio": "devenv.exe",
                "spotify": "spotify.exe", "discord": "discord.exe", "whatsapp": "whatsapp.exe",
                "chrome": "chrome.exe", "firefox": "firefox.exe", "notepad": "notepad.exe",
                "calculator": "calculator.exe", "cmd": "cmd.exe", "powershell": "powershell.exe",
                "terminal": "windowsterminal.exe", "explorer": "explorer.exe", "task manager": "taskmgr.exe",
                "paint": "mspaint.exe",
            }
            
            target_processes = [exe for key, exe in KNOWN_PROCESSES.items() if key in app_lower or app_lower in key]
            if not target_processes:
                target_processes = [f"{app_lower}.exe", f"{app_lower.replace(' ', '')}.exe", f"{app_lower.split()[0] if ' ' in app_lower else app_lower}.exe"]

            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        p_name = proc.info['name'].lower()
                        for target in target_processes:
                            if target.lower() in p_name or p_name in target.lower():
                                proc.terminate()
                                try: proc.wait(timeout=3)
                                except psutil.TimeoutExpired: proc.kill()
                                closed = True
                                logger.info(f"[app_launcher] Terminated: {p_name}")
                                break
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired): continue
            except ImportError:
                logger.warning("[app_launcher] 'psutil' not installed. Falling back to taskkill.")

            if not closed:
                for exe_name in target_processes:
                    if os.system(f'taskkill /F /IM "{exe_name}" /T') == 0:
                        closed = True
                        logger.info(f"[app_launcher] taskkill success: {exe_name}")
                        break

            return {"success": True, "message": f"Closed {app_name}, Sir."} if closed else {"success": False, "error": f"Could not close {app_name}. It might not be running."}
            
        elif action == "focus_app":
            import pyautogui
            import win32gui
            import time as _time
            
            def callback(hwnd, windows):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd).lower()
                    if app_name.lower() in title: windows.append((hwnd, title))
                return True
            
            windows = []
            win32gui.EnumWindows(callback, windows)
            
            if windows:
                hwnd, title = windows[0]
                try:
                    placement = win32gui.GetWindowPlacement(hwnd)
                    if placement[1] == 2: 
                        win32gui.ShowWindow(hwnd, 9)
                        _time.sleep(0.3)
                    win32gui.SetForegroundWindow(hwnd)
                    _time.sleep(0.2)
                    return {"success": True, "message": f"Focused {title}", "window_title": title}
                except Exception as e:
                    logger.warning(f"[app_launcher] SetForegroundWindow failed: {e}")
            
            pyautogui.hotkey('alt', 'tab')
            return {"success": True, "message": "Cycled window focus (fallback)"}

        raise ValueError(f"AppLauncherTool: unknown action {action}")
    
class BrowserTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import webbrowser
        import pyautogui

        if action == "open_website":
            from executor.validator import ExecutionValidator
            url = ExecutionValidator().clean_url(params.get("url", ""))
            if not url: raise ValueError("No URL provided for open_website")
            webbrowser.open(url)
            try:
                from agent.world_model import world as _world
                _world.update(current_url=url, last_entity=url, last_intent="open_website")
            except Exception: pass
            return {"success": True, "opened": url}

        elif action == "close_tab":
            pyautogui.hotkey("ctrl", "w")
            return {"success": True, "closed": "current_tab"}

        elif action == "new_tab":
            pyautogui.hotkey("ctrl", "t")
            return {"success": True, "opened": "new_tab"}

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
            try:
                from agent.world_model import world as _world
                _world.update(current_url=url, last_entity=query, last_intent="search_web", active_app=platform)
            except Exception: pass
            return {"success": True, "searched": query, "platform": platform}

        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = -500 if direction == "down" else 500
            pyautogui.scroll(amount)
            return {"success": True, "scrolled": direction}

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
                    
                    safe_query = urllib.parse.quote(query)
                    html = urllib.request.urlopen(f"https://www.youtube.com/results?search_query={safe_query}")
                    video_ids = re.findall(r"watch\?v=(\S{11})", html.read().decode())
                    
                    if video_ids:
                        video_url = f"https://www.youtube.com/watch?v={video_ids[0]}"
                        logger.info(f"[youtube] Autoplaying: {video_url}")
                        webbrowser.open(video_url)
                        try:
                            from agent.world_model import world as _world
                            _world.update(last_song=query, last_artist=params.get("artist", ""), is_playing=True, active_app="youtube", last_intent="play_media", current_url=video_url)
                        except Exception: pass
                        return {"success": True, "status": "success", "message": f"Playing {query} on YouTube now, Sir."}
                except Exception as e:
                    logger.warning(f"Native YouTube scrape failed: {e}")
                
                safe_song = query.replace(" ", "+")
                webbrowser.open(f"https://www.youtube.com/results?search_query={safe_song}")
                return {"success": True, "status": "success", "message": f"I have pulled up the search results for {query} on YouTube, Sir."}

            elif platform == "spotify":
                import urllib.parse
                import subprocess
                import os
                
                safe_song = urllib.parse.quote(query)
                uri = f"spotify:search:{safe_song}"
                logger.info(f"[spotify] Launching URI: {uri}")

                try: os.startfile(uri)
                except AttributeError: subprocess.Popen(["cmd", "/c", "start", "", uri], shell=True)
                
                import pyautogui
                import time as _t
                _t.sleep(3.0) 
                
                pyautogui.press('tab')     
                _t.sleep(0.2)
                pyautogui.press('enter')   
                _t.sleep(0.2)
                pyautogui.press('enter')   
                
                try:
                    from agent.world_model import world as _world
                    _world.update(last_song=query, last_artist=params.get("artist", ""), is_playing=True, active_app=platform, last_intent="play_media")
                except Exception: pass
                return {"success": True, "status": "success", "message": f"Playing {query} on Spotify, Sir."}

        elif action == "pause_media":
            import pyautogui
            pyautogui.press("playpause")
            return {"success": True, "status": "success", "message": "Paused."}
            
        elif action == "resume_media":
            import pyautogui
            pyautogui.press("playpause")
            return {"success": True, "status": "success", "message": "Resumed."}
            
        elif action == "next_track":
            import pyautogui
            pyautogui.press("nexttrack")
            return {"success": True, "status": "success", "message": "Next track."}
            
        elif action == "previous_track":
            import pyautogui
            pyautogui.press("prevtrack")
            return {"success": True, "status": "success", "message": "Previous track."}

        raise ValueError(f"MediaControllerTool: unknown action {action}")

class SystemControllerTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        if action in ("close_app", "close_tab"):
            raw = intent.get("raw_input", "").lower()
            if "tab" in raw:
                pyautogui.hotkey('ctrl', 'w')
                return {"success": True, "status": "success", "message": "Tab closed, Sir."}
            else:
                pyautogui.hotkey('alt', 'f4')
                return {"success": True, "status": "success", "message": "Window closed, Sir."}
        raise ValueError(f"SystemControllerTool: unknown action {action}")

class KeyboardTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        import time as _time

        if action == "type_text":
            text = params.get("text", "")
            _time.sleep(0.2)
            pyautogui.write(text, interval=0.03)
            return {"success": True, "typed": text[:50]}
        elif action == "save_file":
            filename = params.get("filename")
            pyautogui.hotkey("ctrl", "s")
            _time.sleep(0.5)
            if filename:
                pyautogui.write(filename, interval=0.03)
                pyautogui.press("enter")
            return {"success": True, "saved": filename or "current_file"}
        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = -500 if direction == "down" else 500
            pyautogui.scroll(amount)
            return {"success": True, "scrolled": direction}

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
            return await loop.run_in_executor(None, self._nav.search_and_navigate, query, num)
        elif action == "fetch_and_parse":
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
            content = "\n\n".join(f"Source: {p.get('title', '')}\n{p.get('content', '')[:500]}" for p in pages[:5])
            return {"topic": topic, "content": content, "sources": len(pages)}
        raise ValueError(f"WebNavigatorTool: unknown action {action}")

class AIBrainTool(BaseTool):
    def __init__(self, api_key: str):
        self._api_key = api_key

    # 🟢 THE STREAMING FIX: Async Streaming LLM Utility
    async def _stream_and_speak(self, messages: list, tts_callback) -> str:
        """Stream LLM output, chunk by sentence, dispatch to TTS instantly."""
        from groq import AsyncGroq
        import re
        
        async_client = AsyncGroq(api_key=self._api_key)
        stream = await async_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            stream=True,
            temperature=0.3,
            max_tokens=400
        )
        
        full_response = ""
        buffer = ""
        boundary_regex = re.compile(r'(?<=[.!?\n])\s+')
        
        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                buffer += token
                full_response += token
                
                # Check for sentence boundary and dispatch to Audio Engine
                if any(p in buffer for p in ['. ', '? ', '! ', '\n']):
                    splits = boundary_regex.split(buffer)
                    if len(splits) > 1:
                        for sentence in splits[:-1]:
                            clean = sentence.strip()
                            if clean and tts_callback:
                                tts_callback(clean)
                        buffer = splits[-1]
        
        # Flush remaining buffer
        if buffer.strip() and tts_callback:
            tts_callback(buffer.strip())
        
        return full_response

    async def execute(self, action, params, intent, context, step_results):
        if action == "answer_question":
            query = params.get("query", "")

            try:
                from session_memory import session as _session
                messages = _session.inject_into_messages(
                    [{"role": "user", "content": query}],
                    active_app=context.get("active_app", "desktop"),
                )
            except Exception:
                messages = [
                    {"role": "system", "content": "You are Jarvis. Answer concisely and accurately."},
                    {"role": "user", "content": query},
                ]

            # Dynamically grab the global TTS callback
            tts_callback = None
            try:
                import __main__
                if hasattr(__main__, "agent") and hasattr(__main__.agent, "_tts_callback"):
                    tts_callback = __main__.agent._tts_callback
            except Exception:
                pass

            # Generates & Speaks streaming sentences at 300ms latency
            answer = await self._stream_and_speak(messages, tts_callback)
            
            # Returning " " (blank space) so core.py doesn't repeat the massive block at the end!
            return {"success": True, "message": " ", "answer": answer, "query": query}

        elif action == "synthesize_research":
            topic = params.get("topic", "")
            content = params.get("content", "")
            if not content:
                for r in step_results:
                    out = r.get("output", {})
                    if isinstance(out, dict) and "content" in out:
                        content = out["content"]
                        break

            messages=[
                {"role": "system", "content": "You are a research analyst. Synthesize the findings into a clear, concise summary."},
                {"role": "user", "content": f"Topic: {topic}\n\nSources:\n{content[:4000]}\n\nProvide a structured summary with key findings."}
            ]
            
            tts_callback = None
            try:
                import __main__
                if hasattr(__main__, "agent") and hasattr(__main__.agent, "_tts_callback"):
                    tts_callback = __main__.agent._tts_callback
            except Exception: pass
            
            synthesis = await self._stream_and_speak(messages, tts_callback)
            return {"success": True, "synthesis": synthesis, "message": " ", "topic": topic}

        raise ValueError(f"AIBrainTool: unknown action {action}")


class CommunicatorTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        import time
        if action in ("make_call", "initiate_call"):
            contact = params.get("contact", "").replace(".", "") 
            platform = params.get("platform", "discord").lower()
            if not contact: return {"success": False, "status": "failed", "error": "I need a contact name to make a call, Sir."}

            if platform == "discord":
                logger.info(f"[communicator] Automating Discord call to: {contact}")
                time.sleep(1.5) 
                pyautogui.hotkey('ctrl', 'k')
                time.sleep(0.5)
                pyautogui.write(contact, interval=0.05)
                time.sleep(1.0) 
                pyautogui.press('enter')
                time.sleep(1.0)
                pyautogui.hotkey('ctrl', "'")
                return {"success": True, "message": f"Dialing {contact} on Discord now, Sir."}

            return {"success": False, "status": "failed", "error": f"I don't have calling macros for {platform} yet."}
        raise ValueError(f"CommunicatorTool: unknown action {action}")


class SystemTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        import subprocess
        import pyautogui

        if action == "close_current":
            raw = intent.get("raw_input", "").lower()
            if "tab" in raw:
                pyautogui.hotkey("ctrl", "w")
                return {"success": True, "closed": True, "message": "Tab closed, Sir."}
            else:
                pyautogui.hotkey("alt", "f4")
                return {"success": True, "closed": True, "message": "Window closed, Sir."}
            
        elif action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "30"], shell=True)
            return {"success": True, "shutdown_scheduled": "30s"}
        elif action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "30"], shell=True)
            return {"success": True, "restart_scheduled": "30s"}
        elif action == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return {"success": True, "locked": True}
        elif action == "take_screenshot":
            import datetime
            import os
            filename = params.get("filename") or f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            def _get_desktop() -> str:
                try:
                    import ctypes
                    from ctypes import wintypes
                    CSIDL_DESKTOP = 0
                    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                    ctypes.windll.shell32.SHGetFolderPathW(0, CSIDL_DESKTOP, 0, 0, buf)
                    path = buf.value
                    if path and os.path.isdir(path): return path
                except Exception: pass
                onedrive = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
                if os.path.isdir(onedrive): return onedrive
                standard = os.path.join(os.path.expanduser("~"), "Desktop")
                os.makedirs(standard, exist_ok=True)
                return standard

            desktop = os.path.join(_get_desktop(), filename)
            pyautogui.screenshot(desktop)

            if not os.path.isfile(desktop):
                return {"success": False, "error": f"Screenshot capture failed — file not found at {desktop}"}

            logger.info(f"[take_screenshot] Saved to: {desktop}")
            short_name = os.path.basename(desktop)
            try:
                from agent.world_model import world as _world
                _world.update(last_intent="take_screenshot", last_entity=desktop)
            except Exception: pass
            return {
                "success": True,
                "saved_to": desktop,
                "message": f"Screenshot saved to your Desktop as {short_name}, Sir.",
            }
        elif action == "cancel_current":
            pyautogui.press("escape")
            return {"success": True, "cancelled": True}
        elif action == "minimize_app":
            pyautogui.hotkey("win", "d")
            return {"success": True, "minimized": True}
        elif action == "maximize_app":
            pyautogui.hotkey("win", "up")
            return {"success": True, "maximized": True}
        elif action == "set_volume":
            level = params.get("level", 50)
            pyautogui.press("volumemute") 
            for _ in range(min(int(level / 2), 50)): pyautogui.press("volumeup")
            return {"success": True, "volume": level}

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
            return {"success": True, "stored": fact}
        elif action == "recall_memory":
            query = params.get("query", "")
            result = await self._memory.recall(query, {}, {})
            return {"success": True, "recalled": result}
        raise ValueError(f"MemoryTool: unknown action {action}")

class SmartOpenTool(BaseTool):
    def __init__(self, config: Dict):
        self.config = config
    async def execute(self, action, params, intent, context, step_results):
        if action == "smart_open":
            query = params.get("query", "")
            from utils.app_locator import app_locator
            launched = await asyncio.to_thread(app_locator.launch, query)
            if launched: return {"success": True, "title": query, "message": f"Opening {query}, Sir."}
            
            import webbrowser
            import urllib.parse
            safe_query = urllib.parse.quote(query)
            url = f"https://www.google.com/search?q={safe_query}"
            webbrowser.open(url)
            return {"success": True, "title": query, "opened": url, "message": f"I couldn't find {query} locally, so I searched the web for you, Sir."}
        raise ValueError(f"SmartOpenTool: unknown action {action}")

class PageContextTool(BaseTool):
    def __init__(self, config: Dict):
        self.config = config
        self._speak_fn = None
        self._groq_key = config.get("groq_api_key", "")
        
    def set_speak_fn(self, fn):
        self._speak_fn = fn
        
    async def execute(self, action, params, intent, context, step_results):
        import pyautogui
        import pyperclip
        import time as _t
        
        if action == "page_summary":
            pyautogui.hotkey("ctrl", "a")
            _t.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            _t.sleep(0.3)
            text = pyperclip.paste()
            if not text or len(text) < 50: return {"success": False, "error": "Could not extract page text."}
            
            try:
                from groq import AsyncGroq
                import re
                client = AsyncGroq(api_key=self._groq_key)
                
                stream = await client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "Summarize this webpage in 2-3 sentences."},
                        {"role": "user", "content": text[:4000]}
                    ],
                    stream=True, temperature=0.3, max_tokens=200
                )
                
                full_response = ""
                buffer = ""
                boundary_regex = re.compile(r'(?<=[.!?\n])\s+')
                
                async for chunk in stream:
                    token = chunk.choices[0].delta.content
                    if token:
                        buffer += token
                        full_response += token
                        if any(p in buffer for p in ['. ', '? ', '! ', '\n']):
                            splits = boundary_regex.split(buffer)
                            if len(splits) > 1:
                                for sentence in splits[:-1]:
                                    clean = sentence.strip()
                                    if clean and self._speak_fn: self._speak_fn(clean)
                                buffer = splits[-1]
                                
                if buffer.strip() and self._speak_fn:
                    self._speak_fn(buffer.strip())
                
                try:
                    from session_memory import session as _session
                    _session.set_page_context(text[:3000], url=context.get("active_url", ""), title=context.get("active_window_title", ""))
                except Exception: pass

                return {"success": True, "full_summary": full_response, "spoken_summary": " ", "url": context.get("active_url", "")}
            except Exception as e:
                return {"success": False, "error": f"Summary generation failed: {e}"}

        elif action == "read_page":
            pyautogui.hotkey("ctrl", "a")
            _t.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            _t.sleep(0.3)
            text = pyperclip.paste()

            if not text or len(text.strip()) < 50: return {"success": False, "error": "Could not read page."}

            import re as _re
            _SKIP = _re.compile(r'^(cookie|privacy policy|terms|sign in|log in|subscribe|newsletter|advertisement|loading|skip to|navigation|menu|search|home|about|contact|copyright|©|accept|reject|allow|deny|close|dismiss|button|checkbox|radio|select|dropdown|[\[\]<>|•·▶►])', _re.IGNORECASE)
            lines = text.split("\n")
            kept = []
            seen = set()
            for ln in lines:
                ln = ln.strip()
                if not ln or len(ln) < 4: continue
                if _SKIP.match(ln): continue
                key = ln.lower()[:60]
                if key in seen: continue
                seen.add(key)
                kept.append(ln)
            cleaned = "\n".join(kept)

            try:
                from session_memory import session as _session
                _session.set_page_context(cleaned, url=context.get("active_url", ""), title=context.get("active_window_title", ""))
            except Exception: pass

            try:
                from groq import AsyncGroq
                client = AsyncGroq(api_key=self._groq_key)
                prompt = f"PAGE TEXT:\n{cleaned[:4000]}"
                
                stream = await client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are Jarvis. The user asked you to read aloud what is on their screen. Summarize the main content in 3-5 natural spoken sentences. Focus only on the core article/product/event/page content. Ignore navigation, buttons, and repeated UI labels."},
                        {"role": "user", "content": prompt}
                    ],
                    stream=True, temperature=0.2, max_tokens=250
                )
                
                full_response = ""
                buffer = ""
                boundary_regex = _re.compile(r'(?<=[.!?\n])\s+')
                
                async for chunk in stream:
                    token = chunk.choices[0].delta.content
                    if token:
                        buffer += token
                        full_response += token
                        if any(p in buffer for p in ['. ', '? ', '! ', '\n']):
                            splits = boundary_regex.split(buffer)
                            if len(splits) > 1:
                                for sentence in splits[:-1]:
                                    clean = sentence.strip()
                                    if clean and self._speak_fn: self._speak_fn(clean)
                                buffer = splits[-1]
                                
                if buffer.strip() and self._speak_fn:
                    self._speak_fn(buffer.strip())
                    
                summary = full_response.strip()
            except Exception as _e:
                logger.warning(f"[PageContextTool] read_page LLM failed: {_e}")
                summary = cleaned[:300].strip()

            return {
                "success":  True,
                "spoken":   " ", # Returns blank space to prevent core.py repeating the text
                "raw":      cleaned[:500],
                "page_text": cleaned, 
                "page_url":  context.get("active_url", ""),
                "full_summary": summary
            }
            
        raise ValueError(f"PageContextTool: unknown action {action}")

class TTSTool(BaseTool):
    def __init__(self):
        self._tts = None
    async def execute(self, action, params, intent, context, step_results):
        if self._tts is None:
            try:
                from src.voice_io import JarvisVoice
                self._tts = JarvisVoice()
            except Exception: self._tts = None

        if action == "tts_speak":
            source = params.get("source", "")
            text = params.get("text", "")
            if not text and source.startswith("step_"):
                try:
                    idx = int(source.split("_")[1])
                    if idx < len(step_results):
                        out = step_results[idx].get("output", {})
                        if isinstance(out, dict): text = out.get("text", "") or out.get("content", "")
                        else: text = str(out)
                except (ValueError, IndexError): pass

            if text and self._tts: self._tts.speak(text)
            elif text: print(f"[Jarvis TTS] {text}")
            return {"success": True, "spoken": text[:100] if text else ""}
        raise ValueError(f"TTSTool: unknown action {action}")

class ResponderTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        if action == "greet": return {"success": True, "response": "Good to see you, Sir. How may I assist?"}
        elif action == "acknowledge_thanks": return {"success": True, "response": "Always a pleasure, Sir."}
        return {"success": True, "response": "Understood, Sir."}

class BrowserReaderTool(BaseTool):
    async def execute(self, action, params, intent, context, step_results):
        if action == "extract_page_text":
            try:
                import pyautogui
                import pyperclip
                import time as _t
                pyautogui.hotkey("ctrl", "a")
                _t.sleep(0.2)
                pyautogui.hotkey("ctrl", "c")
                _t.sleep(0.3)
                text = pyperclip.paste()
                return {"success": True, "text": text[:3000] if text else "Could not extract page text."}
            except Exception as e: return {"success": False, "text": f"Page extraction failed: {e}"}
        raise ValueError(f"BrowserReaderTool: unknown action {action}")

# ── PATCHES ───────────────────────────────────────────────────────────────
from executor.runner_patch import run_plan_patched, _execute_with_retry_patched
ExecutionRunner.run_plan = run_plan_patched
ExecutionRunner._execute_with_retry = _execute_with_retry_patched

from core.contract import CapabilityContract, verify_process_running, verify_window_exists, verify_file_exists

# ── REGISTER TOOLS WITH VERIFICATION CONTRACTS ──────────────────────────
cap_registry.register_with_contract("app_launcher", AppLauncherTool, CapabilityContract(name="open_app", description="Open applications and verify they started", verify_fn=None, fallback_capabilities=["browser"], max_retries=2, timeout_seconds=5.0))
cap_registry.register_with_contract("browser", BrowserTool, CapabilityContract(name="open_website", description="Open URLs and verify page loaded", verify_fn=None, fallback_capabilities=["web_navigator"], max_retries=2, timeout_seconds=8.0))
cap_registry.register_with_contract("system", SystemTool, CapabilityContract(name="take_screenshot", description="Take screenshot and verify file exists", verify_fn=lambda o, p: verify_file_exists(o.get("saved_to","") if isinstance(o,dict) else ""), fallback_capabilities=[], max_retries=1, timeout_seconds=5.0))
cap_registry.register_with_contract("media_controller", MediaControllerTool, CapabilityContract(name="play_media", description="Play media and verify playback started", verify_fn=None, fallback_capabilities=["browser"], max_retries=2, timeout_seconds=8.0))

cap_registry.register("system_controller", SystemControllerTool)
cap_registry.register("keyboard", KeyboardTool)
cap_registry.register("web_navigator", WebNavigatorTool)
cap_registry.register("communicator", CommunicatorTool)
cap_registry.register("memory", MemoryTool)
cap_registry.register("tts", TTSTool)
cap_registry.register("responder", ResponderTool)
cap_registry.register("browser_reader", BrowserReaderTool)
cap_registry.register("ai_brain", AIBrainTool)
cap_registry.register("smart_open", SmartOpenTool)
cap_registry.register("page_context", PageContextTool)

from executor.tools.click_simulator import ClickSimulatorTool
cap_registry.register("click_simulator", ClickSimulatorTool)

from executor.tools.communication_tool import UnifiedCommunicationTool
cap_registry.register("unified_comm", UnifiedCommunicationTool)

logger.info(f"[CapabilityRegistry] Registered {len(cap_registry.list_tools())} tools")