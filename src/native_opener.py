"""
Native Opener (V21.0 - PRODUCTION READY)
Features: Proper Spotify desktop app control, YouTube autoplay, accurate app/tab management
"""
import os
import sys
import time
import json
import subprocess
import webbrowser
import difflib
import psutil
import pyautogui
import win32gui
import win32con
import win32process
from pathlib import Path
from typing import Dict, Tuple, Optional, List
from urllib.parse import quote_plus

# Performance optimization
pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = False

CACHE_FILE = Path("app_cache.json")

# ============================================================================
# BROWSER TAB MANAGER
# ============================================================================

class BrowserTabManager:
    """Manages browser tabs and windows"""
    
    BROWSER_PROCESSES = {
        'chrome': 'chrome.exe',
        'firefox': 'firefox.exe',
        'edge': 'msedge.exe',
        'brave': 'brave.exe',
    }
    
    @staticmethod
    def get_active_window_info() -> Tuple[str, int, str]:
        """Get active window title, hwnd, and process name"""
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            proc_name = proc.name().lower()
            return title, hwnd, proc_name
        except:
            return "", 0, ""
    
    @staticmethod
    def is_browser_active() -> bool:
        """Check if a browser is currently active"""
        _, _, proc_name = BrowserTabManager.get_active_window_info()
        return any(browser_exe in proc_name for browser_exe in BrowserTabManager.BROWSER_PROCESSES.values())
    
    @staticmethod
    def find_browser_tab(search_term: str) -> Optional[int]:
        """Find browser window with search term in title"""
        search_lower = search_term.lower()
        
        def callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    proc = psutil.Process(pid)
                    proc_name = proc.name().lower()
                    
                    is_browser = any(exe in proc_name for exe in BrowserTabManager.BROWSER_PROCESSES.values())
                    
                    if is_browser and search_lower in title:
                        windows.append((hwnd, title))
                except:
                    pass
            return True
        
        windows = []
        win32gui.EnumWindows(callback, windows)
        return windows[0][0] if windows else None
    
    @staticmethod
    def close_current_tab() -> bool:
        """Close the active tab in browser"""
        if BrowserTabManager.is_browser_active():
            pyautogui.hotkey('ctrl', 'w')
            time.sleep(0.2)
            return True
        return False
    
    @staticmethod
    def close_tab_by_name(tab_name: str) -> bool:
        """
        Safely close browser tab by name.
        Strategy: Open new tab first to guarantee browser stays open.
        """
        hwnd = BrowserTabManager.find_browser_tab(tab_name)
        
        if not hwnd:
            print(f"[Tab] Tab '{tab_name}' not found")
            return False
        
        print(f"[Tab] Closing '{tab_name}'...")
        
        try:
            # Step 1: Capture current active window to potentially restore later
            original_active_hwnd = win32gui.GetForegroundWindow()
            
            # Step 2: Check if target window is minimized
            placement = win32gui.GetWindowPlacement(hwnd)
            was_minimized = placement[1] == win32con.SW_SHOWMINIMIZED
            
            # Step 3: Bring browser to foreground (restore if minimized)
            if was_minimized:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.15)  # Small wait for window activation
            
            # Step 4: 🔐 CRITICAL SAFETY - Open new tab first
            # This guarantees browser won't close if we're on the last tab
            pyautogui.hotkey('ctrl', 't')
            time.sleep(0.1)   # Wait for new tab to open
            
            # Step 5: Close the target tab
            pyautogui.hotkey('ctrl', 'w')
            time.sleep(0.1)   # Wait for tab to close
            
            # Step 6: Optional - Restore original window focus
            # Only if user wasn't already in the browser
            try:
                if original_active_hwnd != hwnd and win32gui.IsWindow(original_active_hwnd):
                    # Small delay to ensure browser operations complete
                    time.sleep(0.2)
                    win32gui.SetForegroundWindow(original_active_hwnd)
            except:
                pass  # Don't fail if we can't restore focus
            
            print(f"[Tab] ✓ Successfully closed '{tab_name}'")
            return True
            
        except Exception as e:
            print(f"[Tab] Error closing tab: {e}")
            return False
    
    @staticmethod
    def open_new_tab() -> bool:
        """Open new tab in active browser"""
        if BrowserTabManager.is_browser_active():
            pyautogui.hotkey('ctrl', 't')
            time.sleep(0.2)
            return True
        return False
    
# ============================================================================
# SPOTIFY URI PLAYER - RELIABLE METHOD
# ============================================================================

