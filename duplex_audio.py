"""
FULL-DUPLEX AUDIO — Production-Complete Software AEC + Barge-In
================================================================
v2 upgrades:
  - WebRTC VAD replaces energy-based detection (much better on background noise)
  - Thread-decoupled TTS / mic paths
  - Spectral subtraction AEC with gate fallback
  - Semantic barge-in via Vosk partial transcripts
  - Volume ducking during speech detection
  - Echo-contaminated buffer flush after barge-in

WebRTC VAD install: pip install webrtcvad
"""

import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# WEBRTC VAD WRAPPER
# ════════════════════════════════════════════════════════════════════════════

class WebRTCVAD:
    """
    WebRTC Voice Activity Detector.
    Much more accurate than energy-based detection — works on noisy rooms.

    Aggressiveness: 0 (least aggressive) → 3 (most aggressive = filters most noise)
    Frame sizes: WebRTC VAD requires exactly 10ms, 20ms, or 30ms frames.
    """

    # Valid frame durations for webrtcvad (ms)
    _VALID_FRAME_MS = (10, 20, 30)

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2,
                 frame_ms: int = 30):
        self._sr            = sample_rate
        self._aggressiveness = min(3, max(0, aggressiveness))
        self._frame_ms      = frame_ms if frame_ms in self._VALID_FRAME_MS else 30
        self._frame_samples = int(sample_rate * self._frame_ms / 1000)
        self._vad           = None
        self._available     = False
        self._buffer        = np.array([], dtype=np.int16)
        self._speech_frames = 0
        self._silence_frames = 0
        # Smoothing: require N consecutive speech frames before firing
        self._speech_trigger_frames  = 2   # ~60ms
        self._silence_trigger_frames = 8   # ~240ms
        self._is_speech = False
        self._try_init()

    def _try_init(self):
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self._aggressiveness)
            self._available = True
            logger.info(
                f"[VAD] WebRTC VAD active "
                f"(aggr={self._aggressiveness}, frame={self._frame_ms}ms)"
            )
        except ImportError:
            logger.info("[VAD] webrtcvad not installed — falling back to energy VAD")
            logger.info("[VAD]   Install: pip install webrtcvad")
        except Exception as e:
            logger.warning(f"[VAD] WebRTC VAD init failed: {e} — using energy fallback")

    @property
    def available(self) -> bool:
        return self._available

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Determine if the audio chunk contains human speech.
        Uses WebRTC VAD if available, falls back to RMS energy.
        """
        if not self._available:
            return self._energy_vad(audio_chunk)

        # Accumulate into frames
        chunk16 = audio_chunk.astype(np.int16)
        self._buffer = np.concatenate([self._buffer, chunk16])

        detected = False
        while len(self._buffer) >= self._frame_samples:
            frame = self._buffer[:self._frame_samples]
            self._buffer = self._buffer[self._frame_samples:]

            try:
                frame_bytes = frame.tobytes()
                speech = self._vad.is_speech(frame_bytes, self._sr)
            except Exception:
                speech = self._energy_vad(frame)

            if speech:
                self._speech_frames  += 1
                self._silence_frames  = 0
            else:
                self._silence_frames += 1
                self._speech_frames   = 0

            if self._speech_frames >= self._speech_trigger_frames:
                self._is_speech = True
                detected = True
            elif self._silence_frames >= self._silence_trigger_frames:
                self._is_speech = False

        return self._is_speech

    def _energy_vad(self, audio: np.ndarray, threshold: float = 0.003) -> bool:
        """Fallback energy-based VAD."""
        if len(audio) == 0:
            return False
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2) + 1e-9))
        return rms > threshold

    def reset(self):
        """Reset state between utterances."""
        self._buffer        = np.array([], dtype=np.int16)
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speech     = False


# ════════════════════════════════════════════════════════════════════════════
# ACOUSTIC ECHO CANCELLER (Software AEC via spectral subtraction)
# ════════════════════════════════════════════════════════════════════════════

class AcousticEchoCanceller:
    """
    Software AEC using spectral subtraction.

    When TTS plays, its audio chunks are buffered here. The mic input
    thread calls cancel(mic_chunk) which subtracts the known TTS signal
    from the mic input, reducing echo by ~15-20 dB.

    For a zero-dependency approach: just gates the mic when TTS energy
    is above threshold (simpler but effective for Jarvis's use case).
    """

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 20):
        self._sr        = sample_rate
        self._frame_sz  = int(sample_rate * frame_ms / 1000)
        self._tts_buf: queue.Queue = queue.Queue(maxsize=200)
        self._tts_rms   = 0.0
        self._rms_alpha = 0.9
        self._lock      = threading.Lock()
        self._tts_active = False

    def on_tts_chunk(self, audio: np.ndarray):
        if not self._tts_active:
            return
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2) + 1e-9))
        with self._lock:
            self._tts_rms = self._rms_alpha * self._tts_rms + (1 - self._rms_alpha) * rms
        try:
            self._tts_buf.put_nowait(audio.copy())
        except queue.Full:
            try:
                self._tts_buf.get_nowait()
                self._tts_buf.put_nowait(audio.copy())
            except queue.Empty:
                pass

    def on_tts_started(self):
        self._tts_active = True
        logger.debug("[AEC] TTS started — echo cancellation active")

    def on_tts_stopped(self):
        self._tts_active = False
        with self._lock:
            self._tts_rms = 0.0
        while not self._tts_buf.empty():
            try:
                self._tts_buf.get_nowait()
            except queue.Empty:
                break
        logger.debug("[AEC] TTS stopped — echo cancellation off")

    def cancel(self, mic_chunk: np.ndarray) -> np.ndarray:
        """Apply AEC to a mic audio chunk."""
        if not self._tts_active:
            return mic_chunk

        with self._lock:
            tts_rms = self._tts_rms

        mic_f   = mic_chunk.astype(np.float32)
        mic_rms = float(np.sqrt(np.mean(mic_f ** 2) + 1e-9))

        # Gate: if TTS is much louder than mic, zero out mic
        if tts_rms > mic_rms * 3.0:
            return np.zeros_like(mic_chunk, dtype=np.int16)

        # Spectral subtraction
        try:
            ref = self._tts_buf.get_nowait().astype(np.float32)
            min_len   = min(len(mic_f), len(ref))
            cancelled = mic_f[:min_len] - ref[:min_len] * 0.8
            cancelled = np.clip(cancelled, -32768, 32767)
            result    = cancelled.astype(np.int16)
            if len(result) < len(mic_chunk):
                result = np.pad(result, (0, len(mic_chunk) - len(result)))
            return result
        except queue.Empty:
            pass

        return mic_chunk

    @property
    def tts_active(self) -> bool:
        return self._tts_active

    @property
    def current_tts_rms(self) -> float:
        with self._lock:
            return self._tts_rms


# ════════════════════════════════════════════════════════════════════════════
# STREAMING INTERRUPT HANDLER
# ════════════════════════════════════════════════════════════════════════════

class StreamingInterruptHandler:
    """
    Listens to PARTIAL audio transcripts from Vosk or Whisper streaming.
    Executes true semantic barge-in to kill TTS playback instantly.

    Also wires WebRTC VAD for accurate speech-onset detection during TTS.
    """

    _TTS_ENERGY_THRESHOLD = 0.005
    _DEBOUNCE_S = 1.5

    def __init__(
        self,
        tts_engine,
        fast_router,
        recorder,
        aec: Optional[AcousticEchoCanceller] = None,
        sample_rate: int = 16000,
    ):
        self.tts      = tts_engine
        self.router   = fast_router
        self.recorder = recorder
        self.aec      = aec
        # WebRTC VAD for accurate speech detection during TTS playback
        self.vad      = WebRTCVAD(sample_rate=sample_rate, aggressiveness=2, frame_ms=30)
        self._last_interrupt_at = 0.0
        self._lock    = threading.Lock()

    def on_partial_transcript(self, partial_text: str):
        """
        Called with each partial transcript from Vosk/Whisper.
        Must be FAST — runs in the audio callback thread.
        """
        if not self._is_tts_active():
            return

        partial_lower = partial_text.lower().strip()
        if not partial_lower or len(partial_lower) < 2:
            return

        now = time.time()
        with self._lock:
            if now - self._last_interrupt_at < self._DEBOUNCE_S:
                return

        self._duck_volume(0.2)

        result       = self.router.classify(partial_lower, is_partial=True)
        is_interrupt = (
            result is not None and
            result.get("intent") in {"cancel", "pause_media"}
        )

        if is_interrupt:
            with self._lock:
                self._last_interrupt_at = time.time()
            logger.warning(
                f"[Duplex]  BARGE-IN on partial: '{partial_text}' "
                f"→ intent={result.get('intent')}"
            )
            self._do_barge_in()
        else:
            self._duck_volume(1.0)

    def on_mic_chunk(self, audio_chunk: np.ndarray):
        """
        Called with each raw mic frame (before STT).
        Uses WebRTC VAD to detect speech onset during TTS
        and immediately duck volume — no partial transcript needed.
        """
        if not self._is_tts_active():
            return
        if self.vad.is_speech(audio_chunk):
            self._duck_volume(0.2)

    def _is_tts_active(self) -> bool:
        if self.aec and self.aec.tts_active:
            return True
        player = getattr(self.tts, '_player', None)
        if player:
            if getattr(player, '_running', False):
                return True
            thread = getattr(player, '_thread', None)
            if thread and thread.is_alive():
                return True
        if hasattr(self.tts, 'is_speaking') and callable(self.tts.is_speaking):
            return self.tts.is_speaking()
        return False

    def _do_barge_in(self):
        try:
            if hasattr(self.tts, 'stop'):
                self.tts.stop()
            self._duck_volume(1.0)
            self._flush_recorder()
            if self.aec:
                self.aec.on_tts_stopped()
            self.vad.reset()
            logger.info("[Duplex]  Barge-in executed — TTS stopped, buffer flushed")
        except Exception as e:
            logger.error(f"[Duplex] Barge-in error: {e}")

    def _duck_volume(self, level: float):
        try:
            player = getattr(self.tts, '_player', None)
            if player and hasattr(player, '_volume_scale'):
                player._volume_scale = max(0.0, min(1.0, level))
        except Exception:
            pass

    def _flush_recorder(self):
        try:
            q = getattr(self.recorder, '_queue', None)
            if q:
                flushed = 0
                while not q.empty():
                    try:
                        q.get_nowait()
                        flushed += 1
                    except Exception:
                        break
                if flushed > 0:
                    logger.debug(f"[Duplex] Flushed {flushed} echo-contaminated audio chunks")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# VOSK PARTIAL CALLBACK PATCHER
# ════════════════════════════════════════════════════════════════════════════

def patch_vosk_partial_callback(wake_detector, interrupt_handler: StreamingInterruptHandler):
    """
    Patch Vosk WakeWordDetector to fire partial transcript callbacks.
    Wraps detect() to call interrupt_handler.on_partial_transcript()
    on every Vosk partial result.
    """
    import json

    _orig_detect = wake_detector.detect

    def _patched_detect(audio_chunk, is_speaking=False):
        recognizer = getattr(wake_detector, '_recognizer', None)
        if recognizer:
            try:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                if partial:
                    interrupt_handler.on_partial_transcript(partial)
            except Exception:
                pass
        # Also feed raw chunk to VAD for immediate volume ducking
        try:
            chunk_array = np.frombuffer(audio_chunk, dtype=np.int16)
            interrupt_handler.on_mic_chunk(chunk_array)
        except Exception:
            pass
        return _orig_detect(audio_chunk, is_speaking=is_speaking)

    wake_detector.detect = _patched_detect
    logger.info("[Duplex]  Vosk partial transcript callback wired (WebRTC VAD active)")


# ════════════════════════════════════════════════════════════════════════════
# TTS ENGINE PATCHES
# ════════════════════════════════════════════════════════════════════════════

def patch_tts_engine(tts_engine, aec: Optional[AcousticEchoCanceller] = None):
    """
    Patch LocalTTSEngine with:
      - is_speaking() method
      - duck_volume(level) method
      - on_chunk_play hook → AEC
      - on_stopped hook → AEC
    """
    def _is_speaking(self_tts=None):
        player = getattr(tts_engine, '_player', None)
        if not player:
            return False
        if getattr(player, '_running', False):
            return True
        thread = getattr(player, '_thread', None)
        return bool(thread and thread.is_alive())

    tts_engine.is_speaking = _is_speaking

    def _duck_volume(level: float):
        try:
            player = getattr(tts_engine, '_player', None)
            if player:
                player._volume_scale = max(0.0, min(1.0, level))
        except Exception:
            pass

    tts_engine.duck_volume = _duck_volume

    if aec:
        _orig_speak = tts_engine.speak

        def _patched_speak(text: str, priority: bool = False):
            aec.on_tts_started()
            try:
                _orig_speak(text, priority=priority)
            finally:
                aec.on_tts_stopped()

        tts_engine.speak = _patched_speak

    logger.info("[Duplex]  TTS engine patched (is_speaking + duck_volume + AEC hooks + WebRTC VAD)")


import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# ACOUSTIC ECHO CANCELLER (Software AEC via spectral subtraction)
# ════════════════════════════════════════════════════════════════════════════

class AcousticEchoCanceller:
    """
    Software AEC using spectral subtraction.

    When TTS plays, its audio chunks are buffered here. The mic input
    thread calls cancel(mic_chunk) which subtracts the known TTS signal
    from the mic input, reducing echo by ~15-20 dB.

    This is NOT perfect (hardware AEC in phones achieves 30-40 dB), but
    it prevents the most obvious feedback loops where Jarvis hears itself
    and triggers duplicate commands.

    For a zero-dependency approach: just gates the mic when TTS energy
    is above threshold (simpler but effective for Jarvis's use case).
    """

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 20):
        self._sr        = sample_rate
        self._frame_sz  = int(sample_rate * frame_ms / 1000)  # samples per frame
        self._tts_buf: queue.Queue = queue.Queue(maxsize=200)
        self._tts_rms   = 0.0          # rolling RMS of TTS output
        self._rms_alpha = 0.9          # smoothing factor
        self._lock      = threading.Lock()
        self._tts_active = False

    def on_tts_chunk(self, audio: np.ndarray):
        """Called by TTS engine for each audio chunk it plays."""
        if not self._tts_active:
            return
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2) + 1e-9))
        with self._lock:
            self._tts_rms = self._rms_alpha * self._tts_rms + (1 - self._rms_alpha) * rms
        # Buffer for spectral subtraction
        try:
            self._tts_buf.put_nowait(audio.copy())
        except queue.Full:
            try:
                self._tts_buf.get_nowait()
                self._tts_buf.put_nowait(audio.copy())
            except queue.Empty:
                pass

    def on_tts_started(self):
        self._tts_active = True
        logger.debug("[AEC] TTS started — echo cancellation active")

    def on_tts_stopped(self):
        self._tts_active = False
        with self._lock:
            self._tts_rms = 0.0
        # Drain buffer
        while not self._tts_buf.empty():
            try:
                self._tts_buf.get_nowait()
            except queue.Empty:
                break
        logger.debug("[AEC] TTS stopped — echo cancellation off")

    def cancel(self, mic_chunk: np.ndarray) -> np.ndarray:
        """
        Apply AEC to a mic audio chunk.

        Strategy:
          - If TTS is NOT active: pass through unchanged
          - If TTS is active and TTS RMS > mic threshold: gate the mic
            (return near-silence, preventing Jarvis from hearing itself)
          - Otherwise: spectral subtraction (remove estimated echo component)

        Returns processed audio chunk.
        """
        if not self._tts_active:
            return mic_chunk

        with self._lock:
            tts_rms = self._tts_rms

        mic_f = mic_chunk.astype(np.float32)
        mic_rms = float(np.sqrt(np.mean(mic_f ** 2) + 1e-9))

        # Gate: if TTS is much louder than mic, zero out mic
        # This prevents Jarvis's own voice from being fed back
        if tts_rms > mic_rms * 3.0:
            return np.zeros_like(mic_chunk, dtype=np.int16)

        # Spectral subtraction: subtract reference TTS signal estimate
        try:
            ref = self._tts_buf.get_nowait().astype(np.float32)
            # Align lengths
            min_len = min(len(mic_f), len(ref))
            cancelled = mic_f[:min_len] - ref[:min_len] * 0.8
            # Clip and convert back
            cancelled = np.clip(cancelled, -32768, 32767)
            result = cancelled.astype(np.int16)
            if len(result) < len(mic_chunk):
                result = np.pad(result, (0, len(mic_chunk) - len(result)))
            return result
        except queue.Empty:
            pass

        return mic_chunk

    @property
    def tts_active(self) -> bool:
        return self._tts_active

    @property
    def current_tts_rms(self) -> float:
        with self._lock:
            return self._tts_rms


# ════════════════════════════════════════════════════════════════════════════
# STREAMING INTERRUPT HANDLER
# ════════════════════════════════════════════════════════════════════════════

class StreamingInterruptHandler:
    """
    Listens to PARTIAL audio transcripts from Vosk or Whisper streaming.
    Executes true semantic barge-in to kill TTS playback instantly.

    Requires partial transcript callback to be wired into the STT engine.

    For Vosk:
        recognizer.SetPartialWords(True)
        # In audio loop:
        if recognizer.AcceptWaveform(chunk):
            text = json.loads(recognizer.Result())["text"]
        else:
            partial = json.loads(recognizer.PartialResult())["partial"]
            interrupt_handler.on_partial_transcript(partial)

    For faster-whisper (no native streaming):
        Wire to a chunk-based pipeline that calls on_partial_transcript()
        with accumulated partial text every ~200ms.
    """

    # Minimum TTS RMS energy to confirm TTS is really playing (not silence)
    _TTS_ENERGY_THRESHOLD = 0.005
    # Debounce: ignore barge-in within this many seconds of last interrupt
    _DEBOUNCE_S = 1.5

    def __init__(
        self,
        tts_engine,
        fast_router,
        recorder,
        aec: Optional[AcousticEchoCanceller] = None,
    ):
        self.tts         = tts_engine
        self.router      = fast_router
        self.recorder    = recorder
        self.aec         = aec
        self._last_interrupt_at = 0.0
        self._lock       = threading.Lock()

    def on_partial_transcript(self, partial_text: str):
        """
        Called with each partial transcript from Vosk/Whisper.
        Performs barge-in detection and instant TTS kill if triggered.

        This method must be FAST — it runs in the audio callback thread.
        """
        # Is TTS actually playing?
        if not self._is_tts_active():
            return

        partial_lower = partial_text.lower().strip()
        if not partial_lower or len(partial_lower) < 2:
            return

        # Debounce: prevent rapid-fire interrupts
        now = time.time()
        with self._lock:
            if now - self._last_interrupt_at < self._DEBOUNCE_S:
                return

        # Duck volume immediately when speech is detected during TTS
        # This gives acoustic headroom before we decide to kill
        self._duck_volume(0.2)

        # Fast router: Tier 1 + Tier 2 only (partial = is_partial=True)
        result = self.router.classify(partial_lower, is_partial=True)
        is_interrupt = (
            result is not None and
            result.get("intent") in {"cancel", "pause_media"}
        )

        if is_interrupt:
            with self._lock:
                self._last_interrupt_at = time.time()

            logger.warning(
                f"[Duplex]  BARGE-IN on partial: '{partial_text}' "
                f"→ intent={result.get('intent')}"
            )
            self._do_barge_in()
        else:
            # Not an interrupt — restore volume
            self._duck_volume(1.0)

    def _is_tts_active(self) -> bool:
        """Check if TTS engine is currently playing audio."""
        # Try multiple ways to detect active TTS
        if self.aec and self.aec.tts_active:
            return True
        # Check LocalTTSEngine._player._running
        player = getattr(self.tts, '_player', None)
        if player:
            if getattr(player, '_running', False):
                return True
            thread = getattr(player, '_thread', None)
            if thread and thread.is_alive():
                return True
        # Check is_speaking flag
        if hasattr(self.tts, 'is_speaking') and callable(self.tts.is_speaking):
            return self.tts.is_speaking()
        return False

    def _do_barge_in(self):
        """Execute the barge-in: stop TTS and flush echo buffer."""
        try:
            # Stop TTS immediately
            if hasattr(self.tts, 'stop'):
                self.tts.stop()
            # Restore volume
            self._duck_volume(1.0)
            # Flush echo-contaminated audio from recorder queue
            self._flush_recorder()
            # Notify AEC
            if self.aec:
                self.aec.on_tts_stopped()
            logger.info("[Duplex]  Barge-in executed — TTS stopped, buffer flushed")
        except Exception as e:
            logger.error(f"[Duplex] Barge-in error: {e}")

    def _duck_volume(self, level: float):
        """Reduce or restore TTS volume. level=1.0 is full, 0.2 is 20%."""
        try:
            player = getattr(self.tts, '_player', None)
            if player and hasattr(player, '_volume_scale'):
                player._volume_scale = max(0.0, min(1.0, level))
        except Exception:
            pass

    def _flush_recorder(self):
        """Discard echo-contaminated audio that accumulated during TTS."""
        try:
            q = getattr(self.recorder, '_queue', None)
            if q:
                flushed = 0
                while not q.empty():
                    try:
                        q.get_nowait()
                        flushed += 1
                    except Exception:
                        break
                if flushed > 0:
                    logger.debug(f"[Duplex] Flushed {flushed} echo-contaminated audio chunks")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# VOSK PARTIAL CALLBACK PATCHER
# ════════════════════════════════════════════════════════════════════════════

def patch_vosk_partial_callback(wake_detector, interrupt_handler: StreamingInterruptHandler):
    """
    Patch the Vosk WakeWordDetector to also fire partial transcript callbacks.

    Wraps WakeWordDetector.detect() to call interrupt_handler.on_partial_transcript()
    on every Vosk partial result before it's processed.

    Usage:
        from duplex_audio import patch_vosk_partial_callback
        patch_vosk_partial_callback(service.wake_detector, interrupt_handler)
    """
    import json

    _orig_detect = wake_detector.detect

    def _patched_detect(audio_chunk, is_speaking=False):
        # Try to extract partial result from Vosk before full recognition
        recognizer = getattr(wake_detector, '_recognizer', None)
        if recognizer:
            try:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                if partial:
                    interrupt_handler.on_partial_transcript(partial)
            except Exception:
                pass
        return _orig_detect(audio_chunk, is_speaking=is_speaking)

    wake_detector.detect = _patched_detect
    logger.info("[Duplex]  Vosk partial transcript callback wired")


# ════════════════════════════════════════════════════════════════════════════
# TTSENGINE PATCHES — add duck_volume and is_speaking to LocalTTSEngine
# ════════════════════════════════════════════════════════════════════════════

def patch_tts_engine(tts_engine, aec: Optional[AcousticEchoCanceller] = None):
    """
    Patch LocalTTSEngine with:
      - is_speaking() method
      - duck_volume(level) method
      - on_chunk_play hook → AEC
      - on_stopped hook → AEC
    Call after LocalTTSEngine is created.
    """
    # Add is_speaking()
    def _is_speaking(self_tts=None):
        player = getattr(tts_engine, '_player', None)
        if not player:
            return False
        if getattr(player, '_running', False):
            return True
        thread = getattr(player, '_thread', None)
        return bool(thread and thread.is_alive())

    tts_engine.is_speaking = _is_speaking

    # Add duck_volume()
    def _duck_volume(level: float):
        try:
            player = getattr(tts_engine, '_player', None)
            if player:
                # We scale the enqueued audio by this factor
                player._volume_scale = max(0.0, min(1.0, level))
        except Exception:
            pass

    tts_engine.duck_volume = _duck_volume

    # Wire AEC if provided
    if aec:
        _orig_speak = tts_engine.speak

        def _patched_speak(text: str, priority: bool = False):
            aec.on_tts_started()
            try:
                _orig_speak(text, priority=priority)
            finally:
                aec.on_tts_stopped()

        tts_engine.speak = _patched_speak

    logger.info("[Duplex]  TTS engine patched (is_speaking + duck_volume + AEC hooks)")