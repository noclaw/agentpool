# Pipeline

## Overview

`Pipeline` runs Claude SDK agents in **sequential stages** where the output of one stage feeds into the next. Each stage gets its own sandbox, model, and timeout — but shares context via prompt templating.

Use pipelines when work must happen in order: research before planning, planning before implementation, implementation before review.

```
Stage 1: research       Stage 2: plan          Stage 3: implement
┌──────────────┐      ┌──────────────┐       ┌──────────────┐
│ "Investigate  │      │ "Create a    │       │ "Implement   │
│  the codebase"│─────→│  plan based  │──────→│  this plan"  │
│               │ resp │  on this     │ resp  │              │
│  LocalSandbox │      │  research"   │       │ DockerSandbox│
│  Sonnet       │      │  LocalSandbox│       │  Sonnet      │
└──────────────┘      │  Haiku       │       └──────────────┘
                      └──────────────┘
```

If any stage fails, the pipeline stops immediately. No subsequent stages run.

---

## Quick Start

```python
from agentpool import Pipeline, Stage

pipeline = Pipeline([
    Stage("research", prompt="Investigate the auth module for security issues"),
    Stage("plan", prompt="Based on this research:\n{previous_response}\n\nCreate a remediation plan."),
    Stage("implement", prompt="Implement this plan:\n{previous_response}", sandbox="docker"),
])

result = await pipeline.run()

if result.success:
    print(result.final_response)
else:
    for stage in result.stages:
        print(f"{stage.agent_id}: {stage.status.value}")
```

---

## Stage Configuration

Each stage is a `Stage` dataclass:

```python
@dataclass
class Stage:
    name: str                              # identifier (used in agent_id and logs)
    prompt: str                            # prompt template, may include {previous_response}
    model: Optional[str] = None            # override pipeline default
    sandbox: Optional[str] = None          # "local" or "docker", override pipeline default
    system_prompt: Optional[str] = None    # per-stage system prompt
    timeout: Optional[int] = None          # override pipeline default
    transform: Optional[Callable] = None   # transform previous output before injection
```

### Per-Stage Overrides

Every stage inherits from the pipeline's `AgentPoolConfig` defaults, but can override individually:

```python
Pipeline([
    Stage("research", prompt="...", model="claude-haiku-4-5", timeout=60),
    Stage("plan", prompt="...", model="claude-sonnet-4-5"),
    Stage("implement", prompt="...", sandbox="docker", timeout=600),
])
```

This lets you use a cheap/fast model for research, a balanced model for planning, and Docker isolation for implementation — all in one pipeline.

---

## Prompt Templating

The `{previous_response}` placeholder is replaced with the output from the preceding stage.

### Explicit Placeholder

```python
Stage("plan", prompt="Given this analysis:\n{previous_response}\n\nCreate an implementation plan.")
```

The placeholder is replaced via simple string substitution.

### No Placeholder (Auto-Append)

If your prompt doesn't contain `{previous_response}`, the previous output is appended automatically:

```python
Stage("implement", prompt="Implement the changes described below.")
# Becomes:
# "Implement the changes described below.\n\n## Context from previous stage\n<previous output>"
```

### First Stage

The first stage receives no previous context — `{previous_response}` substitution is skipped entirely. Its prompt is used as-is.

### Transform Function

Use `transform` to process the previous output before injection — useful for extracting sections, trimming, or reformatting:

```python
def extract_plan(response: str) -> str:
    """Pull just the numbered plan from a verbose response."""
    lines = response.split("\n")
    plan_lines = [l for l in lines if l.strip().startswith(("1.", "2.", "3.", "4.", "5."))]
    return "\n".join(plan_lines)

Pipeline([
    Stage("plan", prompt="Create a detailed plan with numbered steps..."),
    Stage("implement",
        prompt="Implement these steps:\n{previous_response}",
        transform=extract_plan,
    ),
])
```

The transform runs before `{previous_response}` substitution. If there's no placeholder, the transformed output is auto-appended.

---

## Pipeline Configuration

```python
from agentpool import Pipeline, Stage, AgentPoolConfig, SandboxType

config = AgentPoolConfig(
    default_sandbox=SandboxType.LOCAL,
    default_model="claude-sonnet-4-5",
    timeout=300,
    log_level="INFO",
    log_file=Path("pipeline.jsonl"),
)

pipeline = Pipeline(
    stages=[...],
    config=config,
    workspace=Path("./my-project"),  # shared workspace for all stages
)
```

All stages share the same workspace directory. Files created by stage 1 are visible to stage 2, and so on.

---

## PipelineResult

