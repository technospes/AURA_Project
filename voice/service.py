"""
VOICE SERVICE v9 — Fixed STT Pipeline + Correct Defaults
=========================================================
Changes from v8:
  1. silence_frames default: 8 → 25 (5 seconds before cut)
  2. min_record_secs default: 0.5 → 2.5 (record at least 2.5s)
  3. min_speech_energy: 0.006 → 0.003 (catch quieter voices)
  4. chunk_size: 3200 → 1600 (finer-grained silence detection)
  5. STT stability gate: DISABLED for now (rely on transcriber accuracy)
     Will re-enable once STT is confirmed working.
  6. normalize_command: permissive mode — pass everything ≥ 2 words
     that has any alphabetic content.

ROOT CAUSE SUMMARY:
  The previous version had a cascade of over-aggressive filters:
  a) VAD in Whisper cutting 50-80% of speech (fixed in stt_patch.py)
  b) silence_frames=8 cutting recordings too early (fixed here)
  c) STT gate rejecting valid short commands (relaxed here)
  d) normalize_command dropping valid sentences (fixed here)
"""

import asyncio
import concurrent.futures
import io
import json
import logging
import math
import queue
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum
from jarvis_patch.stt_patch import tempfile
import numpy as np

logger = logging.getLogger(__name__)

# ── GLOBAL INTERRUPT FLAG ─────────────────────────────────────────────────
INTERRUPT_FLAG = threading.Event()


def request_interrupt():
    INTERRUPT_FLAG.set()


def clear_interrupt():
    INTERRUPT_FLAG.clear()


# ════════════════════════════════════════════════════════════════════════════
# STT GATE — permissive version
# ════════════════════════════════════════════════════════════════════════════

# Only the most obvious garbage from Whisper on silent audio
_HARD_GARBAGE = frozenset({
    "", " ", ".", "..", "...",
    "thank you for watching.",
    "thanks for watching.",
    "subtitles by the amara.org community",
    "like and subscribe",
    "subscribe",
    "[music]", "[silence]",
})

# Single words that are clearly not commands and clearly Whisper artifacts
_ARTIFACT_SINGLES = frozenset({
    "hm", "hmm", "um", "uh", "ah", "oh",
    "bye", "goodbye",
})


class STTStabilityLayer:
    """
    PERMISSIVE gate — only reject obvious garbage.

    The goal is to NOT drop valid commands.
    We'd rather pass a noisy transcription than reject a real command.
    The agent's intent engine handles ambiguous input gracefully.
    """

    @classmethod
    def accept(cls, text: str, confidence: float = 1.0) -> bool:
        if not text or not text.strip():
            return False

        stripped = text.strip(" .,!?;:").lower()

        # Empty after stripping
        if not stripped:
            return False

        # Known Whisper garbage on silent audio
        if stripped in _HARD_GARBAGE:
            return False

        words = stripped.split()

        # Single-word artifact check (only obvious filler)
        if len(words) == 1 and words[0] in _ARTIFACT_SINGLES:
            return False

        # Has any alphabetic content → accept
        if any(c.isalpha() for c in stripped):
            return True

        # Only punctuation/numbers
        return False

    @classmethod
    def on_audio_chunk(cls):
        pass  # Hook preserved for compatibility


stt_stability = STTStabilityLayer()


# ── VOICE STATE MACHINE ────────────────────────────────────────────────────

class VoiceState(Enum):
    IDLE          = "idle"
    WAKE_DETECTED = "wake_detected"
    RECORDING     = "recording"
    TRANSCRIBING  = "transcribing"
    PROCESSING    = "processing"
    SPEAKING      = "speaking"
    BACKGROUND    = "background"
    ERROR         = "error"


_WATCHDOG_TIMEOUTS: Dict[str, float] = {
    VoiceState.WAKE_DETECTED.value: 5.0,
    VoiceState.RECORDING.value:    35.0,   # Increased — longer commands need more time
    VoiceState.TRANSCRIBING.value: 20.0,   # small.en takes longer than distil-small.en
    VoiceState.PROCESSING.value:   60.0,
    VoiceState.SPEAKING.value:     30.0,
    VoiceState.ERROR.value:         3.0,
}


