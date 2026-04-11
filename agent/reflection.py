"""
REFLECTION & REPLANNING ENGINE
================================
When execution fails or produces unexpected results, Jarvis doesn't stop.
It reflects on what went wrong and creates a new plan.

Three reflection modes:
  DIAGNOSE     → Identify WHY a step failed
  REPLAN       → Create alternative approach using different tools
  DECOMPOSE    → Break a complex failed step into smaller ones
  ESCALATE     → Tell user it can't be done (last resort)

Reflection is triggered by:
  - Any step failure after retries exhausted
  - Verification failure (action ran but didn't work)
  - Partial plan completion (some steps succeeded, others failed)
  - Low-quality output (e.g. research returned nothing useful)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ReflectionMode(Enum):
    DIAGNOSE   = "diagnose"    # Figure out why it failed
    REPLAN     = "replan"      # Try a completely different approach
    DECOMPOSE  = "decompose"   # Break the failed step into sub-steps
    ESCALATE   = "escalate"    # Inform user it's not possible


@dataclass
class ReflectionResult:
    mode: ReflectionMode
    diagnosis: str
    new_plan: Optional[List[Dict]] = None
    user_message: Optional[str] = None
    should_retry: bool = False
    max_reflection_depth: int = 3  # Prevent infinite loops


@dataclass
class ReflectionContext:
    """Everything the reflector needs to reason about."""
    original_intent: Dict
    original_plan: List[Dict]
    execution_results: List[Dict]
    failed_steps: List[int]
    succeeded_steps: List[int]
    context: Dict
    memory: Dict
    reflection_depth: int = 0
    previous_reflections: List[str] = field(default_factory=list)


class ReflectionEngine:
    """
    Post-execution reflection and replanning.

    Called from agent/core.py when execution has failures.
    Returns either a new plan to execute, or a user message explaining failure.
    """

    def __init__(self, config: Dict, groq_api_key: str):
        self.config = config
        self.groq_api_key = groq_api_key
        self._client = None

        # Failure patterns → known fixes
        self._known_fixes = self._build_known_fixes()

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self.groq_api_key)
        return self._client

    async def reflect(self, ctx: ReflectionContext) -> ReflectionResult:
        """
        Main reflection method. Called when plan execution has failures.
        """
        max_depth = getattr(ctx, "max_reflection_depth", 3)
        if ctx.reflection_depth >= max_depth:
            return ReflectionResult(
                mode=ReflectionMode.ESCALATE,
                diagnosis=f"Max reflection depth reached",
                user_message=(
                    "I've tried multiple approaches, Sir, but I'm unable to "
                    "complete this task. Please try a different approach or "
                    "do it manually."
                )
            )

        logger.info(
            f"🔄 Reflecting (depth={ctx.reflection_depth}) | "
            f"failed={ctx.failed_steps} | succeeded={ctx.succeeded_steps}"
        )

        # ── STEP 1: DIAGNOSE ───────────────────────────────────────────────
        diagnosis = self._diagnose(ctx)
        logger.info(f"   Diagnosis: {diagnosis}")

        # ── STEP 2: CHECK KNOWN FIXES ─────────────────────────────────────
        known_fix = self._try_known_fix(ctx)
        if known_fix:
            logger.info(f"   Known fix found: {known_fix['description']}")
            return ReflectionResult(
                mode=ReflectionMode.REPLAN,
                diagnosis=diagnosis,
                new_plan=known_fix["steps"],
                should_retry=True
            )

        # ── STEP 3: PARTIAL SUCCESS? → continue from where we left off ─────
        partial = self._handle_partial_success(ctx)
        if partial:
            return partial

        # ── STEP 4: LLM-DRIVEN REPLANNING ─────────────────────────────────
        return await self._llm_replan(ctx, diagnosis)

    # ── DIAGNOSIS ─────────────────────────────────────────────────────────

    def _diagnose(self, ctx: ReflectionContext) -> str:
        """Diagnose the failure without LLM — fast, deterministic."""
        failed_actions = [
            ctx.original_plan[i].get("action", "unknown")
            for i in ctx.failed_steps
            if i < len(ctx.original_plan)
        ]
        errors = []
        for i in ctx.failed_steps:
            if i < len(ctx.execution_results):
                err = ctx.execution_results[i].get("error", "")
                if err:
                    errors.append(err)

        parts = []
        if failed_actions:
            parts.append(f"Failed actions: {', '.join(failed_actions)}")
        if errors:
            parts.append(f"Errors: {'; '.join(errors[:3])}")

        # Classify error type
        error_text = " ".join(errors).lower()
        if "process" in error_text or "not found" in error_text:
            parts.append("→ App likely not installed")
        elif "timeout" in error_text or "connection" in error_text:
            parts.append("→ Network or timeout issue")
        elif "permission" in error_text or "access" in error_text:
            parts.append("→ Permission denied")
        elif "not running" in error_text:
            parts.append("→ Target app not running")

        return " | ".join(parts) if parts else "Unknown failure"

    # ── KNOWN FIXES ────────────────────────────────────────────────────────

    def _build_known_fixes(self) -> Dict[str, Dict]:
        """
        Hardcoded fix strategies for common failure patterns.
        These run before LLM — faster and more reliable.
        """
        return {
            # App not found → try web version
            "app_not_found": {
                "description": "App not installed — open web version",
                "trigger": lambda ctx: any(
                    "not found" in (ctx.execution_results[i].get("error", "")).lower()
                    for i in ctx.failed_steps if i < len(ctx.execution_results)
                ),
                "build_steps": lambda ctx: self._web_fallback_steps(ctx)
            },

            # Spotify failed → try YouTube
            "spotify_failed": {
                "description": "Spotify failed — fallback to YouTube",
                "trigger": lambda ctx: (
                    ctx.original_intent.get("entities", {}).get("platform") == "spotify"
                    and ctx.failed_steps
                ),
                "build_steps": lambda ctx: [{
                    "action": "play_media",
                    "tool": "media_controller",
                    "params": {
                        "name": ctx.original_intent["entities"].get("song", ""),
                        "platform": "youtube"
                    },
                    "description": f"Play on YouTube (Spotify fallback)",
                    "retry_policy": {"max_retries": 1},
                    "verify": None,
                    "expected_duration_ms": 3000
                }]
            },

            # Type_text failed → notepad not open
            "type_no_window": {
                "description": "Typing failed — open notepad first",
                "trigger": lambda ctx: any(
                    ctx.original_plan[i].get("action") == "type_text"
                    for i in ctx.failed_steps if i < len(ctx.original_plan)
                ),
                "build_steps": lambda ctx: [
                    {
                        "action": "open_app",
                        "tool": "app_launcher",
                        "params": {"name": "notepad"},
                        "description": "Open Notepad first",
                        "retry_policy": {"max_retries": 1},
                        "verify": {"type": "process_running", "name": "notepad"},
                        "expected_duration_ms": 1500
                    },
                    {
                        "action": "type_text",
                        "tool": "keyboard",
                        "params": {
                            "text": ctx.original_intent.get("entities", {}).get("text", "")
                        },
                        "description": "Type text",
                        "retry_policy": {"max_retries": 1},
                        "verify": None,
                        "expected_duration_ms": 500,
                        "depends_on": [0]
                    }
                ]
            },

            # Browser tab close failed → focus browser first
            "close_tab_no_browser": {
                "description": "Tab close failed — bring browser to focus first",
                "trigger": lambda ctx: any(
                    ctx.original_plan[i].get("action") == "close_tab"
                    for i in ctx.failed_steps if i < len(ctx.original_plan)
                ),
                "build_steps": lambda ctx: [
                    {
                        "action": "focus_app",
                        "tool": "app_launcher",
                        "params": {"name": "chrome"},
                        "description": "Focus browser",
                        "retry_policy": {"max_retries": 1},
                        "verify": None,
                        "expected_duration_ms": 1000
                    },
                    {
                        "action": "close_tab",
                        "tool": "browser",
                        "params": {},
                        "description": "Close tab",
                        "retry_policy": {"max_retries": 0},
                        "verify": None,
                        "expected_duration_ms": 300,
                        "depends_on": [0]
                    }
                ]
            }
        }

    def _try_known_fix(self, ctx: ReflectionContext) -> Optional[Dict]:
        """Try each known fix in order, return first match."""
        for fix_name, fix in self._known_fixes.items():
            try:
                if fix["trigger"](ctx):
                    steps = fix["build_steps"](ctx)
                    if steps:
                        return {"description": fix["description"], "steps": steps}
            except Exception as e:
                logger.debug(f"Fix '{fix_name}' check failed: {e}")
        return None

    def _web_fallback_steps(self, ctx: ReflectionContext) -> List[Dict]:
        """Generic web fallback for any app that wasn't found."""
        import urllib.parse
        entities = ctx.original_intent.get("entities", {})
        app = entities.get("app", "")
        url = f"https://www.{app.lower()}.com" if app else None

        if not url:
            return []

        return [{
            "action": "open_website",
            "tool": "browser",
            "params": {"url": url},
            "description": f"Open {app} web version (app not found)",
            "retry_policy": {"max_retries": 1},
            "verify": {"type": "browser_opened"},
            "expected_duration_ms": 2000
        }]

    # ── PARTIAL SUCCESS ────────────────────────────────────────────────────

    def _handle_partial_success(self, ctx: ReflectionContext) -> Optional[ReflectionResult]:
        """
        If some steps succeeded and some failed, figure out what to retry.
        Only retry the failed steps (not the whole plan).
        """
        if not ctx.succeeded_steps or not ctx.failed_steps:
            return None  # All failed or all succeeded — not partial

        # Build a plan from only the failed steps
        retry_steps = []
        for i in ctx.failed_steps:
            if i < len(ctx.original_plan):
                step = dict(ctx.original_plan[i])
                # Remove dependency on steps that already succeeded
                deps = step.get("depends_on", [])
                step["depends_on"] = [d for d in deps if d not in ctx.succeeded_steps]
                retry_steps.append(step)

        if not retry_steps:
            return None

        logger.info(f"   Partial success — retrying {len(retry_steps)} failed steps")
        return ReflectionResult(
            mode=ReflectionMode.REPLAN,
            diagnosis="Partial execution — retrying failed steps only",
            new_plan=retry_steps,
            should_retry=True
        )

    # ── LLM REPLANNING ─────────────────────────────────────────────────────

    async def _llm_replan(
        self, ctx: ReflectionContext, diagnosis: str
    ) -> ReflectionResult:
        """
        Use LLM to reason about the failure and create a new plan.
        Falls back to ESCALATE if LLM can't help.
        """
        import json

        intent = ctx.original_intent
        failed_descriptions = [
            ctx.original_plan[i].get("description", f"step {i}")
            for i in ctx.failed_steps
            if i < len(ctx.original_plan)
        ]
        prev_reflections = "\n".join(ctx.previous_reflections[-2:]) if ctx.previous_reflections else "None"

        prompt = f"""You are a planning agent that must recover from an execution failure.

ORIGINAL INTENT:
{json.dumps(intent, indent=2)}

FAILED STEPS:
{json.dumps(failed_descriptions, indent=2)}

DIAGNOSIS:
{diagnosis}

PREVIOUS REFLECTION ATTEMPTS:
{prev_reflections}

AVAILABLE TOOLS:
- app_launcher: open_app, close_app
- browser: open_website, search_web, close_tab, new_tab, scroll  
- media_controller: play_media, pause_media, resume_media, next_track
- keyboard: type_text, save_file, scroll
- web_navigator: search_web, fetch_and_parse, synthesize_research
- ai_brain: answer_question, synthesize_research
- system: take_screenshot, lock, shutdown, restart
- memory: store_memory, recall_memory

TASK: Create an alternative plan to achieve the same goal.
If it's truly impossible, respond with "ESCALATE" and explain why.

Respond ONLY with valid JSON:
{{
  "decision": "replan" OR "escalate",
  "reasoning": "brief explanation",
  "steps": [
    {{
      "action": "action_name",
      "tool": "tool_name",
      "params": {{}},
      "description": "what this does",
      "retry_policy": {{"max_retries": 1}},
      "verify": null,
      "expected_duration_ms": 1000
    }}
  ],
  "user_message": "message to user if escalating"
}}"""

        try:
            loop = asyncio.get_event_loop()
            client = self._get_client()

            def _call():
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=800,
                    response_format={"type": "json_object"}
                )

            response = await loop.run_in_executor(None, _call)
            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)

            if data.get("decision") == "escalate":
                return ReflectionResult(
                    mode=ReflectionMode.ESCALATE,
                    diagnosis=diagnosis,
                    user_message=data.get("user_message", "Unable to complete this task, Sir.")
                )

            new_plan = data.get("steps", [])
            if not new_plan:
                raise ValueError("LLM returned empty plan")

            return ReflectionResult(
                mode=ReflectionMode.REPLAN,
                diagnosis=diagnosis,
                new_plan=new_plan,
                should_retry=True
            )

        except Exception as e:
            logger.error(f"LLM replanning failed: {e}")
            return ReflectionResult(
                mode=ReflectionMode.ESCALATE,
                diagnosis=f"{diagnosis} | Replanning failed: {e}",
                user_message=(
                    "I was unable to find an alternative approach, Sir. "
                    "This task may require manual intervention."
                )
            )
