"""
Aura Voice Service (V21.0 - PRODUCTION OPTIMIZED)
Features: Accurate recognition, proper intent routing, low latency
"""
import sys
import os
import json
import queue
import time
import threading
import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer
from difflib import SequenceMatcher

from .config import VOICE_CONFIG, MODEL_PATHS, ASR_VOCABULARY
from .intent_parser import parse_intent, validate_intent
from .native_opener import execute_intent, REGISTRY

class AuraVoiceAssistant:
    """Production voice assistant with enhanced accuracy"""
    
    def __init__(self, shared_state):
        self.shared_state = shared_state
        self.config = VOICE_CONFIG
        
        # Audio queue
        self.audio_queue = queue.Queue(maxsize=3)
        
        # State management
        self.running = True
        self.current_gain = 1.0
        self.pending_confirmation = None
        self.last_command_time = 0
        self.command_cooldown = 0.3
        
        # Wake word state
        self.wake_word_detected = False
        self.wake_word_time = 0
        self.listening_for_command = False
        
        # Noise filtering
        self.silence_frames = 0
        self.speech_detected = False
        self.rms_history = []
        self.adaptive_threshold = self.config.min_speech_energy
        self.background_noise_level = 0.0
        self.speech_start_time = 0
        
        # Stats
        self.commands_processed = 0
        self.wake_word_detections = 0
        
        # Audio stream
        self.stream = None
        
        # Validate model
        if not os.path.exists(MODEL_PATHS['asr_english']):
            print(f"[FATAL] Model not found at {MODEL_PATHS['asr_english']}")
            sys.exit(1)
        
        # Load model
        print(f"[Jarvis] Loading ASR model...")
        self.model = Model(MODEL_PATHS['asr_english'])
        
        # Build vocabulary
        self._build_vocabulary()
        
        print(f"[Jarvis] Ready! Say '{self.config.wake_word}' to activate.")
    
    def _build_vocabulary(self):
        """Build comprehensive vocabulary"""
        vocab = ASR_VOCABULARY.get_all_words()
        
        # Add app names
        try:
            app_names = REGISTRY.get_installed_app_names()
            for app in app_names:
                words = app.lower().split()
                for word in words:
                    if word.isalnum() and len(word) > 1:
                        vocab.add(word)
        except Exception as e:
            print(f"[Jarvis] Warning: Could not load app names: {e}")
        
        # Add wake word variations
        for wake_word in self.config.wake_words:
            for word in wake_word.split():
                if len(word) > 1:
                    vocab.add(word.lower())
        
        # Convert to sorted list
        vocab_list = sorted(list(vocab))
        vocab_list.append("[unk]")
        
        # Initialize recognizer
        self.recognizer = KaldiRecognizer(
            self.model,
            self.config.sample_rate,
            json.dumps(vocab_list)
        )
        
        self.recognizer.SetMaxAlternatives(0)
        self.recognizer.SetWords(False)
        
        print(f"[Jarvis] Vocabulary: {len(vocab_list)} words loaded")
    
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
        """Audio stream callback"""
        try:
            processed = self.process_audio(indata)
            if self.audio_queue.full():
                try:
                    self.audio_queue.get_nowait()
                except:
                    pass
            self.audio_queue.put(processed, block=False)
        except:
            pass
    
    def start(self):
        """Start voice recognition loop"""
        print(f"[Jarvis] Listening for wake word: {self.config.wake_word}")
        print(f"[Jarvis] Learning background noise... (takes ~3 seconds)")
        
        threshold_reported = False
        
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
                    if not threshold_reported and len(self.rms_history) >= 30:
                        print(f"[Jarvis] Noise threshold: {self.adaptive_threshold:.1f}")
                        print(f"[Jarvis] Ready! Waiting for wake word...")
                        threshold_reported = True
                    
                    # Feed to recognizer
                    if self.recognizer.AcceptWaveform(data):
                        result = json.loads(self.recognizer.Result())
                        self._handle_result(result)
                    
                    # Check timeout
                    if self.listening_for_command:
                        elapsed = time.time() - self.wake_word_time
                        if elapsed > self.config.wake_word_timeout:
                            print("[Jarvis] Command timeout.")
                            self.listening_for_command = False
                
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[Error] Recognition: {e}")
                    continue
        
        except Exception as e:
            print(f"[FATAL] Audio error: {e}")
        
        finally:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except:
                    pass
    
    def _handle_result(self, result: dict):
        """Handle recognition result"""
        try:
            text = result.get("text", "").strip()
            text = text.replace("[unk]", "").strip()
            
            # Filter short utterances
            if len(text) < 2:
                return
            
            # Check speech duration
            if self.speech_start_time > 0:
                speech_duration = time.time() - self.speech_start_time
                if speech_duration < self.config.min_speech_duration:
                    self.speech_start_time = 0
                    return
            
            self.speech_start_time = 0
            
            # Handle confirmation
            if self.pending_confirmation:
                self._handle_confirmation(text)
                return
            
            # Wake word detection
            matched, wake_word_used, command_text, confidence = self._fuzzy_match_wake_word(text)
            
            if matched:
                self.wake_word_detections += 1
                self.wake_word_time = time.time()
                self.listening_for_command = True
                
                print(f"[WAKE] '{wake_word_used}' detected!")
                
                if command_text:
                    # Command in same utterance
                    print(f"[CMD] {command_text}")
                    self._process_command(command_text)
                    self.listening_for_command = False
                else:
                    print(f"[WAKE] Listening for command...")
            
            elif self.listening_for_command:
                # Process command after wake word
                print(f"[CMD] {text}")
                self._process_command(text)
                self.listening_for_command = False
        
        except Exception as e:
            print(f"[Error] Handle result: {e}")
    
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
        """Process and execute command"""
        try:
            # Check cooldown
            current_time = time.time()
            if current_time - self.last_command_time < self.command_cooldown:
                return
            
            self.last_command_time = current_time
            
            # Parse intent
            intent = parse_intent(command_text)
            
            # Validate
            if not validate_intent(intent):
                print("[Jarvis] I didn't understand that command.")
                return
            
            command = intent.to_command()
            
            # Check confirmation
            if self._requires_confirmation(command):
                self.pending_confirmation = command
                print(f"[CONFIRM] {command['action'].title()} - Say 'confirm' or 'cancel'")
                return
            
            # Execute async
            threading.Thread(
                target=self._execute_command_async,
                args=(command,),
                daemon=True
            ).start()
        
        except Exception as e:
            print(f"[Error] Process command: {e}")
    
    def _execute_command_async(self, command: dict):
        """Execute command asynchronously"""
        try:
            result = execute_intent(command)
            
            if result["status"] == "success":
                print(f"[✓] {result['message']}")
            else:
                print(f"[✗] {result['message']}")
            
            self.commands_processed += 1
        except Exception as e:
            print(f"[Error] Execute: {e}")
    
    def _requires_confirmation(self, command: dict) -> bool:
        """Check if command requires confirmation"""
        if not self.config.confirm_destructive:
            return False
        
        action = command.get("action", "").lower()
        return action in self.config.critical_actions
    
    def _handle_confirmation(self, text: str):
        """Handle confirmation response"""
        try:
            text_lower = text.lower()
            
            if self._fuzzy_match(text_lower, self.config.fuzzy_confirm_words):
                print(f"[Jarvis] Executing...")
                threading.Thread(
                    target=self._execute_command_async,
                    args=(self.pending_confirmation,),
                    daemon=True
                ).start()
                self.pending_confirmation = None
            
            elif self._fuzzy_match(text_lower, self.config.fuzzy_cancel_words):
                print("[Jarvis] Cancelled.")
                self.pending_confirmation = None
            
            else:
                print("[Jarvis] Say 'confirm' or 'cancel'")
        
        except Exception as e:
            print(f"[Error] Handle confirmation: {e}")
            self.pending_confirmation = None
    
    def stop(self):
        """Stop the assistant"""
        self.running = False
        print(f"[Jarvis] Stopped.")
        print(f"[Stats] Commands: {self.commands_processed}, Wake words: {self.wake_word_detections}")

def voice_process_loop(shared_state):
    """Main entry point for voice service"""
    assistant = AuraVoiceAssistant(shared_state)
    
    try:
        assistant.start()
    except KeyboardInterrupt:
        print("\n[Jarvis] Shutdown requested")
    except Exception as e:
        print(f"[FATAL] Voice service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        assistant.stop()