import asyncio
import logging
import os
import signal
import sys
import time
from multiprocessing import Event, Process, Value, freeze_support
from pathlib import Path
from typing import Any, Optional
from browser_detector import patch_core_patch_browser
from datetime import datetime, timezone
from agent.world_model import world
sys.path.insert(0, str(Path(__file__).parent))
from browser_detector import (
    smart_browser_ctrl,    # drop-in for browser_ctrl
    browser_detector,      # for browser name in responses
    validate_plan,         # pre-execution gate (Item #1)
    TOOL_ACTIONS,          # single source of truth
    patch_core_patch_browser,
)
patch_core_patch_browser()
import logging
logging.getLogger(__name__).info(
    f"[Jarvis] Default browser: {browser_detector.display_name} "
    f"(Playwright engine: {browser_detector.playwright_type})"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    datefmt="%H:%M:%S",
    force=True
)
logger = logging.getLogger("jarvis.main")
from ui_bridge import ui_bridge

def build_config() -> dict:
    from dotenv import load_dotenv
    load_dotenv()
    groq_key = os.getenv("GROQ_API_KEY", "")
    return {
        "groq_api_key":       groq_key,
        # Messaging API tokens (optional — falls back to GUI automation)
        "discord_bot_token":  os.getenv("DISCORD_BOT_TOKEN", ""),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "voice": {
            "sample_rate":          16000,
            "chunk_size":           1600,
            "silence_frames":       25,
            "max_command_duration": 30.0,
            "min_speech_energy":    0.003,
            "min_record_secs":      2.5,
            "vosk_model_path":      os.getenv(
                "VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15"
            ),
            "groq_api_key":         groq_key,
            "use_local_whisper":    True,
        },
        "memory":   {"memory_file": "data/jarvis_memory.json"},
        "planner":  {},
        "executor": {"groq_api_key": groq_key},
        "response": {"groq_api_key": groq_key},
        "security": {"max_commands_per_window": 30, "rate_window_seconds": 60},
        "decision": {"min_confidence_execute": 0.40, "min_confidence_clarify": 0.20},
        "thinking": {},
    }


# ════════════════════════════════════════════════════════════════════════════
# VOICE PROCESS
# ════════════════════════════════════════════════════════════════════════════

