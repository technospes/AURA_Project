"""
GUIDED ADVISOR v2 — Adaptive Conversation Layer
================================================
WHAT'S NEW vs v1:
  1. INTERRUPT HANDLING — user can say "actually under 25k" mid-flow
     and constraints are updated without restarting the session.
  2. EARLY EXIT — "just give me the best one" skips remaining questions
     immediately.
  3. CONFIDENCE-BASED STOPPING — stops asking questions as soon as the
     LLM judges it has enough signal, rather than always asking a fixed
     number of questions.
  4. ADAPTIVE QUESTIONING — LLM decides whether the next question is
     needed at all; rule-based pool is only a fallback.
  5. CONTEXT LIFETIME — sessions expire after 10 min of inactivity.
  6. MID-FLOW CONSTRAINT UPDATE — budget / category changes mid-session
     are detected and applied without discarding gathered answers.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── EARLY-EXIT TRIGGERS ───────────────────────────────────────────────────
_EARLY_EXIT_PHRASES = frozenset([
    "just give", "best one", "just pick", "surprise me", "you decide",
    "doesn't matter", "don't care", "anything is fine", "just recommend",
    "just tell me", "go ahead", "proceed", "no more questions",
    "stop asking", "enough", "just do it",
])

# ── INTERRUPT / CONSTRAINT-CHANGE PATTERNS ────────────────────────────────
_BUDGET_INTERRUPT_RE = re.compile(
    r'(?:actually|wait|no|change it to|make it|now|under|below|within|up to|upto)\s*'
    r'(?:₹|rs\.?|inr)?\s*(\d[\d,]*)\s*(?:k)?',
    re.IGNORECASE
)
_CATEGORY_INTERRUPT_WORDS = {
    "gaming", "camera", "battery", "performance", "business",
    "student", "photography", "video", "editing", "travel",
}


@dataclass
class RecommendationSession:
    session_id: str
    original_query: str
    category: str
    known_constraints: Dict       # from initial query (budget, brand, etc.)
    answers: Dict[str, str]       # question → answer pairs
    questions_asked: List[str]
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    completed: bool = False
    result_text: Optional[str] = None
    result_spoken: Optional[str] = None

    SESSION_TIMEOUT = 600.0  # 10 min inactivity → auto-expire

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > self.SESSION_TIMEOUT

    def touch(self):
        self.last_activity = time.time()


class GuidedAdvisor:
    """
    Multi-turn recommendation engine with adaptive questioning.

    Key design principles (v2):
    - Never ask a question if LLM says confidence is already high enough.
    - Mid-flow interrupts (budget change, category change) are merged
      into the running session — no restart.
    - "Just give me the best one" triggers immediate research.
    - Max 3 questions hard cap; LLM may stop sooner.
    """

    CATEGORY_MAP = {
        "smartphone": ["smartphone", "phone", "mobile", "android", "iphone"],
        "laptop":     ["laptop", "notebook", "macbook", "chromebook"],
        "headphone":  ["headphone", "earphone", "earbud", "earbuds", "headset", "earphones"],
        "tablet":     ["tablet", "ipad"],
        "tv":         ["tv", "television", "smart tv"],
        "camera":     ["camera", "dslr", "mirrorless"],
        "smartwatch": ["watch", "smartwatch", "wearable", "fitbit"],
        "speaker":    ["speaker", "bluetooth speaker", "soundbar"],
        "keyboard":   ["keyboard", "mechanical keyboard"],
        "monitor":    ["monitor", "display", "screen"],
    }

    QUESTION_POOL = {
        "smartphone": [
            "What will you mainly use it for — gaming, photography, or everyday tasks?",
            "Any brand preference, or open to all options?",
            "Battery life or performance — which matters more to you?",
            "Do you prefer a compact phone under 6 inches, or a larger screen?",
            "Stock Android or are you okay with custom UI like MIUI or One UI?",
        ],
        "laptop": [
            "What's your primary use — coding, gaming, design, or everyday work?",
            "Windows or open to macOS too?",
            "Do you carry it daily, or mostly use it at a desk?",
            "Do you need a dedicated GPU for gaming or creative work?",
            "Preferred screen size — 13, 14, 15, or 16 inches?",
        ],
        "headphone": [
            "Over-ear, on-ear, or in-ear earbuds?",
            "Do you need active noise cancellation?",
            "Mainly for music, calls, gaming, or all three?",
            "Wired or wireless preferred?",
        ],
        "tv": [
            "What screen size are you looking for?",
            "Mainly for streaming, cable TV, or gaming?",
            "4K or Full HD is fine?",
        ],
        "_default": [
            "What will you mainly use it for?",
            "Any brand preferences?",
            "Any specific must-have features?",
        ]
    }

    TRIGGERS = [
        "suggest", "recommend", "which is best", "best ", "good ",
        "should i buy", "worth buying", "top 3", "top three",
        "which phone", "which laptop", "which headphone", "which tablet",
        "what phone", "what laptop", "compare", "vs ", "versus",
        "good phones", "good laptops", "worth it",
    ]

    def __init__(self, groq_api_key: str):
        self._api_key = groq_api_key
        self._client = None
        self._sessions: Dict[str, RecommendationSession] = {}
        self._active_id: Optional[str] = None
        # ── ISSUE 3: store task ID for user-initiated cancel ──────────────
        self.current_task_id: Optional[str] = None
        # ── ISSUE 2: deduplication lock — one research job at a time ──────
        self._research_lock = asyncio.Lock()

    def _groq(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    # ── PUBLIC ─────────────────────────────────────────────────────────────

    def is_recommendation_query(self, text: str) -> bool:
        t = text.lower()
        return any(tr in t for tr in self.TRIGGERS)

    def has_active_session(self) -> bool:
        if not self._active_id:
            return False
        s = self._sessions.get(self._active_id)
        if s is None or s.completed or s.is_expired:
            self._active_id = None
            return False
        return True

    async def start_or_continue(
        self,
        user_text: str,
        speak_fn: Callable[[str], None],
        task_manager = None,
        on_complete = None
    ) -> Optional[str]:
        """
        Main entry point.
        Returns full recommendation text when done, None while questioning, 
        or "[BACKGROUND_TASK_STARTED]" if research is handed off.
        """
        if self.has_active_session():
            return await self._handle_answer(user_text, speak_fn, task_manager, on_complete)
        else:
            return await self._start(user_text, speak_fn, task_manager, on_complete)

    def abandon(self):
        if self._active_id:
            s = self._sessions.get(self._active_id)
            if s:
                s.completed = True
        self._active_id = None

    # ── SESSION FLOW ───────────────────────────────────────────────────────

    async def _start(
        self,
        query: str,
        speak_fn: Callable,
        task_manager=None,
        on_complete=None,
    ) -> Optional[str]:
        sid = str(uuid.uuid4())[:8]
        category = self._detect_category(query)
        constraints = self._extract_constraints(query)

        session = RecommendationSession(
            session_id=sid,
            original_query=query,
            category=category,
            known_constraints=constraints,
            answers={},
            questions_asked=[]
        )
        self._sessions[sid] = session
        self._active_id = sid

        logger.info(f" Advisor [{sid}] {category} | known={constraints}")

        # Check early-exit right away
        if self._is_early_exit(query):
            speak_fn("Got it. Let me find the best option for you right away.")
            return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

        q = await self._next_question(session)
        if q:
            speak_fn(q)
            session.questions_asked.append(q)
            return None

        return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

    async def _handle_answer(
        self,
        answer: str,
        speak_fn: Callable,
        task_manager=None,
        on_complete=None,
    ) -> Optional[str]:
        session = self._sessions.get(self._active_id)
        if not session:
            return None

        session.touch()

        # ── 1. EARLY EXIT DETECTION ────────────────────────────────────────
        if self._is_early_exit(answer):
            logger.info("[Advisor] Early exit triggered — skipping remaining questions")
            speak_fn("Understood. Looking up the best option for you now.")
            return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

        # ── 2. INTERRUPT / CONSTRAINT UPDATE DETECTION ────────────────────
        updated = self._detect_and_apply_interrupt(answer, session)
        if updated:
            logger.info(f"[Advisor] Constraint updated mid-flow: {session.known_constraints}")
            speak_fn(f"Got it, I've updated your requirements. {updated}")
            # Don't count this as an answer turn — check if we need more info
            if len(session.questions_asked) >= 1 and await self._enough_info(session):
                speak_fn("I have enough information. Researching now.")
                return await self._dispatch_research(session, speak_fn, task_manager, on_complete)
            q = await self._next_question(session)
            if q:
                speak_fn(q)
                session.questions_asked.append(q)
            else:
                speak_fn("Searching for the best options now.")
                return await self._dispatch_research(session, speak_fn, task_manager, on_complete)
            return None

        # ── 3. JUNK ANSWER GUARD ───────────────────────────────────────────
        answer_stripped = answer.strip(" .?!,;")
        _JUNK = {
            "one", "two", "three", "the", "a", "an", "um", "uh",
            "yes", "no", "ok", "okay", "mm", "hm", "hmm", "ah",
            "who", "what", "how", "why", "where", "when",
        }
        if len(answer_stripped) <= 3 or answer_stripped.lower() in _JUNK:
            last_q = session.questions_asked[-1] if session.questions_asked else None
            logger.info(f"[Advisor] Junk answer '{answer}' discarded")
            if last_q:
                speak_fn(f"Sorry, I didn't catch that. {last_q}")
            else:
                speak_fn("Sorry, I didn't catch that. Could you repeat?")
            return None

        # ── 4. RECORD ANSWER ───────────────────────────────────────────────
        last_q = session.questions_asked[-1] if session.questions_asked else "general"
        session.answers[last_q] = answer_stripped
        logger.info(f"   Answer: '{answer_stripped}'")

        # ── 5. CONFIDENCE-BASED STOPPING ──────────────────────────────────
        # Check if LLM says we have enough — don't blindly ask all 3
        if await self._enough_info(session):
            speak_fn("Got it. Searching for the best options now.")
            return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

        # Hard cap at 3 questions
        if len(session.questions_asked) >= 3:
            speak_fn("Thanks. Looking up the best options now.")
            return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

        q = await self._next_question(session)
        if q:
            speak_fn(q)
            session.questions_asked.append(q)
            return None

        speak_fn("Thanks. Looking up the best options now.")
        return await self._dispatch_research(session, speak_fn, task_manager, on_complete)

    # ── INTERRUPT HANDLING ─────────────────────────────────────────────────

    def _is_early_exit(self, text: str) -> bool:
        t = text.lower().strip()
        return any(phrase in t for phrase in _EARLY_EXIT_PHRASES)

    def _detect_and_apply_interrupt(
        self, text: str, session: RecommendationSession
    ) -> Optional[str]:
        """
        Detect mid-flow constraint changes and apply them.
        Returns a short confirmation string if something was updated, else None.
        """
        t_low = text.lower()
        updated_parts = []

        # Budget interrupt: "actually under 25k" / "make it 20000" / "now 30k"
        m = _BUDGET_INTERRUPT_RE.search(t_low)
        if m:
            raw = m.group(1).replace(",", "")
            num = int(raw)
            if num < 1000:
                num *= 1000
            session.known_constraints["budget"] = f"under ₹{num:,}"
            updated_parts.append(f"budget updated to under ₹{num:,}")

        # Category/use-case interrupt: "actually for gaming" / "I need it for photography"
        for word in _CATEGORY_INTERRUPT_WORDS:
            if word in t_low:
                session.known_constraints["use_case"] = word
                updated_parts.append(f"use case set to {word}")
                break

        return ", ".join(updated_parts) if updated_parts else None

    # ── QUESTION GENERATION ────────────────────────────────────────────────

    async def _next_question(self, session: RecommendationSession) -> Optional[str]:
        """
        LLM-driven question selection.
        Returns None if no more questions are needed (confidence is high).
        """
        pool = self.QUESTION_POOL.get(session.category, self.QUESTION_POOL["_default"])
        known_text = " ".join(str(v) for v in {
            **session.known_constraints,
            **session.answers
        }.values()).lower()

        # Filter already-asked and context-redundant questions
        available = []
        for q in pool:
            if q in session.questions_asked:
                continue
            if any(w in q.lower() for w in ["budget", "price", "cost", "much"]):
                if any(w in known_text for w in ["₹", "rs", "k", "under", "000", "lakh"]):
                    continue
            if "brand" in q.lower() and any(b in known_text for b in [
                "samsung", "apple", "oneplus", "realme", "poco", "xiaomi",
                "redmi", "vivo", "oppo", "motorola", "nokia", "sony", "dell",
                "hp", "lenovo", "asus", "acer", "lg", "bose", "jbl", "boat",
            ]):
                continue
            available.append(q)

        if not available:
            return None

        # Ask LLM: do we actually need another question, or is confidence high enough?
        try:
            known_summary = {**session.known_constraints, **session.answers}
            prompt = (
                f"User wants a {session.category} recommendation.\n"
                f'Original query: "{session.original_query}"\n'
                f"What we already know: {json.dumps(known_summary)}\n"
                f"Questions already asked: {session.questions_asked}\n\n"
                f"Available follow-up questions:\n{json.dumps(available)}\n\n"
                f"TASK: Decide if another question is needed.\n"
                f"If we already have enough info (budget + primary use case known), return: DONE\n"
                f"If more info is needed, return ONLY the single best question from the list.\n"
                f"Return nothing else."
            )
            resp = await self._llm_call(prompt, max_tokens=80, temp=0.2)
            resp = resp.strip().strip('"\'')
            if resp.upper().startswith("DONE"):
                logger.info("[Advisor] LLM says confidence is high — skipping further questions")
                return None
            if len(resp) > 10 and ("?" in resp or len(resp) > 20):
                return resp
        except Exception as e:
            logger.warning(f"Question LLM failed: {e}")

        return available[0]

    async def _enough_info(self, session: RecommendationSession) -> bool:
        """Fast heuristic check — avoids LLM call for obvious cases."""
        known = {**session.known_constraints, **session.answers}
        all_text = " ".join(str(v) for v in known.values()).lower()
        has_budget = any(c in all_text for c in ["₹", "rs", " k", "under", "000", "lakh"])
        has_use = len(session.answers) >= 1
        # If both budget and use-case are known, we have enough
        if has_budget and has_use:
            return True
        # If user gave very detailed initial query (4+ words after budget), trust it
        words_in_query = session.original_query.split()
        if len(words_in_query) >= 6 and has_budget:
            return True
        return False

    # ── RESEARCH + RECOMMENDATIONS ─────────────────────────────────────────

    async def _dispatch_research(self, session, speak_fn, task_manager, on_complete):
        speak_fn("Searching for the best options. Give me a moment.")

        if task_manager:
            # ── ISSUE 2: Deduplication — only one advisor research job at a time ──
            task_name = f"Advisor: {session.category}"
            if task_manager.is_running(task_name):
                logger.info("[Advisor] Research task already running — cancelling previous before starting new.")
                task_manager.cancel(task_name)

            # ── ISSUE 3: Store task ID so user can cancel via intent ──────────────
            task_id = task_manager.submit(
                name=task_name,
                coro=self._bg_research_wrapper(session, speak_fn, on_complete),
                notify=False,  # We handle the spoken notification manually
            )
            self.current_task_id = task_id
            logger.info(f"[Advisor] Background task submitted: id={task_id} name={task_name!r}")
            return "[BACKGROUND_TASK_STARTED]"
        else:
            # Fallback for synchronous / unit-test paths
            return await self._research_and_recommend(session)

    async def _bg_research_wrapper(self, session, speak_fn, on_complete):
        """
        Runs inside the background task.  All three safety rules are enforced here:

        ISSUE 1 — TTS thread safety
            speak_fn is called via loop.call_soon_threadsafe() so it is always
            scheduled onto the main event-loop thread, preventing overlapping
            audio and race conditions.

        ISSUE 2 — Lock prevents two concurrent research coroutines even if the
            deduplication guard in _dispatch_research were somehow bypassed (e.g.
            in tests that call this directly).

        ISSUE 3 — current_task_id is cleared on exit so the core can see the
            advisor is no longer occupying a slot.
        """
        loop = asyncio.get_event_loop()

        def _safe_speak(text: str) -> None:
            """Route TTS through the main-loop thread — never call speak_fn directly
            from a background coroutine running in a thread-pool executor."""
            try:
                loop.call_soon_threadsafe(speak_fn, text)
            except RuntimeError:
                # Loop already closed (shutdown path) — best-effort direct call
                speak_fn(text)

        # ── ISSUE 2: acquire lock so only one research run executes at a time ──
        async with self._research_lock:
            try:
                result = await self._research_and_recommend(session)
                if session.result_spoken:
                    _safe_speak(session.result_spoken)
                if on_complete:
                    await on_complete(result, session.result_spoken)
            except asyncio.CancelledError:
                logger.info("[Advisor] Background research task was cancelled.")
                _safe_speak("Research cancelled, Sir.")
                raise  # re-raise so the task_manager knows it was cancelled
            except Exception as e:
                logger.error(f"Advisor BG research error: {e}", exc_info=True)
                _safe_speak("I ran into an issue finding those recommendations, Sir.")
            finally:
                # ── ISSUE 3: clear task handle when done (success, cancel, or error) ──
                self.current_task_id = None

        return "Recommendation complete"

    def cancel_current_research(self, task_manager) -> bool:
        """
        Public helper — call this when the user says 'Jarvis, cancel'.
        Returns True if a running task was cancelled, False otherwise.
        """
        if self.current_task_id and task_manager:
            logger.info(f"[Advisor] Cancelling research task {self.current_task_id}")
            task_manager.cancel(self.current_task_id)
            self.current_task_id = None
            return True
        return False

    async def _research_and_recommend(self, session: RecommendationSession) -> str:
        known = {**session.known_constraints, **session.answers}
        query = self._build_search_query(session, known)
        logger.info(f" Researching: {query}")

        try:
            research = await asyncio.wait_for(
                self._web_search(query),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("[Advisor] Web search timed out — using knowledge fallback")
            research = "Web search timed out. Answering from training knowledge."

        result = await self._format_recommendations(session, known, research)

        session.completed = True
        session.result_text = result
        self._active_id = None
        return result

    def _build_search_query(self, session: RecommendationSession, known: Dict) -> str:
        parts = [f"best {session.category}"]
        budget = known.get("budget", "")
        if budget:
            parts.append(budget)
        for k, v in known.items():
            if k != "budget" and v and isinstance(v, str) and len(v) < 80:
                parts.append(v)
        parts.append("India latest review price")
        return " ".join(parts)

    async def _web_search(self, query: str) -> str:
        try:
            from ddgs import DDGS
            loop = asyncio.get_event_loop()

            def _sync_search():
                with DDGS(timeout=8) as ddgs:
                    return list(ddgs.text(query, max_results=5))

            try:
                results = await asyncio.wait_for(
                    loop.run_in_executor(None, _sync_search),
                    timeout=10.0
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"DDGS search failed/timed out: {e}")
                results = []

            if not results:
                return "No live results. Answering from training knowledge."

            formatted = []
            total_chars = 0
            for r in results:
                body = r.get("body", "")[:300]
                entry = f"[{r.get('title', '')}]\n{body}"
                formatted.append(entry)
                total_chars += len(entry)
                if total_chars >= 2500:
                    break

            return "\n\n".join(formatted)

        except Exception as e:
            logger.warning(f"DDG search failed: {e}")
            return "Search unavailable. Answering from training knowledge."

    async def _format_recommendations(
        self, session: RecommendationSession, known: Dict, research: str
    ) -> str:
        known_str = "; ".join(f"{k}: {v}" for k, v in known.items() if v)
        research_snippet = research[:2000]

        prompt = f"""You are Jarvis, a precise product advisor for Indian users.

