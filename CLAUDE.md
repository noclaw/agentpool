# CLAUDE.md

## Project Overview

**agentpool** is a standalone Python package for Claude SDK agent orchestration. It runs multiple agents in parallel, coordinates teams with shared task boards, or chains sequential stages in a pipeline — with optional Docker sandboxing.

## Architecture

```
AgentPool (pool.py)           — parallel & team modes
├── TaskBoard (tasks.py)       — shared task list, file-locked atomic claiming
├── MessageBus (messages.py)   — async inter-agent messaging
├── MCP Server (mcp_server.py) — exposes coordination tools to agents
├── Security (security.py)     — workspace path validation before sandbox creation
└── Sandboxes (sandbox/)
    ├── LocalSandbox            — direct host execution
    └── DockerSandbox           — persistent container with docker exec

Pipeline (pipeline.py)        — sequential stages with context handoff
├── Stage[]                    — ordered list of stages
└── run_session()              — executes each stage, injects previous output
```

### Key Design Decisions
- **MCP tools for coordination**: agents get TaskBoard/MessageBus access via a stdio MCP server
- **Persistent Docker containers**: created once, commands via `docker exec`, cleaned up on stop(). Agent runs on host — container is only for command isolation
- **File locking for atomicity**: TaskBoard uses fcntl locks for safe cross-process task claiming
- **Stale claim recovery**: TaskBoard tracks `claimed_at` timestamps; stale IN_PROGRESS tasks are swept back to PENDING. Pool also releases agent tasks on cleanup
- **Workspace validation**: `validate_workspace()` blocks system directories before any sandbox is created
- **Zero external dependencies**: core package has no deps; claude-agent-sdk is optional

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev,sdk]"

# Run tests
pytest

# Run tests with output
pytest -v -s
```

## Testing

- Unit tests don't require Claude SDK or Docker — they test TaskBoard, MessageBus, sandbox lifecycle
- Integration tests (marked with `@pytest.mark.integration`) require claude-agent-sdk installed
- Docker tests (marked with `@pytest.mark.docker`) require Docker running

## Gotchas

- `TaskBoard._save()` takes a `_lock_held` param — `claim()` holds the lock and passes `_lock_held=True` to avoid deadlock
- `fcntl.flock` is used for file locking — works on macOS and Linux, not Windows
- The MCP server (`mcp_server.py`) uses file-based message passing (not the in-memory MessageBus) since each agent runs in a separate process
- Docker containers do NOT receive auth tokens — the agent (Claude SDK session) runs on the host, only shell commands execute inside the container
- On macOS, `/var` resolves to `/private/var` — security validation checks both forms. `/var/folders` and `/var/tmp` are explicitly allowed (macOS temp dirs)
