"""
API-FIRST MESSAGING LAYER
==========================
Replaces pyautogui GUI automation for messaging with real API calls.

Priority order per platform:
  Discord:  discord.py bot API (instant, background, no GUI)
  Telegram: python-telegram-bot API (instant, background, no GUI)
  WhatsApp: pywinauto (still GUI — no public API for personal accounts)

Usage:
    from src.actions.messaging import messaging_layer
    result = await messaging_layer.send_message(
        platform="discord",
        contact="Avi",
        body="Hello!",
    )

Wiring in task_orchestrator.py BrowserAutomation:
    from src.actions.messaging import messaging_layer
    result = await messaging_layer.send_message(...)
    if not result["success"]:
        # fall back to GUI automation
        return await self._whatsapp_message(...)
"""

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# DISCORD BOT SENDER
# ════════════════════════════════════════════════════════════════════════════

class DiscordSender:
    """
    Sends messages / initiates calls via Discord bot API.
    Zero GUI dependency. Instant delivery.

    Setup:
      1. Create bot at https://discord.com/developers/applications
      2. Add to your server with Send Messages permission
      3. Set DISCORD_BOT_TOKEN in .env

    Limitations:
      - Bot can only message channels/DMs in servers it's in
      - Bot cannot initiate voice calls (Discord API restriction)
        → calls still use GUI automation (Ctrl+K)
    """

    def __init__(self, token: str = ""):
        self._token   = token
        self._client  = None
        self._ready   = False
        self._user_id_cache: Dict[str, str] = {}   # name → user_id

    def configure(self, token: str):
        self._token = token

    async def _get_client(self):
        """Lazy-init discord.py client."""
        if self._client and self._ready:
            return self._client
        if not self._token:
            return None
        try:
            import discord

            intents = discord.Intents.default()
            intents.members = True
            intents.message_content = True
            client  = discord.Client(intents=intents)

            _ready_event = asyncio.Event()

            @client.event
            async def on_ready():
                _ready_event.set()

            # Start client in background task
            asyncio.ensure_future(client.start(self._token))
            await asyncio.wait_for(_ready_event.wait(), timeout=10.0)
            self._client = client
            self._ready  = True
            return client
        except ImportError:
            logger.debug("[Discord] discord.py not installed")
            return None
        except Exception as e:
            logger.warning(f"[Discord] Client init failed: {e}")
            return None

    async def send_message(self, contact: str, body: str) -> Dict:
        """Send a direct message to a Discord user by display name."""
        client = await self._get_client()
        if not client:
            return {"success": False, "message": "Discord bot not configured"}

        try:
            import discord
            # Search for user in all guilds the bot is in
            target = None
            for guild in client.guilds:
                for member in guild.members:
                    if contact.lower() in member.display_name.lower():
                        target = member
                        break
                if target:
                    break

            if not target:
                return {"success": False, "message": f"Could not find Discord user '{contact}'"}

            dm = await target.create_dm()
            await dm.send(body)
            return {"success": True, "message": f"Message sent to {target.display_name} on Discord, Sir."}

        except Exception as e:
            logger.error(f"[Discord] Send message failed: {e}")
            return {"success": False, "message": f"Discord send failed: {e}"}

    async def available(self) -> bool:
        return bool(self._token)


# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT SENDER
# ════════════════════════════════════════════════════════════════════════════

