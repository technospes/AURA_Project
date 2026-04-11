"""
JARVIS AGENT CORE v3 — True Agent with Thinking Layer
=======================================================
NEW in v3 vs v2:

  1. ThinkingEngine integrated between Intent and Planning:
     Intent → THINK (goal + subtasks) → Plan → Execute
     This is what enables "find me something relaxing" to become
     a multi-step plan automatically.

  2. Memory actually drives execution:
     ThinkingEngine._fill_from_memory() injects user preferences
     into entities BEFORE planning. "play music" → platform filled
     from memory.

  3. Continuous session state:
     Agent maintains awareness across turns via context + memory.
     "play it again" works because context has last_song.

  4. Better error messages:
     Specific spoken error types instead of generic "I encountered an error".

  5. IntentEngine initialized ONCE (not per-call):
     v2 had a subtle bug where IntentEngine was re-created each turn,
     rebuilding the Groq client and recompiling regex patterns.
     Now it's created in _init_modules() and reused.
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
    THINKING   = "thinking"    # NEW: reasoning about goal
    PROCESSING = "processing"
    EXECUTING  = "executing"
    REFLECTING = "reflecting"
    RESPONDING = "responding"


@dataclass
class AgentTurn:
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    raw_input: str = ""
    timestamp: float = field(default_factory=time.time)

    intent: Optional[Dict] = None
    think_result: Optional[Any] = None     # NEW: ThinkResult
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
    """Central orchestrator. Single public entry point: process()."""

    def __init__(self, config: Dict):
        self.config = config
        self.state = AgentState.IDLE
        self._tts_callback = None
        self._pending_intent: Optional[Dict] = None
        self._loop_set = False
        self._init_modules()

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
        from agent.thinking import ThinkingEngine
        from voice.intent_engine import IntentEngine  # ← initialized ONCE

        groq_key = self.config.get("groq_api_key", "")

        self.memory       = MemoryStore(self.config.get("memory", {}))
        self.context      = ContextTracker()
        self.planner      = PlanningEngine(self.config.get("planner", {}))
        self.executor     = ExecutionRunner(self.config.get("executor", {}))
        self.responder    = ResponseEngine(self.config.get("response", {}))
        self.security     = SecurityValidator(self.config.get("security", {}))
        self.decider      = DecisionEngine(self.config.get("decision", {}))
        self.reflector    = ReflectionEngine(self.config.get("reflection", {}), groq_key)
        self.thinker      = ThinkingEngine(groq_key, self.config.get("thinking", {}))  # NEW
        self.intent_engine = IntentEngine(groq_key)  # initialized ONCE
        self.task_manager = BackgroundTaskManager(on_notify=self._on_bg_task_done)

        logger.info("🧠 JarvisAgentCore v3 initialized")

    def set_tts_callback(self, fn):
        self._tts_callback = fn
        if hasattr(self, 'task_manager'):
            self.task_manager._on_notify = fn

    def _on_bg_task_done(self, message: str):
        logger.info(f"📢 BG task: {message[:80]}")
        if self._tts_callback:
            self._tts_callback(message)

    # ──────────────────────────────────────────────────────────────────────
    # MAIN AGENT PIPELINE
    # ──────────────────────────────────────────────────────────────────────

    async def process(self, raw_input: str, audio_features: Optional[Dict] = None) -> AgentTurn:
        """
        Full pipeline for one user command.

        Flow:
          Security → Intent → Memory → THINK → Decision
          → (Clarify|Answer|Execute) → Reflection → Memory Store
          → Context Update → Response
        """
        turn = AgentTurn(raw_input=raw_input)
        start = time.perf_counter()
        self.state = AgentState.PROCESSING

        # Set background task loop on first call
        if not self._loop_set:
            try:
                loop = asyncio.get_event_loop()
                self.task_manager.set_loop(loop)
                self._loop_set = True
            except RuntimeError:
                pass

        try:
            logger.info(f"[{turn.turn_id}] ▶ '{raw_input}'")

            # ── 1. SECURITY ────────────────────────────────────────────────
            validation = await self.security.validate(raw_input)
            if not validation["allowed"]:
                return self._exit(turn, validation["user_message"], start)
            if validation.get("needs_confirmation"):
                return self._exit(turn, validation["confirmation_prompt"], start, success=True)

            # ── 2. PENDING CLARIFICATION ───────────────────────────────────
            if self._pending_intent and len(raw_input.strip()) > 1:
                return await self._handle_clarification(raw_input, turn, start)

            # ── 3. INTENT ──────────────────────────────────────────────────
            ctx = self.context.snapshot()
            mem_hints = await self.memory.get_context_hints(raw_input)

            turn.intent = await self.intent_engine.understand(
                raw_input, context=ctx,
                memory_hints=mem_hints, audio_features=audio_features or {}
            )
            logger.info(f"[{turn.turn_id}] Intent: {turn.intent['intent']} ({turn.intent['confidence']:.2f})")

            # ── 4. MEMORY RECALL ───────────────────────────────────────────
            turn.memory_context = await self.memory.recall(raw_input, turn.intent, ctx)

            # ── 5. RESOLVE IMPLICIT REFERENCES ────────────────────────────
            turn.intent = self._resolve_implicit(turn.intent, ctx)


            if turn.intent.get("intent") == "cancel":
                if self.advisor and self.advisor.has_active_session():
                    self.advisor.abandon()
                    return self._exit(turn, "Cancelled.", start, success=True)
    
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

            # ── 6. THINKING LAYER (NEW) ────────────────────────────────────
            self.state = AgentState.THINKING
            turn.think_result = await self.thinker.think(
                turn.intent, turn.memory_context, ctx
            )
            logger.info(
                f"[{turn.turn_id}] Think: '{turn.think_result.goal}' "
                f"({len(turn.think_result.subtasks)} subtasks, "
                f"conf={turn.think_result.confidence:.2f})"
            )

            # Thinking detected it needs clarification
            if turn.think_result.requires_clarification:
                self._pending_intent = turn.intent
                return self._exit(turn, turn.think_result.clarification_question, start, success=True)

            # ── 7. DECISION ────────────────────────────────────────────────
            self.state = AgentState.PROCESSING
            from agent.decision import Decision
            dr = self.decider.decide(turn.intent, ctx, turn.memory_context)
            turn.decision = {"decision": dr.decision.value, "reason": dr.reason}
            logger.info(f"[{turn.turn_id}] Decision: {dr.decision.value}")

            if dr.decision == Decision.IGNORE:
                return self._exit(turn, None, start, success=True)

            if dr.decision == Decision.CLARIFY:
                self._pending_intent = turn.intent
                return self._exit(turn, dr.clarification_question, start, success=True)

            if dr.decision == Decision.ANSWER:
                return await self._direct_answer(turn, dr, ctx, start)

            if dr.decision == Decision.REFLECT:
                turn.intent = await self._reflect_intent(turn.intent, dr.reflection_prompt or "", ctx)
                dr2 = self.decider.decide(turn.intent, ctx, turn.memory_context)
                if dr2.decision != Decision.EXECUTE:
                    return self._exit(turn, dr2.clarification_question or "Could you clarify?", start)

            # ── 8. BACKGROUND ROUTE ────────────────────────────────────────
            bg_msg = await self._check_background_route(turn.intent, turn.think_result)
            if bg_msg:
                return self._exit(turn, bg_msg, start, success=True)

            # ── 9. PLANNING (informed by ThinkResult) ─────────────────────
            turn.plan = await self.planner.create_plan(
                turn.intent, turn.memory_context, ctx,
                think_hints=turn.think_result.to_plan_hints()  # NEW: pass think hints
            )
            logger.info(f"[{turn.turn_id}] Plan: {len(turn.plan)} steps")
            for i, s in enumerate(turn.plan, 1):
                logger.info(f"   {i}. [{s['tool']}] {s['description']}")

            # ── 10. EXECUTE + REFLECTION LOOP ─────────────────────────────
            self.state = AgentState.EXECUTING
            turn = await self._execute_with_reflection(turn, ctx)

            # ── 11. STORE + CONTEXT ────────────────────────────────────────
            await self._store_turn(turn)
            await self.context.update_from_turn(turn)

            # ── 12. RESPONSE ───────────────────────────────────────────────
            self.state = AgentState.RESPONDING
            rd = await self.responder.generate(turn, ctx, turn.memory_context)
            turn.response        = rd["full_response"]
            turn.spoken_response = rd["spoken_response"]

        except Exception as e:
            logger.error(f"[{turn.turn_id}] Agent error: {e}", exc_info=True)
            turn.error = str(e)
            turn.success = False
            # Specific error messages instead of generic
            err = str(e).lower()
            if "groq" in err or "api" in err or "rate" in err:
                turn.spoken_response = "AI service is busy. Try again."
            elif "connection" in err or "network" in err:
                turn.spoken_response = "Network issue. Check your connection."
            elif "timeout" in err:
                turn.spoken_response = "That took too long. Try again."
            else:
                turn.spoken_response = "I ran into an issue. Try a different approach."
            turn.response = turn.spoken_response

        finally:
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            logger.info(
                f"[{turn.turn_id}] {'✓' if turn.success else '✗'} "
                f"{turn.duration_ms:.0f}ms | refs={turn.reflection_rounds}"
            )

        return turn

    # ── EXECUTION + REFLECTION ────────────────────────────────────────────

    async def _execute_with_reflection(self, turn: AgentTurn, ctx: Dict) -> AgentTurn:
        """Execute plan with up to MAX_REFLECTION_ROUNDS reflection cycles."""
        from agent.reflection import ReflectionContext, ReflectionMode

        current_plan = turn.plan
        all_results  = []
        ref_log      = []

        for round_num in range(MAX_REFLECTION_ROUNDS + 1):
            if round_num > 0:
                self.state = AgentState.REFLECTING
                logger.info(f"[{turn.turn_id}] 🔄 Reflection round {round_num}")

            results = await self.executor.run_plan(current_plan, turn.intent, ctx)
            all_results.extend(results)

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
                failed_steps=failed, succeeded_steps=success,
                context=ctx, memory=turn.memory_context or {},
                reflection_depth=round_num, previous_reflections=ref_log
            )
            reflection = await self.reflector.reflect(ref_ctx)
            ref_log.append(f"R{round_num}: {reflection.mode.value}")
            logger.info(f"   → {reflection.mode.value}: {reflection.diagnosis}")

            from agent.reflection import ReflectionMode
            if reflection.mode == ReflectionMode.ESCALATE:
                turn.execution_results = all_results
                turn.success           = False
                turn.spoken_response   = reflection.user_message
                turn.response          = reflection.user_message
                turn.reflection_rounds = round_num + 1
                return turn

            if reflection.should_retry and reflection.new_plan:
                current_plan = reflection.new_plan
                continue

            break

        turn.execution_results = all_results
        turn.success           = any(r.get("success") for r in all_results)
        turn.reflection_rounds = MAX_REFLECTION_ROUNDS
        return turn

    # ── DIRECT ANSWER ─────────────────────────────────────────────────────

    async def _direct_answer(self, turn, dr, ctx, start):
        """Handle intents that need no tool execution."""
        intent_nm = turn.intent.get("intent", "")
        entities  = turn.intent.get("entities", {})

        if dr.direct_answer:
            turn.response = turn.spoken_response = dr.direct_answer
            turn.success  = True

        elif intent_nm in ("express_preference", "remember_fact"):
            fact = entities.get("fact", entities.get("preference", ""))
            subj = entities.get("subject", intent_nm.replace("_", "_"))
            if fact:
                await self.memory.store(key=f"{subj}_{int(time.time())}", value=fact,
                                        category="preference" if "prefer" in intent_nm else "fact",
                                        importance=0.8, source="user_explicit")
            turn.response = turn.spoken_response = "Got it."
            turn.success  = True

        elif intent_nm == "recall_fact":
            query    = entities.get("query", turn.raw_input)
            recalled = await self.memory.recall(query, turn.intent, ctx)
            items    = recalled.get("personal",[]) + recalled.get("preferences",[]) + recalled.get("facts",[])
            if items:
                s   = "; ".join(f"{i['key']}: {i['value']}" for i in items[:4])
                msg = f"Here's what I know: {s}"
            else:
                msg = "I don't have anything stored on that."
            turn.response = turn.spoken_response = msg
            turn.success  = True

        elif intent_nm == "introduce_self":
            name = entities.get("name", "")
            if name:
                await self.memory.store(key="user_name", value=name,
                                        category="personal", importance=1.0,
                                        source="user_explicit")
            msg = f"Pleased to meet you{', ' + name if name else ''}."
            turn.response = turn.spoken_response = msg
            turn.success  = True

        else:
            # quick_answer — use AI brain
            from executor.runner import AIBrainTool
            tool   = AIBrainTool(self.config.get("groq_api_key", ""))
            query  = entities.get("query", turn.raw_input)
            try:
                result = await tool.execute("answer_question", {"query": query}, turn.intent, ctx, [])
                answer = result.get("answer", "I'm not sure.")
                turn.execution_results = [{"action": "answer_question", "success": True, "output": result}]
                turn.success  = True
                rd = await self.responder.generate(turn, ctx, turn.memory_context or {})
                turn.response        = rd["full_response"]
                turn.spoken_response = rd["spoken_response"]
            except Exception as e:
                turn.response = turn.spoken_response = "I couldn't answer that."
                turn.success  = False

        await self.context.update_from_turn(turn)
        turn.duration_ms = (time.perf_counter() - start) * 1000
        return turn

    # ── BACKGROUND ROUTING ────────────────────────────────────────────────

    async def _check_background_route(self, intent: Dict, think_result) -> Optional[str]:
        nm = intent.get("intent", "")
        en = intent.get("entities", {})

        if nm == "deep_research":
            topic = en.get("topic", "the topic")
            from agent.background import background_research_task

            async def _do():
                return await background_research_task(
                    topic=topic, task_manager=self.task_manager,
                    task_id=str(uuid.uuid4())[:8],
                    groq_api_key=self.config.get("groq_api_key", "")
                )

            self.task_manager.submit(f"Research: {topic[:30]}", _do(), notify=True)
            return f"Researching '{topic}' in the background. I'll let you know when done."

        if nm == "set_reminder":
            msg  = en.get("reminder_text", "Your reminder")
            secs = self._parse_time(en.get("time", "5 minutes"))
            from agent.background import background_reminder_task

            async def _remind():
                return await background_reminder_task(
                    message=msg, delay_seconds=secs,
                    task_manager=self.task_manager, task_id=str(uuid.uuid4())[:8]
                )

            self.task_manager.submit(f"Reminder: {msg[:30]}", _remind(), notify=True)
            return f"Reminder set for '{msg}' in {secs:.0f} seconds."

        if nm == "quick_answer":
            q = en.get("query", "").lower()
            if any(w in q for w in ["task", "background", "running", "status"]):
                return self.task_manager.get_status_summary()

        return None

    # ── CLARIFICATION ─────────────────────────────────────────────────────

    async def _handle_clarification(self, text: str, turn: AgentTurn, start: float) -> AgentTurn:
        pending  = self._pending_intent
        entities = pending.get("entities", {})
        t        = text.lower().strip()

        if "spotify" in t:   entities["platform"] = "spotify"
        elif "youtube" in t: entities["platform"] = "youtube"
        elif "discord" in t: entities["platform"] = "discord"
        else:
            for slot in ["song", "contact", "text", "query", "reminder_text", "topic"]:
                if not entities.get(slot):
                    entities[slot] = text.strip()
                    break

        pending["entities"]   = entities
        pending["confidence"] = 0.85
        self._pending_intent  = None
        return await self.process(text)

    # ── IMPLICIT RESOLUTION ───────────────────────────────────────────────

    def _resolve_implicit(self, intent: Dict, ctx: Dict) -> Dict:
        entities = intent.get("entities", {})
        text     = intent.get("original_text", "").lower()
        nm       = intent.get("intent", "")
        implicit = {"it", "that", "this", "again", "same"}

        if not (implicit & set(text.split())):
            return intent

        if nm in ("close_app", "open_app") and not entities.get("app") and ctx.get("last_app"):
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
        if not any(entities.values()) and ctx.get("last_entity"):
            entities["target"] = ctx["last_entity"]

        intent["entities"] = entities
        return intent

    # ── INTENT REFLECTION ─────────────────────────────────────────────────

    async def _reflect_intent(self, intent: Dict, prompt: str, ctx: Dict) -> Dict:
        new_intent = await self.intent_engine.understand(
            text=intent.get("original_text", ""),
            context={**ctx, "reflection_hint": prompt},
            memory_hints={}, audio_features={}
        )
        new_intent["reflection_applied"] = True
        return new_intent

    # ── MEMORY STORAGE ────────────────────────────────────────────────────

    async def _store_turn(self, turn: AgentTurn):
        intent = turn.intent or {}
        nm     = intent.get("intent", "")
        en     = intent.get("entities", {})

        if nm == "express_preference" and en.get("fact"):
            await self.memory.store(
                key=f"preference_{en.get('subject','general')}",
                value=en["fact"], category="preference", importance=0.9
            )

        if nm == "introduce_self" and en.get("name"):
            await self.memory.store(
                key="user_name", value=en["name"],
                category="personal", importance=1.0
            )

        # Learn platform preference from successful plays
        if nm == "play_media" and turn.success and en.get("platform"):
            await self.memory.store(
                key="preferred_music_platform", value=en["platform"],
                category="preference", importance=0.6
            )

        # Log task (low importance)
        if turn.success and nm not in ("greet", "thank", "cancel"):
            await self.memory.store(
                key=f"task_{turn.turn_id}", value=f"{nm}: {en}",
                category="task", importance=0.2
            )

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _exit(self, turn, message, start, success=False):
        turn.response        = message
        turn.spoken_response = message
        turn.success         = success
        turn.duration_ms     = (time.perf_counter() - start) * 1000
        self.state           = AgentState.IDLE
        return turn

    def _parse_time(self, s: str) -> float:
        import re
        s = s.lower()
        total = 0.0
        for pat, mul in [(r'(\d+)\s*h', 3600), (r'(\d+)\s*m', 60), (r'(\d+)\s*s', 1)]:
            m = re.search(pat, s)
            if m:
                total += int(m.group(1)) * mul
        return total or 300
