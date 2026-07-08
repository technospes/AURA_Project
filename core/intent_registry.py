"""
Dynamic Intent Registry - No hardcoded intent patterns
Intents register themselves with pattern + priority
"""
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any
import re
import logging

logger = logging.getLogger(__name__)

@dataclass
class IntentDefinition:
    """Defines an intent with its matching pattern and metadata"""
    name: str
    description: str
    patterns: List[str] = field(default_factory=list)  # Regex patterns
    priority: int = 50  # Higher = checked first
    extractor: Optional[Callable] = None  # fn(match) -> entities dict
    requires_context: bool = False  # Needs page context active
    fallback_intent: str = ""  # If this fails, try this intent

class IntentRegistry:
    """Dynamic registry for intent patterns"""
    
    def __init__(self):
        self._intents: Dict[str, IntentDefinition] = {}
        self._compiled: List[tuple] = []  # (priority, compiled_regex, intent_def, extractor)
        self._dirty = True  # Recompile needed
        
    def register(self, intent_def: IntentDefinition):
        """Register an intent definition"""
        self._intents[intent_def.name] = intent_def
        self._dirty = True
        logger.debug(f"[IntentRegistry] Registered: {intent_def.name}")
        
    def register_simple(self, name: str, patterns: List[str], 
                        priority: int = 50, extractor: Callable = None):
        """Quick registration for simple intents"""
        self.register(IntentDefinition(
            name=name,
            description=name,
            patterns=patterns,
            priority=priority,
            extractor=extractor or (lambda m: {})
        ))
        
    def _compile(self):
        """Compile all patterns in priority order"""
        if not self._dirty:
            return
            
        self._compiled = []
        for intent_def in self._intents.values():
            for pattern_str in intent_def.patterns:
                try:
                    compiled = re.compile(pattern_str, re.I)
                    self._compiled.append((
                        intent_def.priority,
                        compiled,
                        intent_def,
                        intent_def.extractor or (lambda m: {})
                    ))
                except re.error as e:
                    logger.error(f"Bad pattern for {intent_def.name}: {e}")
                    
        # Sort by priority (highest first)
        self._compiled.sort(key=lambda x: x[0], reverse=True)
        self._dirty = False
        logger.info(f"[IntentRegistry] Compiled {len(self._compiled)} patterns for {len(self._intents)} intents")
        
    def match(self, text: str, context: Dict = None) -> Optional[Dict]:
        """Match text against all registered patterns. Returns intent dict or None."""
        self._compile()
        context = context or {}
        
        for priority, pattern, intent_def, extractor in self._compiled:
            m = pattern.search(text)
            if m:
                # Check context requirements
                if intent_def.requires_context:
                    if not context.get("has_page_context"):
                        continue
                        
                entities = extractor(m)
                return {
                    "intent": intent_def.name,
                    "entities": entities,
                    "confidence": 0.95,
                    "original_text": text,
                    "matched_pattern": pattern.pattern[:50]
                }
                
        return None
        
    def list_intents(self) -> List[str]:
        """List all registered intent names"""
        return list(self._intents.keys())

# Global instance
intent_registry = IntentRegistry()