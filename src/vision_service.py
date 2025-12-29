"""
Aura Vision Service - Professional Hand Tracking System
Author: Aura Team
Version: 3.0
Performance Target: 120 FPS cursor, 30 FPS vision
"""

import cv2
import mediapipe as mp
import numpy as np
import time
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from ctypes import windll, Structure, c_long, create_unicode_buffer
import os

from src.config import CAM_WIDTH, CAM_HEIGHT, MOUSE_PHYSICS, GESTURE_CONFIG
from src.native_opener import open_app, close_app, play_music, search_web

# Disable PyAutoGUI failsafe globally
import pyautogui
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# ============================================================================
# CONFIGURATION
# ============================================================================
DEBUG = True  # Set False for production (removes display overhead)

class GestureThresholds:
    """Centralized gesture detection thresholds"""
    # Click detection
    PINCH_CLICK = 30        # Distance for click trigger (pixels)
    PINCH_RELEASE = 50      # Distance for click release (pixels)
    
    # Right-click
    RIGHT_CLICK_HOLD = 0.5  # Hold duration for right-click (seconds)
    DOUBLE_CLICK_TIME = 0.3 # Max time between clicks (seconds)
    
    # Sniper mode
    SNIPER_DISTANCE = 45    # Pinch distance for precision mode (pixels)
    
    # Scroll
    SCROLL_DEADZONE = 40    # Minimum movement for scroll (pixels)
    
    # Anti-jitter
    CLICK_DEBOUNCE = 0.15   # Min time between clicks (seconds)
    GESTURE_COOLDOWN = 0.5  # Min time between gesture switches (seconds)

class CursorConfig:
    """Cursor behavior settings"""
    TARGET_FPS = 120
    SMOOTH_ALPHA_NORMAL = 0.4   # Higher = more responsive
    SMOOTH_ALPHA_SNIPER = 0.15  # Lower = more stable
    CAMERA_MARGIN = 70          # Edge deadzone (pixels)

# ============================================================================
# GESTURE STATE MACHINE
# ============================================================================
class GestureMode(Enum):
    IDLE = "idle"
    CURSOR = "cursor"
    CLICKING = "clicking"
    DRAGGING = "dragging"
    RIGHT_CLICKING = "right_clicking"
    SCROLLING = "scrolling"
    VOLUME = "volume"
    MEDIA_CONTROL = "media"

@dataclass
class ClickState:
    """Tracks click timing and state"""
    is_left_down: bool = False
    is_right_down: bool = False
    last_click_time: float = 0.0
    pinch_start_time: float = 0.0
    click_count: int = 0
    last_click_count_reset: float = 0.0

# ============================================================================
# WIN32 MOUSE API (Zero-Latency)
# ============================================================================
class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]

# Pre-cache Win32 functions
_set_cursor = windll.user32.SetCursorPos
_mouse_event = windll.user32.mouse_event

# Mouse event constants
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

class MouseController:
    """Professional mouse controller with proper state management"""
    
    @staticmethod
    def move(x: int, y: int) -> None:
        """Move cursor instantly"""
        _set_cursor(int(x), int(y))
    
    @staticmethod
    def left_down() -> None:
        """Press left mouse button"""
        _mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    
    @staticmethod
    def left_up() -> None:
        """Release left mouse button"""
        _mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    
    @staticmethod
    def right_down() -> None:
        """Press right mouse button"""
        _mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    
    @staticmethod
    def right_up() -> None:
        """Release right mouse button"""
        _mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
    
    @staticmethod
    def left_click() -> None:
        """Complete left click"""
        MouseController.left_down()
        time.sleep(0.01)
        MouseController.left_up()
    
    @staticmethod
    def right_click() -> None:
        """Complete right click"""
        MouseController.right_down()
        time.sleep(0.01)
        MouseController.right_up()
    
    @staticmethod
    def double_click() -> None:
        """Double click"""
        MouseController.left_click()
        time.sleep(0.05)
        MouseController.left_click()

