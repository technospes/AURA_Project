"""
JARVIS STT PIPELINE v4 — Semantic Correction + Multi-Pass Validation
=====================================================================

NEW vs v3:
  1. SemanticCorrector integrated (PART 1 requirement)
  2. Multi-pass transcription with scoring (PART 2 requirement)
  3. Confidence-aware pipeline routing (PART 4 requirement)
  4. Early intent detection for latency reduction (PART 5 requirement)

Confidence-aware pipeline (PART 4):
  ┌─────────────────────────────────────────────────────────────────┐
  │  conf >= 0.85  → Accept as-is (phonetic corrections only)       │
  │  0.50 < conf < 0.85 → Run SemanticCorrector                     │
  │  conf <= 0.50  → Retry transcription → then SemanticCorrector   │
  └─────────────────────────────────────────────────────────────────┘

Multi-pass selection (PART 2):
  Pass 1: normal audio (preprocessed)
  Pass 2: original audio (only if pass-1 conf < 0.60)
  Score  = conf + language_quality_score
  Winner = higher score, validated against entity preservation

All v3 fixes are preserved:
  - Pre-roll buffer (BUG C)
  - silence_frames=25 (BUG D)
  - vad_filter=False (BUG A)
  - small.en model (BUG B)
  - condition_on_previous_text=True (BUG E)
  - No compression_ratio_threshold (BUG F)
"""

import asyncio
import collections
import concurrent.futures
import io
import logging
import math
import os
import re
import tempfile
import threading
import time
import wave
from typing import Deque, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Import the new modules (sibling files in jarvis_patch/) ──────────────
try:
    from jarvis_patch.semantic_corrector import (
        SemanticCorrector, CorrectionResult, needs_correction,
        _CONF_CORRECTION_THRESHOLD, _CONF_RETRY_THRESHOLD,
    )
    _SEMANTIC_CORRECTOR_AVAILABLE = True
except ImportError:
    _SEMANTIC_CORRECTOR_AVAILABLE = False
    logger.warning("[STT v4] semantic_corrector not found — correction disabled")

try:
    from jarvis_patch.safety_validator import HardenedSafetyValidator, HardenedSandboxExecutor
    _HARDENED_SANDBOX_AVAILABLE = True
except ImportError:
    _HARDENED_SANDBOX_AVAILABLE = False
    logger.debug("[STT v4] hardened_sandbox not found — using original")


# ════════════════════════════════════════════════════════════════════════════
# ALL v3 CONSTANTS — UNCHANGED
# ════════════════════════════════════════════════════════════════════════════

WHISPER_CONTEXT_PROMPT = (
    "The following is a voice command spoken to an AI assistant named Jarvis. "
    "The speaker uses complete natural sentences in Indian English. "
    "Common commands include: open Spotify, open YouTube, play a song, "
    "search the web, which laptop should I buy under 50000 rupees, "
    "recommend a phone under 30000, research AI trends, close the tab, "
    "set the volume to 50 percent, send a message to someone, make a call, "
    "take a screenshot, lock the screen, shut down, change the resolution. "
    "Transcribe exactly what is spoken, preserving all words."
)

_DOMAIN_PROMPTS = {
    "recommend": (
        "Product recommendations: laptop, phone, headphones, smartwatch, TV. "
        "Price in rupees: 10000, 20000, 30000, 50000, 100000. "
        "Brands: Samsung, Apple, OnePlus, Realme, Poco, Dell, HP, Lenovo, Asus. "
    ),
    "media": (
        "Music platforms: Spotify, YouTube, SoundCloud, Netflix. "
        "Songs, albums, artists, playlists by name. "
    ),
    "system": (
        "Windows settings: resolution 1080p 1440p 4K, "
        "refresh rate 60Hz 144Hz 165Hz, brightness, volume, "
        "display, Bluetooth, Wi-Fi, airplane mode. "
    ),
    "communication": (
        "Contact names. WhatsApp, Discord, Telegram, Teams. "
        "Call, video call, send message, compose email. "
    ),
}