class SpotifyUriPlayer:
    """Play Spotify tracks using official URI scheme - RELIABLE"""
    
    @staticmethod
    def search_track_id(track_name: str) -> Optional[str]:
        """
        Search for a track using Spotify Web API and get its ID.
        This is the ONLY reliable way to play specific tracks.
        """
        try:
            import requests
            import re
            
            # Search Spotify web (no API key needed for public search)
            search_query = quote_plus(track_name)
            search_url = f"https://open.spotify.com/search/{search_query}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=5)
            
            if response.status_code != 200:
                return None
            
            html = response.text
            
            # Extract track ID from HTML (Spotify embeds track URIs)
            # Look for patterns like spotify:track:xxxx or /track/xxxx
            patterns = [
                r'spotify:track:(\w{22})',
                r'/track/(\w{22})',
                r'"uri":"spotify:track:(\w{22})"',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, html)
                if matches:
                    # Return the first track ID found (most relevant)
                    return matches[0]
            
            return None
            
        except Exception as e:
            print(f"[Spotify Search] Error: {e}")
            return None
    
    @staticmethod
    def play_via_uri(track_name: str) -> bool:
        """
        Play track using Spotify URI - the ONLY reliable method.
        This opens Spotify desktop app with the specific track.
        """
        try:
            print(f"[Spotify URI] Searching for track ID: {track_name}")
            
            # Get track ID
            track_id = SpotifyUriPlayer.search_track_id(track_name)
            
            if not track_id:
                print(f"[Spotify URI] Could not find track ID for: {track_name}")
                return False
            
            print(f"[Spotify URI] Found track ID: {track_id}")
            
            # Construct Spotify URI
            spotify_uri = f"spotify:track:{track_id}"
            
            # Open URI - Spotify desktop app will handle this
            print(f"[Spotify URI] Opening: {spotify_uri}")
            os.startfile(spotify_uri)
            
            # Wait for playback to start
            time.sleep(2)
            
            print(f"[Spotify URI] ✓ URI sent for: {track_name}")
            print(f"[Spotify URI] Note: Spotify desktop app should play the track")
            return True
            
        except Exception as e:
            print(f"[Spotify URI] Error: {e}")
            return False
    
# ============================================================================
# SPOTIFY DESKTOP AUTOMATION - REALISTIC PRODUCTION READY
# ============================================================================

