"""Tests for Pipeline mode — sequential stages with handoff (no SDK required)."""

import pytest

from agentpool.pipeline import Stage, PipelineResult, Pipeline, build_prompt
from agentpool.session import SessionResult, SessionStatus
from agentpool.config import AgentPoolConfig


class TestStage:
    """Test Stage dataclass."""

    def test_defaults(self):
        stage = Stage(name="research", prompt="Do research")
        assert stage.name == "research"
        assert stage.prompt == "Do research"
        assert stage.model is None
        assert stage.sandbox is None
        assert stage.system_prompt is None
        assert stage.timeout is None
        assert stage.transform is None

    def test_all_fields(self):
        transform_fn = lambda s: s.upper()
        stage = Stage(
            name="implement",
            prompt="Do it: {previous_response}",
            model="claude-sonnet-4-5",
            sandbox="docker",
            system_prompt="You are an implementer.",
            timeout=120,
            transform=transform_fn,
        )
        assert stage.sandbox == "docker"
        assert stage.transform is transform_fn


class TestPipelineResult:
    """Test PipelineResult dataclass."""

    def test_empty(self):
        result = PipelineResult()
        assert result.stages == []
        assert result.final_response == ""
        assert result.total_duration == 0.0
        assert not result.success

    def test_single_completed_stage(self):
        result = PipelineResult(stages=[
            SessionResult(
                agent_id="pipeline-research",
                status=SessionStatus.COMPLETED,
                response="Found 3 issues",
                duration_seconds=5.0,
            ),
        ])
        assert result.final_response == "Found 3 issues"
        assert result.total_duration == 5.0
        assert result.success

    def test_multiple_stages(self):
        result = PipelineResult(stages=[
            SessionResult(
                agent_id="pipeline-research",
                status=SessionStatus.COMPLETED,
                response="Research done",
                duration_seconds=3.0,
            ),
            SessionResult(
                agent_id="pipeline-plan",
                status=SessionStatus.COMPLETED,
                response="Plan created",
                duration_seconds=4.0,
            ),
        ])
        assert result.final_response == "Plan created"
        assert result.total_duration == 7.0
        assert result.success

    def test_partial_failure(self):
        result = PipelineResult(stages=[
            SessionResult(
                agent_id="pipeline-research",
                status=SessionStatus.COMPLETED,
                response="Research done",
                duration_seconds=3.0,
            ),
            SessionResult(
                agent_id="pipeline-plan",
                status=SessionStatus.ERROR,
                error="Something broke",
                duration_seconds=1.0,
            ),
        ])
        assert not result.success
        assert result.total_duration == 4.0


class TestBuildPrompt:
    """Test prompt template building."""

    def test_first_stage_no_previous(self):
        stage = Stage(name="first", prompt="Do something")
        result = build_prompt(stage, None)
        assert result == "Do something"

    def test_placeholder_substitution(self):
        stage = Stage(name="plan", prompt="Plan based on: {previous_response}")
        result = build_prompt(stage, "Found 3 bugs")
        assert result == "Plan based on: Found 3 bugs"

    def test_no_placeholder_appends_context(self):
        stage = Stage(name="plan", prompt="Create a plan.")
        result = build_prompt(stage, "Research findings here")
        assert "Create a plan." in result
        assert "Research findings here" in result
        assert "Context from previous stage" in result

    def test_transform_applied(self):
        stage = Stage(
            name="plan",
            prompt="Plan: {previous_response}",
            transform=lambda s: s.upper(),
        )
        result = build_prompt(stage, "found bugs")
        assert result == "Plan: FOUND BUGS"

    def test_transform_not_applied_to_first_stage(self):
        stage = Stage(
            name="first",
            prompt="Start here",
            transform=lambda s: s.upper(),
        )
        result = build_prompt(stage, None)
        assert result == "Start here"

    def test_multiple_placeholders(self):
        stage = Stage(
            name="plan",
            prompt="Summary: {previous_response}\nDetails: {previous_response}",
        )
        result = build_prompt(stage, "data")
        assert result == "Summary: data\nDetails: data"


class TestPipelineInit:
    """Test Pipeline construction."""

    def test_empty_stages_raises(self):
        with pytest.raises(ValueError, match="at least one stage"):
            Pipeline([])

    def test_defaults(self):
        pipeline = Pipeline([Stage(name="s1", prompt="Go")])
        assert len(pipeline.stages) == 1
        assert pipeline.config.default_model == "claude-sonnet-4-5"

    def test_custom_config(self):
        config = AgentPoolConfig(default_model="claude-haiku-4-5", timeout=30)
        pipeline = Pipeline(
            [Stage(name="s1", prompt="Go")],
            config=config,
        )
        assert pipeline.config.default_model == "claude-haiku-4-5"
        assert pipeline.config.timeout == 30
