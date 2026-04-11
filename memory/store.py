"""
MEMORY STORE — Persistent, Recall-Aware, Importance-Scored
===========================================================
Every interaction triggers a memory recall before responding.
Memory actively influences decisions — it's not just a log.

Storage: JSON file (no DB dependency, portable)
Retrieval: Keyword + recency + importance scoring
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    id: str
    key: str
    value: Any
    category: str          # "preference", "personal", "task", "fact", "context"
    importance: float      # 0.0–1.0
    keywords: List[str]
    timestamp: float
    access_count: int = 0
    last_accessed: float = 0.0
    source: str = "agent"  # "agent" | "user_explicit" | "inferred"

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "MemoryEntry":
        return cls(**d)

    def relevance_score(self, query_keywords: set, current_time: float) -> float:
        """Score this memory's relevance to a query."""
        kw_overlap = len(set(query_keywords) & set(self.keywords or []))
        kw_score = (kw_overlap / max(len(query_keywords), 1)) * 0.5

        recency_age = (current_time - self.timestamp) / 3600  # hours
        recency_score = max(0.0, 0.2 - recency_age * 0.01)

        access_score = min(self.access_count / 20.0, 0.1)
        importance_score = self.importance * 0.2

        return min(kw_score + recency_score + access_score + importance_score, 1.0)


class MemoryStore:
    """
    Persistent memory with intelligent recall.

    Core contract:
    - store()  → persists a fact, preference, or event
    - recall() → returns memories RELEVANT to the current input
    - get_context_hints() → quick summary for LLM prompt injection
    """

    STOP_WORDS = {
        "i", "me", "my", "we", "you", "your", "he", "she", "it", "they",
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
        "to", "of", "in", "on", "at", "for", "with", "this", "that",
        "what", "how", "why", "when", "where", "who", "will", "can", "do"
    }

    def __init__(self, config: Dict):
        self._file = Path(config.get("memory_file", "data/jarvis_memory.json"))
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: Dict[str, MemoryEntry] = {}
        self._load()
        self._dirty = False

        # Auto-save every 30 seconds
        self._start_autosave()
        logger.info(f"💾 Memory loaded: {len(self._entries)} entries")

    # ── PUBLIC API ─────────────────────────────────────────────────────────

    async def store(
        self,
        key: str,
        value: Any,
        category: str = "fact",
        importance: float = 0.5,
        source: str = "agent"
    ) -> str:
        """Store a memory entry. Returns the entry ID."""
        entry_id = self._make_id(key, value)
        keywords = self._extract_keywords(f"{key} {value}")

        entry = MemoryEntry(
            id=entry_id,
            key=key,
            value=value,
            category=category,
            importance=importance,
            keywords=keywords,
            timestamp=time.time(),
            source=source
        )

        with self._lock:
            self._entries[entry_id] = entry
            self._dirty = True

        logger.info(f"💾 Stored [{category}] {key!r} = {str(value)[:60]}")
        return entry_id

    async def recall(self, query: str, intent: Dict, context: Dict) -> Dict:
        """
        Recall memories relevant to this query + intent.
        Injects both into the returned dict.
        """
        query_kw = self._extract_keywords(query)
        current_time = time.time()

        scored = []
        with self._lock:
            for entry in self._entries.values():
                score = entry.relevance_score(query_kw, current_time)
                if score > 0.05:
                    scored.append((score, entry))

        scored.sort(key=lambda x: x[0] * x[1].importance, reverse=True)
        top = scored[:8]

        # Update access stats
        with self._lock:
            for _, entry in top:
                entry.access_count += 1
                entry.last_accessed = current_time
                self._dirty = True

        # Categorize results
        preferences = []
        personal = []
        tasks = []
        facts = []

        for score, entry in top:
            obj = {"key": entry.key, "value": entry.value, "score": round(score, 3)}
            if entry.category == "preference":
                preferences.append(obj)
            elif entry.category == "personal":
                personal.append(obj)
            elif entry.category == "task":
                tasks.append(obj)
            else:
                facts.append(obj)

        return {
            "preferences": preferences,
            "personal": personal,
            "recent_tasks": tasks,
            "facts": facts,
            "total_recalled": len(top)
        }

    async def get_context_hints(self, query: str) -> Dict:
        """
        Quick summary of relevant memory for LLM prompt injection.
        Returns compact strings, not full entries.
        """
        result = await self.recall(query, {}, {})
        hints = []

        for p in result["personal"][:2]:
            hints.append(f"{p['key']}: {p['value']}")

        for p in result["preferences"][:3]:
            hints.append(f"Prefers {p['key']}: {p['value']}")

        for f in result["facts"][:2]:
            hints.append(f"{f['key']}: {f['value']}")

        return {"facts": hints, "raw": result}

    async def get(self, key: str) -> Optional[Any]:
        """Direct key lookup."""
        with self._lock:
            for entry in self._entries.values():
                if entry.key == key:
                    entry.access_count += 1
                    entry.last_accessed = time.time()
                    self._dirty = True
                    return entry.value
        return None

    async def forget(self, key: str) -> bool:
        """Delete a memory entry by key."""
        with self._lock:
            to_delete = [eid for eid, e in self._entries.items() if e.key == key]
            for eid in to_delete:
                del self._entries[eid]
            if to_delete:
                self._dirty = True
        return len(to_delete) > 0

    async def get_preferences(self) -> Dict:
        """Get all stored user preferences."""
        with self._lock:
            prefs = {e.key: e.value for e in self._entries.values()
                     if e.category == "preference"}
        return prefs

    async def cleanup(self, max_age_days: int = 30):
        """Remove old, low-importance, rarely accessed entries."""
        cutoff = time.time() - (max_age_days * 86400)
        with self._lock:
            to_delete = [
                eid for eid, e in self._entries.items()
                if (e.timestamp < cutoff
                    and e.importance < 0.6
                    and e.access_count < 3
                    and e.category not in ("preference", "personal"))
            ]
            for eid in to_delete:
                del self._entries[eid]
            if to_delete:
                self._dirty = True
                logger.info(f"🧹 Cleaned {len(to_delete)} old memories")

    def stats(self) -> Dict:
        with self._lock:
            by_cat: Dict[str, int] = {}
            for e in self._entries.values():
                by_cat[e.category] = by_cat.get(e.category, 0) + 1
        return {"total": len(self._entries), "by_category": by_cat}

    # ── INTERNALS ──────────────────────────────────────────────────────────

    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r"\b\w+\b", text.lower())
        filtered = [w for w in words if w not in self.STOP_WORDS and len(w) > 2]
        # Deduplicate preserving order, return top 12
        seen = set()
        result = []
        for w in filtered:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result[:12]

    def _make_id(self, key: str, value: Any) -> str:
        raw = f"{key}:{value}:{time.time()}"
        return hashlib.md5(raw.encode()).hexdigest()[:10]

    def _load(self):
        if not self._file.exists():
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for eid, ed in data.get("entries", {}).items():
                self._entries[eid] = MemoryEntry.from_dict(ed)
        except Exception as e:
            logger.error(f"Memory load failed: {e}")

    def _save(self):
        if not self._dirty:
            return
        try:
            tmp = self._file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"entries": {eid: e.to_dict() for eid, e in self._entries.items()}},
                    f, indent=2, ensure_ascii=False
                )
            tmp.replace(self._file)
            self._dirty = False
        except Exception as e:
            logger.error(f"Memory save failed: {e}")

    def _start_autosave(self):
        def _loop():
            while True:
                time.sleep(30)
                with self._lock:
                    self._save()

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
