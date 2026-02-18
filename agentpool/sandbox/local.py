"""
Local sandbox — runs commands directly on the host.

No isolation. Fast. Good for development and trusted workloads.
"""

import asyncio
from pathlib import Path
from typing import Optional

from .base import Sandbox, ExecutionResult
from ..logging import get_logger

logger = get_logger("sandbox.local")


class LocalSandbox(Sandbox):
    """Executes commands directly on the host machine."""

    def __init__(self, workspace: Path, name: Optional[str] = None):
        super().__init__(workspace, name or "local")

    async def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._started = True
        logger.info(f"Local sandbox started: {self.workspace}")

    async def stop(self) -> None:
        self._started = False
        logger.info("Local sandbox stopped")

    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        if not self._started:
            raise RuntimeError("Sandbox not started")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return ExecutionResult(
                stdout=stdout.decode(),
                stderr=stderr.decode(),
                returncode=process.returncode or 0,
            )
        except asyncio.TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            return ExecutionResult(stdout="", stderr="Timed out", returncode=-1)
