import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from fast_router import HybridFastRouter
from screen_awareness import EventDrivenScreenDaemon
from core.goal_manager import GoalManager
from data_sync import HybridDataSyncManager
logger = logging.getLogger(__name__)

MAX_REFLECTION_ROUNDS = 3


class AgentState(Enum):
    IDLE              = "idle"
    IDLE_WITH_BG_TASK = "idle_with_bg_task"
    PROCESSING        = "processing"
    EXECUTING         = "executing"
    REFLECTING        = "reflecting"
    RESPONDING        = "responding"
    ERROR             = "error"


@dataclass
class AgentTurn:
    turn_id:           str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    raw_input:         str   = ""
    timestamp:         float = field(default_factory=time.time)
    intent:            Optional[Dict] = None
    intent_version:    int   = 0          # Checklist 3: versioning
    memory_context:    Optional[Dict] = None
    decision:          Optional[Dict] = None
    plan:              Optional[List[Dict]] = None
    plan_valid:        bool  = True        # Checklist 6
    execution_results: List[Dict] = field(default_factory=list)
    reflection_rounds: int   = 0
    response:          Optional[str] = None
    spoken_response:   Optional[str] = None
    requires_followup: bool  = False
    duration_ms:       float = 0.0
    success:           bool  = False
    error:             Optional[str] = None
    error_category:    Optional[str] = None   # Checklist 12


