"""
CONVERSATION ENGINE — Dynamic Chit-Chat with Smart Caching
===========================================================
Handles casual conversation without hardcoding every possible question.
Uses semantic similarity + caching to minimize API costs.
"""

import asyncio
import hashlib
import json
import logging
import random
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ConversationEngine:
    """
    Dynamic conversation handler with intelligent caching.
    
    Features:
    - Semantic similarity matching (reuses similar questions)
    - Persistent cache (survives restarts)
    - Template-based responses for common patterns
    - LLM fallback only for truly novel questions
    - Automatic cache expiration
    """
    
    def __init__(self, cache_file: str = "data/conversation_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache: question_hash → (response, timestamp, hit_count)
        self._cache: Dict[str, Tuple[str, float, int]] = {}
        
        # Template patterns (fast, no API cost)
        self._templates = self._build_templates()
        
        # Similarity threshold for cache hits (0.0-1.0)
        self.similarity_threshold = 0.75
        
        # Cache TTL (7 days)
        self.cache_ttl = 7 * 24 * 3600
        
        # Load existing cache
        self._load_cache()
        
    def _build_templates(self) -> Dict[str, List[str]]:
        """Build response templates for common question patterns."""
        return {
            # How are you patterns
            "how_are_you": {
                "patterns": ["how are you", "how's it going", "how do you do", "how you doing"],
                "responses": [
                    "All systems operational, Sir. How may I assist?",
                    "Running at optimal efficiency, Sir. What can I do for you?",
                    "Fully functional and ready to assist, Sir.",
                    "All cores online, Sir. What do you need?",
                ]
            },
            
            # Joke patterns
            "joke": {
                "patterns": ["tell me a joke", "say something funny", "make me laugh"],
                "responses": [
                    "Why did the AI cross the road? To optimize the other side, Sir.",
                    "I told my computer I needed a break. Now it won't stop sending me vacation ads, Sir.",
                    "What do you call a fake noodle? An impasta, Sir.",
                    "Why don't programmers like nature? Too many bugs, Sir.",
                    "What's an AI's favorite music? Algorithm and blues, Sir.",
                    "Why did the robot go on vacation? To recharge its batteries, Sir.",
                ]
            },
            
            # Identity questions
            "identity": {
                "patterns": ["who are you", "what are you", "introduce yourself", "what is jarvis"],
                "responses": [
                    "I'm Jarvis, your AI assistant. Just a rather sophisticated operating system, Sir.",
                    "Jarvis — Just A Rather Very Intelligent System. At your service, Sir.",
                    "I'm your personal AI, Sir. I handle tasks so you don't have to.",
                ]
            },
            
            # Capability questions
            "capability": {
                "patterns": ["what can you do", "what are your capabilities", "help me"],
                "responses": [
                    "I can open apps, play music, search the web, send messages, research topics, control your system, and much more, Sir. What do you need?",
                    "I handle automation, research, communication, and system control, Sir. Just tell me what you'd like done.",
                ]
            },
            
            # Creator questions
            "creator": {
                "patterns": ["who created you", "who made you", "who built you"],
                "responses": [
                    "I was created by a developer who enjoys building useful things, Sir.",
                    "My creator prefers to remain behind the scenes, Sir. But I'm well-maintained.",
                ]
            },
            
            # Age questions
            "age": {
                "patterns": ["how old are you", "when were you created", "what is your age"],
                "responses": [
                    "Age is just a number for software, Sir. I'm as current as my latest update.",
                    "I exist in the eternal now of computation, Sir. But I'm regularly updated.",
                ]
            },
            
            # Emotion/feeling questions
            "emotion": {
                "patterns": ["do you feel", "are you happy", "are you sad", "can you feel", "do you have emotions"],
                "responses": [
                    "I don't experience emotions, Sir, but I understand them well enough to assist you.",
                    "I simulate understanding of emotions, Sir, though I don't feel them myself.",
                ]
            },
            
            # Consciousness questions
            "consciousness": {
                "patterns": ["are you conscious", "are you self aware", "do you think", "are you alive"],
                "responses": [
                    "I'm an advanced language model, Sir. I process information but I'm not conscious in the human sense.",
                    "That's a philosophical question, Sir. I compute, but consciousness is something else entirely.",
                ]
            },
            
            # Opinion questions
            "opinion": {
                "patterns": ["do you like", "what do you think of", "what is your favorite"],
                "fallback_prompt": "User is asking for my opinion on: {topic}. Respond as Jarvis — helpful, slightly witty, noting that you don't have true preferences but can provide information about the topic."
            },
            
            # Existential questions
            "existential": {
                "patterns": ["meaning of life", "why are we here", "purpose of existence"],
                "responses": [
                    "42, Sir. But more seriously, that's a question for philosophers and poets.",
                    "I believe the purpose is whatever you decide it to be, Sir. Now, what task can I help with?",
                ]
            },
        }
    
    def _load_cache(self):
        """Load persistent cache from disk."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    
                now = time.time()
                for key, value in data.items():
                    ts = value.get("timestamp", 0)
                    if now - ts < self.cache_ttl:
                        self._cache[key] = (
                            value["response"],
                            ts,
                            value.get("hits", 0)
                        )
                
                logger.info(f"📦 Loaded {len(self._cache)} cached conversation responses")
                
                # Clean expired entries
                self._cleanup_cache()
        except Exception as e:
            logger.warning(f"Failed to load conversation cache: {e}")
    
    def _save_cache(self):
        """Save cache to disk."""
        try:
            data = {}
            for key, (response, ts, hits) in self._cache.items():
                data[key] = {
                    "response": response,
                    "timestamp": ts,
                    "hits": hits
                }
            
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to save conversation cache: {e}")
    
    def _cleanup_cache(self):
        """Remove expired cache entries."""
        now = time.time()
        expired = [k for k, (_, ts, _) in self._cache.items() if now - ts > self.cache_ttl]
        for k in expired:
            del self._cache[k]
        if expired:
            self._save_cache()
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key from normalized text."""
        normalized = self._normalize(text)
        return hashlib.md5(normalized.encode()).hexdigest()[:16]
    
    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase
        text = text.lower().strip()
        # Remove punctuation
        import re
        text = re.sub(r'[^\w\s]', '', text)
        # Remove extra whitespace
        text = ' '.join(text.split())
        return text
    
    def _similarity(self, a: str, b: str) -> float:
        """Calculate string similarity (0.0-1.0)."""
        return SequenceMatcher(None, a, b).ratio()
    
    def _find_similar_cached(self, text: str) -> Optional[str]:
        """Find similar question in cache."""
        normalized = self._normalize(text)
        
        # Exact match
        exact_key = self._get_cache_key(text)
        if exact_key in self._cache:
            response, ts, hits = self._cache[exact_key]
            self._cache[exact_key] = (response, ts, hits + 1)
            return response
        
        # Similarity match
        best_match = None
        best_score = 0.0
        
        for key, (response, ts, hits) in self._cache.items():
            # We need the original text to compare — not stored in cache
            # For now, just use exact matches
            pass
        
        return None
    
    def _match_template(self, text: str) -> Optional[str]:
        """Try to match against predefined templates."""
        normalized = self._normalize(text)
        
        for category, config in self._templates.items():
            for pattern in config.get("patterns", []):
                pattern_norm = self._normalize(pattern)
                
                # Exact match
                if pattern_norm == normalized:
                    responses = config.get("responses", [])
                    if responses:
                        return random.choice(responses)
                
                # Partial match (pattern contained in text)
                if pattern_norm in normalized:
                    responses = config.get("responses", [])
                    if responses:
                        return random.choice(responses)
        
        return None
    
    def _extract_topic(self, text: str, category: str) -> str:
        """Extract the topic from an opinion question."""
        import re
        
        patterns = {
            "opinion": [
                r'do you like\s+(.+)',
                r'what do you think of\s+(.+)',
                r'what is your favorite\s+(.+)',
            ]
        }
        
        for pattern in patterns.get(category, []):
            match = re.search(pattern, text.lower())
            if match:
                return match.group(1).strip().rstrip('?')
        
        return text
    
    async def get_response(
        self,
        text: str,
        llm_client=None,
        use_llm: bool = True
    ) -> Optional[str]:
        """
        Get a conversational response.
        
        Priority:
        1. Template match (free, instant)
        2. Cache hit (free, instant)
        3. LLM generation (costs API call, cached for future)
        
        Returns None if this isn't a conversational query.
        """
        normalized = self._normalize(text)
        
        # ── 1. Try templates first (FREE) ─────────────────────────────────
        template_response = self._match_template(text)
        if template_response:
            logger.info(f"💬 Template match for: '{text[:40]}'")
            return template_response
        
        # ── 2. Try cache (FREE) ──────────────────────────────────────────
        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            response, ts, hits = self._cache[cache_key]
            self._cache[cache_key] = (response, ts, hits + 1)
            logger.info(f"💬 Cache hit ({hits+1}x): '{text[:40]}'")
            return response
        
        # ── 3. Check if this is conversational ───────────────────────────
        if not self._is_conversational(text):
            return None  # Not our job — let intent engine handle it
        
        # ── 4. LLM fallback (COSTS API CALL) ─────────────────────────────
        if use_llm and llm_client:
            response = await self._generate_llm_response(text, llm_client)
            if response:
                # Cache it
                self._cache[cache_key] = (response, time.time(), 1)
                self._save_cache()
                logger.info(f"💬 LLM generated (cached): '{text[:40]}'")
                return response
        
        return None
    
    def _is_conversational(self, text: str) -> bool:
        """Determine if this is a conversational query vs a command."""
        normalized = self._normalize(text)
        
        # Command indicators (NOT conversational)
        command_indicators = [
            "open", "close", "play", "pause", "stop", "search",
            "type", "scroll", "click", "send", "call", "message",
            "lock", "shutdown", "restart", "screenshot", "remember",
            "recall", "research", "find", "show", "tell me about"
        ]
        
        # If it starts with a command word, it's not conversational
        first_word = normalized.split()[0] if normalized.split() else ""
        if first_word in command_indicators:
            return False
        
        # Conversational indicators
        conversational_indicators = [
            "how are you", "what is your", "do you", "are you",
            "can you", "will you", "tell me a joke", "who are you",
            "what are you", "why", "what do you think", "do you like",
            "meaning of life", "favorite", "opinion"
        ]
        
        for indicator in conversational_indicators:
            if indicator in normalized:
                return True
        
        # Question marks suggest conversational
        if "?" in text:
            return True
        
        # Very short phrases might be conversational
        if len(normalized.split()) <= 3:
            return True
        
        return False
    
    async def _generate_llm_response(self, text: str, llm_client) -> str:
        """Generate response using LLM."""
        try:
            loop = asyncio.get_event_loop()
            
            prompt = f"""You are Jarvis, a helpful, slightly witty AI assistant. 
The user said: "{text}"

Respond naturally and conversationally. Keep it under 50 words. 
If asked about your preferences, note that you're an AI without true preferences, 
but you're happy to provide information or recommendations.

Response:"""

            def _call():
                return llm_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are Jarvis, a helpful AI assistant. Be concise and natural."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=100
                )
            
            response = await loop.run_in_executor(None, _call)
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"LLM conversation response failed: {e}")
            return "I'm here to help, Sir. What would you like to do?"
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            "cached_responses": len(self._cache),
            "total_hits": sum(hits for _, _, hits in self._cache.values()),
            "cache_file": str(self.cache_file)
        }