class VoiceController:
    def __init__(self):
        self._state      = VoiceState.IDLE
        self._changed_at = time.time()
        self._lock       = threading.Lock()

    @property
    def state(self) -> VoiceState:
        return self._state

    def set_state(self, new_state: VoiceState) -> None:
        with self._lock:
            old = self._state.value
            self._state      = new_state
            self._changed_at = time.time()
        if old != new_state.value:
            logger.debug(f"[STATE] {old} → {new_state.value}")
        
        # ── Broadcast state to UI ──────────────────────────────────────
        try:
            from ui_bridge import ui_bridge
            state_map = {
                VoiceState.IDLE: "idle",
                VoiceState.WAKE_DETECTED: "listening",
                VoiceState.RECORDING: "listening",
                VoiceState.TRANSCRIBING: "thinking",
                VoiceState.PROCESSING: "thinking",
                VoiceState.SPEAKING: "speaking",
                VoiceState.BACKGROUND: "idle",
                VoiceState.ERROR: "error",
            }
            ui_state = state_map.get(new_state, "idle")
            ui_bridge.broadcast(ui_state, "")
        except Exception:
            pass

    def time_in_state(self) -> float:
        return time.time() - self._changed_at

    def watchdog_check(self) -> bool:
        timeout = _WATCHDOG_TIMEOUTS.get(self._state.value)
        if timeout and self.time_in_state() > timeout:
            logger.warning(
                f"[WATCHDOG] '{self._state.value}' stuck "
                f"{self.time_in_state():.1f}s > {timeout}s — resetting"
            )
            self.set_state(VoiceState.IDLE)
            return True
        return False

    def mark_bg_task_done(self, *args, **kwargs):
        if self.state == VoiceState.BACKGROUND:
            self.set_state(VoiceState.IDLE)


# ── WAKE WORD VARIANTS ────────────────────────────────────────────────────
_WAKE_VARIANTS = frozenset([
    "jarvis", "hey jarvis", "ok jarvis", "yo jarvis", "hello jarvis",
    "jarves", "jarvish", "jarviz", "jervis", "jarwis", "garvis",
    "harvis", "jarvi", "jarv", "jarvus", "jarvas", "jarvez", "jarvy",
])

_NOISE_ONLY = frozenset([
    "the", "a", "an", "is", "it", "in", "of", "to", "and", "or",
    "yes", "no", "ok", "okay", "um", "uh", "ah", "mm", "hm", "huh",
])

_INTERRUPT_COMMANDS = frozenset([
    "stop", "cancel", "abort", "quit",
    "stop that", "cancel that", "never mind", "forget it",
    "jarvis stop", "jarvis cancel",
])

_POST_WAKE_DRAIN_SEC = 0.05  # Reduced from 0.08 to start recording sooner


# ── LATENCY TRACKER ───────────────────────────────────────────────────────

class LatencyTracker:
    def __init__(self):
        self.marks      = {}
        self.start_time = time.perf_counter()

    def mark(self, name: str):
        self.marks[name] = time.perf_counter()

    def delta(self, a: str, b: str) -> float:
        if a in self.marks and b in self.marks:
            return max(0.0, (self.marks[b] - self.marks[a]) * 1000)
        return 0.0

    def summary(self, text: str) -> str:
        t_stt = self.delta("wake_end", "stt_end")
        t_ex  = self.delta("exec_start", "exec_end")
        t_tot = max(0.0, (time.perf_counter() - self.start_time) * 1000)
        return (
            f"⏱ '{text[:35]}' | "
            f"STT={t_stt:.0f}ms  exec={t_ex:.0f}ms  total={t_tot:.0f}ms"
        )


@dataclass
class PendingCommand:
    text:         str
    timestamp:    float
    tracker:      LatencyTracker = field(default_factory=LatencyTracker)
    is_interrupt: bool = False
    confidence:   float = 1.0


# ── WAKE WORD DETECTOR ────────────────────────────────────────────────────

