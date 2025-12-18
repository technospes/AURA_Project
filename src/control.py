import pyautogui
import numpy as np
import time
from src.config import CAM_WIDTH, CAM_HEIGHT

# Disable safety corners so you can hit the start menu/close buttons
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.01 # Tiny pause for OS stability

class MouseController:
    def __init__(self):
        self.screen_w, self.screen_h = pyautogui.size()
        self.is_dragging = False
        self.last_click_time = 0
        self.last_pinch_dist = 100

    def map_coordinates(self, x, y):
        # Increased margin to let you reach corners comfortably
        pad_margin = 70 
        target_x = np.interp(x, [pad_margin, CAM_WIDTH-pad_margin], [0, self.screen_w])
        target_y = np.interp(y, [pad_margin, CAM_HEIGHT-pad_margin], [0, self.screen_h])
        return np.clip(target_x, 0, self.screen_w), np.clip(target_y, 0, self.screen_h)

    def move_with_physics(self, target_x, target_y, pinch_dist):
        curr_x, curr_y = pyautogui.position()
        dx = target_x - curr_x
        dy = target_y - curr_y
        dist = np.hypot(dx, dy)
        
        # 1. Deadzone: Ignore tiny jitters
        if dist < 3.0: return 

        # 2. Variable Friction (The "Sticky" Logic)
        # If you are pinching (getting close to click), move SLOWER.
        # This improves accuracy without freezing you completely.
        friction = 1.0
        if pinch_dist < 50: 
            friction = 0.3 # Move at 30% speed when about to click
        
        # 3. Acceleration Curve
        # speed = distance^1.5 (Fast swipes are fast, slow moves are precise)
        speed = (dist / 120.0) ** 1.5
        speed = np.clip(speed, 0.05, 0.8) # Cap max speed to prevent teleporting
        
        # Apply Friction
        final_speed = speed * friction

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

        # --- STATE MACHINE THRESHOLDS ---
        # Click activates at 30px
        # Click releases at 50px (Hysteresis prevents accidental double-clicks)
        CLICK_THRESH = 30
        RELEASE_THRESH = 50 

        status = "HOVER"
        color = (255, 255, 255)

        # 1. RIGHT CLICK (Pinky + Thumb)
        # One-shot trigger (Cooldown 1s)
        if dist_right < CLICK_THRESH:
            if time.time() - self.last_click_time > 1.0:
                pyautogui.rightClick()
                self.last_click_time = time.time()
                return "RIGHT CLICK", (0, 255, 255), dist_left

        # 2. DOUBLE CLICK (Ring + Thumb)
        if dist_double < CLICK_THRESH:
            if time.time() - self.last_click_time > 1.0:
                pyautogui.doubleClick()
                self.last_click_time = time.time()
                return "DOUBLE CLICK", (255, 0, 255), dist_left

        # 3. LEFT CLICK (Index + Thumb)
        if self.is_dragging:
            # We are currently holding the click.
            # Only release if we open hand wide.
            if dist_left > RELEASE_THRESH:
                pyautogui.mouseUp()
                self.is_dragging = False
                status = "RELEASED"
            else:
                status = "DRAGGING"
                color = (0, 255, 0) # Green
        else:
            # We are not clicking.
            # Click only if we cross the tight threshold.
            if dist_left < CLICK_THRESH:
                pyautogui.mouseDown()
                self.is_dragging = True
                status = "CLICK DOWN"
                color = (0, 0, 255) # Red

        return status, color, dist_left
    
    def process_scroll(self, landmarks):
        current_y = landmarks[8][2]
        center_y = CAM_HEIGHT // 2
        delta = current_y - center_y
        
        # Deadzone of 50px around center
        if abs(delta) > 50:
            speed = int((abs(delta) - 50) / 10) # Speed scales with distance
            direction = -1 if delta > 0 else 1
            pyautogui.scroll(direction * speed * 10)
            return f"SCROLL {'UP' if direction==1 else 'DOWN'}"
        return "SCROLL READY"