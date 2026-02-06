"""
OPTIMIZED AUDIO CONFIG v33.0 - ULTRA-FAST
==========================================
Fixes:
- Higher AGC gain for quiet mics
- Faster silence detection
- Better voice activity detection
- Real-time adaptation
"""

import sounddevice as sd
import numpy as np
from typing import Dict, Any


class OptimizedAudioConfig:
    """
    Audio configuration optimized for SPEED and ACCURACY
    """
    
    def __init__(self):
        # Base settings
        self.sample_rate = 16000
        self.channels = 1
        self.dtype = np.int16
        
        # Chunk size - optimized for latency
        self.chunk_size = 4800  # 300ms @ 16kHz
        
        # AGC - AGGRESSIVE for quiet mics
        self.agc_enabled = True
        self.target_rms = 0.20  # Higher target
        self.current_gain = 12.0  # START HIGH (was 5.0)
        self.max_gain = 25.0  # Higher max (was 20.0)
        self.min_gain = 1.0
        
        # Voice Activity Detection - SENSITIVE
        self.vad_threshold = 0.025  # Lower = more sensitive
        self.noise_gate_threshold = 0.015  # Lower = less filtering
        
        # Device
        self.device_id = None
    
    def get_stream_config(self) -> Dict[str, Any]:
        """Get stream configuration"""
        return {
            'samplerate': self.sample_rate,
            'channels': self.channels,
            'dtype': self.dtype,
            'blocksize': self.chunk_size,
            'device': self.device_id,
            'latency': 'low',  # LOW latency
        }
    
    def process_audio_chunk(self, audio_data: np.ndarray) -> np.ndarray:
        """
        Process audio with aggressive AGC
        """
        # Convert to float
        audio_float = audio_data.astype(np.float32) / 32768.0
        
        # Calculate RMS
        rms = np.sqrt(np.mean(audio_float ** 2))
        
        # Noise gate
        if rms < self.noise_gate_threshold:
            return np.zeros_like(audio_data)
        
        # AGC - AGGRESSIVE
        if rms > 0.001:
            desired_gain = self.target_rms / rms
            desired_gain = np.clip(desired_gain, self.min_gain, self.max_gain)
            
            # Fast attack, slow release
            if desired_gain > self.current_gain:
                self.current_gain += 0.2 * (desired_gain - self.current_gain)  # Fast attack
            else:
                self.current_gain += 0.05 * (desired_gain - self.current_gain)  # Slow release
            
            audio_float *= self.current_gain
        
        # Soft clipping to avoid distortion
        audio_float = np.tanh(audio_float * 0.9)
        
        # Convert back
        audio_processed = (audio_float * 32768.0).astype(np.int16)
        
        return audio_processed
    
    def detect_voice_activity(self, audio_data: np.ndarray) -> bool:
        """Detect if speech is present"""
        audio_float = audio_data.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float ** 2))
        return rms * self.current_gain > self.vad_threshold
    
    def auto_configure_device(self) -> Dict[str, Any]:
        """Auto-configure best microphone"""
        try:
            devices = sd.query_devices()
            
            best_device = None
            best_score = -1
            
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    score = 0
                    
                    # Prefer USB/external
                    if 'usb' in device['name'].lower():
                        score += 10
                    
                    # Prefer "microphone" in name
                    if 'microphone' in device['name'].lower():
                        score += 5
                    
                    # Prefer default
                    if i == sd.default.device[0]:
                        score += 2
                    
                    if score > best_score:
                        best_score = score
                        best_device = (i, device)
            
            if best_device:
                device_id, device_info = best_device
                self.device_id = device_id
                
                print(f"\n🎤 Microphone:")
                print(f"   {device_info['name']}")
                print(f"   Sample Rate: {device_info['default_samplerate']} Hz")
                print(f"   AGC Gain: {self.current_gain}x (auto-adjust)")
                print(f"   Chunk Size: {self.chunk_size} samples")
                
                return device_info
        
        except Exception as e:
            print(f"⚠️  Auto-config failed: {e}")
        
        return None
    
    def get_diagnostics(self) -> Dict[str, Any]:
        """Get current state"""
        return {
            'current_gain': self.current_gain,
            'max_gain': self.max_gain,
            'chunk_size': self.chunk_size,
            'sample_rate': self.sample_rate,
            'vad_threshold': self.vad_threshold,
        }


