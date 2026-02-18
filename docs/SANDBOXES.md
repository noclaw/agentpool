# Sandboxes

## Overview

Every agent runs inside a sandbox — an isolated environment that controls where shell commands execute. Two implementations:

| Sandbox | Isolation | Speed | Use case |
|---------|-----------|-------|----------|
| **LocalSandbox** | None (host) | Fast | Development, trusted workloads |
| **DockerSandbox** | Container | Slower | Multi-user, untrusted code |

The agent (Claude SDK session) always runs on the host. "Sandbox" refers to where the agent's *shell commands* execute.

```
┌─────────────────────────────────────────┐
│  Host Machine                           │
│                                         │
│  ┌─────────────────┐                    │
│  │ Claude SDK Agent │ ← always on host  │
│  └────────┬────────┘                    │
│           │ shell commands              │
│     ┌─────┴─────┐                       │
│     ▼           ▼                       │
│  [Host]     [Docker Container]          │
│  LocalSandbox   DockerSandbox           │
│                 ├─ /workspace (mounted)  │
│                 ├─ memory/cpu limits     │
│                 └─ no-new-privileges     │
└─────────────────────────────────────────┘
```

---

## Sandbox Interface

Both sandboxes implement the abstract `Sandbox` base class:

```python
class Sandbox(ABC):
    async def start(self) -> None       # prepare the environment
    async def stop(self) -> None        # cleanup resources
    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult
```

`ExecutionResult` contains `stdout`, `stderr`, and `returncode`. The `.ok` property checks `returncode == 0`.

All sandboxes support the async context manager protocol:

```python
async with LocalSandbox(workspace=Path("./project")) as sandbox:
    result = await sandbox.execute("ls -la")
    print(result.stdout)
```

---

## LocalSandbox

Executes commands directly on the host using `asyncio.create_subprocess_shell`. The workspace directory is the `cwd` for all commands.

```python
from agentpool import LocalSandbox

sandbox = LocalSandbox(workspace=Path("./my-project"), name="agent-1")
await sandbox.start()   # creates workspace dir if needed
result = await sandbox.execute("python test.py", timeout=30)
await sandbox.stop()
```

### Behavior

- `start()` — creates the workspace directory (with `parents=True`)
- `execute()` — runs the command in a subprocess with `cwd=workspace`
- `stop()` — marks the sandbox as stopped (no resources to clean up)
- Timeout — terminates the process after the specified seconds

### When to Use

- Single-user development
- Trusted agents working on your own code
- When container overhead is unnecessary

---

## DockerSandbox

Creates a **persistent Docker container** on `start()` and executes commands via `docker exec`. The container stays alive across multiple tool calls — this avoids the overhead of starting a new container per command.

```python
from agentpool import DockerSandbox, DockerConfig

config = DockerConfig(
    image="noclaw-worker:latest",
    memory_limit="1g",
    cpu_limit="1.0",
    network=None,  # default bridge network
)

sandbox = DockerSandbox(
    workspace=Path("./my-project"),
    name="agent-1",
    config=config,
)
await sandbox.start()   # docker run -d ...
result = await sandbox.execute("npm test")  # docker exec ...
await sandbox.stop()    # docker rm -f ...
```

### Container Lifecycle

1. **`start()`** — runs `docker run -d` with the configured image, mounts workspace at `/workspace`, applies resource limits and security options. If a container with the same name exists and is healthy, it is reused.
2. **`execute()`** — runs `docker exec <container> sh -c <command>` with a timeout.
3. **`stop()`** — runs `docker rm -f <container>` to remove the container.

### Container Naming

Containers are named `agentpool-{agent_name}-{pid}` to avoid collisions between concurrent pools.

### Security Options

Every container runs with:
- `--security-opt no-new-privileges` — prevents privilege escalation
- `--memory` and `--cpus` limits from `DockerConfig`
- Workspace mounted as the only volume at `/workspace`

### Runtime Detection

`DockerSandbox` auto-detects `docker` or `podman` — whichever is available. If neither is found, it raises `RuntimeError` at construction time.

### DockerConfig

```python
@dataclass
class DockerConfig:
    image: str = "noclaw-worker:latest"  # container image
    memory_limit: str = "1g"             # docker --memory
    cpu_limit: str = "1.0"              # docker --cpus
    network: Optional[str] = None        # None = default bridge
```

---

## Workspace Security

Before any sandbox is created, `AgentPool._create_sandbox()` calls `validate_workspace()` to check the path:

### Blocked Paths

| Category | Examples | Reason |
|----------|----------|--------|
| Root filesystem | `/` | Mounting root gives full access |
| System directories | `/etc`, `/usr`, `/bin`, `/sbin`, `/boot`, `/dev`, `/proc`, `/sys`, `/root` | Sensitive system files |
| `/var` | `/var/lib`, `/var/log` | System state and logs |

### Allowed Exceptions

macOS temp directories under `/var` are explicitly allowed:
- `/var/folders` — macOS per-user temp
- `/var/tmp` — shared temp

This matters because on macOS, `/var` resolves to `/private/var`, and `tempfile.mkdtemp()` creates directories under `/var/folders`. The validation checks both the original and resolved paths.

### Allowed Root

If `AgentPoolConfig.workspace_root` is set, workspace paths must be under that directory:

```python
config = AgentPoolConfig(workspace_root=Path("data/workspaces"))
# Only workspaces under data/workspaces/ are accepted
```

---

## Using Sandboxes with AgentPool

You typically don't interact with sandboxes directly — `AgentPool` manages them:

```python
# All agents use local sandbox (default)
async with AgentPool(max_agents=2) as pool:
    pool.submit(Task(prompt="Do something"))

# All agents use Docker sandbox
config = AgentPoolConfig(default_sandbox=SandboxType.DOCKER)
async with AgentPool(config=config) as pool:
    pool.submit(Task(prompt="Do something"))

# Mix: pool default is local, one agent overrides to Docker
async with AgentPool() as pool:
    pool.submit(Task(prompt="Safe task"))
    pool.submit(Task(prompt="Untrusted code", sandbox="docker"))
```

---

## Gotchas

1. **Container stays alive** — `DockerSandbox` creates one container and reuses it. This is efficient for multi-turn agent sessions but means the container accumulates state. `stop()` removes it.

2. **Agent runs on host** — the Claude SDK client is never inside the container. Only shell commands (`execute()`) run in Docker. Auth tokens and SDK credentials stay on the host.

3. **Timeout terminates the process** — both sandboxes call `process.terminate()` on timeout. For Docker, this terminates the `docker exec` process on the host; the container itself keeps running.

4. **No Windows support** — `DockerSandbox` uses `asyncio.create_subprocess_exec` with Unix assumptions. `LocalSandbox` works anywhere Python runs.

---

## See Also

- [AGENTPOOL.md](AGENTPOOL.md) — How the pool manages sandbox lifecycle
- [TASKBOARD.md](TASKBOARD.md) — Shared task coordination
- [MESSAGEBUS.md](MESSAGEBUS.md) — Inter-agent messaging