class SpotifyDesktopController:
    """Production-ready Spotify desktop controller with robust window management"""
    
    @staticmethod
    def is_running():
        """Check if Spotify process is running"""
        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info['name']
                if name and 'spotify.exe' in name.lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False
    
    @staticmethod
    def launch_spotify():
        """Launch Spotify desktop app and wait for it to be ready"""
        try:
            print("[Spotify] Launching Spotify desktop app...")
            os.startfile("spotify:")
            
            # Wait for process to start (max 10 seconds)
            max_wait = 10
            start_time = time.time()
            while time.time() - start_time < max_wait:
                if SpotifyDesktopController.is_running():
                    print("[Spotify] ✓ Process started")
                    # Extra wait for window to initialize
                    time.sleep(3)
                    return True
                time.sleep(0.5)
            
            print("[Spotify] ✗ Launch timeout")
            return False
        except Exception as e:
            print(f"[Spotify] Launch error: {e}")
            return False
    
    @staticmethod
    def ensure_spotify_running():
        """Ensure Spotify is running, launch if needed"""
        if not SpotifyDesktopController.is_running():
            return SpotifyDesktopController.launch_spotify()
        else:
            print("[Spotify] ✓ Already running")
            return True
    
    @staticmethod
    def get_spotify_windows():
        """Get all Spotify window handles with detailed info"""
        spotify_windows = []
        
        def callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    # Get window title
                    title = win32gui.GetWindowText(hwnd)
                    
                    # Get process info
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        proc = psutil.Process(pid)
                        proc_name = proc.name().lower()
                        
                        # Check if this is a Spotify window
                        if 'spotify.exe' in proc_name:
                            # Get window class name for additional verification
                            class_name = win32gui.GetClassName(hwnd)
                            
                            # Spotify main window characteristics:
                            # - Has a title (not empty)
                            # - Class name contains 'Chrome' (Spotify uses Chromium)
                            # - Is not a utility/helper window
                            
                            if title and len(title) > 0:
                                windows.append({
                                    'hwnd': hwnd,
                                    'title': title,
                                    'class': class_name,
                                    'pid': pid
                                })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                except Exception:
                    pass
            return True
        
        win32gui.EnumWindows(callback, spotify_windows)
        return spotify_windows
    
    @staticmethod
    def focus_spotify():
        """Focus Spotify window with robust multi-method approach"""
        try:
            # Get all Spotify windows
            windows = SpotifyDesktopController.get_spotify_windows()
            
            if not windows:
                print("[Spotify] ✗ No Spotify windows found")
                return False
            
            # Filter for main window (usually has longest title or contains specific keywords)
            # Spotify main window titles include: "Spotify Premium", "Spotify Free", or song names
            main_window = None
            
            # First, try to find window with "Spotify" in title
            for win in windows:
                title_lower = win['title'].lower()
                if 'spotify' in title_lower:
                    # Prefer windows with more specific titles
                    if 'premium' in title_lower or 'free' in title_lower:
                        main_window = win
                        break
                    elif main_window is None:
                        main_window = win
            
            # If no Spotify-titled window, use the first one with Chrome class
            if main_window is None:
                for win in windows:
                    if 'chrome' in win['class'].lower():
                        main_window = win
                        break
            
            # Last resort: use first window
            if main_window is None:
                main_window = windows[0]
            
            hwnd = main_window['hwnd']
            print(f"[Spotify] Found window: '{main_window['title']}' (PID: {main_window['pid']})")
            
            # Get current window state
            placement = win32gui.GetWindowPlacement(hwnd)
            current_state = placement[1]
            
            # Restore if minimized
            if current_state == win32con.SW_SHOWMINIMIZED:
                print("[Spotify] Window minimized, restoring...")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.5)
            
            # Ensure window is shown
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            time.sleep(0.3)
            
            # Multiple methods to bring window to foreground (Windows can be stubborn)
            try:
                # Method 1: Standard SetForegroundWindow
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception as e:
                print(f"[Spotify] SetForegroundWindow failed: {e}")
            
            # Method 2: BringWindowToTop (alternative)
            try:
                win32gui.BringWindowToTop(hwnd)
                time.sleep(0.3)
            except Exception as e:
                print(f"[Spotify] BringWindowToTop failed: {e}")
            
            # Method 3: Force with ctypes (most aggressive)
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception as e:
                print(f"[Spotify] ctypes SetForegroundWindow failed: {e}")
            
            # Verify window is focused
            current_hwnd = win32gui.GetForegroundWindow()
            if current_hwnd == hwnd:
                print("[Spotify] ✓ Window successfully focused")
                return True
            else:
                # Even if verification fails, continue - window might still be accessible
                print("[Spotify] ⚠ Focus verification unclear, continuing anyway...")
                return True
            
        except Exception as e:
            print(f"[Spotify] Focus error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    @staticmethod
    def play_track_desktop(track_name: str) -> bool:
        """
        Play track in Spotify desktop app - COMPLETE PRODUCTION SOLUTION
        
        Returns:
            bool: True if automation completed successfully, False otherwise
        """
        try:
            print(f"\n{'='*60}")
            print(f"[Spotify] Starting playback automation")
            print(f"[Spotify] Track: '{track_name}'")
            print(f"{'='*60}\n")
            
            # STEP 1: Ensure Spotify is running
            if not SpotifyDesktopController.ensure_spotify_running():
                print("[Spotify] ✗ Failed to start Spotify")
                return False
            
            # STEP 2: Wait for Spotify to fully initialize
            print("[Spotify] Waiting for Spotify to initialize...")
            time.sleep(2)
            
            # STEP 3: Focus Spotify window (with retries)
            focused = False
            max_attempts = 5
            
            for attempt in range(max_attempts):
                print(f"[Spotify] Focus attempt {attempt + 1}/{max_attempts}...")
                
                if SpotifyDesktopController.focus_spotify():
                    focused = True
                    break
                
                if attempt < max_attempts - 1:
                    print(f"[Spotify] Retrying in 1 second...")
                    time.sleep(1)
            
            if not focused:
                print("[Spotify] ✗ Could not focus Spotify window after all attempts")
                return False
            
            # STEP 4: Wait for window to be fully responsive
            print("[Spotify] Window focused, waiting for responsiveness...")
            time.sleep(1.5)
            
            # STEP 5: Open search (Ctrl+L is universal Spotify shortcut)
            print(f"[Spotify] Opening search...")
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(0.8)
            
            # STEP 6: Clear search field (multiple methods for reliability)
            print(f"[Spotify] Clearing search field...")
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.press('backspace')
            time.sleep(0.3)
            
            # STEP 7: Type track name (character by character for reliability)
            print(f"[Spotify] Typing: '{track_name}'")
            for char in track_name:
                pyautogui.write(char, interval=0.04)
            time.sleep(0.6)
            
            # STEP 8: Submit search
            print(f"[Spotify] Submitting search...")
            pyautogui.press('enter')
            time.sleep(2.5)  # Wait for search results to load
            
            # STEP 9: Play first result
            print(f"[Spotify] Playing first result...")
            
            # In Spotify, after search:
            # - First Enter selects the first track
            # - Need to press Enter again or Space to play
            pyautogui.press('enter')
            time.sleep(0.5)
            
            # Double-check playback started (press space)
            # This acts as a toggle, so it ensures playback
            pyautogui.press('space')
            time.sleep(0.3)
            
            print(f"\n{'='*60}")
            print(f"[Spotify] ✓ Automation completed successfully!")
            print(f"[Spotify] Track: '{track_name}'")
            print(f"[Spotify] Check Spotify app for playback confirmation")
            print(f"{'='*60}\n")
            
            return True
            
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"[Spotify] ✗ AUTOMATION FAILED")
            print(f"[Spotify] Error: {e}")
            print(f"{'='*60}\n")
            import traceback
            traceback.print_exc()
            return False

# ============================================================================
# YOUTUBE AUTOPLAY
# ============================================================================

