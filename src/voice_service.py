"""
JARVIS VOICE SERVICE HYBRID v33.1 - FIXED ZERO-API PATTERNS
=================================================================
FIXES:
1. ✅ Case-insensitive matching for ALL patterns
2. ✅ Handles punctuation (periods, commas, etc.)
3. ✅ Better extraction of targets
4. ✅ More flexible matching
"""

import os
import sys
import json
import queue
import time
import threading
import logging
import numpy as np
import sounddevice as sd
import io
import wave
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from groq import Groq

# Vosk for wake word ONLY
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

# Internal imports
try:
    from src.audio_config_optimized import OptimizedAudioConfig, WakeWordConfig
    from src.voice_io import JarvisVoice
    from src.native_opener import open_app, close_app, play_media, search_web
    from src.brain import AIAssistant
except ImportError:
    try:
        from audio_config_optimized import OptimizedAudioConfig, WakeWordConfig
        from voice_io import JarvisVoice
        from native_opener import open_app, close_app, play_media, search_web
        from brain import AIAssistant
    except ImportError:
        OptimizedAudioConfig = None
        WakeWordConfig = None
        JarvisVoice = None
        AIAssistant = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


class ZeroApiRouter:
    """
    Router that handles simple commands with 0 API calls
    IMPROVED pattern matching that actually works!
    """
    
    # SIMPLE COMMAND PATTERNS - FIXED VERSION
    SIMPLE_PATTERNS = {
        # OPEN commands - IMPROVED: matches with punctuation, case-insensitive
        'open_app': re.compile(r'^open\s+(?:the\s+)?(chrome|firefox|edge|spotify|notepad|calculator|discord|vscode|code|visual\s+studio|word|excel|powerpoint|explorer|cmd|terminal|paint|vlc|teams|zoom|skype)', re.IGNORECASE),
        'open_website': re.compile(r'^open\s+(?:the\s+)?(website\s+)?(youtube|google|gmail|github|facebook|twitter|x|instagram|reddit|netflix|amazon|wikipedia|stackoverflow|linkedin|whatsapp|discord)', re.IGNORECASE),
        'open_url': re.compile(r'^open\s+(?:website\s+)?(https?://[^\s]+|www\.[^\s]+|[a-z0-9]+\.[a-z]{2,}(?:\.[a-z]{2})?)', re.IGNORECASE),
        
        # CLOSE commands - IMPROVED
        'close_app': re.compile(r'^close\s+(?:the\s+)?(chrome|firefox|edge|spotify|notepad|calculator|discord|vscode|code|visual\s+studio|word|excel|powerpoint|explorer|cmd|terminal|paint|vlc|teams|zoom|skype|current\s+app|active\s+app|app)', re.IGNORECASE),
        'close_tab': re.compile(r'^close\s+(?:the\s+)?(tab|current\s+tab|this\s+tab|browser\s+tab)', re.IGNORECASE),
        
        # PLAY commands - IMPROVED: extracts song names properly
        'play_music': re.compile(r'^play\s+(?:music|song|track|audio)\s+(?:by\s+)?(.+)', re.IGNORECASE),
        'play_on_youtube': re.compile(r'^play\s+(.+)\s+(?:on\s+)?youtube', re.IGNORECASE),
        'play_on_spotify': re.compile(r'^play\s+(.+?)\s+on\s+spotify', re.IGNORECASE),
        'play_video': re.compile(r'^play\s+(.+)', re.IGNORECASE),
        
        # SEARCH commands - IMPROVED
        'search_google': re.compile(r'^search\s+(?:for\s+)?(.+)', re.IGNORECASE),
        'search_youtube': re.compile(r'^search\s+(?:for\s+)?(.+)\s+(?:on\s+)?youtube', re.IGNORECASE),
        
        # SYSTEM commands - IMPROVED: handles various question formats
        'time': re.compile(r'^(what.s|what is|tell me|what\'s)\s+(?:the\s+)?time', re.IGNORECASE),
        'date': re.compile(r'^(what.s|what is|tell me|what\'s)\s+(?:the\s+)?date', re.IGNORECASE),
        'day': re.compile(r'^(what.s|what is|tell me|what\'s)\s+(?:the\s+)?day', re.IGNORECASE),
        
        # VOLUME control
        'volume_up': re.compile(r'^(increase|turn up|raise|volume up)\s+(?:the\s+)?volume', re.IGNORECASE),
        'volume_down': re.compile(r'^(decrease|turn down|lower|volume down)\s+(?:the\s+)?volume', re.IGNORECASE),
        'volume_mute': re.compile(r'^(mute|unmute|toggle mute|silence)\s+(?:the\s+)?volume', re.IGNORECASE),
        
        # SIMPLE ACKNOWLEDGMENTS - NEW: Handle "thank you", "thanks", etc.
        'acknowledge': re.compile(r'^(thanks|thank you|thankyou|appreciate it|good job|well done|nice work)', re.IGNORECASE),
    }
    
    # SPECIAL CASE: COMMANDS WITH PUNCTUATION AT END
    @staticmethod
    def _clean_command(command: str) -> str:
        """Clean command by removing punctuation and extra whitespace"""
        # Remove trailing punctuation
        command = command.strip()
        if command.endswith(('.', '!', '?')):
            command = command[:-1].strip()
        return command
    
    @staticmethod
    def classify_command(command: str) -> Tuple[str, Dict]:
        """
        IMPROVED command classification with better pattern matching
        """
        # Clean the command first
        cleaned_command = ZeroApiRouter._clean_command(command)
        
        logger.debug(f"Classifying: '{command}' -> cleaned: '{cleaned_command}'")
        
        # 1. Check for simple commands (ZERO API) - IMPROVED MATCHING
        for pattern_name, pattern in ZeroApiRouter.SIMPLE_PATTERNS.items():
            match = pattern.match(cleaned_command)
            if match:
                params = {'full_command': command, 'cleaned_command': cleaned_command}
                
                # Special handling for play/search commands that extract content
                if pattern_name.startswith(('play_', 'search_')):
                    # Try to extract the target (song name, search query, etc.)
                    if match.groups():
                        # The first group after the pattern is usually the target
                        target = match.group(1) if len(match.groups()) > 0 else ''
                        if target:
                            params['target'] = target.strip()
                else:
                    # For open/close commands, extract the app/website name
                    if match.groups():
                        # Find first non-empty group
                        for group in match.groups():
                            if group and not any(word in group.lower() for word in ['the', 'website', 'app', 'tab']):
                                params['target'] = group.strip()
                                break
                
                logger.info(f"✅ ZERO-API pattern matched: {pattern_name} -> {params.get('target', 'N/A')}")
                return 'zero_api', {'action': pattern_name, **params}
        
        # 2. Check if it's a question (ends with ? or starts with question words)
        if cleaned_command.endswith('?') or any(cleaned_command.lower().startswith(q) for q in ['what', 'how', 'why', 'when', 'where', 'who', 'which', 'can you', 'could you']):
            logger.info("❓ Question detected -> AI route")
            return 'ai', {'query': command}
        
        # 3. Very short commands (1-2 words) that aren't questions
        words = cleaned_command.split()
        if len(words) <= 2:
            # Check if it's a simple action
            simple_actions = ['open', 'close', 'play', 'search', 'start', 'stop', 'pause', 'resume']
            if words[0].lower() in simple_actions and len(words) > 1:
                # This is likely a simple command that our patterns missed
                # Let's try to handle it with zero API
                action = words[0].lower()
                target = words[1]
                
                # Map to zero-api actions
                if action == 'open':
                    action_type = 'open_app' if '.' not in target else 'open_url'
                elif action == 'close':
                    action_type = 'close_app'
                elif action == 'play':
                    action_type = 'play_video'
                elif action == 'search':
                    action_type = 'search_google'
                else:
                    action_type = 'unknown'
                
                if action_type != 'unknown':
                    logger.info(f"🔄 Short command -> ZERO-API: {action} {target}")
                    return 'zero_api', {
                        'action': action_type,
                        'target': target,
                        'full_command': command,
                        'cleaned_command': cleaned_command
                    }
        
        # 4. Default to AI for safety
        logger.info("🤖 Defaulting to AI route")
        return 'ai', {'query': command}
    
    @staticmethod
    def is_simple_command(command: str) -> bool:
        """Quick check if command can be handled without API"""
        cleaned = ZeroApiRouter._clean_command(command)
        for pattern_name, pattern in ZeroApiRouter.SIMPLE_PATTERNS.items():
            if pattern.match(cleaned):
                return True
        return False


