"""
INTENT ENGINE v5 — Extended with Email, Calls, Messages, Platform Detection
============================================================================
NEW vs v4:
  1. compose_email intent — triggers AgenticOrchestrator email workflow
  2. make_call now properly captures platform from the phrase
     "call xyz on WhatsApp/Discord"
  3. send_message captures platform, contact, and body in one shot
  4. Improved conversational catch-all — "what is special about you" routes
     correctly to the conversation engine
  5. answer_question catch-all for open questions when page context is active
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
    "autonomous_task":       "Multi-step autonomous task requiring planning and execution",
    "open_website":          "Open a URL in the browser",
    "smart_open":            "Find and open a website by name (unknown URL)",
    "close_tab":             "Close the current or a named browser tab",
    "new_tab":               "Open a new browser tab",
    "search_web":            "Search the internet",
    "open_url":              "Open a specific URL or website domain directly in browser",
    "click_result":          "Click or open a search result by index (first, second, third link)",
    "browser_navigation":    "Navigate within the browser using context (this tab, go back, etc.)",
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
    "compose_email":         "Write and send an email via Gmail",
    "make_call":             "Make a voice/video call on WhatsApp or Discord",
    "send_message":          "Send a chat message on WhatsApp or Discord",
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
    "answer_question":       "Answer a question about page content or context",
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
        """
        self._fast_patterns = [

            # ── SOCIAL ────────────────────────────────────────────────────
            (re.compile(r'^(thanks|thank you|cheers|appreciate it)', re.I),
             "thank", lambda m: {}),

            (re.compile(r'^(hi|hello|hey|good morning|good afternoon|good evening)(\s|$)', re.I),
             "greet", lambda m: {}),

            (re.compile(r'^(stop|cancel|abort|never mind|forget it)', re.I),
             "cancel", lambda m: {}),

            # ── EMAIL (before generic open/write) ─────────────────────────
            (re.compile(
                r'^(?:write|compose|draft|send)\s+(?:an?\s+)?(?:email|mail|e-mail)'
                r'(?:\s+to\s+(?P<to>[\w.+@\-]+))?',
                re.I),
             "compose_email",
             lambda m: {"to_address": m.group("to").strip() if m.group("to") else ""}),

            # ── RECOMMENDATION (before research catch-all) ─────────────────
            (re.compile(
                r'^(?:'
                r'suggest\s+|recommend\s+|'
                r'which\s+(?:is\s+)?(?:the\s+)?best|'
                r'best\s+\w+\s+(?:under|below|for|to)\s+|'
                r'good\s+\w+\s+(?:under|below|for)\s+|'
                r'should\s+i\s+buy|'
                r'worth\s+buying|'
                r'top\s+\d+\s+|'
                r'(?:what|which)\s+(?:phone|laptop|headphone|tablet|tv|camera|watch|speaker|'
                r'keyboard|monitor|earbuds?|earphones?)'
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

            # ── PAGE CONTEXT ──────────────────────────────────────────────
            (re.compile(r'^read\s+(?:this\s+)?(?:aloud|out\s+loud|to\s+me)', re.I),
             "read_page", lambda m: {}),

            (re.compile(r'^read\s+(?:this\s+)?(?:page|article|content|site)', re.I),
             "read_page", lambda m: {}),

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

            # ── BROWSER TABS ──────────────────────────────────────────────
            (re.compile(r'^close\s+(?:the\s+)?(\w+)\s+tab$', re.I),
             "close_tab",
             lambda m: {"tab_name": m.group(1).strip().lower()}),

            (re.compile(r'^close\s+(?:this|the|current|that)\s+tab$', re.I),
             "close_tab", lambda m: {}),

            (re.compile(r'^close\s+(?:this|the|current)\s+(?:window|browser)$', re.I),
             "close_tab", lambda m: {}),

            (re.compile(r'^close\s+tab$', re.I),
             "close_tab", lambda m: {}),

            (re.compile(r'^(?:open\s+)?new\s+tab$', re.I),
             "new_tab", lambda m: {}),

            # ── URL / WEBSITE DIRECT OPEN (must be BEFORE open_app) ───────
            # Catches: "open youtube.com", "open github.com/user", "open https://..."
            (re.compile(
                r'^(?:open|go\s+to|visit|navigate\s+to|launch)\s+'
                r'(?P<url>(?:https?://)?'
                r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
                r'(?:[a-zA-Z]{2,6})'
                r'(?:/\S*)?)',
                re.I),
             "open_url",
             lambda m: {"url": m.group("url").strip().rstrip(".")}),

            # ── CLICK RESULT BY INDEX (must be BEFORE open_app) ───────────
            # Catches: "open the first link", "click second result", "open link 1"
            (re.compile(
                r'^(?:open|click|go\s+to|select|visit)\s+'
                r'(?:the\s+)?'
                r'(?P<index>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|[1-5])\s+'
                r'(?:link|result|option|one)?',
                re.I),
             "click_result",
             lambda m: {"index": m.group("index").strip().lower()}),

            # ── BROWSER NAVIGATION (must be BEFORE open_app) ──────────────
            # Catches: "use this tab", "switch to this tab", "use this window"
            (re.compile(
                r'^(?:use|switch\s+to|focus\s+on|go\s+to)\s+'
                r'(?:this|the\s+(?:current|active))\s+'
                r'(?:tab|window|page|browser)',
                re.I),
             "browser_navigation",
             lambda m: {"action": "focus_current"}),

            # ── APP CONTROL ───────────────────────────────────────────────
            (re.compile(r'^(open|launch|start)\s+(.+)', re.I),
             "open_app",
             lambda m: {"app": m.group(2).strip().rstrip('.')}),

            (re.compile(
                r'^close\s+'
                r'(?!(?:this|the|current|that)\s+(?:tab|window|browser)$)'
                r'(?!\w+\s+tab$)'
                r'(.+)',
                re.I),
             "close_app",
             lambda m: {"app": m.group(1).strip().rstrip('.')}),

            (re.compile(r'^search\s+(?:for\s+)?(?!.*\b(?:and|then|also)\b)(.+?)(?:\s+on\s+(\w+))?$', re.I),
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

            # ── CALLS WITH PLATFORM ───────────────────────────────────────
            (re.compile(
                r'^(?:please\s+)?(?:make\s+(?:a\s+)?)?(?:call|ring|video\s+call)\s+'
                r'(?P<contact>(?:(?!\s+on\b).)+?)(?:\s+on\s+(?P<platform>\w+))?$',
                re.I),
             "make_call",
             lambda m: {
                 "contact":  (m.group("contact") or "").strip(),
                 "platform": (m.group("platform") or "").strip().lower()
             }),

            # ── MESSAGES WITH PLATFORM ────────────────────────────────────
            (re.compile(
                r'^(?:send|message|text|whatsapp|dm)\s+'
                r'(?P<contact>.+?)(?:\s+(?:on|via)\s+(?P<platform>\w+))?'
                r'(?:\s+saying\s+(?P<body>.+))?$',
                re.I),
             "send_message",
             lambda m: {
                 "contact":  (m.group("contact") or "").strip(),
                 "platform": (m.group("platform") or "").strip().lower(),
                 "body":     (m.group("body") or "").strip()
             }),

            # ── QUICK SYSTEM ANSWERS ──────────────────────────────────────
            (re.compile(r'^(?:what.s\s+the\s+time|what\s+time\s+is\s+it)', re.I),
             "quick_answer", lambda m: {"query": "current time"}),

            (re.compile(r'^(?:what.s\s+today.s?\s+date|what\s+date\s+is\s+it)', re.I),
             "quick_answer", lambda m: {"query": "current date"}),

            # Page-context answer (when page has been read)
            (re.compile(
                r'^(?:what|who|where|when|why|how|explain|summarize).*?'
                r'\b(?:this|that|the)\b.*(?:page|site|article|hackathon|document)',
                re.I),
             "answer_question", lambda m: {}),

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

            (re.compile(
                r'^(?:what\s+do\s+you\s+know\s+about\s+me|recall|what\s+did\s+i\s+tell)',
                re.I),
             "recall_fact", lambda m: {}),

            # ── REMINDERS ─────────────────────────────────────────────────
            (re.compile(
                r'^(?:remind\s+me\s+(?:to\s+)?(.+?)\s+in\s+(\d+)\s*(minute|hour|second)s?)',
                re.I),
             "set_reminder",
             lambda m: {
                 "reminder_text": m.group(1).strip(),
                 "time": f"{m.group(2)} {m.group(3)}s"
             }),

            # ── CONVERSATION (LAST RESORT) ─────────────────────────────────
            (re.compile(
                r'^(?:tell\s+me\s+(?:a\s+)?(?:joke|story|fact)|'
                r'how\s+are\s+you|'
                r'what\s+is\s+special\s+about\s+you|'
                r'are\s+you\s+(?:okay|fine|there|awake)|'
                r'what\s+(?:can|could)\s+you\s+do|'
                r'do\s+you\s+(?:know|remember)|'
                r'can\s+you\s+(?:help|explain|tell)|'
                r'why\s+are\s+you|'
                r'who\s+are\s+you|'
                r'what\s+are\s+you)',
                re.I),
             "conversation",
             lambda m: {"query": m.group(0).strip()}),
        ]

    async def understand(self, text: str, context: Dict = None,
                        memory_hints: Dict = None, audio_features: Dict = None) -> Dict:
        if not text.strip():
            return self._intent("ignore", {}, 1.0, text)
        
        text_lower = text.lower().strip()
        
        # ── STT ALIAS FIXES (these stay as quick patches) ──────────────────
        if "close" in text_lower and "tab" in text_lower:
            return self._intent("close_tab", {}, 0.95, text)
        
        # Remove the "time" alias that was causing the bug
        # if "close" in text_lower and "time" in text_lower:  ← DELETE THIS LINE
        
        if re.search(
            r"\b(read\s+aloud|read\s+this\s+page|read\s+this\s+article|"
            r"read\s+what\s+(?:is|was|'s)?\s*written|read\s+(?:the\s+)?(?:page|content)\s+aloud)\b",
            text_lower
        ):
            return self._intent("read_page", {}, 0.95, text)
        
        # ── TRY DYNAMIC REGISTRY FIRST ─────────────────────────────────────
        from core.intent_registry import intent_registry
        result = intent_registry.match(text, context or {})
        if result:
            logger.debug(f"Dynamic match: {result['intent']} | {result['entities']}")
            return result
        
        # ── FALLBACK TO HARDCODED PATTERNS (gradually migrate these) ───────
        for pattern, intent_name, extractor in self._fast_patterns:
            m = pattern.search(text)
            if m:
                entities = extractor(m)
                entities = self._resolve_context(intent_name, entities, context or {})
                logger.debug(f"Pattern match: {intent_name} | {entities}")
                return self._intent(intent_name, entities, 0.95, text)
        
        # ── LLM FALLBACK ───────────────────────────────────────────────────
        return await self._llm_understand(text, context or {}, memory_hints or {})

    def _resolve_context(self, intent_name: str, entities: Dict, context: Dict) -> Dict:
        if intent_name == "close_app" and entities.get("app") in ("this", "it", None):
            active = context.get("active_app", "")
            if active and active != "desktop":
                entities["app"] = active
        return entities

    async def _llm_understand(self, text: str, context: Dict, memory: Dict) -> Dict:
        try:
            client = self._get_client()

            ctx_str = (
                f"Active app: {context.get('active_app', 'desktop')}, "
                f"Last app: {context.get('last_app', 'none')}, "
                f"Last song: {context.get('last_song', 'none')}"
            )

            has_page = context.get("has_page_context", False)
            page_ctx_note = ""
            if has_page:
                page_title = context.get("page_title", "")
                page_ctx_note = (
                    f"\nIMPORTANT: A page was recently read aloud"
                    f"{(' (' + page_title + ')') if page_title else ''}. "
                    f"Any question about that page's content MUST be classified as: "
                    f'intent=answer_question, entities={{"query": "<the question>"}}'
                )

            mem_facts = [f"{h}" for h in memory.get("facts", [])[:3]]
            mem_str = "; ".join(mem_facts) if mem_facts else "none"

            intent_list = "\n".join(f"- {k}: {v}" for k, v in INTENT_CATALOGUE.items())

            prompt = f"""Jarvis AI assistant. Convert this voice command to an intent.

Command: "{text}"
Context: {ctx_str}
User memory: {mem_str}
{page_ctx_note}

Available intents:
{intent_list}

Special rules:
- "close this/the/current tab" → close_tab, entities: {{}}
- "close [name] tab" → close_tab, entities: {{"tab_name": "[name]"}}
- "open youtube.com" or "open site.com" → open_url, entities: {{"url": "youtube.com"}}
- "open the first link" / "click second result" → click_result, entities: {{"index": "first"}}
- "use this tab" / "switch to this window" → browser_navigation, entities: {{"action": "focus_current"}}
- "open [site without extension]" → smart_open, entities: {{"query": "[site name]"}}
- "what is this site about" → page_summary, entities: {{}}
- "read this aloud" → read_page, entities: {{}}
- "search for X and summarize" or any command with "and" joining two actions → autonomous_task
- "find X and do Y" → autonomous_task
- "suggest/recommend [product]" → guided_recommendation, entities: {{"query": "[full query]"}}
- "write/compose/send an email" → compose_email, entities: {{"to_address": ""}}
- "call [name] on [platform]" → make_call, entities: {{"contact": "[name]", "platform": "[platform]"}}
- "send/message [name] on [platform]" → send_message, entities: {{"contact": "[name]", "platform": "[platform]", "body": ""}}
- "what is special about you" or any meta question → conversation
- Any follow-up question when page context is active → answer_question

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

from core.intent_registry import intent_registry, IntentDefinition

def _register_all_intents():
    """Register all intents with the dynamic registry"""
    
    # ── SOCIAL (priority 100 - checked first) ──────────────────────────
    intent_registry.register_simple("thank", 
        [r'^(thanks|thank you|cheers|appreciate it)'], priority=100)
    
    intent_registry.register_simple("greet",
        [r'^(hi|hello|hey|good morning|good afternoon|good evening)(\s|$)'], priority=100)
    
    intent_registry.register_simple("cancel",
        [r'^(stop|cancel|abort|never mind|forget it)'], priority=100)
    
    # ── TIME/DATE (priority 98 - before shutdown confusion) ────────────
    intent_registry.register_simple("quick_answer",
        [r'^(?:what.s\s+the\s+time|what\s+time\s+is\s+it)'],
        priority=98,
        extractor=lambda m: {"query": "current time"})
    
    intent_registry.register_simple("quick_answer",
        [r'^(?:what.s\s+today.s?\s+date|what\s+date\s+is\s+it)'],
        priority=98,
        extractor=lambda m: {"query": "current date"})
    
    # ── SHUTDOWN (priority 95 - AFTER time queries) ────────────────────
    intent_registry.register_simple("shutdown",
        [r'^(?:shut\s*down|turn\s+off\s+(?:the\s+)?(?:pc|computer))'],
        priority=95)
    
    intent_registry.register_simple("restart",
        [r'^restart(?:\s+(?:the\s+)?(?:pc|computer))?'], priority=95)
    
    # ── PAGE CONTEXT (priority 95) ─────────────────────────────────────
    intent_registry.register_simple("read_page",
        [r'^read\s+(?:this\s+)?(?:aloud|out\s+loud|to\s+me)',
         r'^read\s+(?:this\s+)?(?:page|article|content|site)'],
        priority=95)
    
    intent_registry.register_simple("page_summary",
        [r'^(?:what\s+(?:is|are)\s+(?:this|the)\s+(?:site|page|article|content)\s+(?:about|saying|for)|'
         r'summarize\s+(?:this\s+)?(?:page|site|article|content)|'
         r'what.s\s+(?:on\s+)?this\s+(?:page|site))'],
        priority=95)
    
    # ── CLICK / UI INTERACTION (priority 82) ──────────────────────────
    intent_registry.register_simple("autonomous_task",
        [r'^(?:click|press|select|choose|hit)\s+(?:on\s+)?',
        r'^(?:type|write|enter)\s+.+\s+(?:and|then)\s+(?:press|hit)\s+(?:enter|return)',
        r'^(?:scroll\s+(?:down|up)\s+(?:to|until)\s+)',
        r'^(?:find\s+and\s+click\s+)'],
        priority=82)
    
    # ── MEDIA (priority 90) ────────────────────────────────────────────
    intent_registry.register_simple("pause_media",
        [r'^(pause|stop playing)'], priority=90)
    
    intent_registry.register_simple("resume_media",
        [r'^(resume|continue playing|unpause)'], priority=90)
    
    intent_registry.register_simple("next_track",
        [r'^(next|next track|skip)'], priority=90)
    
    intent_registry.register_simple("previous_track",
        [r'^(previous|back|go back|prev)'], priority=90)
    
    intent_registry.register_simple("play_media",
        [r'^play\s+(.+?)\s+on\s+(youtube|spotify|soundcloud)'],
        priority=90,
        extractor=lambda m: {"song": m.group(1).strip(), "platform": m.group(2).lower()})
    
    intent_registry.register_simple("play_media",
        [r'^play\s+(.+)'],
        priority=89,
        extractor=lambda m: {"song": m.group(1).strip()})
    
    # ── BROWSER (priority 88) ──────────────────────────────────────────
    intent_registry.register_simple("close_tab",
        [r'^close\s+(?:the\s+)?(\w+)\s+tab$',
         r'^close\s+(?:this|the|current|that)\s+tab$',
         r'^close\s+tab$'],
        priority=88)
    
    intent_registry.register_simple("new_tab",
        [r'^(?:open\s+)?new\s+tab$'], priority=88)
    
    intent_registry.register_simple("open_url",
        [r'^(?:open|go\s+to|visit|navigate\s+to|launch)\s+(?P<url>(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+(?:[a-zA-Z]{2,6})(?:/\S*)?)'],
        priority=87,
        extractor=lambda m: {"url": m.group("url").strip().rstrip(".")})
    
    # ── APP CONTROL (priority 85) ──────────────────────────────────────
    intent_registry.register_simple("open_app",
        [r'^(open|launch|start)\s+(.+)'],
        priority=85,
        extractor=lambda m: {"app": m.group(2).strip().rstrip('.')})
    
    intent_registry.register_simple("close_app",
        [r'^close\s+(?!(?:this|the|current|that)\s+(?:tab|window|browser)$)(?!\w+\s+tab$)(.+)'],
        priority=85,
        extractor=lambda m: {"app": m.group(1).strip().rstrip('.')})
    
    intent_registry.register_simple("search_web",
    [r'^search\s+(?:for\s+)?(?!.*\b(?:and|then|also)\b)(.+?)(?:\s+on\s+(\w+))?$'],
    priority=84,
    extractor=lambda m: {"query": m.group(1).strip(), "platform": m.group(2).lower() if m.group(2) else "google"})
    
    # ── SYSTEM (priority 83) ───────────────────────────────────────────
    intent_registry.register_simple("take_screenshot",
        [r'^take\s+(?:a\s+)?screenshot'], priority=83)
    
    intent_registry.register_simple("lock",
        [r'^(?:lock|lock\s+(?:the\s+)?(?:screen|computer|pc))'], priority=83)
    
    intent_registry.register_simple("scroll",
        [r'^scroll\s+(up|down)'], priority=83,
        extractor=lambda m: {"direction": m.group(1).lower()})
    
    # ── TYPING (priority 82) ───────────────────────────────────────────
    intent_registry.register_simple("type_text",
        [r'^type\s+(?:in\s+)?(?:out\s+)?(.+)'], priority=82,
        extractor=lambda m: {"text": m.group(1).strip()})
    
    # ── CALLS (priority 81) ────────────────────────────────────────────
    intent_registry.register_simple("make_call",
        [r'^(?:please\s+)?(?:make\s+(?:a\s+)?)?(?:call|ring|video\s+call)\s+(?P<contact>(?:(?!\s+on\b).)+?)(?:\s+on\s+(?P<platform>\w+))?$'],
        priority=81,
        extractor=lambda m: {"contact": (m.group("contact") or "").strip(), "platform": (m.group("platform") or "").strip().lower()})
    
    # ── MESSAGES (priority 80) ─────────────────────────────────────────
    intent_registry.register_simple("send_message",
        [r'^(?:send|message|text|whatsapp|dm)\s+(?P<contact>.+?)(?:\s+(?:on|via)\s+(?P<platform>\w+))?(?:\s+saying\s+(?P<body>.+))?$'],
        priority=80,
        extractor=lambda m: {"contact": (m.group("contact") or "").strip(), "platform": (m.group("platform") or "").strip().lower(), "body": (m.group("body") or "").strip()})
    
    # ── AUTONOMOUS TASK (priority 75 - raised from 70) ─────────────────
    intent_registry.register(
        IntentDefinition(
            name="autonomous_task",
            description="Multi-step autonomous goal pursuit",
            patterns=[
                # Commands with "and" joining two actions
                r'(?:search|find|look\s+up|google).+\band\b.+(?:summarize|tell|show|read|explain|list|get|open)',
                r'(?:open|go\s+to|launch).+\band\b.+(?:search|find|play|read|summarize|get)',
                r'(?:find|get|download).+\band\b.+(?:send|email|message|save|open)',
                
                # Original patterns
                r'^(?:jarvis\s+)?(?:please\s+)?(?:can\s+you\s+)?(?:find\s+and\s+|go\s+and\s+|automatically\s+)',
                r'^(?:keep\s+trying\s+until|monitor\s+|watch\s+for\s+|track\s+)',
                r'^(?:notify\s+me\s+when|alert\s+me\s+if|let\s+me\s+know\s+when)',
                r'^(?:apply\s+to|submit\s+to|fill\s+out\s+and\s+submit)',
            ],
            priority=75  # Raised from 70
        )
    )

    # ── CONVERSATION (priority 10 - last resort) ───────────────────────
    intent_registry.register_simple("conversation",
        [r'^(?:tell\s+me\s+(?:a\s+)?(?:joke|story|fact)|how\s+are\s+you|what\s+is\s+special\s+about\s+you|are\s+you\s+(?:okay|fine|there|awake)|what\s+(?:can|could)\s+you\s+do)'],
        priority=10)

# Register at module load
_register_all_intents()