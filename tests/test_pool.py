"""Tests for AgentPool orchestrator (unit tests, no SDK required)."""

import tempfile
from pathlib import Path

import pytest

from agentpool.pool import AgentPool
from agentpool.session import Task
from agentpool.config import SandboxType


class TestAgentPoolSetup:

    def test_default_config(self):
        pool = AgentPool()
        assert pool.config.max_agents == 4
        assert pool.mode == "parallel"

    def test_max_agents_cap(self):
        pool = AgentPool(max_agents=100)
        assert pool.config.max_agents == 8  # hard cap

    def test_submit_returns_agent_id(self):
        pool = AgentPool()
        aid = pool.submit(Task(prompt="Test"))
        assert aid == "agent-1"

    def test_submit_custom_agent_id(self):
        pool = AgentPool()
        aid = pool.submit(Task(prompt="Test", agent_id="custom"))
        assert aid == "custom"

    def test_submit_increments_counter(self):
        pool = AgentPool()
        a1 = pool.submit(Task(prompt="A"))
        a2 = pool.submit(Task(prompt="B"))
        assert a1 == "agent-1"
        assert a2 == "agent-2"

    def test_add_tasks_to_board(self):
        pool = AgentPool(mode="team")
        ids = pool.add_tasks(["Task A", "Task B", "Task C"])
        assert len(ids) == 3
        assert pool.task_board.pending_count == 3

    async def test_run_empty_pool(self):
        pool = AgentPool()
        results = await pool.run()
        assert results == []

    def test_state_dir_created(self):
        pool = AgentPool()
        assert pool._state_dir.exists()

    def test_custom_state_dir(self):
        with tempfile.TemporaryDirectory() as td:
            pool = AgentPool(state_dir=Path(td))
            assert pool._state_dir == Path(td)

    def test_request_stop(self):
        pool = AgentPool()
        assert not pool._stop_requested
        pool.request_stop()
        assert pool._stop_requested

    async def test_context_manager(self):
        async with AgentPool() as pool:
            pool.submit(Task(prompt="Test"))
            # Don't actually run — just verify cleanup doesn't crash
