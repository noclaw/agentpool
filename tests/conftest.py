"""
Shared pytest configuration.

Integration tests (marked @pytest.mark.integration) are automatically skipped
when Claude API credentials are not set in the environment.

To run integration tests:
    export CLAUDE_CODE_OAUTH_TOKEN=...   # or ANTHROPIC_API_KEY=...
    pytest tests/test_integration.py -v -s
"""

import os

import pytest


def _has_credentials() -> bool:
    return bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY"))


def pytest_collection_modifyitems(config, items):
    """Skip integration and docker tests when prerequisites are missing."""
    skip_integration = pytest.mark.skip(
        reason="No API credentials. Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY."
    )
    skip_docker = pytest.mark.skip(reason="Docker not available")

    has_creds = _has_credentials()
    has_docker = _check_docker()

    for item in items:
        if "integration" in item.keywords and not has_creds:
            item.add_marker(skip_integration)
        if "docker" in item.keywords and not has_docker:
            item.add_marker(skip_docker)


def _check_docker() -> bool:
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
