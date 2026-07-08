"""
SERVICE PATCH — Follow-up Microphone Timing Fix
================================================
Problem from logs: After Jarvis asks a clarification question (e.g.
"What resolution would you like?"), trigger_followup() was called
IMMEDIATELY in on_command() BEFORE the TTS had finished speaking.
This meant the microphone opened while Jarvis was still talking,
picking up the tail of Jarvis's own voice as the user's answer.

Fix: patch service.trigger_followup() to delay until TTS is done.
We check whether the TTS engine is currently speaking and wait for
it to finish (up to 10 seconds) before opening the microphone.

This is a drop-in patch — add this import at the top of main.py
and call patch_trigger_followup(service) after service is created.

Usage in main.py voice_process_main():
    from service_patch import patch_trigger_followup
    patch_trigger_followup(service, tts)
"""

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def patch_trigger_followup(service, tts_engine=None):
    """
    Patches service.trigger_followup() to wait for TTS to finish
    before opening the microphone for the follow-up answer.

    Args:
        service:    VoiceService instance
        tts_engine: LocalTTSEngine (or any object with a speaking state)
    """
    _orig_trigger = service.trigger_followup

    def _patched_trigger_followup():
        """
        Delayed trigger — waits for TTS to finish speaking before
        opening the microphone.  Max wait: 12 seconds.
        """
        def _delayed():
            # Wait for TTS to go quiet before opening microphone
            waited   = 0.0
            max_wait = 12.0
            poll     = 0.1

            if tts_engine:
                # LocalTTSEngine: check if AudioPlayer thread is alive
                player = getattr(tts_engine, '_player', None)
                if player:
                    while waited < max_wait:
                        if not (getattr(player, '_running', False) or
                                getattr(player, '_thread', None) and
                                getattr(player._thread, 'is_alive', lambda: False)()):
                            break
                        time.sleep(poll)
                        waited += poll
                    # Extra 150ms buffer after audio stops (room acoustics)
                    time.sleep(0.15)

            logger.info(f"[ServicePatch] Trigger followup after {waited:.1f}s TTS wait")
            _orig_trigger()

        # Run in a daemon thread so on_command() returns immediately
        t = threading.Thread(target=_delayed, daemon=True, name="followup-delay")
        t.start()

    service.trigger_followup = _patched_trigger_followup
    logger.info("[ServicePatch]  trigger_followup patched (TTS-aware delay)")