"""
jarvis_patch package v5 — Siri-Level Reliability Bundle
=========================================================
Import order:
  1. safety_validator    (no internal deps)
  2. semantic_corrector  (no internal deps)
  3. stt_patch           (imports 1+2)
  4. core_patch          (imports all)
  5. reliability_layer   (standalone — StateController, Metrics, etc.)

Checklist coverage per module:
  reliability_layer  → 1, 2, 3, 9, 11, 12, 13
  core_patch         → 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
  safety_validator   → 10
  semantic_corrector → 7
  stt_patch          → 7
  core.py            → all 14 (updated agent core)
"""

import logging
logger = logging.getLogger(__name__)

_SAFETY_VALIDATOR_OK   = False
_SEMANTIC_CORRECTOR_OK = False
_STT_V4_OK             = False
_CORE_PATCH_OK         = False
_RELIABILITY_LAYER_OK  = False

# ── 1. Safety validator ───────────────────────────────────────────────────
try:
    from jarvis_patch.safety_validator import (
        HardenedSafetyValidator,
        HardenedSandboxExecutor,
        patch_tool_builder_security,
    )
    _SAFETY_VALIDATOR_OK = True
except Exception as e:
    logger.warning(f"[patch] safety_validator unavailable: {e}")

# ── 2. Semantic corrector ─────────────────────────────────────────────────
try:
    from jarvis_patch.semantic_corrector import SemanticCorrector
    _SEMANTIC_CORRECTOR_OK = True
except Exception as e:
    logger.warning(f"[patch] semantic_corrector unavailable: {e}")

# ── 3. STT patch ──────────────────────────────────────────────────────────
try:
    from jarvis_patch.stt_patch import (
        EnhancedTranscriber,
        apply_stt_patch_to_instance,
        patch_voice_service_stt,
    )
    _STT_V4_OK = True
except Exception as e:
    logger.warning(f"[patch] stt_patch unavailable: {e}")

# ── 4. Core patch ─────────────────────────────────────────────────────────
try:
    from jarvis_patch.core_patch import apply_patches
    _CORE_PATCH_OK = True
except Exception as e:
    logger.warning(f"[patch] core_patch unavailable: {e}")

# ── 5. Reliability layer ──────────────────────────────────────────────────
try:
    from reliability_layer import (
        state_controller,
        metrics,
        bg_tracker,
        plan_validator,
        latency_enforcer,
        retry_orchestrator,
        verifier as execution_verifier,
        ErrorCategory,
        CategorizedError,
        categorize_error,
        BackgroundTaskTracker,
        MetricsCollector,
        StateController,
        PlanValidator,
        LatencyEnforcer,
        RetryOrchestrator,
        ExecutionVerifier,
    )
    _RELIABILITY_LAYER_OK = True
except Exception as e:
    logger.warning(f"[patch] reliability_layer unavailable: {e}")


def apply_all_patches():
    """
    Apply all patches in the correct order.
    Safe to call multiple times — idempotent.
    Returns dict of {component: status}.
    """
    results = {}

    # 1. Security hardening (Checklist 10)
    if _SAFETY_VALIDATOR_OK:
        try:
            patch_tool_builder_security()
            results["security"] = ""
        except Exception as e:
            results["security"] = f" {e}"

    # 2. Core patch (all checklists)
    if _CORE_PATCH_OK:
        try:
            apply_patches()
            results["core"] = ""
        except Exception as e:
            results["core"] = f" {e}"

    # 3. Reliability layer status
    results["reliability"] = "" if _RELIABILITY_LAYER_OK else " not loaded"

    # 4. STT status
    results["stt"] = "" if _STT_V4_OK else " not loaded"

    return results


def get_system_health() -> dict:
    """
    Return current system health snapshot.
    Useful for diagnostics and monitoring (Checklist 9).
    """
    health = {
        "patches": {
            "safety_validator":   _SAFETY_VALIDATOR_OK,
            "semantic_corrector": _SEMANTIC_CORRECTOR_OK,
            "stt_v4":             _STT_V4_OK,
            "core_patch":         _CORE_PATCH_OK,
            "reliability_layer":  _RELIABILITY_LAYER_OK,
        }
    }
    if _RELIABILITY_LAYER_OK:
        try:
            health["metrics"]       = metrics.summary()
            health["state"]         = state_controller.phase.value
            health["bg_tasks"]      = len(bg_tracker.active_tasks())
        except Exception:
            pass
    return health


__all__ = [
    # Patch functions
    "apply_patches",
    "apply_all_patches",
    "get_system_health",
    # STT
    "apply_stt_patch_to_instance",
    "patch_voice_service_stt",
    "EnhancedTranscriber",
    # Correctors
    "SemanticCorrector",
    # Safety
    "HardenedSafetyValidator",
    "HardenedSandboxExecutor",
    # Reliability layer
    "state_controller",
    "metrics",
    "bg_tracker",
    "plan_validator",
    "latency_enforcer",
    "retry_orchestrator",
    "execution_verifier",
    "ErrorCategory",
    "CategorizedError",
    "categorize_error",
    "BackgroundTaskTracker",
    "MetricsCollector",
    "StateController",
    "PlanValidator",
    "LatencyEnforcer",
    "RetryOrchestrator",
    "ExecutionVerifier",
]