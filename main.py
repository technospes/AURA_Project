"""
JARVIS MAIN v3
==============
Key changes from v2:
  - VoiceService now uses 2-thread architecture (always listening)
  - TTS pre-warmed before listener starts
  - on_command signature updated: (text, tts) → void
  - Persistent agent_loop (no per-call new_event_loop)
"""

import asyncio
import logging
import os
import signal
import sys
import time
from multiprocessing import Process, Value, freeze_support
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("jarvis.main")


def build_config() -> dict:
    from dotenv import load_dotenv
    load_dotenv()
    groq_key = os.getenv("GROQ_API_KEY", "")
    return {
        "groq_api_key": groq_key,
        "voice": {
            "sample_rate": 16000,
            "chunk_size": 3200,
            
            # ── INCREASED SENSITIVITY SETTINGS ──
            "silence_frames": 12,          # Wait longer before cutting you off
            "max_command_duration": 15.0,  # Allow up to 15 seconds of speaking
            "min_speech_energy": 0.002,    # Lowered from 0.012 (6x more sensitive!)
            
            "vosk_model_path": os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15"),
            "groq_api_key": groq_key,
            "use_local_whisper": True,
        },
        "memory":   {"memory_file": "data/jarvis_memory.json"},
        "planner":  {},
        "executor": {"groq_api_key": groq_key},
        "response": {"groq_api_key": groq_key},
        "security": {"max_commands_per_window": 30, "rate_window_seconds": 60},
        "decision": {"min_confidence_execute": 0.40, "min_confidence_clarify": 0.20},
        "thinking": {},
    }


def voice_process_main(system_active: Any, config: dict):
    from agent.core import JarvisAgentCore
    from voice.service import VoiceService

    logger.info("🎤 Voice process starting...")

    # ── TTS first (starts pre-warming in background) ───────────────────────
    tts = None
    try:
        from src.voice_io import JarvisVoice
        tts = JarvisVoice()
        time.sleep(0.3)  # Let pre-warm thread start
        logger.info("✅ TTS ready")
    except Exception as e:
        logger.warning(f"TTS unavailable: {e}")

    # ── Agent ──────────────────────────────────────────────────────────────
    agent = JarvisAgentCore(config)
    if tts:
        agent.set_tts_callback(lambda msg: tts.speak(msg))

    # ── Persistent event loop for agent pipeline ───────────────────────────
    agent_loop = asyncio.new_event_loop()

    def on_command(command_text: str, _tts=None) -> None:
        """Called by VoiceService execution thread for each command."""
        the_tts = _tts or tts
        try:
            turn = agent_loop.run_until_complete(agent.process(command_text))
            spoken = turn.spoken_response
            if spoken and the_tts:
                the_tts.speak(spoken)
            elif spoken:
                print(f"\n[Jarvis] {spoken}\n")
            logger.info(
                f"Turn: '{command_text[:40]}' | "
                f"{'✓' if turn.success else '✗'} | {turn.duration_ms:.0f}ms"
            )

        except asyncio.TimeoutError:
            msg = "Taking too long. Try again."
            logger.warning(f"Timeout: '{command_text}'")
            if the_tts: the_tts.speak(msg)

        except ConnectionError as e:
            msg = "Connection issue. Check your internet."
            logger.error(f"Connection: {e}")
            if the_tts: the_tts.speak(msg)

        except Exception as e:
            logger.error(f"Command failed: '{command_text}' — {e}", exc_info=True)
            err = str(e).lower()
            if "groq" in err or "api" in err or "rate" in err:
                msg = "AI service is busy. Try in a moment."
            elif "process" in err or "app" in err:
                msg = "Couldn't complete that. App may not be installed."
            elif "network" in err or "connect" in err:
                msg = "Network issue. Check your connection."
            else:
                msg = "Ran into an issue. Try a different approach."
            if the_tts: the_tts.speak(msg)

    # ── Start VoiceService (blocks this thread in listen loop) ───────────
    service = VoiceService(config=config["voice"], on_command=on_command, tts=tts)
    try:
        service.start()
    except Exception as e:
        logger.error(f"Voice service crashed: {e}", exc_info=True)
    finally:
        service.stop()
        agent_loop.close()