class JarvisAgentCore:
    """Central orchestrator. Single entry point for ALL voice commands."""

    def __init__(self, config: Dict):
        self.config = config
        self.state  = AgentState.IDLE
        self._tts_callback = None
        self._pending_intent: Optional[Dict] = None
        self._pending_intent_ts: float = 0.0       # Checklist 3: expiry
        self._pending_slots: List[str] = []
        self._loop_set  = False
        self._command_queue: List[str] = []
        self.goal_manager = GoalManager()
        from task_orchestrator import get_orchestrator
        self._orchestrator = get_orchestrator(groq_api_key=self.config.get("groq_api_key", ""))
        self._init_modules()
        logger.info("[CORE] Booting Native OS Daemons...")
        # Shared Fast Router (Zero Latency)
        self.fast_router = HybridFastRouter()
        # Unified Live Context dictionary
        self.live_os_context = {}
        # 1. Screen Awareness (Event-Driven)
        self.screen_daemon = EventDrivenScreenDaemon()
        self.screen_daemon.start(context_updater=lambda d: self.live_os_context.update(d))
        # Start Data Sync (Wait for executor to initialize registry)
        if hasattr(self, 'executor') and hasattr(self.executor, 'registry'):
            if hasattr(self.executor.registry, 'entity_resolver'):
                self.data_sync = HybridDataSyncManager(self.executor.registry.entity_resolver)
                self.data_sync.start_background_sync()

    # ── RELIABILITY SINGLETONS ────────────────────────────────────────────

    @property
    def _state_ctrl(self):
        if not hasattr(self, '__state_ctrl'):
            from reliability_layer import state_controller
            self.__state_ctrl = state_controller
        return self.__state_ctrl

    @property
    def _metrics(self):
        if not hasattr(self, '__metrics'):
            from reliability_layer import metrics
            self.__metrics = metrics
        return self.__metrics

    @property
    def _bg_tracker(self):
        if not hasattr(self, '__bg_tracker'):
            from reliability_layer import bg_tracker
            self.__bg_tracker = bg_tracker
        return self.__bg_tracker

    @property
    def _plan_validator(self):
        if not hasattr(self, '__plan_validator'):
            from reliability_layer import plan_validator
            self.__plan_validator = plan_validator
        return self.__plan_validator

    # ── INTENT EXPIRY (Checklist 3) ───────────────────────────────────────

    @property
    def _has_valid_pending_intent(self) -> bool:
        if not self._pending_intent:
            return False
        if time.time() - self._pending_intent_ts > 120.0:  # 2 min expiry
            logger.info("[CORE] Pending intent expired — clearing")
            self._pending_intent    = None
            self._pending_intent_ts = 0.0
            return False
        return True

    def _set_pending_intent(self, intent: Dict):
        self._pending_intent    = intent
        self._pending_intent_ts = time.time()

    def _clear_pending_intent(self):
        self._pending_intent    = None
        self._pending_intent_ts = 0.0

    # ── MODULES ───────────────────────────────────────────────────────────

    def _get_llm_client(self):
        if not hasattr(self, '_llm_client'):
            from groq import Groq
            self._llm_client = Groq(api_key=self.config.get("groq_api_key", ""))
        return self._llm_client

    def _summarize_for_speech(self, text: str) -> str:
        import re
        lines  = text.split("\n")
        parts  = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "Option" in line and line.startswith("**"):
                parts.append(line.replace("**", ""))
            elif "My Top Pick" in line:
                parts.append(line.replace("**", ""))
            elif line.startswith("Why this suits you:"):
                parts.append(line)
            elif line.startswith("Price:"):
                parts.append(line)
        result = " | ".join(parts[:10])
        if not result:
            clean  = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
            clean  = re.sub(r'https?://\S+', '', clean)
            result = clean[:400].strip()
        return result

    def _init_modules(self):
        from memory.store import MemoryStore
        from context.tracker import ContextTracker
        from planner.engine import PlanningEngine
        from executor.runner import ExecutionRunner
        from response.engine import ResponseEngine
        from security.validator import SecurityValidator
        from agent.decision import DecisionEngine
        from agent.reflection import ReflectionEngine
        from agent.background import BackgroundTaskManager
        from agent.clarifier import SmartClarifier
        from agent.tool_selector import ToolSelector
        from voice.intent_engine import IntentEngine
        from src.task_planner import TaskPlanner

        self.clarifier     = SmartClarifier()
        self.tool_selector = ToolSelector()
        self.task_planner  = TaskPlanner(groq_api_key=self.config.get("groq_api_key", ""))
        self.memory        = MemoryStore(self.config.get("memory", {}))
        self.context       = ContextTracker()
        self.planner       = PlanningEngine(self.config.get("planner", {}))
        self.executor      = ExecutionRunner(self.config.get("executor", {}))
        self.responder     = ResponseEngine(self.config.get("response", {}))
        self.security      = SecurityValidator(self.config.get("security", {}))
        self.decider       = DecisionEngine(self.config.get("decision", {}))
        self.reflector     = ReflectionEngine(
            self.config.get("reflection", {}),
            self.config.get("groq_api_key", ""),
        )
        self.task_manager  = BackgroundTaskManager(
            on_notify=self._on_background_task_complete
        )
        self.intent_engine = IntentEngine(self.config.get("groq_api_key", ""))

        # Smart open + page context tools
        from executor.runner_additions import SmartOpenTool, PageContextTool
        smart_tool = SmartOpenTool(self.config.get("executor", {}))
        page_tool  = PageContextTool(self.config.get("executor", {}))
        self._page_tool = page_tool
        page_tool.set_speak_fn(
            lambda t: self._tts_callback(t) if self._tts_callback else print(f"[Jarvis] {t}")
        )
        self._inject_tools({"smart_open": smart_tool, "page_context": page_tool})

        self.advisor = None
        logger.info(" JarvisAgentCore v3 initialized (Siri-Level Reliability)")

    def _inject_tools(self, tools: Dict):
        reg = self.executor.registry
        for name, tool in tools.items():
            for attr in ("tools", "_tools"):
                d = getattr(reg, attr, None)
                if isinstance(d, dict):
                    d[name] = tool
            if hasattr(reg, "get_tool"):
                _orig = reg.get_tool
                def _make_getter(n, t, orig):
                    return lambda nm: t if nm == n else orig(nm)
                reg.get_tool = _make_getter(name, tool, _orig)
            elif hasattr(reg, "_create_tool"):
                _orig = reg._create_tool
                def _make_creator(n, t, orig):
                    return lambda nm: t if nm == n else orig(nm)
                reg._create_tool = _make_creator(name, tool, _orig)

    def set_tts_callback(self, fn):
        self._tts_callback = fn
        if hasattr(self, '_page_tool') and self._page_tool is not None:
            self._page_tool.set_speak_fn(fn)

    def _on_background_task_complete(self, message: str):
        logger.info(f" BG task done: {message[:80]}")
        self._bg_tracker.check_timeouts()
        if self._tts_callback:
            self._tts_callback(message)
        # Checklist 11: return state to IDLE after BG completion
        try:
            from reliability_layer import SystemPhase
            if self._state_ctrl.phase == SystemPhase.IDLE_WITH_BG_TASK:
                self._state_ctrl.force_idle()
        except Exception:
            pass

    # ── MAIN AGENT LOOP ───────────────────────────────────────────────────

    async def process(self, raw_input: str, audio_features: Optional[Dict] = None) -> AgentTurn:
        """Full agent pipeline. Single public entry point."""
        turn  = AgentTurn(raw_input=raw_input)
        start = time.perf_counter()
        self.state = AgentState.PROCESSING

        # FIX BUG 4: ensure task_manager has event loop
        if not self._loop_set:
            try:
                loop = asyncio.get_event_loop()
                self.task_manager.set_loop(loop)
                self._loop_set = True
            except RuntimeError:
                pass

        # Checklist 11: timeout check on bg tasks
        self._bg_tracker.check_timeouts()

        # Checklist 2: watchdog — recover if stuck
        self._state_ctrl.watchdog_check()

        try:
            logger.info(f"\n{'─'*60}")
            logger.info(f"[{turn.turn_id}] ▶ '{raw_input}'")

            # ── 1. SECURITY ──────────────────────────────────────────────
            validation = await self.security.validate(raw_input)
            if not validation["allowed"]:
                return self._early_exit(turn, validation["user_message"], start)
            if validation.get("needs_confirmation"):
                return self._early_exit(turn, validation["confirmation_prompt"], start, success=True)

            # ── 2. CONTEXT SNAPSHOT ──────────────────────────────────────
            ctx_snapshot = self.context.snapshot()
            mem_hints    = await self.memory.get_context_hints(raw_input)
            try:
                from session_memory import session as _session
                ctx_snapshot["has_page_context"] = _session.has_page_context()
                ctx_snapshot["page_url"]         = _session._page_url
                ctx_snapshot["page_title"]       = _session._page_title
            except Exception:
                pass

            # ── 3. INTENT (Checklist 3: versioned, isolated) ─────────────
            turn.intent = await self.intent_engine.understand(
                raw_input, context=ctx_snapshot,
                memory_hints=mem_hints, audio_features=audio_features or {}
            )
            turn.intent_version = int(time.time() * 1000) & 0xFFFF

            # Checklist 9: log intent decision
            logger.info(
                f"[{turn.turn_id}] Intent: {turn.intent['intent']} "
                f"(conf={turn.intent['confidence']:.2f} v={turn.intent_version})"
            )
            self._metrics.record_turn(True, turn.intent.get("intent",""), 0.0)

            # ── 4. CLASSIFY: new command vs follow-up ─────────────────────
            _SYSTEM_INTENTS = {
                "open_app","close_app","close_tab","new_tab","open_website",
                "smart_open","play_media","pause_media","resume_media",
                "next_track","previous_track","shutdown","restart","lock",
                "take_screenshot","scroll","search_web","type_text","cancel",
                "read_page","page_summary","make_call","send_message",
                "deep_research","set_reminder","answer_question","system_action",
            }
            is_new_command = turn.intent.get("intent") in _SYSTEM_INTENTS

            # Checklist 3: pending intent with 2-min expiry guard
            if self._has_valid_pending_intent and not is_new_command and len(raw_input.strip()) > 1:
                return await self._handle_clarification_response(raw_input, turn, start)

            self._clear_pending_intent()
            if hasattr(self.task_planner, "cancel_active_session"):
                self.task_planner.cancel_active_session()

            # ── 5. PAGE CONTEXT ENFORCEMENT ──────────────────────────────
            current_intent = turn.intent.get("intent", "unknown")
            try:
                from session_memory import session
                if getattr(session, '_page_context', None):
                    if current_intent in ["quick_answer", "unknown"]:
                        turn.intent["intent"] = "answer_question"
                        current_intent = "answer_question"
            except Exception:
                pass

            # ── 6. ENTITY SANITIZATION (Checklist 3) ────────────────────
            _ALLOWED_SLOTS = {
                "play_media": ["song","platform","artist"],
                "guided_recommendation": ["query","budget","brand","category"],
                "search_web": ["query"],
                "answer_question": ["query"],
                "compose_message": ["contact","body","platform"],
                "make_call": ["contact","platform"],
                "open_app": ["name","app","app_name"],
                "close_app": ["name","app","app_name"],
                "system_action": ["action_type","setting","value","target"],
                "send_message": ["contact","body","platform"],
            }
            if "entities" in turn.intent and current_intent in _ALLOWED_SLOTS:
                allowed = _ALLOWED_SLOTS[current_intent]
                turn.intent["entities"] = {
                    k: v for k, v in turn.intent["entities"].items() if k in allowed
                }

            # ── 7. CANCEL ─────────────────────────────────────────────────
            if turn.intent.get("intent") == "cancel":
                self.task_planner.cancel_active_session()
                if self.advisor:
                    if getattr(self.advisor, 'current_task_id', None):
                        cancelled = self.advisor.cancel_current_research(self.task_manager)
                        if cancelled:
                            self.state = AgentState.IDLE
                            self._state_ctrl.force_idle()
                            return self._early_exit(turn, "Research cancelled, Sir.", start, success=True)
                    if self.advisor.has_active_session():
                        self.advisor.abandon()
                return self._early_exit(turn, "Cancelled, Sir.", start, success=True)

            # ── 8. ACTIVE ADVISOR ─────────────────────────────────────────
            if self.advisor and self.advisor.has_active_session():
                if is_new_command:
                    logger.info("[CORE] System command — abandoning Advisor")
                    self.advisor.abandon()
                    self._clear_pending_intent()
                    self.state = AgentState.IDLE
                    # Fall through to execute system command
                else:
                    return await self._handle_advisor_turn(turn, raw_input, start)

            # ── 9. NEW ADVISOR SESSION ────────────────────────────────────
            if turn.intent.get("intent") == "guided_recommendation":
                return await self._start_advisor_session(turn, raw_input, start)

            # ── 10. TASK PLANNER ──────────────────────────────────────────
            planner_result = await self.task_planner.process(
                user_input=raw_input,
                system_context=ctx_snapshot,
                intent_result=turn.intent,
            )
            from src.task_planner import PlanPhase
            if planner_result.phase == PlanPhase.CANCELLED:
                return self._early_exit(
                    turn, planner_result.clarification_question or "Cancelled, Sir.",
                    start, success=True
                )
            if planner_result.needs_clarification:
                if self._tts_callback:
                    self._tts_callback(planner_result.clarification_question)
                turn.response          = planner_result.clarification_question
                turn.spoken_response   = planner_result.clarification_question
                turn.success           = True
                turn.requires_followup = True
                turn.duration_ms       = (time.perf_counter() - start) * 1000
                self.state = AgentState.IDLE
                return turn
            if planner_result.ready_to_execute and planner_result.goal:
                turn.intent["entities"].update(planner_result.slots)
                turn.intent["intent"] = planner_result.execution_intent

            # ── 11. MEMORY RECALL ─────────────────────────────────────────
            turn.memory_context = await self.memory.recall(raw_input, turn.intent, ctx_snapshot)
            logger.info(f"[{turn.turn_id}] Recalled: {turn.memory_context.get('total_recalled',0)} items")

            # ── 12. IMPLICIT RESOLUTION ───────────────────────────────────
            turn.intent = self._resolve_implicit(turn.intent, ctx_snapshot)

            # ── 13. DECISION ENGINE ───────────────────────────────────────
            from agent.decision import Decision
            dr = self.decider.decide(turn.intent, ctx_snapshot, turn.memory_context)
            turn.decision = {"decision": dr.decision.value, "reason": dr.reason}
            logger.info(f"[{turn.turn_id}] Decision: {dr.decision.value} — {dr.reason}")

            if dr.decision == Decision.IGNORE:
                return self._early_exit(turn, None, start, success=True)
            if dr.decision == Decision.CLARIFY:
                self._set_pending_intent(turn.intent)
                turn.requires_followup = True
                return self._early_exit(turn, dr.clarification_question, start, success=True)
            if dr.decision == Decision.ANSWER:
                return await self._direct_answer(turn, dr, ctx_snapshot, start)
            if dr.decision == Decision.REFLECT:
                turn.intent = await self._reflect_intent(
                    turn.intent, dr.reflection_prompt or "", ctx_snapshot
                )
                dr2 = self.decider.decide(turn.intent, ctx_snapshot, turn.memory_context)
                if dr2.decision != Decision.EXECUTE:
                    turn.requires_followup = True
                    return self._early_exit(
                        turn, dr2.clarification_question or "Could you clarify, Sir?", start
                    )

            # ── 14. BACKGROUND ROUTE ──────────────────────────────────────
            bg_msg = await self._check_background_route(turn.intent)
            if bg_msg:
                return self._early_exit(turn, bg_msg, start, success=True)

            # ── 15. PLAN + VALIDATE (Checklist 6) ─────────────────────────
            self.state = AgentState.EXECUTING
            turn.plan  = await self.planner.create_plan(
                turn.intent, turn.memory_context, ctx_snapshot
            )
            logger.info(f"[{turn.turn_id}] Plan: {len(turn.plan)} steps")

            # Validate plan before execution (Checklist 6)
            plan_ok, plan_issues = self._plan_validator.validate(turn.plan, turn.intent)
            if not plan_ok:
                logger.warning(f"[{turn.turn_id}] Plan issues: {plan_issues}")
                turn.plan_valid = False

            for i, s in enumerate(turn.plan, 1):
                logger.info(f"   {i}. [{s.get('tool','')}] {s.get('description','')}")

            # ── 16. EXECUTE + REFLECT ──────────────────────────────────────
            turn = await self._execute_with_reflection(turn, ctx_snapshot)

            # ── 17. STORE + UPDATE ────────────────────────────────────────
            await self._store_turn(turn)
            await self.context.update_from_turn(turn)

            # ── 18. RESPONSE ──────────────────────────────────────────────
            self.state = AgentState.RESPONDING
            rd = await self.responder.generate(turn, ctx_snapshot, turn.memory_context)
            turn.response        = rd["full_response"]
            turn.spoken_response = rd["spoken_response"]

        except Exception as e:
            # Checklist 12: categorized errors + recovery
            try:
                from reliability_layer import categorize_error
                err = categorize_error(e)
                self._metrics.record_error(err.category)
                turn.error_category = err.category.value
                logger.error(f"[{turn.turn_id}] Agent error: {err}", exc_info=True)
            except Exception:
                logger.error(f"[{turn.turn_id}] Agent error: {e}", exc_info=True)
            turn.error           = str(e)
            turn.success         = False
            turn.spoken_response = "I encountered an error, Sir. Please try again."
            turn.response        = f"Error: {e}"
            # Checklist 12: never stuck
            self._state_ctrl.force_idle()

        finally:
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            logger.info(
                f"[{turn.turn_id}] {'' if turn.success else ''} "
                f"{turn.duration_ms:.0f}ms | refs={turn.reflection_rounds}"
            )

        return turn

    # ── ADVISOR HELPERS ───────────────────────────────────────────────────

    async def _handle_advisor_turn(self, turn: AgentTurn, raw_input: str, start: float) -> AgentTurn:
        async def _on_adv(res_text, spoken_text):
            await self.memory.store(
                key=f"recommendation_{turn.turn_id}", value=res_text[:500],
                category="fact", importance=0.7, source="advisor",
            )

        result = await self.advisor.start_or_continue(
            user_text=raw_input,
            speak_fn=self._tts_callback or (lambda t: None),
            task_manager=self.task_manager,
            on_complete=_on_adv,
        )

        if result == "[BACKGROUND_TASK_STARTED]":
            turn.response = turn.spoken_response = ""
            turn.success  = True
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE_WITH_BG_TASK
            self._state_ctrl.set_bg_task_count(1)
            return turn

        if result:
            from agent.advisor import GuidedAdvisor
            spoken = GuidedAdvisor.spoken_summary(result)
            for s in (self.advisor._sessions.values() if self.advisor else []):
                if s.result_text == result and s.result_spoken:
                    spoken = s.result_spoken
                    break
            await self.memory.store(
                key=f"recommendation_{turn.turn_id}", value=result[:500],
                category="fact", importance=0.7, source="advisor",
            )
            turn.response        = result
            turn.spoken_response = spoken
            turn.success         = True
        else:
            turn.response          = ""
            turn.spoken_response   = ""
            turn.success           = True
            turn.requires_followup = True

        turn.duration_ms = (time.perf_counter() - start) * 1000
        self.state = AgentState.IDLE
        return turn

    async def _start_advisor_session(self, turn: AgentTurn, raw_input: str, start: float) -> AgentTurn:
        if not self.advisor:
            from agent.advisor import GuidedAdvisor
            self.advisor = GuidedAdvisor(self.config.get("groq_api_key", ""))

        async def _on_adv_new(res_text, spoken_text):
            await self.memory.store(
                key=f"recommendation_{turn.turn_id}", value=res_text[:500],
                category="fact", importance=0.7, source="advisor",
            )

        result = await self.advisor.start_or_continue(
            user_text=raw_input,
            speak_fn=self._tts_callback or (lambda t: None),
            task_manager=self.task_manager,
            on_complete=_on_adv_new,
        )

        if result == "[BACKGROUND_TASK_STARTED]":
            turn.response = turn.spoken_response = ""
            turn.success  = True
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE_WITH_BG_TASK
            self._state_ctrl.set_bg_task_count(1)
            return turn

        if result:
            from agent.advisor import GuidedAdvisor
            spoken = GuidedAdvisor.spoken_summary(result)
            for s in (self.advisor._sessions.values() if self.advisor else []):
                if s.result_text == result and s.result_spoken:
                    spoken = s.result_spoken
                    break
            turn.response        = result
            turn.spoken_response = spoken
            turn.success         = True
        else:
            turn.response          = ""
            turn.spoken_response   = ""
            turn.success           = True
            turn.requires_followup = True

        turn.duration_ms = (time.perf_counter() - start) * 1000
        self.state = AgentState.IDLE
        return turn

    # ── EXECUTION + REFLECTION LOOP ───────────────────────────────────────

    async def _execute_with_reflection(self, turn: AgentTurn, ctx: Dict) -> AgentTurn:
        from agent.reflection import ReflectionContext, ReflectionMode

        current_plan = turn.plan
        if self.tool_selector:
            current_plan = self.tool_selector.select_for_plan(current_plan, ctx)
        if not current_plan:
            raise ValueError("Planner returned empty plan")

        _n_steps          = len(current_plan)
        _base_per_step    = 8.0
        _PIPELINE_TIMEOUT = min(max(_n_steps * _base_per_step, 10.0), 45.0)
        _pipeline_start   = time.perf_counter()

        all_results    = []
        reflection_log = []

        for round_num in range(MAX_REFLECTION_ROUNDS + 1):
            if round_num > 0:
                self.state = AgentState.REFLECTING
                logger.info(f"[{turn.turn_id}]  Reflection round {round_num}/{MAX_REFLECTION_ROUNDS}")

            elapsed = time.perf_counter() - _pipeline_start
            budget  = _PIPELINE_TIMEOUT - elapsed
            if budget <= 0:
                logger.warning(f"[{turn.turn_id}] ⏱ Pipeline budget exhausted after {elapsed:.1f}s")
                turn.execution_results = all_results
                turn.success           = any(r.get("success") for r in all_results)
                turn.spoken_response   = "That's taking too long, Sir. Please try again."
                return turn

            try:
                results = await asyncio.wait_for(
                    self.executor.run_plan(current_plan, turn.intent, ctx),
                    timeout=budget
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{turn.turn_id}] ⏱ run_plan timed out")
                turn.execution_results = all_results
                turn.success           = False
                turn.spoken_response   = "That's taking too long, Sir. Please try again."
                return turn

            all_results.extend(results)

            # Checklist 9: record tool metrics
            for r in results:
                tool = current_plan[r["step"]].get("tool", "") if r["step"] < len(current_plan) else ""
                if tool:
                    self.tool_selector.record_result(tool, r.get("success", False))
                    try:
                        self._metrics.record_tool(tool, r.get("success", False))
                    except Exception:
                        pass

            failed  = [i for i, r in enumerate(results) if not r.get("success")]
            success = [i for i, r in enumerate(results) if r.get("success")]

            if not failed:
                turn.execution_results = all_results
                turn.success           = True
                turn.reflection_rounds = round_num
                return turn

            if round_num >= MAX_REFLECTION_ROUNDS:
                turn.execution_results = all_results
                turn.success           = bool(success)
                turn.reflection_rounds = round_num
                return turn

            ref_ctx = ReflectionContext(
                original_intent=turn.intent,
                original_plan=current_plan,
                execution_results=results,
                failed_steps=failed,
                succeeded_steps=success,
                context=ctx,
                memory=turn.memory_context or {},
                reflection_depth=round_num,
                previous_reflections=reflection_log
            )
            reflection = await self.reflector.reflect(ref_ctx)
            reflection_log.append(reflection.diagnosis)

            if reflection.mode == ReflectionMode.ESCALATE:
                turn.execution_results = all_results
                turn.success           = False
                turn.spoken_response   = reflection.user_message
                turn.response          = reflection.user_message
                return turn

            if reflection.should_retry and reflection.new_plan:
                current_plan = reflection.new_plan
                continue

            break

        turn.execution_results = all_results
        turn.success = any(r.get("success") for r in all_results)
        return turn

    # ── DIRECT ANSWER ─────────────────────────────────────────────────────

    async def _direct_answer(self, turn: AgentTurn, dr, ctx: Dict, start: float) -> AgentTurn:
        intent_nm = turn.intent.get("intent","")
        entities  = turn.intent.get("entities",{})
        text      = turn.raw_input
        text_low  = text.lower()

        if any(w in text_low for w in ["task","background","running","status","research done","research finished"]):
            msg = self.task_manager.get_status_summary()
            turn.response = turn.spoken_response = msg
            turn.success  = True
            await self.context.update_from_turn(turn)
            turn.duration_ms = (time.perf_counter() - start) * 1000
            return turn

        if not hasattr(self, '_conversation_engine'):
            from agent.conversation import ConversationEngine
            self._conversation_engine = ConversationEngine()

        try:
            from session_memory import session as _session
            mem_facts_list = []
            if turn.memory_context:
                for cat in ("personal","preferences","facts"):
                    for item in (turn.memory_context.get(cat) or [])[:2]:
                        mem_facts_list.append(f"{item['key']}: {item['value']}")
            context_messages = _session.inject_into_messages(
                [{"role":"user","content":text}],
                user_name=self.config.get("user_name","Sir"),
                active_app=ctx.get("active_app","desktop"),
                memory_facts=mem_facts_list,
            )
        except Exception:
            context_messages = [{"role":"user","content":text}]

        conv_response = await self._conversation_engine.get_response(
            text=text,
            llm_client=self._get_llm_client(),
            use_llm=True,
            context_messages=context_messages,
        )

        if conv_response:
            turn.response = turn.spoken_response = conv_response
            turn.success  = True
            await self.context.update_from_turn(turn)
            turn.duration_ms = (time.perf_counter() - start) * 1000
            return turn

        if dr.direct_answer:
            turn.response = turn.spoken_response = dr.direct_answer
            turn.success  = True

        elif intent_nm == "express_preference":
            fact = entities.get("fact", entities.get("preference",""))
            subj = entities.get("subject","preference")
            if fact:
                await self.memory.store(key=f"preference_{subj}", value=fact,
                                        category="preference", importance=0.9, source="user_explicit")
                try:
                    from session_memory import session as _s
                    _s.update_profile(f"preference_{subj}", fact)
                except Exception:
                    pass
            turn.response = turn.spoken_response = "Noted, Sir. I'll keep that in mind."
            turn.success  = True

        elif intent_nm == "remember_fact":
            fact = entities.get("fact","")
            _saved = False
            try:
                from session_memory import session as _s
                import re as _re
                em = _re.search(r"(?:my\s+)?email(?:\s+(?:is|address|id)\s*(?:is)?)?\s+([\w.+-]+@[\w.-]+\.\w+)",
                                turn.raw_input, _re.IGNORECASE)
                if em:
                    _s.update_profile("email", em.group(1).strip())
                    await self.memory.store(key="email", value=em.group(1).strip(),
                                            category="personal", importance=1.0, source="user_explicit")
                    turn.response = turn.spoken_response = f"Got it, Sir. Saved your email as {em.group(1).strip()}."
                    turn.success  = True
                    _saved = True
                if not _saved:
                    pm = _re.search(r"(?:my\s+)?(?:phone|mobile|number)\s+(?:is\s+)?([\d\s+\-]{7,15})",
                                    turn.raw_input, _re.IGNORECASE)
                    if pm:
                        _s.update_profile("phone", pm.group(1).strip())
                        await self.memory.store(key="phone", value=pm.group(1).strip(),
                                                category="personal", importance=1.0, source="user_explicit")
                        turn.response = turn.spoken_response = "Saved your phone number, Sir."
                        turn.success  = True
                        _saved = True
            except Exception:
                pass
            if not _saved:
                if fact:
                    key = (fact.split("=")[0].strip().lower().replace(" ","_") if "=" in fact
                           else f"fact_{int(time.time())}")
                    val = fact.split("=",1)[1].strip() if "=" in fact else fact
                    await self.memory.store(key=key, value=val, category="fact",
                                            importance=0.7, source="user_explicit")
                turn.response = turn.spoken_response = "Remembered, Sir."
                turn.success  = True

        elif intent_nm == "recall_fact":
            query = entities.get("query", turn.raw_input)
            ans   = None
            try:
                from session_memory import session as _s
                p = _s.profile; q = query.lower()
                if any(w in q for w in ("email","email address","email id")):
                    ans = f"Your email is {p.email}, Sir." if p.email else None
                elif any(w in q for w in ("phone","mobile","number")):
                    ans = f"Your phone number is {p.phone}, Sir." if p.phone else None
                elif any(w in q for w in ("name","my name","who am i")):
                    ans = f"Your name is {p.name}, Sir." if p.name else None
            except Exception:
                pass
            if ans:
                turn.response = turn.spoken_response = ans
                turn.success  = True
            else:
                recalled = await self.memory.recall(query, turn.intent, ctx)
                items = recalled.get("personal", []) + recalled.get("preferences", []) + recalled.get("facts", [])
                if items:
                    parts = [f"{i['key']}: {i['value']}" for i in items[:4]]
                    msg = "Here's what I know, Sir: " + "; ".join(parts)
                else:
                    msg = "I don't have anything stored on that, Sir."
                turn.response = turn.spoken_response = msg
                turn.success = True

        elif intent_nm == "introduce_self":
            name = entities.get("name","")
            if name:
                await self.memory.store(key="user_name", value=name,
                                        category="personal", importance=1.0, source="user_explicit")
                try:
                    from session_memory import session as _s
                    _s.update_profile("name", name)
                except Exception:
                    pass
            turn.response = turn.spoken_response = (
                f"Pleased to meet you{', '+name if name else ''}, Sir. I'll remember you."
            )
            turn.success = True

        else:
            from executor.runner import AIBrainTool
            tool  = AIBrainTool(self.config.get("groq_api_key",""))
            query = entities.get("query", turn.raw_input)
            try:
                result = await tool.execute("answer_question", {"query":query}, turn.intent, ctx, [])
                turn.execution_results = [{"action":"answer_question","success":True,"output":result}]
                turn.success = True
                rd = await self.responder.generate(turn, ctx, turn.memory_context or {})
                turn.response        = rd["full_response"]
                turn.spoken_response = rd["spoken_response"]
            except Exception:
                turn.response = turn.spoken_response = "Unable to answer that, Sir."
                turn.success  = False

        await self.context.update_from_turn(turn)
        turn.duration_ms = (time.perf_counter() - start) * 1000
        return turn

    # ── BACKGROUND TASK ROUTING ───────────────────────────────────────────

    async def _check_background_route(self, intent: Dict) -> Optional[str]:
        nm = intent.get("intent","")
        en = intent.get("entities",{})

        if nm == "deep_research":
            topic = en.get("topic","the topic")
            from agent.background import background_research_task

            async def _do():
                tid = str(uuid.uuid4())[:8]
                return await background_research_task(
                    topic=topic, task_manager=self.task_manager,
                    task_id=tid, groq_api_key=self.config.get("groq_api_key","")
                )

            tracker_id = self._bg_tracker.register(f"Research: {topic[:30]}", timeout_s=120.0)
            task_id    = self.task_manager.submit(
                name=f"Research: {topic[:30]}", coro=_do(), notify=True
            )
            self._bg_tracker.start(tracker_id)
            self._state_ctrl.set_bg_task_count(1)
            return (f"I'll research '{topic}' in the background, Sir. "
                    f"Task ID: {task_id}. I'll notify you when done.")

        if nm == "set_reminder":
            msg  = en.get("reminder_text","Your reminder")
            secs = self._parse_time(en.get("time","5 minutes"))
            from agent.background import background_reminder_task

            async def _remind():
                tid = str(uuid.uuid4())[:8]
                return await background_reminder_task(
                    message=msg, delay_seconds=secs,
                    task_manager=self.task_manager, task_id=tid
                )

            self.task_manager.submit(name=f"Reminder: {msg[:30]}", coro=_remind(), notify=True)
            return f"Reminder set for '{msg}' in {secs:.0f} seconds, Sir."

        if nm == "quick_answer":
            q = en.get("query","").lower()
            if any(w in q for w in ["task","background","running","status"]):
                return self.task_manager.get_status_summary()

        return None

    def _parse_time(self, s: str) -> float:
        import re
        s = s.lower(); total = 0.0
        for pat, mul in [(r'(\d+)\s*h',3600),(r'(\d+)\s*m',60),(r'(\d+)\s*s',1)]:
            m = re.search(pat, s)
            if m:
                total += int(m.group(1)) * mul
        return total or 300

    # ── CLARIFICATION ─────────────────────────────────────────────────────

    async def _handle_clarification_response(
        self, text: str, turn: AgentTurn, start: float
    ) -> AgentTurn:
        pending  = self._pending_intent
        entities = dict(pending.get("entities",{}))
        text_low = text.lower().strip()

        if "spotify"    in text_low: entities["platform"] = "spotify"
        elif "youtube"  in text_low: entities["platform"] = "youtube"
        elif "discord"  in text_low: entities["platform"] = "discord"
        elif "whatsapp" in text_low: entities["platform"] = "whatsapp"
        else:
            for slot in ["song","contact","text","query","reminder_text","topic"]:
                if not entities.get(slot):
                    entities[slot] = text.strip()
                    break

        pending["entities"]   = entities
        pending["confidence"] = 0.85
        self._clear_pending_intent()
        logger.info(f"Clarification filled: {entities}")

        turn.intent  = pending
        ctx_snapshot = self.context.snapshot()
        turn.memory_context = await self.memory.recall(text, pending, ctx_snapshot)

        from agent.decision import Decision
        dr = self.decider.decide(pending, ctx_snapshot, turn.memory_context)

        if dr.decision == Decision.EXECUTE:
            bg_msg = await self._check_background_route(pending)
            if bg_msg:
                return self._early_exit(turn, bg_msg, start, success=True)
            turn.plan  = await self.planner.create_plan(pending, turn.memory_context, ctx_snapshot)
            self.state = AgentState.EXECUTING
            turn = await self._execute_with_reflection(turn, ctx_snapshot)
            await self._store_turn(turn)
            await self.context.update_from_turn(turn)
            rd = await self.responder.generate(turn, ctx_snapshot, turn.memory_context)
            turn.response        = rd["full_response"]
            turn.spoken_response = rd["spoken_response"]
        elif dr.decision == Decision.ANSWER:
            turn = await self._direct_answer(turn, dr, ctx_snapshot, start)
        else:
            self._set_pending_intent(pending)
            turn.requires_followup = True
            return self._early_exit(
                turn, dr.clarification_question or "Could you be more specific, Sir?",
                start, success=True
            )

        turn.duration_ms = (time.perf_counter() - start) * 1000
        return turn

    # ── IMPLICIT REFERENCES ───────────────────────────────────────────────

    def _resolve_implicit(self, intent: Dict, ctx: Dict) -> Dict:
        entities = intent.get("entities",{})
        text     = intent.get("original_text","").lower()
        nm       = intent.get("intent","")
        implicit = {"it","that","this","again","same"}
        if not (implicit & set(text.split())):
            return intent
        if nm in ("close_app","open_app","focus_app") and not entities.get("app") and ctx.get("last_app"):
            entities["app"] = ctx["last_app"]
        if nm == "play_media":
            if not entities.get("song") and ctx.get("last_song"):
                entities["song"] = ctx["last_song"]
            if not entities.get("platform") and ctx.get("last_platform"):
                entities["platform"] = ctx["last_platform"]
        if nm == "open_website" and not entities.get("url") and ctx.get("last_url"):
            entities["url"] = ctx["last_url"]
        if nm == "send_message":
            if not entities.get("contact") and ctx.get("last_contact"):
                entities["contact"] = ctx["last_contact"]
            if not entities.get("platform") and ctx.get("last_message_platform"):
                entities["platform"] = ctx["last_message_platform"]
        if not any(entities.values()) and ctx.get("last_entity"):
            entities["target"] = ctx["last_entity"]
        intent["entities"] = entities
        return intent

    # ── INTENT REFLECTION ─────────────────────────────────────────────────

    async def _reflect_intent(self, intent: Dict, prompt: str, ctx: Dict) -> Dict:
        new_intent = await self.intent_engine.understand(
            text=intent.get("original_text",""),
            context={**ctx, "reflection_hint": prompt},
            memory_hints={}, audio_features={}
        )
        new_intent["reflection_applied"] = True
        logger.info(
            f"Reflected: {intent.get('intent')}→{new_intent.get('intent')} "
            f"({intent.get('confidence',0):.2f}→{new_intent.get('confidence',0):.2f})"
        )
        return new_intent

    # ── MEMORY STORAGE ────────────────────────────────────────────────────

    async def _store_turn(self, turn: AgentTurn):
        intent = turn.intent or {}
        nm     = intent.get("intent","")
        en     = intent.get("entities",{})

        if nm == "express_preference" and en.get("fact"):
            await self.memory.store(
                key=f"preference_{en.get('subject','general')}", value=en["fact"],
                category="preference", importance=0.9
            )
        if nm == "introduce_self" and en.get("name"):
            await self.memory.store(key="user_name", value=en["name"],
                                    category="personal", importance=1.0)
        if nm == "play_media" and turn.success and en.get("platform"):
            await self.memory.store(key="preferred_music_platform", value=en["platform"],
                                    category="preference", importance=0.6)
        if turn.success and nm not in ("greet","thank","cancel","ignore"):
            await self.memory.store(key=f"task_{turn.turn_id}", value=f"{nm}: {en}",
                                    category="task", importance=0.2)

        if turn.execution_results:
            for result in turn.execution_results:
                if not result.get("success"):
                    continue
                tool_name = result.get("tool","")
                output    = result.get("output",{})
                if not isinstance(output, dict):
                    continue
                if tool_name == "page_context" or result.get("action") in ("read_page","page_summary"):
                    page_text  = output.get("page_text", output.get("raw",""))
                    page_url   = output.get("page_url", output.get("url",""))
                    page_title = output.get("page_title","")
                    if page_text and len(page_text) > 50:
                        try:
                            from session_memory import session as _s
                            _s.set_page_context(page_text, url=page_url, title=page_title)
                        except Exception:
                            pass
                    if page_url:
                        summary = output.get("full_summary", output.get("spoken",""))
                        if summary:
                            safe_key = f"page_{page_url.replace('https://','').replace('http://','').replace('/','_')[:50]}"
                            await self.memory.store(key=safe_key, value=summary[:500],
                                                    category="fact", importance=0.6, source="page_context")

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _early_exit(self, turn, message, start, success=False):
        turn.response        = message
        turn.spoken_response = message
        turn.success         = success
        turn.duration_ms     = (time.perf_counter() - start) * 1000
        self.state           = AgentState.IDLE
        return turn
