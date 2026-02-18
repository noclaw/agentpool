"""
Security validation for agentpool sandboxes.

Validates workspace paths before mounting into Docker containers.
Prevents directory traversal and access to sensitive system paths.
"""

from pathlib import Path
from typing import Optional

from .logging import get_logger

logger = get_logger("security")

# Paths that must never be mounted (exact match only)
BLOCKED_EXACT = {Path("/")}

# System directories: workspace must not be inside these
BLOCKED_TREES = {
    Path("/etc"),
    Path("/var"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/boot"),
    Path("/dev"),
    Path("/proc"),
    Path("/sys"),
    Path("/root"),
}

# Safe subdirectories under blocked trees (e.g. macOS temp dirs under /var)
ALLOWED_SUBTREES = {
    Path("/var/folders"),  # macOS per-user temp
    Path("/var/tmp"),
}


def validate_workspace(workspace: Path, allowed_root: Optional[Path] = None) -> bool:
    """
    Validate a workspace path is safe for container mounting.

    Rules:
    - Cannot mount the root filesystem itself
    - Cannot mount inside system directories (/etc, /var, /usr, etc.)
    - If allowed_root is set, must be under that directory

    Args:
        workspace: The workspace path to validate
        allowed_root: If set, workspace must be under this directory

    Returns:
        True if the path is safe to mount
    """
    workspace = workspace.resolve()

    # Block exact matches (e.g. root)
    if workspace in BLOCKED_EXACT:
        logger.warning(f"Blocked workspace path: {workspace} (exact match)")
        return False

    # Check if workspace is under an allowed subtree (overrides blocked trees)
    for allowed in ALLOWED_SUBTREES:
        for check in (allowed, allowed.resolve()):
            try:
                workspace.relative_to(check)
                break  # under an allowed subtree, skip blocked tree check
            except ValueError:
                continue
        else:
            continue
        break  # found an allowed subtree match
    else:
        # Not under any allowed subtree — check blocked trees
        for blocked in BLOCKED_TREES:
            blocked_resolved = blocked.resolve()
            for check in (blocked, blocked_resolved):
                try:
                    workspace.relative_to(check)
                    logger.warning(f"Blocked workspace path: {workspace} (under {check})")
                    return False
                except ValueError:
                    continue

    # Check allowed root if specified
    if allowed_root:
        allowed_root = allowed_root.resolve()
        if not str(workspace).startswith(str(allowed_root)):
            logger.warning(
                f"Workspace {workspace} is not under allowed root {allowed_root}"
            )
            return False

    return True
