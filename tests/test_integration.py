"""
Integration tests for agentpool — runs real Claude agents.

Requirements:
- claude-agent-sdk installed (pip install -e ".[sdk]")
- Valid API credentials (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)

Run:
    pytest tests/test_integration.py -v -s
    pytest tests/test_integration.py -v -s -k single    # just the single agent test
    pytest tests/test_integration.py -v -s -k parallel   # just parallel mode
    pytest tests/test_integration.py -v -s -k team       # just team mode

Docker tests additionally require Docker running:
    pytest tests/test_integration.py -v -s -m docker
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from agentpool import (
    AgentPool,
    AgentPoolConfig,
    Pipeline,
    Stage,
    Task,
    SessionResult,
    SessionStatus,
    SandboxType,
)

# All tests in this file require the SDK and API credentials
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_completed(result: SessionResult, agent_id: str = None):
    """Assert a session completed successfully with a non-empty response."""
    assert result.status == SessionStatus.COMPLETED, (
        f"[{result.agent_id}] Expected COMPLETED, got {result.status}: {result.error}"
    )
    assert result.response, f"[{result.agent_id}] Empty response"
    assert result.duration_seconds > 0
    if agent_id:
        assert result.agent_id == agent_id


def print_result(result: SessionResult):
    """Print a result for human inspection during test runs."""
    status = result.status.value
    duration = f"{result.duration_seconds:.1f}s"
    tools = ", ".join(result.tool_uses) if result.tool_uses else "none"
    response_preview = result.response[:200] if result.response else "(empty)"
    print(f"\n  [{result.agent_id}] status={status} duration={duration} tools=[{tools}]")
    print(f"  Response: {response_preview}")
    if result.error:
        print(f"  Error: {result.error}")


# ---------------------------------------------------------------------------
# 1. Single agent — simplest possible test
# ---------------------------------------------------------------------------

class TestSingleAgent:
    """Verify a single agent can execute a prompt and return a result."""

    async def test_single_agent_local(self, tmp_path):
        """One agent, local sandbox, simple prompt."""
        config = AgentPoolConfig(timeout=60, log_level="DEBUG")
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(
                prompt="What is 2 + 2? Reply with just the number.",
            ))
            results = await pool.run()

        assert len(results) == 1
        result = results[0]
        print_result(result)
        assert_completed(result, agent_id="agent-1")
        assert "4" in result.response


# ---------------------------------------------------------------------------
# 2. Parallel mode — multiple independent agents
# ---------------------------------------------------------------------------

class TestParallelMode:
    """Verify multiple agents run concurrently and independently."""

    async def test_parallel_two_agents(self, tmp_path):
        """Two agents with independent prompts, both should complete."""
        config = AgentPoolConfig(max_agents=2, timeout=60, log_level="DEBUG")
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(
                prompt="What is the capital of France? Reply with just the city name.",
                agent_id="geo-agent",
            ))
            pool.submit(Task(
                prompt="What is 10 * 7? Reply with just the number.",
                agent_id="math-agent",
            ))
            results = await pool.run()

        assert len(results) == 2
        for r in results:
            print_result(r)
            assert_completed(r)

        # Find results by agent_id
        by_id = {r.agent_id: r for r in results}
        assert "Paris" in by_id["geo-agent"].response
        assert "70" in by_id["math-agent"].response

    async def test_parallel_respects_concurrency(self, tmp_path):
        """Submit 3 tasks with max_agents=2 — all should still complete."""
        config = AgentPoolConfig(max_agents=2, timeout=60, log_level="DEBUG")
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(prompt="Say 'alpha'.", agent_id="a1"))
            pool.submit(Task(prompt="Say 'bravo'.", agent_id="a2"))
            pool.submit(Task(prompt="Say 'charlie'.", agent_id="a3"))
            results = await pool.run()

        assert len(results) == 3
        for r in results:
            print_result(r)
            assert_completed(r)


# ---------------------------------------------------------------------------
# 3. Team mode — shared TaskBoard + MCP coordination
# ---------------------------------------------------------------------------

class TestTeamMode:
    """Verify team mode with TaskBoard claiming and MCP coordination."""

    async def test_team_basic(self, tmp_path):
        """Lead + 1 worker. Pre-populate board with simple tasks."""
        state_dir = tmp_path / "state"
        config = AgentPoolConfig(max_agents=3, timeout=120, log_level="DEBUG")
        async with AgentPool(
            config=config,
            mode="team",
            workspace=tmp_path,
            state_dir=state_dir,
        ) as pool:
            # Pre-populate the task board
            pool.add_tasks([
                "Write a haiku about Python programming",
                "Write a haiku about ocean waves",
            ])
            assert pool.task_board.pending_count == 2

            results = await pool.run_team(
                lead_prompt=(
                    "You are coordinating a small team. "
                    "First, use list_tasks to see what's on the board. "
                    "Then claim a task with claim_task, do it, and complete it with complete_task. "
                    "Let the worker handle the other task."
                ),
                num_workers=1,
            )

        for r in results:
            print_result(r)

        # At least one agent should have completed its session
        completed = [r for r in results if r.status == SessionStatus.COMPLETED]
        assert len(completed) >= 1, "Expected at least one agent to complete"

        # Verify agents used the MCP coordination tools
        all_tools = []
        for r in results:
            all_tools.extend(r.tool_uses)
        mcp_tools = [t for t in all_tools if "agentpool-coordinator" in t]
        print(f"\n  MCP tools used: {mcp_tools}")
        assert len(mcp_tools) >= 1, "Expected agents to use MCP coordination tools"

        # Reload the task board from disk — the MCP server (separate process)
        # wrote updates to taskboard.json, so the in-memory board is stale
        from agentpool import TaskBoard
        board = TaskBoard(state_dir=state_dir)
        done = board.completed_count
        total = len(board.status())
        print(f"  TaskBoard (from disk): {done}/{total} tasks completed")
        for t in board.status():
            print(f"    [{t['status']}] {t['description'][:50]} (assigned: {t['assigned_to']})")
        assert done >= 1, "Expected at least 1 task completed on the board"


# ---------------------------------------------------------------------------
# 4. Agent with tool use — verify agents can use built-in tools
# ---------------------------------------------------------------------------

class TestToolUse:
    """Verify agents can use tools (file operations via the sandbox)."""

    async def test_agent_creates_file(self, tmp_path):
        """Ask the agent to create a file and verify it exists afterward."""
        config = AgentPoolConfig(timeout=90, log_level="DEBUG")
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(
                prompt=(
                    f"Create a file called 'hello.txt' in {tmp_path} "
                    "containing the text 'Hello from agentpool'. "
                    "Use the Write tool or bash to create it."
                ),
            ))
            results = await pool.run()

        result = results[0]
        print_result(result)
        assert_completed(result)

        # Verify the file was created
        hello_file = tmp_path / "hello.txt"
        assert hello_file.exists(), f"Expected {hello_file} to exist"
        content = hello_file.read_text()
        assert "Hello from agentpool" in content


# ---------------------------------------------------------------------------
# 5. Event callback — verify pool events fire
# ---------------------------------------------------------------------------

class TestEventCallback:
    """Verify the event callback is invoked during pool execution."""

    async def test_events_fire(self, tmp_path):
        """Collect events and verify agent_started/agent_complete fire."""
        events = []

        async def on_event(agent_id: str, event_type: str, data: dict):
            events.append((agent_id, event_type))

        config = AgentPoolConfig(timeout=60, log_level="DEBUG")
        async with AgentPool(
            config=config,
            workspace=tmp_path,
            event_callback=on_event,
        ) as pool:
            pool.submit(Task(prompt="Say hello."))
            results = await pool.run()

        print_result(results[0])
        assert_completed(results[0])

        event_types = [e[1] for e in events]
        assert "agent_started" in event_types, f"Missing agent_started in {events}"
        assert "agent_complete" in event_types, f"Missing agent_complete in {events}"


# ---------------------------------------------------------------------------
# 6. Error handling — verify graceful failures
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Verify the pool handles errors and timeouts gracefully."""

    async def test_timeout(self, tmp_path):
        """An agent with an impossibly short timeout should return TIMEOUT."""
        config = AgentPoolConfig(timeout=1, log_level="DEBUG")
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(
                prompt=(
                    "Write a 10,000 word essay on the history of computing, "
                    "covering every decade from the 1940s to the 2020s."
                ),
                timeout=1,  # 1 second — will definitely time out
            ))
            results = await pool.run()

        result = results[0]
        print_result(result)
        assert result.status == SessionStatus.TIMEOUT, (
            f"Expected TIMEOUT, got {result.status}"
        )


