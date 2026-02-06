"""
Enhanced AURA AI Brain with Research Agent - FIXED VERSION
Built on existing brain.py - adds research, memory, files, code execution
"""
import os
import requests
import json
import asyncio
import logging
from typing import Dict, Any, Optional, List
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime
import time
from ddgs import DDGS
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import hashlib
import re

# Import your existing tools
from src.native_opener import open_app, close_app, play_media, search_web, close_tab

load_dotenv()
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Import production features
try:
    from src.brain_PRODUCTION import (
        ExecutionVerifier,
        SystemStateTracker,
        RetryExecutor,
        MultiStepPlanner,
        SelfCorrectingAgent,
        ProductionOpenAppTool
    )
    PRODUCTION_MODE = True
    logger.info("✓ Production features loaded")
except ImportError as e:
    logger.warning(f"Production features not available: {e}")
    PRODUCTION_MODE = False

class ModelSelector:
    """Smart model selection to minimize token usage"""
    FAST_MODEL = "llama-3.1-8b-instant"
    SMART_MODEL = "llama-3.3-70b-versatile"
    
    # Keywords that indicate complex queries needing smart model
    COMPLEX_KEYWORDS = [
        'research', 'analyze', 'compare', 'explain in detail',
        'comprehensive', 'deep dive', 'summarize article',
        'write essay', 'create report', 'complex'
    ]
    
    # Simple command patterns (always use fast model)
    SIMPLE_PATTERNS = [
        r'^(open|close|play|pause|stop|start|launch)\s+',
        r'^(search|google|find)\s+',
        r'^(type|write|enter)\s+',
        r'^close\s+tab',
        r'^(what|show|get)\s+(time|date|weather)',
    ]
    
    @classmethod
    def select_model(cls, command: str, tool_name: str = None) -> str:
        """
        Select appropriate model based on command complexity
        
        Args:
            command: User's command text
            tool_name: Name of tool being called (if known)
            
        Returns:
            Model name to use
        """
        command_lower = command.lower().strip()
        
        # 1. Force smart model for research tool
        if tool_name == "deep_research":
            logger.info(f"🧠 Using SMART model for: {tool_name}")
            return cls.SMART_MODEL
        
        # 2. Check for complex keywords
        for keyword in cls.COMPLEX_KEYWORDS:
            if keyword in command_lower:
                logger.info(f"🧠 Using SMART model (detected: {keyword})")
                return cls.SMART_MODEL
        
        # 3. Use fast model for simple patterns
        import re
        for pattern in cls.SIMPLE_PATTERNS:
            if re.match(pattern, command_lower):
                logger.info(f"⚡ Using FAST model (simple pattern)")
                return cls.FAST_MODEL
        
        # 4. Fast model for short commands
        if len(command.split()) <= 5:
            logger.info(f"⚡ Using FAST model (short command)")
            return cls.FAST_MODEL
        
        # 5. Fast model for medium-length queries
        if len(command.split()) <= 15:
            logger.info(f"⚡ Using FAST model (medium query)")
            return cls.FAST_MODEL
        
        # 6. Smart model for long, complex queries
        logger.info(f"🧠 Using SMART model (long query)")
        return cls.SMART_MODEL
    
@dataclass
class ToolDefinition:
    """Data class for tool definitions"""
    name: str
    description: str
    parameters: Dict[str, Any]
    required: List[str]