```python
@dataclass
class PipelineResult:
    stages: list[SessionResult]    # one per executed stage

    @property
    def final_response(self) -> str    # last stage's response text
    def total_duration(self) -> float  # sum of all stage durations
    def success(self) -> bool          # True if ALL stages completed
```

### Inspecting Results

```python
result = await pipeline.run()

# Overall outcome
print(f"Success: {result.success}")
print(f"Total time: {result.total_duration:.1f}s")
print(f"Final output: {result.final_response[:200]}")

# Per-stage breakdown
for stage in result.stages:
    print(f"  {stage.agent_id}: {stage.status.value} ({stage.duration_seconds:.1f}s)")
    if stage.error:
        print(f"    Error: {stage.error}")
```

---

## Failure Handling

The pipeline stops on the first failure. If stage 2 of 4 fails:

- `result.stages` contains 2 entries (stages 1 and 2)
- `result.success` is `False`
- `result.final_response` is the failed stage's (empty) response
- Stages 3 and 4 never execute

Failure modes:

| Status | Cause |
|--------|-------|
| `ERROR` | Exception during the Claude SDK session |
| `TIMEOUT` | Stage exceeded its timeout |

Each stage's sandbox is always cleaned up, even on failure.

---

## Examples

### Code Review Pipeline

```python
Pipeline([
    Stage("analyze",
        prompt="Analyze this codebase for code quality issues. Focus on security, performance, and maintainability.",
        model="claude-sonnet-4-5",
    ),
    Stage("prioritize",
        prompt="Prioritize these findings by severity and effort:\n{previous_response}\n\nReturn a ranked list with estimated fix complexity.",
        model="claude-haiku-4-5",
    ),
    Stage("fix",
        prompt="Fix the top 3 highest-priority issues:\n{previous_response}",
        sandbox="docker",
        timeout=600,
    ),
])
```

### Research-Then-Write

```python
Pipeline([
    Stage("research",
        prompt="Research how authentication is implemented in this project. List all relevant files, patterns, and dependencies.",
    ),
    Stage("write",
        prompt="Write comprehensive documentation for the auth system based on this research:\n{previous_response}",
        system_prompt="You are a technical writer. Write clear, concise documentation.",
    ),
])
```

### Multi-Stage Refactor

```python
def just_the_file_list(response: str) -> str:
    """Extract file paths from the analysis."""
    import re
    return "\n".join(re.findall(r'`([^`]+\.\w+)`', response))

Pipeline([
    Stage("identify",
        prompt="Identify all files that use the deprecated `getUser()` API.",
    ),
    Stage("plan",
        prompt="Create a migration plan for these files:\n{previous_response}",
        transform=just_the_file_list,
    ),
    Stage("migrate",
        prompt="Execute this migration plan:\n{previous_response}",
        sandbox="docker",
    ),
    Stage("verify",
        prompt="Run tests and verify the migration:\n{previous_response}",
        sandbox="docker",
    ),
])
```

---

## Pipeline vs AgentPool

| | Pipeline | AgentPool (parallel) | AgentPool (team) |
|---|---|---|---|
| **Execution** | Sequential | Concurrent | Concurrent |
| **Communication** | Output → next prompt | None | TaskBoard + MessageBus |
| **Use case** | Ordered workflows | Independent tasks | Coordinated tasks |
| **Failure** | Stops on first error | Other agents continue | Other agents continue |
| **Shared state** | Workspace files + prompt | Workspace files | Workspace + TaskBoard + MessageBus |

Use a pipeline when order matters. Use parallel/team mode when agents can work independently.

---

## Gotchas

1. **Shared workspace** — all stages operate on the same workspace directory. A stage can see (and overwrite) files from previous stages. This is a feature, not a bug — it enables incremental work.

2. **No rollback** — if stage 3 modifies files and then fails, stages 1 and 2's file changes persist. Consider using git within stages if you need rollback capability.

3. **Each stage is a fresh agent** — there's no conversation continuity between stages. The only context carried forward is the text response via `{previous_response}`. If stage 1 creates files, stage 2 can read them from disk but won't "remember" creating them.

4. **Sandbox per stage** — each stage creates and destroys its own sandbox. A Docker container from stage 1 is removed before stage 2 starts a new one.

5. **Transform runs before substitution** — if you use both `transform` and `{previous_response}`, the transform is applied first. The transformed text is what gets substituted into the placeholder.

---

## See Also

- [AGENTPOOL.md](AGENTPOOL.md) — Parallel and team execution modes
- [SANDBOXES.md](SANDBOXES.md) — Local and Docker sandbox details
- [TASKBOARD.md](TASKBOARD.md) — Shared task coordination (team mode alternative)
- [MESSAGEBUS.md](MESSAGEBUS.md) — Inter-agent messaging (team mode alternative)