def vision_process_main(system_active: Any):
    try:
        from src.vision_service import vision_process_loop
        import multiprocessing
        from ctypes import c_char

        class _State:
            def __init__(self, active):
                self.system_active = active
                self.command_queue = multiprocessing.Queue()
                self.active_context = multiprocessing.Array(c_char, 50)
            def get_context(self): return self.active_context.value.decode("utf-8")
            def set_context(self, c): self.active_context.value = c[:49].encode("utf-8")

        vision_process_loop(_State(system_active))
    except ImportError as e:
        logger.warning(f"Vision unavailable: {e}")
    except Exception as e:
        logger.error(f"Vision error: {e}", exc_info=True)


class JarvisSystem:
    def __init__(self, config):
        self.config = config
        self.system_active = Value("b", True)
        self.voice_proc: Optional[Process] = None
        self.vision_proc: Optional[Process] = None
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def start(self, enable_vision=True) -> bool:
        self._banner()
        logger.info("Starting voice process...")
        self.voice_proc = Process(
            target=voice_process_main,
            args=(self.system_active, self.config),
            name="Jarvis_Voice", daemon=False
        )
        self.voice_proc.start()
        time.sleep(3.5)  # TTS pre-warm + Vosk model load

        if not self.voice_proc.is_alive():
            logger.error("Voice process failed to start")
            return False
        logger.info("✅ Voice process running")

        if enable_vision:
            self.vision_proc = Process(
                target=vision_process_main,
                args=(self.system_active,),
                name="Jarvis_Vision", daemon=False
            )
            self.vision_proc.start()
            time.sleep(1.0)
            if self.vision_proc.is_alive():
                logger.info("✅ Vision process running")
            else:
                logger.warning("Vision failed — gesture control disabled")
                self.vision_proc = None
        return True

    def monitor(self):
        logger.info("🟢 Online. Say 'Jarvis' to start. Ctrl+C to exit.\n")
        try:
            while self.system_active.value:
                if self.voice_proc and not self.voice_proc.is_alive():
                    logger.error("Voice process died — restarting...")
                    time.sleep(2.0)
                    self.voice_proc = Process(
                        target=voice_process_main,
                        args=(self.system_active, self.config),
                        name="Jarvis_Voice", daemon=False
                    )
                    self.voice_proc.start()
                    time.sleep(3.5)
                if self.vision_proc and not self.vision_proc.is_alive():
                    logger.warning("Vision died")
                    self.vision_proc = None
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down...")
        self.system_active.value = False
        for proc, name in [(self.voice_proc, "voice"), (self.vision_proc, "vision")]:
            if not proc: continue
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
        logger.info("Goodbye.\n")

    def _shutdown(self, sig, frame):
        self.shutdown()
        sys.exit(0)

    def _banner(self):
        print("\n" + "=" * 56)
        print("   J A R V I S  v3  —  True AI Agent")
        print("=" * 56)
        print("  Wake:      'Jarvis'")
        print("  Interrupt: 'Jarvis, stop'")
        print("  Multi-cmd: 'Jarvis, open YouTube and play Starboy'")
        print("  Ctrl+C:    exit")
        print("=" * 56 + "\n")


def main():
    freeze_support()
    enable_vision = "--no-vision" not in sys.argv
    config = build_config()

    if not config["groq_api_key"]:
        print("\n[FATAL] GROQ_API_KEY not set in .env\n")
        sys.exit(1)

    vosk = config["voice"]["vosk_model_path"]
    if not Path(vosk).exists():
        print(f"\n[FATAL] Vosk model not found: {vosk}")
        print("  Download: https://alphacephei.com/vosk/models")
        print("  Extract to: models/vosk-model-small-en-us-0.15\n")
        sys.exit(1)

    system = JarvisSystem(config)
    if system.start(enable_vision=enable_vision):
        system.monitor()
    else:
        print("\n[FATAL] Startup failed\n")
        import sys as _sys; _sys.exit(1)


if __name__ == "__main__":
    main()
