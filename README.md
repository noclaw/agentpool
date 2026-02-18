# agentpool

Claude SDK agent orchestration. Run multiple agents in parallel, coordinate teams with shared task boards, or chain sequential stages in a pipeline — with optional Docker sandboxing.

## Quick Start

```python
from agentpool import AgentPool, Task

async with AgentPool(max_agents=3) as pool:
    pool.submit(Task(prompt="Review auth module for security issues"))
    pool.submit(Task(prompt="Write integration tests for the API"))
    results = await pool.run()

for r in results:
    print(f"{r.agent_id}: {r.status} ({r.duration_seconds:.1f}s)")
```

## Execution Modes

### Parallel — independent tasks, no communication

```python
async with AgentPool(max_agents=3) as pool:
    pool.submit(Task(prompt="Review security", sandbox="docker"))
    pool.submit(Task(prompt="Write tests", sandbox="local"))
    results = await pool.run()
```

### Team — agents share a task board and can message each other

```python
async with AgentPool(mode="team", max_agents=4) as pool:
    pool.add_tasks([
        "Implement user registration",
        "Add password reset flow",
        "Write auth middleware",
        "Add session management",
    ])
    results = await pool.run_team(
        lead_prompt="Break down and coordinate these auth tasks.",
        num_workers=3,
    )
```

### Pipeline — sequential stages where each output feeds the next

```python
from agentpool import Pipeline, Stage

pipeline = Pipeline([
    Stage("research", prompt="Investigate the auth module for security issues."),
    Stage("plan", prompt="Based on this research:\n{previous_response}\n\nCreate a remediation plan."),
    Stage("implement", prompt="Implement this plan:\n{previous_response}", sandbox="docker"),
])
result = await pipeline.run()

print(result.final_response)  # last stage's output
print(result.success)         # True if all stages completed
print(result.total_duration)  # sum of all stage durations
```

Each stage can override model, sandbox, timeout, and system prompt. Use `{previous_response}` in the prompt template to inject the prior stage's output, or omit it and the context is appended automatically.

Optional `transform` function to reshape output between stages:

```python
Stage(
    "summarize",
    prompt="Key findings: {previous_response}",
    transform=lambda resp: resp[:500],  # truncate to 500 chars
)
```

In team mode, agents get MCP tools for coordination:

| Tool | Description |
|------|-------------|
| `claim_task` | Claim the next available task from the board |
| `complete_task` | Mark a task as done |
| `list_tasks` | See all tasks and their status |
| `send_message` | Send a message to another agent |
| `broadcast_message` | Send to all agents |
| `check_messages` | Check inbox for messages |

## Sandboxes

Each agent runs in a sandbox. Two types:

- **`local`** — runs directly on the host. No isolation, fast. Good for development.
- **`docker`** — persistent Docker container with workspace mounted at `/workspace`. Commands via `docker exec`. The agent itself runs on the host; only shell commands execute inside the container.

```python
from agentpool import AgentPoolConfig, SandboxType, DockerConfig

config = AgentPoolConfig(
    default_sandbox=SandboxType.DOCKER,
    docker=DockerConfig(
        image="noclaw-worker:latest",
        memory_limit="2g",
        cpu_limit="1.0",
    ),
)

async with AgentPool(config=config) as pool:
    pool.submit(Task(prompt="...", sandbox="docker"))  # per-task override
    pool.submit(Task(prompt="...", sandbox="local"))
    results = await pool.run()
```

Workspace paths are validated before mounting — system directories (`/etc`, `/var`, `/usr`, etc.) and root are blocked. Set `workspace_root` in config to restrict workspaces to a specific directory tree.

Docker containers run with `--security-opt no-new-privileges` by default. Additional hardening can be applied by customizing the `docker run` command in `DockerSandbox.start()`:

- `--read-only` — read-only root filesystem (only `/workspace` writable)
- `--cap-drop=ALL` — drop all Linux capabilities
- `--pids-limit=256` — prevent fork bombs
- `--network none` — disable networking
- `--security-opt seccomp=<profile>` — restrict system calls

## Configuration

