# TaskBoard

## Overview

`TaskBoard` is a shared task list that agents use to coordinate work in **team mode**. Agents claim tasks atomically, implement them, and mark them complete. File locking ensures no two agents claim the same task.

```
                    TaskBoard
┌───────────────────────────────────────────┐
│ ┌─────────┐ ┌──────────────┐ ┌─────────┐ │
│ │ PENDING  │ │ IN_PROGRESS  │ │COMPLETED│ │
│ │          │ │              │ │         │ │
│ │ task-3   │ │ task-1       │ │ task-2  │ │
│ │ task-5   │ │ (worker-1)   │ │         │ │
│ │          │ │ task-4       │ │         │ │
│ │          │ │ (worker-2)   │ │         │ │
│ └─────────┘ └──────────────┘ └─────────┘ │
└───────────────────────────────────────────┘
         ↑           ↑              ↑
      claim()    implement     complete()
```

---

## Quick Start

### With AgentPool (Typical)

In team mode, the pool manages the TaskBoard. Agents interact with it via MCP tools:

```python
from agentpool import AgentPool

async with AgentPool(mode="team", max_agents=4) as pool:
    # Add tasks to the shared board
    pool.add_tasks([
        "Implement user registration endpoint",
        "Add input validation to forms",
        "Write unit tests for auth module",
    ])

    # Lead coordinates, workers claim tasks
    results = await pool.run_team(
        lead_prompt="Break down and coordinate the auth feature",
    )
```

Agents see these MCP tools during their session:
- `claim_task` — get the next available task
- `complete_task` — mark a task as done (with result summary)
- `fail_task` — mark a task as failed (with error)
- `list_tasks` — see all tasks and their status

### Direct Python Usage

```python
from agentpool import TaskBoard

board = TaskBoard(state_dir=Path("/tmp/my-board"))

# Add tasks
board.add("Write the login endpoint", priority=2)
board.add("Write tests for login", depends_on=["<login-task-id>"])
board.add("Update API docs")

# Agent claims next available task
task = board.claim("worker-1")
if task:
    print(f"Claimed: {task.description}")
    # ... do the work ...
    board.complete(task.id, result="Login endpoint implemented")
```

---

## Task Lifecycle

```
   add()          claim()         complete()
PENDING ──────→ IN_PROGRESS ──────→ COMPLETED
                     │
                     │ fail()
                     └──────→ FAILED
                     │
                     │ release() / stale timeout / agent crash
                     └──────→ PENDING (re-claimable)
```

### States

| Status | Meaning |
|--------|---------|
| `PENDING` | Available to be claimed |
| `IN_PROGRESS` | Claimed by an agent, work underway |
| `COMPLETED` | Finished successfully |
| `FAILED` | Agent reported failure |

---

## API

### `add(description, depends_on=None, priority=0) -> str`

Create a new task on the board. Returns the task ID (8-char UUID).

- `depends_on` — list of task IDs that must be `COMPLETED` before this task can be claimed
- `priority` — higher values are claimed first (default 0)

### `claim(agent_id) -> Optional[BoardTask]`

Atomically claim the next available task. A task is available if:
1. Status is `PENDING`
2. All dependencies are `COMPLETED`
3. Not assigned to another agent

Returns the claimed `BoardTask` or `None` if nothing is available. Tasks are sorted by priority (highest first), then by creation time (oldest first).

### `complete(task_id, result=None)`

Mark a task as `COMPLETED` with an optional result summary.

### `fail(task_id, error)`

Mark a task as `FAILED` with an error description.

### `release(task_id)`

Release a claimed task back to `PENDING`. Only works on `IN_PROGRESS` tasks.

### `release_agent_tasks(agent_id) -> list[str]`

Release all `IN_PROGRESS` tasks assigned to a specific agent. Used for cleanup when an agent crashes or times out. Returns the list of released task IDs.

### `status() -> list[dict]`

Get all tasks with their current status. Auto-reloads from disk if file-backed.

### Properties

- `pending_count` — number of `PENDING` tasks
- `completed_count` — number of `COMPLETED` tasks
- `all_done` — `True` when no tasks are `PENDING` or `IN_PROGRESS`

---

## BoardTask

