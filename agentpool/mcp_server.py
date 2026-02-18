"""
MCP coordination server for agentpool.

Exposes TaskBoard and MessageBus as MCP tools that agents can call
during their Claude SDK sessions. Runs as a stdio MCP server.

Tools exposed:
- claim_task: Claim the next available task from the board
- complete_task: Mark a task as completed with result
- fail_task: Mark a task as failed
- list_tasks: See all tasks and their status
- send_message: Send a message to another agent
- broadcast_message: Send a message to all agents
- check_messages: Check inbox for new messages

Usage:
    The AgentPool starts this server and passes it as an MCP server
    config to each agent's ClaudeSDKClient. The server reads its
    state directory from the AGENTPOOL_STATE_DIR environment variable
    and the agent ID from AGENTPOOL_AGENT_ID.
"""

import json
import os
import sys
from pathlib import Path


def main():
    """
    Stdio MCP server entry point.

    Reads JSON-RPC messages from stdin, dispatches to handlers,
    writes responses to stdout. Minimal implementation — no framework.
    """
    state_dir = Path(os.environ.get("AGENTPOOL_STATE_DIR", "/tmp/agentpool"))
    agent_id = os.environ.get("AGENTPOOL_AGENT_ID", "unknown")
    messages_file = state_dir / "messages.jsonl"

    # Lazy imports to keep startup fast
    from .tasks import TaskBoard

    board = TaskBoard(state_dir=state_dir)

    TOOLS = [
        {
            "name": "claim_task",
            "description": "Claim the next available task from the shared task board. Returns the task description or null if none available.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "complete_task",
            "description": "Mark a task as completed. Call this after you finish implementing a task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to complete"},
                    "result": {"type": "string", "description": "Summary of what was done"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "fail_task",
            "description": "Mark a task as failed if you cannot complete it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID"},
                    "error": {"type": "string", "description": "What went wrong"},
                },
                "required": ["task_id", "error"],
            },
        },
        {
            "name": "list_tasks",
            "description": "List all tasks on the board with their current status.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "send_message",
            "description": "Send a message to another agent. Use this to share findings, ask questions, or coordinate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Target agent ID"},
                    "content": {"type": "string", "description": "Message content"},
                },
                "required": ["to", "content"],
            },
        },
        {
            "name": "broadcast_message",
            "description": "Send a message to ALL other agents. Use sparingly.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Message content"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "check_messages",
            "description": "Check your inbox for messages from other agents.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]

    def handle_request(request: dict) -> dict:
        """Handle a single JSON-RPC request."""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "agentpool-coordinator",
                        "version": "0.1.0",
                    },
                },
            }

        if method == "notifications/initialized":
            return None  # notification, no response

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            result = dispatch_tool(tool_name, args, board, agent_id, state_dir, messages_file)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                },
            }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    # Main loop: read JSON-RPC from stdin, write to stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def dispatch_tool(
    tool_name: str,
    args: dict,
    board,
    agent_id: str,
    state_dir: Path,
    messages_file: Path,
) -> dict:
    """Dispatch a tool call to the appropriate handler."""

    if tool_name == "claim_task":
        task = board.claim(agent_id)
        if task:
            return {"claimed": True, "task_id": task.id, "description": task.description}
        return {"claimed": False, "message": "No tasks available"}

    if tool_name == "complete_task":
        try:
            board.complete(args["task_id"], args.get("result"))
            return {"success": True}
        except ValueError as e:
            return {"success": False, "error": str(e)}

    if tool_name == "fail_task":
        try:
            board.fail(args["task_id"], args.get("error", "Unknown error"))
            return {"success": True}
        except ValueError as e:
            return {"success": False, "error": str(e)}

    if tool_name == "list_tasks":
        return {"tasks": board.status()}

    if tool_name == "send_message":
        _write_message(messages_file, agent_id, args["to"], args["content"])
        return {"sent": True}

    if tool_name == "broadcast_message":
        _write_message(messages_file, agent_id, "*", args["content"])
        return {"sent": True}

    if tool_name == "check_messages":
        msgs = _read_messages(messages_file, agent_id)
        return {"messages": msgs}

    return {"error": f"Unknown tool: {tool_name}"}


def _write_message(messages_file: Path, from_id: str, to_id: str, content: str) -> None:
    """Append a message to the shared messages file (file-locked)."""
    import fcntl
    import time

    msg = {
        "from": from_id,
        "to": to_id,
        "content": content,
        "timestamp": time.time(),
        "read_by": [],
    }

    lock_file = messages_file.parent / "messages.lock"
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(messages_file, "a") as f:
                f.write(json.dumps(msg) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _read_messages(messages_file: Path, agent_id: str) -> list:
    """Read unread messages for an agent. Messages are addressed or broadcast."""
    if not messages_file.exists():
        return []

    import fcntl

    lock_file = messages_file.parent / "messages.lock"
    messages = []

    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            lines = messages_file.read_text().splitlines()
            updated_lines = []

            for line in lines:
                if not line.strip():
                    continue
                msg = json.loads(line)

                # Message is for this agent if addressed to them or broadcast
                is_for_me = (
                    msg["to"] == agent_id or msg["to"] == "*"
                ) and msg["from"] != agent_id

                if is_for_me and agent_id not in msg.get("read_by", []):
                    messages.append({
                        "from": msg["from"],
                        "content": msg["content"],
                        "timestamp": msg["timestamp"],
                    })
                    msg.setdefault("read_by", []).append(agent_id)

                updated_lines.append(json.dumps(msg))

            # Write back with updated read_by
            messages_file.write_text("\n".join(updated_lines) + "\n" if updated_lines else "")

        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

    return messages


if __name__ == "__main__":
    main()
