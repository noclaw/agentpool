"""Tests for configuration."""

from agentpool.config import AgentPoolConfig, SandboxType, DockerConfig


class TestConfig:

    def test_defaults(self):
        config = AgentPoolConfig()
        assert config.max_agents == 4
        assert config.default_sandbox == SandboxType.LOCAL
        assert config.default_model == "claude-sonnet-4-5"
        assert config.timeout == 300
        assert config.log_level == "INFO"
        assert config.log_file is None

    def test_docker_defaults(self):
        config = AgentPoolConfig()
        assert config.docker.image == "noclaw-worker:latest"
        assert config.docker.memory_limit == "1g"
        assert config.docker.cpu_limit == "1.0"
        assert config.docker.network is None

    def test_override(self):
        config = AgentPoolConfig(
            max_agents=2,
            default_sandbox=SandboxType.DOCKER,
            docker=DockerConfig(image="custom:v1", memory_limit="2g"),
        )
        assert config.max_agents == 2
        assert config.default_sandbox == SandboxType.DOCKER
        assert config.docker.image == "custom:v1"
        assert config.docker.memory_limit == "2g"

    def test_sandbox_type_enum(self):
        assert SandboxType.LOCAL.value == "local"
        assert SandboxType.DOCKER.value == "docker"
        assert SandboxType("local") == SandboxType.LOCAL
