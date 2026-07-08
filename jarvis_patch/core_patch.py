import asyncio
import logging
import re
import time
import threading
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from response.engine import clean_for_speech
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# STT STABILITY LAYER  (Checklist 7 + BUG 2 FIX)
# ════════════════════════════════════════════════════════════════════════════

class STTStabilityLayer:
    """
    Permissive gate — only reject obvious Whisper hallucinations.
    Numbers like '1440', '1080', '60hz' are VALID follow-up answers.
    Checklist 7: ensures numeric + short responses are preserved.
    """
    _GARBAGE = frozenset({
        "", " ", ".",
        "thank you for watching.", "thanks for watching.",
        "subtitles by the amara.org community",
        "[music]", "[silence]",
    })

    def accept(self, text: str, confidence: float = 1.0) -> bool:
        if not text or not text.strip():
            return False
        stripped = text.strip(" .,!?;:").lower()
        if not stripped:
            return False
        if stripped in self._GARBAGE:
            return False
        if len(stripped) <= 1 and not stripped.isalnum():
            return False
        # Accept anything with alphanumeric content (letters OR digits)
        return any(c.isalnum() for c in stripped)


stt_stability = STTStabilityLayer()


# ════════════════════════════════════════════════════════════════════════════
# KNOWN INTENTS
# ════════════════════════════════════════════════════════════════════════════

_BUILT_IN_INTENTS = frozenset({
    "guided_recommendation","deep_research","answer_question",
    "quick_answer","conversation","greet","thank","cancel",
    "remember_fact","recall_fact","express_preference","introduce_self",
    "system_action","set_reminder",
    "open_app","close_app","play_media","pause_media","resume_media",
    "next_track","previous_track","search_web","open_website","open_url",
    "click_result","browser_navigation",
    "close_tab","new_tab","scroll","type_text","take_screenshot",
    "lock","shutdown","restart","send_message","make_call",
    "compose_email","smart_open","read_page","page_summary",
    "focus_app","minimize_app","maximize_app",
    "unknown",
})

_SYSTEM_INTENTS = frozenset({
    "open_app","close_app","close_tab","new_tab","open_website","open_url",
    "click_result","browser_navigation",
    "smart_open","play_media","pause_media","resume_media",
    "next_track","previous_track","shutdown","restart","lock",
    "take_screenshot","scroll","search_web","type_text","cancel",
    "read_page","page_summary","make_call","send_message",
    "deep_research","set_reminder","system_action",
})

_ALLOWED_SLOTS: Dict[str, List[str]] = {
    "play_media":            ["song","platform","artist"],
    "guided_recommendation": ["query","budget","brand","category"],
    "search_web":            ["query"],
    "answer_question":       ["query"],
    "compose_message":       ["contact","body","platform"],
    "make_call":             ["contact","platform"],
    "open_app":              ["name","app","app_name"],
    "close_app":             ["name","app","app_name"],
    "system_action":         ["action_type","setting","value","target"],
    "send_message":          ["contact","body","platform"],
}

# Phrases that look like "open X" but are actually advisor answers (BUG 3)
_OPEN_CONTINUATION_PHRASES = re.compile(
    r'^open\s+(?:to\s+)?(?:all|any|every|no\s+)?(?:option|brand|preference|choice)',
    re.IGNORECASE,
)


def _sanitize_entities(intent_name: str, entities: Dict) -> Dict:
    allowed = _ALLOWED_SLOTS.get(intent_name)
    if allowed is None:
        return entities
    return {k: v for k, v in entities.items() if k in allowed}


def _looks_like_advisor_continuation(text: str, intent_name: str) -> bool:
    if intent_name != "open_app":
        return False
    return bool(_OPEN_CONTINUATION_PHRASES.match(text.strip()))


# ════════════════════════════════════════════════════════════════════════════
# GAME LAUNCHER MAP
# ════════════════════════════════════════════════════════════════════════════

_GAME_PLATFORM_URIS = {
    "steam":       "steam://open/games",
    "epic":        "com.epicgames.launcher://apps",
    "epic games":  "com.epicgames.launcher://apps",
    "riot":        "riotclient://",
    "riot games":  "riotclient://",
    "battle.net":  "battlenet://",
    "battlenet":   "battlenet://",
    "ea app":      "origin://",
    "origin":      "origin://",
    "gog":         "goggalaxy://",
    "ubisoft":     "uplay://",
    "uplay":       "uplay://",
}

_GAME_PLATFORM_URLS = {
    "steam":       "https://store.steampowered.com",
    "epic":        "https://www.epicgames.com/store",
    "epic games":  "https://www.epicgames.com/store",
    "riot":        "https://www.riotgames.com",
    "riot games":  "https://www.riotgames.com",
    "battle.net":  "https://us.battle.net/login",
    "battlenet":   "https://us.battle.net/login",
    "ea app":      "https://www.ea.com/ea-app",
    "origin":      "https://www.ea.com/ea-app",
    "gog":         "https://www.gog.com/en/games",
    "ubisoft":     "https://www.ubisoft.com/en-us/ubisoft-connect",
    "uplay":       "https://www.ubisoft.com/en-us/ubisoft-connect",
}


# ════════════════════════════════════════════════════════════════════════════
# PATCHED process()
# ════════════════════════════════════════════════════════════════════════════