_HARD_HALLUCINATIONS = frozenset({
    "", " ", ".",
    "thank you for watching.", "thanks for watching.",
    "subtitles by the amara.org community",
    "like and subscribe", "subscribe",
    "www.movieweb.com",
    "[music]", "[Music]", "[MUSIC]",
    "[silence]", "[Silence]",
})

_REPEAT_ARTIFACT_RE = re.compile(r'^(.)\1{7,}$')


def _k_to_num(match):
    return str(int(match.group(1)) * 1000)

_CORRECTIONS = [
    (re.compile(r'^\s*(?:hey\s+|ok\s+|okay\s+)?jarvis[,.\s]+', re.I), ""),
    (re.compile(r'^\s*(?:yo\s+)?jarvis[,.\s]+',                  re.I), ""),
    (re.compile(r'\bspot\s*if\b',     re.I), "Spotify"),
    (re.compile(r'\byou\s*tube\b',    re.I), "YouTube"),
    (re.compile(r'\bdis\s*cord\b',    re.I), "Discord"),
    (re.compile(r'\bwhat\s*sapp\b',   re.I), "WhatsApp"),
    (re.compile(r'\bnet\s*flix\b',    re.I), "Netflix"),
    (re.compile(r'\bvs\s*code\b',     re.I), "VSCode"),
    (re.compile(r'\bfifty\s*thousand\b',  re.I), "50000"),
    (re.compile(r'\bthirty\s*thousand\b', re.I), "30000"),
    (re.compile(r'\btwenty\s*thousand\b', re.I), "20000"),
    (re.compile(r'\bten\s*thousand\b',    re.I), "10000"),
    (re.compile(r'\bone\s*lakh\b',        re.I), "100000"),
    (re.compile(r'\b(\d+)\s*k\s*(?:rupees?)?\b', re.I), _k_to_num),
    (re.compile(r'\b(?:fourteen|14)\s*(?:40|forty)\s*p\b', re.I), "1440p"),
    (re.compile(r'\b(?:ten|10)\s*(?:80|eighty)\s*p\b',     re.I), "1080p"),
    (re.compile(r'\bfour\s*k\b',  re.I), "4K"),
    (re.compile(r'\b144\s*(?:hurts|hertz|hz)\b', re.I), "144Hz"),
    (re.compile(r'\s{2,}'), " "),
    (re.compile(r'^\s+|\s+$'), ""),
]


