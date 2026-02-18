# AgentPool Orchestrator

## Overview

`AgentPool` is the main entry point for running Claude SDK agents in parallel. It manages sandbox lifecycle, concurrency limits, and optional team coordination — all behind an async context manager.

Three execution modes:

| Mode | Use case | How agents interact |
|------|----------|---------------------|
| **Parallel** | Independent tasks | No interaction — each agent works alone |
| **Team** | Coordinated work | Shared TaskBoard + MessageBus via MCP tools |
| **Pipeline** | Sequential stages | Output from one stage feeds the next (see `Pipeline`) |

---

## System Flow

```
AgentPool
├── submit(Task)              # queue tasks
├── run()                     # parallel execution
│   ├── Semaphore(max_agents) # concurrency limit
│   └── _run_agent(task)      # per-agent lifecycle:
│       ├── _create_sandbox() #   create Local or Docker sandbox
│       ├── _build_mcp_config()#  coordination tools (team mode)
│       ├── run_session()     #   Claude SDK client session
│       └── cleanup           #   stop sandbox, release tasks
└── run_team()                # team execution
    ├── submit(lead)          #   coordinator agent
    └── submit(workers...)    #   worker agents
```

Each agent gets its own sandbox instance. The pool handles creation, error handling, timeout enforcement, and cleanup.

---

## Quick Start

### Parallel Mode (Default)

Independent tasks, no coordination:

```python
from agentpool import AgentPool, Task

async with AgentPool(max_agents=3) as pool:
    pool.submit(Task(prompt="Review auth module for security issues"))
    pool.submit(Task(prompt="Write integration tests for the API"))
    pool.submit(Task(prompt="Update the README"))
    results = await pool.run()

for r in results:
    print(f"{r.agent_id}: {r.status.value} ({r.duration_seconds:.1f}s)")
```

### Team Mode

Agents share a task board and communicate via messages:

```python
from agentpool import AgentPool

async with AgentPool(mode="team", max_agents=4) as pool:
    pool.add_tasks([
        "Implement user registration",
        "Add password reset flow",
        "Write auth middleware",
    ])
    results = await pool.run_team(
        lead_prompt="Coordinate these auth feature tasks...",
        worker_prompt="Claim and implement tasks from the board.",
    )
```

In team mode, each agent gets MCP tools for coordination:
- `claim_task` / `complete_task` / `list_tasks` — shared TaskBoard
- `send_message` / `broadcast_message` / `check_messages` — inter-agent messaging

See [TASKBOARD.md](TASKBOARD.md) and [MESSAGEBUS.md](MESSAGEBUS.md) for details.

### Per-Task Configuration

Override pool defaults on individual tasks:

```python
pool.submit(Task(
    prompt="Run the expensive analysis",
    model="claude-opus-4-5",        # override model
    sandbox="docker",               # override sandbox type
    timeout=600,                    # override timeout
    system_prompt="You are a code auditor.",
))
```

---

## Configuration

All settings have sensible defaults. Override via `AgentPoolConfig`:

```python
from agentpool import AgentPool, AgentPoolConfig, SandboxType, DockerConfig

config = AgentPoolConfig(
    max_agents=4,                           # max concurrent agents (hard cap: 8)
    default_sandbox=SandboxType.LOCAL,      # "local" or "docker"
    default_model="claude-sonnet-4-5",      # Claude model ID
    timeout=300,                            # seconds per agent session
    log_level="INFO",                       # logging level
    log_file=Path("agents.jsonl"),          # optional JSON lines performance log
    workspace_root=Path("data/workspaces"), # security: restrict workspace paths
    docker=DockerConfig(
        image="noclaw-worker:latest",
        memory_limit="1g",
        cpu_limit="1.0",
        network=None,                       # None = default bridge
    ),
)

async with AgentPool(config=config, workspace=Path("./project")) as pool:
    ...
```

### Constructor Shortcuts

For simple cases, skip the config object:

```python
# These are equivalent:
AgentPool(max_agents=3)
AgentPool(config=AgentPoolConfig(max_agents=3))
```

---

## Key Methods

### `submit(task) -> str`

Queue a task for execution. Returns the assigned `agent_id`. Tasks are not started until `run()` is called.

### `add_tasks(descriptions) -> list[str]`

Add tasks to the shared TaskBoard (team mode). Returns task IDs. These are different from submitted agent tasks — they're items on the board that agents claim during execution.

### `run() -> list[SessionResult]`

Execute all submitted tasks concurrently, respecting the `max_agents` semaphore. Returns one `SessionResult` per task.

### `run_team(lead_prompt, ...) -> list[SessionResult]`

Convenience method for team mode. Automatically creates a lead agent and N worker agents, then calls `run()`. The lead gets a system prompt instructing it to coordinate; workers get prompts to claim tasks from the board.

### `request_stop()`

Signal all agents to stop after their current work. Non-blocking.

---

## Agent Lifecycle

Each agent follows this lifecycle inside `_run_agent()`:

1. **Create sandbox** — `LocalSandbox` or `DockerSandbox` based on config
2. **Register on MessageBus** — agent gets an inbox for team communication
3. **Build MCP config** — in team mode, starts a coordination MCP server
4. **Run session** — `run_session()` creates a `ClaudeSDKClient`, sends the prompt, streams the response
5. **Cleanup** — stop sandbox, unregister from MessageBus, release uncompleted tasks

If an agent times out or errors, the pool catches it and returns a `SessionResult` with the appropriate status (`TIMEOUT` or `ERROR`). Uncompleted tasks on the TaskBoard are released back to `PENDING` so other agents (or a retry) can pick them up.

---

## SessionResult

Every agent returns a `SessionResult`:

```python
@dataclass
class SessionResult:
    agent_id: str
    status: SessionStatus    # COMPLETED, ERROR, TIMEOUT
    response: str            # agent's text output
    error: Optional[str]     # error message if failed
    model_used: str
    tokens_used: Optional[int]
    tool_uses: list[str]     # names of tools the agent called
    duration_seconds: float
```

---

## Event Callbacks

Monitor agent activity with an async callback:

```python
async def on_event(agent_id: str, event_type: str, data: dict):
    print(f"[{agent_id}] {event_type}: {data}")

async with AgentPool(event_callback=on_event) as pool:
    ...
```

Events emitted:
- `agent_started` — agent sandbox is up, session beginning
- `agent_complete` — agent finished (includes full result dict)

---

## Design Decisions

### 1. Semaphore-Based Concurrency

All tasks are launched as coroutines, gated by `asyncio.Semaphore(max_agents)`. This keeps the implementation simple — no thread pool, no process pool, just async concurrency with a cap.

### 2. Hard Cap at 8 Agents

`max_agents` is clamped to 8 regardless of what you pass. This prevents accidentally spinning up too many Claude SDK sessions, which are expensive and resource-heavy.

### 3. Fresh Pool per Request

The intended usage pattern is one `AgentPool` per logical operation (similar to a database session). Create it, submit tasks, run, read results, dispose. No shared state between runs.

### 4. Security Before Sandbox

`_create_sandbox()` calls `validate_workspace()` before creating any sandbox. If the workspace path is under a system directory (`/etc`, `/var`, etc.) or outside the allowed root, the agent is rejected immediately. See [SANDBOXES.md](SANDBOXES.md) for details.

---

## See Also

- [SANDBOXES.md](SANDBOXES.md) — Local and Docker sandbox details
- [TASKBOARD.md](TASKBOARD.md) — Shared task coordination
- [MESSAGEBUS.md](MESSAGEBUS.md) — Inter-agent messaging
