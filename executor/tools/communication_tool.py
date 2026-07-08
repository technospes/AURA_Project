"""
ATOMIC COMMUNICATION TOOL — Split Search from Action
=====================================================
search_contact: Opens WhatsApp, searches, highlights (no Enter)
call_whatsapp:  Presses Enter on highlighted contact, then calls
send_whatsapp_message: Presses Enter, types message, sends
"""
import asyncio
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


class UnifiedCommunicationTool:
    def __init__(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
            self.pg = pyautogui
        except ImportError:
            self.pg = None
        
        try:
            import uiautomation as auto
            self.auto = auto
        except ImportError:
            self.auto = None

    async def execute(self, action: str, params: Dict,
                      intent: Dict = None, context: Dict = None,
                      step_results: list = None) -> Dict:
        try:
            if action == "search_contact":
                return await self._search(params)
            elif action == "call_whatsapp":
                return await self._call(params)
            elif action == "send_whatsapp_message":
                return await self._send(params)
            return {"success": False, "error": f"Unknown action: {action}"}
        except Exception as e:
            logger.error(f"[CommTool] Error: {e}")
            return {"success": False, "error": str(e)}

    async def _search(self, params: Dict) -> Dict:
        """Open WhatsApp, search contact, highlight first result. Does NOT press Enter."""
        contact = params.get("contact", "").strip()
        if not contact:
            return {"success": False, "error": "No contact specified"}
        if not self.pg:
            return {"success": False, "error": "pyautogui not available"}
        
        # Launch WhatsApp via protocol
        os.startfile("whatsapp://")
        await asyncio.sleep(2.0)
        
        # Wait for WhatsApp window
        import time as _time
        start = _time.time()
        ready = False
        while _time.time() - start < 8.0:
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                if "whatsapp" in win32gui.GetWindowText(hwnd).lower():
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        
        if not ready:
            return {"success": False, "error": "WhatsApp did not open"}
        
        await asyncio.sleep(0.5)
        
        # Search
        self.pg.hotkey('ctrl', 'f')
        await asyncio.sleep(0.4)
        self.pg.hotkey('ctrl', 'a')
        await asyncio.sleep(0.1)
        self.pg.press('backspace')
        await asyncio.sleep(0.1)
        self.pg.write(contact, interval=0.03)
        await asyncio.sleep(1.5)
        
        # Highlight first result (DOWN arrow, not Enter)
        self.pg.press('down')
        await asyncio.sleep(0.3)
        
        return {"success": True, "contact_searched": contact}

    async def _call(self, params: Dict) -> Dict:
        """Press Enter to open chat, then physically click Phone icon via isolated thread."""
        if not self.pg:
            return {"success": False, "error": "pyautogui not available"}
        
        # 1. Open the chat (bypasses Archive trap)
        self.pg.press('enter')
        
        import asyncio
        import logging
        await asyncio.sleep(2.0)  # Wait for chat window to fully render
        
        # 2. ISOLATED UIA THREAD (Prevents 60-second asyncio loop freezes)
        def _click_phone_native():
            if not self.auto:
                return False
            try:
                # Force UIA to fail fast
                self.auto.SetGlobalSearchTimeout(1.0)
                wa = self.auto.WindowControl(ClassName="ApplicationFrameWindow", Name="WhatsApp")
                
                # Check standard names. searchDepth=6 prevents infinite DOM walking.
                for btn_name in ["Audio call", "Voice call", "Call"]:
                    call_btn = wa.ButtonControl(Name=btn_name, searchDepth=6)
                    
                    if call_btn.Exists(1.0, 0.1):
                        call_btn.Click(simulateMove=True)
                        logging.getLogger(__name__).info(f"[CommTool] Natively clicked '{btn_name}'")
                        return True
            except Exception:
                pass
            return False

        # Run the synchronous COM API calls in a background executor thread
        loop = asyncio.get_event_loop()
        call_initiated = await loop.run_in_executor(None, _click_phone_native)

        # 3. Ultimate Fallback (If native click fails)
        if not call_initiated:
            logging.getLogger(__name__).info("[CommTool] UIA failed, falling back to keyboard shortcut.")
            self.pg.hotkey('ctrl', 'shift', 'a')
            await asyncio.sleep(0.5)
            # If the split-menu (down arrow) opens, this Enter press confirms the call
            self.pg.press('enter')
        
        return {"success": True, "message": "Calling now, Sir."}

    async def _send(self, params: Dict) -> Dict:
        """Press Enter instantly to open chat, type message, send."""
        message = params.get("message", params.get("body", "")).strip()
        if not message:
            return {"success": False, "error": "No message body provided"}
        if not self.pg:
            return {"success": False, "error": "pyautogui not available"}
        
        # 1. BYPASS THE ARCHIVE TRAP
        self.pg.press('enter')
        
        # 2. Wait for chat to open
        import asyncio
        await asyncio.sleep(1.5)  
        
        # 3. Type and send the message instantly
        self.pg.write(message, interval=0.01)
        await asyncio.sleep(0.2)
        self.pg.press('enter')
        
        logger.info(f"[CommTool] Message sent: '{message[:20]}...'")
        return {"success": True, "message": "Message sent, Sir."}