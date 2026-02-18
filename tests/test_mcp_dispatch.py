"""Tests for MCP server tool dispatch logic."""

import json
import tempfile
from pathlib import Path

from agentpool.tasks import TaskBoard
from agentpool.mcp_server import dispatch_tool, _write_message, _read_messages


class TestMCPDispatch:

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir)
        self.board = TaskBoard(state_dir=self.state_dir)
        self.messages_file = self.state_dir / "messages.jsonl"

    def _dispatch(self, tool_name, args=None, agent_id="agent-1"):
        return dispatch_tool(
            tool_name,
            args or {},
            self.board,
            agent_id,
            self.state_dir,
            self.messages_file,
        )

    def test_claim_task_empty(self):
        result = self._dispatch("claim_task")
        assert result["claimed"] is False

    def test_claim_task_success(self):
        self.board.add("Do the thing")
        result = self._dispatch("claim_task")
        assert result["claimed"] is True
        assert "task_id" in result
        assert result["description"] == "Do the thing"

    def test_complete_task(self):
        tid = self.board.add("Do it")
        self.board.claim("agent-1")
        result = self._dispatch("complete_task", {"task_id": tid, "result": "Done"})
        assert result["success"] is True

    def test_complete_unknown_task(self):
        result = self._dispatch("complete_task", {"task_id": "nope"})
        assert result["success"] is False

    def test_fail_task(self):
        tid = self.board.add("Risky task")
        self.board.claim("agent-1")
        result = self._dispatch("fail_task", {"task_id": tid, "error": "Broke"})
        assert result["success"] is True

    def test_list_tasks(self):
        self.board.add("Task A")
        self.board.add("Task B")
        result = self._dispatch("list_tasks")
        assert len(result["tasks"]) == 2

    def test_send_and_check_messages(self):
        _write_message(self.messages_file, "agent-1", "agent-2", "Hello there")
        result = self._dispatch("check_messages", agent_id="agent-2")
        assert len(result["messages"]) == 1
        assert result["messages"][0]["content"] == "Hello there"
        assert result["messages"][0]["from"] == "agent-1"

    def test_broadcast_message(self):
        result = self._dispatch("broadcast_message", {"content": "All hands"})
        assert result["sent"] is True

        # Both agent-2 and agent-3 should see it
        msgs2 = _read_messages(self.messages_file, "agent-2")
        msgs3 = _read_messages(self.messages_file, "agent-3")
        assert len(msgs2) == 1
        assert len(msgs3) == 1

        # Sender should not see it
        msgs1 = _read_messages(self.messages_file, "agent-1")
        assert len(msgs1) == 0

    def test_messages_mark_as_read(self):
        _write_message(self.messages_file, "agent-1", "agent-2", "Read me")

        # First read
        msgs = _read_messages(self.messages_file, "agent-2")
        assert len(msgs) == 1

        # Second read — already read
        msgs2 = _read_messages(self.messages_file, "agent-2")
        assert len(msgs2) == 0

    def test_unknown_tool(self):
        result = self._dispatch("nonexistent_tool")
        assert "error" in result
