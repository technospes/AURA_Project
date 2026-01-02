import os
import json
import time
import shutil
import subprocess
import webbrowser
import difflib
from pathlib import Path
CACHE_FILE = Path("app_cache.json")
CACHE_EXPIRY_DAYS = 7

# COMPREHENSIVE PROCESS MAP (Fixes "Close X" failing)
PROCESS_MAP = {
    # Development
    "vscode": "Code.exe",
    "vs": "Code.exe",
    "code": "Code.exe",
    "visual studio code": "Code.exe",
    "visual studio": "devenv.exe",
    "pycharm": "pycharm64.exe",
    "unity": "Unity.exe",
    
    # Browsers
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    
    # Office
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    
    # Media & Social
    "spotify": "Spotify.exe",
    "discord": "Discord.exe",
    "whatsapp": "WhatsApp.exe",
    "telegram": "Telegram.exe",
    "vlc": "vlc.exe",
    "steam": "steam.exe",
    "obs": "obs64.exe",
    
    # System
    "task manager": "Taskmgr.exe",
    "explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "terminal": "WindowsTerminal.exe"
}

# ============================================================================
# APP REGISTRY SYSTEM (Caching + Deep Scan)
# ============================================================================
class AppRegistry:
    def __init__(self):
        self.apps = {}
        self.load_cache()

    def load_cache(self):
        if CACHE_FILE.exists():
            last_modified = CACHE_FILE.stat().st_mtime
            if time.time() - last_modified > CACHE_EXPIRY_DAYS * 86400:
                print("[System] ⚠️ App cache is stale. Re-scanning...")
                self.scan_and_cache()
                return

            try:
                with open(CACHE_FILE, "r") as f:
                    self.apps = json.load(f)
                    print(f"[System] Loaded {len(self.apps)} apps from cache.")
                    return
            except: pass
        self.scan_and_cache()

    def scan_and_cache(self):
        print("[System] 🔄 Indexing installed apps...")
        start_time = time.time()
        self.apps = {}
        
        search_paths = [
            os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ["USERPROFILE"], r"Desktop"),
            os.path.join(os.environ["LOCALAPPDATA"], r"Programs"),
        ]
        
        for path in search_paths:
            if not os.path.exists(path): continue
            for root, _, files in os.walk(path):
                for file in files:
                    if file.lower().endswith((".lnk", ".exe", ".url")):
                        clean_name = file.rsplit(".", 1)[0].lower()
                        full_path = os.path.join(root, file)
                        self.apps[clean_name] = full_path
                        
                        # Smart Shortcuts
                        if "visual studio code" in clean_name: self.apps["vscode"] = full_path
                        if "discord" in clean_name: self.apps["discord"] = full_path

        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.apps, f, indent=4)
            print(f"[System] Indexing complete in {time.time() - start_time:.2f}s.")
        except: pass

    def get_path(self, app_name):
        app_name = app_name.lower().strip()
        if app_name in self.apps: return self.apps[app_name]
        
        for key, path in self.apps.items():
            if app_name == key: return path
            if f" {app_name} " in f" {key} ": return path
        
        matches = difflib.get_close_matches(app_name, self.apps.keys(), n=1, cutoff=0.6)
        if matches:
            print(f"[System] Fuzzy Match: '{app_name}' -> '{matches[0]}'")
            return self.apps[matches[0]]
        return None

REGISTRY = AppRegistry()

# ============================================================================
# PROTOCOL HANDLERS
# ============================================================================
def handle_protocol(app_name, payload=""):
    app = app_name.lower()
    if app == "spotify":
        if "play" in payload or "search" in payload:
            query = payload.replace("play", "").replace("search", "").strip()
            os.startfile(f"spotify:search:{query}") if query else os.startfile("spotify:")
        else: os.startfile("spotify:")
        return True
    elif app == "steam": os.startfile("steam://open/main"); return True
    elif app == "discord": os.startfile("discord://"); return True
    elif app in ["calculator", "calc"]: subprocess.Popen("calc.exe"); return True
    return False

# ============================================================================
# PUBLIC API
# ============================================================================
def open_url(url):
    if not url.startswith("http"): url = "https://" + url
    print(f"[System] 🌐 Opening URL: {url}")
    webbrowser.open(url)

def open_app(app_name, match_closest=True, output=False):
    app_name = app_name.strip().lower()

    # 1. URL Handler
    if any(x in app_name for x in [".com", ".org", ".net", ".io", ".in"]):
        open_url(app_name); return

    # 2. Refresh
    if app_name == "refresh apps": REGISTRY.scan_and_cache(); return

    # 3. Protocol Handler
    if app_name in ["spotify", "steam", "discord", "calculator", "calc"]:
        if handle_protocol(app_name, app_name): return

    # 4. Native Windows Commands
    if shutil.which(app_name) or app_name in ["notepad", "explorer", "cmd", "powershell"]:
        try: os.startfile(app_name); return
        except: pass

    # 5. Registry Search
    path = REGISTRY.get_path(app_name)
    if path:
        print(f"[System] 🚀 Launching: {path}")
        try: os.startfile(path); return
        except: pass
    
    # 6. Web Fallback
    print(f"[System] App not found locally. Using Web Fallback...")
    open_url(f"{app_name.replace(' ', '')}.com")

def close_app(app_name, match_closest=True, output=False):
    app_name = app_name.lower().strip()
    
    # Check map
    target = PROCESS_MAP.get(app_name)
    
    # If not in map, assume process name = app name (e.g. "notepad" -> "notepad.exe")
    if not target:
        if " " in app_name: target = app_name.split()[-1] + ".exe"
        else: target = app_name + ".exe"
            
    print(f"[System] Attempting to kill: {target}")
    os.system(f"taskkill /f /im {target}")

def play_music(song_name, platform="youtube"):
    print(f"[System] Request: Play '{song_name}' on {platform}")
    if "spotify" in platform: handle_protocol("spotify", f"play {song_name}")
    elif "apple" in platform: open_url(f"music.apple.com/us/search?term={song_name.replace(' ', '+')}")
    else: open_url(f"music.youtube.com/search?q={song_name.replace(' ', '+')}")

def search_web(query, platform="google"):
    print(f"[System] Search: '{query}' on {platform}")
    query = query.replace(" ", "+")
    if platform == "google": url = f"https://www.google.com/search?q={query}"
    elif "youtube" in platform: url = f"https://www.youtube.com/results?search_query={query}"
    else:
        domain = platform.replace(" ", "") + ".com"
        url = f"https://www.google.com/search?q=site:{domain}+{query}"
    webbrowser.open(url)