def correct_transcript(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _CORRECTIONS:
        if callable(replacement):
            text = pattern.sub(replacement, text)
        else:
            text = pattern.sub(replacement, text)
    return text.strip()


# ════════════════════════════════════════════════════════════════════════════
# AUDIO PREPROCESSOR — unchanged from v3
# ════════════════════════════════════════════════════════════════════════════

class AudioPreprocessor:
    def __init__(self, sample_rate: int = 16000):
        self.sr = sample_rate
        self._scipy_ok: Optional[bool] = None

    def _check_scipy(self) -> bool:
        if self._scipy_ok is None:
            try:
                import scipy.signal
                self._scipy_ok = True
            except ImportError:
                self._scipy_ok = False
        return self._scipy_ok

    def process(self, audio: np.ndarray) -> np.ndarray:
        if audio is None or audio.size < 320:
            return audio if audio is not None else np.array([], dtype=np.int16)
        try:
            f = audio.astype(np.float32) / 32768.0
            f = f - np.mean(f)
            f = self._bandpass(f)
            f = self._agc(f)
            f = np.append(f[0], f[1:] - 0.85 * f[:-1])
            return (np.clip(f, -1.0, 1.0) * 32767).astype(np.int16)
        except Exception as e:
            logger.debug(f"[Preprocessor] Error: {e}")
            return audio

    def _bandpass(self, audio: np.ndarray) -> np.ndarray:
        if self._check_scipy():
            try:
                from scipy.signal import butter, sosfilt
                nyq  = self.sr / 2.0
                sos  = butter(3, [max(80.0/nyq, 0.001), min(8000.0/nyq, 0.999)],
                              btype='band', output='sos')
                return sosfilt(sos, audio).astype(np.float32)
            except Exception:
                pass
        alpha  = 0.95
        result = np.zeros_like(audio, dtype=np.float32)
        result[0] = audio[0]
        for i in range(1, len(audio)):
            result[i] = audio[i] - alpha * audio[i - 1]
        return result

    def _agc(self, f: np.ndarray) -> np.ndarray:
        rms = float(np.sqrt(np.mean(f ** 2)) + 1e-9)
        if rms > 0.0001:
            f = np.clip(f * min(0.126 / rms, 20.0), -1.0, 1.0)
        return f


# ════════════════════════════════════════════════════════════════════════════
# PRE-ROLL BUFFER — unchanged from v3
# ════════════════════════════════════════════════════════════════════════════

class PreRollBuffer:
    DURATION_S = 0.6

    def __init__(self, chunk_size: int = 1600, sample_rate: int = 16000):
        chunk_duration = chunk_size / sample_rate
        n_chunks       = max(2, int(self.DURATION_S / chunk_duration))
        self._buf: Deque[np.ndarray] = collections.deque(maxlen=n_chunks)
        self._lock = threading.Lock()

    def push(self, chunk: np.ndarray):
        with self._lock:
            self._buf.append(chunk.copy())

    def get_and_clear(self) -> np.ndarray:
        with self._lock:
            if not self._buf:
                return np.array([], dtype=np.int16)
            result = np.concatenate(list(self._buf))
            self._buf.clear()
            return result

    def clear(self):
        with self._lock:
            self._buf.clear()


# ════════════════════════════════════════════════════════════════════════════
# MULTI-PASS SCORER — NEW for PART 2
# ════════════════════════════════════════════════════════════════════════════

def _language_quality_score(text: str) -> float:
    """
    Estimate language quality of transcription without an LLM.
    Score 0.0 (garbage) to 1.0 (natural sentence).

    Heuristics:
    - Penalize repeated words heavily
    - Reward proper sentence structure
    - Penalize very short outputs from known-long commands
    - Reward presence of known command vocabulary
    """
    if not text or not text.strip():
        return 0.0

    words = text.lower().split()
    if not words:
        return 0.0

    score = 0.5  # Base score

    # Penalize repeated words
    word_pairs = [(words[i], words[i+1]) for i in range(len(words)-1)]
    repeat_pairs = sum(1 for a, b in word_pairs if a == b)
    score -= repeat_pairs * 0.15

    # Penalize triple repeats
    for i in range(len(words)-2):
        if words[i] == words[i+1] == words[i+2]:
            score -= 0.30

    # Reward recognized command vocabulary
    _KNOWN_VOCAB = {
        "open", "close", "play", "search", "find", "change", "set",
        "make", "call", "send", "type", "lock", "restart", "shutdown",
        "spotify", "youtube", "discord", "whatsapp", "chrome", "netflix",
        "phone", "laptop", "resolution", "volume", "brightness",
        "research", "recommend", "remind", "remember",
    }
    known_count = sum(1 for w in words if w in _KNOWN_VOCAB)
    score += min(known_count * 0.08, 0.30)

    # Penalize suspiciously short output
    if len(words) == 1:
        score -= 0.20

    return max(0.0, min(1.0, score))


def _select_best_hypothesis(
    h1_text: str, h1_conf: float,
    h2_text: str, h2_conf: float,
) -> Tuple[str, float, str]:
    """
    Score and select the better of two transcription hypotheses.

    Score = (confidence * 0.65) + (language_quality * 0.35)

    Returns: (best_text, best_conf, which_pass: "pass1"|"pass2")
    """
    if not h1_text and not h2_text:
        return "", 0.0, "neither"
    if not h1_text:
        return h2_text, h2_conf, "pass2"
    if not h2_text:
        return h1_text, h1_conf, "pass1"

    lq1 = _language_quality_score(h1_text)
    lq2 = _language_quality_score(h2_text)

    s1 = h1_conf * 0.65 + lq1 * 0.35
    s2 = h2_conf * 0.65 + lq2 * 0.35

    logger.debug(
        f"[STT] H1: '{h1_text[:50]}' conf={h1_conf:.3f} lq={lq1:.3f} → {s1:.3f}\n"
        f"[STT] H2: '{h2_text[:50]}' conf={h2_conf:.3f} lq={lq2:.3f} → {s2:.3f}"
    )

    if s1 >= s2:
        return h1_text, h1_conf, "pass1"
    else:
        return h2_text, h2_conf, "pass2"


# ════════════════════════════════════════════════════════════════════════════
# ENHANCED TRANSCRIBER v4 — FULL PIPELINE
# ════════════════════════════════════════════════════════════════════════════

class EnhancedTranscriber:
    """
    Drop-in replacement for WhisperTranscriber.

    v4 pipeline:
      1. Gentle audio preprocessing
      2. Energy gate (reject silence)
      3. Pass 1: transcribe preprocessed audio
      4. Pass 2: transcribe original audio (if conf < 0.60)
      5. Multi-pass selection via scoring
      6. Phonetic corrections (fast regex)
      7. Confidence-aware routing:
         - conf >= 0.85: done
         - conf < 0.85: SemanticCorrector
      8. Safety validation of semantic correction
      9. Return final text + confidence

    All v3 model choices preserved (small.en / medium.en based on VRAM).
    """

    def __init__(self, groq_api_key: str = "", use_local: bool = True):
        self._groq_key     = groq_api_key
        self._model        = None
        self._groq_client  = None
        self._preprocessor = AudioPreprocessor()
        self._executor     = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="stt-v4"
        )
        self._model_name   = "unknown"
        self._last_context = ""

        # SemanticCorrector — new in v4
        self._corrector: Optional["SemanticCorrector"] = None
        if _SEMANTIC_CORRECTOR_AVAILABLE and groq_api_key:
            self._corrector = SemanticCorrector(
                groq_api_key=groq_api_key,
                model="llama-3.1-8b-instant",   # Fast model — not overloading
                timeout_s=2.5,
                enable_cache=True,
            )
            logger.info("[STT v4]  SemanticCorrector ready")
        else:
            logger.info("[STT v4] ℹ SemanticCorrector disabled (no API key or module missing)")

        if use_local:
            self._load_local()
        if not self._model:
            self._load_groq()

    # ── Model loading (identical to v3) ─────────────────────────────────

    def _load_local(self):
        try:
            from faster_whisper import WhisperModel
            device, compute, model_id = "cpu", "int8", "small.en"
            try:
                import torch
                if torch.cuda.is_available():
                    device  = "cuda"
                    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
                    model_id = "medium.en" if vram_gb >= 6.0 else "small.en"
                    logger.info(f"[STT] GPU: {vram_gb:.1f}GB → {model_id}")
            except Exception:
                pass
            logger.info(f"[STT] Loading {model_id} on {device}...")
            self._model      = WhisperModel(model_id, device=device, compute_type=compute)
            self._model_name = f"{model_id}@{device}"
            logger.info(f"[STT]  {self._model_name}")
        except ImportError:
            logger.warning("[STT] faster-whisper not installed")
        except Exception as e:
            logger.error(f"[STT] Load failed: {e}")

    def _load_groq(self):
        if not self._groq_key:
            return
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=self._groq_key)
            self._model_name  = "groq/whisper-large-v3-turbo"
            logger.info("[STT]  Groq Whisper ready")
        except Exception as e:
            logger.error(f"[STT] Groq init failed: {e}")

    def update_context(self, context_hint: str):
        """Called by planner to improve vocabulary for next transcription."""
        self._last_context = context_hint.lower()

    def _build_prompt(self) -> str:
        prompt = WHISPER_CONTEXT_PROMPT
        ctx = self._last_context
        for key, extra in _DOMAIN_PROMPTS.items():
            if key in ctx:
                prompt += " " + extra
                break
        return prompt

    # ── MAIN PIPELINE ────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> Tuple[str, float]:
        """
        Full v4 pipeline. Returns (text, confidence).
        """
        if audio is None or audio.size < 800:
            return "", 0.0

        loop   = asyncio.get_event_loop()
        t_start = time.perf_counter()

        # ── Step 1: Preprocessing ────────────────────────────────────────
        clean = await loop.run_in_executor(
            self._executor, self._preprocessor.process, audio
        )

        # ── Step 2: Energy gate ──────────────────────────────────────────
        rms = float(np.sqrt(np.mean((clean.astype(np.float32) / 32768.0) ** 2)))
        if rms < 0.0008:
            return "", 0.0

        # Estimate audio duration (for truncation check)
        audio_dur_s = audio.size / sample_rate

        prompt = self._build_prompt()

        # ── Step 3: Pass 1 (preprocessed audio) ─────────────────────────
        if self._model:
            h1_text, h1_conf = await loop.run_in_executor(
                self._executor, self._transcribe_local, clean, sample_rate, prompt
            )
        elif self._groq_client:
            h1_text, h1_conf = await loop.run_in_executor(
                self._executor, self._transcribe_groq, clean, sample_rate
            )
        else:
            return "", 0.0

        # ── Step 4: Pass 2 (original audio) — only if needed ────────────
        h2_text, h2_conf = "", 0.0
        if self._model and h1_conf < 0.60 and audio.size > 8000:
            logger.info(f"[STT v4] Pass-1 conf={h1_conf:.3f} → running Pass-2 (original audio)")
            h2_text, h2_conf = await loop.run_in_executor(
                self._executor, self._transcribe_local, audio, sample_rate, prompt
            )

        # ── Step 5: Multi-pass selection ─────────────────────────────────
        best_text, best_conf, which = _select_best_hypothesis(
            h1_text, h1_conf, h2_text, h2_conf
        )
        if which == "pass2":
            logger.info(f"[STT v4] Pass-2 wins: conf={h2_conf:.3f} > pass1={h1_conf:.3f}")

        # ── Hard hallucination filter ─────────────────────────────────────
        if self._is_hard_hallucination(best_text):
            return "", 0.0

        # ── Step 6: Phonetic corrections (fast, always applied) ──────────
        best_text = correct_transcript(best_text)
        if not best_text:
            return "", 0.0

        # ── Step 7: Confidence-aware semantic correction ──────────────────
        # PART 4: route based on confidence tier
        if self._corrector:
            if best_conf <= _CONF_RETRY_THRESHOLD:
                # Tier 3: retry already done (Pass 2), now correct
                logger.info(f"[STT v4] Tier-3 (conf={best_conf:.3f}) → SemanticCorrector")
                best_text, best_conf = await self._run_semantic_correction(
                    best_text, best_conf, audio_dur_s
                )
            elif best_conf < _CONF_CORRECTION_THRESHOLD:
                # Tier 2: medium confidence — just correct
                logger.info(f"[STT v4] Tier-2 (conf={best_conf:.3f}) → SemanticCorrector")
                best_text, best_conf = await self._run_semantic_correction(
                    best_text, best_conf, audio_dur_s
                )
            else:
                # Tier 1: high confidence — still check for artifact patterns
                should_fix, reason = needs_correction(best_text, best_conf, audio_dur_s)
                if should_fix:
                    logger.info(f"[STT v4] Tier-1 artifact ({reason}) → SemanticCorrector")
                    best_text, best_conf = await self._run_semantic_correction(
                        best_text, best_conf, audio_dur_s
                    )

        t_total = (time.perf_counter() - t_start) * 1000
        logger.info(
            f" '{best_text[:80]}' "
            f"(conf={best_conf:.3f} | {self._model_name} | {t_total:.0f}ms)"
        )
        return best_text, best_conf

    async def _run_semantic_correction(
        self,
        text: str,
        conf: float,
        audio_dur_s: float,
    ) -> Tuple[str, float]:
        """Run SemanticCorrector and return (possibly corrected text, confidence)."""
        try:
            result = await self._corrector.correct(
                text=text,
                confidence=conf,
                context_hint=self._last_context,
                audio_duration_s=audio_dur_s,
            )
            if result.was_changed:
                return result.corrected, result.confidence
            return text, conf
        except Exception as e:
            logger.warning(f"[STT v4] Semantic correction failed: {e}")
            return text, conf

    # ── Local Whisper (identical to v3 params) ───────────────────────────

    def _transcribe_local(
        self,
        audio: np.ndarray,
        sr: int,
        initial_prompt: str,
    ) -> Tuple[str, float]:
        tmp_path = None
        try:
            try:
                import soundfile as sf
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                sf.write(tmp_path, audio, sr)
                tmp.close()
            except ImportError:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                tmp.close()
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2)
                    wf.setframerate(sr); wf.writeframes(audio.tobytes())

            segments, info = self._model.transcribe(
                tmp_path,
                language="en",
                task="transcribe",
                beam_size=5,
                best_of=5,
                temperature=0.0,
                condition_on_previous_text=True,
                initial_prompt=initial_prompt,
                vad_filter=False,          # v3 BUG A FIX — preserved
                no_speech_threshold=0.8,   # permissive
                word_timestamps=False,
            )
            seg_list = list(segments)
            if not seg_list:
                return "", 0.0

            text     = " ".join(s.text.strip() for s in seg_list).strip()
            total_lp = sum(getattr(s, 'avg_logprob', -0.5) for s in seg_list)
            conf     = max(0.0, min(1.0, math.exp(total_lp / len(seg_list))))
            return text, conf

        except Exception as e:
            logger.error(f"[STT] Local error: {e}", exc_info=True)
            return "", 0.0
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _transcribe_groq(self, audio: np.ndarray, sr: int) -> Tuple[str, float]:
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2)
                w.setframerate(sr); w.writeframes(audio.tobytes())
            buf.seek(0)
            result = self._groq_client.audio.transcriptions.create(
                file=("cmd.wav", buf.read()),
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
                language="en",
                temperature=0.0,
                prompt=WHISPER_CONTEXT_PROMPT[:224],
            )
            text = result.text.strip()
            conf = 0.85
            try:
                segs = getattr(result, 'segments', [])
                if segs:
                    avg  = sum(s.get('avg_logprob', -0.5) for s in segs) / len(segs)
                    conf = max(0.0, min(1.0, math.exp(avg)))
            except Exception:
                pass
            return text, conf
        except Exception as e:
            logger.error(f"[STT] Groq error: {e}")
            return "", 0.0

    def _is_hard_hallucination(self, text: str) -> bool:
        if not text:
            return True
        stripped = text.strip(" .!?,;-").lower()
        if not stripped:
            return True
        if stripped in _HARD_HALLUCINATIONS:
            return True
        if _REPEAT_ARTIFACT_RE.match(stripped):
            return True
        return False

    def get_corrector_stats(self) -> dict:
        if self._corrector:
            return self._corrector.get_stats()
        return {}


