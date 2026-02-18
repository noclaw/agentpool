"""
Docker sandbox — persistent container with mounted workspace.

The container stays alive across tool calls (unlike NoClaw's per-request model).
Commands are executed via `docker exec`. This matches YokeFlow2's approach
for efficiency in multi-turn agent sessions.
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from .base import Sandbox, ExecutionResult
from ..config import DockerConfig
from ..logging import get_logger

logger = get_logger("sandbox.docker")


class DockerSandbox(Sandbox):
    """
    Runs commands inside a persistent Docker container.

    The container is created on start() and removed on stop().
    The workspace directory is mounted at /workspace inside the container.
    """

    def __init__(
        self,
        workspace: Path,
        name: Optional[str] = None,
        config: Optional[DockerConfig] = None,
    ):
        super().__init__(workspace, name or "docker")
        self.config = config or DockerConfig()
        self.container_name: Optional[str] = None
        self._runtime = self._detect_runtime()

    def _detect_runtime(self) -> str:
        """Detect docker or podman."""
        import subprocess
        for runtime in ("docker", "podman"):
            try:
                result = subprocess.run(
                    [runtime, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return runtime
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        raise RuntimeError("No container runtime found. Install Docker or Podman.")

    async def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Generate container name
        self.container_name = f"agentpool-{self.name}-{os.getpid()}"

        # Check if container already exists and is healthy
        if await self._container_exists():
            if await self._is_healthy():
                logger.info(f"Reusing existing container: {self.container_name}")
                self._started = True
                return
            else:
                await self._remove_container()

        # Build docker run command
        cmd = [
            self._runtime, "run", "-d",
            "--name", self.container_name,
            "--memory", self.config.memory_limit,
            "--cpus", self.config.cpu_limit,
            "--security-opt", "no-new-privileges",
            "-v", f"{self.workspace.absolute()}:/workspace:rw",
            "-w", "/workspace",
        ]

        if self.config.network:
            cmd.extend(["--network", self.config.network])

        # Use the configured image, keep container alive with tail
        cmd.extend([self.config.image, "tail", "-f", "/dev/null"])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"Failed to start container: {error}")

        self._started = True
        logger.info(
            f"Docker sandbox started: {self.container_name} "
            f"(image={self.config.image}, memory={self.config.memory_limit})"
        )

    async def stop(self) -> None:
        if self.container_name:
            await self._remove_container()
        self._started = False
        logger.info(f"Docker sandbox stopped: {self.container_name}")

    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        if not self._started or not self.container_name:
            raise RuntimeError("Sandbox not started")

        cmd = [
            self._runtime, "exec",
            self.container_name,
            "sh", "-c", command,
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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

    async def _container_exists(self) -> bool:
        process = await asyncio.create_subprocess_exec(
            self._runtime, "inspect", self.container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        return process.returncode == 0

    async def _is_healthy(self) -> bool:
        result = await self.execute("echo ok", timeout=5)
        return result.ok and "ok" in result.stdout

    async def _remove_container(self) -> None:
        process = await asyncio.create_subprocess_exec(
            self._runtime, "rm", "-f", self.container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
