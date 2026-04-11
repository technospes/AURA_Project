"""
JARVIS MEMORY SYSTEM
====================
Persistent memory with intelligent retrieval and categorization

Features:
- Long-term storage (JSON file)
- Smart categorization (facts, preferences, tasks, context)
- Efficient retrieval (keyword + semantic search)
- Automatic cleanup (prevent bloat)
- Thread-safe operations
"""

import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """Single memory entry"""
    id: str
    category: str  # 'fact', 'preference', 'task', 'context', 'screen'
    content: str
    keywords: List[str]
    timestamp: float
    access_count: int = 0
    last_accessed: float = 0
    importance: float = 1.0  # 0.0-1.0, higher = more important
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON storage"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MemoryEntry':
        """Create from dictionary"""
        return cls(**data)


class MemoryCategory:
    """Memory categories"""
    FACT = "fact"              # General knowledge: "Python is a programming language"
    PREFERENCE = "preference"  # User preferences: "I prefer dark mode"
    TASK = "task"             # Completed tasks: "Opened Spotify at 10:30 AM"
    CONTEXT = "context"       # Conversation context: "Last discussed Python"
    SCREEN = "screen"         # Screen content: "Screen shows code editor"


class JarvisMemory:
    """
    Production-ready memory system
    
    Storage structure:
    {
        "memories": {
            "mem_id_1": {...},
            "mem_id_2": {...}
        },
        "metadata": {
            "total_entries": 150,
            "last_cleanup": 1234567890,
            "version": "1.0"
        }
    }
    """
    
    def __init__(self, memory_file: Path = None, max_entries: int = 500):
        self.memory_file = memory_file or Path("jarvis_memory.json")
        self.max_entries = max_entries
        
        # In-memory storage
        self.memories: Dict[str, MemoryEntry] = {}
        self.metadata: Dict[str, Any] = {
            "total_entries": 0,
            "last_cleanup": time.time(),
            "version": "1.0"
        }
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Load existing memories
        self._load()
        
        logger.info(f"✓ Memory system initialized: {len(self.memories)} entries loaded")
    
    def _generate_id(self, content: str, category: str) -> str:
        """Generate unique ID for memory entry"""
        unique_string = f"{content}{category}{time.time()}"
        return hashlib.md5(unique_string.encode()).hexdigest()[:12]
    
    def _extract_keywords(self, content: str) -> List[str]:
        """Extract keywords from content"""
        # Simple keyword extraction (can be enhanced with NLP)
        import re
        
        # Remove common words
        stop_words = {
            'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'you', 'your',
            'he', 'him', 'his', 'she', 'her', 'it', 'they', 'them',
            'what', 'which', 'who', 'when', 'where', 'why', 'how',
            'a', 'an', 'the', 'and', 'but', 'or', 'if', 'then',
            'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'should', 'could', 'can', 'may',
            'to', 'from', 'in', 'on', 'at', 'by', 'for', 'with'
        }
        
        # Extract words
        words = re.findall(r'\b\w+\b', content.lower())
        
        # Filter and return
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Return top 10 most relevant (by length as proxy for importance)
        return sorted(set(keywords), key=len, reverse=True)[:10]
    
    def _categorize_content(self, content: str, hint: str = None) -> str:
        """Auto-categorize content if no hint provided"""
        if hint:
            return hint
        
        content_lower = content.lower()
        
        # Preference indicators
        if any(word in content_lower for word in ['prefer', 'like', 'love', 'hate', 'favorite', 'best']):
            return MemoryCategory.PREFERENCE
        
        # Task indicators
        if any(word in content_lower for word in ['opened', 'closed', 'played', 'searched', 'completed', 'did']):
            return MemoryCategory.TASK
        
        # Screen indicators
        if any(word in content_lower for word in ['screen shows', 'display shows', 'window contains', 'seeing']):
            return MemoryCategory.SCREEN
        
        # Default to fact
        return MemoryCategory.FACT
    
    def store(self, content: str, category: str = None, importance: float = 1.0) -> str:
        """
        Store memory
        
        Args:
            content: What to remember
            category: Optional category hint
            importance: 0.0-1.0, higher = more important
            
        Returns:
            Memory ID
        """
        with self.lock:
            # Auto-categorize if needed
            category = self._categorize_content(content, category)
            
            # Extract keywords
            keywords = self._extract_keywords(content)
            
            # Create entry
            memory_id = self._generate_id(content, category)
            
            entry = MemoryEntry(
                id=memory_id,
                category=category,
                content=content,
                keywords=keywords,
                timestamp=time.time(),
                importance=importance
            )
            
            self.memories[memory_id] = entry
            self.metadata["total_entries"] = len(self.memories)
            
            # Auto-save
            self._save()
            
            logger.info(f"💾 Stored [{category}]: {content[:50]}...")
            
            return memory_id
    
    def recall(
        self,
        query: str,
        category: str = None,
        limit: int = 5,
        min_relevance: float = 0.3
    ) -> List[MemoryEntry]:
        """
        Recall memories matching query
        
        Args:
            query: Search query
            category: Optional category filter
            limit: Max results
            min_relevance: Minimum relevance score (0.0-1.0)
            
        Returns:
            List of matching memories, sorted by relevance
        """
        with self.lock:
            query_lower = query.lower()
            query_keywords = set(self._extract_keywords(query))
            
            results = []
            
            for memory in self.memories.values():
                # Category filter
                if category and memory.category != category:
                    continue
                
                # Calculate relevance
                relevance = self._calculate_relevance(
                    query_lower,
                    query_keywords,
                    memory
                )
                
                if relevance >= min_relevance:
                    results.append((relevance, memory))
                    
                    # Update access stats
                    memory.access_count += 1
                    memory.last_accessed = time.time()
            
            # Sort by relevance * importance
            results.sort(
                key=lambda x: x[0] * x[1].importance,
                reverse=True
            )
            
            # Return top matches
            matches = [mem for _, mem in results[:limit]]
            
            if matches:
                logger.info(f"🧠 Recalled {len(matches)} memories for: {query}")
                self._save()  # Save updated access stats
            
            return matches
    
    def _calculate_relevance(
        self,
        query: str,
        query_keywords: set,
        memory: MemoryEntry
    ) -> float:
        """
        Calculate relevance score (0.0-1.0)
        
        Factors:
        - Exact phrase match: +0.5
        - Keyword overlap: +0.3
        - Recency: +0.1
        - Access frequency: +0.1
        """
        score = 0.0
        
        # Exact phrase match
        if query in memory.content.lower():
            score += 0.5
        
        # Keyword overlap
        memory_keywords = set(memory.keywords)
        overlap = len(query_keywords & memory_keywords)
        if query_keywords:
            score += (overlap / len(query_keywords)) * 0.3
        
        # Recency bonus (memories from last hour get boost)
        age_hours = (time.time() - memory.timestamp) / 3600
        if age_hours < 1:
            score += 0.1 * (1 - age_hours)
        
        # Access frequency bonus
        if memory.access_count > 0:
            score += min(memory.access_count / 10.0, 0.1)
        
        return min(score, 1.0)
    
    def get_recent(self, category: str = None, limit: int = 10) -> List[MemoryEntry]:
        """Get recent memories"""
        with self.lock:
            memories = list(self.memories.values())
            
            # Filter by category
            if category:
                memories = [m for m in memories if m.category == category]
            
            # Sort by timestamp
            memories.sort(key=lambda m: m.timestamp, reverse=True)
            
            return memories[:limit]
    
    def forget(self, memory_id: str) -> bool:
        """Delete specific memory"""
        with self.lock:
            if memory_id in self.memories:
                del self.memories[memory_id]
                self.metadata["total_entries"] = len(self.memories)
                self._save()
                logger.info(f"🗑️  Forgot memory: {memory_id}")
                return True
            return False
    
    def cleanup_old_memories(self, max_age_days: int = 30):
        """
        Remove old, unimportant memories
        
        Keeps:
        - Important memories (importance > 0.7)
        - Recently accessed memories
        - Preferences (always keep)
        """
        with self.lock:
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 3600
            
            to_delete = []
            
            for mem_id, memory in self.memories.items():
                # Always keep preferences
                if memory.category == MemoryCategory.PREFERENCE:
                    continue
                
                # Keep important memories
                if memory.importance > 0.7:
                    continue
                
                # Keep recently accessed
                if memory.last_accessed > 0:
                    access_age = current_time - memory.last_accessed
                    if access_age < (7 * 24 * 3600):  # Accessed in last week
                        continue
                
                # Check age
                age = current_time - memory.timestamp
                if age > max_age_seconds and memory.access_count < 2:
                    to_delete.append(mem_id)
            
            # Delete old memories
            for mem_id in to_delete:
                del self.memories[mem_id]
            
            if to_delete:
                logger.info(f"🧹 Cleaned up {len(to_delete)} old memories")
                self.metadata["last_cleanup"] = current_time
                self.metadata["total_entries"] = len(self.memories)
                self._save()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get memory statistics"""
        with self.lock:
            stats = {
                "total": len(self.memories),
                "by_category": {},
                "most_accessed": [],
                "oldest": None,
                "newest": None
            }
            
            # Count by category
            for memory in self.memories.values():
                cat = memory.category
                stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            
            # Most accessed
            sorted_by_access = sorted(
                self.memories.values(),
                key=lambda m: m.access_count,
                reverse=True
            )
            stats["most_accessed"] = [
                {
                    "content": m.content[:50],
                    "access_count": m.access_count,
                    "category": m.category
                }
                for m in sorted_by_access[:5]
            ]
            
            # Age range
            if self.memories:
                all_memories = list(self.memories.values())
                stats["oldest"] = min(m.timestamp for m in all_memories)
                stats["newest"] = max(m.timestamp for m in all_memories)
            
            return stats
    
    def _load(self):
        """Load memories from disk"""
        if not self.memory_file.exists():
            logger.info("No existing memory file, starting fresh")
            return
        
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Load memories
            for mem_id, mem_data in data.get("memories", {}).items():
                self.memories[mem_id] = MemoryEntry.from_dict(mem_data)
            
            # Load metadata
            self.metadata = data.get("metadata", self.metadata)
            
            logger.info(f"✓ Loaded {len(self.memories)} memories from disk")
            
        except Exception as e:
            logger.error(f"Failed to load memories: {e}")
            logger.warning("Starting with empty memory")
    
    def _save(self):
        """Save memories to disk"""
        try:
            data = {
                "memories": {
                    mem_id: mem.to_dict()
                    for mem_id, mem in self.memories.items()
                },
                "metadata": self.metadata
            }
            
            # Write to temp file first (atomic write)
            temp_file = self.memory_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Rename (atomic on most systems)
            temp_file.replace(self.memory_file)
            
            logger.debug(f"💾 Saved {len(self.memories)} memories")
            
        except Exception as e:
            logger.error(f"Failed to save memories: {e}")
    
    def clear_category(self, category: str):
        """Clear all memories in a category"""
        with self.lock:
            to_delete = [
                mem_id for mem_id, mem in self.memories.items()
                if mem.category == category
            ]
            
            for mem_id in to_delete:
                del self.memories[mem_id]
            
            if to_delete:
                logger.info(f"🗑️  Cleared {len(to_delete)} {category} memories")
                self.metadata["total_entries"] = len(self.memories)
                self._save()
    
    def export_memories(self, output_file: Path = None) -> Dict:
        """Export memories to JSON"""
        with self.lock:
            output_file = output_file or Path("jarvis_memory_export.json")
            
            export_data = {
                "export_time": datetime.now().isoformat(),
                "total_memories": len(self.memories),
                "memories": [
                    {
                        **mem.to_dict(),
                        "created": datetime.fromtimestamp(mem.timestamp).isoformat(),
                        "last_accessed": datetime.fromtimestamp(mem.last_accessed).isoformat() if mem.last_accessed > 0 else None
                    }
                    for mem in self.memories.values()
                ],
                "statistics": self.get_statistics()
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"📤 Exported {len(self.memories)} memories to {output_file}")
            
            return export_data


# ============================================================================
# MEMORY INTEGRATION HELPERS
# ============================================================================

class MemoryManager:
    """
    High-level memory manager for voice assistant
    
    Provides simple interface for common memory operations
    """
    
    def __init__(self, memory: JarvisMemory):
        self.memory = memory
    
    def remember_user_preference(self, preference: str):
        """Store user preference"""
        self.memory.store(
            preference,
            category=MemoryCategory.PREFERENCE,
            importance=0.9  # High importance
        )
    
    def remember_fact(self, fact: str):
        """Store factual information"""
        self.memory.store(
            fact,
            category=MemoryCategory.FACT,
            importance=0.6
        )
    
    def remember_task(self, task: str):
        """Store completed task"""
        self.memory.store(
            task,
            category=MemoryCategory.TASK,
            importance=0.3  # Low importance (auto-cleanup)
        )
    
    def remember_screen_content(self, content: str):
        """Store screen content (OCR result)"""
        self.memory.store(
            f"Screen shows: {content}",
            category=MemoryCategory.SCREEN,
            importance=0.4  # Medium-low (recent context)
        )
    
    def what_do_i_prefer(self, about: str = None) -> Optional[str]:
        """Recall user preferences"""
        query = about or "preference"
        
        memories = self.memory.recall(
            query,
            category=MemoryCategory.PREFERENCE,
            limit=3
        )
        
        if memories:
            return memories[0].content
        return None
    
    def what_did_i_do(self, when: str = "recent") -> List[str]:
        """Recall recent tasks"""
        if when == "recent":
            memories = self.memory.get_recent(
                category=MemoryCategory.TASK,
                limit=5
            )
        else:
            memories = self.memory.recall(
                when,
                category=MemoryCategory.TASK,
                limit=5
            )
        
        return [m.content for m in memories]
    
    def whats_on_screen(self) -> Optional[str]:
        """Get most recent screen content"""
        memories = self.memory.get_recent(
            category=MemoryCategory.SCREEN,
            limit=1
        )
        
        if memories:
            return memories[0].content
        return None


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

# Global instance (initialized by voice service)
_global_memory: Optional[JarvisMemory] = None
_global_manager: Optional[MemoryManager] = None


def initialize_memory(memory_file: Path = None) -> JarvisMemory:
    """Initialize global memory instance"""
    global _global_memory, _global_manager
    
    _global_memory = JarvisMemory(memory_file)
    _global_manager = MemoryManager(_global_memory)
    
    return _global_memory


def get_memory() -> Optional[JarvisMemory]:
    """Get global memory instance"""
    return _global_memory


def get_memory_manager() -> Optional[MemoryManager]:
    """Get global memory manager"""
    return _global_manager


# ============================================================================
# EXPORT
# ============================================================================

__all__ = [
    'JarvisMemory',
    'MemoryManager',
    'MemoryEntry',
    'MemoryCategory',
    'initialize_memory',
    'get_memory',
    'get_memory_manager'
]