# ============================================================================
# HAND STATE (Thread-Safe Shared Memory)
# ============================================================================
class HandState:
    """Thread-safe state container for hand tracking data"""
    
    def __init__(self):
        self.lock = threading.Lock()
        
        # Hand position
        self.index_x: float = 0.0
        self.index_y: float = 0.0
        self.thumb_x: float = 0.0
        self.thumb_y: float = 0.0
        self.pinch_dist: float = 100.0
        
        # Detection state
        self.hand_detected: bool = False
        self.cursor_active: bool = False
        
        # Gesture mode
        self.mode: GestureMode = GestureMode.IDLE
        self.mode_text: str = ""
        
        # Timing
        self.last_update: float = time.perf_counter()
        
        # FPS tracking
        self.vision_fps: float = 0.0
        self.cursor_fps: float = 0.0
    
    def update(self, index_x: float, index_y: float, thumb_x: float, thumb_y: float,
               pinch_dist: float, mode: GestureMode, cursor_active: bool):
        """Thread-safe update of hand state"""
        with self.lock:
            self.index_x = index_x
            self.index_y = index_y
            self.thumb_x = thumb_x
            self.thumb_y = thumb_y
            self.pinch_dist = pinch_dist
            self.mode = mode
            self.cursor_active = cursor_active
            self.hand_detected = True
            self.last_update = time.perf_counter()
    
    def get_snapshot(self) -> dict:
        """Get thread-safe snapshot of current state"""
        with self.lock:
            # Emergency timeout: if vision died, clear state
            if time.perf_counter() - self.last_update > 0.1:  # 100ms timeout
                self.hand_detected = False
                self.cursor_active = False
                self.mode = GestureMode.IDLE
            
            return {
                'index_x': self.index_x,
                'index_y': self.index_y,
                'thumb_x': self.thumb_x,
                'thumb_y': self.thumb_y,
                'pinch_dist': self.pinch_dist,
                'mode': self.mode,
                'cursor_active': self.cursor_active,
                'detected': self.hand_detected
            }
    
    def clear(self):
        """Clear hand detection state"""
        with self.lock:
            self.hand_detected = False
            self.cursor_active = False
            self.mode = GestureMode.IDLE

# ============================================================================
# EXPONENTIAL SMOOTHING FILTER
# ============================================================================
class CursorSmoother:
    """Ultra-fast exponential smoothing for cursor movement"""
    
    def __init__(self, alpha_normal: float = 0.4, alpha_sniper: float = 0.15):
        self.alpha_normal = alpha_normal
        self.alpha_sniper = alpha_sniper
        self.x: float = None
        self.y: float = None
    
    def smooth(self, raw_x: float, raw_y: float, sniper_mode: bool = False) -> tuple:
        """Apply exponential smoothing"""
        alpha = self.alpha_sniper if sniper_mode else self.alpha_normal
        
        if self.x is None:
            self.x, self.y = raw_x, raw_y
        else:
            self.x = alpha * raw_x + (1 - alpha) * self.x
            self.y = alpha * raw_y + (1 - alpha) * self.y
        
        return self.x, self.y
    
    def reset(self):
        """Reset filter state"""
        self.x = None
        self.y = None

# ============================================================================
# THREADED CAMERA CAPTURE
# ============================================================================
class ThreadedCamera:
    """Non-blocking camera capture in separate thread"""
    
    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480):
        self.cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 60)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.frame = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()
        
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
    
    def _capture_loop(self):
        """Background capture loop"""
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.ret = ret
                    self.frame = frame
    
    def read(self) -> tuple:
        """Get latest frame (non-blocking)"""
        with self.lock:
            if self.frame is not None:
                return self.ret, self.frame.copy()
            return False, None
    
    def release(self):
        """Stop camera"""
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()

# ============================================================================
# CURSOR UPDATE THREAD (120 FPS)
# ============================================================================
def cursor_thread(hand_state: HandState, screen_w: int, screen_h: int, 
                 cam_w: int = 640, cam_h: int = 480):
    """
    Dedicated cursor thread running at 120 FPS.
    Completely decoupled from vision processing.
    """
    smoother = CursorSmoother()
    fps_tracker = deque(maxlen=30)
    last_time = time.perf_counter()
    frame_time = 1.0 / CursorConfig.TARGET_FPS
    
    margin = CursorConfig.CAMERA_MARGIN
    
    print(f"[Cursor] Running at {CursorConfig.TARGET_FPS} FPS")
    
    while True:
        loop_start = time.perf_counter()
        
        # Get current hand state
        state = hand_state.get_snapshot()
        
        if state['detected'] and state['cursor_active']:
            # Check for sniper mode
            sniper = state['pinch_dist'] < GestureThresholds.SNIPER_DISTANCE
            
            # Smooth position
            sx, sy = smoother.smooth(state['index_x'], state['index_y'], sniper)
            
            # Map camera coords to screen coords
            norm_x = (sx - margin) / (cam_w - 2 * margin)
            norm_y = (sy - margin) / (cam_h - 2 * margin)
            
            # Clamp and convert
            screen_x = int(np.clip(norm_x * screen_w, 0, screen_w - 1))
            screen_y = int(np.clip(norm_y * screen_h, 0, screen_h - 1))
            
            # Move cursor instantly
            MouseController.move(screen_x, screen_y)
        
        # FPS tracking
        current_time = time.perf_counter()
        fps = 1.0 / max(current_time - last_time, 0.001)
        fps_tracker.append(fps)
        hand_state.cursor_fps = sum(fps_tracker) / len(fps_tracker)
        last_time = current_time
        
        # Sleep to maintain target FPS
        elapsed = time.perf_counter() - loop_start
        sleep_time = frame_time - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

