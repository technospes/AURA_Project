"""
VOICE SERVICE v3 — Truly Non-Blocking Concurrent Architecture
==============================================================
Includes:
- Two-thread Producer/Consumer architecture
- Vosk PartialResult detection for zero-latency wake
- Auto-Listen for conversational flow
- Aggressive Regex cleaning to prevent TTS feedback loops
"""

import asyncio
import concurrent.futures
import io
import json
import logging
import queue
import threading
import time
import wave
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── GLOBAL INTERRUPT FLAG ─────────────────────────────────────────────────
INTERRUPT_FLAG = threading.Event()

def request_interrupt():
    INTERRUPT_FLAG.set()

def clear_interrupt():
    INTERRUPT_FLAG.clear()

# ── WAKE WORD VARIANTS (strict — no false triggers) ───────────────────────
_WAKE_VARIANTS = frozenset([
    "jarvis", "hey jarvis", "ok jarvis", "yo jarvis", "hello jarvis",
    "jarves", "jarvish", "jarviz", "jervis", "jarwis", "garvis",
    "harvis", "jarvi", "jarv",
])

_NOISE_ONLY = frozenset([
    "the", "a", "an", "is", "it", "in", "of", "to", "and", "or",
    "yes", "no", "ok", "okay", "um", "uh", "ah", "mm", "hm", "huh"
])

_INTERRUPT_COMMANDS = frozenset([
    "stop", "cancel", "abort", "quit", "pause", "wait",
    "stop that", "cancel that", "never mind", "forget it",
])

@dataclass
class PendingCommand:
    """A command queued for execution."""
    text: str
    timestamp: float
    is_interrupt: bool = False


