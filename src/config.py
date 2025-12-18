"""
Configuration file for Jarvis system
Adjust these values for optimal performance on your system
"""

# ============================================
# CAMERA SETTINGS
# ============================================
CAM_WIDTH = 640
CAM_HEIGHT = 480
FPS = 30

# ============================================
# HAND TRACKING SETTINGS
# ============================================
# Confidence thresholds
MIN_DETECTION_CONFIDENCE = 0.7  # How confident to detect a hand
MIN_TRACKING_CONFIDENCE = 0.7   # How confident to track a hand

# ============================================
# SMOOTHING SETTINGS
# ============================================
# One Euro Filter parameters
SMOOTHING_MIN_CUTOFF = 0.008    # Lower = smoother (0.005-0.01 recommended)
SMOOTHING_BETA = 0.6            # Higher = more responsive (0.5-0.8 recommended)
SMOOTHING_D_CUTOFF = 1.0        # Derivative cutoff

# ============================================
# GESTURE SETTINGS
# ============================================
# Distance thresholds (in pixels)
CLICK_THRESHOLD = 28            # Distance to trigger click
RELEASE_THRESHOLD = 50          # Distance to release click
MAGNETIC_THRESHOLD = 35         # Distance to activate magnetic freeze

# Cooldown times (in seconds)
CLICK_COOLDOWN = 0.3           # Time between clicks
GESTURE_COOLDOWN = 0.3         # Time between gesture changes

# ============================================
# CURSOR CONTROL SETTINGS
# ============================================
# Mapping margins (padding from camera edges)
PAD_MARGIN = 80                 # Pixels to ignore at camera edges

# Physics parameters
CURSOR_DEADZONE = 2.0          # Minimum distance before moving cursor
CURSOR_MAX_SPEED = 1.0         # Maximum cursor speed multiplier
CURSOR_MIN_SPEED = 0.2         # Minimum cursor speed multiplier
CURSOR_ACCELERATION = 1.3      # Acceleration curve exponent

# ============================================
# SCROLL SETTINGS
# ============================================
SCROLL_THRESHOLD = 50          # Vertical distance to activate scroll
SCROLL_SPEED = 3               # Scroll amount per frame

# ============================================
# VOICE RECOGNITION SETTINGS
# ============================================
# Speech recognition parameters
VOICE_ENERGY_THRESHOLD = 3000       # Minimum audio energy to consider
VOICE_PAUSE_THRESHOLD = 0.6         # Seconds of silence to end phrase
VOICE_PHRASE_THRESHOLD = 0.3        # Minimum phrase length
VOICE_NON_SPEAKING_DURATION = 0.3   # Silence duration in phrase

# Typing settings
TYPING_INTERVAL = 0.02         # Delay between keystrokes (seconds)

# ============================================
# VISUAL FEEDBACK SETTINGS
# ============================================
# HUD colors (BGR format)
COLOR_NORMAL = (0, 255, 0)      # Green
COLOR_MAGNETIC = (0, 255, 255)  # Yellow
COLOR_CLICKING = (0, 0, 255)    # Red
COLOR_DRAGGING = (0, 255, 0)    # Green
COLOR_RELEASED = (255, 255, 0)  # Cyan

# Display settings
SHOW_FPS = True                 # Show FPS counter
SHOW_INSTRUCTIONS = True        # Show instruction text
SHOW_PINCH_BAR = True          # Show pinch distance indicator

# ============================================
# PERFORMANCE SETTINGS
# ============================================
# Frame buffer size (lower = less latency)
CAMERA_BUFFER_SIZE = 1

# MediaPipe model complexity (0=light, 1=full)
MODEL_COMPLEXITY = 0

# ============================================
# SYSTEM SETTINGS
# ============================================
# PyAutoGUI settings
PYAUTOGUI_FAILSAFE = False     # Disable failsafe (move to corner to abort)
PYAUTOGUI_PAUSE = 0            # No pause between pyautogui commands

# Debug mode
DEBUG_MODE = False             # Enable detailed logging