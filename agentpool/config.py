"""
Configuration dataclasses for agentpool.

All settings have sensible defaults. Override via AgentPoolConfig() or YAML.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
from pathlib import Path


class SandboxType(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


@dataclass
class DockerConfig:
    """Docker sandbox settings."""
    image: str = "noclaw-worker:latest"
    memory_limit: str = "1g"
    cpu_limit: str = "1.0"
    network: Optional[str] = None  # None = default bridge


@dataclass
class AgentPoolConfig:
    """Top-level configuration for AgentPool."""
    max_agents: int = 4
    default_sandbox: SandboxType = SandboxType.LOCAL
    default_model: str = "claude-sonnet-4-5"
    timeout: int = 300  # seconds per agent session
    docker: DockerConfig = field(default_factory=DockerConfig)
    log_level: str = "INFO"
    log_file: Optional[Path] = None  # JSON lines file for agent performance analysis
    workspace_root: Optional[Path] = None  # for security validation
