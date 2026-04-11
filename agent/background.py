"""
BACKGROUND TASK SYSTEM
=======================
Enables Jarvis to run long-duration tasks without blocking voice interaction.

Supports:
  - One-shot background tasks  (research, file generation, downloads)
  - Scheduled tasks            (reminders, daily briefings)
  - Repeating tasks            (check news every hour)
  - Continuous monitoring      (watch a stock, notify on condition)

Task lifecycle:
  PENDING → RUNNING → DONE | FAILED | CANCELLED

Voice commands:
  "What's the status of my research?"  → task_manager.get_status()
  "Cancel the reminder"                → task_manager.cancel(task_id)
  "What tasks are running?"            → task_manager.list_active()
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW    = 1
    NORMAL = 2
    HIGH   = 3


@dataclass
class Task:
    id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    progress: float = 0.0        # 0.0–1.0
    progress_message: str = ""   # human-readable progress
    notify_on_complete: bool = True
    scheduled_at: Optional[float] = None   # Unix timestamp for scheduled tasks
    repeat_interval: Optional[float] = None  # seconds

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        if self.started_at:
            return time.time() - self.started_at
        return None

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

    def summary(self) -> str:
        status_emoji = {
            TaskStatus.PENDING:   "⏳",
            TaskStatus.RUNNING:   "⚙️",
            TaskStatus.DONE:      "✅",
            TaskStatus.FAILED:    "❌",
            TaskStatus.CANCELLED: "🚫",
        }
        emoji = status_emoji.get(self.status, "?")
        dur = f" ({self.duration_seconds:.1f}s)" if self.duration_seconds else ""
        progress = f" [{self.progress*100:.0f}%]" if self.status == TaskStatus.RUNNING else ""
        return f"{emoji} {self.name}{progress}{dur}: {self.progress_message or self.status.value}"


class BackgroundTaskManager:
    """
    Manages all background tasks with an asyncio event loop.
    
    Thread-safe — can be called from voice process while tasks run.
    Notifies via callback when tasks complete.
    """

    def __init__(self, on_notify: Optional[Callable[[str], None]] = None):
        self._tasks: Dict[str, Task] = {}
        self._on_notify = on_notify  # Called when a task needs to speak a result
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running_handles: Dict[str, asyncio.Task] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop (called from voice process)."""
        self._loop = loop

    # ── PUBLIC API ─────────────────────────────────────────────────────────

    def submit(
        self,
        name: str,
        coro: Coroutine,
        description: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        notify: bool = True,
        scheduled_at: Optional[float] = None,
        repeat_interval: Optional[float] = None,
    ) -> str:
        """
        Submit a coroutine as a background task.
        Returns the task ID immediately (non-blocking).
        """
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            id=task_id,
            name=name,
            description=description,
            priority=priority,
            notify_on_complete=notify,
            scheduled_at=scheduled_at,
            repeat_interval=repeat_interval,
        )
        self._tasks[task_id] = task

        if self._loop and self._loop.is_running():
            handle = asyncio.run_coroutine_threadsafe(
                self._run_task(task_id, coro),
                self._loop
            )
            logger.info(f"📋 Task submitted: [{task_id}] {name}")
        else:
            logger.warning(f"No event loop — task queued: {name}")

        return task_id

    def cancel(self, task_id: str) -> bool:
        """Cancel a running or pending task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False

        # Cancel the asyncio task
        handle = self._running_handles.get(task_id)
        if handle:
            handle.cancel()

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        logger.info(f"🚫 Task cancelled: [{task_id}] {task.name}")
        return True

    def get_status(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_active(self) -> List[Task]:
        return [t for t in self._tasks.values() if t.is_active]

    def list_all(self) -> List[Task]:
        return list(self._tasks.values())

    def get_status_summary(self) -> str:
        """Human-readable summary of all tasks."""
        active = self.list_active()
        if not active:
            return "No active tasks, Sir."
        lines = ["Active tasks:"]
        for t in active:
            lines.append(f"  • {t.summary()}")
        return "\n".join(lines)

    def update_progress(self, task_id: str, progress: float, message: str = ""):
        """Update task progress (called from within a running coroutine)."""
        task = self._tasks.get(task_id)
        if task:
            task.progress = max(0.0, min(1.0, progress))
            task.progress_message = message

    # ── INTERNAL ───────────────────────────────────────────────────────────

    async def _run_task(self, task_id: str, coro: Coroutine):
        """Wrap and execute a task coroutine with lifecycle management."""
        task = self._tasks[task_id]

        if task.scheduled_at:
            delay = task.scheduled_at - time.time()
            if delay > 0:
                task.progress_message = f"Scheduled in {delay:.0f}s"
                await asyncio.sleep(delay)

        try:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            logger.info(f"▶ Task running: [{task_id}] {task.name}")

            # Wait for the research to finish
            result = await coro

            task.result = result
            task.status = TaskStatus.DONE
            task.progress = 1.0
            task.completed_at = time.time()
            task.progress_message = "Complete"
            
            logger.info(f"✅ Task done: [{task_id}] {task.name} ({task.duration_seconds:.1f}s)")

            # Force TTS Notification Even If Loop Is Busy
            if task.notify_on_complete and self._on_notify:
                message = self._build_completion_message(task)
                # Ensure the message is sent
                try:
                    self._on_notify(message)
                except Exception as e:
                    logger.error(f"Failed to trigger TTS notification: {e}")

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            task.progress_message = f"Failed: {e}"
            logger.error(f"❌ Task failed: [{task_id}] {task.name}: {e}")

            if task.notify_on_complete and self._on_notify:
                self._on_notify(f"The background task encountered an error, Sir. {str(e)[:80]}")

        finally:
            self._running_handles.pop(task_id, None)

    def _build_completion_message(self, task: Task) -> str:
        """Build a natural speech notification for task completion."""
        dur = task.duration_seconds
        dur_str = f" It took {dur:.0f} seconds." if dur and dur > 5 else ""

        result = task.result
        if isinstance(result, dict):
            # Research result
            if "synthesis" in result:
                synopsis = result["synthesis"][:200]
                return f"Research complete, Sir.{dur_str} Here's a summary: {synopsis}"
            if "answer" in result:
                return f"{task.name} complete, Sir.{dur_str} {result['answer'][:150]}"
            if "saved_to" in result:
                return f"File saved to {result['saved_to']}, Sir.{dur_str}"

        return f"Task '{task.name}' is complete, Sir.{dur_str}"


# ── COMMON BACKGROUND TASK FACTORIES ──────────────────────────────────────

async def background_research_task(
    topic: str,
    task_manager: BackgroundTaskManager,
    task_id: str,
    groq_api_key: str,
    output_format: str = "spoken"
) -> Dict:
    """
    Full deep research as a background task.
    Updates progress as it runs.
    """
    from executor.researcher import DeepResearcher
    
    researcher = DeepResearcher(groq_api_key=groq_api_key)
    result = await researcher.research(
        topic=topic,
        task_manager=task_manager,
        task_id=task_id,
        max_sources=8,
    )
    
    return {
        "topic": result.topic,
        "synthesis": result.synthesis,
        "full_report": result.full_report,
        "sources_count": result.sources_count,
        "sources": [s["title"] for s in result.sources[:5]],
        "key_facts": result.key_facts,
        "confidence": result.confidence,
    }


async def background_reminder_task(
    message: str,
    delay_seconds: float,
    task_manager: BackgroundTaskManager,
    task_id: str,
) -> Dict:
    """Fire a reminder after a delay."""
    total = delay_seconds
    interval = min(60, total / 10)
    elapsed = 0

    while elapsed < total:
        await asyncio.sleep(interval)
        elapsed += interval
        progress = elapsed / total
        remaining = total - elapsed
        task_manager.update_progress(
            task_id, progress,
            f"Reminder in {remaining:.0f}s"
        )

    return {"reminder": message, "fired_at": time.time()}