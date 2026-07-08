"""
HYBRID DATA SYNC — Production-Complete Native Data Layer
=========================================================
Solves the "Who is Mom?" problem.

Hierarchy (tried in order, stops at first success):
  1. Outlook Desktop (win32com) — zero auth, instant, offline-capable
  2. MS Graph API (MSAL OAuth) — full cloud contacts + calendar
  3. Local cache (data/local_contacts.json) — always works

Features:
  - Alias resolution: "mom" → "Priya Sharma", "boss" → "Rahul Gupta"
  - Fuzzy name matching: "Avi" matches "Avinash Sharma"
  - Delta sync: only updates EntityResolver when data changes (hash check)
  - Calendar injection: today's events → SystemContext
  - Boot-time sync + background 5-min refresh
  - Automatic local cache write so next boot is instant

Wiring in main.py voice_process_main():
    from data_sync import data_sync_manager, entity_resolver
    entity_resolver.load_aliases("data/aliases.json")
    data_sync_manager.start_background_sync()
    # Then resolve contacts anywhere:
    full_name = entity_resolver.resolve("mom")   # → "Priya Sharma"
"""

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# CONTACT + CALENDAR DATA MODELS
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Contact:
    name:  str
    email: str = ""
    phone: str = ""
    tags:  List[str] = field(default_factory=list)   # e.g. ["family", "work"]

    @property
    def display(self) -> str:
        return self.name

    def matches(self, query: str) -> bool:
        """True if query fuzzy-matches this contact."""
        q   = query.lower().strip()
        n   = self.name.lower()
        # Exact
        if q == n:
            return True
        # First name
        if n.split()[0] == q:
            return True
        # Contains
        if q in n:
            return True
        # All query words in name
        words = q.split()
        if all(w in n for w in words):
            return True
        return False


@dataclass
class CalendarEvent:
    subject:    str
    start_time: str   # ISO string
    end_time:   str
    location:   str = ""
    body:       str = ""

    def to_summary(self) -> str:
        return f"{self.subject} at {self.start_time}" + (f" ({self.location})" if self.location else "")


# ════════════════════════════════════════════════════════════════════════════
# ENTITY RESOLVER
# ════════════════════════════════════════════════════════════════════════════