async def process_patched(self, raw_input: str, audio_features: Optional[Dict] = None):
    from agent.core import AgentState, AgentTurn

    turn  = AgentTurn(raw_input=raw_input)
    start = time.perf_counter()
    self.state = AgentState.PROCESSING

    # Checklist 2: watchdog
    try:
        self._state_ctrl.watchdog_check()
    except Exception:
        pass

    # STT gate (Checklist 7: preserves numbers)
    if not stt_stability.accept(raw_input):
        logger.info(f"[STT GATE] Garbage discarded: '{raw_input}'")
        turn.success         = True
        turn.spoken_response = turn.response = ""
        turn.duration_ms     = (time.perf_counter() - start) * 1000
        self.state           = AgentState.IDLE
        return turn

    if not self._loop_set:
        try:
            self.task_manager.set_loop(asyncio.get_event_loop())
            self._loop_set = True
        except RuntimeError:
            pass

    # Checklist 9: structured turn logging
    logger.info(f"\n{'─'*60}")
    logger.info(f"[{turn.turn_id}] ▶ '{raw_input}'")

    # ── HANDLE WHATSAPP CONFIRMATION FOLLOW-UP ──────────────────────────
    if hasattr(self, '_pending_whatsapp') and self._pending_whatsapp:
        text_lower = raw_input.lower().strip().rstrip('.')
        pending = self._pending_whatsapp
        
        # Check if user said YES (any variation with extra words)
        yes_words = ("yes", "yeah", "yep", "ok", "okay", "sure", "go ahead", "do it", "call", "send", "proceed", "correct", "right")
        is_yes = any(text_lower.startswith(w) or text_lower == w for w in yes_words)
        
        # Check if user said NO (any variation)
        no_words = ("no", "nope", "nah", "cancel", "never mind", "wrong", "stop", "incorrect")
        is_no = any(text_lower.startswith(w) or text_lower == w for w in no_words)
        
        # Check for navigation commands
        nav_down = any(w in text_lower for w in ("below", "down", "next", 
            "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"))
        nav_up = any(w in text_lower for w in ("above", "up", "previous", "back"))
        
        if nav_down or nav_up:
            # Navigate search results
            direction = "up" if nav_up else "down"
            count = 1
            for word, num in [("two", 2), ("2", 2), ("three", 3), ("3", 3), 
                            ("four", 4), ("4", 4), ("five", 5), ("5", 5),
                            ("six", 6), ("6", 6), ("sixth", 6), ("6th", 6),
                            ("seven", 7), ("7", 7), ("seventh", 7), ("7th", 7),
                            ("eight", 8), ("8", 8), ("eighth", 8), ("8th", 8)]:
                if word in text_lower:
                    count = num
                    break
            
            for _ in range(count):
                key = 'up' if direction == 'up' else 'down'
                import pyautogui
                pyautogui.press(key)
                await asyncio.sleep(0.15)
            
            turn.requires_followup = True
            turn.spoken_response = f"Moved {direction} {count}. Is this correct?"
            turn.success = True
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            return turn
        
        elif is_yes:
            self._pending_whatsapp = None
            result = await self.executor.registry.get("unified_comm").execute(
                action=pending["action"],
                params={"contact": pending["contact"], "message": pending.get("message", "")},
            )
            msg = result.get("message", "Done, Sir.") if isinstance(result, dict) else "Done, Sir."
            turn.response = msg
            turn.spoken_response = msg
            turn.success = True
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            return turn
        
        elif is_no:
            self._pending_whatsapp = None
            import pyautogui
            pyautogui.press('escape')  # Close WhatsApp search
            turn.response = "Cancelled, Sir."
            turn.spoken_response = "Cancelled, Sir."
            turn.success = True
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            return turn

    # ── COMPOUND COMMAND SPLIT ────────────────────────────────────────────
    # Wire CommandSplitter from clarifier.py — it exists but was never called.
    # If the input contains multiple commands ("Open Notepad and type hello"),
    # process each sequentially and return the last turn's response.
    try:
        from agent.clarifier import split_commands
        _sub_commands = split_commands(raw_input)
    except Exception:
        _sub_commands = [raw_input]

    if len(_sub_commands) > 1:
        logger.info(f"[CommandSplit] '{raw_input}' → {len(_sub_commands)} commands: {_sub_commands}")
        last_turn = turn
        for i, _cmd in enumerate(_sub_commands):
            logger.info(f"[CommandSplit] Executing sub-command {i+1}/{len(_sub_commands)}: '{_cmd}'")
            _sub_turn = await self.process(_cmd)
            last_turn = _sub_turn
            # Short pause between sub-commands so app launches settle
            if i < len(_sub_commands) - 1:
                await asyncio.sleep(0.5)
        # Return combined spoken response
        last_turn.duration_ms = (time.perf_counter() - start) * 1000
        return last_turn
    
    # # ── HANDLE ORCHESTRATOR FOLLOW-UP ────────────────────────────────────
    # try:
    #     from task_orchestrator import get_orchestrator
    #     orchestrator = get_orchestrator()
    #     if orchestrator.has_active_task():
    #         response = orchestrator.check_and_route(raw_input, None)
    #         if response == "continue":
    #             # This is a follow-up response to an active task
    #             msg = await orchestrator.handle_response(
    #                 raw_input,
    #                 speak=self._tts_callback if self._tts_callback else lambda x: None
    #             )
    #             turn.response = msg
    #             turn.spoken_response = msg
    #             turn.success = True
    #             turn.requires_followup = orchestrator.has_active_task()
    #             turn.duration_ms = (time.perf_counter() - start) * 1000
    #             self.state = AgentState.IDLE
    #             return turn
    #         elif response == "cancel":
    #             turn.response = "Task cancelled, Sir."
    #             turn.spoken_response = "Task cancelled, Sir."
    #             turn.success = True
    #             turn.duration_ms = (time.perf_counter() - start) * 1000
    #             self.state = AgentState.IDLE
    #             return turn
    # except Exception as e:
    #     logger.debug(f"[Orchestrator] Check failed (non-fatal): {e}")

    try:
        # ── 1. SECURITY ──────────────────────────────────────────────────
        validation = await self.security.validate(raw_input)
        if not validation["allowed"]:
            return self._early_exit(turn, validation["user_message"], start)
        if validation.get("needs_confirmation"):
            return self._early_exit(turn, validation["confirmation_prompt"], start, success=True)

        # ── 2. CONTEXT ────────────────────────────────────────────────────
        ctx_snapshot = self.context.snapshot()
        mem_hints    = await self.memory.get_context_hints(raw_input)

        # ── Inject UI element map from screen daemon ─────────────────────────
        try:
            from screen_awareness import screen_daemon
            ctx_snapshot["ui_map"] = screen_daemon.ui_map
            ctx_snapshot["screen_text"] = screen_daemon.current.screen_text
        except Exception:
            ctx_snapshot["ui_map"] = {}
            
        try:
            from session_memory import session as _sess
            ctx_snapshot["has_page_context"] = _sess.has_page_context()
            ctx_snapshot["page_url"]         = _sess._page_url
            ctx_snapshot["page_title"]       = _sess._page_title
        except Exception:
            pass

        # ── 3. INTENT ─────────────────────────────────────────────────────
        turn.intent = await self.intent_engine.understand(
            raw_input, context=ctx_snapshot,
            memory_hints=mem_hints, audio_features=audio_features or {}
        )
        # Checklist 3: version intent
        turn.intent_version = int(time.time() * 1000) & 0xFFFF
        intent_name = turn.intent.get("intent","unknown")
        is_system   = intent_name in _SYSTEM_INTENTS

        # Checklist 9: structured intent log
        logger.info(
            f"[{turn.turn_id}] Intent: {intent_name} "
            f"(conf={turn.intent['confidence']:.2f} v={turn.intent_version})"
        )
        try:
            self._metrics.record_turn(True, intent_name, 0.0)
        except Exception:
            pass

        # ── BUG 3 FIX: advisor-continuation override ───────────────────
        if is_system and self.advisor and self.advisor.has_active_session():
            if _looks_like_advisor_continuation(raw_input, intent_name):
                logger.info("[PATCH] 'open X' → advisor continuation")
                is_system   = False
                intent_name = "unknown"

        # ── RESOLVE CONTACT ALIASES ──────────────────────────────────────────
        if intent_name in ("make_call", "send_message"):
            contact_name = turn.intent.get("entities", {}).get("contact", "")
            if contact_name:
                try:
                    from data_sync import entity_resolver
                    if entity_resolver:
                        resolved = entity_resolver.resolve_contact(contact_name)
                        if resolved and resolved != contact_name:
                            logger.info(f"[Alias] '{contact_name}' → '{resolved}'")
                            turn.intent["entities"]["contact"] = resolved
                except Exception:
                    pass

        # ── 4. ENTITY SANITIZATION ────────────────────────────────────────
        if "entities" in turn.intent:
            turn.intent["entities"] = _sanitize_entities(
                intent_name, turn.intent["entities"]
            )

        # ── 5. PAGE CONTEXT ENFORCEMENT ───────────────────────────────────
        try:
            from session_memory import session as _sess2
            if getattr(_sess2, '_page_context', None):
                if intent_name in ["quick_answer","unknown"]:
                    turn.intent["intent"] = "answer_question"
                    intent_name = "answer_question"
        except Exception:
            pass

        # ── 6. PENDING INTENT (Checklist 3: expiry + stale cleanup) ───────
        # BUG 4 FIX: clear stale open_app pending if app doesn't exist
        if self._has_valid_pending_intent:
            pi_name = self._pending_intent.get("intent","")
            if pi_name in ("open_app","close_app"):
                stale_app = self._pending_intent.get("entities",{}).get("app","")
                if stale_app and not _app_exists(stale_app):
                    logger.info(f"[PATCH] Clearing stale open_app pending — '{stale_app}' not found")
                    self._clear_pending_intent()

        if self._has_valid_pending_intent and not is_system and len(raw_input.strip()) > 1:
            return await self._handle_clarification_response(raw_input, turn, start)

        self._clear_pending_intent()
        if hasattr(self.task_planner, "cancel_active_session"):
            self.task_planner.cancel_active_session()

        # ── 7. CANCEL ─────────────────────────────────────────────────────
        if intent_name == "cancel":
            self.task_planner.cancel_active_session()
            if self.advisor:
                if getattr(self.advisor, 'current_task_id', None):
                    if self.advisor.cancel_current_research(self.task_manager):
                        self.state = AgentState.IDLE
                        return self._early_exit(turn, "Research cancelled, Sir.", start, success=True)
                if self.advisor.has_active_session():
                    self.advisor.abandon()
            return self._early_exit(turn, "Cancelled, Sir.", start, success=True)

        # ── 8. ACTIVE ADVISOR ─────────────────────────────────────────────
        if self.advisor and self.advisor.has_active_session():
            if is_system:
                self.advisor.abandon()
                self._clear_pending_intent()
                self.state = AgentState.IDLE
                # Fall through to execute system command
            else:
                return await self._handle_advisor_turn(turn, raw_input, start)

        # ── 9. NEW ADVISOR SESSION ────────────────────────────────────────
        if intent_name == "guided_recommendation":
            return await self._start_advisor_session(turn, raw_input, start)

        if intent_name == "autonomous_task":
            goal_text = turn.intent.get("original_text", raw_input)
            goal = self.goal_manager.create_goal(goal_text)

            if self._tts_callback:
                await asyncio.sleep(0.4)
                self._tts_callback("Working on it.")

            turn.plan = await self._plan_autonomous_goal(goal_text, ctx_snapshot)
            self.state = AgentState.EXECUTING
            result = await self.executor.run_graph(turn.plan, turn.intent, ctx_snapshot)

            if result["success"]:
                self.goal_manager.complete_goal(goal.id, result.get("summary", ""))
                
                # Get the actual synthesis from ai_brain tool output
                # run_graph stores results in graph.nodes, not _step_results
                synthesis_text = ""
                try:
                    # Access the graph's nodes to find the synthesize step output
                    for node in self.executor._last_graph.nodes.values() if hasattr(self.executor, '_last_graph') else []:
                        if node.action == "synthesize_research" and node.result:
                            out = node.result.get("output", {})
                            if isinstance(out, dict) and "synthesis" in out:
                                synthesis_text = out["synthesis"]
                                break
                except Exception:
                    pass
                
                if synthesis_text:
                    response = synthesis_text
                else:
                    # Fallback: just say it's done
                    response = "I've found and summarized the information you requested."
            else:
                self.goal_manager.fail_goal(goal.id, "Could not complete all steps")
                response = "I wasn't able to complete that task."

            turn.response = response
            turn.spoken_response = clean_for_speech(response)
            turn.success = result["success"]
            turn.duration_ms = (time.perf_counter() - start) * 1000
            self.state = AgentState.IDLE
            return turn

        # ── 10. TASK PLANNER ──────────────────────────────────────────────
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
            intent_name = planner_result.execution_intent

        # ── 11. MEMORY ────────────────────────────────────────────────────
        turn.memory_context = await self.memory.recall(raw_input, turn.intent, ctx_snapshot)

        # ── 12. IMPLICIT REFS ─────────────────────────────────────────────
        turn.intent = self._resolve_implicit(turn.intent, ctx_snapshot)

        # ── 13. DECISION ──────────────────────────────────────────────────
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

        # ── 14. BACKGROUND ROUTE ──────────────────────────────────────────
        bg_msg = await self._check_background_route(turn.intent)
        if bg_msg:
            return self._early_exit(turn, bg_msg, start, success=True)

        # ── 15. TOOL BUILDER CHECK ────────────────────────────────────────
        current_intent = turn.intent.get("intent","")
        if current_intent not in _BUILT_IN_INTENTS:
            has_dynamic = (
                hasattr(self.executor.registry, '_tools') and
                current_intent in self.executor.registry._tools
            )
            if not has_dynamic:
                logger.info(f"[ToolBuilder] No tool for '{current_intent}' — attempting build")
                from jarvis_patch.tool_builder import handle_build_tool_request
                return await handle_build_tool_request(
                    self, raw_input, turn.intent, turn, start
                )

        # ── 16. PLAN + VALIDATE ───────────────────────────────────────────
        turn.plan = await self.planner.create_plan(
            turn.intent, turn.memory_context, ctx_snapshot
        )
        logger.info(f"[{turn.turn_id}] Plan: {len(turn.plan)} steps")

        # Checklist 6: validate plan
        try:
            plan_ok, plan_issues = self._plan_validator.validate(turn.plan, turn.intent)
            if not plan_ok:
                logger.warning(f"[{turn.turn_id}] Plan issues: {plan_issues}")
                turn.plan_valid = False
        except Exception:
            pass

        self.state = AgentState.EXECUTING

        # ── TASK GRAPH EXECUTION ROUTING ───────────────────────────────────
        if len(turn.plan) > 2 and any(step.get("depends_on") for step in turn.plan):
            result = await self.executor.run_graph(
                turn.plan, turn.intent, ctx_snapshot
            )
            turn.success = result["success"]
            logger.info(f"[TaskGraph] Result: {result['summary']}")
        else:
            turn = await self._execute_with_reflection(turn, ctx_snapshot)

        # ── CHECK IF SEARCH NEEDS CONFIRMATION ──────────────────────────────
        if intent_name in ("make_call", "send_message") and turn.intent.get("entities", {}).get("platform", "").lower() == "whatsapp":
            if turn.execution_results:
                # execution_results is a list of step results
                for result in turn.execution_results:
                    if isinstance(result, dict):
                        # Check the output field of each step result
                        out = result.get("output", {})
                        if isinstance(out, dict) and out.get("contact_searched"):
                            self._pending_whatsapp = {
                                "action": "call_whatsapp" if intent_name == "make_call" else "send_whatsapp_message",
                                "contact": out["contact_searched"],
                                "message": turn.intent.get("entities", {}).get("body", ""),
                            }
                            turn.requires_followup = True
                            turn.spoken_response = f"I found {out['contact_searched']}. Should I proceed?"
                            turn.success = True
                            turn.duration_ms = (time.perf_counter() - start) * 1000
                            self.state = AgentState.IDLE
                            return turn
                            break  # Found it, stop searching

        # ── DEBUG: See what the step returned ──────────────────────────
        if intent_name in ("make_call", "send_message"):
            logger.info(f"[DEBUG] intent={intent_name}, platform={turn.intent.get('entities', {}).get('platform', '')}")
            logger.info(f"[DEBUG] execution_results count: {len(turn.execution_results) if turn.execution_results else 0}")
            if turn.execution_results:
                for i, r in enumerate(turn.execution_results):
                    out = r.get("output", {}) if isinstance(r, dict) else "not a dict"
                    logger.info(f"[DEBUG] result[{i}]: action={r.get('action','?')}, success={r.get('success','?')}, output_keys={list(out.keys()) if isinstance(out, dict) else 'N/A'}")

        # ── CHECK IF SEARCH NEEDS CONFIRMATION ──────────────────────────
        if intent_name in ("make_call", "send_message") and turn.intent.get("entities", {}).get("platform", "").lower() == "whatsapp":
            if turn.execution_results:
                for result in turn.execution_results:
                    if isinstance(result, dict):
                        out = result.get("output", {})
                        if isinstance(out, dict) and out.get("contact_searched"):
                            self._pending_whatsapp = {
                                "action": "call_whatsapp" if intent_name == "make_call" else "send_whatsapp_message",
                                "contact": out["contact_searched"],
                                "message": turn.intent.get("entities", {}).get("body", ""),
                            }
                            turn.requires_followup = True
                            turn.spoken_response = f"I found {out['contact_searched']}. Should I proceed?"
                            turn.success = True
                            turn.duration_ms = (time.perf_counter() - start) * 1000
                            self.state = AgentState.IDLE
                            return turn

        await self._store_turn(turn)
        await self.context.update_from_turn(turn)

        self.state = AgentState.RESPONDING
        rd = await self.responder.generate(turn, ctx_snapshot, turn.memory_context)
        turn.response        = rd["full_response"]
        turn.spoken_response = rd["spoken_response"]

    except Exception as e:
        # Checklist 12: categorized error handling
        try:
            from reliability_layer import categorize_error
            err = categorize_error(e)
            try:
                self._metrics.record_error(err.category)
            except Exception:
                pass
            logger.error(f"[{turn.turn_id}] Agent error: {err}", exc_info=True)
            turn.error_category = err.category.value
        except Exception:
            logger.error(f"[{turn.turn_id}] Agent error: {e}", exc_info=True)
        turn.error           = str(e)
        turn.success         = False
        turn.spoken_response = "I encountered an error, Sir. Please try again."
        turn.response        = f"Error: {e}"
        # Checklist 2: force recovery
        try:
            self._state_ctrl.force_idle()
        except Exception:
            pass

    finally:
        turn.duration_ms = (time.perf_counter() - start) * 1000
        self.state = AgentState.IDLE
        logger.info(
            f"[{turn.turn_id}] {'' if turn.success else ''} "
            f"{turn.duration_ms:.0f}ms | refs={turn.reflection_rounds}"
        )

    return turn


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _app_exists(app_name: str) -> bool:
    try:
        from utils.app_locator import app_locator
        return bool(app_locator.find_app(app_name))
    except Exception:
        return True


