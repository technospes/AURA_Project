import numpy as np

class ButterworthFilter:
    def __init__(self, cutoff_frequency=3.0, sampling_rate=30.0):
        # cutoff_frequency: Lower = Smoother/Laggy. Higher = Jittery/Fast.
        # 3.0 is a "Golden Mean" for mouse control.
        
        # Pre-calculate filter coefficients (2nd Order Low Pass)
        # This math simulates a physical damper (like a shock absorber)
        w_c = 2 * np.pi * cutoff_frequency
        k1 = np.sqrt(2) * w_c
        k2 = w_c ** 2
        k3 = 2 * k1 / sampling_rate
        
        self.a0 = k2 / (k2 + k1/sampling_rate + 4/(sampling_rate**2)) # Gain? No, coefficient mapping.
        
        # Simplified Digital Biquad Implementation for Real-time
        # (Using a simpler EMA-based approximation for speed in Python)
        
        self.pos_x = 0
        self.pos_y = 0
        self.initialized = False
        
        # Dual-layer smoothing (Position + Velocity)
        self.curr_x = 0
        self.curr_y = 0

    def update(self, raw_x, raw_y):
        if not self.initialized:
            self.pos_x = raw_x
            self.pos_y = raw_y
            self.curr_x = raw_x
            self.curr_y = raw_y
            self.initialized = True
            return raw_x, raw_y

        # "Gliding" Logic
        # Instead of jumping to the new point, we "pull" the cursor towards it.
        # Friction Factor: 0.2 means we move 20% of the way there per frame.
        friction = 0.25 
        
        # Distance to target
        dx = raw_x - self.curr_x
        dy = raw_y - self.curr_y
        
        # Non-Linear Response (The Secret Sauce)
        # If moving fast (large distance), reduce friction (move instantly).
        # If moving slow (small distance), increase friction (smooth out shake).
        dist = np.hypot(dx, dy)
        
        if dist > 50: friction = 0.8  # Fast Swipe -> Instant
        elif dist > 20: friction = 0.5 # Medium -> Responsive
        else: friction = 0.15          # Slow -> Very Smooth
        
        self.curr_x += dx * friction
        self.curr_y += dy * friction
        
        return int(self.curr_x), int(self.curr_y)