class WakeWordDetector:
    def __init__(self, model_path: str, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._recognizer = None
        self._last_trigger = 0.0
        self._cooldown = 1.2
        self._init_vosk(model_path)

    def _init_vosk(self, model_path: str):
        try:
            from vosk import Model, KaldiRecognizer
            model = Model(model_path)
            self._recognizer = KaldiRecognizer(model, self.sample_rate)
            self._recognizer.SetWords(False)
            logger.info(f"✅ Vosk ready: {model_path}")
        except Exception as e:
            logger.error(f"Vosk init failed: {e}")

    def detect(self, audio_chunk: np.ndarray) -> Tuple[bool, str, str]:
        if not self._recognizer: 
            return False, "", ""
        
        # Feed raw audio to Vosk
        is_final = self._recognizer.AcceptWaveform(audio_chunk.tobytes())
        
        # ── THE FIX: Check Partial Results for INSTANT detection ──
        if is_final:
            res = json.loads(self._recognizer.Result())
            text = res.get("text", "")
        else:
            res = json.loads(self._recognizer.PartialResult())
            text = res.get("partial", "")

        text = text.lower().strip()
        if not text or text in _NOISE_ONLY: 
            return False, "", ""
        
        now = time.time()
        if now - self._last_trigger < self._cooldown: 
            return False, "", ""

        for variant in sorted(_WAKE_VARIANTS, key=len, reverse=True):
            if variant in text:
                self._last_trigger = now
                # Reset Vosk buffer so it doesn't double-trigger on the next chunk
                self._recognizer.Reset()
                
                inline = text.replace(variant, "", 1).strip().lstrip(",;:. ")
                if inline in _NOISE_ONLY: 
                    inline = ""
                return True, variant, inline
                
        return False, "", ""


class AudioRecorder:
    def __init__(self, sample_rate=16000, chunk_size=3200, silence_frames=10, max_duration=15.0, min_speech_energy=0.008):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_frames = silence_frames
        self.max_duration = max_duration
        self.min_speech_energy = min_speech_energy
        self._queue = queue.Queue(maxsize=100)
        self._stream = None
        self._bg_noise = 0.008
        self._running = False

    def start_stream(self, device_id=None):
        import sounddevice as sd
        def _cb(indata, frames, time_info, status):
            chunk = indata.copy().flatten()
            try: self._queue.put_nowait(chunk)
            except queue.Full:
                try: self._queue.get_nowait(); self._queue.put_nowait(chunk)
                except queue.Empty: pass
        self._stream = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype=np.int16, blocksize=self.chunk_size, device=device_id, callback=_cb, latency="low")
        self._stream.start()
        self._running = True

    def stop_stream(self):
        if self._stream: self._stream.stop(); self._stream.close()
        self._running = False

    def get_chunk(self, timeout=0.05):
        try: return self._queue.get(timeout=timeout)
        except queue.Empty: return None

    def record_command(self) -> np.ndarray:
        logger.info("🎙 Recording...")
        
        # 1. ACTUALLY wait for the "Yes?" TTS to finish playing (~0.6 seconds)
        time.sleep(0.6) 
        
        # 2. Safely flush the queue of any audio captured while the speaker was playing
        while not self._queue.empty():
            try: self._queue.get_nowait()
            except queue.Empty: break

        buf = []
        silence_count = 0
        start = time.time()

        while True:
            chunk = self.get_chunk(timeout=0.15)
            if chunk is None: continue
            buf.append(chunk)
            energy = self._rms(chunk)
            
            # Very forgiving threshold so he doesn't drop you mid-sentence
            threshold = max(self.min_speech_energy, self._bg_noise * 1.5)

            if energy < threshold: 
                silence_count += 1
            else:
                silence_count = 0
                if not INTERRUPT_FLAG.is_set(): 
                    # Cap the background noise adjustment
                    self._bg_noise = min(0.02, 0.98 * self._bg_noise + 0.02 * energy)

            # 3. Minimum Record Time: Force him to listen for at least 1.5 seconds (len(buf) > 7)
            # This prevents him from cutting you off if you say "Play... [pause] ... Starboy"
            if silence_count >= self.silence_frames and len(buf) > 7: 
                break
                
            if time.time() - start > self.max_duration: 
                break
            if INTERRUPT_FLAG.is_set(): 
                break

        raw_audio = np.concatenate(buf) if buf else np.array([], dtype=np.int16)
        if raw_audio.size > 0:
            return self._agc(raw_audio)
        return raw_audio

    def _agc(self, audio, target=0.15, max_gain=30.0):
        f = audio.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(f ** 2)) + 1e-9)
        if rms > 0.001: f = np.clip(f * min(target / rms, max_gain), -1.0, 1.0)
        return (f * 32768.0).astype(np.int16)

    def _rms(self, audio): return float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))

class WhisperTranscriber:
    """faster-whisper loaded once, reused for all transcriptions."""

    def __init__(self, groq_api_key="", use_local=True):
        self._groq_key = groq_api_key
        self._local_model = None
        self._groq_client = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="whisper"
        )
        if use_local:
            self._load_local()
        if not self._local_model:
            self._load_groq()

    def _load_local(self):
        try:
            from faster_whisper import WhisperModel
            try:
                self._local_model = WhisperModel("small.en", device="cuda", compute_type="float16")
                logger.info("✅ Whisper: CUDA (small.en) loaded")
            except Exception:
                self._local_model = WhisperModel("small.en", device="cpu", compute_type="int8")
                logger.info("✅ Whisper: CPU (small.en) loaded")
        except ImportError:
            logger.info("faster-whisper not installed → Groq API")
        except Exception as e:
            logger.warning(f"Whisper load failed: {e}")

    def _load_groq(self):
        if not self._groq_key:
            return
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=self._groq_key)
            logger.info("✅ Groq Whisper ready")
        except Exception as e:
            logger.error(f"Groq init failed: {e}")

    async def transcribe(self, audio: np.ndarray, sample_rate=16000) -> str:
        if audio.size < 1600:
            return ""
        loop = asyncio.get_event_loop()
        if self._local_model:
            return await loop.run_in_executor(self._executor, self._local, audio, sample_rate)
        elif self._groq_client:
            return await loop.run_in_executor(self._executor, self._groq, audio, sample_rate)
        return ""

    def _local(self, audio, sr):
        try:
            import tempfile, soundfile as sf
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                sf.write(f.name, audio, sr)
                segs, info = self._local_model.transcribe(
                    f.name, language="en", beam_size=1,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 300}
                )
                text = " ".join(s.text.strip() for s in segs)
                logger.info(f"📝 '{text}' (p={info.language_probability:.2f})")
                return text.strip()
        except Exception as e:
            logger.error(f"Local transcription failed: {e}")
            return ""

    def _groq(self, audio, sr):
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(audio.tobytes())
            buf.seek(0)
            result = self._groq_client.audio.transcriptions.create(
                file=("cmd.wav", buf.read()), model="whisper-large-v3-turbo",
                response_format="json", language="en", temperature=0.0
            )
            text = result.text.strip()
            logger.info(f"📝 '{text}'")
            return text
        except Exception as e:
            logger.error(f"Groq transcription failed: {e}")
            return ""


