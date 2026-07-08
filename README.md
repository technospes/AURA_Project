# Project_AURA: AI-Powered Multimodal Interface

Control your system without touching a mouse or keyboard. Project_AURA turns your webcam and microphone into a high‑precision, hands‑free human–computer interface.

Project_AURA is a high-performance, Python-based HCI that replaces traditional peripherals with computer vision and voice recognition. By combining real-time hand tracking with speech commands, it enables a "Minority Report" style interaction model suitable for accessibility, clean-room environments, and futuristic productivity workflows.

---

## Table of Contents

- [Demo](#demo)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [System Architecture](#system-architecture)
- [Jarvis AI Subsystem — Deep Dive](#jarvis-ai-subsystem--deep-dive)
  - [Jarvis Pipeline Overview](#jarvis-pipeline-overview)
  - [Jarvis Backend Components](#jarvis-backend-components)
  - [Jarvis Frontend (Electron + React + TypeScript)](#jarvis-frontend-electron--react--typescript)
  - [Jarvis Key Features Implemented](#jarvis-key-features-implemented)
  - [Jarvis Files Structure](#jarvis-files-structure)
  - [Jarvis Production Readiness](#jarvis-production-readiness)
  - [Running Jarvis in Dev Mode](#running-jarvis-in-dev-mode)
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

https://github.com/user-attachments/assets/86212eb6-dfd5-4f95-9750-7d913c468568

Examples:
- Real-time gesture tracking with visual feedback bars and status indicators.
- Voice-triggered application launch and text dictation while cursor control remains active.
- Seamless conversational AI with procedural UI waveform animations.

---

## Key Features

- **Physics-Based Air Mouse** – Advanced cursor control with acceleration curves, configurable deadzones, and "sticky" friction logic for pixel‑level precision.

- **Jarvis  AI Subsystem** – A highly advanced, context-aware AI agent powered by Groq. It utilizes a Fast Router to bypass LLMs for simple commands (<1ms response) and streams complex LLM responses chunk-by-chunk for sub-300ms latency.

- **Floating Orb UI (Glassmorphism)** – A beautiful, borderless Electron/React desktop overlay. Features a draggable, interactive Orb with procedural Framer Motion physics that react and "squeeze" in sync with voice activity and system states.

- **Full-Duplex Audio & Local TTS** – Features Acoustic Echo Cancellation (AEC) and barge-in support (saying "stop" interrupts the AI). Voice responses are powered locally by Kokoro TTS for a deep, crisp, zero-cost AI voice.

- **Smart Gesture Recognition**
  - Clicking: Thumb–index pinch with hysteresis to avoid accidental double-clicks.
  - Scrolling: Two-finger navigation mode.
  - Auxiliary Actions: Right-click (pinky) and double-click (ring finger) pinch triggers.

- **High-Performance Architecture** – Threaded camera capture and non‑blocking loops keep the UI responsive and capable of 60 FPS on typical hardware.

- **Robust Smoothing** – Butterworth and One Euro filters reduce hand jitter while preserving low-latency, natural movement.

- **Visual HUD & Dashboard** – On-screen overlay shows pinch distance, while clicking the UI Orb expands a modern dashboard showing recent commands, system memory, and audio settings.

---

## Tech Stack

**Core**

- Language: Python 3.10+ & TypeScript/JavaScript
- Computer Vision: OpenCV, MediaPipe
- Math & Physics: NumPy
- Automation: PyAutoGUI, AppOpener

**Frontend UI**

- Framework: React, Electron, Vite
- Styling: TailwindCSS, Lucide React Icons
- Animation: Framer Motion (Procedural Audio Waveforms & Drag Physics)

**Audio & AI Agent**

- LLM Engine: AsyncGroq API (Llama-3)
- TTS (Text-to-Speech): Kokoro (Local) & PyGame
- Online Voice Recognition: `SpeechRecognition` (Google API) + PyAudio
- Offline Voice Recognition (optional): Vosk models under `models/`
- Concurrency: `threading`, `queue`, and WebSockets for non‑blocking I/O.

---

## System Architecture

Project_AURA uses a non-blocking, multi-threaded architecture to maintain responsiveness under continuous CV and audio workloads.

- **Vision Thread** – Captures webcam frames asynchronously to maximize throughput and decouple I/O from processing.

- **Voice Thread & Agent Core** – Listens continuously in the background, processes wake words instantly, and manages the State Machine (Idle, Listening, Thinking, Speaking).

- **UI WebSocket Bridge** – A full-duplex WebSocket server (`ui_bridge.py`) that syncs the Python backend state to the React frontend at 60Hz.

- **Main Loop (Physics Engine)**
  - Processes MediaPipe hand landmarks.
  - Applies smoothing filters (Butterworth / One Euro) to reduce jitter.
  - Computes vector distances for gesture triggers.
  - Updates cursor position with a variable friction model (cursor slows as you approach a pinch).
  - Consumes and executes queued voice commands.

---

## Jarvis AI Subsystem — Deep Dive

The **Jarvis  AI Subsystem** referenced above is not a simple voice assistant — it's a goal-driven, autonomous desktop agent that understands complex commands, decomposes them into multi-step plans, executes them with real desktop control (apps, browser, WhatsApp, clicks), verifies results, and recovers from failures. This section documents its internal architecture in full.

### Jarvis Pipeline Overview

```
User speaks → Wake Word (Vosk) → STT (Whisper) → Semantic Correction (Groq LLM)
    ↓
Fast Router (3-tier local classifier: exact → keyword → regex → LLM fallback)
    ↓
Intent Engine → Decision Engine → Planner → Task Graph → Executor
    ↓
Tools: App Launcher, Browser, Keyboard, Media, WhatsApp, Click Simulator
    ↓
Verification + Reflection → TTS Response
```

### Jarvis Backend Components

**Voice Pipeline** (`voice/service.py`)
- Wake word detection using Vosk ("Jarvis")
- Audio recording with silence detection, pre-roll buffer, AGC
- STT via faster-whisper (small.en model, CUDA-accelerated)
- Semantic correction via Groq LLM for post-STT fixes
- Full-duplex barge-in (user can interrupt Jarvis while speaking)
- Follow-up microphone auto-trigger for multi-turn conversations

**Intent System**
- **Fast Router** (`fast_router.py`): 3-tier local classifier handling ~80% of commands in <1ms without LLM calls
- **Intent Engine** (`voice/intent_engine.py`): LLM-based intent classification for complex commands
- **Dynamic Intent Registry** (`core/intent_registry.py`): Priority-based pattern matching with 36 patterns across 27 intents

**Planning & Execution**
- **Planner** (`planner/engine.py`): Converts intents to executable step sequences with memory-aware preferences
- **Task Graph** (`core/task_graph.py`): Dependency-aware parallel execution with retry logic
- **Execution Runner** (`executor/runner.py`): Step-by-step execution with verification, retry, and fallback chains
- **Goal Manager** (`core/goal_manager.py`): Persistent goal tracking with disk-based state

**Tool Ecosystem** (all dynamic via `CapabilityRegistry`)
- **AppLauncherTool** – Open/close/focus any Windows application
- **BrowserTool** – URL navigation, search, tab management
- **MediaControllerTool** – Spotify/YouTube playback control
- **KeyboardTool** – Text typing, file saving, scrolling
- **WebNavigatorTool** – Multi-site web research with content extraction
- **AIBrainTool** – Groq-powered Q&A and research synthesis
- **CommunicatorTool** – Discord calling
- **SystemTool** – Screenshot, shutdown, restart, lock, volume control
- **ClickSimulatorTool** – UI element tagging + click simulation
- **UnifiedCommunicationTool** – WhatsApp messaging and calling with contact search + confirmation flow
- **PageContextTool** – Screen content extraction and summarization

**Autonomous Task Pipeline**
- Pattern-based planning for common tasks (search+summarize, standalone summarize)
- LLM-based planning for novel tasks
- Multi-step execution with dependency tracking
- Streaming sentence-by-sentence TTS for real-time feedback
- Contact alias resolution (64 aliases for family relationships)
- WhatsApp confirmation flow: search → highlight → ask user → execute

**Desktop Automation**
- **Screen Awareness** (`screen_awareness.py`): UIA-based window content extraction
- **App Locator** (`utils/app_locator.py`): Multi-strategy app discovery (PATH, Registry, Start Menu, disk index)
- **Web Navigation** (`src/web_navigation.py`): Autonomous multi-site research with DuckDuckGo
- **Click Simulation** (`executor/tools/click_simulator.py`): UI element tagging with coordinate-based clicking

**State Management**
- **Capability Registry** (`core/capability_registry.py`): Dynamic tool registration (no hardcoded if/elif chains)
- **Intent Registry** (`core/intent_registry.py`): Dynamic pattern registration with priority system
- **World Model** (`agent/world_model.py`): Thread-safe shared runtime state
- **Context Tracker** (`context/tracker.py`): Rolling context with implicit reference resolution
- **Memory Store** (`memory/store.py`): Persistent fact/preference storage
- **Event Bus** (`jarvis_patch/core_patch.py`): Decoupled publish/subscribe system

### Jarvis Frontend (Electron + React + TypeScript)

```
Electron Main Process → Transparent frameless always-on-top window
    ↓
React App → WebSocket connection to Python backend
    ↓
Framer Motion → Physics-based animations at 60fps
```

**The Orb UI** (`src/components/Orb.tsx`)
- **Idle State**: Slow floating animation with soft blue glow
- **Listening State**: Squeeze/pulse animation when wake word detected
- **Thinking State**: Rotating animation with purple glow
- **Speaking State**: Lub-dub heartbeat pattern synchronized with TTS output
- **Draggable**: Physics-based drag with momentum and position persistence
- **Transcript Bubble**: Glassmorphism overlay showing Jarvis's spoken response

**Dashboard Panel**
- **Main Menu**: Voice Settings, Recent Commands, Memory & Preferences, Settings
- **Recent Commands**: Last 10 commands with timestamps and intent tags
- **Memory View**: Facts, aliases, and preferences from the backend
- **Settings Panel**: API key status, startup toggle, about section
- **Voice Settings**: Microphone selection, wake word sensitivity, language
- **Glassmorphism Design**: Blur backgrounds, translucent borders, dark theme

**Window Management** (`electron/main.js`)
- Full-screen transparent overlay
- Click-through for non-interactive areas
- IPC-based mouse event passthrough toggling
- System tray integration with show/hide/quit
- Position persistence via localStorage

### Jarvis Key Features Implemented

**Voice Commands**
- "Open Chrome/Notepad/Spotify"
- "Search for weather in Delhi"
- "Play Believer on Spotify"
- "Take a screenshot"
- "Lock the screen"
- "Shut down the PC"
- "What time is it?"
- "Close VS Code" (with known process name mapping)

**Autonomous Multi-Step Tasks**
- "Search for AI news and summarize it" → 3-step plan (search → fetch → synthesize)
- "Summarize this page" → Screen content extraction + LLM summary
- Streaming sentence-by-sentence TTS for real-time feedback

**WhatsApp Automation**
- "Call/Message [contact] on WhatsApp"
- Contact alias resolution (mummy→Mom, daddy→Dad, bhai→Brother, etc.)
- Search → highlight → confirmation → execute flow
- User can navigate results ("No, the one below", "Two down")
- Known process name mapping for reliable app closing

**System Control**
- Brightness, volume, resolution changes (WMI/PowerShell APIs)
- Process management (psutil + taskkill with known process map)
- Screenshot with OneDrive-aware desktop path resolution

**UI/UX**
- Floating draggable orb with state-based animations
- Heartbeat animation synchronized with TTS speaking
- Glassmorphism dashboard with sliding sub-views
- Click-outside-to-close and Escape key handling
- System tray with show/hide/quit
- Position persistence across restarts

### Jarvis Files Structure

```
E:\Jarvis\
├── main.py                          # Entry point, voice process, JarvisSystem
├── ui_bridge.py                     # WebSocket server for frontend state
├── voice/
│   ├── service.py                   # Voice pipeline, wake word, recording, STT
│   └── intent_engine.py             # Intent classification + dynamic registry
├── planner/
│   └── engine.py                    # Plan generation, memory-aware preferences
├── executor/
│   ├── runner.py                    # Tool implementations, execution engine
│   ├── runner_patch.py              # Execution patches (interrupt, retry)
│   ├── validator.py                 # URL validation, fallback URLs
│   └── tools/
│       ├── click_simulator.py       # UI element clicking
│       └── communication_tool.py    # WhatsApp automation
├── core/
│   ├── capability_registry.py       # Dynamic tool registry
│   ├── intent_registry.py           # Dynamic intent pattern registry
│   ├── task_graph.py                # Dependency-aware execution graph
│   ├── goal_manager.py              # Persistent goal tracking
│   ├── contract.py                  # Verification contracts
│   └── autonomous_loop.py           # Observe→Reason→Act→Verify loop
├── agent/
│   ├── core.py                      # JarvisAgentCore orchestrator
│   ├── decision.py                  # Decision engine (EXECUTE/CLARIFY/ANSWER/IGNORE)
│   ├── world_model.py               # Shared runtime state
│   └── intent_registry.py           # Intent metadata (slots, context-free)
├── jarvis_patch/
│   ├── core_patch.py                # Main process patched, SystemActionTool, EventBus
│   ├── stt_patch.py                 # Enhanced STT pipeline
│   ├── semantic_corrector.py        # LLM-based post-STT correction
│   ├── tool_builder.py              # LLM-based dynamic tool generation
│   └── safety_validator.py          # AST-based code safety validation
├── ui/                              # Electron + React frontend
│   ├── electron/
│   │   ├── main.js                  # Electron main process
│   │   ├── preload.js               # IPC bridge
│   │   └── tray.js                  # System tray
│   ├── src/
│   │   ├── App.tsx                  # Root component, WebSocket connection
│   │   ├── components/
│   │   │   └── Orb.tsx              # Floating orb + dashboard
│   │   └── styles/
│   │       └── globals.css          # Tailwind + transparent background
│   ├── package.json
│   ├── vite.config.ts
│   └── index.html
├── data/
│   └── aliases.json                 # 64 family contact aliases
├── fast_router.py                   # 3-tier local intent classifier
├── screen_awareness.py              # UIA-based screen content extraction
├── session_memory.py                # Session-level page context
└── agent_state.py                   # CentralAgentState, CommandRouter, TTSQueue
```

### Jarvis Production Readiness

**What's Solid**
- Dynamic tool/intent registration (no hardcoded chains)
- Verification contracts for critical actions
- Retry with fallback chains
- Process name mapping for reliable app closing
- Multi-turn conversation with follow-up microphone
- Full-duplex barge-in
- Streaming TTS for real-time feedback
- WhatsApp confirmation flow prevents wrong contacts
- Position persistence across restarts
- System tray integration

**Known Limitations (Jarvis subsystem)**
- `pyautogui` fallback disabled (hard-stop on UI automation failure)
- WebSearchTool capture patch fails (non-fatal)
- No streaming STT (uses segment-based approach)
- TTS phonemizer warnings on certain texts
- 0 contacts synced from Outlook (data sync functional but empty)

### Running Jarvis in Dev Mode

```bash
# Terminal 1: Python Backend
cd E:\Jarvis
venv\Scripts\activate
python main.py

# Terminal 2: Vite Dev Server
cd E:\Jarvis\ui
npx vite

# Terminal 3: Electron
cd E:\Jarvis\ui
npx electron .
```

Wake word: **"Jarvis"**

---

## Installation & Setup

### Prerequisites

- Python 3.10+ (recommended)
- Node.js & npm (for the UI)
- Webcam
- Microphone

### Clone the Repository

```bash
git clone https://github.com/technospes/Project_AURA_Project.git
cd Project_AURA-project
```

### Create a Virtual Environment

**Windows**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Dependencies

```bash
# Install Python backend dependencies
pip install -r requirements.txt

# Install Frontend UI dependencies
cd ui
npm install
cd ..
```

On Linux you may also need: `python3-tk` and `python3-dev`.

### Environment Variables

Create a `.env` file in the root directory and add your Groq API key:

```
GROQ_API_KEY=your_api_key_here
```

---

## How to Run

1. Ensure your webcam and microphone are connected.
2. Activate your virtual environment (see above).
3. Build the UI and start the main entry point:

```bash
python main.py
```

The "Jarvis Vision" window will appear, and the borderless Floating Orb UI will launch on your desktop. Hold your hand up to the camera to engage.

---

## Usage Guide

### Hand Gestures

| Gesture | Action | Visual Cue |
|---|---|---|
| Index finger point | Move cursor | Green cursor HUD |
| Index + thumb pinch | Left click | Red HUD bar |
| Index + middle up | Scroll mode | Text: SCROLL |
| Pinky + thumb pinch | Right click | Yellow flash |
| Ring + thumb pinch | Double click | Magenta flash |
| Index + thumb pinch | Hold object | Red HUD bar |

### Voice Commands

By default, the voice engine listens continuously and triggers high-performance procedural animations on the Orb when the wake-word **"Jarvis"** is detected.

Examples:
- **Open [App Name]** – Launches applications (e.g., "Open Notepad").
- **Close [App Name]** – Terminates applications.
- **Type [Text]** – Dictates text into the active field.
- **Search for [Topic]** – Autonomously searches the web and synthesizes research.
- **Summarize this page** – Context-aware reading of the active screen/browser tab.
- **Stop** – Safely terminates the current command sequence via barge-in.

---

## Project Structure

```
Project_AURA_project/
├── models/                        # Offline speech models (Vosk)
├── ui/                             # Electron + React Frontend
│   ├── src/components/            # Framer Motion Orb & Dashboards
│   ├── main.js                    # Electron transparent window shell
│   └── package.json
├── src/
│   ├── config.py                  # Central configuration (sensitivity, thresholds)
│   ├── context.py                 # Context awareness logic
│   ├── control.py                 # Mouse physics and gesture state machine
│   ├── smoothing.py               # Jitter reduction filters
│   ├── tracking.py                # MediaPipe & camera threading
│   ├── audio_config_optimized.py  # Optimized microphone/audio capture configuration
│   ├── brain.py                   # Core AI reasoning / decision module
│   ├── click_drag_system.py       # Click-and-drag gesture handling
│   ├── diagnose_voice.py          # Voice pipeline diagnostics utility
│   ├── gesture_math.py            # Vector/geometry math for gesture recognition
│   ├── intent_parser.py           # Parses voice/text input into structured intents
│   ├── memory_system.py           # Fact/preference memory storage
│   ├── native_opener.py           # Native OS app/file opening utility
│   ├── precision_cursor.py        # High-precision cursor positioning logic
│   ├── shared.py                  # Shared constants/helpers across src modules
│   ├── task_planner.py            # Task decomposition & planning
│   ├── vision_service.py          # Computer vision service wrapper
│   ├── voice_io.py                # Voice input/output handling
│   ├── voice_service.py           # Voice pipeline service
│   └── web_navigation.py          # Autonomous multi-site web research
├── utils/
│   └── app_locator.py             # Multi-strategy app discovery (PATH, Registry, Start Menu, disk index)
├── agent/
│   ├── advisor.py                 # Suggests next actions / recommendations
│   ├── background.py              # Background task/process management
│   ├── core.py                    # JarvisAgentCore orchestrator
│   ├── conversation.py            # Multi-turn conversation handling
│   ├── tool_selector.py           # Selects the appropriate tool for a given intent
│   ├── page_context.py            # Screen/page content extraction
│   ├── decision.py                # Decision engine (EXECUTE/CLARIFY/ANSWER/IGNORE)
│   ├── goal_manager.py            # Persistent goal tracking
│   └── intent_registry.py         # Intent metadata (slots, context-free)
├── executor/
│   ├── runner.py                  # Task execution and async LLM streaming
│   ├── validator.py               # URL/action validation, fallback logic
│   ├── researcher.py              # Web research execution
│   ├── parallel.py                # Parallel task execution
│   ├── click_simulator.py         # UI element tagging + click simulation
│   ├── communication_tool.py      # WhatsApp automation
│   └── tools/                     # Dynamic capabilities (App Launcher, Browser, etc.)
├── planner/
│   └── engine.py                  # Plan generation, memory-aware preferences
├── security/
│   └── validator.py               # AST-based code/action safety validation
├── voice/
│   ├── wake_confidence.py         # Wake-word confidence scoring
│   ├── service.py                 # Voice pipeline, wake word, recording, STT
│   ├── cleaner.py                 # Audio/text cleanup post-processing
│   └── intent_engine.py           # Intent classification + dynamic registry
├── main.py                        # Main application entry point & process manager
├── task_orchestrator.py           # High-level task orchestration
├── task_planner.py                # Root-level task planning (top-level entry)
├── tts_engine.py                  # Kokoro Local TTS engine
├── screen_awareness.py            # UIA-based window content extraction
├── reliability_layer.py           # Retry / fallback / error-recovery layer
├── agent_state.py                 # CentralAgentState, CommandRouter, TTSQueue
├── fast_router.py                 # 3-tier local intent classifier
├── ui_bridge.py                   # Full-duplex WebSocket server
├── requirements.txt                # Dependencies
└── README.md                       # Documentation
```

---

## Configuration

All runtime parameters can be tuned to match your hardware and environment:

- **Vision**: `CAM_WIDTH` / `CAM_HEIGHT` – Camera resolution (default: 640×480 for speed).
- **Physics**: `SMOOTHING_BETA` – Trade-off between jitter reduction and latency.
- **Audio**: Adjust `min_speech_energy` in `main.py` to calibrate microphone sensitivity for background noise.

---

## Performance & Optimization

- **Threaded I/O** – Camera capture runs in a dedicated daemon thread, preventing frame drops during heavy CV inference.
- **Async LLM Streaming** – Generative AI responses are chunked by sentence boundaries via RegEx and piped instantly into the TTS audio buffer, dropping response latency to ~300ms.
- **Variable Friction Physics** – Cursor velocity is scaled by a dynamic friction model. As your fingers approach a pinch, speed is dampened (e.g., ~0.3×), making it easier to click small UI elements without drifting.

---

## Known Limitations

- **Lighting** – Requires decent ambient lighting for stable hand tracking.
- **Occlusion** – Tracking may fail if the hand crosses the face or moves out of frame.
- **Audio Noise** – Voice commands can degrade in very noisy environments; adjusting Windows Mic Gain and `min_speech_energy` is recommended.

---

## Roadmap

- [x] Integration of fast LLMs (Groq) for context-aware, semantic commands.
- [x] Beautiful Desktop Overlay UI.
- [ ] Custom offline wake word engine (Porcupine / OpenWakeWord).
- [ ] 3D gesture support with depth-aware interaction.
- [ ] Cross-platform packaging and performance tuning for macOS / Linux.

---

## Contribution Guidelines

Contributions and experiments are welcome.

1. Fork the repository.
2. Create a feature branch:
   ```bash
   git checkout -b feature/AmazingFeature
   ```
3. Commit your changes:
   ```bash
   git commit -m "Add AmazingFeature"
   ```
4. Push to the branch:
   ```bash
   git push origin feature/AmazingFeature
   ```
5. Open a Pull Request.

---

## License

Distributed under the MIT License. See `LICENSE` for details.

---

https://github.com/user-attachments/assets/bef200b3-940e-4128-9dc0-7df4cb3937ab



## Author

**Technospes**

- GitHub: [https://github.com/technospes](https://github.com/technospes)
- LinkedIn (Ayush Shukla): [https://www.linkedin.com/in/ayushshukla-ar/](https://www.linkedin.com/in/ayushshukla-ar/)
