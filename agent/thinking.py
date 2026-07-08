import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from agent.goal_manager import goal_manager, GoalStep
logger = logging.getLogger(__name__)


@dataclass
class Subtask:
    """One atomic step in achieving a goal."""
    action: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[int] = field(default_factory=list)
    is_optional: bool = False


@dataclass
class ThinkResult:
    """Output of the thinking process."""
    goal: str                           # Clear statement of what user wants
    subtasks: List[Subtask]             # Ordered steps to achieve it
    success_criteria: str               # How to know it worked
    confidence: float                   # 0-1: how sure we are about the plan
    reasoning: str                      # Why we chose this approach
    memory_used: List[str]              # Which memory facts shaped this
    requires_clarification: bool = False
    clarification_question: Optional[str] = None

    def is_simple(self) -> bool:
        """Single-step goals don't need the thinking layer overhead."""
        return len(self.subtasks) <= 1

    def to_plan_hints(self) -> Dict:
        """Convert to hints for the planner."""
        return {
            "goal": self.goal,
            "subtask_count": len(self.subtasks),
            "success_criteria": self.success_criteria,
            "memory_used": self.memory_used,
        }


# ── SIMPLE INTENT PATTERNS (bypass LLM for speed) ─────────────────────────
# These intents are inherently single-step — no decomposition needed.
_SIMPLE_INTENTS = frozenset([
    "open_app", "close_app", "play_media", "pause_media", "resume_media",
    "next_track", "previous_track", "search_web", "type_text", "close_tab",
    "new_tab", "scroll", "take_screenshot", "lock", "shutdown", "restart",
    "greet", "thank", "cancel", "remember_fact", "recall_fact",
    "express_preference", "introduce_self",
])

# ── COMPLEX INTENT PATTERNS (need decomposition) ──────────────────────────
_COMPLEX_INTENTS = {
    "deep_research": "multi-step web research and synthesis",
    "make_call":     "open app + navigate + initiate call",
    "send_message":  "open app + find contact + compose + send",
    "open_notepad_write": "open app + type content + save",
    "read_page":     "extract page content + read aloud",
    "set_reminder":  "background timer + notification",
}

# ── GOAL TEMPLATES (for clarity injection) ────────────────────────────────
_GOAL_TEMPLATES = {
    "open_app":          "Have {app} open and ready to use",
    "play_media":        "Be playing {song} on {platform}",
    "deep_research":     "Have a comprehensive summary of {topic}",
    "make_call":         "Be in a call with {contact} on {platform}",
    "send_message":      "Have sent '{message}' to {contact}",
    "search_web":        "Show search results for '{query}'",
    "type_text":         "Have '{text}' typed in the active window",
    "set_reminder":      "Reminder '{reminder_text}' set for {time}",
}


