"""
Optimized Gesture Math for 120 FPS Cursor Control
Compatible with the decoupled cursor architecture in vision_service.py
"""

import math
import time
import numpy as np

# ============================================================================
# FAST EXPONENTIAL SMOOTHER (Primary - Lightweight)
# ============================================================================
class FastSmoother:
    """
    Ultra-lightweight exponential smoothing filter.
    10x faster than Butterworth, perfect for 120 FPS cursor thread.
    
    Args:
        alpha: Smoothing factor for normal mode (0.3-0.5, higher = more responsive)
        sniper_alpha: Smoothing factor for precision mode (0.1-0.2, lower = more stable)
    """
    def __init__(self, alpha=0.35, sniper_alpha=0.15):
        self.alpha = alpha
        self.sniper_alpha = sniper_alpha
        self.x = None
        self.y = None
        self.freeze_frames = 0
        
    def update(self, raw_x, raw_y, finger_distance=100):
        """
        Update filter with new position.
        
        Returns:
            tuple: (smoothed_x, smoothed_y, mode_string)
        """
        # Click freeze (stabilize cursor during click)
        if self.freeze_frames > 0:
            self.freeze_frames -= 1
            return self.x, self.y, 'frozen'
        
        # Adaptive smoothing based on pinch distance
        mode = 'normal'
        alpha = self.alpha
        
        if finger_distance < 40:  # Sniper mode threshold
            alpha = self.sniper_alpha
            mode = 'sniper'
        
        # Initialize on first update
        if self.x is None:
            self.x, self.y = raw_x, raw_y
        else:
            # Exponential moving average
            self.x = alpha * raw_x + (1 - alpha) * self.x
            self.y = alpha * raw_y + (1 - alpha) * self.y
        
        return self.x, self.y, mode
    
    def trigger_click_freeze(self, frames=3):
        """Freeze cursor for N frames to prevent click jitter"""
        self.freeze_frames = frames
    
    def reset(self):
        """Reset filter state"""
        self.x = None
        self.y = None
        self.freeze_frames = 0


# ============================================================================
# ONE EURO FILTER (Alternative - More Sophisticated)
# ============================================================================
class OneEuroFilter:
    """
    Adaptive 1€ Filter with speed-based cutoff adjustment.
    More sophisticated than FastSmoother but ~3x slower.
    Use this if you need variable speed response.
    
    Args:
        min_cutoff: Base smoothing strength (1.0-2.0, higher = more responsive)
        beta: Speed sensitivity (0.001-0.01, higher = faster snap to movement)
        d_cutoff: Derivative filter strength (1.0 is good default)
    """
    def __init__(self, min_cutoff=1.5, beta=0.007, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        
        # Position filters
        self.x_filter = LowPassFilter()
        self.y_filter = LowPassFilter()
        
        # Velocity filters
        self.dx_filter = LowPassFilter()
        self.dy_filter = LowPassFilter()
        
        self.last_time = None
        
        # State
        self.is_frozen = False
        self.freeze_end = 0

    def trigger_click_freeze(self, duration=0.1):
        """Freeze cursor for duration seconds"""
        self.is_frozen = True
        self.freeze_end = time.perf_counter() + duration

    def update(self, x, y, pinch_dist=100):
        """
        Update filter with new position.
        
        Returns:
            tuple: (smoothed_x, smoothed_y, mode_string)
        """
        t = time.perf_counter()
        
        # Handle frozen state
        if self.is_frozen:
            if t > self.freeze_end:
                self.is_frozen = False
            else:
                return (
                    int(self.x_filter.prev_raw_value) if self.x_filter.prev_raw_value else x,
                    int(self.y_filter.prev_raw_value) if self.y_filter.prev_raw_value else y,
                    "frozen"
                )

        # Adaptive parameters based on pinch distance
        mode = "normal"
        if pinch_dist < 40:  # Sniper mode
            self.min_cutoff = 0.5
            self.beta = 0.001
            mode = "sniper"
        else:
            self.min_cutoff = 1.5
            self.beta = 0.007

        # Initialize
        if self.last_time is None:
            self.last_time = t
            self.x_filter.filter(x, 1.0)
            self.y_filter.filter(y, 1.0)
            return x, y, mode

        # Calculate time delta
        dt = t - self.last_time
        self.last_time = t
        
        if dt <= 0 or dt > 1.0:  # Guard against invalid dt
            return (
                int(self.x_filter.prev_filtered_value or x),
                int(self.y_filter.prev_filtered_value or y),
                mode
            )

        # Calculate velocity
        prev_x = self.x_filter.prev_raw_value or x
        prev_y = self.y_filter.prev_raw_value or y
        
        dx = (x - prev_x) / dt
        dy = (y - prev_y) / dt
        
        # Filter velocity
        alpha_d = self.alpha(dt, self.d_cutoff)
        edx = self.dx_filter.filter(dx, alpha_d)
        edy = self.dy_filter.filter(dy, alpha_d)

        # Calculate speed and dynamic cutoff
        speed = math.sqrt(edx**2 + edy**2)
        cutoff = self.min_cutoff + self.beta * speed

        # Filter position
        alpha = self.alpha(dt, cutoff)
        nx = self.x_filter.filter(x, alpha)
        ny = self.y_filter.filter(y, alpha)

        return int(nx), int(ny), mode

    def alpha(self, dt, cutoff):
        """Calculate alpha from cutoff frequency"""
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)
    
    def reset(self):
        """Reset filter state"""
        self.x_filter = LowPassFilter()
        self.y_filter = LowPassFilter()
        self.dx_filter = LowPassFilter()
        self.dy_filter = LowPassFilter()
        self.last_time = None


