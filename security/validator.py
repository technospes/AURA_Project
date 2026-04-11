"""
SECURITY VALIDATOR — Command Validation, Rate Limiting, Confirmation
====================================================================
Runs BEFORE any intent parsing or execution.

Checks:
1. Rate limiting — prevent command spam
2. Dangerous action detection — require confirmation for destructive commands
3. Restricted actions — block entirely (OS exploits, etc.)
4. Content validation — filter gibberish / injection attempts
"""

import logging
import re
import time
from collections import deque
from typing import Dict

logger = logging.getLogger(__name__)


class SecurityValidator:
    """
    First gate for all user input.
    Blocks dangerous, spammy, or invalid commands.
    """

    # Commands that require explicit confirmation before execution
    DANGEROUS_PATTERNS = [
        (re.compile(r'\b(shutdown|shut down|power off|turn off the computer)\b', re.I),
         "shutdown",
         "Confirm: Shut down the computer? Say 'yes, shut it down' to proceed, Sir."),

        (re.compile(r'\b(restart|reboot)\b', re.I),
         "restart",
         "Confirm: Restart the computer? Say 'yes, restart' to proceed, Sir."),

        (re.compile(r'\b(delete|remove|uninstall)\b.*(file|folder|directory|program|app)\b', re.I),
         "delete_file",
         "Confirm: This will permanently delete files. Say 'yes, delete it' to proceed, Sir."),

        (re.compile(r'\b(format|wipe|erase)\b', re.I),
         "format",
         "That sounds like a destructive operation, Sir. Please confirm by saying 'yes, proceed'."),
    ]

    # Commands that are completely blocked
    BLOCKED_PATTERNS = [
        re.compile(r'\b(run\s+as\s+admin|elevate\s+privileges|bypass\s+uac)\b', re.I),
        re.compile(r'\b(kill\s+antivirus|disable\s+firewall|turn\s+off\s+defender)\b', re.I),
        re.compile(r'\b(rm\s+-rf|del\s+/[qfs]|format\s+c:)\b', re.I),
        re.compile(r'(?:import|exec|eval|__import__|subprocess\.call)\s*\(', re.I),
    ]

    def __init__(self, config: Dict):
        self.config = config

        # Rate limiting: max N commands per window
        self.max_commands = config.get("max_commands_per_window", 15)
        self.window_seconds = config.get("rate_window_seconds", 60)
        self._command_times: deque = deque()

        # Pending confirmations: map of action_type → expiry_time
        self._pending_confirmations: Dict[str, float] = {}
        self._confirmation_timeout = 30.0  # seconds to confirm

        # Track last confirmation check
        self._last_confirmed_action: str = ""

    async def validate(self, text: str) -> Dict:
        """
        Validate raw input text.

        Returns:
            {
              "allowed": bool,
              "reason": str,
              "needs_confirmation": bool,
              "confirmation_prompt": str,
              "user_message": str
            }
        """
        now = time.time()

        # ── RATE LIMITING ──────────────────────────────────────────────────
        self._cleanup_old_commands(now)
        if len(self._command_times) >= self.max_commands:
            oldest = self._command_times[0]
            wait_s = self.window_seconds - (now - oldest)
            msg = f"Please slow down, Sir. Rate limit reached. Try again in {wait_s:.0f} seconds."
            logger.warning(f"Rate limit hit: {len(self._command_times)} commands in window")
            return {
                "allowed": False,
                "reason": "rate_limited",
                "needs_confirmation": False,
                "user_message": msg
            }

        # ── BLOCKED PATTERNS ───────────────────────────────────────────────
        for pattern in self.BLOCKED_PATTERNS:
            if pattern.search(text):
                msg = "I'm unable to execute that command, Sir. It's outside my permitted actions."
                logger.warning(f"Blocked command: '{text[:60]}'")
                return {
                    "allowed": False,
                    "reason": "blocked",
                    "needs_confirmation": False,
                    "user_message": msg
                }

        # ── CHECK IF THIS IS A CONFIRMATION ───────────────────────────────
        confirmation_result = self._check_confirmation(text, now)
        if confirmation_result is not None:
            # User confirmed or denied a pending dangerous action
            self._command_times.append(now)
            return confirmation_result

        # ── DANGEROUS PATTERNS (need confirmation) ─────────────────────────
        for pattern, action_type, prompt in self.DANGEROUS_PATTERNS:
            if pattern.search(text):
                # Store pending confirmation
                self._pending_confirmations[action_type] = now + self._confirmation_timeout
                self._last_confirmed_action = action_type
                logger.info(f"Dangerous action detected: {action_type}")
                self._command_times.append(now)
                return {
                    "allowed": True,
                    "reason": None,
                    "needs_confirmation": True,
                    "confirmation_prompt": prompt,
                    "user_message": prompt,
                    "pending_action": action_type
                }

        # ── CONTENT VALIDATION ─────────────────────────────────────────────
        if len(text.strip()) < 2:
            return {
                "allowed": False,
                "reason": "too_short",
                "needs_confirmation": False,
                "user_message": "I didn't quite catch that, Sir."
            }

        if len(text) > 2000:
            return {
                "allowed": False,
                "reason": "too_long",
                "needs_confirmation": False,
                "user_message": "That command is too long, Sir. Please keep it under 2000 characters."
            }

        # ── ALL CLEAR ──────────────────────────────────────────────────────
        self._command_times.append(now)
        return {
            "allowed": True,
            "reason": None,
            "needs_confirmation": False,
            "user_message": None
        }

    def _check_confirmation(self, text: str, now: float) -> Dict | None:
        """
        Check if the user is confirming or denying a pending dangerous action.
        Returns a validation dict if this is a confirmation response, else None.
        """
        if not self._pending_confirmations:
            return None

        text_lower = text.lower().strip()

        CONFIRM_WORDS = ["yes", "confirm", "proceed", "do it", "go ahead", "affirmative",
                         "yes shut it down", "yes restart", "yes delete it", "yes proceed"]
        DENY_WORDS = ["no", "cancel", "abort", "stop", "negative", "don't", "never mind"]

        is_confirm = any(w in text_lower for w in CONFIRM_WORDS)
        is_deny = any(w in text_lower for w in DENY_WORDS)

        if not is_confirm and not is_deny:
            return None  # Not a confirmation response — process normally

        # Clean up expired confirmations
        expired = [k for k, exp in self._pending_confirmations.items() if now > exp]
        for k in expired:
            del self._pending_confirmations[k]

        if not self._pending_confirmations:
            return None  # All expired

        if is_deny:
            self._pending_confirmations.clear()
            return {
                "allowed": False,
                "reason": "user_cancelled",
                "needs_confirmation": False,
                "user_message": "Understood, Sir. Action cancelled."
            }

        if is_confirm:
            # Allow the pending action — clear it
            action = next(iter(self._pending_confirmations.keys()))
            del self._pending_confirmations[action]
            logger.info(f"Dangerous action confirmed by user: {action}")
            return {
                "allowed": True,
                "reason": None,
                "needs_confirmation": False,
                "confirmed_action": action,
                "user_message": None
            }

        return None

    def _cleanup_old_commands(self, now: float):
        """Remove command timestamps outside the rate window."""
        cutoff = now - self.window_seconds
        while self._command_times and self._command_times[0] < cutoff:
            self._command_times.popleft()
