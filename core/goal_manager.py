"""
GOAL MANAGER
============
Persistent goal tracking with progress, dependencies, and status.
Goals survive across multiple commands and can be resumed.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import time
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class GoalStatus(Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"      # Waiting for external event
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Goal:
    """A goal Jarvis is working toward"""
    id: str
    description: str
    status: GoalStatus = GoalStatus.CREATED
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    steps_total: int = 0
    steps_completed: int = 0
    error: str = ""
    result_summary: str = ""

class GoalManager:
    """
    Manages active and completed goals.
    Goals persist to disk and can be resumed after restart.
    """
    
    def __init__(self, storage_path: str = "data/goals"):
        self._storage = Path(storage_path)
        self._storage.mkdir(parents=True, exist_ok=True)
        self._active_goals: Dict[str, Goal] = {}
        self._completed_goals: List[Goal] = []
        self._load_persisted()
        
    def create_goal(self, description: str) -> Goal:
        """Create a new goal."""
        import uuid
        goal_id = str(uuid.uuid4())[:8]
        
        goal = Goal(
            id=goal_id,
            description=description
        )
        
        self._active_goals[goal_id] = goal
        self._persist_goal(goal)
        
        logger.info(f"[GoalManager] Created: {goal_id} - {description}")
        return goal
    
    def update_progress(self, goal_id: str, steps_completed: int, steps_total: int):
        """Update goal progress."""
        if goal_id not in self._active_goals:
            return
        
        goal = self._active_goals[goal_id]
        goal.steps_completed = steps_completed
        goal.steps_total = steps_total
        goal.progress = steps_completed / max(steps_total, 1)
        goal.updated_at = time.time()
        
        self._persist_goal(goal)
    
    def complete_goal(self, goal_id: str, summary: str = ""):
        """Mark a goal as completed."""
        if goal_id not in self._active_goals:
            return
        
        goal = self._active_goals.pop(goal_id)
        goal.status = GoalStatus.COMPLETED
        goal.progress = 1.0
        goal.completed_at = time.time()
        goal.result_summary = summary
        
        self._completed_goals.append(goal)
        self._persist_goal(goal)
        
        logger.info(f"[GoalManager]  Completed: {goal_id}")
    
    def fail_goal(self, goal_id: str, error: str):
        """Mark a goal as failed."""
        if goal_id not in self._active_goals:
            return
        
        goal = self._active_goals.pop(goal_id)
        goal.status = GoalStatus.FAILED
        goal.error = error
        goal.updated_at = time.time()
        
        self._completed_goals.append(goal)
        self._persist_goal(goal)
        
        logger.warning(f"[GoalManager]  Failed: {goal_id} - {error}")
    
    def get_active_goals(self) -> List[Goal]:
        """Get all currently active goals."""
        return list(self._active_goals.values())
    
    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get a specific goal by ID."""
        return self._active_goals.get(goal_id)
    
    def _persist_goal(self, goal: Goal):
        """Save goal to disk."""
        try:
            filepath = self._storage / f"{goal.id}.json"
            data = {
                "id": goal.id,
                "description": goal.description,
                "status": goal.status.value,
                "progress": goal.progress,
                "created_at": goal.created_at,
                "updated_at": goal.updated_at,
                "completed_at": goal.completed_at,
                "steps_total": goal.steps_total,
                "steps_completed": goal.steps_completed,
                "error": goal.error,
                "result_summary": goal.result_summary
            }
            filepath.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"[GoalManager] Persist failed: {e}")
    
    def _load_persisted(self):
        """Load goals from disk at startup."""
        if not self._storage.exists():
            return
        
        for filepath in self._storage.glob("*.json"):
            try:
                data = json.loads(filepath.read_text())
                goal = Goal(
                    id=data["id"],
                    description=data["description"],
                    status=GoalStatus(data["status"]),
                    progress=data["progress"],
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    completed_at=data.get("completed_at"),
                    steps_total=data.get("steps_total", 0),
                    steps_completed=data.get("steps_completed", 0),
                    error=data.get("error", ""),
                    result_summary=data.get("result_summary", "")
                )
                
                if goal.status in (GoalStatus.COMPLETED, GoalStatus.FAILED):
                    self._completed_goals.append(goal)
                else:
                    self._active_goals[goal.id] = goal
                    
            except Exception as e:
                logger.warning(f"[GoalManager] Load failed for {filepath}: {e}")
        
        logger.info(f"[GoalManager] Loaded {len(self._active_goals)} active, {len(self._completed_goals)} completed goals")