class LowPassFilter:
    """Simple low-pass filter for 1€ implementation"""
    def __init__(self):
        self.prev_raw_value = None
        self.prev_filtered_value = None

    def filter(self, value, alpha):
        if self.prev_raw_value is None:
            s = value
        else:
            s = alpha * value + (1.0 - alpha) * self.prev_filtered_value
        
        self.prev_raw_value = value
        self.prev_filtered_value = s
        return s


# ============================================================================
# KALMAN FILTER (Advanced - Predictive)
# ============================================================================
class KalmanFilter:
    """
    Kalman filter with prediction for minimal latency.
    Most advanced but requires tuning. Use for ultra-low latency needs.
    
    Args:
        process_noise: System uncertainty (0.001-0.01, lower = trust model more)
        measurement_noise: Sensor uncertainty (0.1-1.0, lower = trust measurements more)
    """
    def __init__(self, process_noise=0.005, measurement_noise=0.5):
        self.q = process_noise  # Process noise
        self.r = measurement_noise  # Measurement noise
        
        # State for X and Y (position, velocity)
        self.x_state = np.array([0.0, 0.0])  # [position, velocity]
        self.y_state = np.array([0.0, 0.0])
        
        # Covariance matrices
        self.x_p = np.eye(2)
        self.y_p = np.eye(2)
        
        # State transition (assume constant velocity)
        self.F = np.array([[1.0, 1.0],
                          [0.0, 1.0]])
        
        # Measurement matrix (we only measure position)
        self.H = np.array([[1.0, 0.0]])
        
        self.initialized = False
    
    def update(self, x, y):
        """
        Update filter and predict next position.
        
        Returns:
            tuple: (predicted_x, predicted_y)
        """
        if not self.initialized:
            self.x_state = np.array([x, 0.0])
            self.y_state = np.array([y, 0.0])
            self.initialized = True
            return x, y
        
        # Predict
        self.x_state = self.F @ self.x_state
        self.y_state = self.F @ self.y_state
        
        self.x_p = self.F @ self.x_p @ self.F.T + self.q * np.eye(2)
        self.y_p = self.F @ self.y_p @ self.F.T + self.q * np.eye(2)
        
        # Update X
        y_res_x = x - (self.H @ self.x_state)[0]
        S_x = (self.H @ self.x_p @ self.H.T + self.r)[0, 0]
        K_x = (self.x_p @ self.H.T) / S_x
        self.x_state = self.x_state + K_x.flatten() * y_res_x
        self.x_p = (np.eye(2) - np.outer(K_x, self.H)) @ self.x_p
        
        # Update Y
        y_res_y = y - (self.H @ self.y_state)[0]
        S_y = (self.H @ self.y_p @ self.H.T + self.r)[0, 0]
        K_y = (self.y_p @ self.H.T) / S_y
        self.y_state = self.y_state + K_y.flatten() * y_res_y
        self.y_p = (np.eye(2) - np.outer(K_y, self.H)) @ self.y_p
        
        return int(self.x_state[0]), int(self.y_state[0])
    
    def predict(self):
        """Get predicted next position without updating"""
        next_x = (self.F @ self.x_state)[0]
        next_y = (self.F @ self.y_state)[0]
        return int(next_x), int(next_y)