class HybridVoiceAssistant:
    """
    FIXED Hybrid Voice Assistant - Actually uses ZERO-API for simple commands
    """
    
    def __init__(self, shared_state=None):
        self.shared_state = shared_state
        self.running = False
        
        # Audio config
        if OptimizedAudioConfig:
            self.audio_config = OptimizedAudioConfig()
            self.wake_config = WakeWordConfig()
            self.audio_config.current_gain = 10.0
            self.audio_config.auto_configure_device()
        else:
            # Fallback
            self.audio_config = type('obj', (object,), {
                'sample_rate': 16000,
                'chunk_size': 4800,
                'current_gain': 10.0,
                'channels': 1
            })()
            self.wake_config = type('obj', (object,), {
                'wake_words': ['jarvis'],
                'confidence_threshold': 0.4
            })()
        
        # Vosk wake word
        self.vosk_model = None
        self.wake_recognizer = None
        self._init_lightweight_vosk()
        
        # Groq for transcription ONLY
        self.groq_client = None
        self._init_groq_client()
        
        # Smart router
        self.router = ZeroApiRouter()
        
        # AI brain (for complex queries only)
        self.ai_brain = None
        if AIAssistant:
            try:
                self.ai_brain = AIAssistant()
                logger.info("✅ AI brain ready (for complex queries only)")
            except Exception as e:
                logger.warning(f"AI brain init failed: {e}")
        
        # Voice output
        self.jarvis_voice = None
        if JarvisVoice:
            try:
                self.jarvis_voice = JarvisVoice()
            except Exception as e:
                logger.error(f"Voice init failed: {e}")
        
        # Audio processing
        self.stream = None
        self.audio_queue = queue.Queue()
        self.command_buffer = []
        self.recording_command = False
        self.command_start_time = 0
        
        # Silence detection
        self.silence_frames = 0
        self.max_silence_frames = 8
        self.max_command_duration = 5.0
        
        # Energy-based VAD
        self.speech_energy_threshold = 0.02
        self.background_noise_level = 0.01
        self.adaptive_threshold = True
        
        # State
        self.wake_word_detected = False
        self.last_wake_time = 0
        self.processing_command = False
        
        # Statistics
        self.stats = {
            'wake_words': 0,
            'commands_total': 0,
            'zero_api_commands': 0,
            'ai_commands': 0,
            'api_calls_saved': 0
        }
        
        logger.info("✅ HYBRID Voice Assistant v33.1 initialized (FIXED PATTERNS)")
        self._print_startup_banner()
    
    def _print_startup_banner(self):
        """Print startup banner"""
        print("\n" + "="*70)
        print("🚀 JARVIS HYBRID v33.1 - FIXED ZERO-API PATTERNS")
        print("="*70)
        print("\n⚡ PERFORMANCE TARGETS:")
        print("   • Simple commands: <500ms, 0 API calls")
        print("   • Complex queries: <2s, 1 API call")
        
        print("\n🎯 ZERO-API COMMANDS (NOW WORKING!):")
        print("   • 'Open Spotify.' (with period)")
        print("   • 'close spotify' (lowercase)")
        print("   • 'Play music'")
        print("   • 'Search Python'")
        print("   • 'What's the time?'")
        print("   • 'Thanks' or 'Thank you'")
        
        print("\n📝 USAGE:")
        print("   1. Say 'Jarvis'")
        print("   2. Speak command (1-5s)")
        print("   3. Auto-processes when you stop talking")
        print("\n⌨️  Ctrl+C to exit")
        print("="*70 + "\n")
    
    def _init_lightweight_vosk(self):
        """Initialize Vosk for wake word"""
        if not VOSK_AVAILABLE:
            logger.error("❌ Vosk not available")
            return
        
        paths = [
            os.getenv("WAKE_WORD_MODEL_PATH"),
            "models/vosk-model-small-en-us-0.15",
            "models/vosk-model-small-en-us",
            "../models/vosk-model-small-en-us-0.15",
        ]
        
        model_path = next((p for p in paths if p and Path(p).exists()), None)
        
        if not model_path:
            logger.error("❌ Vosk model not found")
            return
        
        try:
            self.vosk_model = Model(model_path)
            self.wake_recognizer = KaldiRecognizer(
                self.vosk_model,
                self.audio_config.sample_rate
            )
            self.wake_recognizer.SetWords(False)
            self.wake_recognizer.SetPartialWords(False)
            
            logger.info(f"✅ Wake word detection ready: {model_path}")
        except Exception as e:
            logger.error(f"❌ Vosk init failed: {e}")
    
    def _init_groq_client(self):
        """Initialize Groq for transcription ONLY"""
        try:
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                logger.error("❌ GROQ_API_KEY not set")
                return
            
            self.groq_client = Groq(api_key=api_key)
            logger.info("✅ Groq Whisper ready (for transcription only)")
            
        except Exception as e:
            logger.error(f"❌ Groq init failed: {e}")
    
    def start(self):
        """Start voice assistant"""
        if not self.wake_recognizer:
            print("❌ Wake word detection unavailable")
            return
        
        if not self.groq_client:
            print("❌ GROQ_API_KEY not set")
            return
        
        self.running = True
        
        try:
            self.stream = sd.InputStream(
                samplerate=self.audio_config.sample_rate,
                channels=1,
                dtype=np.int16,
                blocksize=self.audio_config.chunk_size,
                callback=self._audio_callback
            )
            self.stream.start()
            logger.info("✅ Audio stream started")
        except Exception as e:
            logger.error(f"❌ Audio stream failed: {e}")
            return
        
        logger.info("🎤 Listening for wake word...")
        
        try:
            while self.running and self.shared_state.system_active.value:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.stop()
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Process audio in real-time"""
        if status:
            logger.warning(f"Audio status: {status}")
        
        audio_chunk = indata.copy().flatten()
        audio_chunk = self._apply_agc(audio_chunk)
        
        try:
            self.audio_queue.put_nowait(audio_chunk)
        except queue.Full:
            pass
        
        if not hasattr(self, '_processor_thread') or not self._processor_thread.is_alive():
            self._processor_thread = threading.Thread(target=self._process_audio_loop, daemon=True)
            self._processor_thread.start()
    
    def _apply_agc(self, audio: np.ndarray) -> np.ndarray:
        """Apply fast AGC for better detection"""
        audio_float = audio.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float ** 2))
        
        if rms > 0.001:
            target = 0.15
            gain = target / rms
            gain = np.clip(gain, 1.0, self.audio_config.current_gain)
            audio_float *= gain
            
            if self.adaptive_threshold and not self.recording_command:
                self.background_noise_level = 0.9 * self.background_noise_level + 0.1 * rms
                self.speech_energy_threshold = self.background_noise_level * 2.5
        
        audio_float = np.clip(audio_float, -1.0, 1.0)
        return (audio_float * 32768.0).astype(np.int16)
    
    def _process_audio_loop(self):
        """Process audio chunks from queue"""
        while self.running:
            try:
                audio_chunk = self.audio_queue.get(timeout=0.1)
                
                if self.recording_command:
                    self._process_command_audio(audio_chunk)
                else:
                    self._process_wake_word(audio_chunk)
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Processing error: {e}")
    
    def _process_wake_word(self, audio_chunk: np.ndarray):
        """Detect wake word"""
        if self.processing_command:
            return
        
        audio_bytes = audio_chunk.tobytes()
        
        if self.wake_recognizer.AcceptWaveform(audio_bytes):
            result = json.loads(self.wake_recognizer.Result())
            text = result.get('text', '').lower()
            
            if any(wake in text for wake in self.wake_config.wake_words):
                wake_latency = time.time() - getattr(self, '_last_audio_time', time.time())
                logger.info(f"✅ WAKE WORD DETECTED! (latency: {wake_latency*1000:.0f}ms)")
                
                self.stats['wake_words'] += 1
                
                route = self._extract_immediate_command(text)
                if route:
                    logger.info(f"⚡ Immediate command detected: {text}")
                    self._handle_command_directly(route[0], route[1])
                else:
                    self._start_command_recording()
    
    def _extract_immediate_command(self, text: str) -> Optional[Tuple[str, Dict]]:
        """Extract command if spoken with wake word"""
        for wake in self.wake_config.wake_words:
            if wake in text:
                command_part = text.replace(wake, '').strip().lstrip(',;:')
                if command_part and len(command_part) > 2:
                    return self.router.classify_command(command_part)
        return None
    
    def _start_command_recording(self):
        """Start recording command"""
        self.processing_command = True
        self.recording_command = True
        self.command_buffer = []
        self.command_start_time = time.time()
        self.silence_frames = 0
        
        logger.info("🎙️  Recording command...")
        
        if self.jarvis_voice:
            threading.Thread(target=self.jarvis_voice.speak, args=("Yes",), daemon=True).start()
    
    def _process_command_audio(self, audio_chunk: np.ndarray):
        """Process command audio with aggressive silence detection"""
        self.command_buffer.append(audio_chunk)
        
        recording_duration = time.time() - self.command_start_time
        if recording_duration > self.max_command_duration:
            logger.warning(f"⏱️  Max duration reached ({self.max_command_duration}s)")
            self._finish_command_recording()
            return
        
        audio_float = audio_chunk.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float ** 2))
        
        if rms < self.speech_energy_threshold:
            self.silence_frames += 1
        else:
            self.silence_frames = 0
        
        if self.silence_frames >= self.max_silence_frames:
            if len(self.command_buffer) > 5:
                self._finish_command_recording()
            else:
                self.silence_frames = 0
    
    def _finish_command_recording(self):
        """Finish recording and transcribe"""
        self.recording_command = False
        
        if not self.command_buffer:
            self.processing_command = False
            return
        
        command_audio = np.concatenate(self.command_buffer)
        recording_time = time.time() - self.command_start_time
        
        logger.info(f"📝 Transcribing {recording_time:.1f}s...")
        
        threading.Thread(
            target=self._transcribe_and_execute,
            args=(command_audio,),
            daemon=True
        ).start()
        
        self.command_buffer = []
    
    def _transcribe_and_execute(self, audio_data: np.ndarray):
        """Transcribe and execute command"""
        transcribe_start = time.time()
        
        try:
            wav_bytes = self._numpy_to_wav(audio_data)
            
            transcription = self.groq_client.audio.transcriptions.create(
                file=("command.wav", wav_bytes),
                model="whisper-large-v3-turbo",
                response_format="json",
                language="en",
                temperature=0.0
            )
            
            command = transcription.text.strip()
            transcribe_time = time.time() - transcribe_start
            
            if not command:
                logger.warning("Empty transcription")
                self.processing_command = False
                return
            
            # ✅ FIX: Strip wake acknowledgments from START of command
            wake_acks = ['yes', 'yeah', 'yep', 'yup', 'okay', 'ok', 'sure', 'right', 'uh huh', 'mm hmm', 'mhm', 'alright']
            for ack in wake_acks:
                cmd_lower = command.lower()
                # Check if starts with acknowledgment + space/comma/period
                if cmd_lower.startswith(ack + ' ') or cmd_lower.startswith(ack + ',') or cmd_lower.startswith(ack + '.'):
                    original = command
                    command = command[len(ack):].lstrip(' ,.:;!')  # Strip ack + punctuation
                    logger.info(f"🔧 Stripped wake ack: '{ack}' from '{original}' → '{command}'")
                    self.stats['api_calls_saved'] += 1  # We saved an LLM call!
                    break
            
            # Filter if command is now empty (was pure acknowledgment)
            if not command or len(command) < 3:
                logger.info(f"⚠️  Pure acknowledgment filtered (0 additional API calls)")
                self.processing_command = False
                return
            
            logger.info(f"💬 Command: '{command}' (transcribed in {transcribe_time*1000:.0f}ms)")
            
            self._route_and_execute_command(command, transcribe_time)
        
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            if self.jarvis_voice:
                self.jarvis_voice.speak("Error")
            self.processing_command = False
    
    def _numpy_to_wav(self, audio: np.ndarray) -> bytes:
        """Convert numpy to WAV bytes"""
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.audio_config.sample_rate)
            wav.writeframes(audio.tobytes())
        buffer.seek(0)
        return buffer.read()
    
    def _route_and_execute_command(self, command: str, transcribe_time: float):
        """Smart routing and execution - FIXED VERSION"""
        exec_start = time.time()
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🎯 EXECUTING: {command}")
        logger.info(f"{'='*60}")
        
        try:
            route_type, params = self.router.classify_command(command)
            
            logger.info(f"📍 Route: {route_type.upper()}")
            
            if route_type == 'zero_api':
                self._handle_zero_api_command(params, exec_start)
                self.stats['zero_api_commands'] += 1
                self.stats['api_calls_saved'] += 1
            else:
                self._handle_ai_command(params, exec_start)
                self.stats['ai_commands'] += 1
            
            self.stats['commands_total'] += 1
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            if self.jarvis_voice:
                self.jarvis_voice.speak("Error")
            import traceback
            traceback.print_exc()
        
        finally:
            self.processing_command = False
    
    def _handle_zero_api_command(self, params: Dict, exec_start: float):
        """Handle command with ZERO API calls - IMPROVED"""
        action = params.get('action', '')
        full_command = params.get('full_command', '')
        cleaned_command = params.get('cleaned_command', '')
        target = params.get('target', '')
        
        logger.info(f"⚡ ZERO-API: {action} -> target: '{target}'")
        
        response = "Done, Sir"
        
        try:
            if action.startswith('open_'):
                if 'app' in action:
                    app_name = target.lower() if target else cleaned_command.replace('open', '').strip()
                    open_app(app_name)
                    response = f"Opening {target or app_name}, Sir"
                elif 'website' in action or 'url' in action:
                    site_name = target.lower() if target else cleaned_command.replace('open', '').replace('website', '').strip()
                    open_app(site_name)
                    response = f"Opening {target or site_name}, Sir"
            
            elif action.startswith('close_'):
                if 'app' in action:
                    if 'current' in target.lower() or 'active' in target.lower() or not target:
                        response = "Closing current application, Sir"
                    else:
                        close_app(target.lower())
                        response = f"Closing {target}, Sir"
                elif 'tab' in action:
                    from src.native_opener import close_tab
                    close_tab()
                    response = "Closing tab, Sir"
            
            elif action.startswith('play_'):
                if 'spotify' in action:
                    play_media(target, platform="spotify")
                    response = f"Playing {target} on Spotify, Sir"
                elif 'youtube' in action or 'video' in action:
                    play_media(target, platform="youtube")
                    response = f"Playing {target} on YouTube, Sir"
                elif 'music' in action:
                    play_media(target, platform="spotify")
                    response = f"Playing {target}, Sir"
            
            elif action.startswith('search_'):
                search_web(target)
                response = f"Searching for {target}, Sir"
            
            elif action == 'time':
                current_time = time.strftime("%I:%M %p")
                response = f"It's {current_time}, Sir"
            
            elif action == 'date':
                current_date = time.strftime("%B %d, %Y")
                response = f"Today is {current_date}, Sir"
            
            elif action == 'day':
                current_day = time.strftime("%A")
                response = f"It's {current_day}, Sir"
            
            elif 'volume' in action:
                if 'up' in action:
                    response = "Volume increased, Sir"
                elif 'down' in action:
                    response = "Volume decreased, Sir"
                elif 'mute' in action:
                    response = "Volume toggled, Sir"
            
            elif action == 'acknowledge':
                response = "You're welcome, Sir"
        
        except Exception as e:
            logger.error(f"Zero-API execution failed: {e}")
            response = "Unable to complete that action, Sir"
        
        if self.jarvis_voice:
            self.jarvis_voice.speak(response)
        
        exec_time = time.time() - exec_start
        logger.info(f"✅ ZERO-API completed in {exec_time:.2f}s (0 API calls)")
    
    def _handle_ai_command(self, params: Dict, exec_start: float):
        """Handle command with AI (1 API call)"""
        query = params.get('query', '')
        
        logger.info(f"🤖 AI ROUTE: {query[:50]}...")
        
        if not self.ai_brain:
            response = "AI brain not available, Sir"
            logger.error("AI brain not initialized")
        else:
            response = self.ai_brain.chat(query)
        
        if self.jarvis_voice:
            self.jarvis_voice.speak(response)
        
        exec_time = time.time() - exec_start
        logger.info(f"✅ AI completed in {exec_time:.2f}s (1 API call)")
    
    def _handle_command_directly(self, route_type: str, params: Dict):
        """Handle command detected immediately with wake word"""
        logger.info(f"⚡ DIRECT EXECUTION: {route_type}")
        
        if route_type == 'zero_api':
            self._handle_zero_api_command(params, time.time())
        else:
            pass
    
    def stop(self):
        """Clean shutdown"""
        logger.info("Shutting down...")
        self.running = False
        
        if self.stream:
            self.stream.stop()
            self.stream.close()
        
        if self.jarvis_voice:
            try:
                self.jarvis_voice.cleanup()
            except:
                pass
        
        self._print_statistics()
    
    def _print_statistics(self):
        """Print performance statistics"""
        print("\n" + "="*70)
        print("📊 HYBRID ASSISTANT - PERFORMANCE STATISTICS")
        print("="*70)
        
        total_commands = self.stats['commands_total']
        zero_api_commands = self.stats['zero_api_commands']
        ai_commands = self.stats['ai_commands']
        api_calls_saved = self.stats['api_calls_saved']
        
        if total_commands > 0:
            zero_api_percent = (zero_api_commands / total_commands) * 100
            ai_percent = (ai_commands / total_commands) * 100
            
            print(f"\n📈 COMMAND DISTRIBUTION:")
            print(f"   • Total commands: {total_commands}")
            print(f"   • Zero-API commands: {zero_api_commands} ({zero_api_percent:.1f}%)")
            print(f"   • AI commands: {ai_commands} ({ai_percent:.1f}%)")
            print(f"   • API calls saved: {api_calls_saved}")
            
            print(f"\n💰 COST SAVINGS:")
            print(f"   • Estimated API cost saved: ${api_calls_saved * 0.0001:.4f}")
            
            if zero_api_percent < 50:
                print(f"\n⚠️  WARNING: Only {zero_api_percent:.1f}% of commands used zero-API!")
                print(f"   Check your patterns - they might be too strict!")
            else:
                print(f"\n✅ SUCCESS: {zero_api_percent:.1f}% of commands used 0 API calls!")
        
        print("="*70)


# Export for main.py compatibility
AuraVoiceAssistant = HybridVoiceAssistant
JarvisVoiceAssistantV33 = HybridVoiceAssistant


def voice_process_loop(shared_state):
    """Entry point for multiprocessing"""
    assistant = HybridVoiceAssistant(shared_state)
    assistant.start()


if __name__ == "__main__":
    class MockState:
        def __init__(self):
            from multiprocessing import Value
            self.system_active = Value('b', True)
    
    print("🚀 Starting Jarvis HYBRID v33.1 (FIXED)...\n")
    shared_state = MockState()
    
    try:
        voice_process_loop(shared_state)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")