def voice_process_main(system_active: Any, config: dict, ready_event: Any):
    from agent.core import JarvisAgentCore
    from voice.service import VoiceService
    from agent_state import CentralAgentState, CommandRouter, TTSQueueWorker

    logger.info(" Voice process starting...")

    try:
        from hardware_profile import print_hardware_report
        print_hardware_report()
    except Exception as e:
        logger.warning(f"Hardware profile unavailable: {e}")

    # ── TTS setup ──────────────────────────────────────────────────────────
    tts          = None
    recorder_ref = [None]

    def _on_tts_done():
        if recorder_ref[0] is not None:
            recorder_ref[0].notify_tts_done()
        try:
            from ui_bridge import ui_bridge
            ui_bridge.broadcast("idle", "")
        except Exception:
            pass

    try:
        from tts_engine import LocalTTSEngine
        tts = LocalTTSEngine(voice="am_onyx", speed=1.3, on_done=_on_tts_done)
        logger.info("✅ LocalTTSEngine ready")
    except Exception:
        try:
            from src.voice_io import JarvisVoice
            tts = JarvisVoice()
            logger.info(" JarvisVoice ready")
        except Exception as e:
            logger.warning(f"TTS unavailable: {e}")

    # ── Duplex audio patch (AEC + is_speaking + duck_volume) ───────────────
    aec = None
    interrupt_handler = None
    try:
        from duplex_audio import AcousticEchoCanceller, StreamingInterruptHandler, patch_tts_engine
        from fast_router import fast_router as _fr
        aec = AcousticEchoCanceller(sample_rate=config["voice"]["sample_rate"])
        if tts:
            patch_tts_engine(tts, aec)
        logger.info(" AEC + TTS duplex patches applied")
    except Exception as e:
        logger.warning(f"Duplex audio setup failed (non-fatal): {e}")

    # ── Raw speak function ─────────────────────────────────────────────────
    def _raw_speak(text: str):
        try:
            if tts:
                if hasattr(tts, "speak"):
                    tts.speak(text)
                else:
                    tts(text)
            else:
                print(f"[Jarvis] {text}")
        except Exception as e:
            logger.warning(f"TTS error: {e}")

    # ── Central agent state ────────────────────────────────────────────────
    agent_state = CentralAgentState()

    # ── TTS worker ────────────────────────────────────────────────────────
    tts_worker = TTSQueueWorker(state=agent_state, tts_fn=_raw_speak)

    # ── DATA SYNC (System 2) — contacts + calendar ─────────────────────────
    try:
        from data_sync import data_sync_manager, entity_resolver
        entity_resolver.load_aliases("data/aliases.json")
        n = data_sync_manager.sync_now()
        data_sync_manager.start_background_sync()
        logger.info(f" Data sync ready ({n} contacts)")
        # Inject calendar context into session memory on startup
        _calendar_summary = data_sync_manager.get_event_summary()
        if _calendar_summary:
            logger.info(f"[Calendar] {_calendar_summary[:100]}")
    except Exception as e:
        logger.warning(f"Data sync unavailable (non-fatal): {e}")
        entity_resolver  = None
        data_sync_manager = None

    # ── Agent ──────────────────────────────────────────────────────────────
    agent = JarvisAgentCore(config)

    # ── Patches ────────────────────────────────────────────────────────────
    _patches_applied = False
    try:
        from jarvis_patch import apply_all_patches
        results = apply_all_patches()
        for k, v in results.items():
            logger.info(f"[Patch] {k}: {v}")
        _patches_applied = True
    except Exception as _pe:
        logger.error(f"Patch failed: {_pe}", exc_info=True)

    # ── Session memory ─────────────────────────────────────────────────────
    from session_memory import session as session_memory
    _prof = session_memory.profile
    logger.info(
        f"[Profile] Loaded — name={_prof.name!r} email={_prof.email!r} "
        f"bookmarks={len(_prof.bookmarks)} prefs={len(_prof.preferences)}"
    )

    # ── Agentic orchestrator ───────────────────────────────────────────────
    from task_orchestrator import get_orchestrator
    orchestrator = get_orchestrator(groq_api_key=config["groq_api_key"])

    # ── Inject messaging API tokens into orchestrator ──────────────────────
    try:
        orchestrator._automation._discord_token  = config.get("discord_bot_token", "")
        orchestrator._automation._telegram_token = config.get("telegram_bot_token", "")
    except Exception:
        pass

    # ── Agent event loop ───────────────────────────────────────────────────
    agent_loop = asyncio.new_event_loop()
    agent.task_manager.set_loop(agent_loop)

    def _on_bg_complete(message: str):
        if message:
            agent_state.speak(message)

    agent._on_background_task_complete = _on_bg_complete
    try:
        agent.task_manager.register_bg_complete_hook(_on_bg_complete)
    except AttributeError:
        pass

    agent.set_tts_callback(agent_state.speak)

    # ── VoiceService ───────────────────────────────────────────────────────
    service = VoiceService(
        config=config["voice"],
        on_command=None,
        tts=tts,
    )

    # This fires the millisecond 'Jarvis' is detected, triggering the UI Squeeze
    if hasattr(service, 'wake_detector'):
        def _on_wake():
            ui_bridge.broadcast("listening", "")
        service.wake_detector.on_wake = _on_wake

    if _patches_applied:
        try:
            from jarvis_patch import apply_stt_patch_to_instance
            apply_stt_patch_to_instance(service)
            logger.info(" STT v4 (multi-pass + semantic correction) active")
        except Exception as _se:
            logger.warning(f"STT patch failed: {_se}")

    # ── Follow-up timing fix ───────────────────────────────────────────────
    try:
        from service_patch import patch_trigger_followup
        patch_trigger_followup(service, tts)
        logger.info(" trigger_followup patched (TTS-aware delay)")
    except Exception as _fp:
        logger.warning(f"service_patch failed (non-fatal): {_fp}")

    recorder_ref[0] = service.recorder

    # ── DUPLEX: wire interrupt handler after recorder exists ───────────────
    if aec:
        try:
            from duplex_audio import StreamingInterruptHandler, patch_vosk_partial_callback
            from fast_router import fast_router as _fr
            interrupt_handler = StreamingInterruptHandler(
                tts_engine=tts,
                fast_router=_fr,
                recorder=service.recorder,
                aec=aec,
            )
            patch_vosk_partial_callback(service.wake_detector, interrupt_handler)
            logger.info(" Full-duplex barge-in active (Vosk partial → interrupt handler)")
        except Exception as e:
            logger.warning(f"Interrupt handler setup failed (non-fatal): {e}")

    # ── SCREEN AWARENESS (System 3) ────────────────────────────────────────
    try:
        from screen_awareness import screen_daemon

        def _on_screen_change(ctx):
            try:
                world.update(
                    active_app=ctx.active_window[:80] if ctx.active_window else "desktop",
                    screen_text=ctx.screen_text[:500] if ctx.screen_text else "",
                    screen_source=ctx.source if ctx.source else "none",
                )
            except Exception:
                pass
            # Push screen text into session memory page context
            # so "read this" / "what's on screen" commands work
            if ctx.has_content and ctx.age_seconds < 5:
                try:
                    session_memory.set_page_context(
                        text=ctx.screen_text,
                        url="",
                        title=ctx.active_window,
                    )
                except Exception:
                    pass
            # Also update agent context snapshot
            try:
                agent_state.update_context(
                    active_window=ctx.active_window,
                    screen_text=ctx.screen_text[:500],
                )
            except Exception:
                pass

        screen_daemon.start(on_context_change=_on_screen_change)
        logger.info(" Screen awareness daemon started")
    except Exception as e:
        logger.warning(f"Screen awareness unavailable (non-fatal): {e}")

    # ── FAST ROUTER (System 4) — proper LLM intercept ───────────────────
    # Strategy: stash fast-classified intent in a dict keyed by raw_input.
    # process_patched's intent_engine.understand() checks this dict and returns
    # the pre-built result instantly — zero Groq call for ~80% of commands.
    import threading as _threading
    _fast_intent_store: dict = {}
    _fast_intent_lock  = _threading.Lock()

    try:
        from fast_router import fast_router as _fast_router

        _original_process = agent.process

        async def _fast_routed_process(raw_input: str, audio_features=None):
            fast_result = _fast_router.classify(raw_input)
            if fast_result:
                logger.info(
                    f"[FastRouter]  '{raw_input[:50]}' → {fast_result['intent']} "
                    f"(conf={fast_result['confidence']:.2f}) — skipping Groq"
                )
                with _fast_intent_lock:
                    _fast_intent_store[raw_input] = fast_result
            return await _original_process(raw_input, audio_features)

        agent.process = _fast_routed_process

        # Intercept intent_engine.understand so it returns the stashed result
        # instantly, bypassing the LLM call entirely for fast-routed commands.
        _orig_understand = agent.intent_engine.understand

        async def _patched_understand(text: str, **kwargs):
            with _fast_intent_lock:
                prebuilt = _fast_intent_store.pop(text, None)
            if prebuilt:
                return prebuilt
            return await _orig_understand(text, **kwargs)

        agent.intent_engine.understand = _patched_understand
        logger.info(f" Fast router active — {_fast_router.get_metrics()}")
    except Exception as e:
        logger.warning(f"Fast router injection failed (non-fatal): {e}")
        _fast_router = None


    # ── Build CommandRouter ────────────────────────────────────────────────
    router = CommandRouter(
        state=agent_state,
        agent_core=agent,
        orchestrator=orchestrator,
        agent_loop=agent_loop,
    )

    # ── Inject entity_resolver into router for contact resolution ──────────
    if entity_resolver:
        router._entity_resolver = entity_resolver

    # Lazy-wire advisor
    _original_process_final = agent.process

    async def _tracked_process(raw_input: str, audio_features=None):
        turn = await _original_process_final(raw_input, audio_features)
        if agent.advisor and router._advisor is None:
            router.set_advisor(agent.advisor)
        return turn

    agent.process = _tracked_process

    # ── on_command ────────────────────────────────────────────────────────

    def on_command(command_text: str, _tts_ignored=None) -> Optional[str]:
        logger.info(f"▶ Command: '{command_text}'")
        ui_bridge.broadcast("thinking", command_text[:60])
        t_start = time.perf_counter()

        result = router.route(command_text)

        # ── Push command to history + memory snapshot to UI ──────────
        try:
            _cmd_history.append({
                "text": command_text,
                "intent": result.intent,
                "result": "done" if result.success else "failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if len(_cmd_history) > 10:
                del _cmd_history[0]
            ui_bridge.broadcast_recent_commands(
                [f"{c['intent']}: {c['text'][:50]}" for c in _cmd_history]
            )
        except Exception:
            pass

        try:
            # Send memory snapshot from session_memory profile
            mem_data = {
                "preferences": dict(getattr(session_memory.profile, "preferences", {}) or {}),
                "bookmarks": list(getattr(session_memory.profile, "bookmarks", []) or []),
            }
            ui_bridge.broadcast_memory(mem_data)
        except Exception:
            pass

        try:
            if result.intent == "open_app" and result.success:
                world.update(last_app=command_text.replace("open ", "").strip(),
                            active_app=command_text.replace("open ", "").strip())
            world.update(last_intent=result.intent)
        except Exception:
            pass
        # Record turn in session memory
        try:
            session_memory.add_user_turn(command_text, intent=result.intent)
            if result.spoken_response:
                session_memory.add_assistant_turn(result.spoken_response, success=result.success)
        except Exception:
            pass

        # Auto-trigger follow-up microphone
        if result.requires_followup:
            logger.info(" Triggering auto-follow-up microphone.")
            service.trigger_followup()

        # Signal background task state
        from agent.core import AgentState as _AgentState
        if agent.state == _AgentState.IDLE_WITH_BG_TASK:
            return "[BACKGROUND_TASK_STARTED]"

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(f"⏱ '{command_text[:40]}' total={total_ms:.0f}ms | {'' if result.success else ''}")
        return None

    _cmd_history: list = []
    service.on_command = on_command
    agent.task_manager.register_bg_complete_hook(service.vc.mark_bg_task_done)

    # 🟢 NEW: Define what happens when a UI button is clicked
    def _handle_ui_command(command: str, payload: dict):
        logger.info(f"[UI Command Received] Action: {command} | Payload: {payload}")
        if command == "reboot_audio":
            logger.info("Restarting audio engine...")
            # Add your audio reset logic here
        elif command == "toggle_startup":
            enabled = payload.get("enabled", False)
            logger.info(f"Windows Startup set to: {enabled}")
                # Add your registry/startup logic here
        elif command == "trigger_sync":
            logger.info("Manual memory sync triggered via UI.")
                
    # Attach the listener to the bridge
    from ui_bridge import ui_bridge
    ui_bridge.on_command_callback = _handle_ui_command
        
    # ── START UI BRIDGE ───────────────────────────────────────────────
    ui_bridge.start()

    logger.info(" UI WebSocket bridge started inside Voice Process")

    # 🟢 INSTANT WAKE WORD HOOK (Triggers the Squeeze)
    if hasattr(service, 'wake_detector'):
        _orig_on_wake = getattr(service.wake_detector, 'on_wake', None)
        def _patched_on_wake(*args, **kwargs):
            ui_bridge.broadcast("listening", "")  # SQUEEZE!
            if _orig_on_wake:
                return _orig_on_wake(*args, **kwargs)
        service.wake_detector.on_wake = _patched_on_wake
        
    # Fallback hook for STT recording start (just to be safe)
    if hasattr(service, 'recorder'):
        _orig_start = getattr(service.recorder, 'start_recording', None)
        if _orig_start:
            def _patched_start(*args, **kwargs):
                ui_bridge.broadcast("listening", "")
                return _orig_start(*args, **kwargs)
            service.recorder.start_recording = _patched_start

    ready_event.set()
    logger.info(" Voice process ready — Jarvis v9 (Siri-level systems active)")

    try:
        import threading

        def _run_loop():
            asyncio.set_event_loop(agent_loop)
            agent_loop.run_forever()

        loop_thread = threading.Thread(target=_run_loop, daemon=True, name="agent-loop")
        loop_thread.start()

        service.start()
    except Exception as e:
        logger.error(f"Voice service crashed: {e}", exc_info=True)
    finally:
        service.stop()
        tts_worker.stop()
        if screen_daemon:
            try:
                from screen_awareness import screen_daemon as _sd
                _sd.stop()
            except Exception:
                pass
        agent_loop.call_soon_threadsafe(agent_loop.stop)


# ════════════════════════════════════════════════════════════════════════════
# VISION PROCESS
# ════════════════════════════════════════════════════════════════════════════

def vision_process_main(system_active: Any):
    try:
        from src.vision_service import vision_process_loop
        import multiprocessing
        from ctypes import c_char

        class _State:
            def __init__(self, active):
                self.system_active  = active
                self.command_queue  = multiprocessing.Queue()
                self.active_context = multiprocessing.Array(c_char, 50)

            def get_context(self):
                return self.active_context.value.decode("utf-8")

            def set_context(self, c):
                self.active_context.value = c[:49].encode("utf-8")

        vision_process_loop(_State(system_active))
    except ImportError as e:
        logger.warning(f"Vision unavailable: {e}")
    except Exception as e:
        logger.error(f"Vision error: {e}", exc_info=True)


# ════════════════════════════════════════════════════════════════════════════
# JARVIS SYSTEM
# ════════════════════════════════════════════════════════════════════════════

class JarvisSystem:
    VOICE_READY_TIMEOUT = 50.0

    def __init__(self, config: dict):
        self.config        = config
        self.system_active = Value("b", True)
        self.voice_proc: Optional[Process] = None
        self.vision_proc: Optional[Process] = None
        self._ready_event  = Event()
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def start(self, enable_vision: bool = True) -> bool:
        self._banner()
        self._ready_event.clear()

        self.voice_proc = Process(
            target=voice_process_main,
            args=(self.system_active, self.config, self._ready_event),
            name="Jarvis_Voice", daemon=False,
        )
        self.voice_proc.start()

        signalled = self._ready_event.wait(timeout=self.VOICE_READY_TIMEOUT)
        if not signalled or not self.voice_proc.is_alive():
            logger.error("Voice process failed to start")
            return False

        logger.info(" Voice process running")

        if enable_vision:
            self.vision_proc = Process(
                target=vision_process_main,
                args=(self.system_active,),
                name="Jarvis_Vision", daemon=False,
            )
            self.vision_proc.start()
            time.sleep(1.0)
            if self.vision_proc.is_alive():
                logger.info(" Vision process running")
            else:
                self.vision_proc = None

        return True

    def monitor(self):
        logger.info("🟢 Online. Say 'Jarvis' to start. Ctrl+C to exit.\n")
        try:
            while self.system_active.value:
                if self.voice_proc and not self.voice_proc.is_alive():
                    logger.error("Voice process died — restarting…")
                    self._ready_event.clear()
                    self.voice_proc = Process(
                        target=voice_process_main,
                        args=(self.system_active, self.config, self._ready_event),
                        name="Jarvis_Voice", daemon=False,
                    )
                    self.voice_proc.start()
                    self._ready_event.wait(timeout=self.VOICE_READY_TIMEOUT)

                if self.vision_proc and not self.vision_proc.is_alive():
                    self.vision_proc = None

                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down…")
        self.system_active.value = False
        for proc in (self.voice_proc, self.vision_proc):
            if not proc:
                continue
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
        print("\n" + "═" * 62)
        print("   J A R V I S  v9  —  Siri-Level Runtime")
        print("═" * 62)
        print("   Fast Router  : 80% commands bypass LLM (<1ms)")
        print("   Data Sync    : Outlook/Graph contacts + calendar")
        print("   Screen Aware : UIA reads any active window")
        print("   Full Duplex  : Say 'stop' to interrupt speech")
        print("   API Messages : Discord/Telegram without GUI")
        print("─" * 62)
        print("  Wake word  : 'Jarvis'")
        print("  Exit       : Ctrl+C")
        print("═" * 62 + "\n")


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def main():
    freeze_support()
    enable_vision = "--no-vision" not in sys.argv
    config        = build_config()

    if not config["groq_api_key"]:
        print("\n[FATAL] GROQ_API_KEY not set in .env\n")
        sys.exit(1)

    vosk = config["voice"]["vosk_model_path"]
    if not Path(vosk).exists():
        print(f"\n[FATAL] Vosk model not found: {vosk}")
        sys.exit(1)

    system = JarvisSystem(config)
    if system.start(enable_vision=enable_vision):
        system.monitor()
    else:
        print("\n[FATAL] Startup failed\n")
        sys.exit(1)


if __name__ == "__main__":
    main()