# ============================================================================
# GESTURE RECOGNITION
# ============================================================================
class GestureRecognizer:
    """Recognizes hand gestures from MediaPipe landmarks"""
    
    @staticmethod
    def get_finger_states(landmarks) -> dict:
        """Detect which fingers are extended"""
        return {
            'thumb': landmarks[4].x < landmarks[3].x,  # Thumb extended
            'index': landmarks[8].y < landmarks[6].y,   # Index up
            'middle': landmarks[12].y < landmarks[10].y, # Middle up
            'ring': landmarks[16].y < landmarks[14].y,   # Ring up
            'pinky': landmarks[20].y < landmarks[18].y   # Pinky up
        }
    
    @staticmethod
    def calculate_pinch_distance(landmarks, w: int, h: int) -> tuple:
        """Calculate distance between thumb and index finger tips"""
        thumb_x = landmarks[4].x * w
        thumb_y = landmarks[4].y * h
        index_x = landmarks[8].x * w
        index_y = landmarks[8].y * h
        
        dist = np.sqrt((thumb_x - index_x)**2 + (thumb_y - index_y)**2)
        return dist, thumb_x, thumb_y, index_x, index_y
    
    @staticmethod
    def recognize_mode(fingers: dict, pinch_dist: float) -> GestureMode:
        """Determine gesture mode from finger states"""
        # Cursor mode: only index finger up
        if fingers['index'] and not fingers['middle'] and not fingers['ring']:
            return GestureMode.CURSOR
        
        # Scroll mode: index + middle + ring up
        elif fingers['index'] and fingers['middle'] and fingers['ring'] and not fingers['pinky']:
            return GestureMode.SCROLLING
        
        # Volume control: shaka (thumb + pinky, others down)
        elif fingers['thumb'] and fingers['pinky'] and not fingers['index']:
            return GestureMode.VOLUME
        
        # Media control: fist (all fingers down)
        elif not any([fingers['index'], fingers['middle'], fingers['ring']]):
            return GestureMode.MEDIA_CONTROL
        
        return GestureMode.IDLE

# ============================================================================
# CLICK HANDLER
# ============================================================================
class ClickHandler:
    """Manages click detection and execution"""
    
    def __init__(self):
        self.state = ClickState()
        self.mouse = MouseController()
    
    def process_pinch(self, pinch_dist: float, current_time: float) -> str:
        """
        Process pinch gesture for clicks/drags.
        Returns status string for display.
        """
        status = ""
        
        # PINCH DETECTED
        if pinch_dist < GestureThresholds.PINCH_CLICK:
            
            # START OF NEW PINCH
            if not self.state.is_left_down and not self.state.is_right_down:
                # Debounce check
                if current_time - self.state.last_click_time > GestureThresholds.CLICK_DEBOUNCE:
                    self.state.pinch_start_time = current_time
                    self.mouse.left_down()
                    self.state.is_left_down = True
                    status = "LEFT DOWN"
            
            # HELD PINCH - Check for right-click
            elif self.state.is_left_down:
                hold_duration = current_time - self.state.pinch_start_time
                if hold_duration > GestureThresholds.RIGHT_CLICK_HOLD:
                    # Convert to right-click
                    self.mouse.left_up()
                    self.state.is_left_down = False
                    self.mouse.right_click()
                    self.state.is_right_down = True
                    self.state.last_click_time = current_time
                    status = "RIGHT CLICK"
                else:
                    status = f"HOLD ({hold_duration:.1f}s)"
        
        # PINCH RELEASED
        elif pinch_dist > GestureThresholds.PINCH_RELEASE:
            if self.state.is_left_down:
                self.mouse.left_up()
                self.state.is_left_down = False
                self.state.last_click_time = current_time
                status = "RELEASED"
            
            if self.state.is_right_down:
                self.state.is_right_down = False
        
        return status
    
    def emergency_release(self):
        """Release all buttons (emergency recovery)"""
        if self.state.is_left_down:
            self.mouse.left_up()
            self.state.is_left_down = False
        if self.state.is_right_down:
            self.mouse.right_up()
            self.state.is_right_down = False