class VoiceService:
    def __init__(self, config: Dict, on_command: Callable[[str, Any], None],
                 tts: Optional[Any] = None):
        self.config = config
        self.on_command = on_command

        self.recorder = AudioRecorder(
            sample_rate=config.get("sample_rate", 16000),
            chunk_size=config.get("chunk_size", 3200),
            silence_frames=config.get("silence_frames", 6),
            max_duration=config.get("max_command_duration", 10.0),
            min_speech_energy=config.get("min_speech_energy", 0.012)
        )

        self.wake_detector = WakeWordDetector(
            model_path=config.get("vosk_model_path", "models/vosk-model-small-en-us-0.15"),
            sample_rate=config.get("sample_rate", 16000)
        )

        self.transcriber = WhisperTranscriber(
            groq_api_key=config.get("groq_api_key", ""),
            use_local=config.get("use_local_whisper", True)
        )

        # ── THE AUTO-LISTEN FLAG ──
        self._followup_event = threading.Event()

        try:
            from voice.cleaner import InputCleaner
            self._cleaner = InputCleaner()
        except ImportError:
            self._cleaner = None

        if tts is not None:
            self._tts = tts
        else:
            try:
                from src.voice_io import JarvisVoice
                self._tts = JarvisVoice()
            except Exception:
                self._tts = None

        self._running = False
        self._command_queue: queue.Queue = queue.Queue(maxsize=10)
        self._exec_loop: Optional[asyncio.AbstractEventLoop] = None
        self._exec_thread: Optional[threading.Thread] = None
        self._executing = threading.Event()

    def trigger_followup(self):
        """Called by main.py to trigger auto-listen for conversational flow."""
        self._followup_event.set()

    def say(self, text: str, priority: bool = False) -> None:
        if self._tts:
            self._tts.speak(text, priority=priority)
        else:
            print(f"[Jarvis] {text}")

    def start(self, device_id: Optional[int] = None):
        self._running = True
        self.recorder.start_stream(device_id=device_id)

        self._exec_loop = asyncio.new_event_loop()
        self._exec_thread = threading.Thread(
            target=self._execution_loop,
            name="jarvis-executor",
            daemon=True
        )
        self._exec_thread.start()
        logger.info("⚙️  Execution loop started")

        logger.info("🎤 Listening for wake word...")
        try:
            self._listen_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        self._running = False
        self.recorder.stop_stream()
        if self._exec_loop and self._exec_loop.is_running():
            self._exec_loop.call_soon_threadsafe(self._exec_loop.stop)
        if self._tts:
            try:
                self._tts.cleanup()
            except Exception:
                pass

    def _listen_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self._running:
            # ── SEAMLESS FOLLOW-UP LOGIC ──
            if self._followup_event.is_set():
                self._followup_event.clear()
                logger.info("🎤 Auto-listening for clarification response...")
                inline_cmd = ""
            else:
                chunk = self.recorder.get_chunk(timeout=0.05)
                if chunk is None:
                    continue

                detected, variant, inline_cmd = self.wake_detector.detect(chunk)
                if not detected:
                    continue

                logger.info(f"🔔 Wake: '{variant}'")
                self.say("Yes?", priority=True)

            if inline_cmd and inline_cmd.strip() in _INTERRUPT_COMMANDS:
                request_interrupt()
                self.say("Stopping.", priority=True)
                continue

            if inline_cmd and len(inline_cmd) > 2:
                logger.info(f"⚡ Inline: '{inline_cmd}'")
                self._enqueue(inline_cmd)
                continue

            audio = self.recorder.record_command()
            if audio.size == 0:
                continue

            text = loop.run_until_complete(
                self.transcriber.transcribe(audio, self.recorder.sample_rate)
            )

            if not text:
                continue

            # ── AGGRESSIVE CLEANING ──
            text = self._strip_acks(text)
            if not text:
                logger.info("Feedback stripped (ignored empty command)")
                continue

            if self._cleaner:
                primary, all_cmds = self._cleaner.process(text)
            else:
                primary = text
                all_cmds = [text]

            if not all_cmds or not all_cmds[0]:
                continue

            if all_cmds[0].strip() in _INTERRUPT_COMMANDS:
                request_interrupt()
                self.say("Stopping.", priority=True)
                continue

            for cmd in all_cmds:
                if cmd:
                    self._enqueue(cmd)
                    logger.info(f"📥 Queued: '{cmd}'")

    def _enqueue(self, text: str):
        cmd = PendingCommand(text=text, timestamp=time.time())
        try:
            self._command_queue.put_nowait(cmd)
        except queue.Full:
            logger.warning("Command queue full — dropping oldest command")
            try:
                self._command_queue.get_nowait()
                self._command_queue.put_nowait(cmd)
            except queue.Empty:
                pass

    def _execution_loop(self):
        asyncio.set_event_loop(self._exec_loop)

        async def _run():
            while self._running or not self._command_queue.empty():
                try:
                    try:
                        # ── 🚨 THE CRITICAL FIX: get_nowait() prevents the 7-minute freeze! ──
                        cmd = self._command_queue.get_nowait()
                    except queue.Empty:
                        # This allows background tasks to run freely!
                        await asyncio.sleep(0.1)
                        continue

                    clear_interrupt()
                    self._executing.set()

                    try:
                        await self._execute_command(cmd)
                    except Exception as e:
                        logger.error(f"Execution error: {e}", exc_info=True)
                        self.say("Something went wrong.")
                    finally:
                        self._executing.clear()

                except Exception as e:
                    logger.error(f"Execution loop error: {e}", exc_info=True)

        self._exec_loop.run_until_complete(_run())

    async def _execute_command(self, cmd: PendingCommand):
        logger.info(f"⚙️  Executing: '{cmd.text}'")
        
        # ── SMART UX ACKNOWLEDGMENT ──
        text_lower = cmd.text.lower()
        if any(w in text_lower for w in ["which", "best", "suggest", "recommend", "research", "how to"]):
            self.say("Let me think...", priority=False)
            
        await self._exec_loop.run_in_executor(
            None,
            self.on_command,
            cmd.text,
            self._tts
        )

    def _strip_acks(self, text: str) -> str:
        """Aggressive Regex to destroy transcription artifacts like 'Yes.'"""
        exact_ignores = {"yes.", "yes", "yes?", "yeah.", "yeah", "playing.", "opening."}
        if text.strip().lower() in exact_ignores:
            return ""
        
        # Strip "Yes.", "Ok,", "Alright!" from the beginning of the command regardless of punctuation
        pattern = r'^(yes|yeah|yep|sure|okay|ok|right|alright|uh huh|mhm)[\s,\.\!\?;]+'
        return re.sub(pattern, '', text.strip(), flags=re.IGNORECASE).strip()