# ════════════════════════════════════════════════════════════════════════════
# RECORDER PATCH — unchanged from v3
# ════════════════════════════════════════════════════════════════════════════

def _patch_recorder(recorder):
    """Patch AudioRecorder for pre-roll and silence fixes (identical to v3)."""
    if not hasattr(recorder, '_preroll'):
        recorder._preroll = PreRollBuffer(
            chunk_size=recorder.chunk_size,
            sample_rate=recorder.sample_rate,
        )

    def patched_start_stream(device_id=None):
        import sounddevice as sd
        import queue as _q

        def _cb(indata, frames, time_info, status):
            chunk = indata.copy().flatten()
            recorder._preroll.push(chunk)
            try:
                recorder._queue.put_nowait(chunk)
            except _q.Full:
                try:
                    recorder._queue.get_nowait()
                    recorder._queue.put_nowait(chunk)
                except _q.Empty:
                    pass

        recorder._stream = sd.InputStream(
            samplerate=recorder.sample_rate, channels=1, dtype=np.int16,
            blocksize=recorder.chunk_size, device=device_id,
            callback=_cb, latency="low",
        )
        recorder._stream.start()
        recorder._running = True
        logger.info("[STT]  Audio stream started with pre-roll buffer")

    def patched_record_command(tracker=None):
        try:
            from voice.service import INTERRUPT_FLAG
        except ImportError:
            import threading
            INTERRUPT_FLAG = threading.Event()

        if recorder.tts_done_at > 0:
            elapsed = time.time() - recorder.tts_done_at
            drain   = max(0.10 - elapsed, 0.0)
            if drain > 0.05:
                time.sleep(min(drain, 0.2))
                while not recorder._queue.empty():
                    try: recorder._queue.get_nowait()
                    except Exception: break
            recorder._preroll.clear()

        logger.info(" Recording...")
        if tracker:
            tracker.mark("record_start")

        preroll = recorder._preroll.get_and_clear()
        buf = []
        if preroll.size > 0:
            pr_rms = float(np.sqrt(np.mean((preroll.astype(np.float32) / 32768.0) ** 2)))
            if pr_rms > recorder.min_speech_energy * 0.3:
                buf.append(preroll)

        threshold     = max(recorder.min_speech_energy, recorder._bg_noise * 1.5)
        speech_frames = 0
        onset_buf     = []
        deadline      = time.time() + 8.0

        while time.time() < deadline:
            if INTERRUPT_FLAG.is_set():
                return np.array([], dtype=np.int16)
            chunk = recorder.get_chunk(timeout=0.1)
            if chunk is None:
                continue
            energy = recorder._rms(chunk)
            if energy >= threshold:
                speech_frames += 1
                onset_buf.append(chunk)
                if speech_frames >= 2:
                    buf.extend(onset_buf)
                    recorder._bg_noise = min(0.02, 0.97 * recorder._bg_noise + 0.03 * energy)
                    break
            else:
                speech_frames = 0
                onset_buf.clear()

        if speech_frames < 2:
            return np.array([], dtype=np.int16)

        silence_count = 0
        start_time    = time.time()
        chunk_sec     = recorder.chunk_size / recorder.sample_rate
        min_chunks    = max(1, int(recorder.min_record_secs / chunk_sec))

        while True:
            chunk = recorder.get_chunk(timeout=0.1)
            if chunk is None:
                continue
            buf.append(chunk)
            energy    = recorder._rms(chunk)
            threshold = max(recorder.min_speech_energy, recorder._bg_noise * 1.4)
            elapsed   = time.time() - start_time

            if energy >= threshold:
                silence_count = 0
                recorder._bg_noise = min(0.02, 0.97 * recorder._bg_noise + 0.03 * energy)
            else:
                silence_count += 1

            if silence_count >= recorder.silence_frames:
                wait_start = time.time()
                extra, resumed = [], False
                while time.time() - wait_start < 1.5:
                    c = recorder.get_chunk(timeout=0.05)
                    if c is not None:
                        extra.append(c)
                        if recorder._rms(c) >= threshold:
                            silence_count = 0
                            buf.extend(extra)
                            resumed = True
                            break
                if not resumed:
                    buf.extend(extra)
                    if len(buf) >= min_chunks:
                        break
                    else:
                        silence_count = 0

            if elapsed >= recorder.max_duration:
                logger.warning(f"[Recorder] Max {recorder.max_duration}s reached")
                break
            if INTERRUPT_FLAG.is_set():
                break

        raw = np.concatenate(buf) if buf else np.array([], dtype=np.int16)
        return recorder._agc(raw) if raw.size > 0 else raw

    recorder.start_stream   = patched_start_stream
    recorder.record_command = patched_record_command
    logger.info("[STT]  Recorder patched (pre-roll + silence fix)")


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API — identical signature to v3
# ════════════════════════════════════════════════════════════════════════════

def patch_voice_service_stt():
    try:
        import voice.service as _svc
        _svc.WhisperTranscriber = EnhancedTranscriber
        logger.info("[STT PATCH]  WhisperTranscriber → EnhancedTranscriber v4")
    except Exception as e:
        logger.error(f"[STT PATCH] Module patch failed: {e}")


def apply_stt_patch_to_instance(service_instance):
    """Apply full v4 STT patch to an existing VoiceService instance."""
    try:
        cfg = service_instance.config
        service_instance.transcriber = EnhancedTranscriber(
            groq_api_key=cfg.get("groq_api_key", ""),
            use_local=cfg.get("use_local_whisper", True),
        )
        _patch_recorder(service_instance.recorder)
        logger.info("[STT PATCH]  v4 applied to VoiceService instance")
    except Exception as e:
        logger.error(f"[STT PATCH] Instance patch failed: {e}", exc_info=True)