# ============================================================================
# CONTEXT MONITORING
# ============================================================================
def get_active_window():
    """Get active window title (Windows)"""
    try:
        hwnd = windll.user32.GetForegroundWindow()
        length = windll.user32.GetWindowTextLengthW(hwnd)
        buf = create_unicode_buffer(length + 1)
        windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.lower()
    except:
        return ""

def context_monitor_thread(shared_state):
    """Monitor active application context"""
    while shared_state.system_active.value:
        title = get_active_window()
        ctx = "desktop"
        
        if "discord" in title:
            ctx = "discord"
        elif "firefox" in title or "chrome" in title:
            ctx = "youtube" if "youtube" in title else "browser"
        elif "spotify" in title:
            ctx = "spotify"
        
        shared_state.set_context(ctx)
        time.sleep(0.5)

# ============================================================================
# COMMAND EXECUTION
# ============================================================================
def execute_command(cmd: dict, context: str):
    """Execute system commands in background"""
    intent = cmd.get('intent')
    payload = cmd.get('payload')
    
    try:
        if intent == "PLAY_MEDIA":
            play_music(payload['song'], payload['platform'])
        elif intent == "SEARCH_WEB":
            search_web(payload['query'], payload['platform'])
        elif intent == "OPEN_APP":
            open_app(payload)
        elif intent == "CLOSE_APP":
            close_app(payload)
        elif intent == "TYPE":
            pyautogui.write(payload + " ", interval=0.02)
        elif intent == "SCROLL":
            direction = -800 if payload == "down" else 800
            pyautogui.scroll(direction)
        elif intent == "SYSTEM_SHUTDOWN":
            os.system("shutdown /s /t 10")
    except Exception as e:
        print(f"[CMD Error] {e}")

