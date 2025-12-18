import win32gui
import win32process

class ContextManager:
    def get_active_app_name(self):
        try:
            window = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(window)
            # This is a simplified way to get app name, robust methods require psutil
            # For MVP, we return window title
            title = win32gui.GetWindowText(window).lower()
            
            if "chrome" in title or "edge" in title:
                return "browser"
            elif "vlc" in title or "player" in title:
                return "media"
            elif "code" in title or "pycharm" in title:
                return "coding"
            else:
                return "desktop"
        except:
            return "desktop"