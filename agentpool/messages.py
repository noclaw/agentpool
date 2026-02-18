"""
MessageBus — lightweight inter-agent communication.

In-process async message passing. No external dependencies.
Agents can send direct messages or broadcast to all agents.

Exposed to agents via MCP tools in the coordination server.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .logging import get_logger

logger = get_logger("messages")


@dataclass
class Message:
    """A message between agents."""
    from_agent: str
    to_agent: Optional[str]  # None = broadcast
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "from": self.from_agent,
            "to": self.to_agent or "*",
            "content": self.content,
            "timestamp": self.timestamp,
        }


class MessageBus:
    """
    Async message passing between agents.

    Each agent has an inbox (asyncio.Queue). Messages are delivered
    immediately to the recipient's queue. Broadcast messages go to all
    agents except the sender.
    """

    def __init__(self):
        self._inboxes: Dict[str, asyncio.Queue] = {}
        self._history: List[Message] = []

    def register(self, agent_id: str) -> None:
        """Register an agent to receive messages."""
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = asyncio.Queue()
            logger.info(f"Agent registered on message bus: {agent_id}")

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the message bus."""
        self._inboxes.pop(agent_id, None)

    async def send(self, from_agent: str, to_agent: str, content: str) -> None:
        """
        Send a message to a specific agent.

        Args:
            from_agent: Sender agent ID
            to_agent: Recipient agent ID
            content: Message content
        """
        msg = Message(from_agent=from_agent, to_agent=to_agent, content=content)
        self._history.append(msg)

        inbox = self._inboxes.get(to_agent)
        if inbox:
            await inbox.put(msg)
            logger.debug(f"Message: {from_agent} → {to_agent}: {content[:60]}")
        else:
            logger.warning(f"Message to unknown agent {to_agent} (from {from_agent})")

    async def broadcast(self, from_agent: str, content: str) -> None:
        """
        Send a message to all agents except the sender.

        Args:
            from_agent: Sender agent ID
            content: Message content
        """
        msg = Message(from_agent=from_agent, to_agent=None, content=content)
        self._history.append(msg)

        for agent_id, inbox in self._inboxes.items():
            if agent_id != from_agent:
                await inbox.put(msg)

        recipient_count = len(self._inboxes) - (1 if from_agent in self._inboxes else 0)
        logger.debug(f"Broadcast from {from_agent} to {recipient_count} agents")

    async def receive(self, agent_id: str, timeout: float = 0) -> List[Message]:
        """
        Get all pending messages for an agent.

        Args:
            agent_id: The agent checking their inbox
            timeout: Seconds to wait if inbox is empty (0 = don't wait)

        Returns:
            List of messages (may be empty)
        """
        inbox = self._inboxes.get(agent_id)
        if not inbox:
            return []

        messages = []

        # Drain all currently queued messages
        while not inbox.empty():
            try:
                messages.append(inbox.get_nowait())
            except asyncio.QueueEmpty:
                break

        # If nothing and timeout > 0, wait for one message
        if not messages and timeout > 0:
            try:
                msg = await asyncio.wait_for(inbox.get(), timeout=timeout)
                messages.append(msg)
            except asyncio.TimeoutError:
                pass

        return messages

    @property
    def history(self) -> List[dict]:
        """Get all messages ever sent (for debugging/logging)."""
        return [m.to_dict() for m in self._history]

    @property
    def agent_count(self) -> int:
        return len(self._inboxes)
