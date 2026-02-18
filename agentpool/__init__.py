"""
agentpool — Parallel Claude SDK agent orchestration.

Manages concurrent agent sessions with optional Docker sandboxing,
shared task coordination, and inter-agent messaging.

Quick start:
    from agentpool import AgentPool, Task

    async with AgentPool(max_agents=3) as pool:
        pool.submit(Task(prompt="Review auth module for security issues"))
        pool.submit(Task(prompt="Write integration tests for the API"))
        results = await pool.run()

Team mode:
    async with AgentPool(mode="team", max_agents=4) as pool:
        pool.add_tasks([
            "Implement user registration",
            "Add password reset flow",
            "Write auth middleware",
        ])
        results = await pool.run_team(
            lead_prompt="Coordinate these auth tasks...",
        )

Pipeline mode:
    from agentpool import Pipeline, Stage

    pipeline = Pipeline([
        Stage("research", prompt="Investigate the codebase..."),
        Stage("plan", prompt="Based on this research:\\n{previous_response}\\nCreate a plan."),
        Stage("implement", prompt="Implement this plan:\\n{previous_response}"),
    ])
    result = await pipeline.run()
"""

from .config import AgentPoolConfig, SandboxType, DockerConfig
from .pool import AgentPool
from .session import Task, SessionResult, SessionStatus
from .tasks import TaskBoard, BoardTask, TaskStatus
from .messages import MessageBus, Message
from .sandbox import Sandbox, LocalSandbox, DockerSandbox, ExecutionResult
from .pipeline import Pipeline, Stage, PipelineResult
from .logging import get_logger, setup_logging

__version__ = "0.1.0"

__all__ = [
    # Core
    "AgentPool",
    "Task",
    "SessionResult",
    "SessionStatus",
    # Config
    "AgentPoolConfig",
    "SandboxType",
    "DockerConfig",
    # Coordination
    "TaskBoard",
    "BoardTask",
    "TaskStatus",
    "MessageBus",
    "Message",
    # Sandbox
    "Sandbox",
    "LocalSandbox",
    "DockerSandbox",
    "ExecutionResult",
    # Pipeline
    "Pipeline",
    "Stage",
    "PipelineResult",
    # Logging
    "get_logger",
    "setup_logging",
]
