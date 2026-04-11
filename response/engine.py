"""
RESPONSE ENGINE v2 — Emotion-Aware, Truthful, Context-Adaptive Tone
===================================================================
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EmotionDetector:
    FRUSTRATED_PATTERNS = [
        re.compile(r'\b(again|still|why|ugh|not working|broken|come on|seriously)\b', re.I),
        re.compile(r'[!]{2,}'),
    ]
    IMPATIENT_PATTERNS = [
        re.compile(r'\b(quick|fast|hurry|now|asap|immediately|just|already)\b', re.I),
        re.compile(r'^\w{1,8}[.!]?$'),
    ]
    CURIOUS_PATTERNS = [
        re.compile(r'\b(why|how|what|explain|tell me|describe|curious|wonder)\b', re.I),
        re.compile(r'\?'),
    ]
    HAPPY_PATTERNS = [
        re.compile(r'\b(great|awesome|amazing|perfect|love|excellent|fantastic|nice|cool)\b', re.I),
    ]

    def detect(self, text: str, audio_features: Optional[Dict] = None) -> str:
        scores = {"neutral": 0, "frustrated": 0, "impatient": 0, "curious": 0, "happy": 0}
        for p in self.FRUSTRATED_PATTERNS:
            if p.search(text): scores["frustrated"] += 1
        for p in self.IMPATIENT_PATTERNS:
            if p.search(text): scores["impatient"] += 1
        for p in self.CURIOUS_PATTERNS:
            if p.search(text): scores["curious"] += 1
        for p in self.HAPPY_PATTERNS:
            if p.search(text): scores["happy"] += 1

        if audio_features:
            pitch = audio_features.get("pitch", 1.0)
            speed = audio_features.get("speed", 1.0)
            volume = audio_features.get("volume", 1.0)
            if pitch > 1.3 and volume > 1.2: scores["frustrated"] += 2
            if speed > 1.4: scores["impatient"] += 2
            if pitch > 1.2 and speed > 1.1: scores["happy"] += 1

        top = max(scores, key=lambda k: scores[k])
        return top if scores[top] > 0 else "neutral"


TONE_CONFIGS = {
    "neutral":    {"max_sentences": 2, "max_chars": 150, "prefix": "", "suffix": "Sir.", "style": "professional"},
    "frustrated": {"max_sentences": 2, "max_chars": 180, "prefix": "I understand. ", "suffix": "Sir.", "style": "patient"},
    "impatient":  {"max_sentences": 1, "max_chars": 80,  "prefix": "", "suffix": "", "style": "ultra-brief"},
    "curious":    {"max_sentences": 3, "max_chars": 250, "prefix": "", "suffix": "Sir.", "style": "informative"},
    "happy":      {"max_sentences": 2, "max_chars": 150, "prefix": "", "suffix": "Sir.", "style": "warm"},
}

TASK_TONE_OVERRIDES = {
    "deep_research":     {"max_sentences": 4, "max_chars": 400},
    "quick_answer":      {"max_sentences": 2, "max_chars": 200},
    "play_media":        {"max_sentences": 1, "max_chars": 60},
    "open_app":          {"max_sentences": 1, "max_chars": 40},
    "close_app":         {"max_sentences": 1, "max_chars": 40},
    "recall_fact":       {"max_sentences": 3, "max_chars": 200},
    "read_page":         {"max_sentences": 1, "max_chars": 50},
    "send_message":      {"max_sentences": 1, "max_chars": 60},
    "make_call":         {"max_sentences": 1, "max_chars": 50},
    "page_summary":      {"max_sentences": 3, "max_chars": 300},
    "smart_open":        {"max_sentences": 1, "max_chars": 80},
}

SUCCESS_TEMPLATES = {
    "open_app": "Opening {app}", "close_app": "Closing {app}",
    "focus_app": "Bringing {app} to the front",
    "play_media": "Playing {song} on {platform}",
    "pause_media": "Paused", "resume_media": "Resuming playback",
    "next_track": "Next track", "previous_track": "Going back",
    "search_web": "Searching for {query} on {platform}",
    "type_text": "Done", "close_tab": "Tab closed", "new_tab": "New tab opened",
    "scroll": "Scrolled {direction}", "take_screenshot": "Screenshot saved",
    "lock": "Locking the workstation", "shutdown": "Shutting down in 30 seconds",
    "restart": "Restarting in 30 seconds", "remember_fact": "Remembered",
    "express_preference": "Noted, I'll keep that in mind",
    "greet": "All systems operational", "thank": "Always a pleasure",
    "cancel": "Cancelled", "send_message": "Message sent to {contact}",
    "make_call": "Calling {contact} on {platform}",
    "open_notepad_write": "Written and saved", "read_page": "Reading the page now",
    "open_website": "Opening {url}", "recall_fact": "Here's what I know",
    "introduce_self": "Pleased to meet you, {name}",
    "smart_open": "Opening {title}",
    "page_summary": "Here's a summary of the page",
}

FAILURE_TEMPLATES = {
    "open_app": "Unable to open {app}. It may not be installed",
    "close_app": "Couldn't close {app}. It may not be running",
    "play_media": "Playback failed for {song}",
    "search_web": "Search failed. Please check your connection",
    "make_call": "Unable to initiate the call",
    "send_message": "Message could not be sent",
    "deep_research": "Research encountered an issue. I have partial findings",
    "type_text": "Typing failed. Please click in a text field first",
    "page_summary": "Couldn't summarize the page.",
    "read_page": "Couldn't read the page.",
    "smart_open": "Couldn't find or open that.",
    "default": "That didn't work as expected. Let me try a different approach",
}

PERSONALITY_RESPONSES = {
    "greet": ["All systems operational. How may I assist?", "At your service. What do you need?"],
    "thank": ["Always a pleasure.", "Happy to help.", "Of course. That's what I'm here for."],
    "cancel": ["Understood. Cancelling.", "Aborting.", "Stopping."],
}


class ResponseEngine:
    def __init__(self, config: Dict):
        self.config = config
        self.emotion_detector = EmotionDetector()

    async def generate(self, turn, context: Dict, memory_context: Dict) -> Dict:
        intent      = turn.intent or {}
        intent_name = intent.get("intent", "unknown")
        entities    = intent.get("entities", {})
        results     = turn.execution_results or []

        # Detect emotion and build tone config
        emotion = self.emotion_detector.detect(
            text=turn.raw_input,
            audio_features=getattr(turn, "audio_features", None)
        )
        if emotion != "neutral":
            logger.info(f"😊 Emotion: {emotion}")

        tone = dict(TONE_CONFIGS.get(emotion, TONE_CONFIGS["neutral"]))
        tone.update(TASK_TONE_OVERRIDES.get(intent_name, {}))

        # Pre-built spoken response (from reflection/escalation)
        if turn.spoken_response and not turn.response:
            spoken = self._format_response(turn.spoken_response, tone)
            return {"full_response": turn.spoken_response, "spoken_response": spoken}

        # Personality responses
        if intent_name in PERSONALITY_RESPONSES:
            opts = PERSONALITY_RESPONSES[intent_name]
            msg  = opts[hash(turn.turn_id) % len(opts)]
            msg  = self._apply_suffix(msg, tone)
            return {"full_response": msg, "spoken_response": msg}

        # ── NEW INTENT RESPONSES ─────────────────────────────────────────────
        
        # Page summary — speak the AI-generated brief
        if intent_name == "page_summary":
            for r in results:
                out = r.get("output", {})
                if isinstance(out, dict) and "spoken_summary" in out:
                    spoken = out["spoken_summary"]
                    full   = out.get("full_summary", spoken)
                    return {"full_response": full, "spoken_response": spoken}
            return {"full_response": "Couldn't read the page.", "spoken_response": "Couldn't read the page."}

        # NOTE: read_page and smart_open were deleted here because the 
        # TRUTHFUL RESPONSE LOGIC below handles them automatically!

        # ── STANDARD ACTION RESPONSES ────────────────────────────────────────

        # Research
        if intent_name == "deep_research":
            return self._research_response(turn, entities, tone)

        # Recall
        if intent_name == "recall_fact":
            return self._recall_response(results, tone)

        # Quick answer
        if intent_name == "quick_answer":
            out = self._find_output(results, "answer_question")
            if out:
                q      = entities.get("query", "").lower()
                answer = out.get("answer", "")
                if "time" in q: answer = f"It's {time.strftime('%I:%M %p')}."
                elif "date" in q: answer = f"Today is {time.strftime('%A, %B %d, %Y')}."
                return {"full_response": answer, "spoken_response": self._format_response(answer, tone)}

        # ── 🚨 THE TRUTHFUL RESPONSE LOGIC 🚨 ──
        # Check if the execution tool provided a specific truthful message
        if results:
            last_result = results[-1]
            out = last_result.get("output", {})
            
            if isinstance(out, dict) and "message" in out:
                # The tool told us exactly what happened! Use it directly.
                exact_message = out["message"]
                return {"full_response": exact_message, "spoken_response": exact_message}
                
            if not last_result.get("success"):
                # The tool failed and gave us a reason. Speak the reason.
                err = last_result.get("error", "Unknown error")
                msg = f"I ran into an issue, Sir. {err}"
                return {"full_response": err, "spoken_response": msg}

        # ── FALLBACK ──
        # Standard action (only used if tool didn't provide a custom message)
        if turn.success:
            msg = self._format_success(intent_name, entities, results, tone)
        else:
            msg = self._format_failure(intent_name, entities, results, tone, emotion)

        return {"full_response": msg, "spoken_response": self._format_response(msg, tone)}

        # ── 🚨 THE TRUTHFUL RESPONSE LOGIC 🚨 ──
        # Check if the execution tool provided a specific truthful message
        if results:
            last_result = results[-1]
            out = last_result.get("output", {})
            
            if isinstance(out, dict) and "message" in out:
                # The tool told us exactly what happened! Use it directly.
                exact_message = out["message"]
                return {"full_response": exact_message, "spoken_response": exact_message}
                
            if not last_result.get("success"):
                # The tool failed and gave us a reason. Speak the reason.
                err = last_result.get("error", "Unknown error")
                msg = f"I ran into an issue, Sir. {err}"
                return {"full_response": err, "spoken_response": msg}

        # ── FALLBACK ──
        # Standard action (only used if tool didn't provide a custom message)
        if turn.success:
            msg = self._format_success(intent_name, entities, results, tone)
        else:
            msg = self._format_failure(intent_name, entities, results, tone, emotion)

        return {"full_response": msg, "spoken_response": self._format_response(msg, tone)}

    def _format_success(self, name, entities, results, tone):
        template = SUCCESS_TEMPLATES.get(name, "Done")
        fill     = {k: v for k, v in entities.items() if v}
        for r in results:
            out = r.get("output", {})
            if isinstance(out, dict):
                if "opened" in out: fill.setdefault("url", out["opened"])
                if "playing" in out: fill.setdefault("song", out["playing"])
                if "title" in out: fill.setdefault("title", out["title"])
        try:
            msg = template.format(**fill)
        except KeyError:
            msg = template.split("{")[0].rstrip(", ")
        return self._apply_suffix(msg, tone)

    def _format_failure(self, name, entities, results, tone, emotion):
        template = FAILURE_TEMPLATES.get(name, FAILURE_TEMPLATES["default"])
        fill     = {k: v for k, v in entities.items() if v}
        errors   = [r.get("error","") for r in results if r.get("error")]
        err      = errors[0] if errors else ""
        try:
            msg = template.format(**fill)
        except KeyError:
            msg = FAILURE_TEMPLATES["default"]
        if err and len(err) < 60:
            msg += f" ({err})"
        if emotion == "frustrated":
            msg = f"I'm sorry about this. {msg}."
        elif emotion == "impatient":
            msg = f"Failed. {msg}."
        else:
            msg = self._apply_suffix(msg, tone)
        return msg

    def _format_response(self, text, tone):
        if not text: return ""
        max_s = tone.get("max_sentences", 2)
        max_c = tone.get("max_chars", 150)
        sents = re.split(r'(?<=[.!?])\s+', text.strip())
        short = " ".join(sents[:max_s])
        if len(short) > max_c:
            short = short[:max_c].rsplit(" ", 1)[0] + "..."
        return short.strip()

    def _apply_suffix(self, msg, tone):
        suffix = tone.get("suffix", "")
        if suffix and not any(msg.endswith(s) for s in ["Sir.", "Sir!", "Sir?"]):
            return f"{msg.rstrip('.')}, {suffix}"
        return msg

    def _research_response(self, turn, entities, tone):
        results = turn.execution_results or []
        topic   = entities.get("topic", "the topic")
        synthesis = None
        for r in results:
            out = r.get("output", {})
            if isinstance(out, dict) and "synthesis" in out:
                synthesis = out["synthesis"]
                break
        if synthesis:
            spoken = f"Here's what I found on {topic}, Sir. " + self._format_response(
                synthesis, {**tone, "max_sentences": 3, "max_chars": 250}
            )
            return {"full_response": synthesis, "spoken_response": spoken}
        msg = f"Research on '{topic}' complete, Sir."
        return {"full_response": msg, "spoken_response": msg}

    def _recall_response(self, results, tone):
        out = self._find_output(results, "recall_memory")
        if not out:
            msg = "I don't have anything stored on that, Sir."
            return {"full_response": msg, "spoken_response": msg}
        recalled = out.get("recalled", {})
        items = recalled.get("personal",[]) + recalled.get("preferences",[]) + recalled.get("facts",[])
        if items:
            s   = "; ".join(f"{i['key']}: {i['value']}" for i in items[:4])
            msg = f"Here's what I know, Sir: {s}"
        else:
            msg = "I don't have anything stored on that, Sir."
            return {"full_response": msg, "spoken_response": self._format_response(msg, tone)}
        return {"full_response": msg, "spoken_response": self._format_response(msg, tone)}

    def _find_output(self, results, action):
        for r in results:
            if r.get("action") == action and r.get("success"):
                return r.get("output")
        return None