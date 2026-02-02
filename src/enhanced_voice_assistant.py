"""
INTEGRATED COGNITIVE VOICE ASSISTANT
=====================================
Integrates cognitive agent capabilities with the existing voice service
"""

import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
import time

from src.cognitive_agent_complete import (
    CognitiveAgent,
    EmotionType,
    ExecutionPlan
)
from web_navigation import AutonomousResearcher

logger = logging.getLogger(__name__)


@dataclass
class VoiceFeatures:
    """Voice characteristics for emotion analysis"""
    pitch: float = 1.0  # 0.5-2.0
    speed: float = 1.0  # 0.5-2.0
    volume: float = 1.0  # 0.5-2.0
    
    @classmethod
    def from_audio(cls, audio_data: bytes) -> 'VoiceFeatures':
        """
        Extract voice features from audio data
        
        In production, this would use audio analysis libraries
        to extract actual pitch, speed, volume
        """
        # Placeholder: return default features
        # Real implementation would use librosa or similar
        return cls()
    
    def to_dict(self) -> Dict[str, float]:
        return {
            'pitch': self.pitch,
            'speed': self.speed,
            'volume': self.volume
        }


class EnhancedVoiceAssistant:
    """
    Enhanced voice assistant with cognitive capabilities
    
    This integrates:
    - Sentiment analysis
    - Explicit reasoning
    - Tool selection
    - Web navigation
    - Memory & learning
    - Self-awareness
    """
    
    def __init__(self, groq_api_key: str = None):
        # Initialize cognitive agent
        self.cognitive_agent_complete = CognitiveAgent(groq_api_key)
        
        # Initialize autonomous researcher
        self.researcher: Optional[AutonomousResearcher] = None
        try:
            from groq import Groq
            import os
            api_key = groq_api_key or os.getenv("GROQ_API_KEY")
            if api_key:
                client = Groq(api_key=api_key)
                self.researcher = AutonomousResearcher(client, headless=True)
                logger.info("✓ Autonomous researcher initialized")
        except Exception as e:
            logger.warning(f"Researcher unavailable: {e}")
        
        # Response templates based on emotion
        self.response_templates = {
            EmotionType.FRUSTRATED: {
                'prefix': "I understand this is frustrating. ",
                'style': "patient and solution-focused"
            },
            EmotionType.ANGRY: {
                'prefix': "I apologize for the inconvenience. ",
                'style': "calm and apologetic"
            },
            EmotionType.IMPATIENT: {
                'prefix': "",
                'style': "brief and direct"
            },
            EmotionType.EXCITED: {
                'prefix': "Great! ",
                'style': "enthusiastic"
            },
            EmotionType.CONFUSED: {
                'prefix': "Let me clarify: ",
                'style': "step-by-step explanation"
            },
            EmotionType.CURIOUS: {
                'prefix': "",
                'style': "informative and detailed"
            },
            EmotionType.CALM: {
                'prefix': "",
                'style': "professional"
            }
        }
    
    def process_voice_command(
        self,
        command_text: str,
        audio_data: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Process voice command with full cognitive capabilities
        
        Args:
            command_text: Transcribed text
            audio_data: Raw audio for voice feature extraction
            
        Returns:
            Response dict with text, metadata, and actions
        """
        start_time = time.time()
        
        # Extract voice features if audio provided
        voice_features = None
        if audio_data:
            voice_features = VoiceFeatures.from_audio(audio_data)
        
        # Use cognitive agent to process
        result = self.cognitive_agent_complete.process_command(
            command_text,
            voice_features.to_dict() if voice_features else None
        )
        
        # Handle different response types
        if result['type'] == 'clarification':
            return self._format_clarification_response(result)
        
        elif result['type'] == 'error':
            return self._format_error_response(result)
        
        elif result['type'] == 'success':
            return self._format_success_response(result, command_text, start_time)
        
        else:
            return {
                'text': "Processing complete, Sir.",
                'metadata': result
            }
    
    def _format_clarification_response(self, result: Dict) -> Dict[str, Any]:
        """Format clarification request"""
        return {
            'text': result['response'],
            'type': 'clarification',
            'metadata': {
                'sentiment': result.get('sentiment')
            }
        }
    
    def _format_error_response(self, result: Dict) -> Dict[str, Any]:
        """Format error response"""
        return {
            'text': result['response'],
            'type': 'error',
            'metadata': {
                'error': result.get('error')
            }
        }
    
    def _format_success_response(
        self,
        result: Dict,
        command: str,
        start_time: float
    ) -> Dict[str, Any]:
        """Format successful execution response"""
        
        # Extract sentiment for tone adaptation
        sentiment_str = result.get('sentiment', 'calm')
        
        # Determine if this was a research task
        is_research = any(word in command.lower() for word in [
            'research', 'find out', 'look up', 'search for',
            'what is', 'tell me about', 'explain'
        ])
        
        response_text = result['response']
        
        # For research tasks with low confidence, offer to do deeper research
        if is_research and result.get('confidence', 1.0) < 0.7 and self.researcher:
            response_text += " Would you like me to research this more thoroughly?"
        
        latency = time.time() - start_time
        
        return {
            'text': response_text,
            'type': 'success',
            'metadata': {
                'sentiment': sentiment_str,
                'plan': result.get('plan'),
                'tools_used': result.get('tools_used', []),
                'confidence': result.get('confidence', 1.0),
                'latency': latency
            }
        }
    
    def perform_deep_research(self, query: str) -> Dict[str, Any]:
        """
        Perform deep autonomous research
        
        Args:
            query: Research query
            
        Returns:
            Research results
        """
        if not self.researcher:
            return {
                'text': "Research capabilities not available, Sir.",
                'type': 'error'
            }
        
        logger.info(f"🔬 Starting deep research: {query}")
        
        try:
            # Perform autonomous research
            result = self.researcher.research(query, max_sources=3)
            
            # Format response
            response_parts = [
                f"I've researched {query} across {result.sources_compared} sources.",
                result.synthesis
            ]
            
            # Add confidence notice if low
            if result.confidence < 0.7:
                response_parts.append(
                    f"Please note, confidence is {result.confidence:.0%} due to source disagreements."
                )
            
            # Mention key findings
            if result.key_findings:
                response_parts.append(
                    "Key findings: " + "; ".join(result.key_findings[:2])
                )
            
            return {
                'text': " ".join(response_parts),
                'type': 'research',
                'metadata': {
                    'sources_compared': result.sources_compared,
                    'confidence': result.confidence,
                    'duplicates_found': result.duplicates_found,
                    'key_findings': result.key_findings
                }
            }
            
        except Exception as e:
            logger.error(f"Research failed: {e}")
            return {
                'text': "Research encountered an error, Sir.",
                'type': 'error',
                'metadata': {'error': str(e)}
            }
    
    def get_agent_statistics(self) -> Dict[str, Any]:
        """Get cognitive agent statistics"""
        stats = self.cognitive_agent_complete.get_statistics()
        
        return {
            'memories': stats['memories_stored'],
            'emotional_pattern': stats['emotional_pattern'],
            'errors': stats['errors_encountered'],
            'plans_created': stats['plans_created']
        }
    
    def explain_reasoning(self) -> str:
        """
        Explain the agent's last reasoning process
        
        Returns user-friendly explanation of what the agent thought and why
        """
        if not self.cognitive_agent_complete.current_plan:
            return "No recent task to explain, Sir."
        
        plan = self.cognitive_agent_complete.current_plan
        
        explanation_parts = [
            f"Here's how I approached: {plan.task}",
            f"\nI created a {plan.estimated_steps}-step plan with {plan.confidence:.0%} confidence.",
            "\nMy reasoning:"
        ]
        
        for i, step in enumerate(plan.reasoning_chain[:3], 1):  # First 3 steps
            explanation_parts.append(
                f"\nStep {i}: {step.thought}"
            )
            if step.tool:
                explanation_parts.append(f"  (using {step.tool})")
        
        if plan.risks:
            explanation_parts.append(
                f"\nPotential issues I considered: {', '.join(plan.risks[:2])}"
            )
        
        return "".join(explanation_parts)
    
    def cleanup(self):
        """Clean up resources"""
        if self.researcher:
            self.researcher.cleanup()
        
        logger.info("✓ Enhanced assistant cleaned up")


# ============================================================================
# QUICK INTEGRATION HELPER
# ============================================================================

def create_enhanced_assistant(groq_api_key: str = None) -> EnhancedVoiceAssistant:
    """
    Factory function to create enhanced assistant
    
    Args:
        groq_api_key: Optional API key (will use env var if not provided)
        
    Returns:
        EnhancedVoiceAssistant instance
    """
    return EnhancedVoiceAssistant(groq_api_key)


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    import os
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create assistant
    assistant = create_enhanced_assistant()
    
    # Example commands
    test_commands = [
        "I'm frustrated! This isn't working!",  # Tests emotion detection
        "What is quantum computing?",  # Tests knowledge assessment
        "Research the latest developments in AI",  # Tests autonomous research
        "Open Spotify",  # Tests simple action
        "Tell me about it",  # Tests clarification
    ]
    
    print("\n" + "="*60)
    print("ENHANCED VOICE ASSISTANT - TEST MODE")
    print("="*60)
    
    for command in test_commands:
        print(f"\n🎤 User: {command}")
        
        result = assistant.process_voice_command(command)
        
        print(f"💬 Assistant: {result['text']}")
        print(f"📊 Type: {result['type']}")
        
        if result.get('metadata'):
            metadata = result['metadata']
            if 'sentiment' in metadata:
                print(f"😊 Detected emotion: {metadata['sentiment']}")
            if 'confidence' in metadata:
                print(f"🎯 Confidence: {metadata['confidence']:.0%}")
            if 'tools_used' in metadata:
                print(f"🔧 Tools used: {metadata['tools_used']}")
        
        print("-" * 60)
    
    # Show statistics
    stats = assistant.get_agent_statistics()
    print(f"\n📊 AGENT STATISTICS:")
    print(f"  Memories: {stats['memories']}")
    print(f"  Emotional pattern: {stats['emotional_pattern']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Plans created: {stats['plans_created']}")
    
    # Cleanup
    assistant.cleanup()
    
    print("\n✓ Test complete")
