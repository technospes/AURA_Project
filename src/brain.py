import os
import json
from typing import Dict, Any, Optional, List
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime
import time
from duckduckgo_search import DDGS
import logging
from dataclasses import dataclass
from functools import lru_cache

# Import your existing tools
from src.native_opener import open_app, close_app, play_media, search_web, close_tab

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ToolDefinition:
    """Data class for tool definitions"""
    name: str
    description: str
    parameters: Dict[str, Any]
    required: List[str]
    # ✅ NO __init__ here - dataclass handles it automatically


# ✅ THIS IS A SEPARATE CLASS, NOT INSIDE ToolDefinition!
class AIAssistant:
    def __init__(self, model: str = "llama-3.3-70b-versatile"):
        """Initialize AI Assistant with optimized configuration"""
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.model = model
        self.history_max_length = 20
        
        # 🔥 FIX: Correct method name
        self._init_history()  # NOT _initialize_history
        
        self.tools = self._define_tools()
        self._tool_map = self._create_tool_map()
        self._search_cache = {}

    def _init_history(self) -> None:
        """Initialize system message with current context"""
        self.history = [
            {
                "role": "system", 
                "content": f"""
                You are JARVIS (Just A Rather Very Intelligent System), Tony Stark's AI.
                Current Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}
                
                JARVIS PERSONALITY:
                1. EXTREMELY BRIEF - 1 sentence maximum unless details requested
                2. PROFESSIONAL but not cold - efficient, not emotional
                3. Use "Sir" for successful actions and greetings
                4. NO unnecessary words - get straight to the point
                5. For failures: "Unable to comply" or "System error"
                6. For queries: Direct factual answers, no speculation
                
                RESPONSE EXAMPLES:
                - Action: "Opening Spotify, Sir"
                - Weather: "25°C and clear, Sir"
                - Question: "Quantum computing uses qubits"
                - Error: "Unable to access weather data"
                - Confirmation: "Confirmed, Sir"
                
                CURRENT MODE: Direct system control assistant.
                MAX RESPONSE LENGTH: 10 words for actions, 15 words for information.
                """
            }
        ]

    def generate_jarvis_response(self, query: str, intent_data: dict = None) -> str:
        """Generate Jarvis-style response for conversational queries"""
        try:
            # Weather queries
            if any(keyword in query.lower() for keyword in ["weather", "temperature", "forecast"]):
                location = ""
                for keyword in ["weather in ", "temperature in ", "forecast for "]:
                    if keyword in query.lower():
                        location = query.lower().split(keyword, 1)[1].strip()
                        break
                
                if location:
                    return f"Displaying weather data for {location}, Sir"
                else:
                    return "Displaying current weather, Sir"
            
            # Time/date queries
            query_lower = query.lower()
            if "time" in query_lower:
                current_time = time.strftime("%I:%M %p")
                return f"It's {current_time}, Sir"
            elif "date" in query_lower:
                current_date = time.strftime("%B %d, %Y")
                return f"Today is {current_date}, Sir"
            elif "day" in query_lower:
                current_day = time.strftime("%A")
                return f"It's {current_day}, Sir"
            
            # General greetings
            if query_lower in ["how are you", "how are you?"]:
                return "All systems operational, Sir"
            
            # General knowledge with JARVIS tone
            jarvis_prompt = f"""You are JARVIS from Iron Man. Respond concisely:
- 1-2 sentences maximum
- Use "Sir" appropriately
- Be sophisticated but brief

Query: {query}"""
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are JARVIS. Be concise and sophisticated."},
                    {"role": "user", "content": jarvis_prompt}
                ],
                temperature=0.7,
                max_tokens=150
            )
            
            jarvis_response = response.choices[0].message.content.strip()
            jarvis_response = jarvis_response.replace("JARVIS: ", "").replace("Jarvis: ", "")
            
            return jarvis_response
            
        except Exception as e:
            logger.error(f"Generate Jarvis response error: {e}")
            return "Data unavailable, Sir"

    def understand_intent(self, user_command: str) -> Optional[Dict[str, Any]]:
        """Use Llama 3 to understand user intent - FIXED VERSION"""
        try:
            system_prompt = """You are a voice command parser. Extract the intent from user commands.
Return ONLY a JSON object with this exact structure:
{
    "action": "open|close|play|search|type|chat|ask|explain",
    "target": "application_name or search_query or question",
    "confidence": 0.0-1.0,
    "parameters": {}
}

EXAMPLES:
1. "open spotify" -> {"action": "open", "target": "spotify", "confidence": 0.95}
2. "play starboy on youtube" -> {"action": "play", "target": "starboy", "parameters": {"platform": "youtube"}, "confidence": 0.95}
3. "how are you" -> {"action": "chat", "target": "how are you", "confidence": 0.9}
4. "open youtube" -> {"action": "open", "target": "youtube", "confidence": 0.95}

IMPORTANT:
- For typing: PRESERVE punctuation
- For apps: lowercase target
- For questions: use "chat" or "ask"
- Return valid JSON only"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_command}
                ],
                temperature=0.1,
                max_tokens=200
            )
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"Raw Llama response: {result_text}")
            
            # Clean up response
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                result_text = "\n".join([line for line in lines if not line.strip().startswith("```")])
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            
            # Parse JSON
            intent = json.loads(result_text.strip())
            
            # Validate required fields
            if "action" not in intent or "target" not in intent:
                logger.warning(f"Missing required fields in intent: {intent}")
                return None
            
            # Ensure lowercase target for apps
            action = intent.get("action", "")
            if action in ["open", "close"] and action != "type":
                intent["target"] = intent["target"].lower()
            
            # Add defaults
            if "confidence" not in intent:
                intent["confidence"] = 0.8
            
            if "parameters" not in intent:
                intent["parameters"] = {}
            
            logger.info(f"Parsed intent: {intent}")
            return intent
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Llama intent understanding failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _define_tools(self) -> List[Dict[str, Any]]:
        """Define available tools with consistent structure"""
        tools_data = [
            ToolDefinition(
                name="open_app",
                description="Opens a desktop application by name",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Application name"}},
                },
                required=["name"]
            ),
            ToolDefinition(
                name="play_media",
                description="Plays music or video on specified platform",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Media name or search query"},
                        "platform": {"type": "string", "enum": ["youtube", "spotify"], 
                                    "description": "Platform to use"}
                    }
                },
                required=["name"]
            ),
            ToolDefinition(
                name="web_search",
                description="Searches internet for information, news, weather, or facts",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Search query"}},
                },
                required=["query"]
            ),
        ]
        
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        **tool.parameters,
                        "required": tool.required
                    }
                }
            }
            for tool in tools_data
        ]
    
    def _create_tool_map(self) -> Dict[str, Any]:
        """Map tool names to handler functions"""
        return {
            "open_app": lambda args: open_app(args['name']),
            "play_media": lambda args: play_media(args['name'], args.get('platform', 'youtube')),
            "web_search": lambda args: self._perform_web_search(args['query']),
        }
    
    @lru_cache(maxsize=32)
    def _perform_web_search(self, query: str) -> str:
        """Optimized web search with caching"""
        logger.info(f"🔍 Searching web for: {query}")
        
        if query in self._search_cache:
            logger.info("Using cached search results")
            return self._search_cache[query]
        
        try:
            with DDGS(timeout=10) as ddgs:
                results = list(ddgs.text(query, max_results=3))
            
            formatted_results = []
            for i, result in enumerate(results, 1):
                formatted_results.append(f"{i}. {result.get('title', 'No title')}: {result.get('body', 'No content')}")
            
            result_str = "\n".join(formatted_results)
            self._search_cache[query] = result_str
            
            return result_str
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return f"Search failed. Please try again. Error: {str(e)}"
    
    def _trim_history(self) -> None:
        """Trim history to prevent token overflow"""
        if len(self.history) > self.history_max_length:
            self.history = [self.history[0]] + self.history[-(self.history_max_length - 1):]
    
    def _call_tools(self, tool_calls: List[Any]) -> List[Dict[str, str]]:
        """Execute tool calls and collect results"""
        tool_responses = []
        
        for tool_call in tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            logger.info(f"🛠️ Calling tool: {func_name} with args: {args}")
            
            try:
                if func_name in self._tool_map:
                    result = self._tool_map[func_name](args)
                else:
                    result = f"Tool '{func_name}' not implemented"
                
                tool_responses.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": str(result)
                })
                
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                tool_responses.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": f"Error: {str(e)}"
                })
        
        return tool_responses
    
    def chat(self, user_input: str) -> str:
        """Main chat - optimized for Jarvis brevity"""
        self.history.append({"role": "user", "content": user_input})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.history,
                tools=self.tools if len(self.history) < 5 else None,
                temperature=0.2,
                max_tokens=50,
                stop=["."]
            )
            
            response_msg = response.choices[0].message
            tool_calls = response_msg.tool_calls if hasattr(response_msg, 'tool_calls') else None
            
            if tool_calls:
                self.history.append(response_msg)
                self._call_tools(tool_calls)
                return "Action executed"
            
            ai_text = response_msg.content.strip()
            
            if len(ai_text.split()) > 12:
                sentences = ai_text.split('. ')
                ai_text = sentences[0] + '.'
            
        except Exception as e:
            logger.error(f"Chat error: {e}")
            ai_text = "System error"
        
        self.history.append({"role": "assistant", "content": ai_text})
        self._trim_history()
        
        return ai_text
    
    def clear_history(self) -> None:
        """Clear conversation history (except system message)"""
        self.history = [self.history[0]]
        logger.info("History cleared")
    
    def get_context_summary(self) -> str:
        """Get summary of current conversation context"""
        user_messages = [msg["content"] for msg in self.history if msg["role"] == "user"]
        return f"Recent conversation: {', '.join(user_messages[-3:])}"