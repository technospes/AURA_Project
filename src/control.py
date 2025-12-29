import pyautogui
import numpy as np
import time
from config import CAM_WIDTH, CAM_HEIGHT

# CRITICAL FIX: Set to 0 for 60FPS feel. 
# Any value > 0 causes "frame skipping" sensation.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0 

class MouseController:
    def __init__(self):
        self.screen_w, self.screen_h = pyautogui.size()
        self.is_dragging = False
        self.last_click_time = 0
        self.last_left_click_time = 0 # Specific debounce for left click

    def map_coordinates(self, x, y):
        pad_margin = 70 
        target_x = np.interp(x, [pad_margin, CAM_WIDTH-pad_margin], [0, self.screen_w])
        target_y = np.interp(y, [pad_margin, CAM_HEIGHT-pad_margin], [0, self.screen_h])
        return np.clip(target_x, 0, self.screen_w), np.clip(target_y, 0, self.screen_h)

    def move_with_physics(self, target_x, target_y, pinch_dist):
        curr_x, curr_y = pyautogui.position()
        dx = target_x - curr_x
        dy = target_y - curr_y
        dist = np.hypot(dx, dy)
        
        # Deadzone: Ignore tiny jitters (< 2px)
        if dist < 2.0: return 

        # --- OPTIMIZED STICKY LOGIC ---
        # Old: friction 0.3 (Too slow/laggy)
        # New: friction 0.6 (Subtle help, not mud)
        friction = 1.0
        if pinch_dist < 40: # Entering click zone
            friction = 0.6 
        
        # Acceleration: speed = distance^1.6
        # Slightly higher exponent makes slow moves precise, fast moves snappy
        speed = (dist / 120.0) ** 1.6
        speed = np.clip(speed, 0.05, 1.0) 
        
        final_speed = speed * friction
        
        # Linear Interpolation (Glide)
        new_x = curr_x + (dx * final_speed)
        new_y = curr_y + (dy * final_speed)
        
        pyautogui.moveTo(new_x, new_y, _pause=False)

    def process_gestures(self, landmarks):
        thumb = landmarks[4]
        index = landmarks[8]
        ring = landmarks[16]
        pinky = landmarks[20]

        # Calculate Distances
        dist_left = np.hypot(thumb[1]-index[1], thumb[2]-index[2]) 
        dist_double = np.hypot(thumb[1]-ring[1], thumb[2]-ring[2]) 
        dist_right = np.hypot(thumb[1]-pinky[1], thumb[2]-pinky[2]) 

        CLICK_THRESH = 30
        RELEASE_THRESH = 50 
        
        status = "HOVER"
        color = (255, 255, 255)

        # --- ANATOMICAL EXCLUSION LOGIC (CRITICAL FIX) ---
        # If Index finger is working (pinching < 60px), DISABLE other clicks.
        # This prevents accidental Right/Double clicks when your other fingers curl.
        intent_to_left_click = dist_left < 60

        # 1. RIGHT CLICK (Pinky) - Only if NOT Left clicking
        if not intent_to_left_click and dist_right < CLICK_THRESH:
            if time.time() - self.last_click_time > 1.0:
                pyautogui.rightClick()
                self.last_click_time = time.time()
                return "RIGHT CLICK", (0, 255, 255), dist_left

        # 2. DOUBLE CLICK (Ring) - Only if NOT Left clicking
        if not intent_to_left_click and dist_double < CLICK_THRESH:
            if time.time() - self.last_click_time > 1.0:
                pyautogui.doubleClick()
                self.last_click_time = time.time()
                return "DOUBLE CLICK", (255, 0, 255), dist_left

        # 3. LEFT CLICK (Index)
        if self.is_dragging:
            # Hysteresis: Release only when hand opens wide
            if dist_left > RELEASE_THRESH:
                pyautogui.mouseUp()
                self.is_dragging = False
                status = "RELEASED"
            else:
                status = "DRAGGING"
                color = (0, 255, 0)
        else:
            # Click
            if dist_left < CLICK_THRESH:
                # DEBOUNCE: Prevent double-triggering within 0.15s
                if time.time() - self.last_left_click_time > 0.15:
                    pyautogui.mouseDown()
                    self.is_dragging = True
                    self.last_left_click_time = time.time()
                    status = "CLICK DOWN"
                    color = (0, 0, 255)

        return status, color, dist_left
    
    def process_scroll(self, landmarks):
        current_y = landmarks[8][2]
        center_y = CAM_HEIGHT // 2
        delta = current_y - center_y
        
        if abs(delta) > 50:
            # Smoother Scroll scaling
            speed = int((abs(delta) - 50) / 10) 
            direction = -1 if delta > 0 else 1
            pyautogui.scroll(direction * speed * 20)
            return f"SCROLL {'UP' if direction==1 else 'DOWN'}"
        return "SCROLL READY"