class WakeWordConfig:
    """
    Wake word detection configuration
    """
    
    def __init__(self):
        # Vosk settings
        self.confidence_threshold = 0.3  # LOW for better detection
        
         # Wake word variations
        self.wake_words = [
            'jarvis',
            'jarvis,',  # With comma
            'hey jarvis',
            'ok jarvis',
            'yo jarvis',
            'listen jarvis',
            'hello jarvis',
            'jarvis listen',
            
            # Common mispronunciations/misrecognitions
            'jarviz',    
            'jarvas',
            'jarvus',
            'jarvez',
            'jervis',
            'jerviz',
            'jerwis',
            'jarvish',
            'jarvys',
            'jarvus',
            'jarvas',
            
            # Short/partial detections
            'jarvi',
            'jarv',
            'jarvy',
            'jerv',
            'jervy',
            
            # With different phonetics
            'jar vis',  # Two words
            'jar-vis',  # Hyphenated
            'jar ves',
            'jarves',
            'jharvis',
            'jharvish',
            
            # Accent variations
            'jaavis',  # Rolling 'r' omission
            'javvis',  # Double 'v'
            'jarfis',  # 'v' -> 'f' confusion
            'charvis',  # 'j' -> 'ch' confusion
            'yarvis',  # 'j' -> 'y' confusion
            'zharvis',  # 'j' -> 'zh' confusion
            
            # Whisper/TTS misrecognitions
            'jarvis\'s',
            'jarvis is',
            'jarvis the',
            'jarvis can',
            
            # AI model common hallucinations
            'garvis',  # 'j' -> 'g' confusion
            'carvis',  # 'j' -> 'c' confusion
            'harvis',  # 'j' -> 'h' confusion
            'marvis',  # 'j' -> 'm' confusion
            
            # Phonetic variations for non-English speakers
            'jarwiz',
            'jarwish',
            'jarwez',
            'jarweez',
            'jarveez',
            'jerveez',
            
            # Sound-alike names
            'harvis',  # Like "Harvis"
            'marvis',  # Like "Marvis"
            'garvis',  # Like "Garvis"
            
            # Quick/slurred speech
            'jarvisss',  # Extended 's'
            'jarvis\'',  # With apostrophe
            'jarvis?',  # With question mark
            
            # With filler words
            'um jarvis',
            'ah jarvis',
            'like jarvis',
            'so jarvis',
            
            # Whispered/silent versions
            'j...arvis',  # Pause in middle
            'ja...rvis',  # Pause in middle
        ]
        
        # Command timing
        self.listen_timeout = 5.0  # Max 5s after wake word
        self.silence_timeout = 0.5  # 0.5s silence stops recording
        
    def is_wake_word(self, text: str) -> bool:
        """Check if text contains wake word"""
        text_clean = text.lower().strip()
        return any(wake in text_clean for wake in self.wake_words)
    
    def extract_command(self, text: str) -> str:
        """Extract command after wake word"""
        text_clean = text.lower().strip()
        
        # Filter false positives (acknowledgments, not commands)
        false_positives = ['yes', 'yes.', 'okay', 'ok', 'sure', 'right', 'uh huh', 'mm hmm']
        if text_clean in false_positives:
            return ""  # Empty = not a valid command
        
        for wake_word in self.wake_words:
            if wake_word in text_clean:
                command = text_clean.replace(wake_word, '', 1).strip()
                command = command.lstrip(',.:;!? ')
                
                # Filter if command is too short or just acknowledgment
                if len(command) < 2 or command in false_positives:
                    return ""
                
                return command
        
        return text_clean


def create_optimized_config():
    """Create optimized audio config"""
    audio_config = OptimizedAudioConfig()
    wake_config = WakeWordConfig()
    
    audio_config.auto_configure_device()
    
    return audio_config, wake_config


__all__ = [
    'OptimizedAudioConfig',
    'WakeWordConfig',
    'create_optimized_config',
]