"""
Abstract sandbox interface for agent execution isolation.

Two implementations:
- LocalSandbox: runs directly on host (no isolation, fast)
- DockerSandbox: runs inside a persistent Docker container (isolated, secure)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ExecutionResult:
    """Result of executing a command in a sandbox."""
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Sandbox(ABC):
    """
    Abstract base for execution sandboxes.

    A sandbox provides an isolated environment where a Claude SDK agent
    can execute commands. The sandbox owns a workspace directory that
    the agent can read/write.
    """

    def __init__(self, workspace: Path, name: Optional[str] = None):
        self.workspace = workspace
        self.name = name or "sandbox"
        self._started = False

    @abstractmethod
    async def start(self) -> None:
        """Start the sandbox. Must be called before execute()."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the sandbox and clean up resources."""
        ...

    @abstractmethod
    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        """
        Execute a shell command inside the sandbox.

        Args:
            command: Shell command to run
            timeout: Max seconds to wait

        Returns:
            ExecutionResult with stdout, stderr, returncode
        """
        ...

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def working_directory(self) -> Path:
        """The workspace directory visible to the agent."""
        return self.workspace

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