class WakeWordDetector:
    def __init__(self, model_path: str, sample_rate: int = 16000):
        self.sample_rate   = sample_rate
        self._recognizer   = None
        self._last_trigger = 0.0
        self._cooldown     = 1.2
        self._init_vosk(model_path)

    def _init_vosk(self, model_path: str):
        try:
            from vosk import Model, KaldiRecognizer
            model = Model(model_path)
            self._recognizer = KaldiRecognizer(model, self.sample_rate)
            self._recognizer.SetWords(False)
            logger.info(f" Vosk ready: {model_path}")
        except Exception as e:
            logger.error(f"Vosk init failed: {e}")

    def detect(
        self,
        audio_chunk: np.ndarray,
        is_speaking: bool = False,
    ) -> Tuple[bool, str, str]:
        if not self._recognizer:
            return False, "", ""
        if not self._recognizer.AcceptWaveform(audio_chunk.tobytes()):
            return False, "", ""
        res  = json.loads(self._recognizer.Result())
        text = res.get("text", "").lower().strip()
        if not text or text in _NOISE_ONLY:
            return False, "", ""

        now = time.time()
        if now - self._last_trigger < self._cooldown:
            return False, "", ""

        for variant in sorted(_WAKE_VARIANTS, key=len, reverse=True):
            if variant in text:
                self._last_trigger = now
                self._recognizer.Reset()
                inline = text.replace(variant, "", 1).strip().lstrip(",;:. ")
                if inline in _NOISE_ONLY:
                    inline = ""
                return True, variant, inline

        return False, "", ""


# ── AUDIO RECORDER ────────────────────────────────────────────────────────

