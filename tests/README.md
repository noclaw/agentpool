# Tests

109 unit tests + 10 integration tests.

## Running

```bash
# Unit tests (no credentials or Docker needed)
pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires API credentials)
export CLAUDE_CODE_OAUTH_TOKEN=...
pytest tests/test_integration.py -v -s

# Docker integration test (requires credentials + Docker)
pytest tests/test_integration.py -v -s -m docker

# Specific test class
pytest tests/test_integration.py -v -s -k parallel
```

## Unit Tests

### test_config.py (4 tests)
`AgentPoolConfig` and `DockerConfig` defaults, overrides, and enum values.

### test_taskboard.py (27 tests)
`TaskBoard` — the shared task list with file-locked atomic claiming.

- **TestTaskBoardInMemory** — add, claim, complete, fail, dependencies, counts
- **TestTaskBoardPersistence** — save/reload from disk, claim persists state
- **TestTaskBoardReload** — explicit and auto-reload from disk for all properties
- **TestTaskBoardConcurrentMutations** — two processes completing/failing different tasks don't clobber each other
- **TestTaskBoardPriority** — higher priority claimed first, creation order tiebreak, dependencies
- **TestTaskBoardStaleRecovery** — stale sweep reverts old IN_PROGRESS tasks, `release()` and `release_agent_tasks()`, `claimed_at` persistence

### test_messages.py (11 tests)
`MessageBus` — async inter-agent messaging.

- Direct messages, broadcast, receive drains inbox, unknown agent handling, unregister, history, timeout on empty inbox, agent count

### test_mcp_dispatch.py (10 tests)
MCP server tool dispatch — tests the JSON-RPC handler logic without running a real server.

- claim_task, complete_task, fail_task, list_tasks, send/check/broadcast messages, unknown tool error

### test_pool.py (11 tests)
`AgentPool` setup and configuration — no actual agent sessions.

- Default config, max_agents cap, submit/agent_id handling, add_tasks to board, empty pool run, state_dir creation, request_stop, context manager

### test_pipeline.py (15 tests)
`Pipeline` sequential stages.

- **TestStage** — dataclass defaults and fields
- **TestPipelineResult** — empty, single, multiple stages, partial failure properties
- **TestBuildPrompt** — `{previous_response}` substitution, auto-append, transform function
- **TestPipelineInit** — empty stages raises, defaults, custom config

### test_sandbox.py (9 tests)
`LocalSandbox` — start/stop, execute, timeout, working directory, context manager, file operations.

### test_security.py (9 tests)
`validate_workspace()` — blocks root, system paths, enforces allowed_root. Allows `/var/folders` and `/var/tmp` (macOS temp dirs).

### conftest.py
Shared pytest configuration. Auto-skips integration tests when API credentials are missing, and Docker tests when Docker is unavailable.

## Integration Tests

### test_integration.py (10 tests)
Require `claude-agent-sdk` and API credentials. All marked `@pytest.mark.integration`.

- **TestSingleAgent** — one agent, simple prompt, verify response
- **TestParallelMode** — two agents concurrent; concurrency limit with 3 tasks / 2 max_agents
- **TestTeamMode** — lead + worker with MCP coordination, TaskBoard reload from disk
- **TestToolUse** — agent creates a file using Write tool
- **TestEventCallback** — verifies agent_started/agent_complete events fire
- **TestErrorHandling** — 1-second timeout test
- **TestPipelineMode** — two-stage handoff (Tokyo -> population), failure-stops-pipeline
- **TestDockerSandbox** — one agent in Docker sandbox (also requires `@pytest.mark.docker`)