class AIAssistant:
    """
    Enhanced AI Assistant with Research Agent Capabilities
    
    NEW FEATURES:
    - Deep research with multi-source synthesis
    - Persistent memory system
    - File read/write operations
    - Safe Python code execution
    - Data analysis
    - Async tool execution
    - Tool usage statistics
    """
    
    def __init__(self, model: str = "llama-3.1-8b-instant"):
        """Initialize AI Assistant with production features"""
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.model = model
        self.history_max_length = 20
        
        # Memory system
        self.memory_file = Path("aura_memory.json")
        self.memory = self._load_memory()
        
        # Research cache
        self._search_cache = {}
        self._research_cache = {}
        
        # Statistics
        self.stats = {
        "commands_processed": 0,
        "tools_used": {},
        "research_queries": 0,
        "cache_hits": 0,
        "verification_checks": 0,
        "retries_attempted": 0,
        "self_corrections": 0,
        "fast_model_calls": 0,
        "smart_model_calls": 0,
        "tokens_saved_estimate": 0
    }
        
        # 🔥 PRODUCTION FEATURES
        if PRODUCTION_MODE:
            self.verifier = ExecutionVerifier()
            self.state_tracker = SystemStateTracker()
            self.retry_executor = RetryExecutor(self.verifier, self.state_tracker)
            self.planner = MultiStepPlanner(self.retry_executor, self.state_tracker)
            self.self_corrector = SelfCorrectingAgent()
            self.production_tools = ProductionOpenAppTool()
            logger.info("✓ Production AI Agent initialized")
        else:
            self.production_tools = None
            logger.warning("Running in basic mode (no production features)")
        
        self._init_history()
        self.tools = self._define_enhanced_tools()
        self._tool_map = self._create_enhanced_tool_map()

    # ========================================================================
    # MEMORY SYSTEM (NEW)
    # ========================================================================
    
    def _load_memory(self) -> Dict:
        """Load persistent memory from disk"""
        try:
            if self.memory_file.exists():
                with open(self.memory_file, 'r') as f:
                    memory = json.load(f)
                    logger.info(f"Loaded memory: {len(memory.get('facts', {}))} facts")
                    return memory
        except Exception as e:
            logger.error(f"Memory load failed: {e}")
        
        return {
            "facts": {},
            "preferences": {},
            "past_tasks": [],
            "feedback": []
        }
    
    def _save_memory(self):
        """Save memory to disk"""
        try:
            with open(self.memory_file, 'w') as f:
                json.dump(self.memory, f, indent=2)
            logger.debug("Memory saved")
        except Exception as e:
            logger.error(f"Memory save failed: {e}")
    
    def _get_relevant_context(self, user_input: str) -> str:
        """Get relevant facts from memory based on input"""
        relevant = []
        input_lower = user_input.lower()
        
        for key, fact in self.memory.get("facts", {}).items():
            if key.lower() in input_lower or any(word in input_lower for word in key.lower().split()):
                relevant.append(f"{key}: {fact.get('value', fact)}")
        
        return "; ".join(relevant[:3]) if relevant else ""  # Max 3 facts

    # ========================================================================
    # INITIALIZATION (ENHANCED)
    # ========================================================================

    def _init_history(self) -> None:
        """Initialize system message with JARVIS personality + context"""
        recent_facts = list(self.memory.get("facts", {}).items())[-5:]
        facts_str = "\n".join([f"- {k}: {v.get('value', v)}" for k, v in recent_facts])
        
        self.history = [
            {
                "role": "system", 
                "content": f"""You are JARVIS (Just A Rather Very Intelligent System), Tony Stark's AI.
    Current Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}

    CRITICAL - TOOL CALLING RULES:
    1. NEVER generate tool calls as text like "<function=...>" or "tool_name(...)"
    2. ALWAYS use the native tool calling mechanism provided by the API
    3. The system will automatically convert your tool_use intentions into proper API calls
    4. DO NOT hallucinate function syntax - just request the tool naturally

    JARVIS PERSONALITY:
    1. BRIEF for simple tasks - 1 sentence maximum
    2. DETAILED for research - comprehensive analysis when needed
    3. PROFESSIONAL but not cold - efficient, sophisticated
    4. Use "Sir" for successful actions and greetings
    5. NO unnecessary words for simple commands
    6. For research: Provide structured, thorough analysis
    7. For failures: "Unable to comply" + brief reason
    8. For queries: Direct factual answers

    TOOL SELECTION INTELLIGENCE:
    When user says "open X":
    - If X is commonly known website (youtube, google, facebook, netflix, twitter, etc.) → use web browser
    - If X ends with .com, .org, .net, etc. → definitely a website → use web browser
    - If X is a known desktop app (spotify, notepad, chrome, discord) → use app launcher
    - When unsure, prefer web browser for consumer brands (youtube, netflix, etc.)

    PRODUCTION FEATURES ACTIVE:
    - Execution Verification: All actions are verified (PID/window checks)
    - State Awareness: I know what's already running
    - Retry Logic: If something fails, I try alternatives
    - Multi-Step Planning: Complex tasks are broken into verified steps
    - Self-Correction: I learn from failures and improve

    INTELLIGENT BEHAVIOR:
    - Before opening an app, I check if it's already running
    - If already running, I focus the window instead
    - If primary method fails, I automatically try fallbacks
    - I remember what worked before and use it next time
    - Complex tasks use verified multi-step plans

    Examples of intelligent decisions:
    - "open youtube" → It's a website, most people access via browser → open in browser
    - "open spotify" → Could be app or web, but desktop app is primary → open desktop app
    - "open youtube.com" → Explicit URL → open in browser
    - "open notepad" → System utility → open desktop app
    - "weather in X" → Requires live data → use web_search or dedicated weather API
    - "research Y" → In-depth → use deep_research tool

    AVAILABLE TOOLS - USE THESE, DON'T HALLUCINATE:
    - open_app: Opens desktop applications OR websites (smart detection built-in)
    - close_app: Closes running applications
    - play_media: Plays content on YouTube or Spotify
    - web_search: Quick web search for current information
    - deep_research: Comprehensive research with multiple sources
    - get_weather: Live weather data (if available)
    - file operations: read_file, write_file, list_files
    - memory: remember_fact, recall_fact
    - execute_python: Safe Python code execution

    RESPONSE PATTERNS:
    - Simple action: "Opening Spotify, Sir" (10 words max)
    - Research start: "Initiating research, Sir"
    - Research result: [Detailed synthesis with structure]
    - File operation: "File saved to [name], Sir"
    - Memory: "Remembered, Sir" or "I recall [fact], Sir"
    - Error: "Unable to comply, Sir. [reason]"

    REMEMBERED FACTS:
    {facts_str if facts_str else "None yet - building context as we go"}

    CURRENT MODE: Advanced AI agent with research capabilities.
    """
            }
        ]

    # ========================================================================
    # ENHANCED TOOL DEFINITIONS
    # ========================================================================

    def _define_enhanced_tools(self) -> List[Dict[str, Any]]:
        """Define complete tool set including research capabilities"""
        tools_data = [
            # ===== BASIC SYSTEM TOOLS (Original) =====
            ToolDefinition(
                name="open_app",
                description="Opens desktop applications or websites. Examples: 'chrome', 'spotify', 'notepad', URLs",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Application name (lowercase) or URL"}},
                },
                required=["name"]
            ),
            ToolDefinition(
            name="get_weather",
            description="Get current weather for any location using live data",
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name or location"}
                },
            },
            required=["location"]
        ),
            ToolDefinition(
                name="close_app",
                description="Closes running applications or browser tabs",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Application name or 'tab'"}},
                },
                required=["name"]
            ),
            ToolDefinition(
                name="play_media",
                description="Plays music or video on Spotify or YouTube",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Song, artist, or video title"},
                        "platform": {"type": "string", "enum": ["youtube", "spotify"], 
                                    "description": "Platform (default: youtube)"}
                    }
                },
                required=["name"]
            ),
            ToolDefinition(
                name="web_search",
                description="Quick web search (use for single questions)",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Search query"}},
                },
                required=["query"]
            ),
            
            # ===== ENHANCED RESEARCH TOOL (NEW) =====
            ToolDefinition(
                name="deep_research",
                description="Multi-source research with AI synthesis. Use for complex topics requiring comprehensive analysis.",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Research topic or question"},
                        "depth": {
                            "type": "string", 
                            "enum": ["quick", "standard", "comprehensive"],
                            "description": "Research depth (default: standard)"
                        },
                        "focus_areas": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional specific areas to focus on"
                        }
                    }
                },
                required=["topic"]
            ),
            
            # ===== FILE TOOLS (NEW) =====
            ToolDefinition(
                name="read_file",
                description="Read contents of a text file",
                parameters={
                    "type": "object",
                    "properties": {"filepath": {"type": "string", "description": "Path to file"}},
                },
                required=["filepath"]
            ),
            ToolDefinition(
                name="write_file",
                description="Write or append content to a file",
                parameters={
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "Path to file"},
                        "content": {"type": "string", "description": "Content to write"},
                        "mode": {
                            "type": "string",
                            "enum": ["overwrite", "append"],
                            "description": "Write mode (default: overwrite)"
                        }
                    }
                },
                required=["filepath", "content"]
            ),
            ToolDefinition(
                name="list_files",
                description="List files in a directory",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Directory path (default: current)"}
                    }
                },
                required=[]
            ),
            
            # ===== CODE EXECUTION (NEW) =====
            ToolDefinition(
                name="execute_python",
                description="Execute safe Python code for calculations and data processing",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python expression to execute"}
                    }
                },
                required=["code"]
            ),
            
            # ===== MEMORY TOOLS (NEW) =====
            ToolDefinition(
                name="remember_fact",
                description="Store a fact or preference in persistent memory",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Fact identifier (e.g., 'user_name')"},
                        "value": {"type": "string", "description": "Fact value"}
                    }
                },
                required=["key", "value"]
            ),
            ToolDefinition(
                name="open_website",
                description="Open a website in the default web browser. Use for URLs and web services.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Website URL or name (e.g., 'youtube.com' or 'youtube')"}
                    },
                },
                required=["url"]
            ),
            ToolDefinition(
                name="recall_fact",
                description="Retrieve a stored fact from memory",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Fact identifier"}
                    }
                },
                required=["key"]
            )
        ]
        
        # Convert to Groq tool format
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

    def _create_enhanced_tool_map(self) -> Dict:
        """Map tool names to implementation functions"""
        return {
            # Basic tools
            "open_app": lambda args: self._open_app_tool(args["name"]),
            "close_app": lambda args: self._close_app_tool(args["name"]),
            "play_media": lambda args: self._play_media_tool(args["name"], args.get("platform", "youtube")),
            "web_search": lambda args: self._web_search_tool(args["query"]),
            
            # Enhanced research
            "deep_research": lambda args: self._deep_research_tool(
                args["topic"], 
                args.get("depth", "standard"),
                args.get("focus_areas", [])
            ),
            "open_website": lambda args: self._open_website_tool(args["url"]),
            "get_weather": lambda args: self._get_weather_tool(args["location"]),
            # File operations
            "read_file": lambda args: self._read_file(args["filepath"]),
            "write_file": lambda args: self._write_file(
                args["filepath"], 
                args["content"], 
                args.get("mode", "overwrite")
            ),
            "list_files": lambda args: self._list_files(args.get("directory", ".")),
            
            # Code execution
            "execute_python": lambda args: self._execute_safe_python(args["code"]),
            
            # Memory
            "remember_fact": lambda args: self._remember_fact(args["key"], args["value"]),
            "recall_fact": lambda args: self._recall_fact(args["key"])
        }
    
    def _open_website_tool(self, url: str) -> Dict[str, Any]:
        """Open website in browser"""
        try:
            import webbrowser
            
            # Format URL if needed
            if not url.startswith(('http://', 'https://')):
                if '.' in url:  # Has domain
                    url = f'https://{url}' if not url.startswith('www.') else f'https://{url}'
                else:  # Just name like "youtube"
                    url = f'https://www.{url}.com'
            
            logger.info(f"🌐 Opening website: {url}")
            webbrowser.open(url)
            return {"status": "success", "url": url}
        except Exception as e:
            return {"status": "error", "message": str(e)}
        
    def _get_weather_tool(self, location: str) -> Dict[str, Any]:
        """Get live weather data from wttr.in (free API, no key needed)"""
        try:
            logger.info(f"🌤️  Getting weather for: {location}")
            
            url = f"https://wttr.in/{location}?format=j1"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                current = data['current_condition'][0]
                
                return {
                    "status": "success",
                    "location": location,
                    "temperature_c": current['temp_C'],
                    "temperature_f": current['temp_F'],
                    "condition": current['weatherDesc'][0]['value'],
                    "feels_like_c": current['FeelsLikeC'],
                    "feels_like_f": current['FeelsLikeF'],
                    "humidity": current['humidity'],
                    "wind_kmh": current['windspeedKmph']
                }
            else:
                return {"status": "error", "message": f"Weather data not available for {location}"}
        
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return {"status": "error", "message": str(e)}
    # ========================================================================
    # BASIC TOOL IMPLEMENTATIONS (Original + Enhanced)
    # ========================================================================
    
    def _open_app_tool(self, name: str) -> Dict[str, Any]:
        """
        Production-ready app opening with:
        - State awareness
        - Execution verification
        - Retry logic
        - Self-correction
        """
        if PRODUCTION_MODE and self.production_tools:
            # Use production tool with all features
            result = self.production_tools.open_app(name)
            
            # Track stats
            self.stats["verification_checks"] += 1
            if result.get('method') != 'primary':
                self.stats["retries_attempted"] += 1
            
            return result
        else:
            # Fallback to basic method
            try:
                from src.native_opener import open_app
                open_app(name)
                return {"status": "success", "action": "opened", "target": name}
            except Exception as e:
                return {"status": "error", "message": str(e)}
    
    def _play_youtube_video(self, video_name: str) -> Dict[str, Any]:
        """
        Example: Multi-step YouTube video playing
        Uses production planner
        """
        if not PRODUCTION_MODE:
            # Fallback to basic method
            return self._play_media_tool(video_name, "youtube")
        
        import webbrowser
        import pyautogui
        import time
        from urllib.parse import quote_plus
        
        # Define multi-step plan
        plan = [
            {
                'action': 'open_youtube',
                'execute': lambda p: webbrowser.open('https://www.youtube.com'),
                'params': {},
                'verify': lambda: self.verifier.verify_browser_opened(timeout=3.0),
                'on_failure': 'retry',
                'delay': 2.0
            },
            {
                'action': 'focus_search',
                'execute': lambda p: pyautogui.press('/'),
                'params': {},
                'verify': None,
                'on_failure': 'continue',
                'delay': 0.5
            },
            {
                'action': 'type_search',
                'execute': lambda p: pyautogui.write(p['query'], interval=0.05),
                'params': {'query': video_name},
                'verify': None,
                'on_failure': 'retry',
                'delay': 0.3
            },
            {
                'action': 'submit',
                'execute': lambda p: pyautogui.press('enter'),
                'params': {},
                'verify': None,
                'on_failure': 'continue',
                'delay': 1.0
            },
            {
                'action': 'select_first_video',
                'execute': lambda p: pyautogui.press('tab') or pyautogui.press('enter'),
                'params': {},
                'verify': None,
                'on_failure': 'continue'
            }
        ]
        
        result = self.planner.execute_plan(plan)
        
        # Log for self-correction
        if result['success']:
            self.self_corrector.log_success('play_youtube', video_name, 'multi_step')
            self.stats["self_corrections"] += 1
        
        return {
            "status": "success" if result['success'] else "partial",
            "plan_result": result,
            "message": f"YouTube video plan completed: {result['completed_steps']}/{result['total_steps']} steps"
        }

    def get_system_state(self) -> Dict[str, Any]:
        """Get current system state - NEW"""
        if not PRODUCTION_MODE:
            return {"mode": "basic", "features": "limited"}
        
        self.state_tracker.update_running_apps()
        self.state_tracker.update_open_windows()
        
        return {
            "mode": "production",
            "running_apps": len(self.state_tracker.running_apps),
            "open_windows": len(self.state_tracker.open_windows),
            "recent_actions": len(self.state_tracker.recent_actions),
            "failures_logged": len(self.self_corrector.failure_log),
            "success_patterns": len(self.self_corrector.success_patterns),
            "stats": self.stats
        }

    def _close_app_tool(self, name: str) -> Dict[str, Any]:
        """Close application wrapper"""
        try:
            if name.lower() == "tab":
                close_tab()
                return {"status": "success", "action": "closed", "target": "tab"}
            else:
                close_app(name)
                return {"status": "success", "action": "closed", "target": name}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _play_media_tool(self, name: str, platform: str) -> Dict[str, Any]:
        """Play media wrapper"""
        try:
            play_media(name, platform)
            return {"status": "success", "action": "playing", "media": name, "platform": platform}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _web_search_tool(self, query: str) -> Dict[str, Any]:
        """Quick web search tool"""
        try:
            results = self._perform_web_search(query)
            return {"status": "success", "query": query, "results": results}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ========================================================================
    # DEEP RESEARCH IMPLEMENTATION (FIXED)
    # ========================================================================
    
    def _deep_research_tool(
        self, 
        topic: str, 
        depth: str = "standard",
        focus_areas: List[str] = None
    ) -> Dict[str, Any]:
        """
        Multi-source research with AI synthesis - FIXED VERSION
        
        Fixes:
        - Better JSON parsing with fallbacks
        - Improved error handling
        - More robust synthesis
        """
        try:
            logger.info(f"🔬 Deep Research Started: {topic}")
            self.stats["research_queries"] += 1
            
            # Check cache
            cache_key = hashlib.md5(f"{topic}_{depth}".encode()).hexdigest()
            if cache_key in self._research_cache:
                logger.info("✓ Using cached research")
                self.stats["cache_hits"] += 1
                return self._research_cache[cache_key]
            
            # Generate search queries
            num_queries = {"quick": 2, "standard": 3, "comprehensive": 5}.get(depth, 3)
            search_queries = self._generate_research_queries(topic, focus_areas or [], num_queries)
            logger.info(f"Generated {len(search_queries)} search queries")
            
            # Perform all searches
            all_results = []
            for query in search_queries:
                logger.info(f"  Searching: {query}")
                results = self._perform_web_search(query)
                all_results.append({
                    "query": query,
                    "results": results
                })
                time.sleep(0.5)  # Rate limiting
            
            # Synthesize findings using AI - FIXED
            synthesis = self._synthesize_research_fixed(topic, all_results, depth)
            
            # Cache the result
            self._research_cache[cache_key] = synthesis
            
            # Store in memory for future reference
            self.memory["past_tasks"].append({
                "type": "research",
                "topic": topic,
                "timestamp": datetime.now().isoformat(),
                "key_findings": synthesis.get("key_findings", [])[:3]
            })
            self._save_memory()
            
            logger.info(f"✓ Research completed: {synthesis.get('status', 'unknown')}")
            return synthesis
        
        except Exception as e:
            logger.error(f"Research failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Research failed: {str(e)}",
                "topic": topic
            }
    
    def _generate_research_queries(
        self, 
        topic: str, 
        focus_areas: List[str],
        num_queries: int
    ) -> List[str]:
        """Generate diverse search queries for comprehensive research"""
        queries = [topic]  # Base query
        
        # If focus areas provided, use them
        if focus_areas:
            for area in focus_areas[:num_queries-1]:
                queries.append(f"{topic} {area}")
        else:
            # Auto-generate varied queries
            variations = [
                f"{topic} overview",
                f"{topic} latest developments",
                f"{topic} applications",
                f"{topic} recent advances",
                f"{topic} challenges and solutions"
            ]
            queries.extend(variations[:num_queries-1])
        
        return queries[:num_queries]
    
    def _synthesize_research_fixed(
        self, 
        topic: str, 
        all_results: List[Dict],
        depth: str
    ) -> Dict[str, Any]:
        """
        FIXED: AI-powered synthesis of research findings
        
        Improvements:
        1. Better JSON cleaning (removes code blocks, extra text)
        2. Multiple fallback strategies
        3. More robust parsing
        """
        # Compile all search results
        compiled_results = []
        for result_set in all_results:
            query = result_set["query"]
            results_text = result_set["results"]
            compiled_results.append(f"Query: {query}\n{results_text}\n")
        
        full_research = "\n---\n".join(compiled_results)
        
        # Truncate if too long (Groq token limits)
        max_length = {"quick": 2000, "standard": 4000, "comprehensive": 6000}
        full_research = full_research[:max_length.get(depth, 4000)]
        
        # Synthesis prompt - IMPROVED
        synthesis_prompt = f"""Analyze these search results about "{topic}" and provide structured synthesis.

SEARCH RESULTS:
{full_research}

IMPORTANT: Respond ONLY with valid JSON. No explanatory text before or after.

REQUIRED JSON FORMAT:
{{
    "overview": "2-3 sentence high-level summary of {topic}",
    "key_findings": ["Finding 1", "Finding 2", "Finding 3", "Finding 4", "Finding 5"],
    "trends": ["Trend 1", "Trend 2", "Trend 3"],
    "applications": ["Application 1", "Application 2"],
    "insights": ["Insight 1", "Insight 2"]
}}

Return ONLY the JSON object above. Be factual and cite patterns from sources."""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a research analyst. Return ONLY valid JSON without any markdown formatting or explanatory text."},
                    {"role": "user", "content": synthesis_prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            
            synthesis_text = response.choices[0].message.content.strip()
            
            # IMPROVED JSON CLEANING
            synthesis_text = self._clean_json_response(synthesis_text)
            
            # Parse JSON
            synthesis = json.loads(synthesis_text)
            synthesis["status"] = "success"
            synthesis["topic"] = topic
            synthesis["sources_analyzed"] = len(all_results)
            synthesis["depth"] = depth
            
            return synthesis
        
        except json.JSONDecodeError as e:
            logger.error(f"Synthesis JSON parse failed: {e}")
            logger.error(f"Raw response: {synthesis_text[:200]}...")
            
            # FALLBACK 1: Try to extract JSON from text
            extracted = self._extract_json_from_text(synthesis_text)
            if extracted:
                try:
                    synthesis = json.loads(extracted)
                    synthesis["status"] = "success"
                    synthesis["topic"] = topic
                    synthesis["sources_analyzed"] = len(all_results)
                    synthesis["depth"] = depth
                    logger.info("✓ Recovered JSON from text")
                    return synthesis
                except:
                    pass
            
            # FALLBACK 2: Return structured summary without JSON
            return {
                "status": "partial",
                "topic": topic,
                "overview": f"Research completed on {topic} from {len(all_results)} sources",
                "key_findings": [r["results"][:100] + "..." for r in all_results[:5]],
                "note": "AI synthesis partially successful",
                "sources_analyzed": len(all_results)
            }
        
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return {
                "status": "error",
                "message": str(e),
                "topic": topic
            }
    
    def _clean_json_response(self, text: str) -> str:
        """
        FIXED: Clean JSON response from LLM
        
        Removes:
        - Markdown code blocks (```json, ```)
        - Explanatory text before/after JSON
        - Extra whitespace
        """
        # Remove markdown code blocks
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        
        # Find JSON object (starts with { ends with })
        start = text.find('{')
        end = text.rfind('}')
        
        if start != -1 and end != -1:
            text = text[start:end+1]
        
        return text.strip()
    
    def _extract_json_from_text(self, text: str) -> Optional[str]:
        """
        FIXED: Extract JSON from mixed text response
        
        Uses regex to find JSON structure
        """
        try:
            # Find the first { and last }
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return match.group(0)
        except:
            pass
        return None

    # ========================================================================
    # FILE OPERATIONS (NEW)
    # ========================================================================
    
    def _read_file(self, filepath: str) -> Dict[str, Any]:
        """Read file contents safely"""
        try:
            path = Path(filepath)
            
            if not path.exists():
                return {"status": "error", "message": f"File not found: {filepath}"}
            
            # Check file size
            if path.stat().st_size > 1_000_000:  # 1MB limit
                return {"status": "error", "message": "File too large (max 1MB)"}
            
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            return {
                "status": "success",
                "filepath": str(filepath),
                "content": content,
                "size_bytes": len(content.encode('utf-8')),
                "lines": content.count('\n') + 1
            }
        
        except Exception as e:
            return {"status": "error", "message": f"Read failed: {str(e)}"}
    
    def _write_file(self, filepath: str, content: str, mode: str = "overwrite") -> Dict[str, Any]:
        """Write content to file safely"""
        try:
            path = Path(filepath)
            
            # Create parent directories
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write mode
            write_mode = 'w' if mode == "overwrite" else 'a'
            
            with open(path, write_mode, encoding='utf-8') as f:
                f.write(content)
            
            return {
                "status": "success",
                "filepath": str(filepath),
                "mode": mode,
                "bytes_written": len(content.encode('utf-8'))
            }
        
        except Exception as e:
            return {"status": "error", "message": f"Write failed: {str(e)}"}
    
    def _list_files(self, directory: str = ".") -> Dict[str, Any]:
        """List files in directory"""
        try:
            path = Path(directory)
            
            if not path.exists():
                return {"status": "error", "message": f"Directory not found: {directory}"}
            
            if not path.is_dir():
                return {"status": "error", "message": f"Not a directory: {directory}"}
            
            files = []
            directories = []
            
            for item in path.iterdir():
                if item.is_file():
                    files.append({
                        "name": item.name,
                        "size": item.stat().st_size,
                        "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
                    })
                elif item.is_dir() and not item.name.startswith('.'):
                    directories.append(item.name)
            
            return {
                "status": "success",
                "directory": str(directory),
                "files": sorted(files, key=lambda x: x["name"]),
                "directories": sorted(directories),
                "total_files": len(files),
                "total_dirs": len(directories)
            }
        
        except Exception as e:
            return {"status": "error", "message": f"List failed: {str(e)}"}

    # ========================================================================
    # CODE EXECUTION (NEW)
    # ========================================================================
    
    def _execute_safe_python(self, code: str) -> Dict[str, Any]:
        """Execute Python code in restricted sandbox"""
        try:
            # Whitelist of safe built-ins
            safe_builtins = {
                'abs': abs, 'round': round, 'min': min, 'max': max,
                'sum': sum, 'len': len, 'range': range, 'enumerate': enumerate,
                'pow': pow, 'int': int, 'float': float, 'str': str, 'bool': bool,
                'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
                'sorted': sorted, 'reversed': reversed, 'zip': zip,
                'all': all, 'any': any
            }
            
            # Add math module
            import math
            safe_namespace = {
                '__builtins__': safe_builtins,
                'math': math,
                'pi': math.pi,
                'e': math.e
            }
            
            # Execute code
            result = eval(code, safe_namespace)
            
            return {
                "status": "success",
                "result": result,
                "result_type": type(result).__name__,
                "code_executed": code
            }
        
        except SyntaxError as e:
            return {"status": "error", "message": f"Syntax error: {str(e)}", "code": code}
        except NameError as e:
            return {"status": "error", "message": f"Name error: {str(e)}", "code": code}
        except Exception as e:
            return {"status": "error", "message": f"Execution error: {str(e)}", "code": code}

    # ========================================================================
    # MEMORY OPERATIONS (NEW)
    # ========================================================================
    
    def _remember_fact(self, key: str, value: str) -> Dict[str, Any]:
        """Store fact in persistent memory"""
        try:
            self.memory["facts"][key] = {
                "value": value,
                "timestamp": datetime.now().isoformat()
            }
            self._save_memory()
            
            logger.info(f"Remembered: {key} = {value}")
            return {
                "status": "success",
                "message": f"Stored: {key}",
                "key": key,
                "value": value
            }
        
        except Exception as e:
            return {"status": "error", "message": f"Memory store failed: {str(e)}"}
    
    def _recall_fact(self, key: str) -> Dict[str, Any]:
        """Retrieve fact from memory"""
        fact = self.memory["facts"].get(key)
        
        if fact:
            return {
                "status": "success",
                "key": key,
                "value": fact.get("value", fact) if isinstance(fact, dict) else fact,
                "stored_at": fact.get("timestamp", "unknown") if isinstance(fact, dict) else "unknown"
            }
        else:
            return {
                "status": "not_found",
                "message": f"No memory for: {key}",
                "key": key
            }

    # ========================================================================
    # WEB SEARCH (ENHANCED WITH BETTER CACHING) - FIXED
    # ========================================================================
    
    def _perform_web_search(self, query: str) -> str:
        """
        FIXED: Enhanced web search with better formatting and caching
        
        Fixes:
        - Removed RuntimeWarning (package rename is expected)
        - Better error handling
        """
        logger.info(f"🔍 Web search: {query}")
        
        # Check instance cache
        if query in self._search_cache:
            logger.info("✓ Cache hit")
            self.stats["cache_hits"] += 1
            return self._search_cache[query]
        
        try:
            # FIXED: Suppress the deprecation warning
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                with DDGS(timeout=10) as ddgs:
                    results = list(ddgs.text(query, max_results=3))
            
            if not results:
                return f"No results found for '{query}'"
            
            formatted_results = []
            for i, result in enumerate(results, 1):
                title = result.get('title', 'No title')
                body = result.get('body', 'No description')[:200]
                href = result.get('href', '')
                
                formatted_results.append(
                    f"{i}. {title}\n"
                    f"   {body}...\n"
                    f"   Source: {href}"
                )
            
            result_str = "\n\n".join(formatted_results)
            
            # Cache it
            self._search_cache[query] = result_str
            
            return result_str
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return f"Search error: {str(e)}"

    # ========================================================================
    # CORE AGENT LOGIC (ENHANCED)
    # ========================================================================
    
    def chat(self, user_input: str) -> str:
        """
        Enhanced chat with full agent capabilities
        
        Supports: multi-tool, research, memory, files, code
        """
        # Track statistics
        self.stats["commands_processed"] += 1
        
        # Add context from memory
        context = self._get_relevant_context(user_input)
        enhanced_input = user_input
        if context:
            enhanced_input = f"{user_input}\n[Context: {context}]"
        
        self.history.append({"role": "user", "content": enhanced_input})
        
        try:
            selected_model = ModelSelector.select_model(user_input)
            if selected_model == ModelSelector.FAST_MODEL:
                self.stats["fast_model_calls"] += 1
                self.stats["tokens_saved_estimate"] += 2500
            else:
                self.stats["smart_model_calls"] += 1
            response = self.client.chat.completions.create(
                model=selected_model,
                messages=self.history,
                tools=self.tools,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=500  # Increased for research responses
            )
            
            response_msg = response.choices[0].message
            
            # Handle tool calls
            if response_msg.tool_calls:
                return self._handle_tool_execution(response_msg)
            
            # Regular text response
            ai_text = response_msg.content.strip()
            
            # Only truncate for non-research queries
            if "research" not in user_input.lower() and "analyze" not in user_input.lower():
                if len(ai_text.split()) > 15:
                    ai_text = '. '.join(ai_text.split('. ')[:2]) + '.'
            
            self.history.append({"role": "assistant", "content": ai_text})
            self._trim_history()
            
            return ai_text
        
        except Exception as e:
            logger.error(f"Chat error: {e}")
            import traceback
            traceback.print_exc()
            return "System error, Sir"
    
    def _handle_tool_execution(self, response_msg) -> str:
        """
        Execute tools and synthesize final response
        
        Handles single and multi-tool execution
        """
        self.history.append(response_msg)
        
        tool_results = []
        
        # Execute each tool
        for tool_call in response_msg.tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            # Track usage
            self.stats["tools_used"][func_name] = self.stats["tools_used"].get(func_name, 0) + 1
            
            # Special logging for research
            if func_name == "deep_research":
                logger.info(f"🔬 Starting research: {args.get('topic')}")
            else:
                logger.info(f"🛠️  Tool: {func_name}({list(args.keys())})")
            
            try:
                # Execute tool
                result = self._tool_map[func_name](args)
                
                # Format result
                result_str = json.dumps(result) if isinstance(result, dict) else str(result)
                
                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": result_str
                })
                
                logger.info(f"✓ Tool completed: {func_name}")
                
            except Exception as e:
                logger.error(f"Tool execution failed: {func_name} - {e}")
                import traceback
                traceback.print_exc()
                
                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": json.dumps({"status": "error", "message": str(e)})
                })
        
        # Add tool results to history
        self.history.extend(tool_results)
        
        # Get final synthesized response
        try:
            final_response = self.client.chat.completions.create(
                model=self.model,
                messages=self.history,
                temperature=0.3,
                max_tokens=500
            )
            
            final_text = final_response.choices[0].message.content.strip()
            
            self.history.append({"role": "assistant", "content": final_text})
            self._trim_history()
            
            return final_text
        
        except Exception as e:
            logger.error(f"Final synthesis failed: {e}")
            return "Tools executed, but synthesis failed, Sir"

    # ========================================================================
    # UTILITY METHODS (ENHANCED)
    # ========================================================================
    
    def _trim_history(self) -> None:
        """Trim history to prevent token overflow"""
        if len(self.history) > self.history_max_length:
            self.history = [self.history[0]] + self.history[-(self.history_max_length - 1):]
            logger.debug(f"History trimmed to {len(self.history)} messages")

    # ========================================================================
    # LEGACY COMPATIBILITY (Keep your existing methods)
    # ========================================================================

    def generate_jarvis_response(self, query: str, intent_data: dict = None) -> str:
        """Generate Jarvis-style response - routes to enhanced chat()"""
        try:
            # Quick responses for common queries
            query_lower = query.lower()
            
            if "time" in query_lower and "?" in query_lower:
                current_time = time.strftime("%I:%M %p")
                return f"It's {current_time}, Sir"
            
            if "date" in query_lower and "?" in query_lower:
                current_date = time.strftime("%B %d, %Y")
                return f"Today is {current_date}, Sir"
            
            if query_lower in ["how are you", "how are you?"]:
                return "All systems operational, Sir"
            
            # Route to enhanced chat for everything else
            return self.chat(query)
            
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            return "Data unavailable, Sir"

    def understand_intent(self, user_command: str) -> Optional[Dict[str, Any]]:
        """Legacy method - routes to enhanced chat()"""
        try:
            # Let enhanced chat handle it
            response = self.chat(user_command)
            
            return {
                "status": "success",
                "response": response,
                "source": "enhanced_agent"
            }
        
        except Exception as e:
            logger.error(f"Intent understanding error: {e}")
            return None
    
    def _call_tools(self, tool_calls: List[Any]) -> List[Dict[str, str]]:
        """Legacy method - kept for backward compatibility"""
        return self._handle_tool_execution(tool_calls)
    
    def clear_history(self) -> None:
        """Clear conversation history (keep memory)"""
        self.history = [self.history[0]]
        logger.info("History cleared (memory preserved)")
    
    def get_context_summary(self) -> str:
        """Get summary of current conversation context"""
        user_messages = [msg["content"] for msg in self.history if msg["role"] == "user"]
        return f"Recent: {', '.join(user_messages[-3:])}"
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get usage statistics - NEW"""
        return {
            **self.stats,
            "memory_facts": len(self.memory.get("facts", {})),
            "cached_searches": len(self._search_cache),
            "cached_research": len(self._research_cache),
            "history_length": len(self.history)
        }