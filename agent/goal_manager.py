"""
GOAL MANAGER — Multi-Step Goal Tracking with Persistence
=========================================================
[NEW: Phase 1 Architecture Fix]

Tracks multi-step user goals across turns. When a user says "book a flight"
the GoalManager records each sub-step (search → select → confirm → pay) and
advances through them as Jarvis completes each one.

Goals persist to disk in data/goals.json so they survive restarts.

Usage:
    from agent.goal_manager import goal_manager

    # Create a new goal:
    gid = goal_manager.create("book_flight", steps=[
        GoalStep(action="search_flights", description="Search for flights"),
        GoalStep(action="select_seat",    description="Select seat"),
        GoalStep(action="confirm_payment",description="Confirm payment"),
    ])

    # Advance after completing a step:
    goal_manager.advance(gid)

    # Check current step:
    goal = goal_manager.get(gid)
    current = goal.current_step()
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PERSIST_PATH = Path("data/goals.json")


# ════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════════════

class GoalStatus(Enum):
    """[NEW: Phase 1 Architecture Fix]"""
    ACTIVE    = "active"
    PAUSED    = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    FAILED    = "failed"


@dataclass
class GoalStep:
    """
    [NEW: Phase 1 Architecture Fix]
    A single step within a multi-step goal.
    """
    action:      str                        # Tool/intent name to execute
    description: str                        # Human-readable description
    params:      Dict[str, Any] = field(default_factory=dict)
    completed:   bool = False
    completed_at: Optional[float] = None

    def mark_done(self) -> None:
        self.completed    = True
        self.completed_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action":       self.action,
            "description":  self.description,
            "params":       self.params,
            "completed":    self.completed,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GoalStep":
        step = cls(
            action=d["action"],
            description=d["description"],
            params=d.get("params", {}),
            completed=d.get("completed", False),
            completed_at=d.get("completed_at"),
        )
        return step


@dataclass
class Goal:
    """
    [NEW: Phase 1 Architecture Fix]
    A multi-step user goal.
    """
    goal_id:    str
    name:       str
    steps:      List[GoalStep]
    status:     GoalStatus = GoalStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata:   Dict[str, Any] = field(default_factory=dict)

    # ── Step navigation ───────────────────────────────────────────────────

    def current_step(self) -> Optional[GoalStep]:
        """Return the first incomplete step, or None if all done."""
        for step in self.steps:
            if not step.completed:
                return step
        return None

    def current_step_index(self) -> int:
        """Return index of the first incomplete step, or len(steps) if done."""
        for i, step in enumerate(self.steps):
            if not step.completed:
                return i
        return len(self.steps)

    def advance(self) -> Optional[GoalStep]:
        """
        [NEW: Phase 1 Architecture Fix]
        Mark the current step as done and return the next step.
        Returns None if all steps are complete (goal finished).
        Automatically sets status to COMPLETED when last step is done.
        """
        step = self.current_step()
        if step is None:
            self.status     = GoalStatus.COMPLETED
            self.updated_at = time.time()
            return None

        step.mark_done()
        self.updated_at = time.time()

        next_step = self.current_step()
        if next_step is None:
            self.status = GoalStatus.COMPLETED
            logger.info(f"[GoalManager]  Goal '{self.name}' ({self.goal_id}) completed")

        return next_step

    def is_complete(self) -> bool:
        return self.status == GoalStatus.COMPLETED or all(s.completed for s in self.steps)

    def progress(self) -> str:
        done = sum(1 for s in self.steps if s.completed)
        return f"{done}/{len(self.steps)}"

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id":    self.goal_id,
            "name":       self.name,
            "steps":      [s.to_dict() for s in self.steps],
            "status":     self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata":   self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Goal":
        return cls(
            goal_id=d["goal_id"],
            name=d["name"],
            steps=[GoalStep.from_dict(s) for s in d.get("steps", [])],
            status=GoalStatus(d.get("status", "active")),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            metadata=d.get("metadata", {}),
        )


# ════════════════════════════════════════════════════════════════════════════
# GOAL MANAGER
# ════════════════════════════════════════════════════════════════════════════

class GoalManager:
    """
    [NEW: Phase 1 Architecture Fix]
    Thread-safe goal tracker with JSON persistence.

    Goals persist to data/goals.json and survive process restarts.
    """

    def __init__(self, persist_path: Path = _PERSIST_PATH):
        self._goals: Dict[str, Goal] = {}
        self._lock  = threading.RLock()
        self._persist_path = persist_path
        self._load()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create(
        self,
        name:     str,
        steps:    List[GoalStep],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        [NEW: Phase 1 Architecture Fix]
        Create a new goal and persist it. Returns the goal_id.
        """
        goal_id = str(uuid.uuid4())[:8]
        goal = Goal(
            goal_id=goal_id,
            name=name,
            steps=steps,
            metadata=metadata or {},
        )
        with self._lock:
            self._goals[goal_id] = goal
        self._save()
        logger.info(f"[GoalManager]  Created goal '{name}' ({goal_id}) with {len(steps)} steps")
        return goal_id

    def get(self, goal_id: str) -> Optional[Goal]:
        """Return goal by ID, or None."""
        with self._lock:
            return self._goals.get(goal_id)

    def advance(self, goal_id: str) -> Optional[GoalStep]:
        """
        [NEW: Phase 1 Architecture Fix]
        Mark the current step of the goal as done.
        Returns the next GoalStep, or None if the goal is now complete.
        """
        with self._lock:
            goal = self._goals.get(goal_id)
            if not goal:
                logger.warning(f"[GoalManager] advance() called for unknown goal '{goal_id}'")
                return None
            next_step = goal.advance()
        self._save()
        return next_step

    def abandon(self, goal_id: str, reason: str = "") -> None:
        """Mark a goal as abandoned."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal:
                goal.status     = GoalStatus.ABANDONED
                goal.updated_at = time.time()
                if reason:
                    goal.metadata["abandon_reason"] = reason
        self._save()
        logger.info(f"[GoalManager]  Goal '{goal_id}' abandoned: {reason}")

    def active_goals(self) -> List[Goal]:
        """Return all currently active goals."""
        with self._lock:
            return [g for g in self._goals.values() if g.status == GoalStatus.ACTIVE]

    def all_goals(self) -> List[Goal]:
        with self._lock:
            return list(self._goals.values())

    # ── Persistence ───────────────────────────────────────────────────────

    def _save(self) -> None:
        """[NEW: Phase 1 Architecture Fix] Persist goals to disk."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {gid: g.to_dict() for gid, g in self._goals.items()}
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"[GoalManager] Failed to persist goals: {e}")

    def _load(self) -> None:
        """[NEW: Phase 1 Architecture Fix] Load goals from disk on startup."""
        try:
            if not self._persist_path.exists():
                return
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for gid, d in data.items():
                    try:
                        self._goals[gid] = Goal.from_dict(d)
                    except Exception as e:
                        logger.warning(f"[GoalManager] Skipping corrupt goal '{gid}': {e}")
            loaded = len(self._goals)
            if loaded:
                active = len([g for g in self._goals.values() if g.status == GoalStatus.ACTIVE])
                logger.info(f"[GoalManager] Loaded {loaded} goals ({active} active) from disk")
        except Exception as e:
            logger.warning(f"[GoalManager] Failed to load persisted goals: {e}")


# ════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ════════════════════════════════════════════════════════════════════════════

# [NEW: Phase 1 Architecture Fix] — Global singleton
goal_manager = GoalManager()
