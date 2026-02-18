# MessageBus

## Overview

`MessageBus` provides lightweight async message passing between agents. Each agent gets an inbox (`asyncio.Queue`). Messages are delivered immediately — no polling, no external broker.

Used in **team mode** to let agents share findings, ask questions, and coordinate work.

```
┌──────────┐    send("worker-2", "Found a bug")    ┌──────────┐
│ worker-1 │ ─────────────────────────────────────→ │ worker-2 │
└──────────┘                                        └──────────┘
                                                         │
┌──────────┐    broadcast("Auth tests passing")     ┌────┴─────┐
│   lead   │ ──────────────────────────────────────→│ worker-1 │
│          │ ──────────────────────────────────────→│ worker-2 │
│          │ ──────────────────────────────────────→│ worker-3 │
└──────────┘                                        └──────────┘
```

---

## Quick Start

### Direct Usage (In-Process)

```python
from agentpool import MessageBus

bus = MessageBus()

# Register agents
bus.register("lead")
bus.register("worker-1")
bus.register("worker-2")

# Direct message
await bus.send("lead", "worker-1", "Please prioritize the auth task")

# Broadcast (goes to everyone except sender)
await bus.broadcast("worker-1", "Auth module has a critical bug in line 42")

# Check inbox
messages = await bus.receive("worker-1")
for msg in messages:
    print(f"From {msg.from_agent}: {msg.content}")
```

### Via MCP Tools (Team Mode)

In team mode, agents don't call `MessageBus` directly. Instead, they use MCP tools exposed by the coordination server:

| MCP Tool | MessageBus Equivalent |
|----------|----------------------|
| `send_message(to, content)` | `bus.send(from, to, content)` |
| `broadcast_message(content)` | `bus.broadcast(from, content)` |
| `check_messages()` | `bus.receive(agent_id)` |

The MCP server uses **file-based message passing** (not the in-memory `MessageBus`) because each agent runs in a separate process. Messages are appended to a shared `messages.jsonl` file with file locking.

---

## API

### `register(agent_id: str)`

Create an inbox for an agent. Called automatically by `AgentPool._run_agent()`.

### `unregister(agent_id: str)`

Remove an agent's inbox. Undelivered messages are discarded. Called during agent cleanup.

### `send(from_agent, to_agent, content)`

Deliver a message to a specific agent's inbox. If the recipient doesn't exist, a warning is logged but no error is raised.

### `broadcast(from_agent, content)`

Send a message to every registered agent except the sender.

### `receive(agent_id, timeout=0) -> list[Message]`

Drain all pending messages from the agent's inbox. If the inbox is empty and `timeout > 0`, wait up to that many seconds for a message to arrive. Returns an empty list if nothing is available.

### `history -> list[dict]`

All messages ever sent, for debugging and logging. Not used during normal operation.

### `agent_count -> int`

Number of currently registered agents.

---

## Message Format

```python
@dataclass
class Message:
    from_agent: str              # sender agent ID
    to_agent: Optional[str]      # recipient, or None for broadcast
    content: str                 # message body
    timestamp: float             # time.time() when created
```

Serialized form (via `to_dict()`):
```json
{
  "from": "worker-1",
  "to": "lead",
  "content": "Found a security issue in auth.py",
  "timestamp": 1708300000.123
}
```

Broadcast messages have `"to": "*"`.

---

## In-Memory vs File-Based

There are two messaging implementations because of how agents run:

| | In-Memory (`MessageBus`) | File-Based (`mcp_server.py`) |
|---|---|---|
| **Where** | `messages.py` | `mcp_server.py` |
| **Transport** | `asyncio.Queue` per agent | `messages.jsonl` shared file |
| **Concurrency** | async-safe (single process) | file-locked (cross-process) |
| **Used by** | Direct Python API | MCP tools in team mode |

The file-based implementation tracks `read_by` per message so agents don't see the same message twice. Messages addressed to a specific agent or broadcast (`to: "*"`) are visible to the recipient; messages from the agent itself are filtered out.

### Why Two Implementations?

The Claude SDK runs each agent as a separate process with its own MCP server instance. An in-memory queue can't cross process boundaries. The file-based approach in `mcp_server.py` solves this with a shared JSONL file and `fcntl` locks.

The in-memory `MessageBus` exists for direct Python usage (e.g., testing, or custom orchestration without MCP).

---

## Usage Patterns

### Lead-Worker Communication

The lead agent broadcasts instructions; workers report back:

```python
# Lead agent's MCP tool calls:
broadcast_message("Focus on auth endpoints first, API tests can wait")

# Worker agent's MCP tool calls:
check_messages()
# → [{"from": "lead", "content": "Focus on auth endpoints first..."}]

send_message(to="lead", content="Auth endpoint review complete, found 2 issues")
```

### Worker-to-Worker Coordination

Workers share findings to avoid duplicate work:

```python
# worker-1:
broadcast_message("I'm handling user registration, skip that")

# worker-2:
check_messages()
# → [{"from": "worker-1", "content": "I'm handling user registration..."}]
# worker-2 now knows to claim a different task
```

---

## Gotchas

1. **Fire-and-forget** — `send()` delivers to the queue and returns immediately. There's no acknowledgment or delivery guarantee. If the recipient crashes before checking their inbox, the message is lost.

2. **No persistence** — the in-memory `MessageBus` does not survive process restarts. The file-based MCP implementation persists to disk.

3. **Broadcast excludes sender** — `broadcast()` delivers to all agents *except* the one who sent it. This prevents agents from processing their own broadcasts.

4. **Receive drains the queue** — `receive()` returns all pending messages at once and removes them from the inbox. There's no peek or selective read.

---

## See Also

- [AGENTPOOL.md](AGENTPOOL.md) — How AgentPool wires up the MessageBus
- [TASKBOARD.md](TASKBOARD.md) — Task coordination (the other half of team mode)
- [SANDBOXES.md](SANDBOXES.md) — Where agent commands execute
