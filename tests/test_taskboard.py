"""Tests for TaskBoard — shared task list with file locking."""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agentpool.tasks import TaskBoard, TaskStatus


class TestTaskBoardInMemory:
    """TaskBoard without file persistence."""

    def test_add_and_claim(self):
        board = TaskBoard()
        tid = board.add("Do something")
        task = board.claim("worker-1")
        assert task is not None
        assert task.id == tid
        assert task.assigned_to == "worker-1"
        assert task.status == TaskStatus.IN_PROGRESS

    def test_claim_empty_board(self):
        board = TaskBoard()
        assert board.claim("worker-1") is None

    def test_no_double_claim(self):
        board = TaskBoard()
        board.add("Task A")
        board.claim("worker-1")
        assert board.claim("worker-2") is None

    def test_complete(self):
        board = TaskBoard()
        tid = board.add("Task A")
        board.claim("worker-1")
        board.complete(tid, "Done")
        status = board.status()
        assert status[0]["status"] == "completed"
        assert status[0]["result"] == "Done"
        assert board.all_done

    def test_fail(self):
        board = TaskBoard()
        tid = board.add("Task A")
        board.claim("worker-1")
        board.fail(tid, "Broke")
        status = board.status()
        assert status[0]["status"] == "failed"
        assert status[0]["result"] == "Broke"
        assert board.all_done  # failed counts as done

    def test_complete_unknown_task(self):
        board = TaskBoard()
        with pytest.raises(ValueError, match="Task not found"):
            board.complete("nonexistent")

    def test_dependencies_block_claim(self):
        board = TaskBoard()
        t1 = board.add("First")
        t2 = board.add("Second", depends_on=[t1])

        # Worker claims t1, t2 should be blocked
        claimed = board.claim("worker-1")
        assert claimed.id == t1

        blocked = board.claim("worker-2")
        assert blocked is None

    def test_dependencies_unblock_after_complete(self):
        board = TaskBoard()
        t1 = board.add("First")
        t2 = board.add("Second", depends_on=[t1])

        board.claim("worker-1")
        board.complete(t1, "Done")

        claimed = board.claim("worker-2")
        assert claimed is not None
        assert claimed.id == t2

    def test_multiple_dependencies(self):
        board = TaskBoard()
        t1 = board.add("A")
        t2 = board.add("B")
        t3 = board.add("C", depends_on=[t1, t2])

        board.claim("w1")
        board.complete(t1)

        # t3 still blocked — t2 not done
        board.claim("w2")  # claims t2
        assert board.claim("w3") is None  # t3 blocked

        board.complete(t2)
        claimed = board.claim("w3")
        assert claimed.id == t3

    def test_pending_and_completed_counts(self):
        board = TaskBoard()
        board.add("A")
        board.add("B")
        assert board.pending_count == 2
        assert board.completed_count == 0

        tid = board.claim("w1").id
        board.complete(tid)
        assert board.pending_count == 1
        assert board.completed_count == 1

    def test_all_done_empty_board(self):
        board = TaskBoard()
        assert not board.all_done  # no tasks means not "done"


