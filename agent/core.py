"""
JARVIS AGENT CORE v2 — Full Loop with Decision + Reflection
============================================================
FIX LOG:
  - BUG 1 (PERFORMANCE): IntentEngine was instantiated with
    `IntentEngine(...)` on EVERY call to process(). This rebuilt the
    Groq client and recompiled all regex patterns on every turn.
    Fixed: IntentEngine is created once in _init_modules() and reused.

  - BUG 2 (NAMING CONFLICT): The project has TWO files called engine.py —
    one in planner/ (PlanningEngine) and one in response/ (ResponseEngine).
    Python's import system resolves these by package path, so
    `from planner.engine import PlanningEngine` and
    `from response.engine import ResponseEngine` are unambiguous as long
    as each folder has an __init__.py. Make sure both folders have empty
    __init__.py files (see setup note at bottom).

  - BUG 3: _handle_clarification_response called self.process(text)
    recursively but the merged pending_intent was never actually passed
    into the new process() call — it was cleared (self._pending_intent = None)
    before process() could read it. Fixed: pass the merged intent directly
    to _execute_merged_intent() instead of re-running full process().

  - BUG 4: set_loop() was never called on BackgroundTaskManager, so
    task_manager.submit() always hit the "No event loop" warning and
    tasks were silently dropped. Fixed: loop is set during process() on
    first call.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_REFLECTION_ROUNDS = 3


class AgentState(Enum):
    IDLE       = "idle"
    PROCESSING = "processing"
    EXECUTING  = "executing"
    REFLECTING = "reflecting"
    RESPONDING = "responding"
    ERROR      = "error"


@dataclass
class AgentTurn:
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    raw_input: str = ""
    timestamp: float = field(default_factory=time.time)

    intent: Optional[Dict] = None
    memory_context: Optional[Dict] = None
    decision: Optional[Dict] = None
    plan: Optional[List[Dict]] = None
    execution_results: List[Dict] = field(default_factory=list)
    reflection_rounds: int = 0
    response: Optional[str] = None
    spoken_response: Optional[str] = None

    duration_ms: float = 0.0
    success: bool = False
    error: Optional[str] = None


class JarvisAgentCore:
    """Central orchestrator. Single entry point for ALL voice commands."""

    def __init__(self, config: Dict):
        self.config = config
        self.state = AgentState.IDLE
        self._tts_callback = None
        self._pending_intent: Optional[Dict] = None
        self._pending_slots: List[str] = []
        self._loop_set = False
        self._command_queue: List[str] = []
        self._init_modules()

    def _get_llm_client(self):
        """Get or create LLM client for conversation."""
        if not hasattr(self, '_llm_client'):
            from groq import Groq
            self._llm_client = Groq(api_key=self.config.get("groq_api_key", ""))
        return self._llm_client
    
    def _summarize_for_speech(self, text: str) -> str:
        """Convert a rich recommendation report to a concise spoken version."""
        import re
        lines = text.split("\n")
        parts = []
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
            clean = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
            clean = re.sub(r'https?://\S+', '', clean)
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
        self.clarifier = SmartClarifier()
        self.tool_selector = ToolSelector()
        from voice.intent_engine import IntentEngine

        self.memory       = MemoryStore(self.config.get("memory", {}))
        self.context      = ContextTracker()
        self.planner      = PlanningEngine(self.config.get("planner", {}))
        self.executor     = ExecutionRunner(self.config.get("executor", {}))
        self.responder    = ResponseEngine(self.config.get("response", {}))
        self.security     = SecurityValidator(self.config.get("security", {}))
        self.decider      = DecisionEngine(self.config.get("decision", {}))
        self.reflector    = ReflectionEngine(
            self.config.get("reflection", {}),
            self.config.get("groq_api_key", "")
        )
        self.task_manager = BackgroundTaskManager(
            on_notify=self._on_background_task_complete
        )
        # FIX BUG 1: single shared IntentEngine instance
        self.intent_engine = IntentEngine(self.config.get("groq_api_key", ""))
        
        # ── NEW TOOLS ──────────────────────────────────────────────────────
        from executor.runner_additions import SmartOpenTool, PageContextTool
        
        smart_tool = SmartOpenTool(self.config.get("executor", {}))
        page_tool = PageContextTool(self.config.get("executor", {}))
        
        # Safely bind the TTS callback
        page_tool.set_speak_fn(lambda t: self._tts_callback(t) if self._tts_callback else print(f"[Jarvis] {t}"))
        
        # ── THE FIX: Safely inject tools into the ToolRegistry object ──
        # 1. Try to inject into the internal dictionary if it exists
        if hasattr(self.executor.registry, "tools") and isinstance(self.executor.registry.tools, dict):
            self.executor.registry.tools["smart_open"] = smart_tool
            self.executor.registry.tools["page_context"] = page_tool
        elif hasattr(self.executor.registry, "_tools") and isinstance(self.executor.registry._tools, dict):
            self.executor.registry._tools["smart_open"] = smart_tool
            self.executor.registry._tools["page_context"] = page_tool
            
        # 2. Monkey-patch the retrieval methods to guarantee they are found
        if hasattr(self.executor.registry, "get_tool"):
            _orig_get = self.executor.registry.get_tool
            self.executor.registry.get_tool = lambda name: smart_tool if name == "smart_open" else (page_tool if name == "page_context" else _orig_get(name))
        elif hasattr(self.executor.registry, "_create_tool"):
            _orig_create = self.executor.registry._create_tool
            self.executor.registry._create_tool = lambda name: smart_tool if name == "smart_open" else (page_tool if name == "page_context" else _orig_create(name))

        # GuidedAdvisor — lazy init on first recommendation
        self.advisor = None
        logger.info("🧠 JarvisAgentCore v3 initialized (with advisor + page context)")

    def set_tts_callback(self, fn):
        self._tts_callback = fn

    def _on_background_task_complete(self, message: str):
        logger.info(f"📢 BG task done: {message[:80]}")
        if self._tts_callback:
            self._tts_callback(message)

    # ── MAIN AGENT LOOP ───────────────────────────────────────────────────

    async def process(self, raw_input: str, audio_features: Optional[Dict] = None) -> AgentTurn:
        """Full agent pipeline. Single public entry point."""
        turn = AgentTurn(raw_input=raw_input)
        start = time.perf_counter()
        self.state = AgentState.PROCESSING

        # FIX BUG 4: give task_manager the running loop on first call
        if not self._loop_set:
            try:
                loop = asyncio.get_event_loop()
                self.task_manager.set_loop(loop)
                self._loop_set = True
            except RuntimeError:
                pass

        try:
            logger.info(f"\n{'─'*60}")
            logger.info(f"[{turn.turn_id}] ▶ '{raw_input}'")

            # ── 1. SECURITY GATE ──────────────────────────────────────────
            validation = await self.security.validate(raw_input)
            if not validation["allowed"]:
                return self._early_exit(turn, validation["user_message"], start)
            if validation.get("needs_confirmation"):
                return self._early_exit(turn, validation["confirmation_prompt"], start, success=True)

            # ── 2. INTENT UNDERSTANDING ───────────────────────────────────
            # Moved up so we can detect if the user is giving a brand new command
            ctx_snapshot = self.context.snapshot()
            mem_hints    = await self.memory.get_context_hints(raw_input)

            turn.intent = await self.intent_engine.understand(
                raw_input, context=ctx_snapshot,
                memory_hints=mem_hints, audio_features=audio_features or {}
            )
            
            # ── 3. PENDING CLARIFICATION ESCAPE HATCH ─────────────────────
            # If Jarvis asked a question, but you respond with a brand NEW command 
            # (like "close this tab"), abandon the question and execute the command!
            is_new_command = (
                turn.intent.get("intent") not in ("unknown", "conversation", "quick_answer") 
                and turn.intent.get("confidence", 0.0) >= 0.80
            )

            if self._pending_intent and not is_new_command and len(raw_input.strip()) > 1:
                return await self._handle_clarification_response(raw_input, turn, start)
                
            self._pending_intent = None # Clear the trap!

            logger.info(
                f"[{turn.turn_id}] Intent: {turn.intent['intent']} "
                f"(conf={turn.intent['confidence']:.2f})"
            )
            
            from agent.clarifier import split_commands
            commands = split_commands(raw_input)
            if len(commands) > 1:
                logger.info(f"Multi-command: {len(commands)} commands")
                self._command_queue = commands[1:] 

            # ── 4. MEMORY RECALL ──────────────────────────────────────────
            turn.memory_context = await self.memory.recall(
                raw_input, turn.intent, ctx_snapshot
            )
            logger.info(f"[{turn.turn_id}] Recalled: {turn.memory_context.get('total_recalled',0)} items")

            # ── 5. RESOLVE IMPLICIT REFERENCES ───────────────────────────
            turn.intent = self._resolve_implicit(turn.intent, ctx_snapshot)

            # ── CANCEL: also abandon any active advisor session ────────────────
            if turn.intent.get("intent") == "cancel":
                if self.advisor and self.advisor.has_active_session():
                    self.advisor.abandon()
                return self._early_exit(turn, "Cancelled, Sir.", start, success=True)

            # ── ACTIVE ADVISOR SESSION: user is answering a question ───────────
            if self.advisor and self.advisor.has_active_session():
                result = await self.advisor.start_or_continue(
                    user_text=raw_input,
                    speak_fn=self._tts_callback or (lambda t: print(f"[Jarvis] {t}"))
                )
                if result:
                    # Recommendation complete — speak summary, store full text
                    from agent.advisor import GuidedAdvisor
                    spoken = GuidedAdvisor.spoken_summary(result)
                    
                    # Store the recommendation in memory
                    await self.memory.store(
                        key=f"recommendation_{turn.turn_id}",
                        value=result[:500],
                        category="fact",
                        importance=0.7,
                        source="advisor"
                    )
                    
                    if self._tts_callback:
                        self._tts_callback(spoken)
                    
                    turn.response        = result
                    turn.spoken_response = spoken
                    turn.success         = True
                else:
                    # Still mid-conversation — advisor already spoke the next question
                    turn.response        = ""
                    turn.spoken_response = ""
                    turn.success         = True
                
                turn.duration_ms = (time.perf_counter() - start) * 1000
                self.state       = AgentState.IDLE
                return turn

            # ── NEW RECOMMENDATION SESSION ─────────────────────────────────────
            if turn.intent.get("intent") == "guided_recommendation":
                if not self.advisor:
                    from agent.advisor import GuidedAdvisor
                    self.advisor = GuidedAdvisor(self.config.get("groq_api_key", ""))
                
                result = await self.advisor.start_or_continue(
                    user_text=raw_input,
                    speak_fn=self._tts_callback or (lambda t: print(f"[Jarvis] {t}"))
                )
                if result:
                    from agent.advisor import GuidedAdvisor
                    spoken = GuidedAdvisor.spoken_summary(result)
                    if self._tts_callback:
                        self._tts_callback(spoken)
                    turn.response        = result
                    turn.spoken_response = spoken
                    turn.success         = True
                else:
                    turn.response        = ""
                    turn.spoken_response = ""
                    turn.success         = True
                
                turn.duration_ms = (time.perf_counter() - start) * 1000
                self.state       = AgentState.IDLE
                return turn

            # ── 6. DECISION ENGINE ────────────────────────────────────────
            from agent.decision import Decision
            dr = self.decider.decide(turn.intent, ctx_snapshot, turn.memory_context)
            turn.decision = {"decision": dr.decision.value, "reason": dr.reason}
            logger.info(f"[{turn.turn_id}] Decision: {dr.decision.value} — {dr.reason}")

            if dr.decision == Decision.IGNORE:
                return self._early_exit(turn, None, start, success=True)

            if dr.decision == Decision.CLARIFY:
                self._pending_intent = turn.intent
                return self._early_exit(turn, dr.clarification_question, start, success=True)

            if dr.decision == Decision.ANSWER:
                return await self._direct_answer(turn, dr, ctx_snapshot, start)

            if dr.decision == Decision.REFLECT:
                turn.intent = await self._reflect_intent(
                    turn.intent, dr.reflection_prompt or "", ctx_snapshot
                )
                dr2 = self.decider.decide(turn.intent, ctx_snapshot, turn.memory_context)
                if dr2.decision != Decision.EXECUTE:
                    return self._early_exit(
                        turn, dr2.clarification_question or "Could you clarify, Sir?", start
                    )

            # ── 7. BACKGROUND ROUTE? ──────────────────────────────────────
            bg_msg = await self._check_background_route(turn.intent)
            if bg_msg:
                return self._early_exit(turn, bg_msg, start, success=True)

            # ── 8. PLANNING ───────────────────────────────────────────────
            turn.plan = await self.planner.create_plan(
                turn.intent, turn.memory_context, ctx_snapshot
            )
            logger.info(f"[{turn.turn_id}] Plan: {len(turn.plan)} steps")
            for i, s in enumerate(turn.plan, 1):
                logger.info(f"   {i}. [{s['tool']}] {s['description']}")

            # ── 9. EXECUTE + REFLECTION LOOP ──────────────────────────────
            self.state = AgentState.EXECUTING
            turn = await self._execute_with_reflection(turn, ctx_snapshot)

            # ── 10. STORE + CONTEXT UPDATE ────────────────────────────────
            await self._store_turn(turn)
            await self.context.update_from_turn(turn)

            # ── 11. RESPONSE GENERATION ───────────────────────────────────
            self.state = AgentState.RESPONDING
            rd = await self.responder.generate(turn, ctx_snapshot, turn.memory_context)
            turn.response        = rd["full_response"]
            turn.spoken_response = rd["spoken_response"]

        except Exception as e:
            logger.error(f"[{turn.turn_id}] Agent error: {e}", exc_info=True)
            turn.error           = str(e)
            turn.success         = False
            turn.spoken_response = "I encountered an error, Sir. Please try again."
            turn.response        = f"Error: {e}"

        finally:
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            logger.info(
                f"[{turn.turn_id}] {'✓' if turn.success else '✗'} "
                f"{turn.duration_ms:.0f}ms | refs={turn.reflection_rounds}"
            )

        return turn

    # ── EXECUTION + REFLECTION LOOP ───────────────────────────────────────

    async def _execute_with_reflection(self, turn: AgentTurn, ctx: Dict) -> AgentTurn:
        from agent.reflection import ReflectionContext, ReflectionMode

        # ✅ USE EXISTING PLAN (DO NOT recreate)
        current_plan = turn.plan

        # ✅ Apply tool selector ONCE
        if self.tool_selector:
            current_plan = self.tool_selector.select_for_plan(current_plan, ctx)

        if not current_plan:
            raise ValueError("Planner returned empty plan")

        all_results = []
        reflection_log = []

        for round_num in range(MAX_REFLECTION_ROUNDS + 1):

            if round_num > 0:
                self.state = AgentState.REFLECTING
                logger.info(f"[{turn.turn_id}] 🔄 Reflection round {round_num}/{MAX_REFLECTION_ROUNDS}")

            # ✅ EXECUTE ONLY HERE (single execution per round)
            results = await self.executor.run_plan(current_plan, turn.intent, ctx)
            all_results.extend(results)

            # Track tool success/failure
            for r in results:
                tool = current_plan[r["step"]].get("tool", "") if r["step"] < len(current_plan) else ""
                if tool:
                    self.tool_selector.record_result(tool, r.get("success", False))

            failed  = [i for i, r in enumerate(results) if not r.get("success")]
            success = [i for i, r in enumerate(results) if r.get("success")]

            # ✅ SUCCESS CASE
            if not failed:
                turn.execution_results = all_results
                turn.success = True
                turn.reflection_rounds = round_num
                return turn

            # ✅ MAX RETRIES HIT
            if round_num >= MAX_REFLECTION_ROUNDS:
                turn.execution_results = all_results
                turn.success = bool(success)
                turn.reflection_rounds = round_num
                return turn

            # ✅ REFLECTION
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
                turn.success = False
                turn.spoken_response = reflection.user_message
                turn.response = reflection.user_message
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
        intent_nm = turn.intent.get("intent", "")
        entities  = turn.intent.get("entities", {})
        text      = turn.raw_input
        text_low  = text.lower()

        # ── 1. BACKGROUND TASK STATUS CHECK (Highest Priority) ──
        if any(w in text_low for w in ["task", "background", "running", "status", "research done", "research finished"]):
            msg = self.task_manager.get_status_summary()
            turn.response = turn.spoken_response = msg
            turn.success = True
            await self.context.update_from_turn(turn)
            turn.duration_ms = (time.perf_counter() - start) * 1000
            return turn

        # ── 2. CONVERSATION ENGINE ──
        if not hasattr(self, '_conversation_engine'):
            from agent.conversation import ConversationEngine
            self._conversation_engine = ConversationEngine()

        if not hasattr(self, '_conversation_engine'):
            from agent.conversation import ConversationEngine
            self._conversation_engine = ConversationEngine()
        
        # Check if this is a conversational query
        conv_response = await self._conversation_engine.get_response(
            text=text,
            llm_client=self._get_llm_client(),
            use_llm=True
        )
        
        if conv_response:
            turn.response = turn.spoken_response = conv_response
            turn.success = True
            await self.context.update_from_turn(turn)
            turn.duration_ms = (time.perf_counter() - start) * 1000
            return turn

        if dr.direct_answer:
            turn.response = turn.spoken_response = dr.direct_answer
            turn.success = True

        elif intent_nm == "express_preference":
            fact = entities.get("fact", entities.get("preference", ""))
            subj = entities.get("subject", "preference")
            if fact:
                await self.memory.store(
                    key=f"preference_{subj}", value=fact,
                    category="preference", importance=0.9, source="user_explicit"
                )
            turn.response = turn.spoken_response = "Noted, Sir. I'll keep that in mind."
            turn.success = True

        elif intent_nm == "remember_fact":
            fact = entities.get("fact", "")
            if fact:
                key = fact.split("=")[0].strip().lower().replace(" ", "_") if "=" in fact else f"fact_{int(time.time())}"
                val = fact.split("=", 1)[1].strip() if "=" in fact else fact
                await self.memory.store(key=key, value=val, category="fact",
                                        importance=0.7, source="user_explicit")
            turn.response = turn.spoken_response = "Remembered, Sir."
            turn.success = True

        elif intent_nm == "recall_fact":
            query = entities.get("query", turn.raw_input)
            recalled = await self.memory.recall(query, turn.intent, ctx)
            items = recalled.get("personal", []) + recalled.get("preferences", []) + recalled.get("facts", [])
            if items:
                s = "; ".join(f"{i['key']}: {i['value']}" for i in items[:4])
                msg = f"Here's what I know, Sir: {s}"
            else:
                msg = "I don't have anything stored on that, Sir."
            turn.response = turn.spoken_response = msg
            turn.success = True

        elif intent_nm == "introduce_self":
            name = entities.get("name", "")
            if name:
                await self.memory.store(key="user_name", value=name,
                                        category="personal", importance=1.0,
                                        source="user_explicit")
            msg = f"Pleased to meet you{', ' + name if name else ''}, Sir. I'll remember you."
            turn.response = turn.spoken_response = msg
            turn.success = True

        else:
            from executor.runner import AIBrainTool
            tool = AIBrainTool(self.config.get("groq_api_key", ""))
            query = entities.get("query", turn.raw_input)
            try:
                result = await tool.execute("answer_question", {"query": query},
                                            turn.intent, ctx, [])
                turn.execution_results = [{"action": "answer_question", "success": True, "output": result}]
                turn.success = True
                rd = await self.responder.generate(turn, ctx, turn.memory_context or {})
                turn.response        = rd["full_response"]
                turn.spoken_response = rd["spoken_response"]
            except Exception:
                turn.response = turn.spoken_response = "Unable to answer that, Sir."
                turn.success = False

        await self.context.update_from_turn(turn)
        turn.duration_ms = (time.perf_counter() - start) * 1000
        return turn

    # ── BACKGROUND TASK ROUTING ───────────────────────────────────────────

    async def _check_background_route(self, intent: Dict) -> Optional[str]:
        nm = intent.get("intent", "")
        en = intent.get("entities", {})

        if nm == "deep_research":
            topic = en.get("topic", "the topic")
            from agent.background import background_research_task

            async def _do():
                task_id_inner = str(uuid.uuid4())[:8]
                return await background_research_task(
                    topic=topic, task_manager=self.task_manager,
                    task_id=task_id_inner,
                    groq_api_key=self.config.get("groq_api_key", "")
                )

            task_id = self.task_manager.submit(
                name=f"Research: {topic[:30]}", coro=_do(), notify=True
            )
            return (
                f"I'll research '{topic}' in the background, Sir. "
                f"Task ID: {task_id}. I'll notify you when it's done."
            )

        if nm == "set_reminder":
            msg  = en.get("reminder_text", "Your reminder")
            secs = self._parse_time(en.get("time", "5 minutes"))
            from agent.background import background_reminder_task

            async def _remind():
                task_id_inner = str(uuid.uuid4())[:8]
                return await background_reminder_task(
                    message=msg, delay_seconds=secs,
                    task_manager=self.task_manager, task_id=task_id_inner
                )

            self.task_manager.submit(name=f"Reminder: {msg[:30]}", coro=_remind(), notify=True)
            return f"Reminder set for '{msg}' in {secs:.0f} seconds, Sir."

        if nm == "quick_answer":
            q = en.get("query", "").lower()
            if any(w in q for w in ["task", "background", "running", "status"]):
                return self.task_manager.get_status_summary()

        return None

    def _parse_time(self, s: str) -> float:
        import re
        s = s.lower()
        total = 0.0
        for pat, mul in [(r'(\d+)\s*h', 3600), (r'(\d+)\s*m', 60), (r'(\d+)\s*s', 1)]:
            m = re.search(pat, s)
            if m:
                total += int(m.group(1)) * mul
        return total or 300

    # ── CLARIFICATION ─────────────────────────────────────────────────────

    async def _handle_clarification_response(
        self, text: str, turn: AgentTurn, start: float
    ) -> AgentTurn:
        """
        FIX BUG 3: The original code merged the pending intent then called
        self.process(text) again. But self._pending_intent was already set to
        None before process() ran, so the merged intent was lost. Instead,
        we now execute the filled intent directly.
        """
        pending  = self._pending_intent
        entities = dict(pending.get("entities", {}))
        text_low = text.lower().strip()

        # Slot-fill heuristics
        if "spotify" in text_low:     entities["platform"] = "spotify"
        elif "youtube" in text_low:   entities["platform"] = "youtube"
        elif "discord" in text_low:   entities["platform"] = "discord"
        elif "whatsapp" in text_low:  entities["platform"] = "whatsapp"
        else:
            for slot in ["song", "contact", "text", "query", "reminder_text", "topic"]:
                if not entities.get(slot):
                    entities[slot] = text.strip()
                    break

        pending["entities"]   = entities
        pending["confidence"] = 0.85
        # Clear BEFORE executing so re-entrant calls don't loop
        self._pending_intent = None

        logger.info(f"Clarification filled: {entities}")

        # Execute the now-complete intent directly
        turn.intent = pending
        ctx_snapshot = self.context.snapshot()
        turn.memory_context = await self.memory.recall(text, pending, ctx_snapshot)

        from agent.decision import Decision
        dr = self.decider.decide(pending, ctx_snapshot, turn.memory_context)

        if dr.decision == Decision.EXECUTE:
            bg_msg = await self._check_background_route(pending)
            if bg_msg:
                return self._early_exit(turn, bg_msg, start, success=True)

            turn.plan = await self.planner.create_plan(pending, turn.memory_context, ctx_snapshot)
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
            # Still missing info — ask again
            self._pending_intent = pending
            return self._early_exit(
                turn,
                dr.clarification_question or "Could you be more specific, Sir?",
                start, success=True
            )

        turn.duration_ms = (time.perf_counter() - start) * 1000
        return turn

    # ── IMPLICIT REFERENCE RESOLUTION ─────────────────────────────────────

    def _resolve_implicit(self, intent: Dict, ctx: Dict) -> Dict:
        entities  = intent.get("entities", {})
        text      = intent.get("original_text", "").lower()
        nm        = intent.get("intent", "")
        implicit  = {"it", "that", "this", "again", "same"}

        if not (implicit & set(text.split())):
            return intent

        if nm in ("close_app", "open_app", "focus_app") and not entities.get("app") and ctx.get("last_app"):
            entities["app"] = ctx["last_app"]
            logger.info(f"Implicit → app: {ctx['last_app']}")

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
        # FIX BUG 1: use shared intent_engine instance
        new_intent = await self.intent_engine.understand(
            text=intent.get("original_text", ""),
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
        nm     = intent.get("intent", "")
        en     = intent.get("entities", {})

        if nm == "express_preference" and en.get("fact"):
            await self.memory.store(
                key=f"preference_{en.get('subject','general')}", value=en["fact"],
                category="preference", importance=0.9
            )

        if nm == "introduce_self" and en.get("name"):
            await self.memory.store(
                key="user_name", value=en["name"],
                category="personal", importance=1.0
            )

        if nm == "play_media" and turn.success and en.get("platform"):
            await self.memory.store(
                key="preferred_music_platform", value=en["platform"],
                category="preference", importance=0.6
            )

        if turn.success and nm not in ("greet", "thank", "cancel", "ignore"):
            await self.memory.store(
                key=f"task_{turn.turn_id}",
                value=f"{nm}: {en}",
                category="task", importance=0.2
            )

        # ── STORE PAGE SUMMARIES FROM PAGE_CONTEXT TOOL ───────────────────
        if turn.execution_results:
            for result in turn.execution_results:
                if result.get("tool") == "page_context" and result.get("success"):
                    output = result.get("output", {})
                    if isinstance(output, dict):
                        url = output.get("url", "")
                        summary = output.get("summary", "")
                        if url and summary:
                            await self.memory.store(
                                key=f"page_{url.replace('https://', '').replace('http://', '').replace('/', '_')[:50]}",
                                value=summary[:500],
                                category="fact",
                                importance=0.5,
                                source="page_context"
                            )
                            logger.info(f"📄 Stored page summary for: {url[:50]}")
        if nm == "page_summary" and turn.execution_results:
            for r in turn.execution_results:
                out = r.get("output", {})
                if isinstance(out, dict) and "full_summary" in out:
                        await self.memory.store(
                            key=f"page_summary_{int(time.time())}",
                            value=out["full_summary"][:400],
                            category="fact",
                            importance=0.6,
                            source="page_context"
                        )
    # ── HELPERS ───────────────────────────────────────────────────────────

    def _early_exit(self, turn, message, start, success=False):
        turn.response        = message
        turn.spoken_response = message
        turn.success         = success
        turn.duration_ms     = (time.perf_counter() - start) * 1000
        self.state           = AgentState.IDLE
        return turn


# ── SETUP NOTE ────────────────────────────────────────────────────────────
#
# The project has TWO files named engine.py:
#   planner/engine.py   → PlanningEngine
#   response/engine.py  → ResponseEngine
#
# These are disambiguated by Python package imports ONLY if every folder
# has an __init__.py. If any folder is missing __init__.py, Python may
# import the wrong engine.py silently.
#
# Run this once to create all required __init__.py files:
#
#   touch agent/__init__.py voice/__init__.py memory/__init__.py \
#         context/__init__.py planner/__init__.py executor/__init__.py \
#         response/__init__.py security/__init__.py src/__init__.py
#