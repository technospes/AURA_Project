# AURA: AI-Powered Multimodal Interface

Control your system without touching a mouse or keyboard. AURA turns your webcam and microphone into a high‑precision, hands‑free human–computer interface.

AURA is a high-performance, Python-based HCI that replaces traditional peripherals with computer vision and voice recognition. By combining real-time hand tracking with speech commands, it enables a “Minority Report” style interaction model suitable for accessibility, clean-room environments, and futuristic productivity workflows.

---

## Table of Contents

- [Demo](#demo)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [System Architecture](#system-architecture)
- [Installation & Setup](#installation--setup)
- [How to Run](#how-to-run)
- [Usage Guide](#usage-guide)
  - [Hand Gestures](#hand-gestures)
  - [Voice Commands](#voice-commands)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Performance & Optimization](#performance--optimization)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contribution Guidelines](#contribution-guidelines)
- [License](#license)
- [Author](#author)

---

## Demo

> Place GIFs / screenshots here (e.g. `assets/gesture_control.gif`, `assets/voice_commands.gif`).

Examples:
- Real-time gesture tracking with visual feedback bars and status indicators.
- Voice-triggered application launch and text dictation while cursor control remains active.

---

## Key Features

- **Physics-Based Air Mouse**  
  Advanced cursor control with acceleration curves, configurable deadzones, and “sticky” friction logic for pixel‑level precision.

- **Concurrent Voice Control**  
  Multi-threaded voice engine capable of opening apps, typing text, and executing shortcuts without interrupting gesture control.

- **Smart Gesture Recognition**  
  - Clicking: Thumb–index pinch with hysteresis to avoid accidental double-clicks.  
  - Scrolling: Two-finger navigation mode.  
  - Auxiliary Actions: Right-click (pinky) and double-click (ring finger) pinch triggers.

- **High-Performance Architecture**  
  Threaded camera capture and non‑blocking loops keep the UI responsive and capable of 60 FPS on typical hardware.

- **Robust Smoothing**  
  Butterworth and One Euro filters reduce hand jitter while preserving low-latency, natural movement.

- **Visual HUD**  
  On-screen overlay shows pinch distance, system status, active modes, and recognized commands in real time.

---

## Tech Stack

**Core**

- Language: Python 3.x  
- Computer Vision: OpenCV, MediaPipe  
- Math & Physics: NumPy  
- Automation: PyAutoGUI, AppOpener  

**Audio & Input**

- Online Voice Recognition: `SpeechRecognition` (Google API) + PyAudio  
- Offline Voice Recognition (optional): Vosk models under `models/`  
- Concurrency: `threading`, `queue` for non‑blocking I/O and command processing

---

## System Architecture

AURA uses a non-blocking, multi-threaded architecture to maintain responsiveness under continuous CV and audio workloads.

- **Vision Thread**  
  Captures webcam frames asynchronously to maximize throughput and decouple I/O from processing.

- **Voice Thread**  
  Listens continuously in the background, transcribes speech (online or offline), and pushes intents into a command queue.

- **Main Loop (Physics Engine)**  
  - Processes MediaPipe hand landmarks.  
  - Applies smoothing filters (Butterworth / One Euro) to reduce jitter.  
  - Computes vector distances for gesture triggers.  
  - Updates cursor position with a variable friction model (cursor slows as you approach a pinch).  
  - Consumes and executes queued voice commands.

---

## Installation & Setup

### Prerequisites

- Python 3.10+ (recommended)  
- Webcam  
- Microphone  

### Clone the Repository

git clone https://github.com/yourusername/aura-project.git
cd aura-project

### Create a Virtual Environment

Windows

python -m venv venv
venv\Scripts\activate
Linux / macOS

python3 -m venv venv
source venv/bin/activate

### Install Dependencies

pip install -r requirements.txt

> On Linux you may also need: `python3-tk` and `python3-dev`.

---


## How to Run

1. Ensure your webcam and microphone are connected.  
2. Activate your virtual environment (see above).  
3. Run the main entry point:

python run.py

The "Jarvis Vision" window will appear. Hold your hand up to the camera to engage.

---

## Usage Guide;

### Hand Gestures

| Gesture                | Action       | Visual Cue        |
|------------------------|--------------|-------------------|
| Index finger point     | Move cursor  | Green cursor HUD  |
| Index + thumb pinch    | Left click   | Red HUD bar       |
| Index + middle up      | Scroll mode  | Text: `SCROLL`    |
| Pinky + thumb pinch    | Right click  | Yellow flash      |
| Ring + thumb pinch     | Double click | Magenta flash     |
| Index + thumb pinch    | Hold object  | Red HUD bar       | 

### Voice Commands

By default, the voice engine listens continuously and strips the wake-word `"Jarvis"` from recognized text in logs.  
You can customize wake-word handling and behavior in `src/voice.py` and `src/config.py`.

Examples:

- `Open [App Name]` – Launches applications (e.g., “Open Notepad”).  
- `Close [App Name]` – Terminates applications.  
- `Type [Text]` – Dictates text into the active field.  
- `New Tab` / `Close Tab` – Browser tab controls.  
- `Select All` / `Copy` / `Paste` – Clipboard operations.  
- `Stop` – Safely terminates the current command sequence.

> Note: Choose between online (Google API) and offline (Vosk) recognition in `src/voice.py` / `src/config.py` based on your latency and privacy requirements.

--

📂 Project Folder Structure

aura_project/
├── models/                  # Offline speech models (Vosk)
├── src/
│   ├── __init__.py
│   ├── config.py            # Central configuration (Sensitivity, Thresholds)
│   ├── context.py           # Context awareness logic
│   ├── control.py           # Mouse physics and gesture state machine
│   ├── smoothing.py         # Jitter reduction filters
│   ├── tracking.py          # MediaPipe & Camera threading
│   └── voice.py             # Speech recognition engine
├── run.py                   # Main application entry point
├── requirements.txt         # Dependency list
└── README.md                # Documentation

--

🔧 Configuration

All runtime parameters can be tuned in `src/config.py` to match your hardware and environment:

- `CAM_WIDTH` / `CAM_HEIGHT` – Camera resolution (default: 640×480 for speed).  
- `SMOOTHING_BETA` – Trade-off between jitter reduction and latency (lower = smoother, higher = more responsive).  
- `CLICK_THRESHOLD` – Pixel distance between fingers to register a click.  
- `CURSOR_ACCELERATION` – Controls how “heavy” the cursor feels.  
- Wake word, hotkeys, and engine selection (online vs offline) – configured via dedicated flags and constants.

---

## Performance & Optimization

- **Threaded I/O**  
  Camera capture runs in a dedicated daemon thread, preventing frame drops during heavy CV inference.

- **Variable Friction Physics**  
  Cursor velocity is scaled by a dynamic friction model. As your fingers approach a pinch, speed is dampened (e.g., ~0.3×), making it easier to click small UI elements without drifting.

- **Lightweight Inference**  
  MediaPipe is configured with `model_complexity = 0` to prioritize frame rate over mesh density, which is ideal for real-time pointer control.

---

## Known Limitations

- **Lighting** – Requires decent ambient lighting for stable hand tracking.  
- **Occlusion** – Tracking may fail if the hand crosses the face or moves out of frame.  
- **Audio Noise** – Voice commands can degrade in very noisy environments; a noise-cancelling microphone is recommended.

---

## Roadmap

- [ ] Integration of local LLMs (Llama/Mistral) for context-aware, semantic commands.  
- [ ] Custom wake word engine (Porcupine / OpenWakeWord).  
- [ ] 3D gesture support with depth-aware interaction.  
- [ ] Cross-platform packaging and performance tuning for macOS / Linux.

--

## Contribution Guidelines

Contributions and experiments are welcome.

1. Fork the repository.  
2. Create a feature branch:  

git checkout -b feature/AmazingFeature

3. Commit your changes:  

git commit -m "Add AmazingFeature"

4. Push to the branch:  

git push origin feature/AmazingFeature

5. Open a Pull Request.

---


📄 License

Distributed under the MIT License. See `LICENSE` for details.

## Author

**Technospes**

- LinkedIn: https://www.linkedin.com/in/ayushshukla-ar/  
- GitHub: https://github.com/technospes
