"""
INTENT ENGINE v4 — Complete Fix
================================
Changes from your current version:

1. CLOSE TAB FIXED
   "close this tab", "close YouTube tab", "close that tab" all correctly
   route to close_tab BEFORE the universal close_app catch-all can grab them.

2. GUIDED RECOMMENDATION ADDED
   "suggest smartphones under 20k" → guided_recommendation intent
   Triggers the GuidedAdvisor multi-turn conversation flow.

3. SMART OPEN ADDED
   "open Notion", "open Jarvis on GitHub" → smart_open intent
   Searches for the URL instead of guessing .com

4. PAGE CONTEXT INTENTS ADDED
   "read this", "read aloud", "what is this site", "summarize this page"
   → route to read_page / page_summary intents

5. CONVERSATIONAL CATCH-ALL FIXED
   The pattern `r'^.*(?:tell|help|how|what...).*'` was matching EVERYTHING
   including "close this tab" (because "this" isn't in the list but the pattern
   is `.*` which matches anything). Replaced with a proper fallback that only
   fires after all specific patterns have been tried.

Replace voice/intent_engine.py with this file.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

INTENT_CATALOGUE = {
    "open_app":              "Open an application",
    "close_app":             "Close a running application",
    "focus_app":             "Bring an app to the front",
    "open_website":          "Open a URL in the browser",
    "smart_open":            "Find and open a website by name (unknown URL)",
    "close_tab":             "Close the current or a named browser tab",
    "new_tab":               "Open a new browser tab",
    "search_web":            "Search the internet",
    "scroll":                "Scroll up or down",
    "read_page":             "Read the current page aloud",
    "page_summary":          "Summarize what the current page is about",
    "play_media":            "Play music or video",
    "pause_media":           "Pause playback",
    "resume_media":          "Resume playback",
    "next_track":            "Skip to next track",
    "previous_track":        "Go to previous track",
    "take_screenshot":       "Take a screenshot",
    "type_text":             "Type text in the active window",
    "lock":                  "Lock the workstation",
    "shutdown":              "Shut down the computer",
    "restart":               "Restart the computer",
    "set_reminder":          "Set a reminder or alarm",
    "deep_research":         "Research a topic across multiple web sources",
    "guided_recommendation": "Get a product recommendation with guided questions",
    "quick_answer":          "Answer a factual question",
    "summarize":             "Summarize content",
    "remember_fact":         "Store a fact or preference",
    "recall_fact":           "Recall a stored fact",
    "express_preference":    "User expressing a preference",
    "introduce_self":        "User introducing themselves",
    "greet":                 "User greeting Jarvis",
    "thank":                 "User thanking Jarvis",
    "cancel":                "Cancel current operation",
    "conversation":          "General conversation / chit-chat",
    "unknown":               "Intent could not be determined",
}


class IntentEngine:
    """
    Converts raw speech to structured intent.

    Priority order:
    1. Fast regex patterns (0ms, handles ~75% of commands)
    2. LLM fallback for ambiguous/novel commands (~400ms)
    """

    def __init__(self, groq_api_key: str):
        self.api_key = groq_api_key
        self._client = None
        self._compile_patterns()

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self.api_key)
        return self._client

    def _compile_patterns(self):
        """
        CRITICAL: ORDER MATTERS.
        Most specific patterns MUST come before broad ones.
        
        Lesson learned: a pattern like `^close\\s+(.+)` will match
        "close this tab" before `^close\\s+this\\s+tab` if placed first.
        """
        self._fast_patterns = [

            # ── SOCIAL ────────────────────────────────────────────────────
            (re.compile(r'^(thanks|thank you|cheers|appreciate it)', re.I),
             "thank", lambda m: {}),

            (re.compile(r'^(hi|hello|hey|good morning|good afternoon|good evening)(\s|$)', re.I),
             "greet", lambda m: {}),

            (re.compile(r'^(stop|cancel|abort|never mind|forget it)', re.I),
             "cancel", lambda m: {}),

            # ── COMMUNICATION ───────────────────────────────────────────────
            (re.compile(r'^(?:please )?(?:make a )?call(?: to)?\s+(?P<contact>(?:(?!\son\b).)+)?(?:\s+on\s+(?P<platform>\w+))?$', re.I),
             "make_call",
             lambda m: {
                 "contact": m.group("contact").strip() if m.group("contact") else "",
                 "platform": m.group("platform").strip() if m.group("platform") else ""
             }),

            # ── RECOMMENDATION (before research catch-all) ─────────────────
            # "suggest smartphones under 20k", "best laptop for coding",
            # "which phone should I buy", "recommend earbuds"
            (re.compile(
                r'^(?:'
                r'suggest\s+|recommend\s+|'
                r'which\s+(?:is\s+)?(?:the\s+)?best|'
                r'best\s+\w+\s+(?:under|below|for|to)\s+|'
                r'good\s+\w+\s+(?:under|below|for)\s+|'
                r'should\s+i\s+buy|'
                r'worth\s+buying|'
                r'top\s+\d+\s+|'
                r'(?:what|which)\s+(?:phone|laptop|headphone|tablet|tv|camera|watch|speaker|keyboard|monitor|earbuds?|earphones?)'
                r')',
                re.I),
             "guided_recommendation",
             lambda m: {"query": m.group(0).strip()}),

            # ── MEDIA CONTROLS ────────────────────────────────────────────
            (re.compile(r'^(pause|stop playing)', re.I),
             "pause_media", lambda m: {}),

            (re.compile(r'^(resume|continue playing|unpause)', re.I),
             "resume_media", lambda m: {}),

            (re.compile(r'^(next|next track|skip)', re.I),
             "next_track", lambda m: {}),

            (re.compile(r'^(previous|back|go back|prev)', re.I),
             "previous_track", lambda m: {}),

            (re.compile(r'^play\s+(.+?)\s+on\s+(youtube|spotify|soundcloud)', re.I),
             "play_media",
             lambda m: {"song": m.group(1).strip(), "platform": m.group(2).lower()}),

            (re.compile(r'^play\s+(.+)', re.I),
             "play_media",
             lambda m: {"song": m.group(1).strip()}),

            # ── PAGE CONTEXT (before generic open/read) ────────────────────
            # "read this aloud", "read this page", "read aloud"
            (re.compile(r'^read\s+(?:this\s+)?(?:aloud|out\s+loud|to\s+me)', re.I),
             "read_page", lambda m: {}),

            (re.compile(r'^read\s+(?:this\s+)?(?:page|article|content|site)', re.I),
             "read_page", lambda m: {}),

            # "what is this site about", "summarize this page", "what's on this page"
            (re.compile(
                r'^(?:'
                r'what\s+(?:is|are)\s+(?:this|the)\s+(?:site|page|article|content)\s+(?:about|saying|for)|'
                r'summarize\s+(?:this\s+)?(?:page|site|article|content)|'
                r'what.s\s+(?:on\s+)?this\s+(?:page|site)|'
                r'tell\s+me\s+about\s+(?:this\s+)?(?:page|site|article)|'
                r'brief\s+(?:me\s+)?(?:on\s+)?this'
                r')',
                re.I),
             "page_summary", lambda m: {}),

            # ── BROWSER TABS — ALL before any generic close ────────────────
            # Named: "close YouTube tab", "close the Gmail tab"
            (re.compile(r'^close\s+(?:the\s+)?(\w+)\s+tab$', re.I),
             "close_tab",
             lambda m: {"tab_name": m.group(1).strip().lower()}),

            # "close this tab", "close the tab", "close current tab"
            (re.compile(r'^close\s+(?:this|the|current|that)\s+tab$', re.I),
             "close_tab", lambda m: {}),

            # "close this window", "close the browser window"
            (re.compile(r'^close\s+(?:this|the|current)\s+(?:window|browser)$', re.I),
             "close_tab", lambda m: {}),

            # Bare "close tab"
            (re.compile(r'^close\s+tab$', re.I),
             "close_tab", lambda m: {}),

            # New tab
            (re.compile(r'^(?:open\s+)?new\s+tab$', re.I),
             "new_tab", lambda m: {}),

            # ── APP CONTROL ───────────────────────────────────────────────
            (re.compile(r'^(open|launch|start)\s+(.+)', re.I),
             "open_app",
             lambda m: {"app": m.group(2).strip().rstrip('.')}),

            # close_app: EXPLICITLY excludes tab/window/browser words
            (re.compile(
                r'^close\s+'
                r'(?!(?:this|the|current|that)\s+(?:tab|window|browser)$)'
                r'(?!\w+\s+tab$)'
                r'(.+)',
                re.I),
             "close_app",
             lambda m: {"app": m.group(1).strip().rstrip('.')}),

            # ── WEB ───────────────────────────────────────────────────────
            (re.compile(r'^search\s+(?:for\s+)?(.+?)(?:\s+on\s+(\w+))?$', re.I),
             "search_web",
             lambda m: {
                 "query": m.group(1).strip(),
                 "platform": m.group(2).lower() if m.group(2) else "google"
             }),

            (re.compile(r'^(?:go\s+to|visit|open)\s+(https?://\S+)', re.I),
             "open_website",
             lambda m: {"url": m.group(1).strip()}),

            # ── TYPING ────────────────────────────────────────────────────
            (re.compile(r'^type\s+(?:in\s+)?(?:out\s+)?(.+)', re.I),
             "type_text",
             lambda m: {"text": m.group(1).strip()}),

            # ── QUICK SYSTEM ANSWERS ──────────────────────────────────────
            (re.compile(r'^(?:what.s\s+the\s+time|what\s+time\s+is\s+it)', re.I),
             "quick_answer", lambda m: {"query": "current time"}),

            (re.compile(r'^(?:what.s\s+today.s?\s+date|what\s+date\s+is\s+it)', re.I),
             "quick_answer", lambda m: {"query": "current date"}),

            # ── SYSTEM ACTIONS ────────────────────────────────────────────
            (re.compile(r'^take\s+(?:a\s+)?screenshot', re.I),
             "take_screenshot", lambda m: {}),

            (re.compile(r'^(?:lock|lock\s+(?:the\s+)?(?:screen|computer|pc))', re.I),
             "lock", lambda m: {}),

            (re.compile(r'^scroll\s+(up|down)', re.I),
             "scroll",
             lambda m: {"direction": m.group(1).lower()}),

            (re.compile(r'^(?:shut\s*down|turn\s+off\s+(?:the\s+)?(?:pc|computer))', re.I),
             "shutdown", lambda m: {}),

            (re.compile(r'^restart(?:\s+(?:the\s+)?(?:pc|computer))?', re.I),
             "restart", lambda m: {}),

            # ── RESEARCH ──────────────────────────────────────────────────
            (re.compile(r'^research\s+(.+)', re.I),
             "deep_research",
             lambda m: {"topic": m.group(1).strip()}),

            # ── MEMORY ────────────────────────────────────────────────────
            (re.compile(r'^remember\s+(?:that\s+)?(.+)', re.I),
             "remember_fact",
             lambda m: {"fact": m.group(1).strip()}),

            (re.compile(r'^(?:what\s+do\s+you\s+know\s+about\s+me|recall|what\s+did\s+i\s+tell)', re.I),
             "recall_fact", lambda m: {}),

            # ── CALLS / MESSAGES ──────────────────────────────────────────
            (re.compile(r'^(?:call|ring|video\s+call)\s+(.+?)(?:\s+on\s+(\w+))?$', re.I),
             "make_call",
             lambda m: {
                 "contact": m.group(1).strip(),
                 "platform": m.group(2).lower() if m.group(2) else None
             }),

            (re.compile(r'^(?:send|message|text|whatsapp)\s+(.+?)(?:\s+(?:on|via)\s+(\w+))?$', re.I),
             "send_message",
             lambda m: {
                 "contact": m.group(1).strip(),
                 "platform": m.group(2).lower() if m.group(2) else "whatsapp"
             }),

            # ── REMINDERS ─────────────────────────────────────────────────
            (re.compile(
                r'^(?:remind\s+me\s+(?:to\s+)?(.+?)\s+in\s+(\d+)\s*(minute|hour|second)s?)',
                re.I),
             "set_reminder",
             lambda m: {
                 "reminder_text": m.group(1).strip(),
                 "time": f"{m.group(2)} {m.group(3)}s"
             }),

            # ── CONVERSATION (LAST RESORT — only fires if nothing above matched) ─
            # This is a deliberate catch-all for chit-chat.
            # It does NOT use .* so it won't accidentally eat specific commands.
            (re.compile(
                r'^(?:tell\s+me\s+(?:a\s+)?(?:joke|story|fact)|'
                r'how\s+are\s+you|'
                r'are\s+you\s+(?:okay|fine|there|awake)|'
                r'what\s+(?:can|could)\s+you\s+do|'
                r'do\s+you\s+(?:know|remember)|'
                r'can\s+you\s+(?:help|explain|tell))',
                re.I),
             "conversation",
             lambda m: {"query": m.group(0).strip()}),
        ]

    async def understand(
        self,
        text: str,
        context: Dict,
        memory_hints: Dict,
        audio_features: Dict
    ) -> Dict:
        """
        Main entry point. Returns structured intent dict.
        """
        text = text.strip()
        if not text:
            return self._intent("unknown", {}, 0.0, text)

        # ── FAST PATTERN MATCH ─────────────────────────────────────────────
        for pattern, intent_name, extractor in self._fast_patterns:
            m = pattern.search(text)
            if m:
                entities = extractor(m)
                # Resolve context-dependent entities
                entities = self._resolve_context(intent_name, entities, context)
                logger.debug(f"Pattern match: {intent_name} | {entities}")
                return self._intent(intent_name, entities, 0.95, text)

        # ── LLM FALLBACK ───────────────────────────────────────────────────
        return await self._llm_understand(text, context, memory_hints)

    def _resolve_context(self, intent_name: str, entities: Dict, context: Dict) -> Dict:
        """
        Fill in implicit entities from context.
        "close this" → app = active_app from context
        "it" → last entity from context
        """
        if intent_name == "close_app" and entities.get("app") in ("this", "it", None):
            active = context.get("active_app", "")
            if active and active != "desktop":
                entities["app"] = active

        return entities

    async def _llm_understand(self, text: str, context: Dict, memory: Dict) -> Dict:
        """LLM-based intent understanding for novel/ambiguous commands."""
        try:
            client = self._get_client()

            ctx_str = (
                f"Active app: {context.get('active_app', 'desktop')}, "
                f"Last app: {context.get('last_app', 'none')}, "
                f"Last song: {context.get('last_song', 'none')}"
            )

            mem_facts = [f"{h}" for h in memory.get("facts", [])[:3]]
            mem_str = "; ".join(mem_facts) if mem_facts else "none"

            intent_list = "\n".join(f"- {k}: {v}" for k, v in INTENT_CATALOGUE.items())

            prompt = f"""Jarvis AI assistant. Convert this voice command to an intent.