class EntityResolver:
    """
    Resolves spoken names/aliases to full contact names.

    Resolution order:
      1. Exact alias match (mom → Priya Sharma)
      2. Exact contact name match
      3. First-name match
      4. Fuzzy contains match
      5. Return original (let WhatsApp search handle it)
    """

    def __init__(self):
        self._contacts: List[Contact] = []
        self._aliases:  Dict[str, str] = {}   # alias → full name
        self._lock = threading.Lock()

    def set_contacts(self, raw: List[Dict]):
        """Replace contact list. Called by sync daemon on each delta."""
        contacts = []
        for c in raw:
            name = c.get("name", "").strip()
            if name:
                contacts.append(Contact(
                    name=name,
                    email=c.get("email", ""),
                    phone=c.get("phone", ""),
                    tags=c.get("tags", []),
                ))
        with self._lock:
            self._contacts = contacts
        logger.info(f"[EntityResolver] {len(contacts)} contacts loaded")

    def load_aliases(self, path: str = "data/aliases.json"):
        """
        Load alias file. Format:
        {"mom": "Priya Sharma", "boss": "Rahul Gupta", "avi": "Avinash Kumar"}
        """
        p = Path(path)
        if not p.exists():
            # Create default empty file
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "mom":    "",
                "dad":    "",
                "boss":   "",
                "bro":    "",
                "sis":    "",
            }, indent=2))
            logger.info(f"[EntityResolver] Created alias template at {path}")
            return
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            with self._lock:
                self._aliases = {k.lower(): v for k, v in raw.items() if v}
            logger.info(f"[EntityResolver] {len(self._aliases)} aliases loaded from {path}")
        except Exception as e:
            logger.warning(f"[EntityResolver] Alias load failed: {e}")

    def resolve(self, query: str) -> str:
        """
        Resolve a spoken name to the best matching full contact name.
        Returns the original query if no match found.
        """
        q = query.lower().strip()
        with self._lock:
            aliases  = self._aliases
            contacts = self._contacts

        # 1. Alias lookup
        if q in aliases:
            logger.debug(f"[EntityResolver] Alias: '{q}' → '{aliases[q]}'")
            return aliases[q]

        if not contacts:
            return query

        # 2. Exact name match
        for c in contacts:
            if c.name.lower() == q:
                return c.name

        # 3. First-name match (returns first match — disambiguation handled by orchestrator)
        matches = [c for c in contacts if c.name.lower().split()[0] == q]
        if len(matches) == 1:
            return matches[0].name
        if len(matches) > 1:
            # Multiple first-name matches — return original so orchestrator can disambiguate
            logger.info(f"[EntityResolver] Multiple first-name matches for '{q}': {[m.name for m in matches]}")
            return query  # orchestrator will show list

        # 4. Fuzzy contains match
        fuzzy = [c for c in contacts if c.matches(q)]
        if len(fuzzy) == 1:
            return fuzzy[0].name
        if fuzzy:
            return query  # let orchestrator disambiguate

        return query

    def get_candidates(self, query: str) -> List[str]:
        """Return all contact names matching the query (for disambiguation)."""
        q = query.lower().strip()
        with self._lock:
            contacts = self._contacts
        return [c.name for c in contacts if c.matches(q)]

    def add_alias(self, alias: str, full_name: str, persist_path: str = "data/aliases.json"):
        """Teach Jarvis: 'boss' = 'Rahul Gupta'. Persists across restarts."""
        with self._lock:
            self._aliases[alias.lower()] = full_name
        try:
            p = Path(persist_path)
            existing = {}
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    existing = json.load(f)
            existing[alias] = full_name
            with open(p, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            logger.info(f"[EntityResolver] Alias saved: '{alias}' → '{full_name}'")
        except Exception as e:
            logger.warning(f"[EntityResolver] Alias persist failed: {e}")


# ════════════════════════════════════════════════════════════════════════════
# DATA SYNC MANAGER
# ════════════════════════════════════════════════════════════════════════════

class HybridDataSyncManager:
    """
    Hierarchical contact + calendar ingestion daemon.

    Priority: Outlook Desktop → MS Graph API (Device Code / Client Credentials) → Local Cache

    Boot-time: sync runs immediately in a thread (doesn't block startup).
    Background: syncs every 5 minutes.

    MS Graph setup (one-time):
      1. Register app in Azure portal (portal.azure.com → Entra ID → App registrations)
      2. Set redirect URI: http://localhost  (Mobile/desktop platform)
      3. Add API permissions: Contacts.Read, Calendars.Read (Delegated)
      4. Copy Application (client) ID → AZURE_CLIENT_ID in .env
      5. On first run, Jarvis prints a URL + code — you log in once in your browser
      6. Token cached to data/msal_cache.bin — no login needed on subsequent boots
    """

    # Delta link caches for incremental sync (much faster after first boot)
    _DELTA_CONTACTS_LINK: Optional[str] = None
    _DELTA_CALENDAR_LINK: Optional[str] = None

    def __init__(self, resolver: EntityResolver):
        self.resolver    = resolver
        self.cache_file  = Path("data/local_contacts.json")
        self._last_hash  = None
        self._lock       = threading.Lock()
        self._today_events: List[CalendarEvent] = []
        # MS Graph credentials (loaded from env)
        self._tenant_id  = ""
        self._client_id  = ""
        # MSAL token cache persisted to disk
        self._msal_cache_path = Path("data/msal_cache.bin")
        self._msal_token_cache = None
        self._load_msal_env()

    def _load_msal_env(self):
        """Load Azure credentials from environment (set in .env)."""
        import os
        self._tenant_id = os.getenv("AZURE_TENANT_ID", "common")
        self._client_id = os.getenv("AZURE_CLIENT_ID", "")

    def configure_graph_api(self, tenant_id: str, client_id: str,
                             client_secret: str = ""):
        """
        Explicit configuration (alternative to .env).
        client_secret only needed for daemon/service mode (not recommended for desktop).
        """
        self._tenant_id = tenant_id
        self._client_id = client_id

    def start_background_sync(self):
        """Launch daemon thread. Returns immediately (non-blocking)."""
        t = threading.Thread(target=self._sync_loop, daemon=True, name="data-sync")
        t.start()
        logger.info("[DataSync] Background contact sync started (5-min interval)")

    def sync_now(self) -> int:
        """Force immediate sync. Returns number of contacts loaded."""
        return self._do_sync()

    @property
    def today_events(self) -> List[CalendarEvent]:
        with self._lock:
            return list(self._today_events)

    def get_event_summary(self) -> str:
        events = self.today_events
        if not events:
            return ""
        parts = [e.to_summary() for e in events[:5]]
        return "Today's calendar: " + "; ".join(parts)

    # ── INTERNAL ──────────────────────────────────────────────────────────

    def _sync_loop(self):
        self._do_sync()
        while True:
            time.sleep(300)  # 5 min
            self._do_sync()

    def _do_sync(self) -> int:
        contacts = (
            self._try_outlook_contacts() or
            self._try_graph_api_contacts() or
            self._load_cache()
        )
        if contacts:
            h = hashlib.md5(
                json.dumps(contacts, sort_keys=True).encode()
            ).hexdigest()
            if h != self._last_hash:
                self.resolver.set_contacts(contacts)
                self._last_hash = h
                self._save_cache(contacts)

        events = self._try_outlook_calendar()
        if events is not None:
            with self._lock:
                self._today_events = events

        return len(contacts or [])

    # ── MSAL DEVICE CODE FLOW ─────────────────────────────────────────────

    def _get_msal_app(self):
        """
        Build MSAL PublicClientApplication with persistent token cache.
        The cache lives at data/msal_cache.bin so the user only logs in once.
        """
        try:
            import msal
            cache = msal.SerializableTokenCache()
            if self._msal_cache_path.exists():
                cache.deserialize(self._msal_cache_path.read_text(encoding="utf-8"))
            self._msal_token_cache = cache
            return msal.PublicClientApplication(
                self._client_id,
                authority=f"https://login.microsoftonline.com/{self._tenant_id}",
                token_cache=cache,
            )
        except ImportError:
            return None
        except Exception as e:
            logger.debug(f"[DataSync] MSAL app build failed: {e}")
            return None

    def _save_msal_cache(self):
        """Persist token cache so next boot skips the login flow."""
        if self._msal_token_cache and self._msal_token_cache.has_state_changed:
            self._msal_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._msal_cache_path.write_text(
                self._msal_token_cache.serialize(), encoding="utf-8"
            )

    def _acquire_graph_token(self) -> Optional[str]:
        """
        Acquire MS Graph token.

        Flow:
          1. Check cache for valid token (silent acquire — no network if cached)
          2. If cache miss: Device Code Flow — prints a URL + 8-char code
             User opens the URL and enters the code once.
          3. Token cached; subsequent calls are instant.

        Returns Bearer token string or None on failure.
        """
        if not self._client_id:
            return None

        app = self._get_msal_app()
        if not app:
            return None

        _SCOPES = [
            "https://graph.microsoft.com/Contacts.Read",
            "https://graph.microsoft.com/Calendars.Read",
        ]

        try:
            # Try silent acquire first (uses cache)
            accounts = app.get_accounts()
            if accounts:
                result = app.acquire_token_silent(_SCOPES, account=accounts[0])
                if result and "access_token" in result:
                    self._save_msal_cache()
                    return result["access_token"]

            # Device Code Flow — interactive one-time login
            flow = app.initiate_device_flow(scopes=_SCOPES)
            if "user_code" not in flow:
                logger.warning("[DataSync] Device Code Flow initiation failed")
                return None

            # Print the auth message so the user can act on it
            logger.info(f"[DataSync]  MS Graph login required:")
            logger.info(f"[DataSync]    {flow['message']}")
            print(f"\n{'='*60}")
            print(f"  Jarvis needs access to your Microsoft contacts & calendar.")
            print(f"  {flow['message']}")
            print(f"{'='*60}\n")

            # Wait for user to complete (timeout: 15 min)
            result = app.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                self._save_msal_cache()
                logger.info("[DataSync]  MS Graph authenticated successfully")
                return result["access_token"]

            logger.warning(f"[DataSync] MS Graph auth failed: {result.get('error_description')}")
            return None

        except Exception as e:
            logger.debug(f"[DataSync] Token acquire failed: {e}")
            return None

    def _try_outlook_contacts(self) -> Optional[List[Dict]]:
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            ns      = outlook.GetNamespace("MAPI")
            items   = ns.GetDefaultFolder(10).Items  # 10 = Contacts
            result  = []
            for item in items:
                if getattr(item, "Class", 0) == 40:  # olContact
                    name  = getattr(item, "FullName", "").strip()
                    email = getattr(item, "Email1Address", "").strip()
                    phone = getattr(item, "MobileTelephoneNumber", "").strip()
                    if name:
                        result.append({"name": name, "email": email, "phone": phone})
            logger.info(f"[DataSync] Outlook contacts: {len(result)}")
            return result or None
        except ImportError:
            return None
        except Exception as e:
            logger.debug(f"[DataSync] Outlook contacts failed: {e}")
            return None

    def _try_outlook_calendar(self) -> Optional[List[CalendarEvent]]:
        try:
            import win32com.client
            from datetime import datetime, timedelta
            outlook = win32com.client.Dispatch("Outlook.Application")
            ns      = outlook.GetNamespace("MAPI")
            cal     = ns.GetDefaultFolder(9)  # 9 = Calendar
            items   = cal.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            today_start = datetime.now().replace(hour=0, minute=0, second=0)
            today_end   = today_start + timedelta(days=1)

            restrict_str = (
                f"[Start] >= '{today_start.strftime('%m/%d/%Y %H:%M %p')}' AND "
                f"[Start] < '{today_end.strftime('%m/%d/%Y %H:%M %p')}'"
            )
            items = items.Restrict(restrict_str)

            events = []
            for item in items:
                events.append(CalendarEvent(
                    subject=getattr(item, "Subject", ""),
                    start_time=str(getattr(item, "Start", "")),
                    end_time=str(getattr(item, "End", "")),
                    location=getattr(item, "Location", ""),
                ))
            return events
        except Exception:
            return None

    def _try_graph_api_contacts(self) -> Optional[List[Dict]]:
        """
        Fetch contacts from MS Graph with delta sync.
        Uses Device Code Flow token from _acquire_graph_token().
        Delta link reuse means subsequent syncs only fetch changes.
        """
        token = self._acquire_graph_token()
        if not token:
            return None
        try:
            import requests
            headers = {"Authorization": f"Bearer {token}"}

            # Delta sync: only fetch changes since last sync
            if self._DELTA_CONTACTS_LINK:
                url = self._DELTA_CONTACTS_LINK
            else:
                url = (
                    "https://graph.microsoft.com/v1.0/me/contacts/delta"
                    "?$select=displayName,emailAddresses,mobilePhone&$top=500"
                )

            result = []
            while url:
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                for c in data.get("value", []):
                    if c.get("@removed"):
                        continue
                    name  = c.get("displayName", "")
                    email = (c.get("emailAddresses") or [{}])[0].get("address", "")
                    phone = c.get("mobilePhone", "")
                    if name:
                        result.append({"name": name, "email": email, "phone": phone})
                # Pagination
                url = data.get("@odata.nextLink")
                # Save delta link for next incremental sync
                if "@odata.deltaLink" in data:
                    HybridDataSyncManager._DELTA_CONTACTS_LINK = data["@odata.deltaLink"]

            logger.info(f"[DataSync] MS Graph contacts: {len(result)}")
            return result or None

        except ImportError:
            return None
        except Exception as e:
            # Invalidate delta link on error so next sync is full
            HybridDataSyncManager._DELTA_CONTACTS_LINK = None
            logger.debug(f"[DataSync] MS Graph failed: {e}")
            return None

    def _try_graph_calendar(self) -> Optional[List[CalendarEvent]]:
        """Fetch today's calendar events from MS Graph."""
        token = self._acquire_graph_token()
        if not token:
            return None
        try:
            import requests
            from datetime import datetime, timedelta, timezone
            headers = {"Authorization": f"Bearer {token}"}
            now       = datetime.now(timezone.utc)
            today_end = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            params = {
                "$filter": (
                    f"start/dateTime ge '{now.isoformat()}' and "
                    f"start/dateTime le '{today_end.isoformat()}'"
                ),
                "$select": "subject,start,end,location",
                "$top": 20,
                "$orderby": "start/dateTime",
            }
            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/calendarView",
                headers=headers, params=params, timeout=15,
            )
            resp.raise_for_status()
            events = []
            for e in resp.json().get("value", []):
                events.append(CalendarEvent(
                    subject=e.get("subject", ""),
                    start_time=e.get("start", {}).get("dateTime", ""),
                    end_time=e.get("end", {}).get("dateTime", ""),
                    location=e.get("location", {}).get("displayName", ""),
                ))
            return events
        except Exception as e:
            logger.debug(f"[DataSync] MS Graph calendar failed: {e}")
            return None

    def _load_cache(self) -> List[Dict]:
        if not self.cache_file.exists():
            return []
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"[DataSync] Local cache: {len(data)} contacts")
            return data
        except Exception:
            return []

    def _save_cache(self, contacts: List[Dict]):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(contacts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[DataSync] Cache save failed: {e}")


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETONS
# ════════════════════════════════════════════════════════════════════════════

entity_resolver  = EntityResolver()
data_sync_manager = HybridDataSyncManager(entity_resolver)