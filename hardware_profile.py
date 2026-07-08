"""
HARDWARE PROFILER
=================
Auto-detects GPU/CPU specs on startup and returns the optimal
faster-whisper model configuration.

Model routing table:
  VRAM >= 6 GB   → large-v3          (highest accuracy, handles accents)
  VRAM >= 3 GB   → distil-large-v3   (very fast, excellent accuracy)
  CPU / VRAM < 3 → distil-small.en   (lightning fast, slight accuracy drop)

The selected config is cached as a module-level singleton so detection
runs exactly once per process lifetime.

Usage:
    from hardware_profile import get_hardware_profile, get_stt_config

    profile = get_hardware_profile()
    # {"device": "cuda", "vram_gb": 8.1, "cpu_cores": 12, ...}

    cfg = get_stt_config()
    # {"model": "large-v3", "device": "cuda", "compute": "float16",
    #   "vram_gb": 8.1, "tier": "high"}
"""

import logging
import platform
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareProfile:
    device: str            # "cuda" | "cpu"
    vram_gb: float         # 0.0 if CPU-only
    gpu_name: str          # "" if CPU-only
    cpu_cores: int
    ram_gb: float
    cuda_version: str      # "" if unavailable
    tier: str              # "high" | "mid" | "low"


@dataclass(frozen=True)
class STTConfig:
    model: str             # faster-whisper model name
    device: str            # "cuda" | "cpu"
    compute: str           # "float16" | "int8_float16" | "int8"
    vram_gb: float
    tier: str
    # Tuned VAD parameters (the sweet spot for not cutting off mid-sentence)
    vad_min_silence_ms: int   # 750ms is the sweet spot from testing
    vad_speech_pad_ms: int    # padding around speech edges


# ── DETECTION ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_hardware_profile() -> HardwareProfile:
    """Detect hardware. Cached — runs once per process."""
    cpu_cores = os.cpu_count() or 4
    ram_gb    = _get_ram_gb()

    # ── GPU detection ──────────────────────────────────────────────────────
    try:
        import torch
        if torch.cuda.is_available():
            props     = torch.cuda.get_device_properties(0)
            vram_gb   = props.total_memory / (1024 ** 3)
            gpu_name  = props.name
            cuda_ver  = torch.version.cuda or ""

            if vram_gb >= 8.0:
                tier = "high"
            elif vram_gb >= 6.0:
                tier = "mid"
            else:
                tier = "low"

            logger.info(
                f"[HW] GPU: {gpu_name} | VRAM: {vram_gb:.1f} GB | "
                f"CUDA: {cuda_ver} | Tier: {tier}"
            )
            return HardwareProfile(
                device="cuda", vram_gb=round(vram_gb, 2),
                gpu_name=gpu_name, cpu_cores=cpu_cores,
                ram_gb=ram_gb, cuda_version=cuda_ver, tier=tier
            )
    except ImportError:
        logger.info("[HW] torch not installed — CPU mode")
    except Exception as e:
        logger.warning(f"[HW] GPU detection failed: {e} — CPU mode")

    # CPU fallback
    logger.info(f"[HW] CPU: {cpu_cores} cores | RAM: {ram_gb:.1f} GB | Tier: low")
    return HardwareProfile(
        device="cpu", vram_gb=0.0, gpu_name="",
        cpu_cores=cpu_cores, ram_gb=ram_gb,
        cuda_version="", tier="low"
    )


@lru_cache(maxsize=1)
def get_stt_config() -> STTConfig:
    """
    Return the optimal STT config for this machine.

    VAD tuning rationale:
      - min_silence_duration_ms=750  → doesn't cut off mid-sentence pauses
        (breathing, thinking). 400ms is too aggressive, 1200ms feels sluggish.
      - speech_pad_ms=200  → adds 200ms buffer around speech edges so the
        first/last phoneme is never clipped.
    """
    hw = get_hardware_profile()

    # These VAD params are tuned for natural speech with ~100ms thinking pauses.
    # 600ms is the sweet spot: catches sentence completions without cutting off
    # mid-word on GPU (CUDA processes fast enough that 600ms feels instant).
    VAD_SILENCE_MS = 1000   # was 900 — 900ms caused truncation on fast speakers
    VAD_PAD_MS     = 200   # was 300 — 200ms is enough buffer

    if hw.tier == "high":
        cfg = STTConfig(
            model="large-v3",
            device="cuda",
            compute="float16",
            vram_gb=hw.vram_gb,
            tier="high",
            vad_min_silence_ms=VAD_SILENCE_MS,
            vad_speech_pad_ms=VAD_PAD_MS,
        )
    elif hw.tier == "mid":
        cfg = STTConfig(
            model="distil-small.en",
            device="cuda",
            compute="int8_float16",
            vram_gb=hw.vram_gb,
            tier="mid",
            vad_min_silence_ms=VAD_SILENCE_MS,
            vad_speech_pad_ms=VAD_PAD_MS,
        )
    else:
        cfg = STTConfig(
            model="distil-small.en",
            # ── THE FIX: Use your GPU if it exists, don't force CPU! ──
            device="cuda" if hw.device == "cuda" else "cpu",
            compute="int8",
            vram_gb=0.0,
            tier="low",
            vad_min_silence_ms=VAD_SILENCE_MS,
            vad_speech_pad_ms=VAD_PAD_MS,
        )

    logger.info(
        f"[HW] STT: {cfg.model} | device={cfg.device} | "
        f"compute={cfg.compute} | tier={cfg.tier}"
    )
    return cfg


def print_hardware_report():
    """Print a human-readable hardware report (called at startup)."""
    hw  = get_hardware_profile()
    stt = get_stt_config()
    lines = [
        "┌─ Hardware Profile ──────────────────────────────────",
        f"│  Device  : {hw.device.upper()}",
    ]
    if hw.device == "cuda":
        lines += [
            f"│  GPU     : {hw.gpu_name}",
            f"│  VRAM    : {hw.vram_gb:.1f} GB",
            f"│  CUDA    : {hw.cuda_version}",
        ]
    lines += [
        f"│  CPU     : {hw.cpu_cores} cores",
        f"│  RAM     : {hw.ram_gb:.1f} GB",
        f"│  Tier    : {hw.tier.upper()}",
        "├─ STT Config ────────────────────────────────────────",
        f"│  Model   : faster-whisper/{stt.model}",
        f"│  Compute : {stt.compute}",
        f"│  VAD gap : {stt.vad_min_silence_ms}ms silence → transcribe",
        "└─────────────────────────────────────────────────────",
    ]
    print("\n".join(lines))


# ── HELPERS ───────────────────────────────────────────────────────────────

def _get_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0