User wants: {session.original_query}
Their preferences: {known_str}

Research data:
{research_snippet}

TASK: Recommend exactly 3 {session.category}s that best match their needs.

OUTPUT TWO SECTIONS — label them exactly as shown:

SPOKEN:
[2-3 natural spoken sentences. Name all 3 products and their approximate prices.
End with your top pick and one reason why. NO markdown, NO URLs, NO asterisks,
NO bullet points. Plain speech only. Max 60 words total.]

FULL:
**Option 1: [Full Product Name]**
Specs: [3-4 key specs]
Why this suits you: [1 sentence tailored to their stated needs]
Price: ₹[amount]
Buy on Flipkart: https://www.flipkart.com/search?q=[product+name]
Buy on Amazon: https://www.amazon.in/s?k=[product+name]

**Option 2: [Full Product Name]**
[same format]

**Option 3: [Full Product Name]**
[same format]

**My Top Pick: [Name]**
[One sentence: why this is the best fit for THIS user's specific needs]

Rules:
- SPOKEN section: plain conversational speech, absolutely no markdown, max 60 words
- FULL section: detailed markdown with real products available in India 2025
- Prices must be realistic for the Indian market
- Tailor all recommendations to what the user actually said they need"""

        try:
            raw = await self._llm_call(
                prompt, max_tokens=800, temp=0.3,
                system="You are Jarvis, a precise product advisor. Always output both SPOKEN: and FULL: sections exactly as instructed."
            )
            raw = raw.strip()

            spoken_match = re.search(
                r'SPOKEN:\s*(.*?)(?=\nFULL:|$)', raw, re.DOTALL | re.IGNORECASE
            )
            full_match = re.search(
                r'FULL:\s*(.*)', raw, re.DOTALL | re.IGNORECASE
            )

            spoken_text = spoken_match.group(1).strip() if spoken_match else ""
            full_text   = full_match.group(1).strip()   if full_match   else raw

            if spoken_text:
                session.result_spoken = GuidedAdvisor._clean_for_tts(spoken_text)

            return full_text if full_text else raw

        except Exception as e:
            logger.error(f"Recommendation LLM failed: {e}")
            return "Research complete, but I had trouble formatting the results. Try asking again."

    async def _llm_call(self, prompt: str, max_tokens=200, temp=0.3, system="") -> str:
        loop = asyncio.get_event_loop()
        client = self._groq()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        def _call():
            return client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                temperature=temp,
                max_tokens=max_tokens
            )
        resp = await loop.run_in_executor(None, _call)
        return resp.choices[0].message.content

    # ── HELPERS ────────────────────────────────────────────────────────────

    def _detect_category(self, text: str) -> str:
        t = text.lower()
        for cat, kws in self.CATEGORY_MAP.items():
            if any(kw in t for kw in kws):
                return cat
        return "product"

    def _extract_constraints(self, text: str) -> Dict:
        constraints = {}
        t = text.lower()

        budget_re = [
            r'(?:under|below|within|upto|up to|less than)\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*)\s*(?:k)?',
            r'(?:₹|rs\.?|inr)\s*(\d[\d,]*)\s*(?:k)?',
            r'\b(\d+)\s*k\b',
            r'\b(\d[\d,]+)\s*(?:rupees?|inr)\b',
        ]
        for pat in budget_re:
            m = re.search(pat, t)
            if m:
                raw = m.group(1).replace(",", "")
                num = int(raw)
                if num < 1000:
                    num *= 1000
                constraints["budget"] = f"under ₹{num:,}"
                break

        brands = [
            "samsung", "apple", "oneplus", "realme", "poco", "motorola",
            "nokia", "xiaomi", "mi", "redmi", "vivo", "oppo", "asus",
            "dell", "hp", "lenovo", "acer", "msi", "sony", "bose", "jbl",
            "boat", "sennheiser", "lg", "google", "nothing"
        ]
        found = [b for b in brands if b in t]
        if found:
            constraints["brand_preference"] = ", ".join(found)

        return constraints

    @staticmethod
    def spoken_summary(full_text: str) -> str:
        option_names = re.findall(
            r'\*{0,2}Option\s*\d+\s*:\s*([^*\n]+)\*{0,2}',
            full_text, re.IGNORECASE
        )
        top_pick = ""
        tp_match = re.search(
            r'\*{0,2}My Top Pick\s*:\s*([^*\n]+)\*{0,2}',
            full_text, re.IGNORECASE
        )
        if tp_match:
            top_pick = tp_match.group(1).strip().strip("*").strip()

        prices = re.findall(r'Price\s*:\s*(₹[\d,]+(?:\s*-\s*₹[\d,]+)?)', full_text)

        if option_names:
            parts = []
            for i, name in enumerate(option_names[:3]):
                name = name.strip().strip("*").strip()
                price = prices[i] if i < len(prices) else ""
                entry = f"Option {i+1}: {name}"
                if price:
                    entry += f" at {price}"
                parts.append(entry)

            summary = ". ".join(parts)
            if top_pick:
                summary += f". My top pick is {top_pick}."
            else:
                summary += "."

            return GuidedAdvisor._clean_for_tts(summary)

        return GuidedAdvisor._clean_for_tts(full_text)

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        text = text.replace("₹", " rupees ")
        text = re.sub(r'(\d),(\d)', r'\1\2', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\*{1,3}([^*]*)\*{1,3}', r'\1', text)
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-•*]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'Buy on \w+\s*:.*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'`[^`]*`', '', text)
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        text = text.strip()

        words = text.split()
        if len(words) > 60:
            text = " ".join(words[:60]).rstrip(",:;") + "."

        return text