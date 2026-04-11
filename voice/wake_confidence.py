"""
WAKE WORD CONFIDENCE SCORER
============================
Adds confidence smoothing, noise robustness, and false-positive filtering
to the basic Vosk wake word detection.

Problems solved:
  1. Single-frame detection is brittle (noise spike = false wake)
  2. "jarvis" in a song playing = false trigger
  3. Whispering in a noisy room = missed detection
  4. Accent variations that Vosk might score low

Solution: multi-frame evidence accumulation + environment-aware thresholds
"""

import collections
import logging
import time
from typing import Deque, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class WakeWordConfidenceScorer:
    """
    Smooths wake word detection over multiple audio frames.
    Reduces false positives in noisy environments.
    Raises sensitivity in quiet environments.

    Works as a wrapper around the basic WakeWordDetector.
    """

    def __init__(
        self,
        base_threshold: float = 0.15,
        smoothing_window: int = 3,
        noise_adaptation_rate: float = 0.1,
        false_positive_cooldown: float = 1.5,
    ):
        self.base_threshold      = base_threshold
        self.smoothing_window    = smoothing_window
        self.noise_adaptation_rate = noise_adaptation_rate
        self.false_positive_cooldown = false_positive_cooldown

        # Rolling evidence buffer: (timestamp, score)
        self._evidence: Deque[Tuple[float, float]] = collections.deque(
            maxlen=smoothing_window
        )

        # Environment tracking
        self._noise_floor: float    = 0.01   # RMS of background
        self._signal_history: Deque = collections.deque(maxlen=30)
        self._last_trigger_time: float = 0.0

        # Stats
        self.stats = {
            "triggers":       0,
            "false_positives_blocked": 0,
            "missed_by_threshold": 0,
        }

    def score_detection(
        self,
        detected: bool,
        variant: str,
        audio_chunk: np.ndarray,
        current_time: float
    ) -> Tuple[bool, float, str]:
        """
        Apply confidence scoring to a raw detection event.

        Args:
            detected: Whether Vosk reported a wake word
            variant: Which wake word variant was matched
            audio_chunk: The audio chunk that was processed
            current_time: Current Unix timestamp

        Returns:
            (should_trigger: bool, confidence: float, reason: str)
        """
        # ── 1. COOLDOWN CHECK ─────────────────────────────────────────────
        time_since_last = current_time - self._last_trigger_time
        if time_since_last < self.false_positive_cooldown:
            if detected:
                self.stats["false_positives_blocked"] += 1
                logger.debug(f"Cooldown: blocked trigger ({time_since_last:.1f}s < {self.false_positive_cooldown}s)")
            return False, 0.0, "cooldown"

        # ── 2. AUDIO QUALITY CHECK ────────────────────────────────────────
        rms = self._compute_rms(audio_chunk)
        self._update_noise_floor(rms, detected)

        snr = rms / max(self._noise_floor, 1e-6)

        if snr < 1.5 and detected:
            # Signal barely above noise floor — likely false positive
            logger.debug(f"Low SNR ({snr:.1f}) — likely noise, not voice")
            self.stats["false_positives_blocked"] += 1
            return False, 0.0, "low_snr"

        # ── 3. EVIDENCE ACCUMULATION ──────────────────────────────────────
        # Convert detection to a score
        if detected:
            # Score depends on how well-known the variant is
            base_score = self._variant_confidence(variant)
            # Boost by SNR
            score = min(base_score * min(snr / 5.0, 1.5), 1.0)
        else:
            score = 0.0

        self._evidence.append((current_time, score))

        # ── 4. SMOOTHED SCORE ─────────────────────────────────────────────
        # Weighted average — recent frames count more
        if len(self._evidence) == 0:
            return False, 0.0, "no_evidence"

        weights = np.linspace(0.5, 1.0, len(self._evidence))
        scores  = np.array([s for _, s in self._evidence])
        smoothed = float(np.average(scores, weights=weights))

        # ── 5. ADAPTIVE THRESHOLD ─────────────────────────────────────────
        threshold = self._adaptive_threshold(snr)

        # ── 6. DECISION ───────────────────────────────────────────────────
        if smoothed >= threshold:
            self._last_trigger_time = current_time
            self._evidence.clear()
            self.stats["triggers"] += 1
            confidence = smoothed
            logger.info(
                f"✅ Wake confirmed: '{variant}' | "
                f"conf={smoothed:.2f} | SNR={snr:.1f}x | threshold={threshold:.2f}"
            )
            return True, confidence, "confirmed"

        if detected and smoothed < threshold:
            self.stats["missed_by_threshold"] += 1
            logger.debug(f"Below threshold: {smoothed:.2f} < {threshold:.2f}")

        return False, smoothed, "below_threshold"

    def _variant_confidence(self, variant: str) -> float:
        """Return base confidence for a wake word variant."""
        EXACT_MATCHES = {"jarvis", "hey jarvis", "ok jarvis"}
        GOOD_MATCHES  = {"yo jarvis", "jarves", "jarvish", "hello jarvis"}

        if variant in EXACT_MATCHES:
            return 0.95
        elif variant in GOOD_MATCHES:
            return 0.80
        elif len(variant) >= 5:   # Longer partial matches
            return 0.65
        else:                      # Very short / uncertain
            return 0.50

    def _adaptive_threshold(self, snr: float) -> float:
        """
        Adjust threshold based on environment.
        Quiet environment (high SNR) → lower threshold (easier to trigger)
        Noisy environment (low SNR)  → higher threshold (harder to trigger)
        """
        if snr > 10:
            return self.base_threshold * 0.75   # Very quiet — be sensitive
        elif snr > 5:
            return self.base_threshold * 0.90
        elif snr > 2:
            return self.base_threshold
        elif snr > 1.5:
            return self.base_threshold * 1.15   # Noisy — be strict
        else:
            return self.base_threshold * 1.30   # Very noisy

    def _update_noise_floor(self, rms: float, speech_detected: bool):
        """Update background noise estimate when no speech is detected."""
        if not speech_detected:
            self._noise_floor = (
                (1 - self.noise_adaptation_rate) * self._noise_floor +
                self.noise_adaptation_rate * rms
            )

    def _compute_rms(self, audio: np.ndarray) -> float:
        audio_f = audio.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(audio_f ** 2)) + 1e-10)

    def get_stats(self) -> Dict:
        return dict(self.stats)


class MultiFrameWakeDetector:
    """
    Improved wake word detection that uses the confidence scorer.
    Drop-in replacement for the basic detection in voice/service.py.
    """

    def __init__(self, vosk_detector, confidence_scorer: Optional[WakeWordConfidenceScorer] = None):
        self._detector = vosk_detector
        self._scorer   = confidence_scorer or WakeWordConfidenceScorer()

    def detect(self, audio_chunk: np.ndarray) -> Tuple[bool, str, str, float]:
        """
        Detect wake word with confidence scoring.

        Returns:
            (detected: bool, variant: str, inline_command: str, confidence: float)
        """
        current_time = time.time()

        # Raw detection from Vosk
        raw_detected, variant, inline_cmd = self._detector.detect(audio_chunk)

        # Score it
        confirmed, confidence, reason = self._scorer.score_detection(
            detected=raw_detected,
            variant=variant,
            audio_chunk=audio_chunk,
            current_time=current_time
        )

        return confirmed, variant, inline_cmd, confidence
