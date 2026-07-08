"""
TASK GRAPH ENGINE
=================
Converts flat plan steps into a dependency-aware graph.
This is what enables: "do steps 1 and 2 in parallel, then step 3"
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class NodeState(Enum):
    PENDING = "pending"
    READY = "ready"        # Dependencies met, can execute
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class TaskNode:
    """A single node in the task graph"""
    id: str
    action: str
    tool: str
    params: Dict = field(default_factory=dict)
    description: str = ""
    depends_on: List[str] = field(default_factory=list)  # Node IDs this depends on
    state: NodeState = NodeState.PENDING
    result: Optional[Dict] = None
    retry_count: int = 0
    max_retries: int = 1

class TaskGraph:
    """
    Manages a graph of tasks with dependencies.
    
    The executor can ask: "what's ready to run?"
    And the graph returns nodes whose dependencies are all completed.
    """
    
    def __init__(self, plan: List[Dict]):
        self.nodes: Dict[str, TaskNode] = {}
        self._build_from_plan(plan)
        
    def _build_from_plan(self, plan: List[Dict]):
        """Convert a flat plan into a dependency graph."""
        for i, step in enumerate(plan):
            node_id = step.get("id", f"step_{i}")
            depends = step.get("depends_on", [])
            
            # Convert index-based deps to ID-based
            if depends and isinstance(depends[0], int):
                depends = [f"step_{d}" for d in depends]
            
            self.nodes[node_id] = TaskNode(
                id=node_id,
                action=step.get("action", ""),
                tool=step.get("tool", ""),
                params=step.get("params", {}),
                description=step.get("description", ""),
                depends_on=depends,
                max_retries=step.get("retry_policy", {}).get("max_retries", 1)
            )
        
        # Set initial state
        for node in self.nodes.values():
            if not node.depends_on:
                node.state = NodeState.READY
        
        logger.info(f"[TaskGraph] Built graph with {len(self.nodes)} nodes")
    
    def get_ready_nodes(self) -> List[TaskNode]:
        """Get all nodes that are ready to execute."""
        return [n for n in self.nodes.values() if n.state == NodeState.READY]
    
    def get_parallel_groups(self) -> List[List[TaskNode]]:
        """
        Group ready nodes by what can run in parallel.
        Nodes with no dependencies between them can run together.
        """
        ready = self.get_ready_nodes()
        if len(ready) <= 1:
            return [ready] if ready else []
        
        # Simple: all ready nodes that don't depend on each other
        # run in parallel
        return [ready]
    
    def mark_completed(self, node_id: str, result: Dict):
        """Mark a node as completed and unlock dependents."""
        if node_id not in self.nodes:
            return
        
        node = self.nodes[node_id]
        node.state = NodeState.COMPLETED
        node.result = result
        
        # Unlock nodes that depend on this one
        for dependent in self.nodes.values():
            if node_id in dependent.depends_on:
                # Check if ALL dependencies are now completed
                all_deps_met = all(
                    self.nodes[dep].state == NodeState.COMPLETED
                    for dep in dependent.depends_on
                )
                if all_deps_met:
                    dependent.state = NodeState.READY
                    logger.debug(f"[TaskGraph] Unlocked: {dependent.id}")
    
    def mark_failed(self, node_id: str, error: str):
        """Mark a node as failed."""
        if node_id not in self.nodes:
            return
        
        node = self.nodes[node_id]
        node.retry_count += 1
        
        if node.retry_count <= node.max_retries:
            node.state = NodeState.READY  # Retry
            logger.info(f"[TaskGraph] Retrying {node_id} ({node.retry_count}/{node.max_retries})")
        else:
            node.state = NodeState.FAILED
            node.result = {"error": error}
            logger.warning(f"[TaskGraph] Failed: {node_id}")
    
    def is_complete(self) -> bool:
        """Check if all nodes are in a terminal state."""
        terminal = {NodeState.COMPLETED, NodeState.FAILED, NodeState.SKIPPED}
        return all(n.state in terminal for n in self.nodes.values())
    
    def is_successful(self) -> bool:
        """Check if all nodes completed successfully."""
        return all(n.state == NodeState.COMPLETED for n in self.nodes.values())
    
    def progress(self) -> float:
        """Get completion progress 0.0 to 1.0."""
        if not self.nodes:
            return 1.0
        completed = sum(1 for n in self.nodes.values() if n.state == NodeState.COMPLETED)
        return completed / len(self.nodes)
    
    def summary(self) -> str:
        """Human-readable summary of graph state."""
        lines = []
        for node in self.nodes.values():
            icon = {
                NodeState.COMPLETED: "[OK]",
                NodeState.FAILED: "[FAIL]",
                NodeState.RUNNING: "[...]",
                NodeState.READY: "[WAIT]",
                NodeState.PENDING: "[HOLD]",
                NodeState.SKIPPED: "[SKIP]"
            }.get(node.state, "[?]")
            lines.append(f"  {icon} {node.description}")
        return "\n".join(lines)