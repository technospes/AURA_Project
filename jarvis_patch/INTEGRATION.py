"""
JARVIS v4 UPGRADE — INTEGRATION GUIDE
======================================
Complete instructions for integrating all 5 parts of the upgrade.

Files provided:
  jarvis_patch/
    __init__.py           ← Updated (auto-imports everything)
    semantic_corrector.py ← NEW (PART 1)
    stt_patch_v4.py       ← NEW (PARTS 2 + 4 + 5, replaces stt_patch.py)
    safety_validator.py   ← NEW (PART 3, replaces SafetyValidator in tool_builder.py)
    core_patch.py         ← EXISTING (unchanged — v4 upgrade is purely additive)
    tool_builder.py       ← EXISTING (hardened at runtime via safety_validator.py)
    stt_patch.py          ← EXISTING (kept as fallback if v4 fails)

═══════════════════════════════════════════════════════════════════════════
STEP 1 — Copy new files
═══════════════════════════════════════════════════════════════════════════

Copy these 3 new files into your E:\\Jarvis\\jarvis_patch\\ directory:
  semantic_corrector.py
  stt_patch_v4.py
  safety_validator.py
  __init__.py  (replace existing)


═══════════════════════════════════════════════════════════════════════════
STEP 2 — Update main.py (2 line changes)
═══════════════════════════════════════════════════════════════════════════

FIND this block in voice_process_main():

    _patches_applied = False
    try:
        from jarvis_patch.core_patch import apply_patches
        apply_patches()
        _patches_applied = True
    except Exception as _pe:
        logger.error(f"Patch failed: {_pe}", exc_info=True)

REPLACE with:

    _patches_applied = False
    try:
        from jarvis_patch import apply_all_patches
        results = apply_all_patches()
        for k, v in results.items():
            logger.info(f"[Patch] {k}: {v}")
        _patches_applied = True
    except Exception as _pe:
        logger.error(f"Patch failed: {_pe}", exc_info=True)


FIND this block (STT application, near bottom):

    if _patches_applied:
        try:
            from jarvis_patch.stt_patch import apply_stt_patch_to_instance
            apply_stt_patch_to_instance(service)
        except Exception as _se:
            logger.warning(f"STT patch failed: {_se}")

REPLACE with:

    if _patches_applied:
        try:
            from jarvis_patch import apply_stt_patch_to_instance
            apply_stt_patch_to_instance(service)
            logger.info(" STT v4 (multi-pass + semantic correction) active")
        except Exception as _se:
            logger.warning(f"STT patch failed: {_se}")


═══════════════════════════════════════════════════════════════════════════
STEP 3 — No changes to any other file
═══════════════════════════════════════════════════════════════════════════

core_patch.py, tool_builder.py, service.py, core.py — NO CHANGES.
Everything is monkey-patched at runtime.


═══════════════════════════════════════════════════════════════════════════
VERIFICATION — Expected startup log output
═══════════════════════════════════════════════════════════════════════════

[Patch] security: 
[Patch] core: 
[PATCH]  JarvisAgentCore.process patched
[PATCH]  SystemActionTool registered in ToolRegistry
[PATCH]  PlanningEngine.system_action plan builder added
[PATCH]  IntentEngine patterns patched + INTENT_CATALOGUE updated
[PATCH]  TaskPlanner INTENT_MAP + PLANNER_ELIGIBLE expanded
[PATCH]  DecisionEngine.system_action → EXECUTE
[PATCH]  All Jarvis patches applied successfully
[Security]  ToolBuilder security hardened
[STT v4]  SemanticCorrector ready
[STT]  small.en@cuda
 STT v4 (multi-pass + semantic correction) active


═══════════════════════════════════════════════════════════════════════════
HOW EACH PART WORKS AT RUNTIME
═══════════════════════════════════════════════════════════════════════════

PART 1 — SemanticCorrector:
  Triggered when: conf < 0.85 OR repeated words detected
  LLM: llama-3.1-8b-instant (fast, ~300ms)
  Cached: yes (256 entries, 30min TTL)

  "I shoot I should buy a laptop"
  → artifacts detected: "I shoot I"
  → SemanticCorrector called
  → "I should buy a laptop"

  "forty k phone"
  → regex fixes: "40000 phone"   ← phonetic correction handles this
  → conf=0.92 → no LLM needed

PART 2 — Multi-pass selection:
  Pass 1: preprocessed audio (always)
  Pass 2: original audio (only if pass-1 conf < 0.60)
  Score: conf×0.65 + language_quality×0.35
  Winner: higher score

  If user speaks quietly and preprocessed audio loses signal:
  → Pass 1 conf=0.40 (low)
  → Pass 2 triggered on original audio
  → Pass 2 conf=0.78 (higher)
  → Pass 2 wins → better transcript

PART 3 — Hardened SafetyValidator:
  OLD: blocklist-based (missed getattr chains)
  NEW: strict allowlist + dunder blocker + depth limit + obfuscation scan

  BLOCKED NOW (was allowed before):
  - getattr(obj, '__import__')  ← bypassed old check
  - setattr(obj, '__class__', ...)
  - globals()['__builtins__']['eval']
  - chr(111)+chr(115)  ← "os" via chr encoding
  - base64.b64decode('...') + exec(...)

PART 4 — Confidence-aware routing:
  conf >= 0.85  →  Accept (phonetic correction only, NO LLM)
  0.50 < conf < 0.85  →  SemanticCorrector (1 LLM call)
  conf <= 0.50  →  Pass-2 retry, THEN SemanticCorrector

PART 5 — Early intent detection (streaming partial):
  The recorder's silence detection is already adaptive (v3).
  Early termination when intent is clear from first few words.
  Implemented via the artifact pattern check — if the first 3 words
  clearly form a command ("open spotify play"), accept early.
  (Full streaming STT would require faster-whisper streaming API
   which is not yet stable — this is the practical near-term approach.)


═══════════════════════════════════════════════════════════════════════════
DEPENDENCIES CHECK
═══════════════════════════════════════════════════════════════════════════

Required (already installed in your env):
  faster-whisper  
  numpy           
  groq            
  sounddevice     

Optional (improves quality):
  scipy           → pip install scipy   (better bandpass filter)
  soundfile       → pip install soundfile (faster WAV writing)

The semantic_corrector uses llama-3.1-8b-instant via the existing
GROQ_API_KEY — no new API keys needed.
"""

if __name__ == "__main__":
    print(__doc__)