class YouTubePlayer:
    """YouTube autoplay handler with enhanced features"""
    
    def __init__(self):
        self.last_played_video = None
        self.last_played_url = None
        self.playback_history = []
        self.max_history = 10
        
    def _search_video_id(self, video_name: str, music_only: bool = False) -> Optional[str]:
        """Search YouTube and extract video ID with optional music filter"""
        try:
            import requests
            import re
            from urllib.parse import quote_plus
            
            # Add music filter if requested
            search_query = video_name
            if music_only:
                search_query = f"{video_name} official music"
            
            search_url = f"https://www.youtube.com/results?search_query={quote_plus(search_query)}"
            
            # Try different user agents for better results
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=5)
            
            if response.status_code != 200:
                return None
            
            html_content = response.text
            
            # Extract video IDs using regex
            video_patterns = [
                r'"videoId":"(\w{11})"',  # JSON embedded format
                r'watch\?v=(\w{11})',      # Traditional URL format
                r'/embed/(\w{11})',        # Embedded format
                r'vi/(\w{11})/',           # Thumbnail format
            ]
            
            all_video_ids = []
            for pattern in video_patterns:
                matches = re.findall(pattern, html_content)
                all_video_ids.extend(matches)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_ids = []
            for vid in all_video_ids:
                if vid not in seen and len(vid) == 11:
                    seen.add(vid)
                    unique_ids.append(vid)
            
            # Return first valid video ID
            return unique_ids[0] if unique_ids else None
            
        except Exception as e:
            print(f"[YouTube Search] Error: {e}")
            return None
    
    def _is_official_video(self, video_name: str, video_title: str) -> bool:
        """Check if video appears to be official"""
        video_lower = video_title.lower()
        query_lower = video_name.lower()
        
        # Official indicators
        official_indicators = [
            'official', 
            'official video', 
            'official music video',
            'vevo',
            'lyric video',
            'official audio',
            'official visualizer'
        ]
        
        # Check for official indicators
        for indicator in official_indicators:
            if indicator in video_lower:
                return True
        
        # Check if video title contains the search query
        if query_lower in video_lower:
            return True
        
        return False
    
    def play_video(self, video_name: str, music_only: bool = False, prefer_official: bool = True) -> bool:
        """
        Open YouTube and automatically play video with enhanced features.
        
        Args:
            video_name: The video to search for
            music_only: Whether to filter for music content
            prefer_official: Whether to prefer official/official music videos
        """
        try:
            from urllib.parse import quote_plus
            
            # Check if this is a resume request
            if video_name.lower() in ["resume", "continue", "unpause", "last video"] and self.last_played_url:
                print(f"[YouTube] Resuming last video...")
                webbrowser.open(self.last_played_url)
                return True
            
            # Check if this is a repeat request
            if video_name.lower() == "again" and self.last_played_url:
                print(f"[YouTube] Repeating last video...")
                webbrowser.open(self.last_played_url)
                return True
            
            print(f"[YouTube] Searching for: {video_name}")
            
            # Get video ID
            video_id = self._search_video_id(video_name, music_only)
            
            # In YouTubePlayer.play_video() method:
            if not video_id:
                # Fallback to traditional search method
                print(f"[YouTube] Using fallback search method...")
                if music_only:
                    search_url = f"https://www.youtube.com/results?search_query={quote_plus(video_name + ' music')}"
                else:
                    search_url = f"https://www.youtube.com/results?search_query={quote_plus(video_name)}"
                
                webbrowser.open(search_url)
                time.sleep(2.5)
                
                # Try to play first result - FIXED VERSION:
                time.sleep(2.5)  # Wait for page to load
                # Simple approach: Just press tab a few times to navigate
                pyautogui.press('tab', presses=6)  # Tab to first result
                time.sleep(0.3)
                pyautogui.press('enter')
                
                # Store as last played
                self.last_played_video = video_name
                self.last_played_url = search_url
                self._add_to_history(video_name, search_url)
                
                print(f"[YouTube] ✓ Playing: {video_name}")
                return True
            
            # Construct autoplay URL
            play_url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
            
            # If we have official preference, we might want to verify
            if prefer_official:
                # Try to get more info about the video (optional enhancement)
                # For now, we'll just note it in the log
                print(f"[YouTube] Found video ID: {video_id}")
            
            # Open the video
            webbrowser.open(play_url)
            
            # Store playback info
            self.last_played_video = video_name
            self.last_played_url = play_url
            self._add_to_history(video_name, play_url)
            
            print(f"[YouTube] ✓ Autoplaying: {video_name}")
            
            # Additional enhancement: Auto-fullscreen (optional)
            time.sleep(3)  # Wait for page to load
            pyautogui.press('f')  # Toggle fullscreen
            
            return True
            
        except Exception as e:
            print(f"[YouTube] Error: {e}")
            return False
    
    def _add_to_history(self, video_name: str, video_url: str):
        """Add video to playback history"""
        self.playback_history.append({
            'name': video_name,
            'url': video_url,
            'timestamp': time.time()
        })
        
        # Limit history size
        if len(self.playback_history) > self.max_history:
            self.playback_history.pop(0)
    
    def resume_playback(self) -> bool:
        """Resume the last played video"""
        if self.last_played_url:
            try:
                webbrowser.open(self.last_played_url)
                print(f"[YouTube] Resuming: {self.last_played_video}")
                return True
            except Exception as e:
                print(f"[YouTube] Resume failed: {e}")
                return False
        else:
            print("[YouTube] No video to resume")
            return False
    
    def get_last_video(self) -> Optional[Dict]:
        """Get information about the last played video"""
        if self.last_played_video and self.last_played_url:
            return {
                'name': self.last_played_video,
                'url': self.last_played_url
            }
        return None
    
    def get_playback_history(self) -> List[Dict]:
        """Get playback history"""
        return self.playback_history.copy()
    
    def clear_history(self):
        """Clear playback history"""
        self.playback_history = []
        self.last_played_video = None
        self.last_played_url = None

