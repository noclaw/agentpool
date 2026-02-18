"""
TaskBoard — shared task list for agent coordination.

In-memory with file-backed persistence for crash recovery.
Uses file locking for atomic task claiming (same approach as Claude Code Agent Teams).

Agents interact with the TaskBoard via MCP tools exposed by the coordination server.
"""

import json
import fcntl
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

from .logging import get_logger

logger = get_logger("tasks")


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BoardTask:
    """A task on the shared TaskBoard."""
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[str] = None  # agent_id
    depends_on: List[str] = field(default_factory=list)  # task IDs
    result: Optional[str] = None
    priority: int = 0  # higher = claimed first
    created_at: float = field(default_factory=time.time)
    claimed_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "depends_on": self.depends_on,
            "result": self.result,
            "priority": self.priority,
            "created_at": self.created_at,
            "claimed_at": self.claimed_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoardTask":
        return cls(
            id=data["id"],
            description=data["description"],
            status=TaskStatus(data["status"]),
            assigned_to=data.get("assigned_to"),
            depends_on=data.get("depends_on", []),
            result=data.get("result"),
            priority=data.get("priority", 0),
            created_at=data.get("created_at", time.time()),
            claimed_at=data.get("claimed_at"),
            completed_at=data.get("completed_at"),
        )