# ============================================================================
# LEGACY COMPATIBILITY (Deprecated)
# ============================================================================
class ButterworthFilter:
    """
    Legacy Butterworth filter - DEPRECATED.
    Use FastSmoother instead for better performance.
    Kept for backward compatibility only.
    """
    def __init__(self):
        print("[WARNING] ButterworthFilter is deprecated. Use FastSmoother instead.")
        self.smoother = FastSmoother()
    
    def update(self, x, y, finger_distance=100):
        return self.smoother.update(x, y, finger_distance)
    
    def trigger_click_freeze(self):
        self.smoother.trigger_click_freeze()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def calculate_distance(p1, p2):
    """Fast Euclidean distance between two points"""
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2) ** 0.5


def calculate_angle(p1, p2, p3):
    """Calculate angle between three points (in degrees)"""
    v1 = (p1[0] - p2[0], p1[1] - p2[1])
    v2 = (p3[0] - p2[0], p3[1] - p2[1])
    
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag1 = math.sqrt(v1[0]**2 + v1[1]**2)
    mag2 = math.sqrt(v2[0]**2 + v2[1]**2)
    
    if mag1 == 0 or mag2 == 0:
        return 0
    
    cos_angle = dot / (mag1 * mag2)
    cos_angle = max(-1.0, min(1.0, cos_angle))  # Clamp
    
    return math.degrees(math.acos(cos_angle))


def smooth_value(current, target, alpha=0.3):
    """Simple exponential smoothing for single values"""
    return alpha * target + (1 - alpha) * current


# ============================================================================
# BENCHMARKING
# ============================================================================
if __name__ == "__main__":
    """Performance test of different filters"""
    import timeit
    
    print("=" * 60)
    print("FILTER PERFORMANCE BENCHMARK")
    print("=" * 60)
    
    # Test data
    test_x, test_y = 320.5, 240.7
    
    # FastSmoother
    fast = FastSmoother()
    fast_time = timeit.timeit(
        lambda: fast.update(test_x, test_y, 50),
        number=10000
    )
    
    # OneEuroFilter
    euro = OneEuroFilter()
    euro_time = timeit.timeit(
        lambda: euro.update(test_x, test_y, 50),
        number=10000
    )
    
    # Kalman (requires numpy)
    try:
        kalman = KalmanFilter()
        kalman_time = timeit.timeit(
            lambda: kalman.update(test_x, test_y),
            number=10000
        )
    except:
        kalman_time = None
    
    print(f"\nFastSmoother:     {fast_time*1000:.2f}ms (10k iterations)")
    print(f"OneEuroFilter:    {euro_time*1000:.2f}ms (10k iterations)")
    if kalman_time:
        print(f"KalmanFilter:     {kalman_time*1000:.2f}ms (10k iterations)")
    
    print(f"\nPer-call latency:")
    print(f"FastSmoother:     {fast_time/10:.2f}µs")
    print(f"OneEuroFilter:    {euro_time/10:.2f}µs")
    if kalman_time:
        print(f"KalmanFilter:     {kalman_time/10:.2f}µs")
    
    print(f"\nRecommendation: Use FastSmoother for 120 FPS cursor thread")
    print("=" * 60)