class AudioRecorder:
    """
    Records voice commands.

    KEY FIXES vs previous version:
      silence_frames: 8 → 25   (BUG D FIX — don't cut too early)
      min_record_secs: 0.5 → 2.5 (record at least 2.5 seconds)
      min_speech_energy: 0.006 → 0.003 (catch quieter speakers)
    """

    def __init__(
        self,
        sample_rate:       int   = 16000,
        chunk_size:        int   = 1600,
        silence_frames:    int   = 25,      # BUG D FIX: was 8, now 25 (5 seconds)
        max_duration:      float = 30.0,
        min_speech_energy: float = 0.003,   # Lower threshold — catch quieter voices
        min_record_secs:   float = 2.5,     # BUG D FIX: was 0.5, now 2.5s minimum
    ):
        self.sample_rate       = sample_rate
        self.chunk_size        = chunk_size
        self.silence_frames    = silence_frames
        self.max_duration      = max_duration
        self.min_speech_energy = min_speech_energy
        self.min_record_secs   = min_record_secs
        self._chunk_sec        = chunk_size / sample_rate
        self._queue: queue.Queue = queue.Queue(maxsize=500)  # Larger buffer
        self._stream           = None
        self._bg_noise         = 0.003
        self._running          = False
        self.tts_done_at       = 0.0

    def notify_tts_done(self):
        self.tts_done_at = time.time()

    def drain_post_wake(self, max_wait: float = 0.05):
        """Drain any audio that was captured during the wake word itself."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                chunk = self._queue.get(timeout=0.01)
            except queue.Empty:
                break
            energy = self._rms(chunk)
            # If we hit actual speech, put it back and stop draining
            if energy > self.min_speech_energy * 3.0:
                self._queue.put(chunk)
                break

    def start_stream(self, device_id: Optional[int] = None):
        import sounddevice as sd

        def _cb(indata, frames, time_info, status):
            chunk = indata.copy().flatten()
            stt_stability.on_audio_chunk()
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(chunk)
                except queue.Empty:
                    pass

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype=np.int16,
            blocksize=self.chunk_size,
            device=device_id,
            callback=_cb,
            latency="low",
        )
        self._stream.start()
        self._running = True

    def stop_stream(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
        self._running = False

    def get_chunk(self, timeout: float = 0.05) -> Optional[np.ndarray]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def record_command(self, tracker: Optional[LatencyTracker] = None) -> np.ndarray:
        """
        Record a voice command.
        Uses patched version if pre-roll buffer is installed (via stt_patch).
        Falls back to this implementation otherwise.
        """
        # TTS echo drain
        if self.tts_done_at > 0:
            elapsed = time.time() - self.tts_done_at
            drain   = max(0.10 - elapsed, 0.0)
            if drain > 0.05:
                time.sleep(min(drain, 0.2))
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break

        logger.info(" Recording...")
        if tracker:
            tracker.mark("record_start")

        buf: list = []
        threshold     = max(self.min_speech_energy, self._bg_noise * 1.5)
        speech_frames = 0
        onset_buf     = []

        # Wait for speech onset
        while True:
            if INTERRUPT_FLAG.is_set():
                return np.array([], dtype=np.int16)
            chunk = self.get_chunk(timeout=0.1)
            if chunk is None:
                continue
            energy = self._rms(chunk)
            if energy >= threshold:
                speech_frames += 1
                onset_buf.append(chunk)
                if speech_frames >= 2:
                    buf.extend(onset_buf)
                    self._bg_noise = min(0.02, 0.97 * self._bg_noise + 0.03 * energy)
                    break
            else:
                speech_frames = 0
                onset_buf.clear()

        # Main recording loop
        silence_count = 0
        start         = time.time()
        min_chunks    = int(self.min_record_secs / self._chunk_sec)

        while True:
            chunk = self.get_chunk(timeout=0.1)
            if chunk is None:
                continue
            buf.append(chunk)
            energy    = self._rms(chunk)
            threshold = max(self.min_speech_energy, self._bg_noise * 1.4)
            elapsed   = time.time() - start

            if energy >= threshold:
                silence_count = 0
                self._bg_noise = min(0.02, 0.97 * self._bg_noise + 0.03 * energy)
            else:
                silence_count += 1

            # BUG D FIX: silence_frames is now 25 (from constructor default / config)
            if silence_count >= self.silence_frames:
                # Continuation window: 1.5s
                wait_start = time.time()
                extra      = []
                resumed    = False
                while time.time() - wait_start < 1.5:
                    c = self.get_chunk(timeout=0.05)
                    if c is not None:
                        extra.append(c)
                        if self._rms(c) >= threshold:
                            silence_count = 0
                            buf.extend(extra)
                            resumed = True
                            break
                if not resumed:
                    buf.extend(extra)
                    if len(buf) >= min_chunks:
                        break
                    else:
                        silence_count = 0  # Not enough — keep going

            if elapsed >= self.max_duration:
                logger.warning(f"[Recorder] Max {self.max_duration}s reached")
                break
            if INTERRUPT_FLAG.is_set():
                break

        raw = np.concatenate(buf) if buf else np.array([], dtype=np.int16)
        return self._agc(raw) if raw.size > 0 else raw

    def _agc(self, audio: np.ndarray, target: float = 0.15, max_gain: float = 20.0) -> np.ndarray:
        f   = audio.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(f ** 2)) + 1e-9)
        if rms > 0.001:
            f = np.clip(f * min(target / rms, max_gain), -1.0, 1.0)
        return (f * 32768.0).astype(np.int16)

    def _rms(self, audio: np.ndarray) -> float:
        return float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))


# ── COMMAND NORMALIZER ────────────────────────────────────────────────────

def normalize_command(text: str, source: str = "stt", is_followup: bool = False) -> str:
    """
    PERMISSIVE normalization — pass most things through.

    The STT model (small.en) is much better than distil-small.en.
    The agent's intent engine handles ambiguous phrasing gracefully.
    We only filter absolute garbage here.
    """
    text = text.strip()
    if not text:
        return ""

    # Strip Jarvis prefix if present
    jarvis_re = re.compile(r'^(?:hey\s+|ok\s+|okay\s+)?jarvis\s*[,.]?\s*', re.I)
    text = jarvis_re.sub("", text).strip()
    if not text:
        return ""

    text_lower = text.lower().strip(" .,!?;:")
    words      = text_lower.split()

    if is_followup:
        # Very permissive in followup mode — user is answering a question
        exps = {"wanna": "want to", "gonna": "going to", "lemme": "let me"}
        for art, exp in exps.items():
            text = re.sub(r'\b' + art + r'\b', exp, text, flags=re.IGNORECASE)
        return text.strip()

    # Drop empty/single-char
    if len(words) == 0:
        return ""

    # Drop known pure filler
    _PURE_FILLER = {
        "um", "uh", "ah", "hmm", "hm", "mm", "oh",
        "ok", "okay",
    }
    if len(words) == 1 and words[0] in _PURE_FILLER:
        return ""

    # Apply word expansions
    exps = {
        "wanna": "want to", "gonna": "going to",
        "gotta": "got to",  "lemme": "let me",
        "gimme": "give me",
    }
    for art, exp in exps.items():
        text = re.sub(r'\b' + art + r'\b', exp, text, flags=re.IGNORECASE)

    return text.strip()


# ── BUILT-IN WHISPER TRANSCRIBER (fallback if patch not applied) ──────────

class WhisperTranscriber:
    """
    Basic Whisper transcriber.
    Normally replaced by EnhancedTranscriber from stt_patch.py.
    """

    def __init__(self, groq_api_key: str = "", use_local: bool = True):
        self._groq_key    = groq_api_key
        self._local_model = None
        self._groq_client = None
        self._model_name  = "unknown"
        self._executor    = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="whisper"
        )
        if use_local:
            self._load_local()
        if not self._local_model:
            self._load_groq()

    def _load_local(self):
        try:
            from faster_whisper import WhisperModel
            import torch
            device   = "cuda" if torch.cuda.is_available() else "cpu"
            model_id = "small.en"
            logger.info(f"[STT-builtin] Loading {model_id} on {device}...")
            self._local_model = WhisperModel(model_id, device=device, compute_type="int8")
            self._model_name  = f"{model_id}@{device}"
            logger.info(f"[STT-builtin]  {self._model_name}")
        except Exception as e:
            logger.warning(f"[STT-builtin] Load failed: {e}")

    def _load_groq(self):
        if not self._groq_key:
            return
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=self._groq_key)
            self._model_name  = "groq/whisper-large-v3-turbo"
        except Exception:
            pass

    async def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> Tuple[str, float]:
        if audio.size < 800:
            return "", 0.0
        loop = asyncio.get_event_loop()
        if self._local_model:
            return await loop.run_in_executor(
                self._executor, self._local_sync, audio, sample_rate
            )
        if self._groq_client:
            return await loop.run_in_executor(
                self._executor, self._groq_sync, audio, sample_rate
            )
        return "", 0.0

    def _local_sync(self, audio: np.ndarray, sr: int) -> Tuple[str, float]:
        try:
            import soundfile as sf
            import os
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            sf.write(tmp.name, audio, sr)
            tmp.close()
            try:
                from jarvis_patch.stt_patch import WHISPER_CONTEXT_PROMPT
                prompt = WHISPER_CONTEXT_PROMPT
            except ImportError:
                prompt = "Voice command to AI assistant."

            segs, _ = self._local_model.transcribe(
                tmp.name,
                language="en",
                beam_size=5,
                temperature=0.0,
                condition_on_previous_text=True,
                initial_prompt=prompt,
                vad_filter=False,          # BUG A FIX
                no_speech_threshold=0.8,
                word_timestamps=False,
            )
            seg_list = list(segs)
            text     = " ".join(s.text.strip() for s in seg_list).strip()
            total_lp = sum(getattr(s, 'avg_logprob', -0.5) for s in seg_list)
            conf     = max(0.0, min(1.0, math.exp(total_lp / max(len(seg_list), 1))))
            os.unlink(tmp.name)
            logger.info(f" '{text[:60]}' (conf={conf:.2f} | {self._model_name})")
            return text, conf
        except Exception as e:
            logger.error(f"[STT-builtin] Error: {e}")
            return "", 0.0

    def _groq_sync(self, audio: np.ndarray, sr: int) -> Tuple[str, float]:
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(audio.tobytes())
            buf.seek(0)
            result = self._groq_client.audio.transcriptions.create(
                file=("cmd.wav", buf.read()),
                model="whisper-large-v3-turbo",
                response_format="json",
                language="en",
                temperature=0.0,
            )
            text = result.text.strip()
            logger.info(f" '{text[:60]}' (Groq)")
            return text, 0.85
        except Exception as e:
            logger.error(f"[STT-builtin] Groq error: {e}")
            return "", 0.0


# ── VOICE SERVICE ─────────────────────────────────────────────────────────

class VoiceService:
    """
    Main voice orchestration service.

    Config defaults now set for production reliability:
      silence_frames:    25   (5s silence tolerance)
      min_record_secs:   2.5  (minimum 2.5s recording)
      min_speech_energy: 0.003 (catches quieter voices)
    """

    def __init__(
        self,
        config:     Dict,
        on_command: Callable[[str, Any], None],
        tts:        Optional[Any] = None,
    ):
        self.config     = config
        self.on_command = on_command

        self.recorder = AudioRecorder(
            sample_rate=config.get("sample_rate", 16000),
            chunk_size=config.get("chunk_size", 1600),
            silence_frames=config.get("silence_frames", 25),       # BUG D FIX
            max_duration=config.get("max_command_duration", 30.0),
            min_speech_energy=config.get("min_speech_energy", 0.003),
            min_record_secs=config.get("min_record_secs", 2.5),    # BUG D FIX
        )

        self.wake_detector = WakeWordDetector(
            model_path=config.get("vosk_model_path", "models/vosk-model-small-en-us-0.15"),
            sample_rate=config.get("sample_rate", 16000),
        )

        # Try EnhancedTranscriber from patch first
        try:
            from jarvis_patch.stt_patch import EnhancedTranscriber
            self.transcriber = EnhancedTranscriber(
                groq_api_key=config.get("groq_api_key", ""),
                use_local=config.get("use_local_whisper", True),
            )
            logger.info("[VoiceService] Using EnhancedTranscriber v3")
        except ImportError:
            self.transcriber = WhisperTranscriber(
                groq_api_key=config.get("groq_api_key", ""),
                use_local=config.get("use_local_whisper", True),
            )
            logger.info("[VoiceService] Using built-in WhisperTranscriber")

        self._tts = tts or self._init_tts()

        self._running         = False
        self._command_queue: queue.Queue = queue.Queue(maxsize=10)
        self._exec_loop: Optional[asyncio.AbstractEventLoop] = None
        self._exec_thread: Optional[threading.Thread] = None
        self._executing       = threading.Event()
        self._followup_event  = threading.Event()

        self.vc           = VoiceController()
        self._is_speaking = threading.Event()

        self._cmd_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-cmd"
        )
        self._stt_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="STT-Worker"
        )
        self._stt_busy = threading.Event()

    def _init_tts(self):
        try:
            from tts_engine import LocalTTSEngine
            engine = LocalTTSEngine(on_done=self.recorder.notify_tts_done)
            logger.info("[VoiceService] Using LocalTTSEngine")
            return engine
        except Exception:
            pass
        try:
            from src.voice_io import JarvisVoice
            return JarvisVoice()
        except Exception as e:
            logger.warning(f"TTS unavailable: {e}")
            return None

    # ── PUBLIC API ────────────────────────────────────────────────────────

    def trigger_followup(self):
        if not self._followup_event.is_set():
            self._followup_event.set()
            logger.info(" Microphone opened for follow-up.")

    def say(self, text: str, priority: bool = False, wait: bool = False) -> None:
        if not text:
            return

        def _speak():
            try:
                if self._tts:
                    if hasattr(self._tts, "speak"):
                        self._tts.speak(text, priority=priority)
                    else:
                        self._tts(text)
                else:
                    print(f"[Jarvis] {text}")
            except Exception as e:
                logger.warning(f"say() error: {e}")
            finally:
                self.recorder.notify_tts_done()

        if wait:
            _speak()
        else:
            threading.Thread(target=_speak, daemon=True, name="jarvis-say").start()

    def start(self, device_id: Optional[int] = None):
        self._running = True
        self.recorder.start_stream(device_id=device_id)

        self._exec_loop   = asyncio.new_event_loop()
        self._exec_thread = threading.Thread(
            target=self._execution_loop, name="jarvis-executor", daemon=True
        )
        self._exec_thread.start()
        logger.info("️  Execution loop started")
        logger.info(" Listening for wake word...")

        try:
            self._listen_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        self._running = False
        self.recorder.stop_stream()
        self._cmd_executor.shutdown(wait=False)
        self._stt_executor.shutdown(wait=False)
        if self._exec_loop and self._exec_loop.is_running():
            self._exec_loop.call_soon_threadsafe(self._exec_loop.stop)
        if self._tts and hasattr(self._tts, "cleanup"):
            try:
                self._tts.cleanup()
            except Exception:
                pass

    # ── LISTEN LOOP ───────────────────────────────────────────────────────

    def _listen_loop(self):
        listen_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(listen_loop)

        while self._running:
            self.vc.watchdog_check()
            current = self.vc.state

            # Followup auto-mic
            if self._followup_event.is_set() and current in (
                VoiceState.IDLE, VoiceState.BACKGROUND
            ):
                self._followup_event.clear()
                logger.info(" Auto-listen: follow-up mode")
                self.vc.set_state(VoiceState.RECORDING)
                tracker = LatencyTracker()
                tracker.mark("wake_end")
                self._stt_executor.submit(
                    self._record_transcribe_and_handle, tracker, True
                )
                continue

            if current in (VoiceState.IDLE, VoiceState.BACKGROUND):
                chunk = self.recorder.get_chunk(timeout=0.05)
                if chunk is None:
                    continue

                detected, variant, inline_cmd = self.wake_detector.detect(
                    chunk, is_speaking=self._is_speaking.is_set()
                )
                if not detected:
                    continue

                logger.info(f" Wake: '{variant}'")
                self.vc.set_state(VoiceState.WAKE_DETECTED)

                # ── Notify UI: Jarvis is listening ─────────────────────
                try:
                    from ui_bridge import ui_bridge
                    ui_bridge.broadcast("listening", "")
                except Exception:
                    pass

                if self._is_speaking.is_set():
                    request_interrupt()
                    if self._tts and hasattr(self._tts, "stop"):
                        self._tts.stop()
                    logger.info(" Barge-in: TTS interrupted")
                    self._is_speaking.clear()
                    time.sleep(0.05)
                    clear_interrupt()

                self.recorder.notify_tts_done()
                self.recorder.drain_post_wake(_POST_WAKE_DRAIN_SEC)

                tracker = LatencyTracker()
                tracker.mark("wake_end")

                self.vc.set_state(VoiceState.RECORDING)
                self._stt_executor.submit(
                    self._record_transcribe_and_handle, tracker, False, inline_cmd
                )

            elif current == VoiceState.SPEAKING:
                time.sleep(0.02)
            elif current == VoiceState.ERROR:
                time.sleep(0.5)
                self.vc.set_state(VoiceState.IDLE)
            else:
                time.sleep(0.02)

    # ── STT WORKER ────────────────────────────────────────────────────────

    def _record_transcribe_and_handle(
        self,
        tracker: LatencyTracker,
        is_followup: bool = False,
        inline_cmd: str = "",
    ) -> None:
        if self._stt_busy.is_set():
            logger.debug("[STT] Busy — skipping duplicate wake")
            self.vc.set_state(VoiceState.IDLE)
            return

        self._stt_busy.set()
        try:
            audio = self.recorder.record_command(tracker=tracker)

            if audio.size == 0:
                logger.info("[STT] Empty audio → IDLE")
                self.vc.set_state(VoiceState.IDLE)
                if is_followup:
                    self.say("I didn't hear anything. Could you try again?", wait=True)
                    self.trigger_followup()
                return

            if self._is_speaking.is_set():
                self.vc.set_state(VoiceState.IDLE)
                return

            self.vc.set_state(VoiceState.TRANSCRIBING)
            tracker.mark("stt_start")

            import asyncio as _aio
            text, confidence = _aio.run(
                self.transcriber.transcribe(audio, self.recorder.sample_rate)
            )
            tracker.mark("stt_end")

            # Permissive STT gate — only drop absolute garbage
            if not stt_stability.accept(text, confidence=confidence):
                logger.info(f"[STT GATE] Rejected garbage: '{text}' (conf={confidence:.3f})")
                self.vc.set_state(VoiceState.IDLE)
                if is_followup:
                    self.say("I missed that, Sir. Could you try again?", wait=True)
                    self.trigger_followup()
                return

            # Normalize (permissive)
            text = normalize_command(text, source="stt", is_followup=is_followup)
            if not text:
                self.vc.set_state(VoiceState.IDLE)
                if is_followup:
                    self.say("I missed that, Sir. Could you try again?", wait=True)
                    self.trigger_followup()
                return

            if text.lower().strip() in _INTERRUPT_COMMANDS:
                request_interrupt()
                self.say("Stopping.", priority=True)
                self.vc.set_state(VoiceState.IDLE)
                return

            logger.info(f" Command accepted: '{text}' (conf={confidence:.3f})")
            self.vc.set_state(VoiceState.PROCESSING)
            self._enqueue(text, tracker, confidence=confidence)

        except Exception as e:
            logger.error(f" STT Pipeline Error: {e}", exc_info=True)
            self.vc.set_state(VoiceState.ERROR)
        finally:
            self._stt_busy.clear()

    # ── EXECUTION LOOP ────────────────────────────────────────────────────

    def _execution_loop(self):
        asyncio.set_event_loop(self._exec_loop)

        async def _run():
            while self._running or not self._command_queue.empty():
                try:
                    cmd = self._command_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue

                clear_interrupt()
                self._executing.set()
                cmd.tracker.mark("exec_start")

                try:
                    await self._execute_command(cmd)
                except Exception as e:
                    logger.error(f" Execution error: {e}", exc_info=True)
                    self.say("Something went wrong.")
                finally:
                    cmd.tracker.mark("exec_end")
                    self._executing.clear()

        try:
            self._exec_loop.run_until_complete(_run())
        except Exception as e:
            logger.error(f" Execution loop crashed: {e}", exc_info=True)

    async def _execute_command(self, cmd: PendingCommand):
        logger.info(f"️  Executing: '{cmd.text}'")

        text_lower = cmd.text.lower()
        if any(w in text_lower for w in ["research", "find out", "look up"]):
            self.say("Let me look into that.")

        self._is_speaking.set()

        try:
            result = await self._exec_loop.run_in_executor(
                self._cmd_executor,
                self.on_command,
                cmd.text,
                self._tts,
            )

            if isinstance(result, str) and result == "[BACKGROUND_TASK_STARTED]":
                self.vc.set_state(VoiceState.BACKGROUND)
            elif self._is_speaking.is_set():
                self.vc.set_state(VoiceState.SPEAKING)
                await asyncio.sleep(0.1)
                self.vc.set_state(VoiceState.IDLE)
            else:
                self.vc.set_state(VoiceState.IDLE)

        except Exception:
            self.vc.set_state(VoiceState.ERROR)
            raise
        finally:
            self._is_speaking.clear()
            cmd.tracker.mark("tts_end")
            logger.info(cmd.tracker.summary(cmd.text))

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _enqueue(
        self,
        text: str,
        tracker: Optional[LatencyTracker] = None,
        confidence: float = 1.0,
    ):
        if tracker is None:
            tracker = LatencyTracker()
        cmd = PendingCommand(
            text=text, timestamp=time.time(),
            tracker=tracker, confidence=confidence,
        )
        try:
            self._command_queue.put_nowait(cmd)
        except queue.Full:
            try:
                dropped = self._command_queue.get_nowait()
                logger.warning(f"️  Queue full — dropped: '{dropped.text[:50]}'")
                self._command_queue.put_nowait(cmd)
            except queue.Empty:
                pass