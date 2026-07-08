"""
CLICK SIMULATOR — Production-Grade DPI-Safe UI Automation
==========================================================
Uses native UIA clicks (bypasses DPI scaling) with PyAutoGUI fallback.
O(1) element lookup via pre-mapped IDs from screen daemon.
"""
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class ClickSimulatorTool:
    """
    Bulletproof click automation tool.
    
    Primary: Native UIA Click() — DPI-safe, respects multi-monitor setups
    Fallback: PyAutoGUI click() — if UIA click fails
    """
    
    def __init__(self):
        self._uia_available = False
        self._pg_available = False
        
        try:
            import uiautomation as auto
            auto.SetGlobalSearchTimeout(0.2)
            self._uia_available = True
        except ImportError:
            logger.warning("[ClickSimulator] uiautomation not available — UIA clicks disabled")
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
            self.pg = pyautogui
            self._pg_available = True
        except ImportError:
            self.pg = None
            logger.warning("[ClickSimulator] pyautogui not available — fallback disabled")
    
    async def execute(self, action: str, params: Dict,
                      intent: Dict = None, context: Dict = None,
                      step_results: list = None) -> Dict:
        
        try:
            if action == "click_element_id":
                return self._click_by_id(params, context)
            
            elif action == "type_text":
                return self._type_text(params)
            
            elif action == "press_key":
                return self._press_key(params)
            
            elif action == "scroll":
                return self._scroll(params)
            
            elif action == "wait":
                seconds = float(params.get("seconds", 1.0))
                await asyncio.sleep(min(seconds, 5.0))
                return {"success": True, "message": f"Waited {seconds}s"}
            
            else:
                return {"success": False, "error": f"Unknown action: {action}"}
        
        except Exception as e:
            logger.error(f"[ClickSimulator] Error in {action}: {e}")
            return {"success": False, "error": str(e)}
    
    def _click_by_id(self, params: Dict, context: Dict) -> Dict:
        """
        O(1) element lookup → DPI-safe native UIA click.
        
        Uses full bounding rect (left/top/right/bottom) to calculate
        center point dynamically. Clicks via UIA's native Click() 
        which perfectly handles Windows DPI scaling.
        """
        element_id = params.get("element_id")
        ui_map = context.get("ui_map", {}) if context else {}
        
        # Normalize element_id to int
        if isinstance(element_id, str) and element_id.isdigit():
            element_id = int(element_id)
        
        if not isinstance(element_id, int) or element_id not in ui_map:
            available = list(ui_map.keys())[:10] if ui_map else []
            return {
                "success": False,
                "error": f"Element ID {element_id} not found. Available IDs: {available}"
            }
        
        target = ui_map[element_id]
        
        # Calculate center from stored rect
        center_x = target["left"] + ((target["right"] - target["left"]) // 2)
        center_y = target["top"] + ((target["bottom"] - target["top"]) // 2)
        
        # ── PRIMARY: Native UIA Click (DPI-safe) ──────────────────────
        if self._uia_available:
            try:
                import uiautomation as auto
                # Native click bypasses DPI scaling entirely
                # simulateMove=True gives smooth cursor animation
                auto.Click(center_x, center_y, simulateMove=True, waitTime=0.1)
                
                logger.info(
                    f"[ClickSimulator]  Native UIA click [{element_id}] "
                    f"'{target['name']}' at ({center_x}, {center_y})"
                )
                return {
                    "success": True,
                    "message": f"Clicked '{target['name']}'",
                    "method": "uia_native",
                    "element": {"id": element_id, "name": target["name"]}
                }
            except Exception as e:
                logger.debug(f"[ClickSimulator] UIA native click failed: {e} — trying fallback")
        
        # ── FALLBACK: PyAutoGUI pixel click ───────────────────────────
        if self._pg_available:
            try:
                self.pg.moveTo(center_x, center_y, duration=0.15)
                self.pg.click()
                
                logger.info(
                    f"[ClickSimulator] ️ PyAutoGUI fallback click [{element_id}] "
                    f"'{target['name']}' at ({center_x}, {center_y})"
                )
                return {
                    "success": True,
                    "message": f"Clicked '{target['name']}' (fallback)",
                    "method": "pyautogui_fallback",
                    "element": {"id": element_id, "name": target["name"]}
                }
            except Exception as e:
                return {"success": False, "error": f"Both UIA and PyAutoGUI clicks failed: {e}"}
        
        return {"success": False, "error": "No click method available (install uiautomation or pyautogui)"}
    
    def _type_text(self, params: Dict) -> Dict:
        """Type text, optionally press Enter."""
        text = params.get("text", "")
        if not text:
            return {"success": False, "error": "No text provided"}
        
        if self._pg_available:
            self.pg.write(text, interval=0.02)
        elif self._uia_available:
            import uiautomation as auto
            auto.SendKeys(text)
        else:
            return {"success": False, "error": "No typing method available"}
        
        if params.get("press_enter", False):
            self._press_key_internal("enter")
        
        return {"success": True, "message": f"Typed '{text[:40]}'"}
    
    def _press_key(self, params: Dict) -> Dict:
        """Press a key or hotkey combination."""
        key = params.get("key", "")
        if not key:
            return {"success": False, "error": "No key specified"}
        
        return self._press_key_internal(key)
    
    def _press_key_internal(self, key: str) -> Dict:
        """Internal key press with fallback chain."""
        # Try UIA first
        if self._uia_available:
            try:
                import uiautomation as auto
                if "+" in key:
                    keys = [k.strip().lower() for k in key.split("+")]
                    auto.SendKeys("{" + "}{".join(keys) + "}")
                else:
                    auto.SendKeys("{" + key.lower() + "}")
                return {"success": True, "message": f"Pressed '{key}' (UIA)"}
            except Exception:
                pass
        
        # Fallback to PyAutoGUI
        if self._pg_available:
            if "+" in key:
                keys = [k.strip().lower() for k in key.split("+")]
                self.pg.hotkey(*keys)
            else:
                self.pg.press(key.lower())
            return {"success": True, "message": f"Pressed '{key}' (PyAutoGUI)"}
        
        return {"success": False, "error": "No key press method available"}
    
    def _scroll(self, params: Dict) -> Dict:
        """Scroll up or down."""
        direction = params.get("direction", "down")
        amount = params.get("amount", 3)
        
        if self._pg_available:
            clicks = -amount if direction == "down" else amount
            for _ in range(abs(clicks)):
                self.pg.scroll(1 if clicks > 0 else -1)
            return {"success": True, "message": f"Scrolled {direction}"}
        
        return {"success": False, "error": "pyautogui not available for scrolling"}