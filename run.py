import cv2
import time
import pyautogui
from AppOpener import open as app_open, close as app_close
from src.tracking import HandTracker
from src.smoothing import ButterworthFilter
from src.control import MouseController
from src.voice import VoiceEngine
from src.context import ContextManager
import numpy as np

# Visual Feedback Bar
def draw_hud(img, dist, status_text):
    # Map distance (0-100) to bar height (300-100)
    h = int(np.interp(dist, [0, 80], [300, 100]))
    h = np.clip(h, 100, 300)
    
    color = (0, 255, 0) # Green (Open)
    if dist < 50: color = (0, 255, 255) # Yellow (Sticky Zone)
    if dist < 30: color = (0, 0, 255)   # Red (Click Zone)

    cv2.rectangle(img, (20, 100), (40, 300), (50, 50, 50), -1)
    cv2.rectangle(img, (20, h), (40, 300), color, -1)
    
    # Threshold Lines
    cv2.line(img, (10, 225), (50, 225), (0, 0, 255), 2) # 30px Click
    cv2.line(img, (10, 175), (50, 175), (0, 255, 255), 2) # 50px Sticky

    cv2.putText(img, f"{int(dist)}px", (50, h), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.putText(img, status_text, (60, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

def execute_command(cmd):
    cmd = cmd.lower().replace("jarvis", "").strip()
    print(f"EXECUTING: {cmd}")
    
    if "stop" in cmd: return False

    # 1. APP CONTROL
    if "open" in cmd:
        app = cmd.replace("open", "").strip()
        try: 
            print(f"Opening {app}...")
            app_open(app, match_closest=True, output=False)
            # Wait for app to actually appear
            time.sleep(2.0) 
        except: pass
        
    elif "close" in cmd:
        app = cmd.replace("close", "").strip()
        try: app_close(app, match_closest=True, output=False)
        except: pass

    # 2. TYPING (Robust Method)
    elif "type" in cmd:
        text = cmd.replace("type", "").strip()
        print(f"Typing: {text}")
        # Type slowly (0.1s per key) to ensure app captures it
        pyautogui.write(text + " ", interval=0.05) 

    # 3. KEYBOARD SHORTCUTS
    elif "press enter" in cmd: pyautogui.press("enter")
    elif "delete" in cmd: pyautogui.press("backspace")
    elif "select all" in cmd: pyautogui.hotkey("ctrl", "a")
    elif "copy" in cmd: pyautogui.hotkey("ctrl", "c")
    elif "paste" in cmd: pyautogui.hotkey("ctrl", "v")
    
    # 4. BROWSER TABS
    elif "new tab" in cmd: pyautogui.hotkey("ctrl", "t")
    elif "close tab" in cmd: pyautogui.hotkey("ctrl", "w")
    
    return True

def main():
    print("Initializing Jarvis V11 (60FPS Threaded)...")
    
    voice = VoiceEngine()
    try:
        tracker = HandTracker()
    except:
        print("Camera failed.")
        return

    # Smoother beta=0.1 means VERY smooth (less jitter), slightly more lag. 
    # Increase to 0.5 if you want faster reaction.
    smoother = ButterworthFilter() 
    mouse = MouseController()
    
    print("SYSTEM ONLINE.")
    
    running = True
    while running:
        try:
            img, landmarks = tracker.get_frame_and_hands()
        except KeyboardInterrupt:
            break
        
        if img is None: break

        status_text = "Idle"
        pinch_dist = 100

        if landmarks:
            # Finger States
            index_up = landmarks[8][2] < landmarks[6][2]
            middle_up = landmarks[12][2] < landmarks[10][2]
            ring_up = landmarks[16][2] < landmarks[14][2]

            # MODE 1: SCROLL (Index + Middle UP)
            if index_up and middle_up and not ring_up:
                status_text = mouse.process_scroll(landmarks)
                
            # MODE 2: CURSOR (Index Only)
            elif index_up and not middle_up:
                # 1. Smooth Coordinates
                rx, ry = landmarks[8][1], landmarks[8][2]
                sx, sy = smoother.update(rx, ry)
                
                # 2. Map to Screen
                tx, ty = mouse.map_coordinates(sx, sy)
                
                # 3. Calculate raw pinch distance for physics engine
                thumb = landmarks[4]
                index = landmarks[8]
                raw_dist = np.hypot(thumb[1]-index[1], thumb[2]-index[2])

                # 4. Move (With "Sticky" Logic)
                mouse.move_with_physics(tx, ty, raw_dist)
                
                # 5. Handle Clicks
                status, color, pinch_dist = mouse.process_gestures(landmarks)
                status_text = status

            else:
                status_text = "PAUSED"
                # Reset acceleration history
                smoother.x_prev = 0 

        draw_hud(img, pinch_dist, status_text)
        
        # Voice Logic
        cmd = voice.get_command()
        if cmd and "jarvis" in cmd:
            # Visual Feedback
            cv2.rectangle(img, (0,0), (640, 50), (0, 255, 0), -1)
            cv2.putText(img, f"CMD: {cmd}", (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,0), 2)
            cv2.imshow("Jarvis Vision", img)
            cv2.waitKey(10) # Force render
            
            if not execute_command(cmd):
                running = False

        cv2.imshow("Jarvis Vision", img)
        if cv2.waitKey(1) & 0xFF == 27: break

    tracker.release()
    voice.stop()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()