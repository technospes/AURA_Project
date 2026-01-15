"""
WORKING Click & Drag System - Disables precision features during drag
Key Fix: Tells precision cursor to allow movement during drag
"""

import time
import numpy as np
from dataclasses import dataclass
from collections import deque
from ctypes import windll

# ============================================================================
# CONFIGURATION
# ============================================================================

class ClickDragConfig:
    """Gesture recognition thresholds"""
    
    # Pinch detection
    PINCH_THRESHOLD = 30
    PINCH_RELEASE_THRESHOLD = 55
    
    # Click vs Drag
    DRAG_MOVEMENT_THRESHOLD = 10
    CLICK_MAX_TIME = 0.25
    
    # Drag smoothing
    DRAG_SMOOTHING = 0.15
    NORMAL_SMOOTHING = 0.4
    
    # Jitter reduction
    ENABLE_POSITION_BUFFER = True
    BUFFER_SIZE = 4
    MIN_MOVEMENT = 2.5
    
    # Double-click
    DOUBLE_CLICK_WINDOW = 0.4


# ============================================================================
# GESTURE STATE
# ============================================================================

@dataclass
class GestureState:
    """Tracks gesture state across frames"""
    
    is_pinching: bool = False
    is_dragging: bool = False
    
    pinch_start_time: float = 0.0
    last_click_time: float = 0.0
    
    pinch_start_x: float = 0.0
    pinch_start_y: float = 0.0
    
    last_cursor_x: float = 0.0
    last_cursor_y: float = 0.0
    total_movement: float = 0.0
    
    position_buffer_x: deque = None
    position_buffer_y: deque = None
    
    def __post_init__(self):
        if self.position_buffer_x is None:
            self.position_buffer_x = deque(maxlen=ClickDragConfig.BUFFER_SIZE)
            self.position_buffer_y = deque(maxlen=ClickDragConfig.BUFFER_SIZE)
    
    def reset(self):
        self.is_pinching = False
        self.is_dragging = False
        self.total_movement = 0.0


# ============================================================================
# JITTER FILTER
# ============================================================================

class JitterFilter:
    """Jitter reduction filter"""
    
    def __init__(self):
        self.config = ClickDragConfig
        self.last_x = None
        self.last_y = None
    
    def apply_median_filter(self, buffer_x: deque, buffer_y: deque) -> tuple:
        if len(buffer_x) < 2:
            return buffer_x[-1] if buffer_x else 0, buffer_y[-1] if buffer_y else 0
        
        median_x = np.median(list(buffer_x))
        median_y = np.median(list(buffer_y))
        return median_x, median_y
    
    def apply_deadzone(self, current_x: float, current_y: float, is_dragging: bool) -> tuple:
        if self.last_x is None:
            self.last_x, self.last_y = current_x, current_y
            return current_x, current_y
        
        dx = current_x - self.last_x
        dy = current_y - self.last_y
        dist = np.sqrt(dx**2 + dy**2)
        
        threshold = self.config.MIN_MOVEMENT * (1.5 if is_dragging else 1.0)
        
        if dist < threshold:
            return self.last_x, self.last_y
        
        self.last_x, self.last_y = current_x, current_y
        return current_x, current_y
    
    def reset(self):
        self.last_x = None
        self.last_y = None


# ============================================================================
# CLICK & DRAG HANDLER
# ============================================================================

