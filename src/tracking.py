import cv2
import mediapipe as mp
import threading
import time
from src.config import CAM_WIDTH, CAM_HEIGHT

class HandTracker:
    def __init__(self):
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=0, # 0=Fastest, 1=Accurate. We choose speed for 60FPS feel.
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.mp_draw = mp.solutions.drawing_utils
        
        # --- THREADED CAPTURE SETUP ---
        self.cap = cv2.VideoCapture(0)
        # Try to force 60 FPS (Depends on webcam hardware)
        self.cap.set(cv2.CAP_PROP_FPS, 60)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
        
        self.grabbed, self.frame = self.cap.read()
        self.started = False
        self.lock = threading.Lock()

        # Start the background thread
        self.start()

    def start(self):
        if self.started: return
        self.started = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        return self

    def update(self):
        while self.started:
            grabbed, frame = self.cap.read()
            with self.lock:
                self.grabbed = grabbed
                self.frame = frame
            time.sleep(0.005) # Tiny sleep to prevent CPU frying

    def get_frame_and_hands(self):
        with self.lock:
            img = self.frame.copy() if self.grabbed else None
        
        if img is None: return None, None
            
        img = cv2.flip(img, 1)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # Inference
        results = self.hands.process(img_rgb)
        
        landmarks = []
        if results.multi_hand_landmarks:
            for hand_lms in results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(img, hand_lms, self.mp_hands.HAND_CONNECTIONS)
                for id, lm in enumerate(hand_lms.landmark):
                    h, w, c = img.shape
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    landmarks.append([id, cx, cy])
                    
        return img, landmarks

    def release(self):
        self.started = False
        self.thread.join()
        self.cap.release()