# ---------------------------------------------------------------------------
# 7. Pipeline mode — sequential stages with handoff
# ---------------------------------------------------------------------------

class TestPipelineMode:
    """Verify pipeline mode runs stages sequentially with context handoff."""

    async def test_pipeline_two_stages(self, tmp_path):
        """Stage 1 answers a question, stage 2 builds on stage 1's answer."""
        config = AgentPoolConfig(timeout=60, log_level="DEBUG")
        pipeline = Pipeline(
            stages=[
                Stage(
                    name="research",
                    prompt="What is the capital of Japan? Reply with just the city name.",
                ),
                Stage(
                    name="expand",
                    prompt=(
                        "The previous stage identified this city: {previous_response}\n\n"
                        "What is the population of this city? Reply with just the number."
                    ),
                ),
            ],
            config=config,
            workspace=tmp_path,
        )
        result = await pipeline.run()

        for sr in result.stages:
            print_result(sr)

        assert result.success, f"Pipeline failed: {[s.error for s in result.stages]}"
        assert len(result.stages) == 2

        # Stage 1 should mention Tokyo
        assert "Tokyo" in result.stages[0].response

        # Stage 2 should have a number (population)
        assert any(c.isdigit() for c in result.stages[1].response)

        print(f"\n  Pipeline total duration: {result.total_duration:.1f}s")

    async def test_pipeline_stops_on_failure(self, tmp_path):
        """Pipeline stops if a stage times out."""
        config = AgentPoolConfig(timeout=60, log_level="DEBUG")
        pipeline = Pipeline(
            stages=[
                Stage(
                    name="will-timeout",
                    prompt="Write a 10,000 word essay on quantum physics.",
                    timeout=1,  # will time out
                ),
                Stage(
                    name="should-not-run",
                    prompt="This stage should never execute: {previous_response}",
                ),
            ],
            config=config,
            workspace=tmp_path,
        )
        result = await pipeline.run()

        assert not result.success
        # Only the first stage should have run
        assert len(result.stages) == 1
        assert result.stages[0].status == SessionStatus.TIMEOUT
        print_result(result.stages[0])


# ---------------------------------------------------------------------------
# 8. Docker sandbox (requires Docker running)
# ---------------------------------------------------------------------------

@pytest.mark.docker
class TestDockerSandbox:
    """Tests that require Docker. Run with: ppytest tests/test_integration.py -v -s -m docker"""
    async def test_single_agent_docker(self, tmp_path):
        """One agent in a Docker sandbox."""
        config = AgentPoolConfig(
            timeout=90,
            default_sandbox=SandboxType.DOCKER,
            log_level="DEBUG",
        )
        async with AgentPool(config=config, workspace=tmp_path) as pool:
            pool.submit(Task(
                prompt="What is 3 + 5? Reply with just the number.",
            ))
            results = await pool.run()

        result = results[0]
        print_result(result)
        assert_completed(result)
        assert "8" in result.response
