"""
Aura Voice Service (V21.1 - GROQ LLAMA OPTIMIZED)
Features: Llama 3 integration, Groq cloud speech-to-text, enhanced accuracy
"""
import sys
import os
import json
import queue
import time
import threading
import numpy as np
import sounddevice as sd
from groq import Groq
from difflib import SequenceMatcher
from pathlib import Path
import logging
import random
from .voice_io import JarvisVoice
from .config import VOICE_CONFIG, GROQ_CONFIG, ASR_VOCABULARY
from .intent_parser import parse_intent, validate_intent
from .native_opener import execute_intent, REGISTRY
from .brain import AIAssistant

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AuraVoiceAssistant:
    """Production voice assistant with Llama 3 brain integration"""
    
    def __init__(self, shared_state):
        """Initialize voice assistant - COMPLETE FIXED VERSION"""
        self.shared_state = shared_state
        self.config = VOICE_CONFIG
        
        # Initialize Groq client for speech-to-text
        self.groq_client = None
        self._init_groq_client()
        
        # Initialize Llama Brain
        self.llama_brain = None
        self._init_llama_brain()
        
        # Initialize Jarvis Voice
        self.jarvis_voice = JarvisVoice(voice="en-US-ChristopherNeural")
        
        # Audio queue
        self.audio_queue = queue.Queue(maxsize=3)
        
        # 🔥 FIX: Initialize ALL missing variables
        self.current_gain = 1.0
        self.pending_confirmation = None
        self.last_command_time = 0
        self.command_cooldown = 0.3
        
        # Wake word state
        self.wake_word_detected = False  # 🔥 Changed to False so it waits for wake word
        self.wake_word_time = 0
        self.listening_for_command = False  # 🔥 Changed to False initially
        
        # Noise filtering
        self.silence_frames = 0
        self.speech_detected = False
        self.rms_history = []
        self.adaptive_threshold = self.config.min_speech_energy
        self.background_noise_level = 0.0
        self.speech_start_time = 0
        
        # Audio buffer for Groq STT
        self.audio_buffer = []
        self.max_buffer_duration = 15  # Max seconds of audio to buffer
        self.min_buffer_duration = 0.5  # Min seconds before transcribing
        
        # Stats
        self.commands_processed = 0
        self.wake_word_detections = 0
        self.stt_requests = 0
        
        # State management
        self.running = True
        self.current_command = None
        
        # Conversation flow
        self.awaiting_confirmation = False
        self.pending_action = None
        
        # Audio stream
        self.stream = None
        
        print(f"[Aura] Voice Assistant initialized with Llama Brain")
        print(f"[Aura] Ready! Say '{self.config.wake_word}' to activate.")
    
    def _init_groq_client(self):
        """Initialize Groq client for speech-to-text"""
        try:
            api_key = os.getenv("GROQ_API_KEY") or GROQ_CONFIG.get("api_key")
            if not api_key:
                logger.error("GROQ_API_KEY not found in environment or config")
                sys.exit(1)
            
            self.groq_client = Groq(api_key=api_key)
            logger.info("✓ Groq client initialized for speech-to-text")
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
            sys.exit(1)
    
    def _init_llama_brain(self):
        """Initialize Llama 3 brain for natural language understanding"""
        try:
            self.llama_brain = AIAssistant(  # Use your existing AIAssistant
                model="llama-3.3-70b-versatile"
            )
            logger.info("✓ AI Brain initialized")
        except Exception as e:
            logger.error(f"Failed to initialize AI Brain: {e}")
    
    def _fuzzy_match_wake_word(self, text: str) -> tuple:
        """
        Check if text contains wake word with fuzzy matching.
        Returns: (matched, wake_word_used, command_text, confidence)
        """
        text_lower = text.lower().strip()
        
        best_match = None
        best_confidence = 0.0
        
        for wake_word in self.config.wake_words:
            wake_word_lower = wake_word.lower()
            
            # Method 1: Exact match or starts with
            if text_lower.startswith(wake_word_lower):
                command_text = text[len(wake_word):].strip()
                return True, wake_word, command_text, 1.0
            
            # Method 2: Check first N words
            text_words = text_lower.split()
            wake_words = wake_word_lower.split()
            
            if len(text_words) >= len(wake_words):
                first_n_words = " ".join(text_words[:len(wake_words)])
                similarity = SequenceMatcher(None, first_n_words, wake_word_lower).ratio()
                
                if similarity >= self.config.wake_word_confidence:
                    if similarity > best_confidence:
                        best_confidence = similarity
                        command_text = " ".join(text_words[len(wake_words):])
                        best_match = (True, wake_word, command_text, similarity)
            
            # Method 3: Wake word appears anywhere
            if wake_word_lower in text_lower:
                parts = text_lower.split(wake_word_lower, 1)
                if len(parts) == 2 and len(parts[1].strip()) > 0:
                    command_text = parts[1].strip()
                    confidence = 0.85
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = (True, wake_word, command_text, confidence)
        
        if best_match:
            return best_match
        
        return False, None, None, 0.0
    
    def process_audio(self, indata):
        """Process audio with AGC and noise filtering"""
        # Convert to float32
        audio = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
        
        # Calculate RMS
        rms = np.sqrt(np.mean(audio**2))
        
        # Update RMS history
        self.rms_history.append(rms)
        if len(self.rms_history) > 100:
            self.rms_history.pop(0)
        
        # Adaptive threshold
        if len(self.rms_history) >= 20:
            sorted_rms = sorted(self.rms_history)
            self.background_noise_level = sorted_rms[len(sorted_rms) // 4]
            self.adaptive_threshold = max(
                self.background_noise_level * 2.5,
                self.config.min_speech_energy
            )
        
        # Only process if above threshold OR listening
        if rms < self.adaptive_threshold and not self.listening_for_command:
            self.silence_frames += 1
            if not self.speech_detected or self.silence_frames > self.config.silence_threshold:
                return np.zeros_like(audio).astype(np.int16).tobytes()
        else:
            if not self.speech_detected:
                self.speech_start_time = time.time()
            self.speech_detected = True
            self.silence_frames = 0
        
        # Apply AGC
        if self.config.agc_enabled and rms > 10:
            target_rms = 8000.0
            target_gain = target_rms / rms
            target_gain = np.clip(target_gain, 0.5, 50.0)
            self.current_gain = 0.9 * self.current_gain + 0.1 * target_gain
            audio = audio * self.current_gain
        
        # Clip
        audio = np.clip(audio, -32768, 32767)
        
        return audio.astype(np.int16).tobytes()
    
    def audio_callback(self, indata, frames, time_info, status):
        """Audio stream callback with buffer for Groq STT - FIXED"""
        try:
            processed = self.process_audio(indata)
            
            # Buffer audio for Groq transcription
            if self.speech_detected or self.listening_for_command:
                self.audio_buffer.append(processed)
                
                # Check buffer size
                buffer_duration = len(self.audio_buffer) * (2400 / self.config.sample_rate)
                if buffer_duration > self.max_buffer_duration:
                    self.audio_buffer = self.audio_buffer[-int(self.max_buffer_duration * self.config.sample_rate / 2400):]
            
            # 🔥 FIX: Use time module properly
            import time as time_module  # Import with alias to avoid collision
            current_time = time_module.time()
            
            # THROTTLE LOGGING
            if not hasattr(self, 'last_queue_log_time'):
                self.last_queue_log_time = 0
                self.queue_operations = 0
            
            self.queue_operations += 1
            
            if current_time - self.last_queue_log_time > 10:
                logger.debug(f"Processed {self.queue_operations} audio queue operations")
                self.last_queue_log_time = current_time
                self.queue_operations = 0
            
            if self.audio_queue.full():
                try:
                    self.audio_queue.get_nowait()
                except:
                    pass
            
            self.audio_queue.put(processed, block=False)
            
        except Exception as e:
            if not hasattr(self, 'last_error_log_time'):
                self.last_error_log_time = 0
            
            import time as time_module
            current_time = time_module.time()
            if current_time - self.last_error_log_time > 5:
                logger.error(f"Audio callback error: {e}")
                self.last_error_log_time = current_time
    
    def _transcribe_with_groq(self) -> str:
        """Transcribe buffered audio using Groq API (in-memory WAV)"""
        # ADD THIS CHECK AT THE BEGINNING
        # # if not self.stt_enabled:
        #     # Only log occasionally to reduce noise
        #     # import random
        #     if random.random() < 0.01:  # Log only 1% of non-wake audio
        #         logger.debug(f"STT disabled, ignoring audio buffer ({len(self.audio_buffer)} chunks)")
        #     self.audio_buffer = []
        #     return ""
        
        if not self.audio_buffer:
            return ""
        
        try:
            # Combine audio chunks
            combined_audio = b"".join(self.audio_buffer)
            audio_data = np.frombuffer(combined_audio, dtype=np.int16)
            
            if audio_data.size == 0:
                return ""
            
            import io
            import soundfile as sf
            
            buffer = io.BytesIO()
            
            # Write WAV to memory
            sf.write(
                buffer,
                audio_data,
                self.config.sample_rate,
                format="WAV",
                subtype="PCM_16",
            )
            buffer.seek(0)
            
            duration = audio_data.size / self.config.sample_rate
            self.stt_requests += 1
            logger.info(f"Transcribing audio ({duration:.1f}s)...")
            
            transcription = self.groq_client.audio.transcriptions.create(
                file=("audio.wav", buffer.read(), "audio/wav"),
                model="whisper-large-v3-turbo",
                response_format="json",
                language="en",
            )
            
            text = transcription.text.strip()
            logger.info(f"Transcribed: {text}")
            
            self.audio_buffer.clear()
            return text
            
        except Exception as e:
            logger.error(f"Groq transcription failed: {e}")
            return ""
    
    def _process_with_llama_brain(self, text: str) -> dict:
        """Process natural language command with Llama 3 brain - FIXED"""
        try:
            if self.llama_brain:
                logger.info(f"Processing with Llama Brain: {text}")
                intent = self.llama_brain.understand_intent(text)
                
                # ✅ FIX: Check 'status' field instead of 'confidence'
                if intent and intent.get("status") == "success":
                    logger.info(f"✓ Llama Brain succeeded")
                    
                    # Check if it's a conversational response (research, questions, etc.)
                    if "response" in intent:
                        # Return the response directly
                        return {
                            "status": "success",
                            "intent": {
                                "action": "conversation",
                                "response": intent["response"],
                                "confidence": 1.0,
                                "parameters": {}
                            },
                            "source": "llama_brain"
                        }
                    else:
                        # Legacy action-based response
                        return {
                            "status": "success",
                            "intent": intent,
                            "source": "llama_brain"
                        }
                else:
                    logger.warning(f"Brain returned status: {intent.get('status', 'unknown')}")
            
            # Fallback to traditional parsing
            logger.info(f"Using traditional parser for: {text}")
            parsed_intent = parse_intent(text)
            
            if validate_intent(parsed_intent):
                return {
                    "status": "success",
                    "intent": parsed_intent.to_dict(),
                    "source": "traditional_parser"
                }
            else:
                return {
                    "status": "error",
                    "message": "Could not understand command",
                    "source": "traditional_parser"
                }
                
        except Exception as e:
            logger.error(f"Intent processing failed: {e}")
            return {
                "status": "error",
                "message": f"Processing error: {str(e)}",
                "source": "error"
            }
    
    def start(self):
        """Start voice recognition loop with Groq STT"""
        print(f"[Aura] Listening for wake word: {self.config.wake_word}")
        print(f"[Aura] Using Groq cloud speech-to-text")
        
        threshold_reported = False
        last_threshold_log_time = 0
        threshold_log_interval = 30  # Log threshold every 30 seconds max
        try:
            # Open audio stream
            self.stream = sd.RawInputStream(
                samplerate=self.config.sample_rate,
                blocksize=2400,  # ~150ms latency
                dtype='int16',
                channels=1,
                callback=self.audio_callback
            )
            
            self.stream.start()
            
            # Main recognition loop
            while self.running and self.shared_state.system_active.value:
                try:
                    data = self.audio_queue.get(timeout=0.1)
                    
                    # Report threshold once
                    current_time = time.time()
                    if not threshold_reported and len(self.rms_history) >= 30:
                        print(f"[Aura] Noise threshold: {self.adaptive_threshold:.1f}")
                        print(f"[Aura] Ready! Waiting for wake word...")
                        threshold_reported = True
                        last_threshold_log_time = current_time
                    # Periodically log threshold (throttled)
                    elif threshold_reported and (current_time - last_threshold_log_time > threshold_log_interval):
                        if len(self.rms_history) >= 10:
                            logger.debug(f"Current noise threshold: {self.adaptive_threshold:.1f}, Background: {self.background_noise_level:.1f}")
                            last_threshold_log_time = current_time
                    # Check for speech completion
                    if self.speech_detected and self.silence_frames > self.config.silence_threshold:
                        buffer_duration = len(self.audio_buffer) * (2400 / self.config.sample_rate)
                        
                        if buffer_duration >= self.min_buffer_duration:
                            # Transcribe audio with Groq
                            text = self._transcribe_with_groq()
                            
                            if text:
                                self._handle_transcription(text)
                        
                        self.speech_detected = False
                        self.audio_buffer = []
                    
                    # Check timeout for command listening
                    if self.listening_for_command:
                        elapsed = time.time() - self.wake_word_time
                        if elapsed > self.config.wake_word_timeout:
                            print("[Aura] Command timeout.")
                            self.listening_for_command = False
                            self.audio_buffer = []
                
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Recognition error: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Audio stream error: {e}")
        
        finally:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except:
                    pass
    
    
    def _handle_transcription(self, text: str):
        """Handle transcribed text with FIXED command extraction"""
        try:
            # Filter short utterances
            if len(text) < 2:
                return
            
            # Wake word detection
            matched, wake_word_used, command_text, confidence = self._fuzzy_match_wake_word(text)
            
            if matched:
                self.wake_word_detections += 1
                self.wake_word_time = time.time()
                self.listening_for_command = True
                
                print(f"[WAKE] '{wake_word_used}' detected!")
                
                # 🔥 FIX: Clean up command text (remove leading punctuation)
                if command_text:
                    command_text = command_text.strip().lstrip('.,!?;: ')  # Remove leading punctuation
                    
                    if not command_text:
                        # Just wake word - greet and wait
                        self._jarvis_greet()
                    else:
                        # Immediate command - process it
                        print(f"[COMMAND] Immediate: {command_text}")
                        self._process_command_flow(command_text)
                else:
                    # Just "Jarvis" - greet and wait for command
                    self._jarvis_greet()
            
            elif self.listening_for_command:
                # Command after wake word
                text = text.strip().lstrip('.,!?;: ')  # Clean this too
                if text:
                    self._process_command_flow(text)
            
            # Handle confirmation responses
            elif self.awaiting_confirmation:
                self._handle_confirmation_flow(text)
        
        except Exception as e:
            logger.error(f"Handle transcription error: {e}")
            import traceback
            traceback.print_exc()
    
    def _jarvis_greet(self):
        """Jarvis greeting for wake word only"""
        import random
        greetings = [
            "Yes, Sir?",
            "At your service",
            "System online",
            "Ready, Sir"
        ]
        greeting = random.choice(greetings)
        self.jarvis_voice.speak(greeting)

    def _process_command_flow(self, command_text: str):
        """Process command with optimal flow"""
        print(f"[COMMAND] {command_text}")
        
        # Determine command type for appropriate acknowledgment
        command_type = self._get_command_type(command_text)
        
        # Acknowledge immediately (non-blocking)
        self.jarvis_voice.speak_acknowledgment(command_type)
        
        # Process in background
        threading.Thread(
            target=self._process_and_execute,
            args=(command_text, command_type),
            daemon=True
        ).start()
    
    def _get_command_type(self, command_text: str) -> str:
        """Determine command type for appropriate acknowledgment"""
        text = command_text.lower()
        
        if any(word in text for word in ["open", "launch", "start"]):
            return "open"
        elif any(word in text for word in ["close", "exit", "quit", "kill"]):
            return "close"
        elif "play" in text:
            return "play"
        elif any(word in text for word in ["search", "find", "google", "look up"]):
            return "search"
        elif any(word in text for word in ["what", "how", "why", "when", "where", "who", "explain"]):
            return "question"
        return "general"
    
    # In voice_service.py - Replace _process_and_execute method

    def _process_and_execute(self, command_text: str, command_type: str):
        """Background processing of command - FULLY FIXED VERSION"""
        try:
            if self.llama_brain:
                try:
                    logger.info(f"Processing with Llama Brain: {command_text}")
                    intent = self.llama_brain.understand_intent(command_text)
                    
                    # ✅ FIX: Check for 'status' field instead of 'confidence'
                    if intent and intent.get("status") == "success":
                        logger.info(f"✓ Llama Brain succeeded")
                        
                        # For research/conversation responses
                        if "response" in intent:
                            # Speak the brain's response directly
                            response_text = intent["response"]
                            logger.info(f"Brain response: {response_text[:100]}...")
                            self.jarvis_voice.speak(response_text)
                            
                            # ✅ RETURN EARLY - conversation is complete, don't execute as action
                            logger.info("✓ Conversation handled, skipping action execution")
                            return
                        else:
                            # Legacy action format - will be executed below
                            intent_result = {
                                "status": "success",
                                "intent": intent,
                                "source": "llama_brain"
                            }
                    else:
                        logger.warning(f"Brain returned non-success: {intent}")
                        raise ValueError("Brain failed")
                
                except Exception as e:
                    logger.warning(f"Llama Brain failed, using fallback: {e}")
                    from .intent_parser import parse_intent, validate_intent
                    parsed_intent = parse_intent(command_text)
                    
                    if validate_intent(parsed_intent):
                        intent_result = {
                            "status": "success",
                            "intent": parsed_intent.to_dict(),
                            "source": "traditional_parser"
                        }
                    else:
                        intent_result = self._simple_intent_match(command_text)
            else:
                logger.info(f"No Llama Brain, using parsers")
                from .intent_parser import parse_intent, validate_intent
                parsed_intent = parse_intent(command_text)
                
                if validate_intent(parsed_intent):
                    intent_result = {
                        "status": "success",
                        "intent": parsed_intent.to_dict(),
                        "source": "traditional_parser"
                    }
                else:
                    intent_result = self._simple_intent_match(command_text)
            
            # Execute based on result (only for non-conversation actions)
            if intent_result["status"] == "success":
                intent_data = intent_result["intent"]
                self.current_command = command_text
                
                logger.info(f"Executing via {intent_result['source']}: {intent_data}")
                
                # Execute based on type
                if command_type == "question":
                    self._handle_conversation(intent_data, command_text)
                else:
                    self._execute_action(intent_data, command_text)
            else:
                logger.error(f"Intent processing failed: {intent_result}")
                self.jarvis_voice.speak("Command not recognized, Sir")
        
        except Exception as e:
            logger.error(f"Process and execute error: {e}")
            import traceback
            traceback.print_exc()
            self.jarvis_voice.speak("Processing error, Sir")


    def _simple_intent_match(self, command_text: str) -> dict:
        """
        Simple pattern matching fallback when both Llama and traditional parser fail
        """
        text_lower = command_text.lower()
        
        # Open commands
        if any(word in text_lower for word in ["open", "launch", "start"]):
            for word in text_lower.split():
                if word not in ["open", "launch", "start", "the", "app", "application"]:
                    return {
                        "status": "success",
                        "intent": {
                            "action": "open",
                            "target": word,
                            "confidence": 0.7,
                            "parameters": {}
                        },
                        "source": "simple_match"
                    }
        
        # Close commands
        if any(word in text_lower for word in ["close", "exit", "quit"]):
            for word in text_lower.split():
                if word not in ["close", "exit", "quit", "the", "app", "application"]:
                    return {
                        "status": "success",
                        "intent": {
                            "action": "close",
                            "target": word,
                            "confidence": 0.7,
                            "parameters": {}
                        },
                        "source": "simple_match"
                    }
        
        # Play commands
        if "play" in text_lower:
            # Extract song name (everything after "play")
            parts = text_lower.split("play", 1)
            if len(parts) > 1:
                song = parts[1].strip()
                # Remove "on spotify/youtube"
                for platform in ["on spotify", "on youtube", "on amazon"]:
                    if platform in song:
                        song = song.replace(platform, "").strip()
                        platform_name = platform.replace("on ", "")
                        break
                else:
                    platform_name = "youtube"
                
                return {
                    "status": "success",
                    "intent": {
                        "action": "play",
                        "target": song,
                        "confidence": 0.7,
                        "parameters": {"platform": platform_name}
                    },
                    "source": "simple_match"
                }
        
        # Search commands
        if any(word in text_lower for word in ["search", "find", "google", "look up"]):
            # Extract search query
            for keyword in ["search", "find", "google", "look up"]:
                if keyword in text_lower:
                    parts = text_lower.split(keyword, 1)
                    if len(parts) > 1:
                        query = parts[1].strip()
                        return {
                            "status": "success",
                            "intent": {
                                "action": "search",
                                "target": query,
                                "confidence": 0.7,
                                "parameters": {}
                            },
                            "source": "simple_match"
                        }
        
        # If nothing matched
        return {
            "status": "error",
            "message": "Could not parse command",
            "source": "simple_match"
        }
    
    def _handle_conversation(self, intent_data: dict, original_text: str):
        """Handle conversational query"""
        try:
            # Get Jarvis response from brain
            if self.llama_brain:
                jarvis_response = self.llama_brain.generate_jarvis_response(original_text, intent_data)
                
                if jarvis_response:
                    self.jarvis_voice.speak(jarvis_response)
                else:
                    # Fallback to regular chat
                    ai_response = self.llama_brain.chat(original_text)
                    if ai_response:
                        self.jarvis_voice.speak(ai_response)
                    else:
                        self.jarvis_voice.speak("No data available, Sir")
        
        except Exception as e:
            logger.error(f"Conversation error: {e}")
            self.jarvis_voice.speak("Database error")
    
    def _execute_action(self, intent_data: dict, original_text: str):
        """Execute action command - FIXED"""
        try:
            # ✅ Extract the actual intent from the wrapper
            if "intent" in intent_data and isinstance(intent_data["intent"], dict):
                actual_intent = intent_data["intent"]
            else:
                actual_intent = intent_data
            
            result = execute_intent(actual_intent)  # ✅ Now passes correct structure!
            
            report = self._generate_action_report(original_text, result)
            if report:
                self.jarvis_voice.speak(report)
            
            self.commands_processed += 1
        
        except Exception as e:
            logger.error(f"Execute action error: {e}")
            import traceback
            traceback.print_exc()
            self.jarvis_voice.speak("Execution failed, Sir")
    
    def _generate_action_report(self, command: str, result: dict) -> str:
        """Generate concise action report"""
        status = result.get("status", "")
        message = result.get("message", "")
        
        if status == "success":
            # Extract target from command
            words = command.lower().split()
            if len(words) > 1:
                target = words[-1]
            else:
                target = "task"
            
            # Simple success messages
            if "open" in command.lower():
                return f"{target} opened, Sir"
            elif "close" in command.lower():
                return f"{target} closed, Sir"
            elif "play" in command.lower():
                return "Playing, Sir"
            elif "search" in command.lower():
                return "Search complete, Sir"
            elif "type" in command.lower():
                return "Text entered, Sir"
            else:
                return "Completed, Sir"
        
        elif status == "error":
            # Brief error messages
            if "not found" in message.lower():
                return "Target not found, Sir"
            elif "failed" in message.lower():
                return "Action failed, Sir"
            else:
                return "Unable to comply, Sir"
        
        return ""
    
    def _handle_confirmation_flow(self, text: str):
        """Handle confirmation responses"""
        text_lower = text.lower()
        
        if any(word in text_lower for word in ["yes", "confirm", "do it", "proceed", "affirmative"]):
            self.jarvis_voice.speak("Confirmed, Sir")
            if self.pending_action:
                threading.Thread(
                    target=self._execute_action,
                    args=(self.pending_action, self.current_command),
                    daemon=True
                ).start()
        
        elif any(word in text_lower for word in ["no", "cancel", "abort", "stop", "negative"]):
            self.jarvis_voice.speak("Cancelled, Sir")
        
        self.awaiting_confirmation = False
        self.pending_action = None
    
    def stop(self):
        """Stop the assistant"""
        self.running = False
        self.jarvis_voice.speak("System shutting down")
        self.jarvis_voice.wait_until_done()
        self.jarvis_voice.cleanup()
        
        print(f"\n[JARVIS] System offline")
        print(f"[STATS] Commands executed: {self.commands_processed}")
    
    def _fuzzy_match(self, text: str, word_list: list) -> bool:
        """Check if text fuzzy matches any word in list"""
        text = text.lower().strip()
        for word in word_list:
            ratio = SequenceMatcher(None, text, word).ratio()
            if ratio >= 0.75:
                return True
            if word in text or text in word:
                return True
        return False
    
    def _process_command(self, command_text: str):
        """Process and execute command using Llama Brain or fallback"""
        try:
            # Check cooldown
            current_time = time.time()
            if current_time - self.last_command_time < self.command_cooldown:
                return
            
            self.last_command_time = current_time
            
            # Process with Llama Brain
            result = self._process_with_llama_brain(command_text)
            
            if result["status"] == "success":
                intent_data = result["intent"]
                logger.info(f"✓ Intent understood via {result['source']}")
                
                # Check if confirmation required
                if self._requires_confirmation(intent_data):
                    self.pending_confirmation = intent_data
                    action = intent_data.get("action", "action").title()
                    print(f"[CONFIRM] {action} - Say 'confirm' or 'cancel'")
                    return
                
                # Execute command
                threading.Thread(
                    target=self._execute_command_async,
                    args=(intent_data,),
                    daemon=True
                ).start()
            else:
                print(f"[Aura] I didn't understand: {result.get('message', 'Unknown error')}")
        
        except Exception as e:
            logger.error(f"Process command error: {e}")
    
    def _execute_command_async(self, intent_data: dict):
        """Execute command asynchronously"""
        try:
            # 🔥 DIRECT CALL - Import execute_intent at top of file!
            # Make sure you have: from .native_opener import execute_intent
            
            result = execute_intent(intent_data)  # ✅ This is the correct call
            
            if result["status"] == "success":
                print(f"[✓] {result['message']}")
            else:
                print(f"[✗] {result['message']}")
            
            self.commands_processed += 1
            
        except Exception as e:
            logger.error(f"Execute command error: {e}")
            import traceback
            traceback.print_exc()
            print(f"[✗] Command execution failed")
    
    def _requires_confirmation(self, intent_data: dict) -> bool:
        """Check if command requires confirmation"""
        if not self.config.confirm_destructive:
            return False
        
        action = intent_data.get("action", "").lower()
        return action in self.config.critical_actions
    
    def _handle_confirmation(self, text: str):
        """Handle confirmation response"""
        try:
            text_lower = text.lower()
            
            if self._fuzzy_match(text_lower, self.config.fuzzy_confirm_words):
                print(f"[Aura] Executing...")
                threading.Thread(
                    target=self._execute_command_async,
                    args=(self.pending_confirmation,),
                    daemon=True
                ).start()
                self.pending_confirmation = None
            
            elif self._fuzzy_match(text_lower, self.config.fuzzy_cancel_words):
                print("[Aura] Cancelled.")
                self.pending_confirmation = None
            
            else:
                print("[Aura] Say 'confirm' or 'cancel'")
        
        except Exception as e:
            logger.error(f"Handle confirmation error: {e}")
            self.pending_confirmation = None
    
    def stop(self):
        """Stop the assistant"""
        self.running = False
        self.stt_enabled = False  # Reset when stopping
        print(f"\n[Aura] Voice Assistant stopped.")
        print(f"[Stats] Commands: {self.commands_processed}")
        print(f"[Stats] Wake words: {self.wake_word_detections}")
        print(f"[Stats] STT requests: {self.stt_requests}")

def voice_process_loop(shared_state):
    """Main entry point for voice service"""
    assistant = AuraVoiceAssistant(shared_state)
    
    try:
        assistant.start()
    except KeyboardInterrupt:
        print("\n[Aura] Shutdown requested")
    except Exception as e:
        logger.error(f"Voice service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        assistant.stop()