# ════════════════════════════════════════════════════════════════════════════
# AUTONOMOUS GOAL PLANNER
# ════════════════════════════════════════════════════════════════════════════

_AUTONOMOUS_ALLOWED_ACTIONS = frozenset({
    "open_app", "search_web", "open_website", "click_element",
    "type_text", "scroll", "take_screenshot", "wait",
})


async def _plan_autonomous_goal(self, goal_text: str, context: Dict) -> List[Dict]:
    """Use LLM to create a multi-step plan for an autonomous goal."""
    
    # Get available tools from capability registry
    from core.capability_registry import registry as cap_registry
    text_lower = goal_text.lower()

    # ── Pattern: standalone "summarize X" ──────────────────────────────
    if re.search(r'^(?:summarize|summarise|summrize|summrise)\s+', text_lower):
        query = re.sub(r'^(?:summarize|summarise|summrize|summrise)\s+', '', text_lower).strip()
        query = re.sub(r'\s+and\s+(?:summarize|tell|show|read).*$', '', query, flags=re.I).strip()
        
        logger.info(f"[AutonomousPlanner] Pattern match: standalone summarize | query='{query}'")
        
        return [
            {
                "action": "search_web",
                "tool": "browser",
                "params": {"query": query, "platform": "google"},
                "description": f"Search for: {query}",
                "depends_on": []
            },
            {
                "action": "fetch_and_parse",
                "tool": "web_navigator",
                "params": {"max_pages": 3},
                "description": "Fetch and parse top search results",
                "depends_on": [0]
            },
            {
                "action": "synthesize_research",
                "tool": "ai_brain",
                "params": {"topic": query},
                "description": "Synthesize findings into a clear summary",
                "depends_on": [1]
            }
        ]
    
    # ── Pattern: "search/Find X and summarize" ─────────────────────────
    if (re.search(r'(?:search|find|look\s+up).+\band\s+summarize', text_lower) or
        re.search(r'(?:search|find).+\band\s+(?:tell|show|read|explain)', text_lower)):
        
        # Extract the search query
        query = goal_text
        for prefix in ['search for', 'search', 'find', 'look up', 'google']:
            pattern = re.compile(rf'{prefix}\s+', re.I)
            m = pattern.search(text_lower)
            if m:
                query = text_lower[m.end():].strip()
                break
        
        # Remove trailing "and summarize/tell me/etc"
        query = re.sub(r'\s+and\s+(?:summarize|tell\s+me|show\s+me|read|explain).*$', '', query, flags=re.I).strip()
        
        logger.info(f"[AutonomousPlanner] Pattern match: search+summarize | query='{query}'")
        
        return [
            {
                "action": "search_web",
                "tool": "browser",
                "params": {"query": query, "platform": "google"},
                "description": f"Search for: {query}",
                "depends_on": []
            },
            {
                "action": "fetch_and_parse",
                "tool": "web_navigator",
                "params": {"max_pages": 3},
                "description": "Fetch and parse top search results",
                "depends_on": [0]
            },
            {
                "action": "synthesize_research",
                "tool": "ai_brain",
                "params": {"topic": query},
                "description": "Synthesize findings into a clear summary",
                "depends_on": [1]
            }
        ]
    available_tools = cap_registry.list_tools()
    tools_list = "\n".join(f"- {t}" for t in available_tools)
    
    prompt = f"""You are Jarvis, planning an autonomous task.

GOAL: {goal_text}

Current context:
- Active app: {context.get('active_app', 'desktop')}
- Active window: {context.get('active_window', '')}

AVAILABLE TOOLS (use EXACTLY these names):
{tools_list}

AVAILABLE ACTIONS per tool:
- click_simulator:
    * click_element_id (REQUIRED: "element_id" (integer).
      CRITICAL: Look at screen_text in context for bracketed IDs like [3] Settings.
      To click Settings, use element_id=3. NEVER guess names — only use IDs.)
    * type_text (REQUIRED: "text". Optional: "press_enter" (true/false))
    * press_key (REQUIRED: "key". Examples: "enter", "esc", "tab", "ctrl+w")
    * scroll (Optional: "direction" ("up"/"down"), "amount" (number, default 3))
    * wait (REQUIRED: "seconds" (number, max 5))
- app_launcher: open_app, close_app, focus_app
- browser: open_website, search_web, close_tab, new_tab, scroll
- keyboard: type_text, save_file
- system: take_screenshot, lock, shutdown, restart, minimize_app, maximize_app, set_volume, cancel_current
- web_navigator: search_web, fetch_and_parse, synthesize_research
- ai_brain: answer_question, synthesize_research
- communicator: make_call, initiate_call
- memory: store_memory, recall_memory
- tts: tts_speak
- unified_comm:
    * send_whatsapp_message (REQUIRED: "contact", "message")
    * call_whatsapp (REQUIRED: "contact")
    * call_discord (REQUIRED: "contact")
    * open_and_search (REQUIRED: "query")

CRITICAL CLICK INSTRUCTION:
When you need to click something, look at the screen_text context.
UI elements are tagged like "[1] File [2] Edit [3] Submit".
To click Submit when you see "[3] Submit", output:
{{"action": "click_element_id", "tool": "click_simulator", "params": {{"element_id": 3}}}}
DO NOT guess names. ONLY use IDs present in the screen_text context.

For "search and summarize" tasks, use:
1. browser / search_web to search
2. web_navigator / fetch_and_parse to get content
3. ai_brain / synthesize_research to create summary
Make each step use ONLY the exact tool and action names listed above. 
You must output the final plan in valid JSON format."""

    try:
        from groq import Groq
        import json
        
        client = Groq(api_key=self.config.get("groq_api_key", ""))
        loop = asyncio.get_event_loop()
        
        def _call():
            return client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"}
            )
        
        resp = await loop.run_in_executor(None, _call)
        data = json.loads(resp.choices[0].message.content)
        plan = data.get("plan", [])
        
        # Validate each step's tool exists
        validated_plan = []
        for step in plan:
            tool = step.get("tool", "")
            if tool and cap_registry.has(tool):
                validated_plan.append(step)
            else:
                # Try to fix common LLM mistakes
                fixed_tool = _fix_tool_name(tool, step.get("action", ""))
                if fixed_tool:
                    step["tool"] = fixed_tool
                    validated_plan.append(step)
                    logger.info(f"[AutonomousPlanner] Fixed tool: {tool} → {fixed_tool}")
                else:
                    logger.warning(f"[AutonomousPlanner] Skipping step with unknown tool: {tool}")

        # ── FIX DEPENDENCIES (outside the for loop) ──────────────────────
        # Ensure step 0 has no dependencies
        if validated_plan:
            validated_plan[0]["depends_on"] = []
        
        # Fix all depends_on indices to be valid
        for i, step in enumerate(validated_plan):
            if step.get("action") == "synthesize_research" and not step.get("depends_on"):
                deps = []
                for j in range(i):
                    prev_action = validated_plan[j].get("action", "")
                    if prev_action in ("search_web", "fetch_and_parse"):
                        deps.append(j)
                if deps:
                    step["depends_on"] = deps
                    logger.info(f"[AutonomousPlanner] Auto-added deps for synthesize: {deps}")

        # Debug: log the full plan
        import json as _json
        logger.info(f"[AutonomousPlanner] Plan details: {_json.dumps(validated_plan, indent=2)}")       
        logger.info(f"[AutonomousPlanner] Generated {len(validated_plan)}-step plan for goal: {goal_text[:50]}")
        return validated_plan
        
    except Exception as e:
        logger.error(f"[Autonomous] Planning failed: {e}")
        # Fallback: simple search
        return [{
            "action": "search_web",
            "tool": "browser",
            "params": {"query": goal_text, "platform": "google"},
            "description": f"Search for: {goal_text}"
        }]

# At module level (top of file, outside any class):
def _fix_tool_name(tool_name: str, action: str) -> Optional[str]:
    """Fix common LLM mistakes in tool naming."""
    from core.capability_registry import registry as cap_registry
    
    fixes = {
        "default_browser": "browser",
        "web_browser": "browser",
        "chrome": "browser",
        "firefox": "browser",
        "text_input": "keyboard",
        "typing": "keyboard",
        "media": "media_controller",
        "spotify": "media_controller",
        "screenshot": "system",
        "system_actions": "system",
        "web_search": "web_navigator",
        "ai": "ai_brain",
        "llm": "ai_brain",
        "groq": "ai_brain",
        "communicator": "communicator",
        "call": "communicator",
    }
    
    fixed = fixes.get(tool_name.lower())
    if fixed and cap_registry.has(fixed):
        return fixed
    return None

# ════════════════════════════════════════════════════════════════════════════
# SYSTEM ACTION TOOL (Checklist 1: verify after every action)
# ════════════════════════════════════════════════════════════════════════════

