"""
AgentPool — the main orchestrator.

Manages concurrent Claude SDK agent sessions with optional sandboxing,
shared task coordination, and inter-agent messaging.

Three execution modes:
1. Simple parallel: independent tasks, no communication
2. Team mode: agents share a TaskBoard and MessageBus
3. Pipeline: sequential stages with handoff (see pipeline.py)

Usage:
    from agentpool import AgentPool, Task

    async with AgentPool(max_agents=3) as pool:
        pool.submit(Task(prompt="Review the auth module"))
        pool.submit(Task(prompt="Write API tests"))
        results = await pool.run()
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable, Awaitable

from .config import AgentPoolConfig, SandboxType, DockerConfig
from .logging import get_logger, setup_logging
from .messages import MessageBus
from .sandbox.base import Sandbox
from .sandbox.local import LocalSandbox
from .sandbox.docker import DockerSandbox
from .security import validate_workspace
from .session import Task, SessionResult, SessionStatus, run_session
from .tasks import TaskBoard

logger = get_logger("pool")


class AgentPool:
    """
    Orchestrates concurrent Claude SDK agent sessions.

    Each submitted task becomes an agent session. The pool manages:
    - Sandbox lifecycle (local or Docker per agent)
    - Concurrent execution up to max_agents
    - Shared TaskBoard and MessageBus for team coordination
    - Result collection and error handling
    """

    def __init__(
        self,
        config: Optional[AgentPoolConfig] = None,
        max_agents: Optional[int] = None,
        mode: str = "parallel",  # "parallel" or "team"
        workspace: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        event_callback: Optional[Callable[[str, str, Dict], Awaitable[None]]] = None,
    ):
        """
        Args:
            config: Full configuration (overrides individual params)
            max_agents: Max concurrent agents (shortcut for config.max_agents)
            mode: "parallel" (independent tasks) or "team" (shared coordination)
            workspace: Default workspace for agents
            state_dir: Directory for TaskBoard/MessageBus persistence
            event_callback: Async callback for pool events (agent_id, event_type, data)
        """
        self.config = config or AgentPoolConfig()
        if max_agents is not None:
            self.config.max_agents = min(max_agents, 8)  # hard cap

        self.mode = mode
        self.workspace = workspace or Path.cwd()
        self.event_callback = event_callback

        # State directory for coordination
        self._state_dir = state_dir or Path(tempfile.mkdtemp(prefix="agentpool-"))

        # Coordination primitives
        self.task_board = TaskBoard(
            state_dir=self._state_dir,
            stale_timeout=self.config.timeout if mode == "team" else None,
        )
        self.message_bus = MessageBus()

        # Submitted tasks and results
        self._tasks: List[Task] = []
        self._sandboxes: Dict[str, Sandbox] = {}
        self._results: List[SessionResult] = []
        self._agent_counter = 0
        self._stop_requested = False

        # Setup logging
        setup_logging(level=self.config.log_level, log_file=self.config.log_file)

    def submit(self, task: Task) -> str:
        """
        Submit a task to be executed by an agent.

        Args:
            task: The task to execute

        Returns:
            The assigned agent_id
        """
        self._agent_counter += 1
        agent_id = task.agent_id or f"agent-{self._agent_counter}"
        task.agent_id = agent_id
        self._tasks.append(task)
        logger.info(f"Task submitted: {agent_id} — {task.prompt[:60]}")
        return agent_id

    def add_tasks(self, descriptions: List[str]) -> List[str]:
        """
        Add tasks to the shared TaskBoard (for team mode).

        Args:
            descriptions: List of task descriptions

        Returns:
            List of task IDs on the board
        """
        return [self.task_board.add(desc) for desc in descriptions]

    async def run(self) -> List[SessionResult]:
        """
        Execute all submitted tasks concurrently (up to max_agents at a time).

        Returns:
            List of SessionResults, one per submitted task
        """
        if not self._tasks:
            logger.warning("No tasks submitted")
            return []

        start_time = time.time()
        logger.info(
            f"Starting pool: {len(self._tasks)} tasks, "
            f"max_agents={self.config.max_agents}, mode={self.mode}"
        )

        # Run with concurrency limit
        semaphore = asyncio.Semaphore(self.config.max_agents)

        async def run_with_limit(task: Task) -> SessionResult:
            async with semaphore:
                return await self._run_agent(task)

        # Launch all tasks
        coros = [run_with_limit(task) for task in self._tasks]
        self._results = await asyncio.gather(*coros, return_exceptions=False)

        elapsed = time.time() - start_time
        completed = sum(1 for r in self._results if r.status == SessionStatus.COMPLETED)
        errored = sum(1 for r in self._results if r.status == SessionStatus.ERROR)

        logger.info(
            f"Pool complete: {completed} succeeded, {errored} failed, "
            f"{elapsed:.1f}s total"
        )

        return self._results

    async def run_team(
        self,
        lead_prompt: str,
        worker_prompt: str = "Claim and implement tasks from the task board.",
        num_workers: Optional[int] = None,
        lead_model: Optional[str] = None,
        worker_model: Optional[str] = None,
    ) -> List[SessionResult]:
        """
        Run in team mode: a lead coordinates, workers claim shared tasks.

        The lead agent gets the full prompt and can add tasks to the board.
        Worker agents claim and implement tasks independently.

        Args:
            lead_prompt: Prompt for the lead/coordinator agent
            worker_prompt: Base prompt for worker agents
            num_workers: Number of workers (defaults to max_agents - 1)
            lead_model: Model for lead (defaults to config default)
            worker_model: Model for workers (defaults to config default)

        Returns:
            List of SessionResults (lead first, then workers)
        """
        num_workers = num_workers or (self.config.max_agents - 1)
        num_workers = max(1, min(num_workers, self.config.max_agents - 1))

        # Submit lead
        self.submit(Task(
            prompt=lead_prompt,
            agent_id="lead",
            model=lead_model,
            system_prompt=(
                "You are the team lead. Your job is to break down the task, "
                "add subtasks to the task board using the claim_task/complete_task tools, "
                "and coordinate workers via messaging. Workers will claim tasks independently."
            ),
        ))

        # Submit workers
        for i in range(num_workers):
            self.submit(Task(
                prompt=worker_prompt,
                agent_id=f"worker-{i + 1}",
                model=worker_model,
                system_prompt=(
                    f"You are worker-{i + 1}. Use claim_task to get your assignment from "
                    "the shared task board. Implement each task, then call complete_task. "
                    "Use send_message to share findings with other agents. "
                    "Use check_messages to see if other agents have sent you information."
                ),
            ))

        return await self.run()

    async def _run_agent(self, task: Task) -> SessionResult:
        """Run a single agent with its sandbox and coordination tools."""
        agent_id = task.agent_id
        model = task.model or self.config.default_model
        workspace = task.workspace or self.workspace
        timeout = task.timeout or self.config.timeout
        sandbox_type = SandboxType(task.sandbox) if task.sandbox else self.config.default_sandbox

        # Create sandbox
        sandbox = self._create_sandbox(sandbox_type, workspace, agent_id)
        self._sandboxes[agent_id] = sandbox

        # Register on message bus
        self.message_bus.register(agent_id)

        # Build MCP server config for coordination tools
        mcp_servers = self._build_mcp_config(agent_id)

        # Merge with any task-specific MCP servers
        if task.mcp_servers:
            mcp_servers.update(task.mcp_servers)

        # Build system prompt
        system_prompt = task.system_prompt or ""
        if self.mode == "team":
            team_instructions = (
                "\n\n## Team Coordination\n"
                "You have access to coordination tools:\n"
                "- `claim_task` — get your next task from the shared board\n"
                "- `complete_task` — mark a task as done\n"
                "- `list_tasks` — see all tasks and their status\n"
                "- `send_message` — send a message to another agent\n"
                "- `broadcast_message` — send to all agents\n"
                "- `check_messages` — check your inbox\n"
            )
            system_prompt = system_prompt + team_instructions

        try:
            # Start sandbox
            await sandbox.start()

            # Notify
            if self.event_callback:
                await self.event_callback(agent_id, "agent_started", {
                    "model": model,
                    "sandbox": sandbox_type.value,
                })

            # Run session
            result = await asyncio.wait_for(
                run_session(
                    agent_id=agent_id,
                    task=task,
                    workspace=workspace,
                    model=model,
                    system_prompt=system_prompt,
                    mcp_servers=mcp_servers if mcp_servers else None,
                    timeout=timeout,
                ),
                timeout=timeout,
            )

            # Notify
            if self.event_callback:
                await self.event_callback(agent_id, "agent_complete", result.to_dict())

            return result

        except asyncio.TimeoutError:
            logger.error(f"[{agent_id}] Timed out after {timeout}s")
            return SessionResult(
                agent_id=agent_id,
                status=SessionStatus.TIMEOUT,
                error=f"Agent timed out after {timeout}s",
            )
        except Exception as e:
            logger.error(f"[{agent_id}] Error: {e}", exc_info=True)
            return SessionResult(
                agent_id=agent_id,
                status=SessionStatus.ERROR,
                error=str(e),
            )
        finally:
            # Cleanup
            self.message_bus.unregister(agent_id)
            try:
                await sandbox.stop()
            except Exception as e:
                logger.warning(f"[{agent_id}] Sandbox cleanup error: {e}")
            self._sandboxes.pop(agent_id, None)

            # Release any IN_PROGRESS tasks this agent had claimed
            if self.mode == "team":
                try:
                    released = self.task_board.release_agent_tasks(agent_id)
                    if released:
                        logger.info(f"[{agent_id}] Released tasks on cleanup: {released}")
                except Exception as e:
                    logger.warning(f"[{agent_id}] Task release error: {e}")

    def _create_sandbox(
        self, sandbox_type: SandboxType, workspace: Path, agent_id: str
    ) -> Sandbox:
        """Create the appropriate sandbox for an agent."""
        if not validate_workspace(workspace, allowed_root=self.config.workspace_root):
            raise ValueError(f"Workspace path rejected by security validation: {workspace}")

        if sandbox_type == SandboxType.DOCKER:
            return DockerSandbox(
                workspace=workspace,
                name=agent_id,
                config=self.config.docker,
            )
        return LocalSandbox(workspace=workspace, name=agent_id)

    def _build_mcp_config(self, agent_id: str) -> Dict[str, Any]:
        """Build MCP server configuration for coordination tools."""
        if self.mode != "team":
            return {}

        # Path to the MCP server script
        mcp_server_path = Path(__file__).parent / "mcp_server.py"

        return {
            "agentpool-coordinator": {
                "command": sys.executable,
                "args": ["-m", "agentpool.mcp_server"],
                "env": {
                    "AGENTPOOL_STATE_DIR": str(self._state_dir),
                    "AGENTPOOL_AGENT_ID": agent_id,
                },
            }
        }

    def request_stop(self) -> None:
        """Request all agents to stop after their current work."""
        self._stop_requested = True
        logger.info("Stop requested for all agents")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Stop any running sandboxes
        for agent_id, sandbox in list(self._sandboxes.items()):
            try:
                await sandbox.stop()
            except Exception as e:
                logger.warning(f"Cleanup error for {agent_id}: {e}")
        self._sandboxes.clear()