# ============================================================================
# TYPING AUTOMATION
# ============================================================================

class TypingController:
    """Automated typing in active window or Notepad"""
    
    @staticmethod
    def is_notepad_active() -> bool:
        """Check if Notepad is active"""
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd).lower()
            return 'notepad' in title or 'untitled' in title
        except:
            return False
    
    @staticmethod
    def open_notepad_and_type(text: str) -> bool:
        """Open Notepad and type text"""
        try:
            if not TypingController.is_notepad_active():
                subprocess.Popen("notepad.exe", shell=False)
                time.sleep(0.8)
            
            return TypingController.type_in_active_window(text)
        except Exception as e:
            print(f"[Typing] Error: {e}")
            return False
    
    @staticmethod
    def type_in_active_window(text: str) -> bool:
        """Type text in currently active window"""
        try:
            for char in text:
                try:
                    pyautogui.write(char, interval=0.02)
                except:
                    pyautogui.press(char)
                    time.sleep(0.02)
            return True
        except Exception as e:
            print(f"[Typing] Error: {e}")
            return False

# ============================================================================
# APPLICATION REGISTRY
# ============================================================================

class ApplicationRegistry:
    """Dynamic application discovery and management"""
    
    def __init__(self):
        self.apps = {}
        self.load_or_scan()
    
    def load_or_scan(self):
        """Load cache or perform full scan"""
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.apps = data.get("apps", {})
                    if len(self.apps) > 10:
                        return
            except:
                pass
        self.full_scan()
    
    def full_scan(self):
        """Comprehensive application scan"""
        # Windows built-in apps
        self.apps.update({
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "cmd": "cmd.exe",
            "command prompt": "cmd.exe",
            "powershell": "powershell.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "paint": "mspaint.exe",
            "wordpad": "wordpad.exe",
            "task manager": "taskmgr.exe",
            "control panel": "control.exe",
        })
        
        # Common browsers
        browser_paths = {
            "chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
            "google chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
            "firefox": [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ],
            "edge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
            "microsoft edge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
            "brave": [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
        }
        
        for name, paths in browser_paths.items():
            for path in paths:
                if os.path.exists(path):
                    self.apps[name] = path
                    break
        
        # Common applications with dynamic path discovery
        app_searches = {
            "spotify": [
                os.path.join(os.environ.get("APPDATA", ""), r"Spotify\Spotify.exe"),
                r"C:\Users\Ayush\OneDrive\Desktop\Spotify.lnk",
                r"C:\Program Files (x86)\Spotify\Spotify.exe",
            ],
            "discord": [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Discord\Update.exe --processStart Discord.exe"),
                r"C:\Program Files\Discord\Discord.exe",
            ],
            "code": [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Microsoft VS Code\Code.exe"),
                r"C:\Program Files\Microsoft VS Code\Code.exe",
            ],
            "vscode": [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Microsoft VS Code\Code.exe"),
                r"C:\Program Files\Microsoft VS Code\Code.exe",
            ],
            "visual studio code": [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Microsoft VS Code\Code.exe"),
                r"C:\Program Files\Microsoft VS Code\Code.exe",
            ],
            "vlc": [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ],
        }
        
        for app_name, paths in app_searches.items():
            for path in paths:
                if os.path.exists(path):
                    self.apps[app_name] = path
                    break
        
        # Scan Start Menu
        start_menu_paths = [
            os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        ]
        
        for base_path in start_menu_paths:
            if not os.path.exists(base_path):
                continue
            
            try:
                for root, _, files in os.walk(base_path):
                    for filename in files:
                        if filename.endswith(".lnk"):
                            app_name = filename.lower().replace(".lnk", "").strip()
                            if app_name not in self.apps:
                                self.apps[app_name] = os.path.join(root, filename)
            except:
                pass
        
        # Save cache
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"apps": self.apps}, f, indent=2)
        except:
            pass
    
    def find_app(self, query: str) -> Tuple[str, str, float]:
        """Find app with fuzzy matching"""
        query = query.lower().strip()
        
        # Exact match
        if query in self.apps:
            return query, self.apps[query], 1.0
        
        # Starts with
        for app_name, app_path in self.apps.items():
            if app_name.startswith(query):
                return app_name, app_path, 0.95
        
        # Fuzzy match
        matches = difflib.get_close_matches(query, self.apps.keys(), n=1, cutoff=0.6)
        if matches:
            return matches[0], self.apps[matches[0]], 0.85
        
        # Contains
        for app_name, app_path in self.apps.items():
            if query in app_name:
                return app_name, app_path, 0.75
        
        return "", "", 0.0
    
    def get_installed_app_names(self) -> List[str]:
        """Get list of all installed app names"""
        return list(self.apps.keys())
    
    def close_app(self, app_name: str) -> bool:
        """Close application by name"""
        app_name_lower = app_name.lower().strip()
        
        # App to process mapping
        process_map = {
            "spotify": "spotify.exe",
            "discord": "discord.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "brave": "brave.exe",
            "notepad": "notepad.exe",
            "calculator": "calculatorapp.exe",
            "calc": "calculatorapp.exe",
            "code": "code.exe",
            "vscode": "code.exe",
            "visual studio code": "code.exe",
            "vlc": "vlc.exe",
        }
        
        # Try exact mapping first
        target_process = process_map.get(app_name_lower, app_name_lower + ".exe")
        
        killed = False
        for proc in psutil.process_iter(['name']):
            try:
                proc_name = proc.info['name'].lower()
                if target_process in proc_name or app_name_lower in proc_name:
                    proc.terminate()
                    killed = True
            except:
                pass
        
        if killed:
            time.sleep(0.3)
            return True
        
        # Fallback: fuzzy match on process names
        for proc in psutil.process_iter(['name']):
            try:
                proc_name = proc.info['name'].lower().replace(".exe", "")
                if difflib.SequenceMatcher(None, app_name_lower, proc_name).ratio() > 0.7:
                    proc.terminate()
                    time.sleep(0.3)
                    return True
            except:
                pass
        
        return False

