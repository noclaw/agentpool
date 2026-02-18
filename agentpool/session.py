"""
Agent session — wraps ClaudeSDKClient for a single agent's lifecycle.

Each session gets its own SDK client, sandbox, and optional coordination
tools (TaskBoard + MessageBus access via MCP).
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

from .logging import get_logger

logger = get_logger("session")


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class SessionResult:
    """Result from a completed agent session."""
    agent_id: str
    status: SessionStatus
    response: str = ""
    error: Optional[str] = None
    model_used: str = ""
    tokens_used: Optional[int] = None
    tool_uses: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "response": self.response,
            "error": self.error,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "tool_uses": self.tool_uses,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class Task:
    """A task to be executed by an agent."""
    prompt: str
    agent_id: Optional[str] = None  # auto-assigned if None
    model: Optional[str] = None  # uses pool default if None
    sandbox: Optional[str] = None  # "local" or "docker", uses pool default if None
    workspace: Optional[Path] = None  # uses pool default if None
    system_prompt: Optional[str] = None
    timeout: Optional[int] = None  # uses pool default if None
    mcp_servers: Optional[Dict[str, Any]] = None  # additional MCP servers


async def run_session(
    agent_id: str,
    task: Task,
    workspace: Path,
    model: str,
    system_prompt: str = "",
    mcp_servers: Optional[Dict[str, Any]] = None,
    timeout: int = 300,
) -> SessionResult:
    """
    Run a single Claude SDK agent session.

    This is the core execution function. It creates a ClaudeSDKClient,
    sends the prompt, collects the response, and returns a SessionResult.

    Args:
        agent_id: Identifier for this agent
        task: The task to execute
        workspace: Working directory for the agent
        model: Claude model ID to use
        system_prompt: System prompt for the agent
        mcp_servers: MCP server configurations
        timeout: Max seconds for the session

    Returns:
        SessionResult with the agent's output
    """
    import time
    start_time = time.time()

    try:
        from claude_agent_sdk import (
            ClaudeSDKClient,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            ThinkingBlock,
        )
    except ImportError:
        return SessionResult(
            agent_id=agent_id,
            status=SessionStatus.ERROR,
            error="claude_agent_sdk not installed. pip install claude-agent-sdk",
        )

    # Build options
    options_kwargs: Dict[str, Any] = {
        "model": model,
        "cwd": str(workspace.resolve()),
        "permission_mode": "bypassPermissions",
    }

    if system_prompt:
        options_kwargs["system_prompt"] = system_prompt

    if mcp_servers:
        options_kwargs["mcp_servers"] = mcp_servers

    options = ClaudeAgentOptions(**options_kwargs)
    client = ClaudeSDKClient(options=options)

    response_text = ""
    tool_uses = []
    model_used = model

    try:
        async with client:
            await client.query(task.prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append(block.name)
                        elif isinstance(block, ThinkingBlock):
                            pass  # thinking is internal
                elif isinstance(message, ResultMessage):
                    logger.info(
                        f"[{agent_id}] Session complete",
                        extra={"agent_id": agent_id},
                    )

        elapsed = time.time() - start_time
        return SessionResult(
            agent_id=agent_id,
            status=SessionStatus.COMPLETED,
            response=response_text,
            model_used=model_used,
            tool_uses=tool_uses,
            duration_seconds=elapsed,
        )

    except asyncio.TimeoutError:
        elapsed = time.time() - start_time
        return SessionResult(
            agent_id=agent_id,
            status=SessionStatus.TIMEOUT,
            error=f"Session timed out after {timeout}s",
            duration_seconds=elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[{agent_id}] Session error: {e}",
            extra={"agent_id": agent_id},
            exc_info=True,
        )
        return SessionResult(
            agent_id=agent_id,
            status=SessionStatus.ERROR,
            error=str(e),
            duration_seconds=elapsed,
        )