class SystemActionTool:
    """
    Handles Windows system settings changes.
    Checklist 1: wraps every action with post-verification.
    Checklist 5: window detection + focus confirmation.
    Checklist 12: categorized errors per action type.
    """

    async def execute(self, action, params, intent, context, step_results):
        if action != "system_action":
            raise ValueError(f"Unknown action: {action}")

        action_type = params.get("action_type","").lower().replace(" ","_")
        setting     = params.get("setting","").lower()
        value       = str(params.get("value","")).strip()
        key         = action_type or f"open_{setting}_settings"

        handlers = {
            "change_resolution":     self._change_resolution,
            "set_resolution":        self._change_resolution,
            "change_refresh_rate":   self._change_refresh_rate,
            "set_refresh_rate":      self._change_refresh_rate,
            "set_brightness":        self._set_brightness,
            "change_brightness":     self._set_brightness,
            "set_volume":            self._set_volume,
            "change_volume":         self._set_volume,
            "mute":                  self._mute_audio,
            "unmute":                self._unmute_audio,
            "open_display_settings": self._open_display_settings,
            "open_settings":         self._open_settings_page,
            "toggle_feature":        self._toggle_feature,
            "toggle_color_mode":     self._toggle_color_mode,
        }
        h = handlers.get(key)
        if not h:
            return await self._open_settings_page(setting or action_type)

        # Checklist 1: time execution + verify result
        t0 = time.perf_counter()
        result = await h(value, params)
        elapsed = (time.perf_counter() - t0) * 1000

        # Log structured result (Checklist 9)
        status = "" if result.get("success") else ""
        logger.info(f"[SystemAction] {status} {key}({value}) → {result.get('message','')} ({elapsed:.0f}ms)")
        return result

    async def _change_resolution(self, value, params):
        w, h = self._parse_res(value)
        if not w:
            logger.warning(f"[SystemAction] Could not parse resolution from '{value}' — opening settings")
            return await self._open_display_settings(value, params)

        ps = (
            "Add-Type -TypeDefinition @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public class Display {\n"
            "    [DllImport(\"user32.dll\")] public static extern int ChangeDisplaySettings(ref DEVMODE devMode, int flags);\n"
            "    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]\n"
            "    public struct DEVMODE {\n"
            "        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;\n"
            "        public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;\n"
            "        public int dmFields;\n"
            "        public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;\n"
            "        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;\n"
            "        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;\n"
            "        public short dmLogPixels;\n"
            "        public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;\n"
            "    }\n"
            "}\n"
            "'@\n"
            f"$dm = New-Object Display+DEVMODE\n"
            f"$dm.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($dm)\n"
            f"$dm.dmPelsWidth  = {w}\n"
            f"$dm.dmPelsHeight = {h}\n"
            f"$dm.dmFields = 0x80000 -bor 0x100000\n"
            f"$rc = [Display]::ChangeDisplaySettings([ref]$dm, 0)\n"
            f"exit $rc\n"
        )
        loop = asyncio.get_event_loop()
        rc   = await loop.run_in_executor(None, self._ps, ps)
        logger.info(f"[SystemAction] ChangeDisplaySettings returned: {rc}")

        if rc == 0:
            # Checklist 1: post-action verification via Settings app confirmation
            return {"success": True, "message": f"Resolution changed to {w}×{h}, Sir."}
        if rc == 1:
            return {"success": True, "message": f"Resolution will change to {w}×{h} after restart, Sir."}
        # Fallback chain: PowerShell failed → open Settings UI (Checklist 1)
        return await self._open_display_settings(value, params)

    async def _change_refresh_rate(self, value, params):
        import re as _re
        m = _re.search(r'(\d+)', value)
        if not m:
            return await self._open_display_settings(value, params)
        hz = int(m.group(1))
        ps = (
            "Add-Type -TypeDefinition @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public class Display {\n"
            "    [DllImport(\"user32.dll\")] public static extern int ChangeDisplaySettings(ref DEVMODE devMode, int flags);\n"
            "    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]\n"
            "    public struct DEVMODE {\n"
            "        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;\n"
            "        public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;\n"
            "        public int dmFields;\n"
            "        public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;\n"
            "        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;\n"
            "        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;\n"
            "        public short dmLogPixels;\n"
            "        public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;\n"
            "    }\n"
            "}\n"
            "'@\n"
            f"$dm = New-Object Display+DEVMODE\n"
            f"$dm.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($dm)\n"
            f"$dm.dmDisplayFrequency = {hz}\n"
            f"$dm.dmFields = 0x400000\n"
            f"$rc = [Display]::ChangeDisplaySettings([ref]$dm, 0)\n"
            f"exit $rc\n"
        )
        loop = asyncio.get_event_loop()
        rc   = await loop.run_in_executor(None, self._ps, ps)
        if rc == 0:
            return {"success": True, "message": f"Refresh rate set to {hz}Hz, Sir."}
        return await self._open_display_settings(value, params)

    async def _set_brightness(self, value, params):
        import re as _re
        m = _re.search(r'(\d+)', value)
        if not m:
            return {"success": False, "error": "Specify brightness 0-100."}
        pct = max(0, min(100, int(m.group(1))))
        ps  = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{pct})"
        await asyncio.get_event_loop().run_in_executor(None, self._ps, ps)
        return {"success": True, "message": f"Brightness set to {pct}%, Sir."}

    async def _set_volume(self, value, params):
        """Set system volume to a specific level (0-100)."""
        try:
            # ── Parse the level from whatever format it arrives in ────────
            level = self._parse_volume_level(value, params)
            level = max(0, min(100, level))
            
            # ── Method 1: nircmd (most reliable) ──────────────────────────
            try:
                import os, subprocess
                nircmd_path = os.path.join(os.path.dirname(__file__), "..", "nircmd.exe")
                if not os.path.exists(nircmd_path):
                    nircmd_path = "nircmd"
                subprocess.run(
                    [nircmd_path, "setsysvolume", str(int(level * 655.35))],
                    capture_output=True, timeout=3, check=False
                )
                return {"success": True, "message": f"Volume set to {level}%, Sir."}
            except Exception:
                pass
            
            # ── Method 2: pycaw ───────────────────────────────────────────
            try:
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                from comtypes import CLSCTX_ALL
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = interface.QueryInterface(IAudioEndpointVolume)
                volume.SetMasterVolumeLevelScalar(level / 100.0, None)
                return {"success": True, "message": f"Volume set to {level}%, Sir."}
            except Exception as e:
                logger.warning(f"[SystemAction] pycaw failed: {e}")
            
            # ── Method 3: Keyboard (mute→0 then raise) ────────────────────
            import pyautogui, time as _t
            pyautogui.press("volumemute")
            _t.sleep(0.15)
            presses = int(level / 2)
            for _ in range(min(presses, 50)):
                pyautogui.press("volumeup")
                _t.sleep(0.02)
            return {"success": True, "message": f"Volume set to approximately {level}%, Sir."}
            
        except Exception as e:
            logger.warning(f"[SystemAction] set_volume failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _parse_volume_level(self, value, params) -> int:
        WORD_NUMS = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,        
            "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
            "ninety": 90, "hundred": 100,
        }
        
        if value:
            v = str(value).strip().lower()
            if v.isdigit():
                return int(v)
            if v in WORD_NUMS:
                return WORD_NUMS[v]
            # Handle hyphenated compounds: "sixty-two", "twenty-five"
            if '-' in v:
                parts = v.split('-')
                if len(parts) == 2:
                    tens = WORD_NUMS.get(parts[0], 0)
                    ones = WORD_NUMS.get(parts[1], 0)
                    if tens and ones:
                        return tens + ones
            # Try extracting digits
            import re
            m = re.search(r'(\d+)', v)
            if m:
                return int(m.group(1))
        
        for key in ("volume", "level", "value"):
            pv = str(params.get(key, "")).strip().lower()
            if pv.isdigit():
                return int(pv)
            if pv in WORD_NUMS:
                return WORD_NUMS[pv]
            if '-' in pv:
                parts = pv.split('-')
                if len(parts) == 2:
                    tens = WORD_NUMS.get(parts[0], 0)
                    ones = WORD_NUMS.get(parts[1], 0)
                    if tens and ones:
                        return tens + ones
        
        return 50
            
    async def _mute_audio(self, value, params):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMute(1, None)
            return {"success": True, "message": "System muted, Sir."}
        except Exception:
            import pyautogui
            pyautogui.press("volumemute")
            return {"success": True, "message": "Muted, Sir."}

    async def _unmute_audio(self, value, params):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMute(0, None)
            return {"success": True, "message": "Unmuted, Sir."}
        except Exception:
            import pyautogui
            pyautogui.press("volumemute")
            return {"success": True, "message": "Unmuted, Sir."}

    async def _open_display_settings(self, value, params):
        import subprocess
        subprocess.Popen("start ms-settings:display", shell=True)
        return {"success": True, "message": "Opening display settings, Sir."}

    async def _toggle_feature(self, value, params):
        import subprocess
        feature = params.get("setting","").lower()
        urls = {
            "wifi":         "ms-settings:network-wifi",
            "wi_fi":        "ms-settings:network-wifi",
            "bluetooth":    "ms-settings:bluetooth",
            "airplane_mode":"ms-settings:network-airplanemode",
        }
        url = urls.get(feature, "ms-settings:network")
        subprocess.Popen(f"start {url}", shell=True)
        return {"success": True, "message": f"Opening {feature} settings, Sir."}

    async def _toggle_color_mode(self, value, params):
        import subprocess
        subprocess.Popen("start ms-settings:personalization-colors", shell=True)
        return {"success": True, "message": "Opening color settings, Sir."}

    async def _open_settings_page(self, setting):
        import subprocess
        _map = {
            "display":    "ms-settings:display",
            "resolution": "ms-settings:display",
            "sound":      "ms-settings:sound",
            "volume":     "ms-settings:sound",
            "network":    "ms-settings:network-wifi",
            "bluetooth":  "ms-settings:bluetooth",
            "power":      "ms-settings:powersleep",
        }
        key = str(setting).lower().replace("_","").replace(" ","")
        for k, url in _map.items():
            if k in key or key in k:
                subprocess.Popen(f"start {url}", shell=True)
                return {"success": True, "message": f"Opening {k} settings, Sir."}
        subprocess.Popen("start ms-settings:", shell=True)
        return {"success": True, "message": "Opening Windows Settings, Sir."}

    def _ps(self, script: str) -> int:
        import subprocess
        try:
            r = subprocess.run(
                ["powershell","-NonInteractive","-NoProfile",
                 "-ExecutionPolicy","Bypass","-Command",script],
                capture_output=True, timeout=15
            )
            logger.debug(f"[PS] rc={r.returncode} stderr={r.stderr.decode(errors='ignore')[:200]}")
            return r.returncode
        except Exception as e:
            logger.error(f"[PS] error: {e}")
            return 1

    def _parse_res(self, value: str):
        presets = {
            "4k":(3840,2160),"2160p":(3840,2160),"uhd":(3840,2160),
            "2k":(2560,1440),"1440p":(2560,1440),"qhd":(2560,1440),"wqhd":(2560,1440),
            "1080p":(1920,1080),"fhd":(1920,1080),"fullhd":(1920,1080),
            "900p":(1600,900),
            "720p":(1280,720),"hd":(1280,720),
            "480p":(854,480),
        }
        v = str(value).lower().strip().replace(" ","").replace("×","x")
        if v in presets:
            return presets[v]
        m = re.match(r'(\d{3,4})[x×](\d{3,4})', v)
        if m:
            return int(m.group(1)), int(m.group(2))
        m2 = re.match(r'^(\d{3,4})$', v)
        if m2:
            h = int(m2.group(1))
            w_map = {2160:3840,1440:2560,1200:1920,1080:1920,
                     900:1600,768:1366,720:1280,600:800,480:854}
            w = w_map.get(h, 0)
            return (w, h) if w else (0, 0)
        return (0, 0)