class ClickDragHandler:
    """Handles Click and Drag gestures"""
    
    def __init__(self):
        self.config = ClickDragConfig
        self.state = GestureState()
        self.jitter_filter = JitterFilter()
        
        self._mouse_event = windll.user32.mouse_event
        
        self.MOUSEEVENTF_LEFTDOWN = 0x0002
        self.MOUSEEVENTF_LEFTUP = 0x0004
    
    def process_gesture(self, pinch_dist: float, cursor_x: float, cursor_y: float,
                       current_time: float) -> dict:
        """Process gesture - returns drag state for precision cursor"""
        
        result = {
            'action': 'none',
            'position': (cursor_x, cursor_y),
            'is_dragging': self.state.is_dragging  # CRITICAL: Tell precision cursor about drag
        }
        
        # Jitter reduction
        if self.config.ENABLE_POSITION_BUFFER:
            self.state.position_buffer_x.append(cursor_x)
            self.state.position_buffer_y.append(cursor_y)
            
            filtered_x, filtered_y = self.jitter_filter.apply_median_filter(
                self.state.position_buffer_x,
                self.state.position_buffer_y
            )
            
            stable_x, stable_y = self.jitter_filter.apply_deadzone(
                filtered_x, filtered_y, self.state.is_dragging
            )
            
            result['position'] = (stable_x, stable_y)
        else:
            stable_x, stable_y = cursor_x, cursor_y
        
        # Pinch detection
        is_pinched = pinch_dist < self.config.PINCH_THRESHOLD
        is_released = pinch_dist > self.config.PINCH_RELEASE_THRESHOLD
        
        # PINCH START
        if is_pinched and not self.state.is_pinching:
            self.state.is_pinching = True
            self.state.pinch_start_time = current_time
            self.state.pinch_start_x = stable_x
            self.state.pinch_start_y = stable_y
            self.state.last_cursor_x = stable_x
            self.state.last_cursor_y = stable_y
            self.state.total_movement = 0.0
        
        # PINCH HELD
        elif is_pinched and self.state.is_pinching:
            dx_start = stable_x - self.state.pinch_start_x
            dy_start = stable_y - self.state.pinch_start_y
            distance_from_start = np.sqrt(dx_start**2 + dy_start**2)
            
            dx_frame = stable_x - self.state.last_cursor_x
            dy_frame = stable_y - self.state.last_cursor_y
            frame_movement = np.sqrt(dx_frame**2 + dy_frame**2)
            
            self.state.total_movement += frame_movement
            self.state.last_cursor_x = stable_x
            self.state.last_cursor_y = stable_y
            
            # ALREADY DRAGGING
            if self.state.is_dragging:
                result['action'] = 'dragging'
                result['is_dragging'] = True
            
            # START DRAG
            elif distance_from_start > self.config.DRAG_MOVEMENT_THRESHOLD:
                self._mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                self.state.is_dragging = True
                result['action'] = 'drag_start'
                result['is_dragging'] = True
        
        # PINCH RELEASE
        elif is_released and self.state.is_pinching:
            hold_duration = current_time - self.state.pinch_start_time
            
            dx = stable_x - self.state.pinch_start_x
            dy = stable_y - self.state.pinch_start_y
            final_distance = np.sqrt(dx**2 + dy**2)
            
            # DROP
            if self.state.is_dragging:
                self._mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                result['action'] = 'drop'
                self.state.is_dragging = False
                result['is_dragging'] = False
            
            # CLICK
            elif (hold_duration < self.config.CLICK_MAX_TIME and 
                  final_distance < self.config.DRAG_MOVEMENT_THRESHOLD):
                self._execute_click(current_time)
                result['action'] = 'click'
            
            self.state.is_pinching = False
            self.state.total_movement = 0.0
        
        return result
    
    def _execute_click(self, current_time: float):
        time_since_last = current_time - self.state.last_click_time
        
        if time_since_last < self.config.DOUBLE_CLICK_WINDOW:
            # Double-click
            self._mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.01)
            self._mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.05)
            self._mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.01)
            self._mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        else:
            # Single click
            self._mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.01)
            self._mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        
        self.state.last_click_time = current_time
    
    def emergency_release(self):
        if self.state.is_dragging:
            self._mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.state.is_dragging = False
        self.state.reset()


# ============================================================================
# INTEGRATION FUNCTION
# ============================================================================

def enhanced_cursor_thread_with_drag(hand_state, screen_w: int, screen_h: int,
                                    cam_w: int = 640, cam_h: int = 480):
    """Enhanced cursor thread with working drag"""
    from collections import deque
    from ctypes import byref, Structure, c_long
    import time
    
    from src.precision_cursor import PrecisionCursorController
    
    controller = PrecisionCursorController(screen_w, screen_h, cam_w, cam_h)
    click_drag_handler = ClickDragHandler()
    
    fps_tracker = deque(maxlen=30)
    last_time = time.perf_counter()
    frame_time = 1.0 / 120
    
    class POINT(Structure):
        _fields_ = [("x", c_long), ("y", c_long)]
    
    while True:
        loop_start = time.perf_counter()
        
        state = hand_state.get_snapshot()
        current_time = time.time()
        
        if state['detected']:
            # Update cursor position
            cursor_status = controller.update_cursor(
                state['index_x'],
                state['index_y'],
                state['pinch_dist'],
                state['cursor_active']
            )
            
            # Get actual cursor position
            pt = POINT()
            windll.user32.GetCursorPos(byref(pt))
            cursor_x, cursor_y = pt.x, pt.y
            
            # Process gestures
            gesture_result = click_drag_handler.process_gesture(
                state['pinch_dist'],
                cursor_x,
                cursor_y,
                current_time
            )
            
            # CRITICAL FIX: If dragging, disable precision cursor locks
            if gesture_result['is_dragging']:
                # Force unlock cursor lock
                controller.state.is_locked = False
                controller.state.locked_x = None
                controller.state.locked_y = None
        
        else:
            click_drag_handler.emergency_release()
        
        # FPS tracking
        current_time_perf = time.perf_counter()
        fps = 1.0 / max(current_time_perf - last_time, 0.001)
        fps_tracker.append(fps)
        hand_state.cursor_fps = sum(fps_tracker) / len(fps_tracker)
        last_time = current_time_perf
        
        # Maintain 120 FPS
        elapsed = time.perf_counter() - loop_start
        sleep_time = frame_time - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)