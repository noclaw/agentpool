"""
Pipeline — sequential stages with handoff.

Each stage runs a single Claude SDK agent session. The output from one
stage is injected into the next stage's prompt via {previous_response}
template substitution.

Usage:
    from agentpool import Pipeline, Stage

    pipeline = Pipeline([
        Stage("research", prompt="Investigate the codebase..."),
        Stage("plan", prompt="Based on this research:\n{previous_response}\n\nCreate a plan."),
        Stage("implement", prompt="Implement this plan:\n{previous_response}", sandbox="docker"),
    ])
    result = await pipeline.run()
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Callable

from .config import AgentPoolConfig, SandboxType, DockerConfig
from .logging import get_logger, setup_logging
from .sandbox.local import LocalSandbox
from .sandbox.docker import DockerSandbox
from .session import Task, SessionResult, SessionStatus, run_session

logger = get_logger("pipeline")


@dataclass
class Stage:
    """A single stage in a pipeline."""
    name: str
    prompt: str
    model: Optional[str] = None
    sandbox: Optional[str] = None  # "local" or "docker"
    system_prompt: Optional[str] = None
    timeout: Optional[int] = None
    transform: Optional[Callable[[str], str]] = None


@dataclass
class PipelineResult:
    """Result from a completed pipeline run."""
    stages: List[SessionResult] = field(default_factory=list)

    @property
    def final_response(self) -> str:
        if not self.stages:
            return ""
        return self.stages[-1].response

    @property
    def total_duration(self) -> float:
        return sum(s.duration_seconds for s in self.stages)

    @property
    def success(self) -> bool:
        return (
            len(self.stages) > 0
            and all(s.status == SessionStatus.COMPLETED for s in self.stages)
        )


def build_prompt(stage: Stage, previous_response: Optional[str]) -> str:
    """Build the prompt for a stage, injecting the previous response if available."""
    if previous_response is None:
        return stage.prompt

    context = previous_response
    if stage.transform:
        context = stage.transform(context)

    if "{previous_response}" in stage.prompt:
        return stage.prompt.replace("{previous_response}", context)

    # If no placeholder, append the previous response as context
    return f"{stage.prompt}\n\n## Context from previous stage\n{context}"


class Pipeline:
    """
    Sequential stages with handoff between agents.

    Each stage runs a single Claude SDK agent. The output from one stage
    becomes input context for the next.
    """

    def __init__(
        self,
        stages: List[Stage],
        config: Optional[AgentPoolConfig] = None,
        workspace: Optional[Path] = None,
    ):
        if not stages:
            raise ValueError("Pipeline requires at least one stage")

        self.stages = stages
        self.config = config or AgentPoolConfig()
        self.workspace = workspace or Path.cwd()

        setup_logging(level=self.config.log_level, log_file=self.config.log_file)

    async def run(self) -> PipelineResult:
        """
        Execute all stages sequentially, threading output to input.

        Returns:
            PipelineResult with per-stage results
        """
        result = PipelineResult()
        previous_response: Optional[str] = None

        logger.info(f"Starting pipeline: {len(self.stages)} stages")

        for i, stage in enumerate(self.stages):
            stage_num = i + 1
            logger.info(f"[{stage.name}] Starting stage {stage_num}/{len(self.stages)}")

            prompt = build_prompt(stage, previous_response)
            model = stage.model or self.config.default_model
            timeout = stage.timeout or self.config.timeout
            sandbox_type = SandboxType(stage.sandbox) if stage.sandbox else self.config.default_sandbox

            # Create sandbox
            if sandbox_type == SandboxType.DOCKER:
                sandbox = DockerSandbox(
                    workspace=self.workspace,
                    name=f"pipeline-{stage.name}",
                    config=self.config.docker,
                )
            else:
                sandbox = LocalSandbox(
                    workspace=self.workspace,
                    name=f"pipeline-{stage.name}",
                )

            agent_id = f"pipeline-{stage.name}"
            task = Task(prompt=prompt, agent_id=agent_id)

            try:
                await sandbox.start()

                stage_result = await asyncio.wait_for(
                    run_session(
                        agent_id=agent_id,
                        task=task,
                        workspace=self.workspace,
                        model=model,
                        system_prompt=stage.system_prompt or "",
                        timeout=timeout,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                stage_result = SessionResult(
                    agent_id=agent_id,
                    status=SessionStatus.TIMEOUT,
                    error=f"Stage '{stage.name}' timed out after {timeout}s",
                )
            except Exception as e:
                logger.error(f"[{stage.name}] Error: {e}", exc_info=True)
                stage_result = SessionResult(
                    agent_id=agent_id,
                    status=SessionStatus.ERROR,
                    error=str(e),
                )
            finally:
                try:
                    await sandbox.stop()
                except Exception as e:
                    logger.warning(f"[{stage.name}] Sandbox cleanup error: {e}")

            result.stages.append(stage_result)

            logger.info(
                f"[{stage.name}] Stage {stage_num} {stage_result.status.value} "
                f"({stage_result.duration_seconds:.1f}s)"
            )

            # Stop pipeline on failure
            if stage_result.status != SessionStatus.COMPLETED:
                logger.error(
                    f"[{stage.name}] Pipeline stopped: stage failed with {stage_result.status.value}"
                )
                break

            previous_response = stage_result.response

        logger.info(
            f"Pipeline {'complete' if result.success else 'failed'}: "
            f"{len(result.stages)}/{len(self.stages)} stages, "
            f"{result.total_duration:.1f}s total"
        )

        return result