class ThinkingEngine:
    """
    Pre-planning reasoning that converts intent + memory → concrete goal + subtasks.

    For simple intents: returns instantly with a 1-step plan hint.
    For complex intents: uses memory + LLM to decompose into ordered subtasks.
    For ambiguous intents: detects gaps and asks clarifying questions.
    """

    def __init__(self, groq_api_key: str, config: Dict = None):
        self._api_key = groq_api_key
        self._config = config or {}
        self._client = None

        # Cache: (intent_name, entity_hash) → ThinkResult (for repeated commands)
        self._think_cache: Dict[str, ThinkResult] = {}
        self._cache_ttl = 300  # 5 minutes

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    async def think(
        self,
        intent: Dict,
        memory_context: Dict,
        context: Dict,
    ) -> ThinkResult:
        """
        Main entry point. Returns a ThinkResult for the intent.

        For simple intents: O(1), no API call.
        For complex intents: ~500ms LLM call.
        For ambiguous intents: clarification returned.
        """
        intent_name = intent.get("intent", "unknown")
        entities    = intent.get("entities", {})
        confidence  = intent.get("confidence", 0.5)

        # ── SIMPLE PATH (no LLM needed) ────────────────────────────────────
        if intent_name in _SIMPLE_INTENTS:
            return self._simple_think(intent_name, entities, memory_context)

        # ── MEMORY-AUGMENTED THINKING ─────────────────────────────────────
        # Fill in missing entities from memory BEFORE deciding complexity
        entities = self._fill_from_memory(intent_name, entities, memory_context)
        intent["entities"] = entities

        # ── CHECK CACHE ────────────────────────────────────────────────────
        cache_key = self._cache_key(intent_name, entities)
        if cache_key in self._think_cache:
            cached = self._think_cache[cache_key]
            logger.debug(f"Think cache hit: {intent_name}")
            return cached

        # ── COMPLEX DECOMPOSITION ─────────────────────────────────────────
        if intent_name in _COMPLEX_INTENTS:
            result = await self._decompose(intent, memory_context, context)
        else:
            # Unknown/general intent — use LLM to reason
            result = await self._llm_think(intent, memory_context, context)

        # Cache the result
        self._think_cache[cache_key] = result
        
        # [NEW: Phase 1 Architecture Fix] — Persist multi-step goals
        self._maybe_create_goal(result, intent, entities)
        
        return result

    def _simple_think(self, intent_name: str, entities: Dict, memory: Dict) -> ThinkResult:
        """Instant ThinkResult for simple single-step intents."""
        # Build goal from template
        template = _GOAL_TEMPLATES.get(intent_name, f"Complete: {intent_name}")
        try:
            goal = template.format(**{k: v or "" for k, v in entities.items()})
        except KeyError:
            goal = f"Execute: {intent_name}"

        return ThinkResult(
            goal=goal,
            subtasks=[Subtask(
                action=intent_name,
                description=goal,
                params=entities
            )],
            success_criteria=f"{intent_name} executed successfully",
            confidence=0.95,
            reasoning="Simple single-step intent — no decomposition needed",
            memory_used=[]
        )

    def _fill_from_memory(self, intent_name: str, entities: Dict, memory: Dict) -> Dict:
        """
        Use memory to fill in missing entities.
        This is what enables "play my favorite song", "call him again", etc.
        """
        prefs = {p["key"]: p["value"] for p in memory.get("preferences", [])}
        personal = {p["key"]: p["value"] for p in memory.get("personal", [])}
        facts = {f["key"]: f["value"] for f in memory.get("facts", [])}

        filled = dict(entities)
        memory_used = []

        # Platform preference
        if intent_name in ("play_media", "search_web", "make_call", "send_message"):
            if not filled.get("platform"):
                preferred = (
                    prefs.get("preferred_music_platform") or
                    prefs.get("preferred_platform")
                )
                if preferred:
                    filled["platform"] = preferred
                    memory_used.append(f"preferred_platform={preferred}")

        # Song/content preference
        if intent_name == "play_media" and not filled.get("song"):
            fav = prefs.get("favorite_song") or prefs.get("favorite_artist")
            if fav:
                filled["song"] = fav
                memory_used.append(f"favorite={fav}")

        # Contact preference for messages/calls
        if intent_name in ("make_call", "send_message") and not filled.get("contact"):
            last_contact = facts.get("last_contact")
            if last_contact:
                filled["contact"] = last_contact
                memory_used.append(f"last_contact={last_contact}")

        return filled

    async def _decompose(self, intent: Dict, memory: Dict, context: Dict) -> ThinkResult:
        """Decompose a known complex intent into subtasks."""
        intent_name = intent.get("intent", "")
        entities    = intent.get("entities", {})

        # Predefined decompositions for known complex intents
        decompositions = {
            "deep_research": self._decompose_research,
            "make_call":     self._decompose_call,
            "send_message":  self._decompose_message,
            "open_notepad_write": self._decompose_notepad,
            "read_page":     self._decompose_read_page,
        }

        decomposer = decompositions.get(intent_name)
        if decomposer:
            return decomposer(entities, memory, context)

        # Fallback to LLM
        return await self._llm_think(intent, memory, context)

    def _decompose_research(self, entities, memory, context) -> ThinkResult:
        topic = (
            entities.get("query") or
            entities.get("topic") or
            entities.get("subject") or
            "the topic"
        )
        fmt    = entities.get("output_format", "spoken")
        prefs  = {p["key"]: p["value"] for p in memory.get("preferences", [])}
        depth  = int(prefs.get("preferred_research_depth", 4))

        return ThinkResult(
            goal=f"Comprehensive research on: {topic}",
            subtasks=[
                Subtask("search_web", f"Search for '{topic}'",
                        {"query": topic, "num_results": depth}),
                Subtask("fetch_and_parse", "Fetch and read top results",
                        {"max_pages": depth}, depends_on=[0]),
                Subtask("synthesize_research", "Synthesize findings",
                        {"topic": topic, "output_format": fmt}, depends_on=[0, 1]),
            ],
            success_criteria=f"A clear, accurate synthesis of {topic} is ready",
            confidence=0.92,
            reasoning=f"Deep research requires: search → fetch → synthesize",
            memory_used=[f"depth={depth}"] if "preferred_research_depth" in prefs else []
        )

    def _decompose_call(self, entities, memory, context) -> ThinkResult:
        contact  = entities.get("contact", "")
        platform = entities.get("platform", "discord")
        return ThinkResult(
            goal=f"Be in a call with {contact} on {platform}",
            subtasks=[
                Subtask("open_app", f"Open {platform}", {"name": platform}),
                Subtask("initiate_call", f"Call {contact}",
                        {"contact": contact, "platform": platform}, depends_on=[0]),
            ],
            success_criteria=f"Call with {contact} is active",
            confidence=0.88,
            reasoning="Calls require: open app → find contact → initiate",
            memory_used=[]
        )

    def _decompose_message(self, entities, memory, context) -> ThinkResult:
        contact  = entities.get("contact", "")
        platform = entities.get("platform", "whatsapp")
        content  = entities.get("message_content", "")
        return ThinkResult(
            goal=f"Send '{content[:30]}' to {contact}",
            subtasks=[
                Subtask("open_app", f"Open {platform}", {"name": platform}),
                Subtask("navigate_to_contact", f"Find {contact}",
                        {"contact": contact, "platform": platform}, depends_on=[0]),
                Subtask("type_and_send", "Send message",
                        {"text": content}, depends_on=[0, 1]),
            ],
            success_criteria=f"Message sent to {contact}",
            confidence=0.88,
            reasoning="Messages require: open → find contact → send",
            memory_used=[]
        )

    def _decompose_notepad(self, entities, memory, context) -> ThinkResult:
        content  = entities.get("text", entities.get("content", ""))
        filename = entities.get("filename", "note.txt")
        return ThinkResult(
            goal=f"Write and save content to {filename}",
            subtasks=[
                Subtask("open_app", "Open Notepad", {"name": "notepad"}),
                Subtask("type_text", "Type content", {"text": content}, depends_on=[0]),
                Subtask("save_file", f"Save as {filename}", {"filename": filename}, depends_on=[0, 1]),
            ],
            success_criteria=f"Content saved to {filename}",
            confidence=0.92,
            reasoning="File writing requires: open → type → save",
            memory_used=[]
        )

    def _decompose_read_page(self, entities, memory, context) -> ThinkResult:
        return ThinkResult(
            goal="Read the current page's content aloud",
            subtasks=[
                Subtask("extract_page_text", "Extract page text", {}),
                Subtask("tts_speak", "Read aloud",
                        {"source": "step_0_result"}, depends_on=[0]),
            ],
            success_criteria="Page content has been read aloud",
            confidence=0.85,
            reasoning="Reading requires: extract text → speak",
            memory_used=[]
        )

    async def _llm_think(self, intent: Dict, memory: Dict, context: Dict) -> ThinkResult:
        """
        Use LLM to reason about an unknown or general intent.
        Returns decomposed subtasks or a clarification request.
        """
        intent_name = intent.get("intent", "")
        entities    = intent.get("entities", {})
        original    = intent.get("original_text", "")

        # Build context string for LLM
        mem_facts = []
        for p in memory.get("preferences", [])[:3]:
            mem_facts.append(f"User prefers {p['key']}: {p['value']}")
        for p in memory.get("personal", [])[:2]:
            mem_facts.append(f"{p['key']}: {p['value']}")
        mem_str = "; ".join(mem_facts) if mem_facts else "No prior context"

        ctx_str = f"Active app: {context.get('active_app', 'desktop')}"
        if context.get("last_app"):
            ctx_str += f", Last used: {context['last_app']}"

        prompt = f"""You are Jarvis, an AI agent. Analyze this user request and create an execution plan.

User said: "{original}"
Intent: {intent_name}
Entities: {json.dumps(entities)}
User memory: {mem_str}
Context: {ctx_str}

AVAILABLE ACTIONS:
- open_app(name): open desktop app
- close_app(name): close app
- play_media(song, platform): play music/video
- search_web(query, platform): search the web
- open_website(url): open URL
- type_text(text): type in active window
- close_tab(): close browser tab
- take_screenshot(): screenshot
- answer_question(query): answer with AI
- search_and_navigate(query): web research
- unified_comm:
    * send_whatsapp_message (REQUIRED: "contact", "message")
    * call_whatsapp (REQUIRED: "contact")
    * call_discord (REQUIRED: "contact")
    * open_and_search (REQUIRED: "query")

Respond ONLY with JSON:
{{
  "goal": "Clear statement of what user wants",
  "subtasks": [
    {{"action": "action_name", "description": "what this does", "params": {{}}, "depends_on": []}}
  ],
  "success_criteria": "How to know it worked",
  "confidence": 0.0-1.0,
  "reasoning": "Why this approach",
  "requires_clarification": false,
  "clarification_question": null
}}

Keep it simple. 1-3 subtasks max unless truly necessary."""

        try:
            loop = asyncio.get_event_loop()
            client = self._get_client()

            def _call():
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=500,
                    response_format={"type": "json_object"}
                )

            resp = await loop.run_in_executor(None, _call)
            data = json.loads(resp.choices[0].message.content)

            subtasks = [
                Subtask(
                    action=s.get("action", intent_name),
                    description=s.get("description", ""),
                    params=s.get("params", {}),
                    depends_on=s.get("depends_on", [])
                )
                for s in data.get("subtasks", [])
            ]

            if not subtasks:
                subtasks = [Subtask(action=intent_name, description=data.get("goal", ""), params=entities)]

            return ThinkResult(
                goal=data.get("goal", f"Execute: {original}"),
                subtasks=subtasks,
                success_criteria=data.get("success_criteria", "Action completed"),
                confidence=float(data.get("confidence", 0.7)),
                reasoning=data.get("reasoning", ""),
                memory_used=mem_facts[:2],
                requires_clarification=data.get("requires_clarification", False),
                clarification_question=data.get("clarification_question")
            )

        except Exception as e:
            logger.error(f"LLM think failed: {e}")
            # Fallback: treat as simple single-step
            return self._simple_think(intent_name, entities, memory)

    def _cache_key(self, intent_name: str, entities: Dict) -> str:
        """Simple cache key from intent + sorted entity values."""
        entity_str = "_".join(sorted(f"{k}={v}" for k, v in entities.items() if v))
        return f"{intent_name}:{entity_str}"
    
        # [NEW: Phase 1 Architecture Fix] — Goal persistence
    def _maybe_create_goal(
        self, 
        result: ThinkResult, 
        intent: Dict, 
        entities: Dict
    ) -> Optional[str]:
        """
        Persist a multi-step goal to the GoalManager so it survives across turns.
        Only creates goals for multi-step, non-clarification ThinkResults.
        
        Returns goal_id if created, None otherwise.
        """
        try:
            # Only persist multi-step goals that don't need clarification
            if result.requires_clarification:
                return None
            if len(result.subtasks) <= 1:
                return None
            
            # Convert Subtasks to GoalSteps
            steps = []
            for s in result.subtasks:
                step = GoalStep(
                    action=s.action,
                    description=s.description,
                    params=s.params
                )
                # Mark dependencies in params for later resolution
                if s.depends_on:
                    step.params["_depends_on"] = s.depends_on
                steps.append(step)
            
            # Create the goal
            goal_id = goal_manager.create(
                name=result.goal,
                steps=steps,
                metadata={
                    "intent": intent.get("intent", "unknown"),
                    "entities": entities,
                    "success_criteria": result.success_criteria,
                    "created_from": "thinking_engine"
                }
            )
            
            logger.info(f"[ThinkingEngine]  Persisted goal '{result.goal}' as {goal_id} ({len(steps)} steps)")
            return goal_id
            
        except Exception as e:
            logger.warning(f"[ThinkingEngine] Failed to persist goal: {e}")
            return None