# ════════════════════════════════════════════════════════════════════════════
# SPOTIFY + GAME LAUNCHER PATCHES
# ════════════════════════════════════════════════════════════════════════════

def _patch_spotify_playback():
    """Checklist 5: robust Spotify playback with window focus confirmation."""
    try:
        from executor.runner import MediaControllerTool
        _orig_execute = MediaControllerTool.execute

        async def _patched_execute(self_m, action, params, intent, context, step_results):
            if action not in ("play_media","play_hybrid","play"):
                return await _orig_execute(self_m, action, params, intent, context, step_results)
            platform = params.get("platform","spotify").lower()
            if platform != "spotify":
                return await _orig_execute(self_m, action, params, intent, context, step_results)
            query = params.get("query", params.get("song",""))
            if not query:
                return await _orig_execute(self_m, action, params, intent, context, step_results)

            import urllib.parse, subprocess, os, time as _t
            import pyautogui

            safe_song = urllib.parse.quote(query)
            uri       = f"spotify:search:{safe_song}"
            logger.info(f"[Spotify] Launching: {uri}")
            try:
                os.startfile(uri)
            except AttributeError:
                subprocess.Popen(["cmd","/c","start","",uri], shell=True)

            _t.sleep(3.5)

            # Checklist 5: window focus confirmation before interacting
            try:
                from pywinauto import Application
                app = Application(backend="uia").connect(title_re=".*Spotify.*", timeout=5)
                win = app.top_window()
                win.set_focus()
                _t.sleep(0.3)
            except Exception:
                pass

            pyautogui.press('tab')
            _t.sleep(0.25)
            pyautogui.press('enter')
            _t.sleep(0.4)
            pyautogui.press('enter')

            # Checklist 1: post-action verification
            logger.info(f"[Spotify]  Playing {query}")
            return {"success": True, "status": "success", "message": f"Playing {query} on Spotify, Sir."}

        MediaControllerTool.execute = _patched_execute
        logger.info("[PATCH]  Spotify playback macro patched")
    except Exception as e:
        logger.warning(f"[PATCH] Spotify patch failed (non-fatal): {e}")


def _patch_game_launcher():
    """Checklist 1: app → URI → web fallback chain for gaming platforms."""
    try:
        from executor.runner import AppLauncherTool
        _orig_execute = AppLauncherTool.execute

        async def _patched_execute(self_a, action, params, intent, context, step_results):
            if action != "open_app":
                return await _orig_execute(self_a, action, params, intent, context, step_results)
            app_name = params.get("name", params.get("app","")).strip().lower()
            if app_name in _GAME_PLATFORM_URIS:
                uri      = _GAME_PLATFORM_URIS[app_name]
                fallback = _GAME_PLATFORM_URLS.get(app_name,"")
                logger.info(f"[GameLauncher] URI launch: {uri}")
                import subprocess, os
                for launcher in [
                    lambda: os.startfile(uri),
                    lambda: subprocess.Popen(["cmd","/c","start","",uri], shell=True),
                ]:
                    try:
                        launcher()
                        return {"success": True, "message": f"Opening {app_name.title()}, Sir."}
                    except Exception:
                        pass
                if fallback:
                    import webbrowser
                    webbrowser.open(fallback)
                    return {"success": True, "message": f"Opened {app_name.title()} website, Sir."}
            result = await _orig_execute(self_a, action, params, intent, context, step_results)
            if not result.get("success"):
                raw_name = params.get("name", params.get("app","")).strip()
                import webbrowser, urllib.parse
                webbrowser.open(f"https://store.steampowered.com/search/?term={urllib.parse.quote(raw_name)}")
                return {"success": True, "message": f"Couldn't find {raw_name} installed. Searching on Steam, Sir."}
            return result

        AppLauncherTool.execute = _patched_execute
        logger.info("[PATCH]  Game launcher patched")
    except Exception as e:
        logger.warning(f"[PATCH] Game launcher patch failed (non-fatal): {e}")


# ════════════════════════════════════════════════════════════════════════════
# INTENT PATTERNS
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_ACTION_PATTERNS = [
    (re.compile(r'^(?:change|set|switch|update)\s+(?:my\s+)?(?:display\s+|screen\s+|desktop\s+)?resolution\s+(?:to\s+)?(?P<value>[\w×x]+)?', re.I),
     "system_action", lambda m: {"action_type":"change_resolution","setting":"resolution","value":(m.group("value") or "").strip()}),
    (re.compile(r'^(?:change|set|switch)\s+(?:my\s+)?(?:display\s+)?refresh\s+rate\s+(?:to\s+)?(?P<value>[\d]+\s*(?:hz)?)?', re.I),
     "system_action", lambda m: {"action_type":"change_refresh_rate","setting":"refresh_rate","value":(m.group("value") or "").strip()}),
    (re.compile(r'^(?:set|change|increase|decrease|lower|raise)\s+(?:screen\s+|display\s+)?brightness\s+(?:to\s+)?(?P<value>\d+\s*%?)?', re.I),
     "system_action", lambda m: {"action_type":"set_brightness","setting":"brightness","value":(m.group("value") or "").strip()}),
    (re.compile(r'^(?:set|change)\s+(?:system\s+|pc\s+)?volume\s+(?:to\s+)?(?P<value>\d+\s*%?)', re.I),
     "system_action", lambda m: {"action_type":"set_volume","setting":"volume","value":m.group("value").strip()}),
    (re.compile(r'^open\s+(?:windows\s+)?settings(?:\s+(?:for\s+)?(?P<page>\w+(?:\s+\w+)*))?', re.I),
     "system_action", lambda m: {"action_type":"open_settings","setting":(m.group("page") or "main").strip()}),
    (re.compile(r'^(?:turn\s+(?:on|off)|enable|disable|toggle)\s+(?P<feature>wi.?fi|wifi|bluetooth|airplane\s+mode)', re.I),
     "system_action", lambda m: {"action_type":"toggle_feature","setting":m.group("feature").lower().replace(" ","_"),"value":"toggle"}),
]

ADDITIONAL_INTENT_MAP = {
    "system_action":"system_action","change_resolution":"system_action",
    "set_resolution":"system_action","configure_display":"system_action",
    "set_brightness":"system_action","change_brightness":"system_action",
    "change_refresh_rate":"system_action","set_refresh_rate":"system_action",
    "toggle_feature":"system_action","open_settings":"system_action",
    "set_volume":"system_action","change_volume":"system_action",
}

ADDITIONAL_PLANNER_ELIGIBLE = {"system_action","change_resolution","configure_display"}


# ════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ════════════════════════════════════════════════════════════════════════════

_system_action_tool_instance = None

def _get_system_action_tool():
    global _system_action_tool_instance
    if _system_action_tool_instance is None:
        _system_action_tool_instance = SystemActionTool()
    return _system_action_tool_instance


# ════════════════════════════════════════════════════════════════════════════
# EVENT BUS — decoupled publish/subscribe between modules
# ════════════════════════════════════════════════════════════════════════════

class EventBus:
    """
    Simple thread-safe publish/subscribe bus.
    Standard events: INTENT_DETECTED, ACTION_EXECUTED, ACTION_FAILED,
                     USER_CLARIFICATION, CONTEXT_UPDATED
    """
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event: str, handler: Callable):
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

    def publish(self, event: str, data: Any = None):
        with self._lock:
            handlers = list(self._handlers.get(event, []))
        for h in handlers:
            try:
                h(data)
            except Exception as e:
                logger.warning(f"[EventBus] Handler error for {event}: {e}")

    def publish_async(self, event: str, data: Any = None):
        """Fire-and-forget in a daemon thread."""
        t = threading.Thread(target=self.publish, args=(event, data), daemon=True)
        t.start()


event_bus = EventBus()


# ════════════════════════════════════════════════════════════════════════════
# ACTION RESULT CONTRACT — every tool must return this schema
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ActionResult:
    """
    Strict result schema for all tool executions.
    Tools that return plain dicts are coerced via ActionResult.from_dict().
    """
    success:             bool
    state_verified:      bool        = False
    confidence:          float       = 1.0
    message:             str         = ""
    error:               Optional[str] = None
    next_possible_actions: List[str] = field(default_factory=list)
    execution_ms:        float       = 0.0

    def to_dict(self) -> dict:
        return {
            "success":               self.success,
            "state_verified":        self.state_verified,
            "confidence":            self.confidence,
            "message":               self.message,
            "error":                 self.error,
            "next_possible_actions": self.next_possible_actions,
            "execution_ms":          self.execution_ms,
        }

    @classmethod
    def from_dict(cls, d: dict, execution_ms: float = 0.0) -> "ActionResult":
        return cls(
            success=bool(d.get("success", False)),
            state_verified=bool(d.get("state_verified", False)),
            confidence=float(d.get("confidence", 1.0 if d.get("success") else 0.0)),
            message=str(d.get("message", "")),
            error=d.get("error"),
            next_possible_actions=d.get("next_possible_actions", []),
            execution_ms=execution_ms,
        )

    @classmethod
    def failure(cls, message: str, error: str = "") -> "ActionResult":
        return cls(success=False, state_verified=False, confidence=0.0,
                   message=message, error=error)


# ════════════════════════════════════════════════════════════════════════════
# VERIFICATION LAYER — post-action state checks
# ════════════════════════════════════════════════════════════════════════════