```python
@dataclass
class BoardTask:
    id: str                           # 8-char UUID
    description: str                  # what needs to be done
    status: TaskStatus                # PENDING, IN_PROGRESS, COMPLETED, FAILED
    assigned_to: Optional[str]        # agent_id of current owner
    depends_on: list[str]             # task IDs that must complete first
    result: Optional[str]             # completion summary or error message
    priority: int                     # higher = claimed first
    created_at: float                 # timestamp
    claimed_at: Optional[float]       # when claimed
    completed_at: Optional[float]     # when completed/failed
```

---

## File Locking

The TaskBoard uses `fcntl.flock` for atomic operations. This prevents race conditions when multiple agents (running as separate MCP server processes) try to claim tasks simultaneously.

### How It Works

```
Agent A: claim()                Agent B: claim()
    │                               │
    ├─ acquire lock ←───────────── waits...
    ├─ reload from disk              │
    ├─ find available task           │
    ├─ mark as IN_PROGRESS           │
    ├─ save to disk                  │
    └─ release lock ────────────→ acquires lock
                                    ├─ reload from disk
                                    ├─ (task A claimed is now IN_PROGRESS)
                                    ├─ find NEXT available task
                                    ├─ mark as IN_PROGRESS
                                    ├─ save to disk
                                    └─ release lock
```

Two files in the state directory:
- `taskboard.json` — task data
- `taskboard.lock` — lock file for `fcntl.flock`

### The `_lock_held` Parameter

`_save()` accepts `_lock_held=True` to skip re-acquiring the lock when the caller already holds it. This prevents deadlock in `claim()`, which acquires the lock, reloads, mutates, and saves — all in one critical section.

---

## Stale Claim Recovery

If an agent crashes while holding a task, that task would be stuck in `IN_PROGRESS` forever. The stale recovery mechanism prevents this.

When `stale_timeout` is set (automatically configured from `config.timeout` in team mode), every `claim()` call sweeps for stale tasks:

```python
board = TaskBoard(state_dir=path, stale_timeout=300)
# Tasks claimed > 300 seconds ago and not completed are reverted to PENDING
```

The sweep runs inside the claim lock, so it's atomic. Stale tasks get their `assigned_to` and `claimed_at` cleared, making them available for other agents.

### Agent Cleanup

In addition to stale recovery, `AgentPool` calls `release_agent_tasks()` when an agent exits (success, error, or timeout). This immediately releases any tasks the agent had claimed, without waiting for the stale timeout.

---

## Dependencies

Tasks can depend on other tasks:

```python
t1 = board.add("Set up database schema")
t2 = board.add("Implement user model", depends_on=[t1])
t3 = board.add("Write user API endpoints", depends_on=[t2])
t4 = board.add("Write API tests", depends_on=[t3])
```

A task with unmet dependencies cannot be claimed — it stays `PENDING` until all dependencies are `COMPLETED`. This lets you define a DAG of work within the shared board.

---

## Persistence

### File-Backed (Default in AgentPool)

When `state_dir` is provided, the board persists to `taskboard.json`:

```json
{
  "tasks": [
    {
      "id": "a1b2c3d4",
      "description": "Implement user registration",
      "status": "completed",
      "assigned_to": "worker-1",
      "depends_on": [],
      "result": "Endpoint created at POST /api/users",
      "priority": 0,
      "created_at": 1708300000.0,
      "claimed_at": 1708300001.0,
      "completed_at": 1708300045.0
    }
  ]
}
```

### In-Memory Only

If `state_dir` is `None`, the board is purely in-memory. No persistence, no file locking. Useful for tests or single-process usage.

---

## Gotchas

1. **`fcntl` is Unix-only** — file locking uses `fcntl.flock`, which works on macOS and Linux but not Windows.

2. **`claim()` reloads from disk** — every claim re-reads `taskboard.json` under lock to pick up changes from other processes. This is necessary because each MCP server instance has its own `TaskBoard` object.

3. **`status()` auto-reloads** — calling `status()` or any property (`pending_count`, etc.) triggers a disk reload. This keeps the view current but adds I/O on every check.

4. **`_save(_lock_held=True)`** — the internal locking parameter exists specifically for `claim()` and `_mutate_under_lock()` to avoid deadlocking on the same file lock. Don't use it from outside the class.

5. **Task IDs are 8-char UUIDs** — short enough for agents to reference in messages, but collisions are theoretically possible at very high volume.

---

## See Also

- [AGENTPOOL.md](AGENTPOOL.md) — How the pool manages the TaskBoard
- [MESSAGEBUS.md](MESSAGEBUS.md) — Inter-agent messaging (the other half of team mode)
- [SANDBOXES.md](SANDBOXES.md) — Where agent commands execute