# ============================================================================
# MAIN VISION LOOP
# ============================================================================
def vision_process_loop(shared_state):
    """
    Main vision processing loop.
    Handles hand detection and gesture recognition at ~30 FPS.
    Cursor movement handled by separate 120 FPS thread.
    """
    
    # Initialize components
    hand_state = HandState()
    camera = ThreadedCamera(camera_id=0, width=640, height=480)
    click_handler = ClickHandler()
    
    # Get screen dimensions
    screen_w, screen_h = pyautogui.size()
    
    # Start cursor thread (120 FPS, independent)
    cursor_thread_handle = threading.Thread(
        target=cursor_thread,
        args=(hand_state, screen_w, screen_h),
        daemon=True
    )
    cursor_thread_handle.start()
    
    # Start context monitor
    threading.Thread(target=context_monitor_thread, args=(shared_state,), daemon=True).start()
    
    # Wait for camera
    time.sleep(0.5)
    
    # MediaPipe setup
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils
    hands = mp_hands.Hands(
        max_num_hands=1,
        model_complexity=0,  # Lite model for speed
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    
    # State tracking
    frame_count = 0
    last_gesture_time = 0.0
    fps_tracker = deque(maxlen=10)
    last_time = time.perf_counter()
    
    # Smoothers for gesture detection
    vision_smoother = CursorSmoother(alpha_normal=0.3)
    
    # Debug window
    if DEBUG:
        cv2.namedWindow("Aura Vision Debug", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Aura Vision Debug", 1280, 720)  # Larger window
    
    print("[Vision] Processing started (30 FPS target)")
    
    # ===== MAIN LOOP =====
    while shared_state.system_active.value:
        
        # Process pending commands
        while not shared_state.command_queue.empty():
            cmd = shared_state.command_queue.get()
            ctx = shared_state.get_context()
            threading.Thread(target=execute_command, args=(cmd, ctx), daemon=True).start()
        
        # Frame skipping for performance (process every 2nd frame)
        if frame_count % 2 != 0:
            frame_count += 1
            continue
        
        # Capture frame
        ret, frame = camera.read()
        if not ret or frame is None:
            continue
        
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # MediaPipe inference
        results = hands.process(rgb)
        
        # FPS calculation
        current_time = time.perf_counter()
        fps = 1.0 / max(current_time - last_time, 0.001)
        fps_tracker.append(fps)
        hand_state.vision_fps = sum(fps_tracker) / len(fps_tracker)
        last_time = current_time
        
        display_text = ""
        click_status = ""
        
        # ===== HAND DETECTED =====
        if results.multi_hand_landmarks:
            landmarks = results.multi_hand_landmarks[0].landmark
            
            # Draw hand skeleton (if debug)
            if DEBUG:
                mp_draw.draw_landmarks(frame, results.multi_hand_landmarks[0], 
                                      mp_hands.HAND_CONNECTIONS)
            
            # Get finger states
            fingers = GestureRecognizer.get_finger_states(landmarks)
            
            # Calculate pinch distance
            pinch_dist, thumb_x, thumb_y, index_x, index_y = \
                GestureRecognizer.calculate_pinch_distance(landmarks, w, h)
            
            # Recognize gesture mode
            mode = GestureRecognizer.recognize_mode(fingers, pinch_dist)
            
            # Smooth positions for vision thread
            sx, sy = vision_smoother.smooth(index_x, index_y)
            
            # ===== CURSOR MODE =====
            if mode == GestureMode.CURSOR:
                # Update state for cursor thread
                hand_state.update(sx, sy, thumb_x, thumb_y, pinch_dist, mode, cursor_active=True)
                
                # Process clicks
                click_status = click_handler.process_pinch(pinch_dist, time.time())
                
                # Sniper mode indicator
                if pinch_dist < GestureThresholds.SNIPER_DISTANCE:
                    display_text = "🎯 SNIPER MODE"
                
                if click_status:
                    display_text = f"🖱️ {click_status}"
            
            # ===== SCROLL MODE =====
            elif mode == GestureMode.SCROLLING and frame_count % 4 == 0:
                hand_state.update(index_x, index_y, thumb_x, thumb_y, pinch_dist, mode, cursor_active=False)
                
                middle_y = landmarks[12].y * h
                delta = middle_y - (h / 2)
                
                if abs(delta) > GestureThresholds.SCROLL_DEADZONE:
                    scroll_power = int(((abs(delta) - 40) / 10) ** 1.5) * 20
                    direction = -1 if delta > 0 else 1
                    pyautogui.scroll(scroll_power * direction)
                    display_text = f"📜 SCROLL {scroll_power * direction:+d}"
            
            # ===== VOLUME CONTROL =====
            elif mode == GestureMode.VOLUME:
                hand_state.update(index_x, index_y, thumb_x, thumb_y, pinch_dist, mode, cursor_active=False)
                
                if time.time() - last_gesture_time > 0.15:
                    pinky_y = landmarks[20].y
                    if pinky_y < 0.3:
                        pyautogui.press('volumeup')
                        display_text = "🔊 VOLUME UP"
                        last_gesture_time = time.time()
                    elif pinky_y > 0.7:
                        pyautogui.press('volumedown')
                        display_text = "🔉 VOLUME DOWN"
                        last_gesture_time = time.time()
            
            # ===== MEDIA CONTROL =====
            elif mode == GestureMode.MEDIA_CONTROL:
                hand_state.update(index_x, index_y, thumb_x, thumb_y, pinch_dist, mode, cursor_active=False)
                
                if time.time() - last_gesture_time > 1.5:
                    pyautogui.press('space')
                    display_text = "⏯️ PLAY/PAUSE"
                    last_gesture_time = time.time()
            
            else:
                hand_state.update(index_x, index_y, thumb_x, thumb_y, pinch_dist, mode, cursor_active=False)
        
        # ===== NO HAND DETECTED =====
        else:
            hand_state.clear()
            click_handler.emergency_release()
        
        frame_count += 1
        
        # ===== DEBUG DISPLAY =====
        if DEBUG:
            # Dark overlay for better text visibility
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, 120), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
            
            # FPS counters
            fps_text = f"Vision: {int(hand_state.vision_fps)} FPS | Cursor: {int(hand_state.cursor_fps)} FPS"
            cv2.putText(frame, fps_text, (20, 35), cv2.FONT_HERSHEY_DUPLEX, 0.8, (0, 255, 0), 2)
            
            # Current gesture
            if display_text:
                cv2.putText(frame, display_text, (20, 75), cv2.FONT_HERSHEY_DUPLEX, 0.9, (0, 255, 255), 2)
            
            # Gesture guide
            guide = [
                "GESTURES:",
                "☝️  INDEX: Cursor | Pinch: Click (hold 0.5s = Right)",
                "🤟 3-FINGERS: Scroll | 🤙 SHAKA: Volume",
                "✊ FIST: Play/Pause"
            ]
            
            y_pos = h - 120
            for i, text in enumerate(guide):
                color = (200, 200, 200) if i == 0 else (255, 255, 255)
                cv2.putText(frame, text, (20, y_pos + i * 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            
            cv2.imshow("Aura Vision Debug", frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                shared_state.system_active.value = False
                break
    
    # Cleanup
    camera.release()
    if DEBUG:
        cv2.destroyAllWindows()
    
    print("[Vision] Shutdown complete")