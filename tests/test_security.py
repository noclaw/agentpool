"""Tests for workspace security validation."""

from pathlib import Path

from agentpool.security import validate_workspace


class TestSecurity:

    def test_normal_path_allowed(self):
        assert validate_workspace(Path("/Users/jeff/projects/myapp"))

    def test_root_blocked(self):
        assert not validate_workspace(Path("/"))

    def test_etc_blocked(self):
        assert not validate_workspace(Path("/etc"))
        assert not validate_workspace(Path("/etc/passwd"))

    def test_system_paths_blocked(self):
        for path in ["/var", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys", "/root"]:
            assert not validate_workspace(Path(path)), f"{path} should be blocked"

    def test_allowed_root_enforced(self):
        allowed = Path("/Users/jeff/workspaces")
        assert validate_workspace(
            Path("/Users/jeff/workspaces/project1"), allowed_root=allowed
        )
        assert not validate_workspace(
            Path("/Users/jeff/other/project1"), allowed_root=allowed
        )

    def test_allowed_root_not_required(self):
        # Without allowed_root, any non-system path is fine
        assert validate_workspace(Path("/tmp/test"))
        assert validate_workspace(Path("/home/user/project"))

    def test_var_folders_allowed(self):
        # macOS per-user temp dirs under /var/folders are safe
        assert validate_workspace(Path("/var/folders/ab/cd/T/pytest-123"))

    def test_var_tmp_allowed(self):
        assert validate_workspace(Path("/var/tmp/agentpool-test"))

    def test_var_itself_still_blocked(self):
        assert not validate_workspace(Path("/var"))
        assert not validate_workspace(Path("/var/log"))
