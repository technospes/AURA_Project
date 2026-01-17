"""
Native Opener (V21.0 - PRODUCTION READY)
Features: Proper Spotify desktop app control, YouTube autoplay, accurate app/tab management
"""
import os
import sys
import time
import json
import subprocess
import logging
from typing import Dict, Any, Optional
from difflib import SequenceMatcher
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

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# BROWSER TAB MANAGER
# ============================================================================

class BrowserTabManager:
    """Manages browser tabs and windows - FIXED VERSION"""
    
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
        Properly close browser tab by name - FIXED VERSION
        Strategy: Focus the tab, then close it directly
        """
        hwnd = BrowserTabManager.find_browser_tab(tab_name)
        
        if not hwnd:
            print(f"[Tab] Tab '{tab_name}' not found")
            return False
        
        print(f"[Tab] Found tab containing '{tab_name}'")
        
        try:
            # Step 1: Capture current active window
            original_active_hwnd = win32gui.GetForegroundWindow()
            is_already_focused = (original_active_hwnd == hwnd)
            
            # Step 2: Check if target window is minimized
            placement = win32gui.GetWindowPlacement(hwnd)
            was_minimized = placement[1] == win32con.SW_SHOWMINIMIZED
            
            # Step 3: Bring browser to foreground (restore if minimized)
            if was_minimized:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                time.sleep(0.2)
            
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)  # Wait for window to be fully focused
            
            # Step 4: Close the tab directly with Ctrl+W
            print(f"[Tab] Closing tab...")
            pyautogui.hotkey('ctrl', 'w')
            time.sleep(0.3)  # Wait for tab to close
            
            # Step 5: Restore original window focus if needed
            # Only restore if user wasn't already in the browser
            if not is_already_focused:
                try:
                    if win32gui.IsWindow(original_active_hwnd):
                        time.sleep(0.2)
                        win32gui.SetForegroundWindow(original_active_hwnd)
                except:
                    pass  # Don't fail if we can't restore focus
            
            print(f"[Tab] ✓ Successfully closed tab containing '{tab_name}'")
            return True
            
        except Exception as e:
            print(f"[Tab] Error closing tab: {e}")
            return False
    
    @staticmethod
    def close_tab_by_name_advanced(tab_name: str) -> bool:
        """
        Advanced tab closing with browser-specific optimizations
        Handles edge cases like last tab, pinned tabs, etc.
        """
        hwnd = BrowserTabManager.find_browser_tab(tab_name)
        
        if not hwnd:
            print(f"[Tab] Tab '{tab_name}' not found")
            return False
        
        print(f"[Tab] Found tab containing '{tab_name}'")
        
        try:
            # Get browser type
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            proc_name = proc.name().lower()
            
            is_chrome_based = any(x in proc_name for x in ['chrome', 'edge', 'brave'])
            is_firefox = 'firefox' in proc_name
            
            # Focus the window
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)
            
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.4)
            
            # Chrome/Edge/Brave: Use Ctrl+W
            if is_chrome_based:
                print(f"[Tab] Using Chrome-based close method")
                pyautogui.hotkey('ctrl', 'w')
                time.sleep(0.2)
            
            # Firefox: Use Ctrl+W
            elif is_firefox:
                print(f"[Tab] Using Firefox close method")
                pyautogui.hotkey('ctrl', 'w')
                time.sleep(0.2)
            
            # Unknown browser: Use Ctrl+W
            else:
                print(f"[Tab] Using generic close method")
                pyautogui.hotkey('ctrl', 'w')
                time.sleep(0.2)
            
            print(f"[Tab] ✓ Successfully closed tab containing '{tab_name}'")
            return True
            
        except Exception as e:
            print(f"[Tab] Error closing tab: {e}")
            import traceback
            traceback.print_exc()
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
            
            # STEP 9: Navigate to and play first result - AGGRESSIVE METHOD
            print(f"[Spotify] Navigating to first search result...")
            
            # Method: Click on first result instead of keyboard navigation
            # This is more reliable as Spotify's focus can be unpredictable
            
            # Wait for search results to fully render
            time.sleep(1.0)
            
            # Strategy: Tab to results, then use arrow keys multiple times to ensure
            # we're on the first actual track (not playlist/artist)
            
            # Tab out of search bar
            pyautogui.press('tab')
            time.sleep(0.4)
            
            # Sometimes there are category headers ("Songs", "Artists", etc.)
            # Press Tab again to get to the actual tracks list
            pyautogui.press('tab')
            time.sleep(0.4)
            
            # Now we should be in the tracks section
            # Press Down to make sure first track is selected
            pyautogui.press('down')
            time.sleep(0.3)
            
            # Press Up to go back to truly first track
            pyautogui.press('up')
            time.sleep(0.3)
            
            # Double-click Enter to ensure we both select AND play
            print(f"[Spotify] Playing selected track...")
            pyautogui.press('enter')
            time.sleep(0.4)
            pyautogui.press('enter')
            time.sleep(0.5)
            
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
    """Automated typing with punctuation and formatting support"""
    
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
            
            return TypingController.type_with_formatting(text)
        except Exception as e:
            print(f"[Typing] Error: {e}")
            return False
    
    @staticmethod
    def type_with_formatting(text: str) -> bool:
        """
        Type text with proper spacing and punctuation handling
        
        Features:
        - Adds spaces after punctuation
        - Handles newlines
        - Preserves capitalization
        """
        try:
            # Split into sentences for proper spacing
            import re
            
            # Handle special punctuation that needs spaces
            text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)  # Space after sentence end
            text = re.sub(r'([,;:])([^\s])', r'\1 \2', text)  # Space after commas
            
            # Type character by character with smart spacing
            prev_char = ''
            for i, char in enumerate(text):
                # Handle special characters
                if char == '\n':
                    pyautogui.press('enter')
                    time.sleep(0.05)
                elif char == '\t':
                    pyautogui.press('tab')
                    time.sleep(0.05)
                else:
                    try:
                        # Check if we need a space before this character
                        # (e.g., after punctuation if not already there)
                        if i > 0 and prev_char in '.!?,;:' and char not in ' \n\t':
                            if text[i-1:i] != ' ':  # Only add if no space already
                                pyautogui.write(' ', interval=0.02)
                        
                        # Type the character
                        pyautogui.write(char, interval=0.02)
                    except:
                        # Fallback for special characters
                        pyautogui.press(char)
                        time.sleep(0.02)
                
                prev_char = char
            
            return True
            
        except Exception as e:
            print(f"[Typing] Error: {e}")
            return False
    
    @staticmethod
    def type_in_active_window(text: str) -> bool:
        """Type text in currently active window (legacy method)"""
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
    
    def speak_text(text: str):
        """Convert text to speech (Windows)"""
        try:
            import platform
            
            if platform.system() == "Windows":
                import win32com.client
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Speak(text)
            else:
                # For Linux/Mac
                import os
                os.system(f'say "{text}"')
                
        except Exception as e:
            print(f"[TTS] Could not speak: {e}")

    def _execute_command_async(self, intent_data: dict):
        """Execute command asynchronously"""
        try:
            result = execute_intent(intent_data)
            
            # FIX: Check if 'message' key exists
            if result["status"] == "success":
                message = result.get("message", "Command executed successfully")
                print(f"[✓] {message}")
            else:
                message = result.get("message", "Command failed")
                print(f"[✗] {message}")
            
            self.commands_processed += 1
            
        except Exception as e:
            logger.error(f"Execute command error: {e}")
            import traceback
            traceback.print_exc()
            print(f"[✗] Command execution failed")

    def _get_cached_result(self, intent_data: dict) -> Optional[dict]:
        """Get cached result only for safe commands"""
        # Double-check this is a cacheable command
        action = intent_data.get("action", "").lower()
        
        # SAFE actions to cache (only these!)
        CACHEABLE_ACTIONS = {
            "open", "close", "minimize", "maximize", 
            "type", "play", "closetab", "close_tab"
        }
        
        if action not in CACHEABLE_ACTIONS:
            return None
        
        cache_key = f"{action}:{intent_data.get('target')}"
        
        # Check cache with timestamp validation
        if cache_key in self._command_cache:
            cached_entry = self._command_cache[cache_key]
            
            # Check if cache entry is expired (30 seconds for most commands)
            if time.time() - cached_entry.get("timestamp", 0) < 30:
                self.stats["cache_hits"] += 1
                return cached_entry.get("result")
            else:
                # Remove expired cache entry
                del self._command_cache[cache_key]
        
        return None

    def _cache_result(self, intent_data: dict, result: dict):
        """Cache result with safety checks"""
        action = intent_data.get("action", "").lower()
        target = intent_data.get("target", "").lower()
        
        # FINAL SAFETY CHECK: Never cache these
        NEVER_CACHE = {
            "what": ["weather", "time", "date", "news"],
            "search": ["weather", "time", "stock", "news"],
            "chat": [],  # All chat commands
            "ask": [],   # All ask commands
            "explain": []  # All explain commands
        }
        
        for forbidden_action, forbidden_patterns in NEVER_CACHE.items():
            if action == forbidden_action:
                if not forbidden_patterns or any(p in target for p in forbidden_patterns):
                    logger.debug(f"SAFETY: Not caching {action} command")
                    return
        
        # Add timestamp to cache entry
        cache_key = f"{action}:{intent_data.get('target')}"
        self._command_cache[cache_key] = {
            "result": result,
            "timestamp": time.time(),
            "action": action,
            "target": target
        }
        
        logger.debug(f"Cached result for: {cache_key}")

    def _handle_cached_result(self, result: dict):
        """Handle cached result with appropriate indicators"""
        message = result.get("message", "")
        action = result.get("original_action", "")
        
        # Different cache indicators based on action type
        if action in ["open", "close", "play"]:
            print(f"[⚡] {message} (from cache)")
        else:
            print(f"[✓] {message} (cached)")
        
        self.commands_processed += 1

    def _handle_ai_conversation(self, query: str):
        """Handle AI conversations - called only from _execute_command_async"""
        try:
            if not self.llama_brain:
                print("[Aura] AI brain not available. Searching web...")
                search_url = f"https://www.google.com/search?q={quote_plus(query)}"
                webbrowser.open(search_url)
                print(f"[Aura] Opened web search for: {query}")
                return
            
            print("[Aura] Let me check...")
            
            # Use the chat method which has web_search tool
            response = self.llama_brain.chat(query)
            
            # Display response
            print(f"\n[Aura] {response}\n")
            
        except Exception as e:
            logger.error(f"AI conversation error: {e}")
            print("[Aura] Sorry, I couldn't process that.")
        
    def _process_execution_result(self, result: dict):
        """Process execution result with safety"""
        status = result.get("status", "")
        
        # Special handling for conversational queries
        if status == "conversation":
            query = result.get("query", "")
            if query:
                # Check if it's a time-sensitive query
                if self._is_time_sensitive_query(query):
                    # Don't use any cached data for time-sensitive queries
                    self._handle_fresh_conversation(query)
                else:
                    self._handle_conversation_result(result)
            return
        
        # Use handler dictionary for other statuses
        status_handlers = {
            "success": self._handle_success_result,
            "error": self._handle_error_result,
            "attempted": self._handle_attempted_result,
            "web_fallback": self._handle_fallback_result
        }
        
        handler = status_handlers.get(status)
        
        if handler:
            handler(result)
        else:
            print(f"[•] {result.get('message', 'Command executed')}")
        
        # Count as processed if not conversational
        if status != "conversation":
            self.commands_processed += 1

    def _is_time_sensitive_query(self, query: str) -> bool:
        """Check if a query is time-sensitive (should never be cached)"""
        query_lower = query.lower()
        
        TIME_SENSITIVE_KEYWORDS = {
            "weather", "time", "date", "now", "today",
            "current", "latest", "stock", "price",
            "score", "news", "update", "temperature",
            "forecast", "traffic", "live"
        }
        
        # Check for time-sensitive keywords
        if any(keyword in query_lower for keyword in TIME_SENSITIVE_KEYWORDS):
            return True
        
        # Check for question words that imply time sensitivity
        question_patterns = ["what's", "what is", "how is", "when is", "where is"]
        if any(pattern in query_lower for pattern in question_patterns):
            # Check what they're asking about
            for keyword in TIME_SENSITIVE_KEYWORDS:
                if keyword in query_lower:
                    return True
        
        return False

    def _handle_fresh_conversation(self, query: str):
        """Handle time-sensitive conversation queries with fresh data"""
        query_lower = query.lower()
        
        # Handle common time-sensitive queries locally if possible
        if "time" in query_lower or "what time" in query_lower:
            current_time = time.strftime("%I:%M %p")
            print(f"[🕒] It's {current_time}")
            return
        
        if "date" in query_lower or "what date" in query_lower:
            current_date = time.strftime("%B %d, %Y")
            print(f"[📅] Today is {current_date}")
            return
        
        if "day" in query_lower or "what day" in query_lower:
            current_day = time.strftime("%A")
            print(f"[📆] It's {current_day}")
            return
        
        # For other time-sensitive queries, indicate fresh response
        print(f"[🔍] Getting latest information for: {query}")
        # This would typically call an API for fresh data
        
    def _handle_conversation_result(self, result: dict):
        """Handle non-time-sensitive conversation results"""
        query = result.get("query", "")
        if query:
            # Try local answers first
            local_response = self._try_local_answer(query)
            if local_response:
                print(f"[🤖] {local_response}")
            else:
                print(f"[💬] I'll help you with: {query}")
    
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

_EXECUTOR = CommandExecutor()
REGISTRY = _EXECUTOR.registry.apps
def detect_shutdown_intent(command_text: str, current_intent: dict) -> dict:
        """Detect if a command is actually a shutdown command"""
        command_lower = command_text.lower()
        
        shutdown_patterns = [
            "shutdown", "shut down", "turn off", "power off", "power down",
            "shut the computer", "shut the pc", "shut the laptop"
        ]
        
        restart_patterns = [
            "restart", "reboot", "reset", "restart computer", "reboot pc"
        ]
        
        sleep_patterns = [
            "sleep", "suspend", "hibernate", "put to sleep", "go to sleep"
        ]
        
        for pattern in shutdown_patterns:
            if pattern in command_lower:
                return {
                    "action": "shutdown",
                    "target": "computer",
                    "confidence": 0.95,
                    "parameters": {}
                }
        
        for pattern in restart_patterns:
            if pattern in command_lower:
                return {
                    "action": "restart",
                    "target": "computer",
                    "confidence": 0.95,
                    "parameters": {}
                }
        
        for pattern in sleep_patterns:
            if pattern in command_lower:
                return {
                    "action": "sleep",
                    "target": "computer",
                    "confidence": 0.95,
                    "parameters": {}
                }
        
        # Return original intent if no shutdown detected
        return current_intent

def execute_intent(intent_data: dict) -> dict:
    """
    MAIN EXECUTION FUNCTION - Fixed version with weather handling and power commands
    """
    try:
        original_text = intent_data.get("original_text", "")
        
        # 🔥 NEW: Detect shutdown intents BEFORE processing
        intent_data = detect_shutdown_intent(original_text, intent_data)
        
        action = intent_data.get("action", "").lower()
        target = intent_data.get("target", "").strip()
        parameters = intent_data.get("parameters", {})
        logger.info(f"[Execute] Action: '{action}', Target: '{target}'")
        
        target_lower = target.lower()
        
        # ============================================================
        # 🔥 NEW: POWER/SHUTDOWN COMMANDS - Handle FIRST (before weather)
        # ============================================================
        if action in ["shutdown", "restart", "reboot", "sleep", "hibernate"]:
            return handle_power_command(action, target, parameters)
        
        # Also check if this is a shutdown disguised as "close computer"
        if action == "close" and target_lower in ["computer", "pc", "laptop", "system"]:
            print(f"[Power] Detected 'close {target}' -> converting to shutdown")
            return handle_power_command("shutdown", "computer", parameters)
        
        # ============================================================
        # 🔥 FIX: WEATHER QUERIES - Handle BEFORE conversation routing
        # ============================================================
        if any(keyword in target_lower for keyword in ["weather", "temperature", "forecast"]):
            return _handle_weather_query(target_lower)
        
        if action in ["chat", "ask", "tell", "what", "how", "explain", "weather", "who", "why"]:
            return handle_conversational_query(target, parameters)
        
        # ============================================================
        # SIMPLE GREETINGS - No change needed
        # ============================================================
        simple_greetings = {
            "how are you": "I'm doing great! Ready to help you with anything.",
            "how are you doing": "I'm functioning perfectly! What can I do for you?",
            "hello": "Hello! How can I help you today?",
            "hi": "Hi there! What can I do for you?",
            "hey": "Hey! How can I assist you?",
        }
        
        if action in ["chat", "ask"] and target_lower in simple_greetings:
            return {
                "status": "success",
                "message": f"[Aura] {simple_greetings[target_lower]}"
            }
        
        # ============================================================
        # TIME/DATE QUERIES - No change needed
        # ============================================================
        if action == "what" and any(word in target_lower for word in ["time", "date", "day"]):
            if "time" in target_lower:
                current_time = time.strftime("%I:%M %p")
                return {"status": "success", "message": f"[🕐] It's {current_time}"}
            elif "date" in target_lower:
                current_date = time.strftime("%B %d, %Y")
                return {"status": "success", "message": f"[📅] Today is {current_date}"}
            elif "day" in target_lower:
                current_day = time.strftime("%A")
                return {"status": "success", "message": f"[📆] It's {current_day}"}
        
        # ============================================================
        # ACTION HANDLERS - Updated with conversation fix
        # ============================================================
        action_handlers = {
            # 🔥 FIX: Conversation queries now properly return for AI handling
            "chat": lambda: {"status": "conversation", "query": target},
            "ask": lambda: {"status": "conversation", "query": target},
            "explain": lambda: {"status": "conversation", "query": target},
            "what": lambda: {"status": "conversation", "query": target},
            
            # Other handlers (unchanged)
            "open": lambda: _handle_open_action(target_lower, target),
            "close": lambda: _handle_close_action(target_lower, target),
            "closetab": lambda: _handle_tab_close(),
            "play": lambda: _handle_play_action(target, parameters),
            "search": lambda: _handle_search_action(target, parameters),
            "type": lambda: _handle_type_action(target),
            "minimize": lambda: _handle_minimize(),
            "maximize": lambda: _handle_maximize(),
        }
        
        if action in action_handlers:
            return action_handlers[action]()
        
        return {"status": "error", "message": f"Unknown action: {action}"}
    
    except Exception as e:
        logger.error(f"Execute intent error: {e}")
        return {"status": "error", "message": str(e)}
    
def handle_power_command(action: str, target: str, parameters: dict) -> dict:
    """Handle shutdown, restart, sleep commands"""
    try:
        import platform
        
        system_os = platform.system().lower()
        
        # Map common shutdown phrases
        shutdown_phrases = ["shutdown", "shut down", "power off", "turn off", "power down"]
        restart_phrases = ["restart", "reboot", "reset"]
        sleep_phrases = ["sleep", "suspend", "hibernate"]
        
        command_text = f"{action} {target}".lower().strip()
        
        # Check which command to execute
        if any(phrase in command_text for phrase in shutdown_phrases):
            if system_os == "windows":
                # Shutdown computer (force after 60 seconds)
                subprocess.run(["shutdown", "/s", "/t", "60"])
                return {
                    "status": "success",
                    "message": "Computer will shut down in 60 seconds. Say 'cancel shutdown' to cancel."
                }
            elif system_os == "linux" or system_os == "darwin":  # Darwin = macOS
                subprocess.run(["shutdown", "-h", "+1"])
                return {
                    "status": "success", 
                    "message": "Computer will shut down in 1 minute."
                }
                
        elif any(phrase in command_text for phrase in restart_phrases):
            if system_os == "windows":
                subprocess.run(["shutdown", "/r", "/t", "60"])
                return {
                    "status": "success",
                    "message": "Computer will restart in 60 seconds."
                }
            elif system_os == "linux" or system_os == "darwin":
                subprocess.run(["shutdown", "-r", "+1"])
                return {
                    "status": "success",
                    "message": "Computer will restart in 1 minute."
                }
                
        elif any(phrase in command_text for phrase in sleep_phrases):
            if system_os == "windows":
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                return {
                    "status": "success",
                    "message": "Putting computer to sleep."
                }
            elif system_os == "linux":
                subprocess.run(["systemctl", "suspend"])
                return {
                    "status": "success",
                    "message": "Putting computer to sleep."
                }
            elif system_os == "darwin":
                subprocess.run(["pmset", "sleepnow"])
                return {
                    "status": "success",
                    "message": "Putting computer to sleep."
                }
        
        # Default fallback
        return {
            "status": "error",
            "message": f"Could not understand power command: {command_text}"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Power command failed: {str(e)}"
        }

def handle_conversational_query(query: str, parameters: dict) -> dict:
    """Handle conversational queries using Groq API"""
    try:
        from groq import Groq
        
        # Ensure query is not empty
        if not query or len(query.strip()) < 2:
            return {
                "status": "error",
                "message": "Query is empty or too short"
            }
        
        # Get API key
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            return {
                "status": "error",
                "message": "GROQ_API_KEY not found"
            }
        
        client = Groq(api_key=api_key)
        
        # Prepare the conversation
        messages = [
            {
                "role": "system",
                "content": """You are Aura, a helpful and friendly voice assistant. 
                Keep your responses concise but friendly. 
                If asked about weather, provide a simple weather update.
                If asked how you are, respond positively.
                Limit responses to 1-2 sentences."""
            },
            {
                "role": "user",
                "content": query
            }
        ]
        
        # Make API call
        response = client.chat.completions.create(
            messages=messages,
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            max_tokens=150
        )
        
        answer = response.choices[0].message.content.strip()
        
        # Print the response
        print(f"\n[Aura] {answer}\n")
        
        # You could add text-to-speech here if you want
        # speak_text(answer)
        
        return {
            "status": "success",
            "message": f"Answered: {query[:30]}..."
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not answer query: {str(e)}"
        }
    
def _handle_weather_query(query_lower: str) -> dict:
    """Handle weather queries with location extraction"""
    try:
        from urllib.parse import quote_plus
        import webbrowser
        
        # Extract location
        location = ""
        for keyword in ["weather in ", "temperature in ", "forecast for "]:
            if keyword in query_lower:
                location = query_lower.split(keyword, 1)[1].strip()
                break
        
        # If no location found, try common patterns
        if not location:
            words = query_lower.replace("weather", "").replace("temperature", "").replace("forecast", "").strip().split()
            if words:
                location = " ".join(words)
        
        # Build search query
        if location:
            search_query = f"weather in {location}"
        else:
            search_query = "weather"
        
        # Open Google weather
        search_url = f"https://www.google.com/search?q={quote_plus(search_query)}"
        webbrowser.open(search_url)
        
        message = f"Showing weather for {location}" if location else "Showing current weather"
        return {"status": "success", "message": f"[🌤️] {message}"}
    
    except Exception as e:
        logger.error(f"Weather query error: {e}")
        return {"status": "error", "message": "Could not fetch weather"}
# ============================================================
# HELPER FUNCTIONS - MODULAR AND REUSABLE
# ============================================================

def _handle_conversation(query: str) -> dict:
    """Handle conversational queries"""
    return {
        "status": "conversation",
        "action": "chat",
        "query": query,
        "message": f"I'll help you with: {query}"
    }

def _handle_what_query(query_lower: str) -> dict:
    """Handle 'what' queries (weather, time, etc.)"""
    if "weather" in query_lower:
        location = query_lower.replace("weather", "").replace("in", "").strip()
        if location:
            search_query = f"weather in {location}"
        else:
            search_query = "weather"
        webbrowser.open(f"https://www.google.com/search?q={quote_plus(search_query)}")
        return {"status": "success", "message": f"Showing weather for {location or 'current location'}"}
    
    if "time" in query_lower or "date" in query_lower:
        current_time = time.strftime("%I:%M %p")
        current_date = time.strftime("%B %d, %Y")
        return {
            "status": "conversation",
            "action": "what",
            "query": query_lower,
            "message": f"It's {current_time} on {current_date}"
        }
    
    # Default for other "what" questions
    return _handle_conversation(query_lower)

def _handle_open_action(target_lower: str, target_original: str) -> dict:
    """Handle open commands - APPS FIRST, then websites"""
    if not target_lower:
        return {"status": "error", "message": "No target specified"}
    
    # 🔥 CRITICAL FIX: Check for INSTALLED APP first
    app_result = _try_open_desktop_app(target_lower)
    if app_result["status"] == "success":
        return app_result  # Found and opened app, stop here
    
    # Website mapping (only if app not found)
    WEBSITE_MAP = {
        'youtube': 'https://www.youtube.com',
        'instagram': 'https://www.instagram.com',
        'facebook': 'https://www.facebook.com',
        'twitter': 'https://twitter.com',
        'reddit': 'https://reddit.com',
        'github': 'https://github.com',
        'gmail': 'https://mail.google.com',
        'google': 'https://google.com',
        'linkedin': 'https://linkedin.com',
        # NOTE: Discord, Spotify removed from here - should open desktop apps
    }
    
    # Only open website if app not found
    if target_lower in WEBSITE_MAP:
        webbrowser.open(WEBSITE_MAP[target_lower])
        return {"status": "success", "message": f"Opening {target_lower} website"}
    
    # Check if it looks like a URL
    URL_INDICATORS = ['.com', '.in', '.org', '.net', '.io', 'www.', 'http']
    if any(indicator in target_original for indicator in URL_INDICATORS):
        url = target_original
        if not url.startswith('http'):
            if not url.startswith('www.'):
                url = 'www.' + url
            url = 'https://' + url
        webbrowser.open(url)
        return {"status": "success", "message": f"Opening {url}"}
    
    # If nothing worked
    return {"status": "error", "message": f"Could not find: {target_lower}"}

def _try_open_desktop_app(app_name: str) -> dict:
    """Try to open desktop app - returns success/error status"""
    matched, app_path, confidence = _EXECUTOR.registry.find_app(app_name)
    
    if confidence < 0.5:
        return {"status": "error", "message": f"App not found: {app_name}"}
    
    try:
        if app_path.endswith('.exe'):
            subprocess.Popen(app_path, shell=False)
        else:
            os.startfile(app_path)
        return {"status": "success", "message": f"Opening {matched}"}
    except Exception as e:
        logger.error(f"Failed to open app: {e}")
        return {"status": "error", "message": f"Failed to open: {e}"}


def _handle_close_action(target_lower: str, target_original: str) -> dict:
    """Handle close commands for apps, websites, and tabs"""
    if not target_lower:
        return {"status": "error", "message": "No target specified"}
    
    # Check if it's a website/browser tab
    WEB_KEYWORDS = [
        'youtube', 'instagram', 'facebook', 'twitter', 'reddit',
        'github', 'gmail', 'google', 'netflix', 'amazon',
        'whatsapp', 'linkedin', 'spotify', 'discord'
    ]
    
    # Check for tab closing (contains "tab" keyword)
    if "tab" in target_lower:
        # Extract the actual site name
        for keyword in WEB_KEYWORDS:
            if keyword in target_lower:
                if _EXECUTOR.tab_manager.close_tab_by_name(keyword):
                    return {"status": "success", "message": f"Closed {keyword} tab"}
    
    # Check if it's a website (close browser tab)
    for site in WEB_KEYWORDS:
        if site in target_lower:
            if _EXECUTOR.tab_manager.close_tab_by_name(site):
                return {"status": "success", "message": f"Closed {site} tab"}
    
    # Otherwise, close desktop app
    if _EXECUTOR.registry.close_app(target_lower):
        return {"status": "success", "message": f"Closed {target_lower}"}
    
    return {"status": "error", "message": f"Could not close {target_lower}"}

def _handle_tab_close() -> dict:
    """Close current browser tab"""
    if _EXECUTOR.tab_manager.close_current_tab():
        return {"status": "success", "message": "Closed current tab"}
    return {"status": "error", "message": "No active browser"}

def _handle_play_action(target: str, parameters: dict) -> dict:
    """Handle media playback"""
    if not target:
        return {"status": "error", "message": "No media specified"}
    
    platform = parameters.get("platform", "youtube").lower()
    
    if platform == "spotify":
        success = SpotifyDesktopController.play_track_desktop(target)
        if success:
            return {"status": "success", "message": f"Playing '{target}' on Spotify"}
        return {"status": "error", "message": f"Failed to play on Spotify"}
    
    elif platform == "youtube":
        if _EXECUTOR.youtube.play_video(target):
            return {"status": "success", "message": f"Playing '{target}' on YouTube"}
        return {"status": "error", "message": "YouTube playback failed"}
    
    return {"status": "error", "message": f"Unsupported platform: {platform}"}

def _handle_search_action(target: str, parameters: dict) -> dict:
    """Handle web searches"""
    if not target:
        return {"status": "error", "message": "No search query"}
    
    platform = parameters.get("platform", "google").lower()
    
    SEARCH_URLS = {
        "google": f"https://google.com/search?q={quote_plus(target)}",
        "youtube": f"https://youtube.com/results?search_query={quote_plus(target)}",
        "weather": f"https://google.com/search?q=weather+{quote_plus(target)}",
        "amazon": f"https://amazon.in/s?k={quote_plus(target)}",
        "wikipedia": f"https://en.wikipedia.org/wiki/Special:Search?search={quote_plus(target)}",
        "flipkart": f"https://flipkart.com/search?q={quote_plus(target)}",
        "spotify": f"https://open.spotify.com/search/{quote_plus(target)}",
    }
    
    # Auto-detect search type
    if "weather" in target.lower():
        platform = "weather"
    elif "wiki" in target.lower() or "wikipedia" in target.lower():
        platform = "wikipedia"
    
    url = SEARCH_URLS.get(platform, SEARCH_URLS["google"])
    webbrowser.open(url)
    
    return {"status": "success", "message": f"Searching '{target}' on {platform.title()}"}

def _handle_type_action(text: str) -> dict:
    """Handle typing with punctuation restoration"""
    if not text:
        return {"status": "error", "message": "No text to type"}
    
    # 🔥 FIX: Restore punctuation that brain might have stripped
    text = _restore_punctuation(text)
    
    if _EXECUTOR.typing.type_with_formatting(text):
        return {"status": "success", "message": f"Typed: {text[:50]}..."}
    return {"status": "error", "message": "Typing failed"}
 
def _restore_punctuation(text: str) -> str:
    """Restore common punctuation patterns"""
    import re
    
    # Question patterns
    question_words = ["what", "when", "where", "who", "why", "how", "which", "whose", "whom"]
    for word in question_words:
        pattern = rf'\b{word}\b.*$'
        if re.search(pattern, text, re.IGNORECASE) and not text.endswith('?'):
            text = text + '?'
            break
    
    # Sentence endings (if not already punctuated)
    if not text[-1] in ['.', '?', '!', ',', ';', ':']:
        # Check if it's a statement
        if any(text.lower().startswith(word) for word in ["i'm", "i am", "currently", "it's", "it is"]):
            text = text + '.'
    
    return text

def _handle_minimize() -> dict:
    """Minimize current window"""
    pyautogui.hotkey('win', 'down')
    return {"status": "success", "message": "Window minimized"}

def _handle_maximize() -> dict:
    """Maximize current window"""
    pyautogui.hotkey('win', 'up')
    return {"status": "success", "message": "Window maximized"}

def refresh_registry():
    """Refresh application registry"""
    _EXECUTOR.registry.full_scan()

# Convenience functions
def open_app(app_name: str) -> dict:
    """
    Open an application by name with Windows-safe launching
    Returns: dict with status and message
    """
    try:
        app_name = app_name.lower().strip()
        logger.info(f"Attempting to open: {app_name}")
        
        # Check direct match in registry
        if app_name in REGISTRY:
            app_info = REGISTRY[app_name]
            path = app_info["path"]
            
            logger.info(f"Found in registry: {path}")
            
            # ✅ Windows-safe launch
            if path.startswith("ms-"):
                # Special URI schemes (like ms-settings:)
                os.system(f'start {path}')
            else:
                # Use START command for better Windows compatibility
                # This handles spaces in paths and proper shell execution
                subprocess.Popen(f'start "" "{path}"', shell=True)
            
            return {
                "status": "success",
                "message": f"Opening {app_name}..."
            }
        
        # Check aliases
        for key, app_info in REGISTRY.items():
            aliases = app_info.get("aliases", [])
            if app_name in aliases:
                path = app_info["path"]
                logger.info(f"Found via alias '{app_name}' -> {key}: {path}")
                
                if path.startswith("ms-"):
                    os.system(f'start {path}')
                else:
                    subprocess.Popen(f'start "" "{path}"', shell=True)
                
                return {
                    "status": "success",
                    "message": f"Opening {key}..."
                }
        
        # Try fuzzy matching
        best_match = None
        best_score = 0
        
        for key in REGISTRY.keys():
            from difflib import SequenceMatcher
            score = SequenceMatcher(None, app_name, key).ratio()
            if score > best_score and score > 0.6:  # 60% similarity threshold
                best_score = score
                best_match = key
        
        if best_match:
            app_info = REGISTRY[best_match]
            path = app_info["path"]
            logger.info(f"Fuzzy match: '{app_name}' -> {best_match} (score: {best_score:.2f})")
            
            if path.startswith("ms-"):
                os.system(f'start {path}')
            else:
                subprocess.Popen(f'start "" "{path}"', shell=True)
            
            return {
                "status": "success",
                "message": f"Opening {best_match}..."
            }
        
        # Last resort: try to launch directly (might work for system apps)
        logger.warning(f"App not in registry, trying direct launch: {app_name}")
        try:
            subprocess.Popen(f'start "" "{app_name}.exe"', shell=True)
            return {
                "status": "success",
                "message": f"Attempting to open {app_name}..."
            }
        except Exception as e:
            logger.error(f"Direct launch failed: {e}")
            return {
                "status": "error",
                "message": f"Could not find application: {app_name}"
            }
    
    except Exception as e:
        logger.error(f"Error opening {app_name}: {e}")
        return {
            "status": "error",
            "message": f"Failed to open {app_name}: {str(e)}"
        }
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
# ============================================================================
# WINDOW MANAGEMENT (Add these to native_opener.py)
# ============================================================================
def minimize_window() -> Dict:
    """Minimizes the active window"""
    try:
        import pyautogui
        # Windows shortcut to minimize active window
        pyautogui.hotkey('win', 'down')
        pyautogui.hotkey('win', 'down') # Press twice to ensure minimize
        return {"status": "success", "message": "Window minimized"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def maximize_window() -> Dict:
    """Maximizes the active window"""
    try:
        import pyautogui
        # Windows shortcut to maximize active window
        pyautogui.hotkey('win', 'up')
        pyautogui.hotkey('win', 'up')
        return {"status": "success", "message": "Window maximized"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def type_text(text: str) -> Dict:
    """Types text directly"""
    return execute_intent({"intent": "input", "action": "type", "payload": {"text": text}})

def search_web(query: str, platform: str = "google") -> Dict:
    """Perform a web search."""
    return execute_intent({"intent": "search", "action": "search", "payload": {"query": query, "platform": platform}})