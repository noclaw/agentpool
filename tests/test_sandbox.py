"""Tests for sandbox implementations."""

import tempfile
from pathlib import Path

import pytest

from agentpool.sandbox.local import LocalSandbox
from agentpool.sandbox.base import Sandbox


class TestLocalSandbox:

    async def test_start_creates_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "new_workspace"
            sandbox = LocalSandbox(workspace=workspace)
            await sandbox.start()
            assert workspace.exists()
            assert sandbox.is_running
            await sandbox.stop()
            assert not sandbox.is_running

    async def test_execute_simple_command(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            await sandbox.start()

            result = await sandbox.execute("echo hello")
            assert result.ok
            assert "hello" in result.stdout
            assert result.returncode == 0

            await sandbox.stop()

    async def test_execute_failing_command(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            await sandbox.start()

            result = await sandbox.execute("false")
            assert not result.ok
            assert result.returncode != 0

            await sandbox.stop()

    async def test_execute_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            await sandbox.start()

            result = await sandbox.execute("sleep 10", timeout=1)
            assert not result.ok
            assert "Timed out" in result.stderr

            await sandbox.stop()

    async def test_execute_before_start_raises(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            with pytest.raises(RuntimeError, match="not started"):
                await sandbox.execute("echo test")

    async def test_working_directory(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            sandbox = LocalSandbox(workspace=workspace)
            await sandbox.start()

            result = await sandbox.execute("pwd")
            assert result.ok
            assert str(workspace) in result.stdout

            await sandbox.stop()

    async def test_context_manager(self):
        with tempfile.TemporaryDirectory() as td:
            async with LocalSandbox(workspace=Path(td)) as sandbox:
                assert sandbox.is_running
                result = await sandbox.execute("echo works")
                assert result.ok
            assert not sandbox.is_running

    async def test_file_operations_in_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            await sandbox.start()

            await sandbox.execute("echo 'test content' > testfile.txt")
            result = await sandbox.execute("cat testfile.txt")
            assert "test content" in result.stdout

            await sandbox.stop()

    async def test_name_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            sandbox = LocalSandbox(workspace=Path(td))
            assert sandbox.name == "local"

            sandbox2 = LocalSandbox(workspace=Path(td), name="custom")
            assert sandbox2.name == "custom"