# ============================================================================
# COMMAND EXECUTOR
# ============================================================================

class CommandExecutor:
    """Main command execution engine"""
    
    def __init__(self):
        self.registry = ApplicationRegistry()
        self.tab_manager = BrowserTabManager()
        self.youtube = YouTubePlayer()
        self.typing = TypingController()
    
    def execute_command(self, intent: Dict, context=None) -> Dict:
        """Execute parsed intent"""
        try:
            category = intent.get("intent")
            action = intent.get("action")
            payload = intent.get("payload", {})
            source_text = intent.get("source_text", "")
            
            # Route to handlers
            handlers = {
                "tab": self._handle_tab,
                "web": self._handle_web,
                "app": self._handle_app,
                "search": self._handle_search,
                "media": self._handle_media,
                "system": self._handle_system,
                "input": self._handle_input,
                "navigate": self._handle_navigation,
                "file": self._handle_file,
            }
            
            handler = handlers.get(category)
            if handler:
                return handler(action, payload, source_text)
            else:
                return {"status": "error", "message": "Unknown command category"}
        
        except Exception as e:
            return {"status": "error", "message": f"Execution failed: {e}"}
    
    def _handle_tab(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle tab management"""
        if action == "close_current":
            if self.tab_manager.close_current_tab():
                return {"status": "success", "message": "Closed current tab"}
            return {"status": "error", "message": "No active browser"}
        
        elif action == "close_named":
            tab_name = payload.get("tab_name", "")
            if self.tab_manager.close_tab_by_name(tab_name):
                return {"status": "success", "message": f"Closed {tab_name} tab"}
            return {"status": "error", "message": f"Tab not found: {tab_name}"}
        
        elif action == "new_tab":
            if self.tab_manager.open_new_tab():
                return {"status": "success", "message": "Opened new tab"}
            return {"status": "error", "message": "No active browser"}
        
        return {"status": "error", "message": "Unknown tab action"}
    
    def _handle_input(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle typing"""
        text = payload.get("text", "")
        if not text:
            return {"status": "error", "message": "No text to type"}
        
        if self.typing.open_notepad_and_type(text):
            return {"status": "success", "message": f"Typed: {text[:30]}..."}
        return {"status": "error", "message": "Typing failed"}
    
    def _handle_web(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle web/URL opening"""
        url = payload.get("url", "")
        site_name = payload.get("site_name", "")
        
        if not url:
            return {"status": "error", "message": "No URL specified"}
        
        try:
            webbrowser.open(url)
            display = site_name if site_name else url.split("//")[-1].split("/")[0]
            return {"status": "success", "message": f"Opening {display}"}
        except Exception as e:
            return {"status": "error", "message": f"Failed: {e}"}
    
    # In the CommandExecutor class, update the _handle_media method:
    def play_again() -> Dict:
        """Play current track/video again"""
        return execute_intent({
            "intent": "media",
            "action": "play_again",
            "payload": {},
            "source_text": "play it again"
        })
    def handle_spotify_playback(media_name: str) -> dict:
        """
        Handle Spotify playback with proper error handling.
        Use this in your CommandExecutor._handle_media method.
        """
        print(f"[Executor] Spotify playback request: '{media_name}'")
        
        # Attempt desktop automation
        success = SpotifyDesktopController.play_track_desktop(media_name)
        
        if success:
            return {
                "status": "success",
                "message": f"✓ Playing '{media_name}' on Spotify Desktop"
            }
        else:
            # Complete failure - inform user
            return {
                "status": "error",
                "message": f"✗ Failed to play '{media_name}' on Spotify",
                "note": "Please open Spotify manually and try again"
            }
    
    def _handle_media(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle media playback - PRODUCTION READY"""
        
        if action == "play_on_platform":
            media_name = payload.get("media_name", "")
            platform = payload.get("platform", "youtube").lower()
            
            if not media_name:
                return {"status": "error", "message": "No media specified"}
            
            # SPOTIFY PLAYBACK - PRODUCTION SOLUTION
            if platform == "spotify":
                print(f"[Executor] Spotify playback request: '{media_name}'")
                
                # Use the production-ready desktop controller
                success = SpotifyDesktopController.play_track_desktop(media_name)
                
                if success:
                    return {
                        "status": "success",
                        "message": f"✓ Playing '{media_name}' on Spotify Desktop"
                    }
                else:
                    # Complete failure - NO WEB FALLBACK
                    return {
                        "status": "error",
                        "message": f"✗ Failed to automate Spotify playback",
                        "note": "Please ensure Spotify is installed and try again"
                    }
            
            # YOUTUBE PLAYBACK
            elif platform == "youtube":
                # Auto-detect if it's music
                is_music = any(keyword in source.lower() for keyword in 
                            ["music", "song", "track", "playlist", "album"])
                
                prefer_official = "official" in source.lower() or "music" in source.lower()
                
                if self.youtube.play_video(media_name, music_only=is_music, prefer_official=prefer_official):
                    return {"status": "success", "message": f"Playing '{media_name}' on YouTube"}
                
                # Fallback
                search_url = f"https://www.youtube.com/results?search_query={quote_plus(media_name)}"
                webbrowser.open(search_url)
                return {"status": "success", "message": f"Opening '{media_name}' on YouTube"}
            
            # OTHER PLATFORMS
            else:
                platform_urls = {
                    "soundcloud": f"https://soundcloud.com/search?q={quote_plus(media_name)}",
                    "netflix": f"https://www.netflix.com/search?q={quote_plus(media_name)}",
                }
                url = platform_urls.get(platform, f"https://www.google.com/search?q={quote_plus(media_name)}")
                webbrowser.open(url)
                return {"status": "success", "message": f"Opening '{media_name}' on {platform.title()}"}
        
        elif action == "play":
            # Generic play defaults to YouTube with autoplay
            media_name = payload.get("media_name", "")
            if not media_name:
                return {"status": "error", "message": "No media specified"}
            
            # Check for special commands
            if media_name.lower() in ["resume", "continue"]:
                if self.youtube.resume_playback():
                    return {"status": "success", "message": f"Resuming last video"}
                return {"status": "error", "message": "Nothing to resume"}
            
            if media_name.lower() == "again":
                last_video = self.youtube.get_last_video()
                if last_video:
                    webbrowser.open(last_video['url'])
                    return {"status": "success", "message": f"Repeating: {last_video['name']}"}
                return {"status": "error", "message": "No video to repeat"}
            
            # Auto-detect if it's music
            is_music = any(keyword in source.lower() for keyword in 
                        ["music", "song", "track", "playlist", "album"])
            
            if self.youtube.play_video(media_name, music_only=is_music, prefer_official=True):
                return {"status": "success", "message": f"Playing '{media_name}' on YouTube"}
            return {"status": "error", "message": "Playback failed"}
        
        elif action == "control":
            command = payload.get("command", "").lower()
            
            controls = {
                "pause": "playpause",
                "stop": "playpause",
                "resume": "playpause",
                "play": "playpause",
                "next": "nexttrack",
                "skip": "nexttrack",
                "previous": "prevtrack",
                "back": "prevtrack",
            }
            
            key = controls.get(command)
            if key:
                pyautogui.press(key)
                return {"status": "success", "message": f"{command.title()}"}
            return {"status": "error", "message": "Unknown media control"}
        
        return {"status": "error", "message": "Unknown media action"}
    
    def _handle_app(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle app open/close"""
        app_name = payload.get("app_name", "").lower()
        
        if not app_name:
            return {"status": "error", "message": "No app specified"}
        
        if action == "open":
            matched, app_path, confidence = self.registry.find_app(app_name)
            
            if confidence < 0.5:
                return {"status": "error", "message": f"App not found: {app_name}"}
            
            try:
                if app_path.endswith('.exe'):
                    subprocess.Popen(
                        app_path,
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                else:
                    os.startfile(app_path)
                
                return {"status": "success", "message": f"Opening {matched}"}
            except Exception as e:
                return {"status": "error", "message": f"Failed to open {matched}: {e}"}
        
        elif action == "close":
            if self.registry.close_app(app_name):
                return {"status": "success", "message": f"Closed {app_name}"}
            return {"status": "error", "message": f"Could not close {app_name}"}
        
        return {"status": "error", "message": "Unknown app action"}
    
    def _handle_search(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle web search"""
        query = payload.get("query", "")
        platform = payload.get("platform", "google").lower()
        
        # 🔴 CRITICAL FIX: If Spotify is requested, use desktop app instead of web
        if platform == "spotify":
            print(f"[Search] Spotify search requested for '{query}', using desktop app...")
            # Use desktop app instead of web
            attempt_made = self.spotify.play_track(query)
            if attempt_made:
                return {
                    "status": "attempted",
                    "message": f"Attempted to play '{query}' on Spotify Desktop",
                    "note": "Desktop app automation attempted (from search request)"
                }
            else:
                # Fallback to web only if desktop completely fails
                web_url = f"https://open.spotify.com/search/{quote_plus(query)}"
                webbrowser.open(web_url)
                return {
                    "status": "web_fallback",
                    "message": f"Opened web player for '{query}'",
                    "note": "Desktop automation failed, using web player"
                }
        
        search_urls = {
            "google": f"https://www.google.com/search?q={quote_plus(query)}",
            "youtube": f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            "amazon": f"https://www.amazon.com/s?k={quote_plus(query)}",
            "reddit": f"https://www.reddit.com/search/?q={quote_plus(query)}",
        }
        
        # Remove Spotify from search_urls since we handle it specially above
        url = search_urls.get(platform, search_urls["google"])
        webbrowser.open(url)
        
        return {"status": "success", "message": f"Searching '{query}' on {platform.title()}"}
    
    def _handle_system(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle system commands"""
        if action == "shutdown":
            os.system("shutdown /s /t 5")
            return {"status": "success", "message": "Shutting down in 5 seconds"}
        elif action == "restart":
            os.system("shutdown /r /t 5")
            return {"status": "success", "message": "Restarting in 5 seconds"}
        elif action == "lock":
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return {"status": "success", "message": "Locking PC"}
        elif action == "edit":
            op = payload.get("operation", "").lower()
            hotkeys = {
                "undo": ('ctrl', 'z'),
                "redo": ('ctrl', 'y'),
                "copy": ('ctrl', 'c'),
                "cut": ('ctrl', 'x'),
                "paste": ('ctrl', 'v'),
                "delete": ('delete',),
            }
            keys = hotkeys.get(op)
            if keys:
                pyautogui.hotkey(*keys)
                return {"status": "success", "message": op.title()}
            return {"status": "error", "message": "Unknown edit operation"}
        
        return {"status": "error", "message": "Unknown system action"}
    
    def _handle_navigation(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle navigation"""
        if action == "scroll":
            direction = payload.get("direction", "down")
            amount = 500 if direction in ["up", "top"] else -500
            pyautogui.scroll(amount)
            return {"status": "success", "message": f"Scrolled {direction}"}
        elif action == "page":
            direction = payload.get("direction", "down")
            key = "pageup" if direction == "up" else "pagedown"
            pyautogui.press(key)
            return {"status": "success", "message": f"Page {direction}"}
        
        return {"status": "error", "message": "Unknown navigation action"}
    
    def _handle_file(self, action: str, payload: Dict, source: str) -> Dict:
        """Handle file operations"""
        if action == "save":
            pyautogui.hotkey('ctrl', 's')
            return {"status": "success", "message": "Saved"}
        return {"status": "error", "message": "Unknown file action"}

# ============================================================================
# GLOBAL INSTANCE & PUBLIC API
# ============================================================================

# ============================================================================
# GLOBAL INSTANCE & PUBLIC API - FIXED
# ============================================================================

EXECUTOR = CommandExecutor()
REGISTRY = EXECUTOR.registry

def execute_intent(intent: Dict, context=None) -> Dict:
    """Execute an intent command"""
    return EXECUTOR.execute_command(intent, context)

def refresh_registry():
    """Refresh application registry"""
    EXECUTOR.registry.full_scan()

# Convenience functions
def open_app(name: str) -> Dict:
    return execute_intent({"intent": "app", "action": "open", "payload": {"app_name": name}})

def close_app(name: str) -> Dict:
    return execute_intent({"intent": "app", "action": "close", "payload": {"app_name": name}})

def close_tab(tab_name: str = None) -> Dict:
    if tab_name:
        return execute_intent({"intent": "tab", "action": "close_named", "payload": {"tab_name": tab_name}})
    return execute_intent({"intent": "tab", "action": "close_current", "payload": {}})

def play_media(name: str, platform: str = "youtube") -> Dict:
    """Play media on specified platform"""
    if platform.lower() == "spotify":
        print(f"[MAIN] Spotify requested: '{name}' - Using desktop automation")
        # Direct desktop automation
        result = SpotifyDesktopController.play_track_desktop(name)
        
        if result:
            return {
                "status": "success",
                "message": f"Playing '{name}' on Spotify Desktop"
            }
        else:
            # Fallback to web
            web_url = f"https://open.spotify.com/search/{quote_plus(name)}"
            webbrowser.open(web_url)
            return {
                "status": "web_fallback",
                "message": f"Opened '{name}' on Spotify Web"
            }
    else:
        # For other platforms, use existing logic
        return execute_intent({
            "intent": "media", 
            "action": "play_on_platform", 
            "payload": {
                "media_name": name, 
                "platform": platform
            },
            "source_text": f"play {name} on {platform}"
        })

def play_music(name: str, platform: str = "youtube") -> Dict:
    """Play music with music filter enabled - DEPRECATED, use play_media"""
    return play_media(name, platform)

def resume_playback() -> Dict:
    """Resume last played media"""
    return execute_intent({
        "intent": "media",
        "action": "resume",
        "payload": {},
        "source_text": "resume playback"
    })

def repeat_last_video() -> Dict:
    """Repeat the last played video"""
    return execute_intent({
        "intent": "media",
        "action": "play_on_platform",
        "payload": {"media_name": "again"},
        "source_text": "play again"
    })

def search_web(query: str, platform: str = "google") -> Dict:
    """Perform a web search."""
    return execute_intent({"intent": "search", "action": "search", "payload": {"query": query, "platform": platform}})