class TelegramSender:
    """
    Sends messages via Telegram Bot API.
    Zero GUI dependency.

    Setup:
      1. Create bot via @BotFather → get token
      2. User must send /start to your bot first (Telegram API requirement)
      3. Set TELEGRAM_BOT_TOKEN in .env
      4. Set user IDs in data/telegram_contacts.json:
         {"Avi": 123456789, "Mom": 987654321}

    Limitations:
      - User must have messaged the bot first (Telegram security policy)
      - No voice calls via API
    """

    def __init__(self, token: str = ""):
        self._token     = token
        self._chat_ids: Dict[str, int] = {}   # name → chat_id
        self._cache_path = "data/telegram_contacts.json"
        self._load_contacts()

    def configure(self, token: str):
        self._token = token
        self._load_contacts()

    def _load_contacts(self):
        import json
        from pathlib import Path
        p = Path(self._cache_path)
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    self._chat_ids = json.load(f)
                logger.info(f"[Telegram] {len(self._chat_ids)} contacts loaded")
            except Exception:
                pass

    def _save_contacts(self):
        import json
        from pathlib import Path
        p = Path(self._cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self._chat_ids, f, indent=2)

    def register_chat_id(self, name: str, chat_id: int):
        """Register a user's Telegram chat ID. Called when user sends /start."""
        self._chat_ids[name] = chat_id
        self._save_contacts()

    def _find_chat_id(self, contact: str) -> Optional[int]:
        """Find chat_id by name (case-insensitive prefix match)."""
        contact_lower = contact.lower()
        for name, chat_id in self._chat_ids.items():
            if contact_lower in name.lower() or name.lower() in contact_lower:
                return chat_id
        return None

    async def send_message(self, contact: str, body: str) -> Dict:
        """Send a Telegram message to a contact by name."""
        if not self._token:
            return {"success": False, "message": "Telegram bot not configured"}

        chat_id = self._find_chat_id(contact)
        if not chat_id:
            return {
                "success": False,
                "message": (
                    f"I don't have {contact}'s Telegram ID, Sir. "
                    f"Ask them to send /start to your bot first."
                )
            }

        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"chat_id": chat_id, "text": body},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return {"success": True, "message": f"Message sent to {contact} on Telegram, Sir."}
                    return {"success": False, "message": f"Telegram API error: {data.get('description')}"}
        except ImportError:
            # Fall back to requests (sync)
            return await asyncio.get_event_loop().run_in_executor(
                None, self._send_sync, chat_id, body, contact
            )
        except Exception as e:
            logger.error(f"[Telegram] Send failed: {e}")
            return {"success": False, "message": f"Telegram send failed: {e}"}

    def _send_sync(self, chat_id: int, body: str, contact: str) -> Dict:
        try:
            import requests
            url  = f"https://api.telegram.org/bot{self._token}/sendMessage"
            resp = requests.post(url, json={"chat_id": chat_id, "text": body}, timeout=10)
            data = resp.json()
            if data.get("ok"):
                return {"success": True, "message": f"Message sent to {contact} on Telegram, Sir."}
            return {"success": False, "message": f"Telegram API error: {data.get('description')}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def available(self) -> bool:
        return bool(self._token)


# ════════════════════════════════════════════════════════════════════════════
# UNIFIED MESSAGING LAYER
# ════════════════════════════════════════════════════════════════════════════

class MessagingLayer:
    """
    API-first messaging dispatcher.

    Try order:
      Discord  → discord.py bot (if token configured + contact found)
      Telegram → bot API (if token configured + chat_id known)
      Fallback → GUI automation (pywinauto/pyautogui)

    Usage:
        result = await messaging_layer.send_message("discord", "Avi", "Hey!")
        result = await messaging_layer.send_message("telegram", "Mom", "Calling you.")
    """

    def __init__(self):
        self.discord  = DiscordSender()
        self.telegram = TelegramSender()

    def configure(self, discord_token: str = "", telegram_token: str = ""):
        if discord_token:
            self.discord.configure(discord_token)
        if telegram_token:
            self.telegram.configure(telegram_token)

    async def send_message(
        self,
        platform: str,
        contact:  str,
        body:     str,
    ) -> Dict:
        """
        Send a message. Returns success dict.
        Caller should fall back to GUI on failure.
        """
        platform = platform.lower()

        if platform == "discord":
            if await self.discord.available():
                return await self.discord.send_message(contact, body)
            return {"success": False, "message": "Discord API not configured — using GUI"}

        if platform == "telegram":
            if await self.telegram.available():
                return await self.telegram.send_message(contact, body)
            return {"success": False, "message": "Telegram API not configured"}

        # WhatsApp: no public API — must use GUI
        return {"success": False, "message": f"No API for {platform} — using GUI automation"}


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ════════════════════════════════════════════════════════════════════════════

messaging_layer = MessagingLayer()
