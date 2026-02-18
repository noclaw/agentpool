"""Tests for MessageBus — inter-agent communication."""

import pytest

from agentpool.messages import MessageBus


@pytest.fixture
def bus():
    b = MessageBus()
    b.register("agent-1")
    b.register("agent-2")
    b.register("agent-3")
    return b


class TestMessageBus:

    async def test_direct_message(self, bus):
        await bus.send("agent-1", "agent-2", "Hello")
        msgs = await bus.receive("agent-2")
        assert len(msgs) == 1
        assert msgs[0].from_agent == "agent-1"
        assert msgs[0].content == "Hello"

    async def test_message_not_delivered_to_others(self, bus):
        await bus.send("agent-1", "agent-2", "Private")
        msgs = await bus.receive("agent-3")
        assert len(msgs) == 0

    async def test_broadcast(self, bus):
        await bus.broadcast("agent-1", "Everyone listen")

        msgs2 = await bus.receive("agent-2")
        msgs3 = await bus.receive("agent-3")
        assert len(msgs2) == 1
        assert len(msgs3) == 1
        assert msgs2[0].content == "Everyone listen"

    async def test_broadcast_excludes_sender(self, bus):
        await bus.broadcast("agent-1", "Not for me")
        msgs = await bus.receive("agent-1")
        assert len(msgs) == 0

    async def test_receive_drains_inbox(self, bus):
        await bus.send("agent-1", "agent-2", "First")
        await bus.send("agent-1", "agent-2", "Second")

        msgs = await bus.receive("agent-2")
        assert len(msgs) == 2

        # Second receive should be empty
        msgs2 = await bus.receive("agent-2")
        assert len(msgs2) == 0

    async def test_receive_unknown_agent(self, bus):
        msgs = await bus.receive("nonexistent")
        assert msgs == []

    async def test_send_to_unknown_agent(self, bus):
        # Should not raise, just log warning
        await bus.send("agent-1", "nonexistent", "Hello?")

    async def test_unregister(self, bus):
        bus.unregister("agent-2")
        assert bus.agent_count == 2

        # Messages to unregistered agent are dropped
        await bus.send("agent-1", "agent-2", "Gone")

    async def test_history(self, bus):
        await bus.send("agent-1", "agent-2", "Hello")
        await bus.broadcast("agent-3", "All")

        history = bus.history
        assert len(history) == 2
        assert history[0]["from"] == "agent-1"
        assert history[1]["to"] == "*"

    async def test_receive_with_timeout_empty(self, bus):
        # Should return empty after short timeout, not hang
        msgs = await bus.receive("agent-1", timeout=0.1)
        assert len(msgs) == 0

    async def test_agent_count(self, bus):
        assert bus.agent_count == 3
