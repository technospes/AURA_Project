"""
COGNITIVE AGENT v38.0 - DIRECT EXECUTION FIX
=============================================
✅ Bypasses complex native_opener 
✅ Directly opens URLs for media
✅ Actually works!
"""

import os
import logging
import time
import re
import webbrowser
import pyautogui
from typing import Dict, Any, List, Optional
from enum import Enum
from groq import Groq
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

# Try to import native functions but have fallbacks
try:
    from .native_opener import open_app, close_app
except:
    def open_app(name): 
        import subprocess
        print(f"[ACTION] Opening {name}")
        subprocess.Popen(f'start {name}', shell=True)
    
    def close_app(name): 
        import subprocess
        print(f"[ACTION] Closing {name}")
        subprocess.run(f'taskkill /F /IM {name}.exe', shell=True, capture_output=True)

logger = logging.getLogger(__name__)


class EmotionType(Enum):
    CALM = "calm"
    FRUSTRATED = "frustrated"
    IMPATIENT = "impatient"
    CURIOUS = "curious"


class CompleteCognitiveAgent:
    """
    v38 - DIRECT EXECUTION
    
    Instead of relying on native_opener's complex intent system,
    this directly opens URLs for media playback
    """
    
    def __init__(self, groq_api_key: str = None):
        self.client = Groq(api_key=groq_api_key or os.getenv("GROQ_API_KEY"))
        
        self.light_model = "llama-3.1-8b-instant"
        self.heavy_model = "llama-3.3-70b-versatile"
        
        self.patterns = self._init_patterns()
        self.context = []
        self.max_context = 3
        self.weather_api_key = os.getenv("OPENWEATHER_API_KEY", "")
        
        logger.info("🧠 Complete Cognitive Agent v38 (DIRECT EXECUTION) initialized")
    
    def _init_patterns(self) -> Dict:
        return {
            'open_app': re.compile(r'\b(open|launch|start)\s+(\w+)', re.I),
            'close_app': re.compile(r'\b(close|quit|exit|kill|stop)\s+(\w+)', re.I),
            'play_media': re.compile(r'(?:yes\s+)?play\s+(.+?)\s+on\s+(\w+)', re.I),
            'play_simple': re.compile(r'(?:yes\s+)?play\s+(.+)', re.I),
            'search_web': re.compile(r'\b(search|google|find)\s+(.+)', re.I),
            'close_tab': re.compile(r'\b(close|shut)\s+(this\s+)?tab', re.I),
            'new_tab': re.compile(r'\b(open|new)\s+(a\s+)?tab', re.I),
            'type_command': re.compile(r'(?:yes\s+)?(?:type|write)\s+(.+)', re.I),
            'research': re.compile(r'\b(research|find out|look up|investigate)\s+', re.I),
            'weather': re.compile(r'\bweather\s+(?:in|at|for)?\s*(.+)', re.I),
            'compare': re.compile(r'\bcompare\s+(.+)\s+(and|vs|versus)\s+(.+)', re.I),
        }
    
    def process_command(self, command: str, voice_features: Dict = None) -> Dict[str, Any]:
        """Process command with YES filtering"""
        start_time = time.time()
        
        # Strip "yes" from beginning
        command_clean = re.sub(r'^yes[\s,.:;!?]*', '', command, flags=re.I).strip()
        
        if not command_clean or len(command_clean) < 2:
            logger.warning(f"Empty command after stripping 'yes': '{command}'")
            return {
                'type': 'error',
                'response': "I didn't catch that",
                'sentiment': 'calm'
            }
        
        logger.info(f"📝 Processing: '{command_clean}' (original: '{command}')")
        command_lower = command_clean.lower().strip()
        
        # Pattern matching with DIRECT execution
        result = self._try_pattern_match(command_lower, command_clean)
        if result:
            logger.info(f"⚡ Pattern match ({time.time() - start_time:.3f}s)")
            return result
        
        # Complexity assessment
        complexity = self._assess_complexity(command_lower)
        
        if complexity == 'research':
            return self._handle_research(command_clean)
        elif complexity == 'weather':
            return self._handle_weather(command_clean)
        elif complexity == 'complex':
            return self._handle_complex_query(command_clean)
        else:
            return self._handle_simple_query(command_clean)
    
    def _assess_complexity(self, command: str) -> str:
        if self.patterns['research'].search(command):
            return 'research'
        if self.patterns['weather'].search(command):
            return 'weather'
        
        complex_words = ['explain', 'analyze', 'why', 'how does', 'difference between']
        if any(word in command for word in complex_words):
            return 'complex'
        
        return 'simple'
    
    def _try_pattern_match(self, command: str, original: str) -> Optional[Dict]:
        """Pattern matching with DIRECT execution (no native_opener complexity)"""
        
        # TYPE
        match = self.patterns['type_command'].search(command)
        if match:
            text_to_type = match.group(1).strip()
            logger.info(f"⌨️  EXECUTING TYPE: '{text_to_type}'")
            
            try:
                time.sleep(0.3)
                pyautogui.write(text_to_type, interval=0.03)
                logger.info("✅ Typed successfully")
                
                return {
                    'type': 'success',
                    'response': f"Typed: {text_to_type}",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Type error: {e}")
                return {
                    'type': 'error',
                    'response': "Couldn't type text",
                    'sentiment': 'calm'
                }
        
        # PLAY MEDIA (specific platform) - DIRECT EXECUTION
        match = self.patterns['play_media'].search(command)
        if match:
            media, platform = match.groups()
            media = media.strip()
            platform = platform.strip().lower()
            
            logger.info(f"▶️  EXECUTING PLAY: '{media}' on '{platform}'")
            
            try:
                # DIRECT URL opening based on platform
                if 'youtube' in platform:
                    url = f"https://www.youtube.com/results?search_query={quote_plus(media)}"
                    logger.info(f"🔗 Opening YouTube: {url}")
                    webbrowser.open(url)
                
                elif 'spotify' in platform:
                    url = f"https://open.spotify.com/search/{quote_plus(media)}"
                    logger.info(f"🔗 Opening Spotify: {url}")
                    webbrowser.open(url)
                
                else:
                    # Default to YouTube
                    url = f"https://www.youtube.com/results?search_query={quote_plus(media)}"
                    logger.info(f"🔗 Opening YouTube (default): {url}")
                    webbrowser.open(url)
                
                logger.info("✅ Media opened successfully")
                
                return {
                    'type': 'success',
                    'response': f"Playing {media} on {platform}",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Play error: {e}")
                return {
                    'type': 'error',
                    'response': f"Couldn't play {media}",
                    'sentiment': 'calm'
                }
        
        # PLAY MEDIA (simple - default to YouTube) - DIRECT EXECUTION
        match = self.patterns['play_simple'].search(command)
        if match:
            media = match.group(1).strip()
            
            # Skip context words
            if media.lower() in ['it', 'that', 'this', 'again']:
                return None
            
            logger.info(f"▶️  EXECUTING PLAY (YouTube default): '{media}'")
            
            try:
                url = f"https://www.youtube.com/results?search_query={quote_plus(media)}"
                logger.info(f"🔗 Opening: {url}")
                webbrowser.open(url)
                logger.info("✅ YouTube opened successfully")
                
                return {
                    'type': 'success',
                    'response': f"Playing {media} on YouTube",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Play error: {e}")
                return {
                    'type': 'error',
                    'response': f"Couldn't play {media}",
                    'sentiment': 'calm'
                }
        
        # OPEN APP
        match = self.patterns['open_app'].search(command)
        if match:
            app_name = match.group(2).strip()
            logger.info(f"🟢 EXECUTING OPEN: '{app_name}'")
            
            try:
                open_app(app_name)
                logger.info("✅ App opened")
                
                return {
                    'type': 'success',
                    'response': f"Opening {app_name}",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Open error: {e}")
                return {
                    'type': 'error',
                    'response': f"Couldn't open {app_name}",
                    'sentiment': 'calm'
                }
        
        # CLOSE APP
        match = self.patterns['close_app'].search(command)
        if match:
            app_name = match.group(2).strip()
            logger.info(f"🔴 EXECUTING CLOSE: '{app_name}'")
            
            try:
                close_app(app_name)
                
                # Also taskkill
                import subprocess
                subprocess.run(
                    f'taskkill /F /IM "{app_name}.exe" /T',
                    shell=True,
                    capture_output=True,
                    timeout=2
                )
                logger.info("✅ App closed")
                
                return {
                    'type': 'success',
                    'response': f"Closing {app_name}",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"⚠️  Close error: {e}")
                return {
                    'type': 'success',
                    'response': f"Attempted to close {app_name}",
                    'sentiment': 'calm'
                }
        
        # SEARCH WEB - DIRECT
        match = self.patterns['search_web'].search(command)
        if match:
            query = match.group(2).strip()
            logger.info(f"🔍 EXECUTING SEARCH: '{query}'")
            
            try:
                url = f"https://www.google.com/search?q={quote_plus(query)}"
                logger.info(f"🔗 Opening: {url}")
                webbrowser.open(url)
                logger.info("✅ Search opened")
                
                return {
                    'type': 'success',
                    'response': f"Searching for {query}",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Search error: {e}")
                return {
                    'type': 'error',
                    'response': "Couldn't search",
                    'sentiment': 'calm'
                }
        
        # CLOSE TAB
        if self.patterns['close_tab'].search(command):
            logger.info("🔴 EXECUTING CLOSE TAB")
            
            try:
                pyautogui.hotkey('ctrl', 'w')
                logger.info("✅ Tab closed")
                
                return {
                    'type': 'success',
                    'response': "Closing tab",
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ Close tab error: {e}")
                return {
                    'type': 'success',
                    'response': "Attempted to close tab",
                    'sentiment': 'calm'
                }
        
        # NEW TAB
        if self.patterns['new_tab'].search(command):
            logger.info("🟢 EXECUTING NEW TAB")
            
            try:
                webbrowser.open('about:blank')
                logger.info("✅ New tab opened")
                
                return {
                    'type': 'success',
                    'response': 'Opening new tab',
                    'sentiment': 'calm'
                }
            except Exception as e:
                logger.error(f"❌ New tab error: {e}")
                return {
                    'type': 'error',
                    'response': "Couldn't open new tab",
                    'sentiment': 'calm'
                }
        
        return None
    
    def _handle_weather(self, command: str) -> Dict:
        """Weather with API or search fallback"""
        match = self.patterns['weather'].search(command)
        if not match:
            return self._handle_simple_query(command)
        
        location = match.group(1).strip() if match.group(1) else "current location"
        location = re.sub(r'\b(today|tomorrow|now)\b', '', location, flags=re.I).strip()
        
        logger.info(f"🌤️  Weather for: {location}")
        
        try:
            if self.weather_api_key:
                url = "http://api.openweathermap.org/data/2.5/weather"
                params = {'q': location, 'appid': self.weather_api_key, 'units': 'metric'}
                response = requests.get(url, params=params, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    temp = data['main']['temp']
                    desc = data['weather'][0]['description']
                    
                    return {
                        'type': 'success',
                        'response': f"In {location}: {temp}°C, {desc}",
                        'sentiment': 'calm'
                    }
            
            # Fallback to search
            url = f"https://www.google.com/search?q=weather+in+{quote_plus(location)}"
            webbrowser.open(url)
            
            return {
                'type': 'success',
                'response': f"Opened weather for {location}",
                'sentiment': 'calm'
            }
        
        except Exception as e:
            logger.error(f"Weather error: {e}")
            return {
                'type': 'error',
                'response': f"Couldn't fetch weather",
                'sentiment': 'calm'
            }
    
    def _handle_research(self, command: str) -> Dict:
        """Research with web scraping"""
        logger.info(f"🔬 Research: {command}")
        
        try:
            query = re.sub(r'\b(research|find out|look up)\s+', '', command, flags=re.I).strip()
            
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=2))
            
            if not results:
                return self._handle_simple_query(command)
            
            contents = []
            for result in results[:2]:
                try:
                    resp = requests.get(result['href'], timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    for tag in soup(['script', 'style']):
                        tag.decompose()
                    
                    text = soup.get_text(separator=' ', strip=True)
                    text = re.sub(r'\s+', ' ', text)[:2000]
                    contents.append(text)
                except:
                    pass
            
            if not contents:
                return self._handle_simple_query(command)
            
            combined = "\n\n".join(contents)
            
            response = self.client.chat.completions.create(
                model=self.heavy_model,
                messages=[{"role": "user", "content": f"Based on these sources, answer briefly (max 3 sentences):\n\nQuestion: {query}\n\nSources:\n{combined[:3000]}\n\nAnswer:"}],
                max_tokens=150,
                temperature=0.3,
                timeout=10
            )
            
            answer = response.choices[0].message.content.strip()
            
            return {
                'type': 'success',
                'response': answer,
                'sentiment': 'calm'
            }
        
        except Exception as e:
            logger.error(f"Research error: {e}")
            return self._handle_simple_query(command)
    
    def _handle_complex_query(self, command: str) -> Dict:
        """Complex query with 70B"""
        logger.info("💡 Complex query (70B)")
        
        try:
            response = self.client.chat.completions.create(
                model=self.heavy_model,
                messages=[{"role": "user", "content": f"{command}\n\nAnswer briefly (2-3 sentences):"}],
                max_tokens=150,
                temperature=0.4,
                timeout=10
            )
            
            return {
                'type': 'success',
                'response': response.choices[0].message.content.strip(),
                'sentiment': 'calm'
            }
        
        except Exception as e:
            logger.error(f"Error: {e}")
            return self._handle_simple_query(command)
    
    def _handle_simple_query(self, command: str) -> Dict:
        """Simple query with 8B"""
        logger.info("💡 Simple query (8B)")
        
        try:
            response = self.client.chat.completions.create(
                model=self.light_model,
                messages=[{"role": "user", "content": f"{command}\n\nAnswer briefly (1-2 sentences):"}],
                max_tokens=100,
                temperature=0.3,
                timeout=5
            )
            
            return {
                'type': 'success',
                'response': response.choices[0].message.content.strip(),
                'sentiment': 'calm'
            }
        
        except Exception as e:
            logger.error(f"Error: {e}")
            return {
                'type': 'error',
                'response': "I'm having trouble processing that",
                'sentiment': 'calm'
            }
    
    def get_statistics(self) -> Dict:
        return {
            'commands_processed': len(self.context),
            'context_size': len(self.context)
        }


# Alias
CognitiveAgent = CompleteCognitiveAgent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    agent = CompleteCognitiveAgent()
    
    tests = [
        "yes play weekend on youtube",
        "yes play starboy on spotify",
        "yes type hello world",
    ]
    
    print("\n🧪 TESTING v38 - DIRECT EXECUTION\n")
    
    for cmd in tests:
        print(f"\n>>> {cmd}")
        result = agent.process_command(cmd)
        print(f"<<< {result['response']}")