class TaskBoard:
    """
    Shared task list with atomic claiming via file locks.

    Thread-safe and process-safe. Multiple agents can concurrently
    claim tasks without conflicts.
    """

    def __init__(self, state_dir: Optional[Path] = None, stale_timeout: Optional[int] = None):
        """
        Args:
            state_dir: Directory for persistence files. If None, in-memory only.
            stale_timeout: Seconds after which an IN_PROGRESS task with no
                completion is considered stale and reverted to PENDING.
                None disables stale recovery (default).
        """
        self._tasks: Dict[str, BoardTask] = {}
        self._state_dir = state_dir
        self._stale_timeout = stale_timeout
        self._state_file: Optional[Path] = None
        self._lock_file: Optional[Path] = None

        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._state_file = state_dir / "taskboard.json"
            self._lock_file = state_dir / "taskboard.lock"
            self._load()

    def _load(self) -> None:
        """Load state from file."""
        if self._state_file and self._state_file.exists():
            data = json.loads(self._state_file.read_text())
            self._tasks = {
                t["id"]: BoardTask.from_dict(t) for t in data.get("tasks", [])
            }

    def reload(self) -> None:
        """Reload state from disk. No-op if in-memory only."""
        if self._state_file:
            if self._lock_file:
                with open(self._lock_file, "w") as lock_fd:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    try:
                        self._load()
                    finally:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
            else:
                self._load()

    def _save(self, _lock_held: bool = False) -> None:
        """
        Persist state to file.

        Args:
            _lock_held: If True, skip locking (caller already holds the lock).
        """
        if not self._state_file:
            return

        data = {"tasks": [t.to_dict() for t in self._tasks.values()]}

        if _lock_held:
            self._state_file.write_text(json.dumps(data, indent=2))
        else:
            with open(self._lock_file, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    self._state_file.write_text(json.dumps(data, indent=2))
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _mutate_under_lock(self, task_id: str, mutator) -> None:
        """
        Reload from disk, apply a mutation to a task, and save — all under lock.

        Used by complete() and fail() to avoid overwriting concurrent changes
        from other processes (e.g., MCP server instances).
        """
        if self._lock_file:
            with open(self._lock_file, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    self._load()
                    task = self._tasks.get(task_id)
                    if not task:
                        raise ValueError(f"Task not found: {task_id}")
                    mutator(task)
                    self._save(_lock_held=True)
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        else:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")
            mutator(task)

    def add(
        self,
        description: str,
        depends_on: Optional[List[str]] = None,
        priority: int = 0,
    ) -> str:
        """
        Add a task to the board.

        Args:
            description: What needs to be done
            depends_on: List of task IDs that must complete first
            priority: Higher values are claimed first (default 0)

        Returns:
            The new task's ID
        """
        task_id = str(uuid4())[:8]
        task = BoardTask(
            id=task_id,
            description=description,
            depends_on=depends_on or [],
            priority=priority,
        )
        self._tasks[task_id] = task
        self._save()
        logger.info(f"Task added: {task_id} — {description[:60]}")
        return task_id

    def claim(self, agent_id: str) -> Optional[BoardTask]:
        """
        Atomically claim the next available task.

        A task is available if:
        - Status is PENDING
        - All dependencies are COMPLETED
        - Not assigned to another agent

        Args:
            agent_id: The agent claiming the task

        Returns:
            The claimed BoardTask, or None if nothing available
        """
        if self._lock_file:
            with open(self._lock_file, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    # Reload state (another process may have changed it)
                    self._load()
                    task = self._claim_internal(agent_id)
                    self._save(_lock_held=True)
                    return task
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        else:
            return self._claim_internal(agent_id)

    def _claim_internal(self, agent_id: str) -> Optional[BoardTask]:
        """Internal claim logic (must be called under lock)."""
        # Sweep stale IN_PROGRESS tasks back to PENDING
        if self._stale_timeout is not None:
            now = time.time()
            for task in self._tasks.values():
                if (
                    task.status == TaskStatus.IN_PROGRESS
                    and task.claimed_at is not None
                    and (now - task.claimed_at) > self._stale_timeout
                ):
                    logger.warning(
                        f"Task {task.id} stale (claimed {now - task.claimed_at:.0f}s ago "
                        f"by {task.assigned_to}), reverting to PENDING"
                    )
                    task.status = TaskStatus.PENDING
                    task.assigned_to = None
                    task.claimed_at = None

        completed_ids = {
            t.id for t in self._tasks.values()
            if t.status == TaskStatus.COMPLETED
        }

        # Sort by priority (highest first), then by creation time (oldest first)
        candidates = sorted(
            self._tasks.values(),
            key=lambda t: (-t.priority, t.created_at),
        )

        for task in candidates:
            if task.status != TaskStatus.PENDING:
                continue
            # Check dependencies
            if all(dep in completed_ids for dep in task.depends_on):
                task.status = TaskStatus.IN_PROGRESS
                task.assigned_to = agent_id
                task.claimed_at = time.time()
                logger.info(
                    f"Task {task.id} claimed by {agent_id}: {task.description[:60]}"
                )
                return task

        return None

    def complete(self, task_id: str, result: Optional[str] = None) -> None:
        """Mark a task as completed. Reloads from disk under lock to avoid data races."""
        def _do(task: BoardTask):
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()

        self._mutate_under_lock(task_id, _do)
        logger.info(f"Task {task_id} completed")

    def release(self, task_id: str) -> None:
        """Release a claimed task back to PENDING so another agent can claim it."""
        def _do(task: BoardTask):
            if task.status != TaskStatus.IN_PROGRESS:
                raise ValueError(
                    f"Cannot release task {task_id}: status is {task.status.value}, not in_progress"
                )
            task.status = TaskStatus.PENDING
            task.assigned_to = None
            task.claimed_at = None

        self._mutate_under_lock(task_id, _do)
        logger.info(f"Task {task_id} released back to PENDING")

    def release_agent_tasks(self, agent_id: str) -> List[str]:
        """Release all IN_PROGRESS tasks assigned to a specific agent.

        Used for cleanup when an agent crashes or times out.

        Returns:
            List of task IDs that were released.
        """
        released = []
        if self._lock_file:
            with open(self._lock_file, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    self._load()
                    for task in self._tasks.values():
                        if task.status == TaskStatus.IN_PROGRESS and task.assigned_to == agent_id:
                            task.status = TaskStatus.PENDING
                            task.assigned_to = None
                            task.claimed_at = None
                            released.append(task.id)
                    if released:
                        self._save(_lock_held=True)
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        else:
            for task in self._tasks.values():
                if task.status == TaskStatus.IN_PROGRESS and task.assigned_to == agent_id:
                    task.status = TaskStatus.PENDING
                    task.assigned_to = None
                    task.claimed_at = None
                    released.append(task.id)

        if released:
            logger.info(f"Released {len(released)} tasks from agent {agent_id}: {released}")
        return released

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as failed. Reloads from disk under lock to avoid data races."""
        def _do(task: BoardTask):
            task.status = TaskStatus.FAILED
            task.result = error
            task.completed_at = time.time()

        self._mutate_under_lock(task_id, _do)
        logger.warning(f"Task {task_id} failed: {error[:80]}")

    def status(self) -> List[Dict[str, Any]]:
        """Get the status of all tasks. Auto-reloads from disk if file-backed."""
        if self._state_file:
            self.reload()
        return [t.to_dict() for t in self._tasks.values()]

    @property
    def pending_count(self) -> int:
        if self._state_file:
            self.reload()
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    @property
    def completed_count(self) -> int:
        if self._state_file:
            self.reload()
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)

    @property
    def all_done(self) -> bool:
        """True if no tasks are pending or in_progress."""
        if self._state_file:
            self.reload()
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in self._tasks.values()
        ) and len(self._tasks) > 0
