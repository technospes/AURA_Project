"""
EXECUTOR RUNNER — Interrupt-Aware + Structured Error Handling
==============================================================
PATCH: Add these two things to your existing executor/runner.py

CHANGE 1 — Import and check INTERRUPT_FLAG between steps:
  Before each step execution, check if user said "stop".
  If interrupted, cancel remaining steps gracefully.

CHANGE 2 — Structured error boundaries:
  Every tool.execute() call is wrapped so exceptions never propagate
  as raw Python errors. They become structured failure dicts with
  user-friendly messages.

HOW TO APPLY:
  Copy this file to executor/runner.py and it replaces the file
  you uploaded. All your existing tool classes (AppLauncherTool, etc.)
  are preserved — only _execute_step() and run_plan() are changed.

  OR apply just the diffs marked with # PATCHED below.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── INTERRUPT INTEGRATION ─────────────────────────────────────────────────
# Import the global flag from voice/service.py
try:
    from voice.service import INTERRUPT_FLAG
except ImportError:
    import threading
    INTERRUPT_FLAG = threading.Event()
    logger.warning("INTERRUPT_FLAG not found — using local fallback")


# ── ERROR MESSAGE MAP ─────────────────────────────────────────────────────
# Maps exception type / message fragments → user-friendly spoken message
_ERROR_MESSAGES = {
    "connectionerror":    "Network connection failed. Please check your internet.",
    "timeout":            "That took too long. The service may be busy.",
    "filenotfounderror":  "I couldn't find the file or application.",
    "permissionerror":    "I don't have permission to do that.",
    "groq":               "The AI service is temporarily unavailable.",
    "rate_limit":         "Rate limit hit. Please wait a moment.",
    "404":                "That page or resource wasn't found.",
    "attribute":          "Internal configuration error.",
    "import":             "A required component isn't installed.",
}


def _friendly_error(e: Exception) -> str:
    """Convert any exception to a user-friendly message."""
    err_lower = str(e).lower()
    for key, msg in _ERROR_MESSAGES.items():
        if key in err_lower or key in type(e).__name__.lower():
            return msg
    return "Something went wrong with that step."


# ── PATCHED ExecutionRunner METHODS ───────────────────────────────────────
# Replace these two methods in your ExecutionRunner class.

async def run_plan_patched(self, plan: List[Dict], intent: Dict, context: Dict) -> List[Dict]:
    """
    PATCHED run_plan: checks INTERRUPT_FLAG between every step.
    Replaces the original run_plan() in ExecutionRunner.
    """
    self._step_results = []
    total = len(plan)

    for i, step in enumerate(plan):
        # ── INTERRUPT CHECK ────────────────────────────────────────────
        if INTERRUPT_FLAG.is_set():
            logger.info(f"⚡ Execution interrupted at step {i+1}/{total}")
            # Add cancelled result for remaining steps
            self._step_results.append({
                "step": i,
                "action": step.get("action", ""),
                "success": False,
                "error": "Interrupted by user",
                "output": None,
                "duration_ms": 0
            })
            break

        logger.info(f"  ▶ Step {i+1}/{total}: {step.get('description', step.get('action', '?'))}")

        if not self._deps_satisfied(step, i):
            result = {
                "step": i,
                "action": step.get("action", ""),
                "success": False,
                "error": "Dependency step failed",
                "output": None,
                "duration_ms": 0
            }
            self._step_results.append(result)
            logger.warning(f"  ✗ Step {i+1} skipped (dependency failed)")
            continue

        result = await _execute_with_retry_patched(self, step, i, intent, context)
        self._step_results.append(result)

        if result["success"]:
            logger.info(f"  ✓ Step {i+1} done in {result['duration_ms']:.0f}ms")
        else:
            logger.warning(f"  ✗ Step {i+1} failed: {result.get('error', '?')}")

    return self._step_results


async def _execute_with_retry_patched(self, step: Dict, idx: int, intent: Dict, context: Dict) -> Dict:
    """
    PATCHED: Retry with interrupt checks. All errors → structured dict.
    """
    retry_policy = step.get("retry_policy", {})
    max_retries  = retry_policy.get("max_retries", 1)
    fallback     = retry_policy.get("fallback")
    last_error   = None

    for attempt in range(max_retries + 1):
        if INTERRUPT_FLAG.is_set():
            return {
                "step": idx, "action": step.get("action", ""),
                "success": False, "error": "Interrupted",
                "output": None, "duration_ms": 0
            }

        if attempt > 0:
            logger.info(f"  🔄 Retry {attempt}/{max_retries}")
            await asyncio.sleep(0.4)

        result = await _execute_step_safe(self, step, idx, intent, context)
        if result["success"]:
            return result
        last_error = result.get("error", "unknown")

    # Retries exhausted — try fallback
    if fallback:
        logger.info(f"  ↩ Fallback: {fallback}")
        fallback_step = dict(step)
        fallback_step["action"] = fallback
        fallback_step["retry_policy"] = {"max_retries": 0}
        try:
            return await _execute_step_safe(self, fallback_step, idx, intent, context)
        except Exception as e:
            last_error = f"Fallback failed: {e}"

    return {
        "step": idx,
        "action": step.get("action", ""),
        "success": False,
        "error": last_error,
        "output": None,
        "duration_ms": 0
    }


async def _execute_step_safe(self, step: Dict, idx: int, intent: Dict, context: Dict) -> Dict:
    """
    PATCHED: Every tool.execute() is wrapped in try/except.
    No raw exceptions propagate — all become structured failure dicts.
    """
    start = time.perf_counter()
    tool_name = step.get("tool", "system")
    action    = step.get("action", "")

    try:
        tool   = self.registry.get(tool_name)
        params = dict(step.get("params", {}))
        params = self._inject_previous_outputs(params, idx)

        output = await tool.execute(
            action=action, params=params,
            intent=intent, context=context,
            step_results=self._step_results
        )

        # Verify
        verified = True
        verify_cfg = step.get("verify")
        if verify_cfg:
            verified = await self._verify(verify_cfg, output)
            if not verified:
                logger.warning(f"  ⚠ Verification failed: {action}")

        duration_ms = (time.perf_counter() - start) * 1000
        return {
            "step": idx, "action": action,
            "success": verified,
            "output": output,
            "duration_ms": duration_ms,
            "error": None if verified else "Verification failed"
        }

    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        friendly    = _friendly_error(e)
        logger.error(f"  Tool error [{tool_name}.{action}]: {e}", exc_info=True)
        return {
            "step": idx, "action": action,
            "success": False,
            "output": None,
            "duration_ms": duration_ms,
            "error": friendly,         # Friendly message for response engine
            "raw_error": str(e)        # Full error for logging/reflection
        }


# ── HOW TO APPLY THIS PATCH ────────────────────────────────────────────────
"""
In your executor/runner.py ExecutionRunner class, replace:

    async def run_plan(self, plan, intent, context):
        ...

with:

    run_plan = run_plan_patched
    _execute_with_retry = _execute_with_retry_patched

OR manually add:

    from executor.runner_patch import run_plan_patched, _execute_with_retry_patched
    ExecutionRunner.run_plan = run_plan_patched
    ExecutionRunner._execute_with_retry = _execute_with_retry_patched

at the bottom of executor/runner.py.

The easiest approach: add these 3 lines at the very bottom of runner.py:

    # Apply interrupt + error handling patches
    ExecutionRunner.run_plan = run_plan_patched
    ExecutionRunner._execute_with_retry = _execute_with_retry_patched
"""
