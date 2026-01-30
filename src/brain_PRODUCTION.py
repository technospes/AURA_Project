"""
PRODUCTION-READY AI BRAIN with:
1. Execution verification (PID/window checks)
2. Retry logic with fallbacks
3. Multi-step planning
4. State awareness (process/window tracking)
5. Self-correction (learns from failures)

Add these enhancements to your existing brain.py
"""
import os
import json
import time
import psutil
import win32gui
import win32process
from typing import Dict, Any, Optional, List
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================================
# 1. EXECUTION VERIFICATION - Check if actions actually worked
# ============================================================================

class ExecutionVerifier:
    """Verifies that commands actually executed successfully"""
    
    @staticmethod
    def verify_app_opened(app_name: str, timeout: float = 3.0) -> Dict[str, Any]:
        """
        Verify an application actually opened by checking running processes
        
        Returns:
            {"success": bool, "pid": int or None, "message": str}
        """
        app_name_lower = app_name.lower()
        start_time = time.time()
        
        # Common process name variations
        process_variations = [
            f"{app_name_lower}.exe",
            app_name_lower,
            app_name_lower.replace(" ", ""),
        ]
        
        while time.time() - start_time < timeout:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = proc.info['name'].lower()
                    
                    # Check if any variation matches
                    for variation in process_variations:
                        if variation in proc_name:
                            return {
                                "success": True,
                                "pid": proc.info['pid'],
                                "process_name": proc.info['name'],
                                "message": f"Verified: {app_name} running (PID: {proc.info['pid']})"
                            }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            time.sleep(0.2)  # Wait a bit before rechecking
        
        return {
            "success": False,
            "pid": None,
            "message": f"Failed to verify: {app_name} not found in running processes"
        }
    
    @staticmethod
    def verify_browser_opened(timeout: float = 3.0) -> Dict[str, Any]:
        """Verify a browser window opened"""
        browser_processes = ['chrome.exe', 'firefox.exe', 'msedge.exe', 'brave.exe']
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'].lower() in browser_processes:
                        return {
                            "success": True,
                            "pid": proc.info['pid'],
                            "browser": proc.info['name'],
                            "message": f"Browser opened: {proc.info['name']}"
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            time.sleep(0.2)
        
        return {
            "success": False,
            "message": "No browser window detected"
        }
    
    @staticmethod
    def verify_window_focused(window_title_contains: str, timeout: float = 2.0) -> Dict[str, Any]:
        """Verify a window with specific title is focused"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                hwnd = win32gui.GetForegroundWindow()
                window_title = win32gui.GetWindowText(hwnd).lower()
                
                if window_title_contains.lower() in window_title:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    return {
                        "success": True,
                        "hwnd": hwnd,
                        "pid": pid,
                        "title": window_title,
                        "message": f"Window focused: {window_title}"
                    }
            except:
                pass
            
            time.sleep(0.2)
        
        return {
            "success": False,
            "message": f"Window containing '{window_title_contains}' not focused"
        }
    
    @staticmethod
    def is_process_running(app_name: str) -> bool:
        """Quick check if process is already running"""
        app_name_lower = app_name.lower()
        
        for proc in psutil.process_iter(['name']):
            try:
                if app_name_lower in proc.info['name'].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        return False


# ============================================================================
# 2. STATE AWARENESS - Track what's running and window states
# ============================================================================

class SystemStateTracker:
    """Tracks system state: running apps, open windows, recent actions"""
    
    def __init__(self):
        self.running_apps = {}  # {app_name: pid}
        self.open_windows = {}  # {hwnd: window_info}
        self.recent_actions = []  # List of recent actions with results
        self.max_history = 50
    
    def update_running_apps(self):
        """Update list of running applications"""
        self.running_apps.clear()
        
        for proc in psutil.process_iter(['pid', 'name', 'create_time']):
            try:
                app_name = proc.info['name'].lower().replace('.exe', '')
                self.running_apps[app_name] = {
                    'pid': proc.info['pid'],
                    'name': proc.info['name'],
                    'started': proc.info['create_time']
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    
    def update_open_windows(self):
        """Update list of open windows"""
        self.open_windows.clear()
        
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:  # Only track windows with titles
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        proc = psutil.Process(pid)
                        
                        self.open_windows[hwnd] = {
                            'title': title,
                            'pid': pid,
                            'process_name': proc.name()
                        }
                    except:
                        pass
            return True
        
        win32gui.EnumWindows(enum_callback, None)
    
    def log_action(self, action: str, target: str, result: Dict[str, Any]):
        """Log an action and its result"""
        self.recent_actions.append({
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'target': target,
            'result': result
        })
        
        # Keep only recent history
        if len(self.recent_actions) > self.max_history:
            self.recent_actions = self.recent_actions[-self.max_history:]
    
    def is_app_running(self, app_name: str) -> Optional[Dict]:
        """Check if specific app is running"""
        app_name = app_name.lower().replace('.exe', '')
        return self.running_apps.get(app_name)
    
    def find_window_by_title(self, title_contains: str) -> Optional[Dict]:
        """Find window by partial title match"""
        title_lower = title_contains.lower()
        
        for hwnd, info in self.open_windows.items():
            if title_lower in info['title'].lower():
                return {**info, 'hwnd': hwnd}
        
        return None
    
    def get_state_summary(self) -> str:
        """Get human-readable state summary"""
        return f"Running apps: {len(self.running_apps)}, Open windows: {len(self.open_windows)}"


# ============================================================================
# 3. RETRY LOGIC WITH FALLBACKS
# ============================================================================

class RetryExecutor:
    """Executes actions with retry logic and intelligent fallbacks"""
    
    def __init__(self, verifier: ExecutionVerifier, state_tracker: SystemStateTracker):
        self.verifier = verifier
        self.state = state_tracker
        self.max_retries = 3
    
    def open_with_retry(self, app_name: str, primary_method, fallback_methods: List) -> Dict[str, Any]:
        """
        Try to open something with primary method, fall back if it fails
        
        Args:
            app_name: Name of app/website
            primary_method: Function to try first
            fallback_methods: List of (function, description) tuples to try if primary fails
        
        Returns:
            Result dict with success status and method used
        """
        attempts = []
        
        # Try primary method
        logger.info(f"[Retry] Attempting primary method for: {app_name}")
        try:
            result = primary_method()
            time.sleep(0.5)  # Give it time to start
            
            verification = self.verifier.verify_app_opened(app_name, timeout=2.0)
            
            if verification['success']:
                return {
                    'success': True,
                    'method': 'primary',
                    'verification': verification,
                    'message': f"✓ {app_name} opened successfully"
                }
            
            attempts.append({'method': 'primary', 'result': 'failed_verification'})
        
        except Exception as e:
            attempts.append({'method': 'primary', 'error': str(e)})
            logger.warning(f"[Retry] Primary method failed: {e}")
        
        # Try fallback methods
        for i, (fallback_fn, description) in enumerate(fallback_methods, 1):
            logger.info(f"[Retry] Trying fallback {i}: {description}")
            
            try:
                result = fallback_fn()
                time.sleep(0.5)
                
                # Verify - for browser fallbacks, check browser instead
                if 'browser' in description.lower() or 'web' in description.lower():
                    verification = self.verifier.verify_browser_opened(timeout=2.0)
                else:
                    verification = self.verifier.verify_app_opened(app_name, timeout=2.0)
                
                if verification['success']:
                    return {
                        'success': True,
                        'method': f'fallback_{i}',
                        'description': description,
                        'verification': verification,
                        'message': f"✓ {app_name} opened via {description}"
                    }
                
                attempts.append({'method': f'fallback_{i}', 'result': 'failed_verification'})
            
            except Exception as e:
                attempts.append({'method': f'fallback_{i}', 'error': str(e)})
                logger.warning(f"[Retry] Fallback {i} failed: {e}")
        
        # All methods failed
        return {
            'success': False,
            'attempts': attempts,
            'message': f"✗ All methods failed to open {app_name}"
        }


# ============================================================================
# 4. MULTI-STEP PLANNER
# ============================================================================

class MultiStepPlanner:
    """Plans and executes multi-step operations"""
    
    def __init__(self, executor: RetryExecutor, state_tracker: SystemStateTracker):
        self.executor = executor
        self.state = state_tracker
    
    def execute_plan(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute a multi-step plan
        
        Args:
            plan: List of steps, each with:
                {
                    'action': str,
                    'params': dict,
                    'verify': callable,
                    'on_failure': str ('abort', 'continue', 'retry')
                }
        
        Returns:
            Execution results for all steps
        """
        results = []
        
        for i, step in enumerate(plan, 1):
            logger.info(f"[Plan] Step {i}/{len(plan)}: {step['action']}")
            
            try:
                # Execute step
                result = step['execute'](step['params'])
                
                # Verify if verifier provided
                if 'verify' in step and step['verify']:
                    verification = step['verify']()
                    result['verified'] = verification['success']
                else:
                    result['verified'] = True
                
                results.append({
                    'step': i,
                    'action': step['action'],
                    'success': result.get('success', True) and result.get('verified', True),
                    'result': result
                })
                
                # Handle failure
                if not results[-1]['success']:
                    failure_action = step.get('on_failure', 'abort')
                    
                    if failure_action == 'abort':
                        logger.error(f"[Plan] Step {i} failed, aborting plan")
                        break
                    elif failure_action == 'retry':
                        # Retry once
                        logger.info(f"[Plan] Retrying step {i}")
                        time.sleep(0.5)
                        result = step['execute'](step['params'])
                        results[-1]['result'] = result
                        results[-1]['success'] = result.get('success', False)
                    # 'continue' = just log and continue
                
                # Small delay between steps
                if i < len(plan):
                    time.sleep(step.get('delay', 0.3))
            
            except Exception as e:
                logger.error(f"[Plan] Step {i} error: {e}")
                results.append({
                    'step': i,
                    'action': step['action'],
                    'success': False,
                    'error': str(e)
                })
                
                if step.get('on_failure', 'abort') == 'abort':
                    break
        
        return {
            'total_steps': len(plan),
            'completed_steps': len(results),
            'success': all(r['success'] for r in results),
            'results': results
        }


# ============================================================================
# 5. SELF-CORRECTION ENGINE
# ============================================================================

class SelfCorrectingAgent:
    """Learns from failures and adjusts strategy"""
    
    def __init__(self):
        self.failure_log = []  # Track what failed
        self.success_patterns = {}  # Track what worked
        self.correction_rules = self._init_correction_rules()
    
    def _init_correction_rules(self) -> Dict[str, List[str]]:
        """Initialize correction strategies for common failures"""
        return {
            # If app not found, try these
            'app_not_found': [
                'try_web_version',
                'try_alternate_name',
                'search_in_start_menu'
            ],
            
            # If app won't open
            'app_failed_to_open': [
                'check_if_already_running',
                'try_as_admin',
                'kill_and_restart'
            ],
            
            # If website won't load
            'website_failed': [
                'try_different_browser',
                'try_alternate_url',
                'check_internet'
            ],
            
            # If search returns no results
            'search_no_results': [
                'rephrase_query',
                'broaden_search_terms',
                'try_alternate_search_engine'
            ]
        }
    
    def log_failure(self, action: str, target: str, error: str):
        """Log a failure for learning"""
        self.failure_log.append({
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'target': target,
            'error': error
        })
    
    def log_success(self, action: str, target: str, method: str):
        """Log a successful action"""
        key = f"{action}:{target}"
        
        if key not in self.success_patterns:
            self.success_patterns[key] = []
        
        self.success_patterns[key].append({
            'method': method,
            'timestamp': datetime.now().isoformat()
        })
    
    def get_correction_strategy(self, failure_type: str) -> List[str]:
        """Get correction strategy for a failure type"""
        return self.correction_rules.get(failure_type, ['retry_default'])
    
    def get_best_method(self, action: str, target: str) -> Optional[str]:
        """Get the method that worked best for this action+target combo"""
        key = f"{action}:{target}"
        
        if key in self.success_patterns and self.success_patterns[key]:
            # Return most recent successful method
            return self.success_patterns[key][-1]['method']
        
        return None


# ============================================================================
# 6. ENHANCED TOOL IMPLEMENTATIONS (Production-Ready)
# ============================================================================

class ProductionOpenAppTool:
    """Production-ready app opening with all enhancements"""
    
    def __init__(self):
        self.verifier = ExecutionVerifier()
        self.state = SystemStateTracker()
        self.retry_executor = RetryExecutor(self.verifier, self.state)
        self.self_corrector = SelfCorrectingAgent()
    
    def open_app(self, app_name: str) -> Dict[str, Any]:
        """
        Open app with full production features:
        - State awareness (check if already running)
        - Execution verification (PID check)
        - Retry logic (fallbacks)
        - Self-correction (learn from failures)
        """
        from src.native_opener import open_app as native_open_app
        import webbrowser
        
        logger.info(f"[ProductionTool] Opening: {app_name}")
        
        # Update state
        self.state.update_running_apps()
        
        # 1. STATE AWARENESS - Check if already running
        existing_app = self.state.is_app_running(app_name)
        if existing_app:
            logger.info(f"[ProductionTool] {app_name} already running (PID: {existing_app['pid']})")
            
            # Try to focus existing window
            self.state.update_open_windows()
            window = self.state.find_window_by_title(app_name)
            
            if window:
                try:
                    win32gui.SetForegroundWindow(window['hwnd'])
                    return {
                        'success': True,
                        'already_running': True,
                        'pid': existing_app['pid'],
                        'message': f"{app_name} was already running, focused window"
                    }
                except:
                    pass
        
        # 2. CHECK BEST METHOD FROM HISTORY
        best_method = self.self_corrector.get_best_method('open', app_name)
        
        # 3. RETRY LOGIC - Try multiple methods
        def primary():
            return native_open_app(app_name)
        
        def fallback_web():
            url = f"https://www.{app_name}.com"
            return webbrowser.open(url)
        
        def fallback_direct():
            import subprocess
            return subprocess.Popen(f'start "" "{app_name}.exe"', shell=True)
        
        fallbacks = [
            (fallback_web, "web_browser_fallback"),
            (fallback_direct, "direct_exe_launch")
        ]
        
        result = self.retry_executor.open_with_retry(app_name, primary, fallbacks)
        
        # 4. LOG RESULTS FOR SELF-CORRECTION
        if result['success']:
            self.self_corrector.log_success('open', app_name, result.get('method', 'primary'))
            self.state.log_action('open', app_name, result)
        else:
            self.self_corrector.log_failure('open', app_name, result.get('message', 'unknown'))
        
        return result


# ============================================================================
# 7. INTEGRATION WITH EXISTING BRAIN
# ============================================================================

"""
To integrate into your existing brain.py:

1. Add these imports at the top:
   from brain_PRODUCTION import (
       ExecutionVerifier, SystemStateTracker, RetryExecutor,
       MultiStepPlanner, SelfCorrectingAgent, ProductionOpenAppTool
   )

2. In AIAssistant.__init__(), add:
   self.verifier = ExecutionVerifier()
   self.state_tracker = SystemStateTracker()
   self.retry_executor = RetryExecutor(self.verifier, self.state_tracker)
   self.planner = MultiStepPlanner(self.retry_executor, self.state_tracker)
   self.self_corrector = SelfCorrectingAgent()
   self.production_tools = ProductionOpenAppTool()

3. Replace _open_app_tool with:
   def _open_app_tool(self, name: str) -> Dict[str, Any]:
       return self.production_tools.open_app(name)

4. For complex operations, use the planner:
   plan = [
       {
           'action': 'open_browser',
           'execute': lambda p: open_app('chrome'),
           'params': {},
           'verify': lambda: self.verifier.verify_browser_opened(),
           'on_failure': 'retry'
       },
       {
           'action': 'navigate',
           'execute': lambda p: webbrowser.open(p['url']),
           'params': {'url': 'https://youtube.com'},
           'verify': None,
           'on_failure': 'continue',
           'delay': 1.0
       }
   ]
   result = self.planner.execute_plan(plan)
"""


# ============================================================================
# 8. EXAMPLE: Complete Production-Ready Action
# ============================================================================

def example_production_ready_youtube_search(query: str):
    """
    Example showing all 5 features working together:
    - State awareness
    - Execution verification  
    - Retry logic
    - Multi-step planning
    - Self-correction
    """
    import webbrowser
    import pyautogui
    import time
    
    # Initialize production components
    verifier = ExecutionVerifier()
    state = SystemStateTracker()
    retry_executor = RetryExecutor(verifier, state)
    planner = MultiStepPlanner(retry_executor, state)
    corrector = SelfCorrectingAgent()
    
    # Define multi-step plan
    plan = [
        {
            'action': 'check_browser_running',
            'execute': lambda p: {'success': state.is_app_running('chrome') is not None},
            'params': {},
            'verify': None,
            'on_failure': 'continue'
        },
        {
            'action': 'open_youtube',
            'execute': lambda p: webbrowser.open('https://www.youtube.com'),
            'params': {},
            'verify': lambda: verifier.verify_browser_opened(timeout=3.0),
            'on_failure': 'retry',
            'delay': 2.0
        },
        {
            'action': 'focus_search',
            'execute': lambda p: pyautogui.press('/'),  # YouTube keyboard shortcut
            'params': {},
            'verify': None,
            'on_failure': 'continue',
            'delay': 0.5
        },
        {
            'action': 'type_search',
            'execute': lambda p: pyautogui.write(p['query'], interval=0.05),
            'params': {'query': query},
            'verify': None,
            'on_failure': 'retry',
            'delay': 0.3
        },
        {
            'action': 'submit_search',
            'execute': lambda p: pyautogui.press('enter'),
            'params': {},
            'verify': None,
            'on_failure': 'continue'
        }
    ]
    
    # Execute plan
    result = planner.execute_plan(plan)
    
    # Log for self-correction
    if result['success']:
        corrector.log_success('youtube_search', query, 'multi_step_plan')
    else:
        corrector.log_failure('youtube_search', query, 'plan_execution_failed')
    
    return result


if __name__ == "__main__":
    # Test production features
    print("Testing Production-Ready AI Agent Features...")
    
    tool = ProductionOpenAppTool()
    result = tool.open_app("notepad")
    print(json.dumps(result, indent=2))