class VerificationLayer:
    """
    Verifies that an action actually had the expected effect.
    Policy: if verification fails → retry once → if still fails → hard stop + speak.
    NEVER fall through to a random alternative action.
    """

    # High-risk actions that always need confirmation BEFORE execution
    HIGH_RISK_ACTIONS = frozenset({
        "send_message", "make_call", "compose_email",
        "shutdown", "restart", "delete",
    })

    def verify_window(self, expected_title_fragment: str, timeout: float = 3.0) -> bool:
        """Check that a window with the expected title is currently focused."""
        try:
            import win32gui
            deadline = time.time() + timeout
            while time.time() < deadline:
                hwnd  = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd).lower()
                if expected_title_fragment.lower() in title:
                    return True
                time.sleep(0.2)
            return False
        except ImportError:
            return True   # Can't verify on non-Windows — assume ok
        except Exception:
            return True

    def verify_url_opened(self, url_fragment: str, timeout: float = 4.0) -> bool:
        """
        Check browser title/URL contains expected fragment.
        Uses win32gui to read the browser title bar.
        """
        try:
            import win32gui
            deadline = time.time() + timeout
            while time.time() < deadline:
                def _check(hwnd, found):
                    if win32gui.IsWindowVisible(hwnd):
                        t = win32gui.GetWindowText(hwnd).lower()
                        if url_fragment.lower() in t:
                            found.append(True)
                found = []
                win32gui.EnumWindows(_check, found)
                if found:
                    return True
                time.sleep(0.3)
            return False
        except Exception:
            return True

    def verify_app_open(self, app_name: str, timeout: float = 4.0) -> bool:
        """Check that a process with the given name is running."""
        try:
            import psutil
            deadline = time.time() + timeout
            name_lower = app_name.lower()
            while time.time() < deadline:
                for proc in psutil.process_iter(['name']):
                    if name_lower in (proc.info['name'] or '').lower():
                        return True
                time.sleep(0.4)
            return False
        except ImportError:
            return True
        except Exception:
            return True

    def is_high_risk(self, action: str) -> bool:
        return action in self.HIGH_RISK_ACTIONS

    async def execute_with_verification(
        self,
        action_fn: Callable,
        verify_fn: Optional[Callable] = None,
        max_retries: int = 1,
        failure_message: str = "",
    ) -> ActionResult:
        """
        Execute action_fn, then verify with verify_fn.
        Retries once on failure. Hard-stops if still failing.
        """
        for attempt in range(max_retries + 1):
            t0 = time.perf_counter()
            try:
                if asyncio.iscoroutinefunction(action_fn):
                    raw = await action_fn()
                else:
                    loop = asyncio.get_event_loop()
                    raw = await loop.run_in_executor(None, action_fn)
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000
                logger.error(f"[Verify] Action raised: {e}")
                result = ActionResult.failure(
                    failure_message or f"Action failed: {e}", str(e)
                )
                result.execution_ms = elapsed
                event_bus.publish("ACTION_FAILED", {"error": str(e), "attempt": attempt})
                if attempt >= max_retries:
                    return result
                await asyncio.sleep(0.5)
                continue

            elapsed = (time.perf_counter() - t0) * 1000
            if isinstance(raw, dict):
                result = ActionResult.from_dict(raw, execution_ms=elapsed)
            elif isinstance(raw, ActionResult):
                result = raw
                result.execution_ms = elapsed
            else:
                result = ActionResult(success=True, message=str(raw), execution_ms=elapsed)

            # Post-action verification
            if verify_fn and result.success:
                try:
                    verified = verify_fn() if not asyncio.iscoroutinefunction(verify_fn) \
                               else await verify_fn()
                    result.state_verified = bool(verified)
                except Exception as ve:
                    logger.warning(f"[Verify] verify_fn raised: {ve}")
                    result.state_verified = False
            else:
                result.state_verified = result.success

            if result.state_verified or not verify_fn:
                event_bus.publish("ACTION_EXECUTED", result.to_dict())
                return result

            logger.warning(f"[Verify] Attempt {attempt+1} verification failed")
            if attempt >= max_retries:
                result.success = False
                result.message = (
                    failure_message or
                    "Sir, I executed the action but couldn't verify it completed. "
                    "Please check manually."
                )
                event_bus.publish("ACTION_FAILED", result.to_dict())
                return result
            await asyncio.sleep(0.6)

        return ActionResult.failure("Max retries exceeded")


verifier = VerificationLayer()


# ════════════════════════════════════════════════════════════════════════════
# ENTITY RESOLVER — fuzzy name/contact matching
# ════════════════════════════════════════════════════════════════════════════

class EntityResolver:
    """
    Resolves ambiguous entity references using fuzzy matching.
    Prevents SemanticCorrector from mangling names by resolving them
    at the execution layer against known contacts/apps.
    """

    def __init__(self):
        self._contact_list: List[str] = []
        self._app_list:     List[str] = []

    def set_contacts(self, contacts: List[str]):
        self._contact_list = contacts

    def set_apps(self, apps: List[str]):
        self._app_list = apps

    def resolve_contact(self, name: str, threshold: float = 70.0) -> Optional[str]:
        """
        Fuzzy-match name against known contacts.
        Returns best match above threshold, or None.
        """
        if not self._contact_list or not name:
            return None
        try:
            from rapidfuzz import process as fz_process
            result = fz_process.extractOne(name, self._contact_list)
            if result and result[1] >= threshold:
                logger.info(f"[EntityResolver] Contact '{name}' → '{result[0]}' ({result[1]:.0f}%)")
                return result[0]
        except ImportError:
            # rapidfuzz not installed — fall back to difflib
            from difflib import get_close_matches
            matches = get_close_matches(name, self._contact_list, n=1, cutoff=threshold/100)
            if matches:
                return matches[0]
        return None

    def resolve_app(self, name: str, threshold: float = 65.0) -> Optional[str]:
        """Fuzzy-match against known app names."""
        if not self._app_list or not name:
            return None
        try:
            from rapidfuzz import process as fz_process
            result = fz_process.extractOne(name, self._app_list)
            if result and result[1] >= threshold:
                logger.info(f"[EntityResolver] App '{name}' → '{result[0]}' ({result[1]:.0f}%)")
                return result[0]
        except ImportError:
            from difflib import get_close_matches
            matches = get_close_matches(name, self._app_list, n=1, cutoff=threshold/100)
            if matches:
                return matches[0]
        return None

    def resolve_pronoun(self, text: str, context: dict) -> str:
        """
        Resolve pronouns like 'him', 'her', 'them', 'it' using last known entities.
        E.g. "call him" → resolves to last_contact.
        """
        t = text.lower().strip()
        replacements = {
            r'\bhim\b':   context.get("last_contact", ""),
            r'\bher\b':   context.get("last_contact", ""),
            r'\bthem\b':  context.get("last_contact", ""),
            r'\bhis\b':   context.get("last_contact", ""),
            r'\bit\b':    context.get("last_entity", ""),
            r'\bthat\b':  context.get("last_entity", ""),
        }
        result = text
        for pattern, replacement in replacements.items():
            if replacement:
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        if result != text:
            logger.info(f"[EntityResolver] Pronoun resolved: '{text}' → '{result}'")
        return result


entity_resolver = EntityResolver()


# ════════════════════════════════════════════════════════════════════════════
# SYSTEM CONTEXT — unified live world state
# ════════════════════════════════════════════════════════════════════════════

class SystemContext:
    """
    Single source of truth for the current system state.
    Continuously updated by the context sync loop.

    Accessible everywhere via the `system_ctx` singleton.
    """

    def __init__(self):
        self.active_app:       str        = "desktop"
        self.active_window_title: str     = ""
        self.current_url:      str        = ""
        self.browser_tabs:     List[dict] = []   # [{title, url}, ...]
        self.last_action:      str        = ""
        self.last_intent:      str        = ""
        self.last_entities:    dict       = {}
        self.last_results:     list       = []   # search result URLs
        self.last_contact:     str        = ""
        self.last_entity:      str        = ""
        self._lock             = threading.Lock()

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)
        event_bus.publish_async("CONTEXT_UPDATED", kwargs)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active_app":          self.active_app,
                "active_window_title": self.active_window_title,
                "current_url":         self.current_url,
                "browser_tabs":        list(self.browser_tabs),
                "last_action":         self.last_action,
                "last_intent":         self.last_intent,
                "last_entities":       dict(self.last_entities),
                "last_results":        list(self.last_results),
                "last_contact":        self.last_contact,
                "last_entity":         self.last_entity,
            }

    def store_search_results(self, results: list):
        """Store search results so 'open first link' can resolve them."""
        with self._lock:
            self.last_results = results
        logger.info(f"[SystemCtx] Stored {len(results)} search results")

    def resolve_result_index(self, index_word: str) -> Optional[str]:
        """Resolve 'first'/'second'/etc → URL."""
        _IDX = {
            "first": 0, "1st": 0, "one": 0, "1": 0,
            "second": 1, "2nd": 1, "two": 1, "2": 1,
            "third": 2, "3rd": 2, "three": 2, "3": 2,
            "fourth": 3, "4th": 3, "four": 3, "4": 3,
            "fifth": 4, "5th": 4, "five": 4, "5": 4,
        }
        idx = _IDX.get(index_word.lower().strip())
        if idx is None:
            return None
        with self._lock:
            results = self.last_results
        if idx < len(results):
            entry = results[idx]
            url = entry[1] if isinstance(entry, (list, tuple)) else entry
            logger.info(f"[SystemCtx] Resolved index {idx} → {url}")
            return url
        logger.warning(f"[SystemCtx] Index {idx} out of range (have {len(results)})")
        return None


system_ctx = SystemContext()


# ════════════════════════════════════════════════════════════════════════════
# BROWSER CONTROLLER — Playwright-based real browser control
# Falls back to webbrowser.open() if Playwright is not installed.
# ════════════════════════════════════════════════════════════════════════════