class TestTaskBoardPersistence:
    """TaskBoard with file-backed state."""

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            # Create and populate
            board1 = TaskBoard(state_dir=path)
            t1 = board1.add("Task A")
            board1.claim("worker-1")
            board1.complete(t1, "Done")

            t2 = board1.add("Task B")

            # Reload from disk
            board2 = TaskBoard(state_dir=path)
            status = board2.status()
            assert len(status) == 2
            assert status[0]["status"] == "completed"
            assert status[1]["status"] == "pending"

    def test_claim_with_file_locking(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board = TaskBoard(state_dir=path)
            board.add("Task A")
            board.add("Task B")

            # Both claims should succeed (different tasks)
            c1 = board.claim("w1")
            c2 = board.claim("w2")
            assert c1 is not None
            assert c2 is not None
            assert c1.id != c2.id

    def test_claim_persists_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            board1.add("Task A")
            board1.claim("w1")

            # Reload — should see claimed state
            board2 = TaskBoard(state_dir=path)
            status = board2.status()
            assert status[0]["status"] == "in_progress"
            assert status[0]["assigned_to"] == "w1"


class TestTaskBoardReload:
    """Test reload() and auto-reload behavior."""

    def test_reload_explicit(self):
        """reload() picks up changes written by another process."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            tid = board1.add("Task A")
            board1.claim("w1")
            board1.complete(tid, "Done")

            # board2 loaded before complete — sees stale state initially
            # but after reload sees the update
            board2 = TaskBoard(state_dir=path)
            # Mutate on disk via board1
            tid2 = board1.add("Task B")
            board2.reload()
            assert len(board2.status()) == 2

    def test_auto_reload_completed_count(self):
        """completed_count auto-reloads from disk."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            tid = board1.add("Task A")
            board1.claim("w1")

            board2 = TaskBoard(state_dir=path)
            assert board2.completed_count == 0

            # Complete via board1 (simulates another process)
            board1.complete(tid, "Done")

            # board2 should see the update automatically
            assert board2.completed_count == 1

    def test_auto_reload_pending_count(self):
        """pending_count auto-reloads from disk."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            board1.add("Task A")

            board2 = TaskBoard(state_dir=path)
            assert board2.pending_count == 1

            board1.add("Task B")
            assert board2.pending_count == 2

    def test_auto_reload_all_done(self):
        """all_done auto-reloads from disk."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            tid = board1.add("Task A")
            board1.claim("w1")

            board2 = TaskBoard(state_dir=path)
            assert not board2.all_done

            board1.complete(tid)
            assert board2.all_done

    def test_auto_reload_status(self):
        """status() auto-reloads from disk."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            tid = board1.add("Task A")
            board1.claim("w1")

            board2 = TaskBoard(state_dir=path)
            assert board2.status()[0]["status"] == "in_progress"

            board1.complete(tid, "Done")
            assert board2.status()[0]["status"] == "completed"

    def test_reload_noop_in_memory(self):
        """reload() is a no-op for in-memory boards."""
        board = TaskBoard()
        board.add("Task A")
        board.reload()  # should not crash
        assert board.pending_count == 1


class TestTaskBoardConcurrentMutations:
    """Test that complete()/fail() don't overwrite concurrent changes."""

    def test_concurrent_complete(self):
        """Two processes completing different tasks don't clobber each other."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            # Setup: two tasks, both claimed
            board = TaskBoard(state_dir=path)
            t1 = board.add("Task A")
            t2 = board.add("Task B")
            board.claim("w1")  # claims t1
            board.claim("w2")  # claims t2

            # Simulate two separate processes with their own board instances
            proc1 = TaskBoard(state_dir=path)
            proc2 = TaskBoard(state_dir=path)

            # proc1 completes t1
            proc1.complete(t1, "Result A")

            # proc2 completes t2 — should NOT overwrite t1's completion
            proc2.complete(t2, "Result B")

            # Verify both are completed
            final = TaskBoard(state_dir=path)
            by_id = {t["id"]: t for t in final.status()}
            assert by_id[t1]["status"] == "completed"
            assert by_id[t1]["result"] == "Result A"
            assert by_id[t2]["status"] == "completed"
            assert by_id[t2]["result"] == "Result B"

    def test_concurrent_fail_and_complete(self):
        """One process fails a task while another completes a different one."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board = TaskBoard(state_dir=path)
            t1 = board.add("Task A")
            t2 = board.add("Task B")
            board.claim("w1")
            board.claim("w2")

            proc1 = TaskBoard(state_dir=path)
            proc2 = TaskBoard(state_dir=path)

            proc1.complete(t1, "Done")
            proc2.fail(t2, "Broke")

            final = TaskBoard(state_dir=path)
            by_id = {t["id"]: t for t in final.status()}
            assert by_id[t1]["status"] == "completed"
            assert by_id[t2]["status"] == "failed"
            assert by_id[t2]["result"] == "Broke"


class TestTaskBoardPriority:
    """Test priority-based task claiming."""

    def test_higher_priority_claimed_first(self):
        board = TaskBoard()
        board.add("Low priority", priority=1)
        board.add("High priority", priority=10)
        board.add("Medium priority", priority=5)

        task = board.claim("w1")
        assert task.description == "High priority"

        task = board.claim("w2")
        assert task.description == "Medium priority"

        task = board.claim("w3")
        assert task.description == "Low priority"

    def test_same_priority_uses_creation_order(self):
        board = TaskBoard()
        board.add("First")
        board.add("Second")
        board.add("Third")

        task = board.claim("w1")
        assert task.description == "First"

    def test_priority_default_zero(self):
        board = TaskBoard()
        tid = board.add("Default")
        status = board.status()
        assert status[0]["priority"] == 0

    def test_priority_persists(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)

            board1 = TaskBoard(state_dir=path)
            board1.add("Low", priority=1)
            board1.add("High", priority=10)

            board2 = TaskBoard(state_dir=path)
            task = board2.claim("w1")
            assert task.description == "High"
            assert task.priority == 10

    def test_priority_with_dependencies(self):
        """High priority task blocked by dependency, lower one claimed first."""
        board = TaskBoard()
        t1 = board.add("Blocker", priority=0)
        board.add("High but blocked", priority=10, depends_on=[t1])
        board.add("Low but available", priority=1)

        # Should claim the blocker first (it's available and priority 1 > 0
        # among available tasks: blocker=0, low=1)
        task = board.claim("w1")
        assert task.description == "Low but available"

        task = board.claim("w2")
        assert task.description == "Blocker"


class TestTaskBoardStaleRecovery:
    """Test stale claim recovery and release methods."""

    def test_stale_task_reverted_to_pending(self):
        """A task IN_PROGRESS longer than stale_timeout gets reverted on next claim."""
        board = TaskBoard(stale_timeout=10)
        board.add("Task A")
        board.claim("w1")  # claims Task A

        # Simulate time passing beyond stale_timeout
        task = list(board._tasks.values())[0]
        task.claimed_at = time.time() - 20  # 20s ago, timeout is 10s

        # Next claim should sweep the stale task and reclaim it
        reclaimed = board.claim("w2")
        assert reclaimed is not None
        assert reclaimed.description == "Task A"
        assert reclaimed.assigned_to == "w2"

    def test_non_stale_task_not_reverted(self):
        """A recently claimed task is NOT reverted."""
        board = TaskBoard(stale_timeout=60)
        board.add("Task A")
        board.claim("w1")

        # claimed_at is now(), well within 60s timeout
        reclaimed = board.claim("w2")
        assert reclaimed is None  # nothing available

    def test_stale_timeout_none_disables_sweep(self):
        """With stale_timeout=None, no sweep happens."""
        board = TaskBoard(stale_timeout=None)
        board.add("Task A")
        board.claim("w1")

        task = list(board._tasks.values())[0]
        task.claimed_at = time.time() - 9999  # very old

        reclaimed = board.claim("w2")
        assert reclaimed is None  # no sweep, stays in_progress

    def test_claimed_at_set_on_claim(self):
        """claimed_at is set when a task is claimed."""
        board = TaskBoard()
        board.add("Task A")
        before = time.time()
        task = board.claim("w1")
        after = time.time()
        assert task.claimed_at is not None
        assert before <= task.claimed_at <= after

    def test_claimed_at_persists(self):
        """claimed_at survives serialization round-trip."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            board1 = TaskBoard(state_dir=path)
            board1.add("Task A")
            task = board1.claim("w1")
            claimed_at = task.claimed_at

            board2 = TaskBoard(state_dir=path)
            status = board2.status()
            assert status[0]["claimed_at"] == pytest.approx(claimed_at, abs=0.01)

    def test_release_task(self):
        """release() puts a claimed task back to PENDING."""
        board = TaskBoard()
        tid = board.add("Task A")
        board.claim("w1")
        board.release(tid)

        status = board.status()
        assert status[0]["status"] == "pending"
        assert status[0]["assigned_to"] is None
        assert status[0]["claimed_at"] is None

    def test_release_non_in_progress_raises(self):
        """release() on a PENDING or COMPLETED task raises."""
        board = TaskBoard()
        tid = board.add("Task A")
        with pytest.raises(ValueError, match="not in_progress"):
            board.release(tid)

    def test_release_unknown_task_raises(self):
        """release() on a nonexistent task raises."""
        board = TaskBoard()
        with pytest.raises(ValueError, match="Task not found"):
            board.release("nonexistent")

    def test_released_task_can_be_reclaimed(self):
        """After release(), the task is claimable by another agent."""
        board = TaskBoard()
        tid = board.add("Task A")
        board.claim("w1")
        board.release(tid)

        task = board.claim("w2")
        assert task is not None
        assert task.id == tid
        assert task.assigned_to == "w2"

    def test_release_agent_tasks(self):
        """release_agent_tasks() releases all tasks for a specific agent."""
        board = TaskBoard()
        board.add("Task A")
        board.add("Task B")
        board.add("Task C")
        board.claim("w1")  # claims A
        board.claim("w1")  # claims B
        board.claim("w2")  # claims C

        released = board.release_agent_tasks("w1")
        assert len(released) == 2
        assert board.pending_count == 2  # A and B back to pending

        # w2's task should be unaffected
        statuses = {t["assigned_to"]: t["status"] for t in board.status()}
        assert statuses["w2"] == "in_progress"

    def test_release_agent_tasks_empty(self):
        """release_agent_tasks() returns empty list when agent has no tasks."""
        board = TaskBoard()
        board.add("Task A")
        board.claim("w1")
        released = board.release_agent_tasks("w2")
        assert released == []

    def test_release_agent_tasks_file_backed(self):
        """release_agent_tasks() works with file-backed board."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            board1 = TaskBoard(state_dir=path)
            board1.add("Task A")
            board1.add("Task B")
            board1.claim("w1")
            board1.claim("w1")

            board2 = TaskBoard(state_dir=path)
            released = board2.release_agent_tasks("w1")
            assert len(released) == 2

            board3 = TaskBoard(state_dir=path)
            assert board3.pending_count == 2

    def test_stale_sweep_file_backed(self):
        """Stale sweep works with file-backed persistence."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            board1 = TaskBoard(state_dir=path, stale_timeout=10)
            board1.add("Task A")
            board1.claim("w1")

            # Manually make the task stale on disk
            state_file = path / "taskboard.json"
            data = json.loads(state_file.read_text())
            data["tasks"][0]["claimed_at"] = time.time() - 20
            state_file.write_text(json.dumps(data))

            # New board instance should sweep and reclaim
            board2 = TaskBoard(state_dir=path, stale_timeout=10)
            reclaimed = board2.claim("w2")
            assert reclaimed is not None
            assert reclaimed.assigned_to == "w2"