All settings have defaults. Override what you need:

```python
from agentpool import AgentPoolConfig

config = AgentPoolConfig(
    max_agents=4,              # concurrent agent limit (hard cap: 8)
    default_model="claude-sonnet-4-5",
    default_sandbox=SandboxType.LOCAL,
    timeout=300,               # seconds per agent session
    log_level="INFO",          # DEBUG, INFO, WARNING, ERROR
    log_file=Path("agents.jsonl"),  # optional JSON lines log for analysis
)
```

## Task Dependencies

Tasks on the board can depend on other tasks:

```python
pool.add_tasks(["Set up database schema"])  # returns ["abc123"]
board = pool.task_board
t2 = board.add("Write API endpoints", depends_on=["abc123"])
# t2 won't be claimable until abc123 is completed
```

## Event Callbacks

Monitor agent lifecycle events:

```python
async def on_event(agent_id: str, event_type: str, data: dict):
    print(f"[{agent_id}] {event_type}: {data}")

pool = AgentPool(event_callback=on_event)
```

Events: `agent_started`, `agent_complete`.

## Architecture

```
AgentPool (parallel & team modes)
├── TaskBoard        — shared task list (file-locked for atomicity)
├── MessageBus       — async inter-agent messaging
├── MCP Server       — exposes coordination tools to agents
└── Sandboxes
    ├── LocalSandbox  — direct host execution
    └── DockerSandbox — persistent container isolation

Pipeline (sequential mode)
├── Stage[]          — ordered list of stages
└── run_session()    — executes each stage with context handoff
```

## File Structure

```
agentpool/
├── __init__.py      — public API
├── pool.py          — AgentPool orchestrator
├── pipeline.py      — Pipeline sequential stages
├── session.py       — Claude SDK session wrapper
├── tasks.py         — TaskBoard with file locking
├── messages.py      — MessageBus
├── mcp_server.py    — MCP coordination server
├── config.py        — configuration dataclasses
├── security.py      — workspace path validation
├── logging.py       — structured logging
└── sandbox/
    ├── base.py      — abstract Sandbox interface
    ├── local.py     — LocalSandbox
    └── docker.py    — DockerSandbox
```

## Documentation

Detailed guides for each component in [`docs/`](docs/):

- [AgentPool Orchestrator](docs/AGENTPOOL.md) — parallel & team modes, configuration, agent lifecycle
- [Pipeline](docs/PIPELINE.md) — sequential stages, prompt templating, transform functions
- [Sandboxes](docs/SANDBOXES.md) — local vs Docker, security validation, container lifecycle
- [TaskBoard](docs/TASKBOARD.md) — shared task coordination, file locking, stale claim recovery
- [MessageBus](docs/MESSAGEBUS.md) — inter-agent messaging, in-memory vs file-based

## Setup

### Install

```bash
pip install -e ".[sdk,dev]"
```

### Authentication

agentpool uses the Claude SDK, which needs API credentials in the environment. Set one of:

```bash
# Option 1: Claude Code OAuth token (recommended)
export CLAUDE_CODE_OAUTH_TOKEN=...   # get via: claude setup-token

# Option 2: Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...
```

Or use a `.env` file in your app (see `examples/.env.example`).

agentpool itself never reads `.env` — that's the consuming app's job. The examples use `python-dotenv` for convenience.

### Examples

Runnable scripts in the `examples/` directory:

```bash
cd examples
cp .env.example .env     # fill in your token
python hello.py          # single agent
python parallel.py       # two agents in parallel
python pipeline.py       # sequential stages
```

## Tests

```bash
# Unit tests (no credentials needed)
pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires API credentials)
export CLAUDE_CODE_OAUTH_TOKEN=...
pytest tests/test_integration.py -v -s

# Specific test class
pytest tests/test_integration.py -v -s -k parallel

# Docker tests (requires Docker running)
pytest tests/test_integration.py -v -s -m docker
```

Integration tests are automatically skipped when credentials are not set.

## Requirements

- Python 3.10+
- `claude-agent-sdk` (for running agents)
- Docker (optional, for Docker sandboxes)