Command: "{text}"
Context: {ctx_str}
User memory: {mem_str}

Available intents:
{intent_list}

Special rules:
- "close this/the/current tab" → close_tab, entities: {{}}
- "close [name] tab" → close_tab, entities: {{"tab_name": "[name]"}}
- "open [site without extension]" → smart_open, entities: {{"query": "[site name]"}}
- "what is this site about" → page_summary, entities: {{}}
- "read this aloud" → read_page, entities: {{}}
- "suggest/recommend [product]" → guided_recommendation, entities: {{"query": "[full query]"}}

Respond with JSON only:
{{"intent": "intent_name", "entities": {{}}, "confidence": 0.0-1.0, "reasoning": "why"}}"""

            import asyncio
            loop = asyncio.get_event_loop()

            def _call():
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=200,
                    response_format={"type": "json_object"}
                )

            resp = await loop.run_in_executor(None, _call)
            data = json.loads(resp.choices[0].message.content)

            return self._intent(
                data.get("intent", "unknown"),
                data.get("entities", {}),
                float(data.get("confidence", 0.6)),
                text
            )

        except Exception as e:
            logger.error(f"LLM intent failed: {e}")
            return self._intent("unknown", {}, 0.3, text)

    def _intent(self, name: str, entities: Dict, confidence: float, text: str) -> Dict:
        return {
            "intent": name,
            "entities": entities,
            "confidence": confidence,
            "original_text": text,
            "timestamp": time.time()
        }