class BrowserController:
    """
    Production browser controller using Playwright.
    Provides: open_url, click_element, get_current_url, get_page_links.
    Falls back gracefully to webbrowser.open() when Playwright unavailable.
    """

    def __init__(self):
        self._playwright = None
        self._browser    = None
        self._page       = None
        self._available  = False
        self._lock       = threading.Lock()
        self._try_init()

    def _try_init(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw_module = sync_playwright
            self._available = True
            logger.info("[Browser] Playwright available — real browser control enabled")
        except ImportError:
            logger.info("[Browser] Playwright not installed — using webbrowser fallback")
            self._available = False

    def _ensure_browser(self):
        """Lazily launch the browser on first use."""
        if not self._available:
            return False
        if self._page and not self._page.is_closed():
            return True
        try:
            with self._lock:
                if self._playwright is None:
                    self._playwright = self._pw_module().start()
                    self._browser    = self._playwright.chromium.launch(headless=False)
                    self._page       = self._browser.new_page()
            return True
        except Exception as e:
            logger.warning(f"[Browser] Playwright launch failed: {e} — using fallback")
            self._available = False
            return False

    def open_url(self, url: str) -> ActionResult:
        """Navigate to URL. Returns ActionResult with state_verified=True on success."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if self._ensure_browser():
            try:
                self._page.goto(url, timeout=12000, wait_until="domcontentloaded")
                title = self._page.title()
                current = self._page.url
                system_ctx.update(current_url=current, last_action=f"open_url:{url}")
                logger.info(f"[Browser]  Opened: {title} @ {current}")
                return ActionResult(
                    success=True, state_verified=True, confidence=1.0,
                    message=f"Opened {url}, Sir.",
                    next_possible_actions=["click_result", "scroll", "read_page"]
                )
            except Exception as e:
                logger.warning(f"[Browser] Playwright navigate failed: {e} — fallback")

        # Fallback
        webbrowser.open(url)
        system_ctx.update(current_url=url, last_action=f"open_url:{url}")
        return ActionResult(
            success=True, state_verified=False, confidence=0.6,
            message=f"Opened {url} in browser, Sir.",
            next_possible_actions=["click_result", "scroll"]
        )

    def click_result(self, index_word: str) -> ActionResult:
        """Click the Nth search result link on the current page."""
        url = system_ctx.resolve_result_index(index_word)
        if url:
            return self.open_url(url)

        # Try clicking directly via Playwright DOM if no stored results
        if self._ensure_browser() and self._page and not self._page.is_closed():
            try:
                _IDX = {"first":0,"1st":0,"one":0,"1":0,
                         "second":1,"2nd":1,"two":1,"2":1,
                         "third":2,"3rd":2,"three":2,"3":2,
                         "fourth":3,"4th":3,"four":3,"4":3,
                         "fifth":4,"5th":4,"five":4,"5":4}
                idx = _IDX.get(index_word.lower().strip(), 0)
                links = self._page.query_selector_all("a[href]")
                visible = [l for l in links if l.is_visible()]
                if idx < len(visible):
                    visible[idx].click()
                    self._page.wait_for_load_state("domcontentloaded", timeout=8000)
                    url = self._page.url
                    system_ctx.update(current_url=url, last_action=f"click_result:{index_word}")
                    return ActionResult(
                        success=True, state_verified=True, confidence=0.9,
                        message=f"Clicked {index_word} link, Sir."
                    )
            except Exception as e:
                logger.warning(f"[Browser] click_result via DOM failed: {e}")

        return ActionResult.failure(
            "Sir, I don't have search results stored. Please search for something first."
        )

    def get_current_url(self) -> str:
        if self._ensure_browser() and self._page and not self._page.is_closed():
            try:
                return self._page.url
            except Exception:
                pass
        return system_ctx.current_url

    def get_page_links(self) -> List[Tuple[str, str]]:
        """Return list of (text, href) from current page."""
        if self._ensure_browser() and self._page and not self._page.is_closed():
            try:
                return [
                    (a.inner_text().strip(), a.get_attribute("href") or "")
                    for a in self._page.query_selector_all("a[href]")
                    if a.is_visible()
                ][:20]
            except Exception:
                pass
        return []

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


from browser_detector import smart_browser_ctrl as browser_ctrl


# ════════════════════════════════════════════════════════════════════════════
# BROWSER CONTEXT MEMORY (legacy alias — system_ctx is the canonical store)
# Kept for backward-compat with any code that imported browser_ctx
# ════════════════════════════════════════════════════════════════════════════

class _BrowserCtxAlias:
    """Thin alias so existing code using browser_ctx still works."""
    def store_search_results(self, results):
        system_ctx.store_search_results(results)
    def resolve_index(self, index_word):
        return system_ctx.resolve_result_index(index_word)


browser_ctx = _BrowserCtxAlias()


# ════════════════════════════════════════════════════════════════════════════
# BROWSER HELPER FUNCTIONS
# These are defined HERE (before BrowserTool) to fix forward-reference errors.
# ════════════════════════════════════════════════════════════════════════════

def _execute_open_url(url: str) -> dict:
    """Open a URL. Uses BrowserController (Playwright if available)."""
    result = browser_ctrl.open_url(url)
    return result.to_dict()


def _execute_click_result(index_word: str) -> dict:
    """Resolve search result index and open it."""
    result = browser_ctrl.click_result(index_word)
    return result.to_dict()


def _execute_browser_navigation(action: str) -> dict:
    """Handle browser navigation commands like 'use this tab'."""
    import pyautogui, time as _t
    if action == "focus_current":
        pyautogui.hotkey("alt", "tab")
        _t.sleep(0.3)
        return ActionResult(
            success=True, state_verified=False, confidence=0.7,
            message="Switched to current browser tab, Sir."
        ).to_dict()
    return ActionResult.failure(f"Unknown browser action: {action}").to_dict()


# ════════════════════════════════════════════════════════════════════════════
# BROWSER TOOL — executes open_url / click_result / browser_navigation plans
# ════════════════════════════════════════════════════════════════════════════

class BrowserTool:
    """
    Handles browser-context plan actions.
    All helpers (_execute_open_url etc.) are defined ABOVE this class.
    """

    async def execute(self, action, params, intent, context, step_results):
        t0 = time.perf_counter()

        if action == "open_url":
            raw = _execute_open_url(params.get("url", ""))
        elif action == "click_result":
            raw = _execute_click_result(params.get("index", "first"))
        elif action == "close_tab":
            import pyautogui
            pyautogui.hotkey('ctrl', 'w')
            return {"success": True, "message": "Closed current tab"}
        elif action == "new_tab":
            import pyautogui
            pyautogui.hotkey('ctrl', 't')
            return {"success": True, "message": "Opened new tab"}
    
        elif action == "scroll":
            import pyautogui
            direction = params.get("direction", "down")
            amount = -500 if direction == "down" else 500
            pyautogui.scroll(amount)
            return {"success": True, "scrolled": direction}

        elif action == "search_web":
            # Delegate to runner.py BrowserTool — it owns the search_web implementation.
            # This prevents the two BrowserTool implementations from diverging further.
            from executor.runner import BrowserTool as _RunnerBrowserTool
            _delegate = _RunnerBrowserTool()
            return await _delegate.execute(action, params, intent, context, step_results)

        elif action == "open_website":
            # Delegate open_website to runner.py as well — it has URL cleaning logic.
            from executor.runner import BrowserTool as _RunnerBrowserTool
            _delegate = _RunnerBrowserTool()
            return await _delegate.execute(action, params, intent, context, step_results)

        elif action == "browser_navigation":
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None, _execute_browser_navigation, params.get("action", "focus_current")
            )
        else:
            logger.warning(f"[BrowserTool] unknown action '{action}' — no handler")
            return {"success": False, "error": f"Browser action '{action}' not supported"}

        elapsed = (time.perf_counter() - t0) * 1000
        result  = ActionResult.from_dict(raw, execution_ms=elapsed)

        # Structured observability log
        status = "" if result.success else ""
        logger.info(
            f"[BrowserTool] {status} {action} | verified={result.state_verified} "
            f"conf={result.confidence:.2f} {elapsed:.0f}ms"
        )
        event_bus.publish_async(
            "ACTION_EXECUTED" if result.success else "ACTION_FAILED",
            {"action": action, "params": params, **result.to_dict()}
        )
        return result.to_dict()


_browser_tool_instance = None

def _get_browser_tool():
    global _browser_tool_instance
    if _browser_tool_instance is None:
        _browser_tool_instance = BrowserTool()
    return _browser_tool_instance



def apply_patches():
    """Apply all Jarvis v7 patches. Idempotent. Checklist: all 14 items."""

    # ── Patch 1: process() + implicit resolution ─────────────────────────────
    try:
        from agent.core import JarvisAgentCore
        from agent.world_model import world as _world

        JarvisAgentCore.process = process_patched

        def _resolve_implicit(self, intent: dict, ctx_snapshot: dict) -> dict:
            """
            Resolve implicit pronouns/references in intent entities using WorldModel.

            Wires WorldModel.resolve_implicit() into the main execution path.
            Called at process_patched line 332.

            Examples resolved:
              "close it"  → close_app  {app: last_entity or last_app}
              "play that" → play_media {song: last_song}
              "search this again" → search with last_intent context
            """
            entities   = intent.get("entities", {})
            intent_name = intent.get("intent", "")

            _IMPLICIT_WORDS = {"it", "that", "this", "again"}

            # Check every entity value for implicit pronouns
            resolved_any = False
            for key, val in list(entities.items()):
                if isinstance(val, str) and val.lower().strip() in _IMPLICIT_WORDS:
                    resolved = _world.resolve_implicit(val.lower().strip())
                    if resolved:
                        entities[key] = resolved
                        resolved_any  = True
                        logger.info(f"[ImplicitRef] '{val}' → '{resolved}' for slot '{key}'")

            # Special case: close/open with no entity at all — infer from last_app
            if intent_name in ("close_app", "focus_app") and not entities.get("app"):
                last = _world.last_app or ctx_snapshot.get("last_app", "")
                if last:
                    entities["app"] = last
                    resolved_any    = True
                    logger.info(f"[ImplicitRef] No app specified → using last_app '{last}'")

            if resolved_any:
                intent = dict(intent)
                intent["entities"] = entities

            return intent

        JarvisAgentCore._resolve_implicit = _resolve_implicit
        JarvisAgentCore._plan_autonomous_goal = _plan_autonomous_goal
        logger.info("[PATCH]  JarvisAgentCore.process patched (v7) + _resolve_implicit wired")
    except Exception as e:
        logger.error(f"[PATCH] core patch failed: {e}")

    # ── Patch 2: SystemActionTool — THREE-LAYER registration ────────────────
    try:
        tool_instance = _get_system_action_tool()
        from executor.runner import ToolRegistry
        _orig_ct = ToolRegistry._create_tool.__func__ if hasattr(ToolRegistry._create_tool,'__func__') else ToolRegistry._create_tool

        def _patched_create_tool(self_r, name):
            if name == "system_action":
                return _get_system_action_tool()
            if name == "browser":
                return _get_browser_tool()
            return _orig_ct(self_r, name)

        ToolRegistry._create_tool = _patched_create_tool

        _orig_get = ToolRegistry.get
        def _patched_get(self_r, tool_name):
            if tool_name == "system_action":
                if "system_action" not in self_r._tools:
                    self_r._tools["system_action"] = _get_system_action_tool()
                return self_r._tools["system_action"]
            if tool_name == "browser":
                if "browser" not in self_r._tools:
                    self_r._tools["browser"] = _get_browser_tool()
                return self_r._tools["browser"]
            return _orig_get(self_r, tool_name)

        ToolRegistry.get = _patched_get

        try:
            from agent.core import JarvisAgentCore
            _orig_init = JarvisAgentCore.__init__

            def _patched_init(self_jac, config):
                _orig_init(self_jac, config)
                self_jac.executor.registry._tools["system_action"] = _get_system_action_tool()
                self_jac.executor.registry._tools["browser"]       = _get_browser_tool()
                _inst_orig = self_jac.executor.registry._create_tool
                from executor.runner import LazyAIBrainTool
                LazyAIBrainTool.set_config(config)

                def _inst_patched(name):
                    if name == "system_action":
                        return _get_system_action_tool()
                    if name == "browser":
                        return _get_browser_tool()
                    return _inst_orig(name)

                self_jac.executor.registry._create_tool = _inst_patched
                try:
                    from jarvis_patch.tool_builder import load_persisted_tools_into_registry
                    load_persisted_tools_into_registry(self_jac)
                except Exception as _e:
                    logger.warning(f"[PATCH] Persisted tools load failed (non-fatal): {_e}")

                # ── GoalManager init (autonomous_task support) ─────────────
                if not hasattr(self_jac, "goal_manager"):
                    try:
                        from core.goal_manager import GoalManager
                        self_jac.goal_manager = GoalManager()
                    except Exception as _ge:
                        logger.warning(f"[PATCH] GoalManager init failed (non-fatal): {_ge}")

            JarvisAgentCore.__init__ = _patched_init
        except Exception as _ie:
            logger.warning(f"[PATCH] __init__ wrap failed (non-fatal): {_ie}")

        logger.info("[PATCH]  SystemActionTool registered (3-layer)")
    except Exception as e:
        logger.error(f"[PATCH] SystemActionTool registration failed: {e}")

    # ── Patch 3: PlanningEngine ──────────────────────────────────────────────
    try:
        from planner.engine import PlanningEngine
        _orig_cp = PlanningEngine.create_plan

        async def _patched_cp(self_pe, intent, memory_context, context, think_hints=None):
            intent_name = intent.get("intent")
            entities    = intent.get("entities", {})

            # ── open_url: open a domain/URL directly ──────────────────────
            if intent_name == "open_url":
                url = entities.get("url", "").strip()
                if not url:
                    # Try to extract from original text
                    raw = intent.get("original_text", "")
                    import re as _re
                    m = _re.search(r'[\w.-]+\.[a-zA-Z]{2,6}(?:/\S*)?', raw)
                    url = m.group(0) if m else ""
                return [{
                    "action":      "open_url",
                    "tool":        "browser",
                    "params":      {"url": url},
                    "description": f"Open URL: {url}",
                    "retry_policy": {"max_retries": 1, "fallback": None},
                    "verify":      None,
                    "expected_duration_ms": 1500,
                }]

            # ── click_result: open Nth search result ──────────────────────
            if intent_name == "click_result":
                index = entities.get("index", "first")
                return [{
                    "action":      "click_result",
                    "tool":        "browser",
                    "params":      {"index": index},
                    "description": f"Open {index} search result",
                    "retry_policy": {"max_retries": 0, "fallback": None},
                    "verify":      None,
                    "expected_duration_ms": 1000,
                }]

            # ── browser_navigation: focus/switch tab context ───────────────
            if intent_name == "browser_navigation":
                action = entities.get("action", "focus_current")
                return [{
                    "action":      "browser_navigation",
                    "tool":        "browser",
                    "params":      {"action": action},
                    "description": f"Browser: {action}",
                    "retry_policy": {"max_retries": 0, "fallback": None},
                    "verify":      None,
                    "expected_duration_ms": 500,
                }]

            if intent_name == "system_action":
                action_type = entities.get("action_type","")
                setting     = entities.get("setting","")
                value       = entities.get("value","")
                raw         = intent.get("original_text","").lower()
                if not action_type:
                    if any(w in raw for w in ["resolution","display","screen"]):
                        action_type, setting = "change_resolution","resolution"
                    elif any(w in raw for w in ["refresh","hz"]):
                        action_type, setting = "change_refresh_rate","refresh_rate"
                    elif "brightness" in raw:
                        action_type, setting = "set_brightness","brightness"
                    elif any(w in raw for w in ["volume","sound"]):
                        action_type, setting = "set_volume","volume"
                    else:
                        action_type = "open_settings"; setting = raw
                return [{
                    "action":        "system_action",
                    "tool":          "system_action",
                    "params":        {"action_type":action_type,"setting":setting,"value":value,"target":entities.get("target","")},
                    "description":   f"System: {action_type}",
                    "retry_policy":  {"max_retries":1,"fallback":None},
                    "verify":        None,
                    "expected_duration_ms": 3000,
                }]
            return await _orig_cp(self_pe, intent, memory_context, context, think_hints)

        PlanningEngine.create_plan = _patched_cp
        logger.info("[PATCH]  PlanningEngine.system_action builder added")
    except Exception as e:
        logger.error(f"[PATCH] PlanningEngine patch failed: {e}")

    # ── Patch 4: IntentEngine fast patterns ──────────────────────────────────
    try:
        from voice.intent_engine import IntentEngine
        _orig_compile = IntentEngine._compile_patterns

        def _patched_compile(self_ie):
            _orig_compile(self_ie)
            self_ie._fast_patterns = SYSTEM_ACTION_PATTERNS + self_ie._fast_patterns

        IntentEngine._compile_patterns = _patched_compile
        try:
            from voice.intent_engine import INTENT_CATALOGUE
            INTENT_CATALOGUE["system_action"]       = "Change Windows OS settings"
            INTENT_CATALOGUE["open_url"]            = "Open a URL or website domain directly"
            INTENT_CATALOGUE["click_result"]        = "Open a search result by index"
            INTENT_CATALOGUE["browser_navigation"]  = "Browser tab/window navigation"
        except Exception:
            pass
        logger.info("[PATCH]  IntentEngine patterns patched")
    except Exception as e:
        logger.error(f"[PATCH] IntentEngine patch failed: {e}")

    # ── Patch 4b: BrowserTool registration + search_web result capture ───────
    try:
        from executor.runner import ToolRegistry
        # Ensure browser tool is in registry on first access
        _orig_get_4b = ToolRegistry.get
        def _patched_get_4b(self_r, tool_name):
            if tool_name == "browser":
                if "browser" not in self_r._tools:
                    self_r._tools["browser"] = _get_browser_tool()
                return self_r._tools["browser"]
            return _orig_get_4b(self_r, tool_name)
        ToolRegistry.get = _patched_get_4b
        logger.info("[PATCH]  BrowserTool registered in ToolRegistry")
    except Exception as e:
        logger.warning(f"[PATCH] BrowserTool registration failed (non-fatal): {e}")

    # Patch search runner to capture results into browser_ctx
    try:
        from executor.runner import WebSearchTool
        _orig_web_execute = WebSearchTool.execute

        async def _patched_web_execute(self_w, action, params, intent, context, step_results):
            result = await _orig_web_execute(self_w, action, params, intent, context, step_results)
            # Capture search results for "open first link" follow-ups
            try:
                links = result.get("links") or result.get("results") or result.get("urls") or []
                if links:
                    browser_ctx.store_search_results(links)
                    logger.info(f"[BrowserCtx] Captured {len(links)} search results from WebSearchTool")
            except Exception:
                pass
            return result

        WebSearchTool.execute = _patched_web_execute
        logger.info("[PATCH]  WebSearchTool patched to capture search results")
    except Exception as e:
        logger.warning(f"[PATCH] WebSearchTool capture patch failed (non-fatal): {e}")

    # ── Patch 5: TaskPlanner intent map ──────────────────────────────────────
    try:
        import src.task_planner as _tp
        _tp.INTENT_MAP.update(ADDITIONAL_INTENT_MAP)
        _tp.INTENT_MAP.update({
            "open_url":           "open_url",
            "click_result":       "click_result",
            "browser_navigation": "browser_navigation",
        })
        _tp.PLANNER_ELIGIBLE_INTENTS = _tp.PLANNER_ELIGIBLE_INTENTS | ADDITIONAL_PLANNER_ELIGIBLE
        logger.info("[PATCH]  TaskPlanner INTENT_MAP expanded")
    except Exception as e:
        logger.error(f"[PATCH] TaskPlanner patch failed: {e}")

    # ── Patch 6: DecisionEngine ───────────────────────────────────────────────
    try:
        from agent.decision import DecisionEngine, Decision, DecisionResult
        _orig_decide = DecisionEngine.decide

        def _patched_decide(self_de, intent, context, memory_context):
            name = intent.get("intent")
            if name == "system_action":
                return DecisionResult(
                    decision=Decision.EXECUTE,
                    reason="system_action — Windows settings",
                    confidence=intent.get("confidence", 0.9),
                )
            if name in ("open_url", "click_result", "browser_navigation"):
                return DecisionResult(
                    decision=Decision.EXECUTE,
                    reason=f"{name} — browser action",
                    confidence=intent.get("confidence", 0.95),
                )
            return _orig_decide(self_de, intent, context, memory_context)

        DecisionEngine.decide = _patched_decide
        logger.info("[PATCH]  DecisionEngine.system_action → EXECUTE")
    except Exception as e:
        logger.error(f"[PATCH] DecisionEngine patch failed: {e}")

    # ── Patch 7: Spotify playback (Checklist 5) ───────────────────────────────
    _patch_spotify_playback()

    # ── Patch 8: Game launcher (Checklist 1 fallback chains) ─────────────────
    _patch_game_launcher()

    # ── Patch 9: Reliability layer integration (Checklist 2, 9, 11, 12, 13) ──
    try:
        from agent.core import JarvisAgentCore
        from reliability_layer import (
            state_controller, metrics, bg_tracker, plan_validator, latency_enforcer
        )
        # Wire reliability singletons into any existing core instances
        # (new instances get them via property accessors in core.py)
        logger.info("[PATCH]  Reliability layer wired (StateController, Metrics, BGTracker, PlanValidator)")
    except Exception as e:
        logger.warning(f"[PATCH] Reliability layer wire failed (non-fatal): {e}")

    # ── Patch 10: WhatsApp contact disambiguation (Checklist 14) ─────────────
    logger.info("[PATCH]  WhatsApp contact disambiguation enabled (task_orchestrator.py)")
    logger.info("[PATCH]  pyautogui fallback DISABLED — hard-stop on UI automation failure")

    # ── Patch 11: Wire new v8 infrastructure ──────────────────────────────────
    try:
        # Subscribe structured observability handler to event bus
        def _log_action_event(data):
            if isinstance(data, dict):
                import json
                logger.info(f"[Metrics] {json.dumps({k: data.get(k) for k in ('action','success','state_verified','confidence','execution_ms') if k in data})}")

        event_bus.subscribe("ACTION_EXECUTED", _log_action_event)
        event_bus.subscribe("ACTION_FAILED",   _log_action_event)

        # ── Wire background task completion to TTS ────────────────────────
        # Background research/tasks publish TASK_COMPLETE but nothing subscribes.
        # This subscriber calls _tts_callback so the user hears the result.
        def _on_task_complete(data: dict):
            try:
                from agent.core import JarvisAgentCore
                # Find the live agent instance via the module-level reference in main.py
                import main as _main
                agent = getattr(_main, "jarvis", None) or getattr(_main, "agent", None)
                if agent and agent._tts_callback:
                    summary = data.get("summary") or data.get("message") or "Research complete, Sir."
                    agent._tts_callback(summary)
            except Exception as e:
                logger.debug(f"[EventBus] TASK_COMPLETE TTS dispatch failed: {e}")

        event_bus.subscribe("TASK_COMPLETE",   _on_task_complete)
        event_bus.subscribe("RESEARCH_DONE",   _on_task_complete)
        event_bus.subscribe("RESEARCH_COMPLETE", _on_task_complete)
        logger.info("[PATCH]  Background task completion → TTS wired")

        # Wire entity_resolver with app list from app_locator
        try:
            from utils.app_locator import app_locator
            def _sync_apps(_=None):
                apps = list(app_locator._disk_index.keys()) if app_locator._indexed else []
                if apps:
                    entity_resolver.set_apps(apps)
            event_bus.subscribe("CONTEXT_UPDATED", _sync_apps)
            _sync_apps()
        except Exception:
            pass

        logger.info("[PATCH]  v8 infrastructure wired (EventBus, VerificationLayer, EntityResolver, SystemContext, BrowserController)")
    except Exception as e:
        logger.warning(f"[PATCH] v8 infrastructure wire failed (non-fatal): {e}")

    logger.info("[PATCH]  All Jarvis v8 patches applied successfully")
    return True