import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from aihq.runtime import Runtime
from scripts.seed_local import seed

if __package__:
    from .test_brain import brain_fixture, builder_payload, repository_files, write_json
else:
    from test_brain import brain_fixture, builder_payload, repository_files, write_json


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()
        self.execution = patch("aihq.adapters.execute", side_effect=AssertionError("Live external execution is forbidden"))
        self.execute = self.execution.start()
        self.addCleanup(self.execution.stop)

    def task(self, operation="memory.put", payload=None, title="Integration task"):
        if payload is None:
            payload = {"key": "next-step", "content": "Review the board."} if operation == "memory.put" else {}
        return self.runtime.action({"action": "task.create", "title": title,
                                    "operation": operation, "payload": payload})["id"]

    def external_task(self):
        return self.task("agent.prompt", {"adapter": "codex", "prompt": "Review only the local specification."})

    def approval(self, task_id, decision="approved"):
        approval = next(item for item in self.runtime.snapshot()["approvals"] if item["task_id"] == task_id)
        return self.runtime.action({"action": "approval.decide", "id": approval["id"], "decision": decision})

    def task_state(self, task_id):
        return next(item for item in self.runtime.snapshot()["tasks"] if item["id"] == task_id)

    def lifecycle(self, transition, app_id="project-board"):
        return self.runtime.action({"action": "app.lifecycle", "id": app_id, "transition": transition})

    def test_builder_preview_explicit_create_and_lifecycle_preserve_private_records(self):
        before = repository_files(self.repo)
        preview = self.runtime.action(builder_payload(action="builder.preview"))
        self.assertEqual(repository_files(self.repo), before)
        with self.assertRaises(ValueError):
            self.runtime.action(builder_payload(action="builder.create"))
        self.assertEqual(repository_files(self.repo), before)
        result = self.runtime.action(builder_payload(action="builder.create", confirm=True))
        self.assertEqual(result, preview)
        after_build = repository_files(self.repo)
        self.assertEqual(set(after_build) - set(before), {
            "apps/integration-board/app.json", "apps/integration-board/SPEC.md", "apps/integration-board/HANDOFF.md",
        })
        self.assertFalse(self.runtime.reconcile()["changed"])
        app_id = "integration-board"
        with self.assertRaises(ValueError):
            self.lifecycle("enable", app_id)
        self.lifecycle("install", app_id)
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "app.record", "id": app_id, "title": "Too early"})
        self.lifecycle("enable", app_id)
        record = self.runtime.action({"action": "app.record", "id": app_id,
                                      "title": "Private review", "status": "open", "note": "Owner-only note"})
        self.runtime = Runtime(root=self.repo, home=self.home)
        state = self.runtime.snapshot()
        self.assertEqual(state["records"][app_id][0]["id"], record["id"])
        self.assertEqual(state["records"][app_id][0]["note"], "Owner-only note")
        self.assertEqual(next(app for app in state["apps"] if app["id"] == app_id)["status"], "enabled")
        self.lifecycle("pause", app_id)
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "app.record", "id": app_id, "title": "Blocked while paused"})
        self.lifecycle("uninstall", app_id)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.assertEqual(self.runtime.snapshot()["records"][app_id][0]["id"], record["id"])
        self.lifecycle("install", app_id)
        self.lifecycle("enable", app_id)
        self.assertEqual(repository_files(self.repo), after_build)
        self.execute.assert_not_called()

    def test_local_memory_tasks_events_and_preferences_do_not_write_repository(self):
        before = repository_files(self.repo)
        self.runtime.action({"action": "memory.put", "key": "private-key", "content": "Private content"})
        self.task()
        self.runtime.tick()
        self.runtime.action({"action": "preferences.set", "theme": "midnight", "panels": ["tasks", "memory"]})
        self.runtime = Runtime(root=self.repo, home=self.home)
        state = self.runtime.snapshot()
        self.assertEqual(state["preferences"], {"theme": "midnight", "panels": ["tasks", "memory"]})
        self.assertIn("Private content", {memory["content"] for memory in state["memories"]})
        self.assertIn("task_succeeded", {event["event"] for event in state["events"]})
        self.assertEqual(repository_files(self.repo), before)
        self.assertTrue(self.runtime.database.is_relative_to(self.home))
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.runtime.database.stat().st_mode & 0o777, 0o600)
        for directory in ("runtime", "private", "secrets", "logs", "exports"):
            self.assertEqual((self.home / directory).stat().st_mode & 0o777, 0o700)

    def test_memory_upsert_retains_identity_and_task_runs_once(self):
        self.runtime.action({"action": "memory.put", "key": "next-step", "content": "Original"})
        original = self.runtime.snapshot()["memories"][0]["id"]
        task_id = self.task()
        self.assertEqual(self.task_state(task_id)["status"], "ready")
        self.assertEqual(self.runtime.tick()["processed"], [task_id])
        self.assertEqual(self.runtime.tick()["processed"], [])
        state = self.runtime.snapshot()
        self.assertEqual(len(state["memories"]), 1)
        self.assertEqual(state["memories"][0]["id"], original)
        self.assertEqual(state["memories"][0]["content"], "Review the board.")
        self.assertEqual(self.task_state(task_id)["status"], "succeeded")
        self.assertEqual(json.loads(self.task_state(task_id)["result"])["key"], "next-step")

    def test_task_memory_and_event_roll_back_together(self):
        task_id = self.task()
        before = self.runtime.snapshot()
        original_event = self.runtime.event

        def fail_after_event(connection, event, message):
            original_event(connection, event, message)
            if event == "task_succeeded":
                raise RuntimeError("Injected event failure")

        with patch.object(self.runtime, "event", side_effect=fail_after_event):
            with self.assertRaisesRegex(RuntimeError, "Injected event failure"):
                self.runtime.tick()
        failed = self.runtime.snapshot()
        self.assertEqual(failed["memories"], before["memories"])
        self.assertEqual(failed["events"], before["events"])
        self.assertEqual(self.task_state(task_id)["status"], "ready")
        self.assertEqual(self.runtime.tick()["processed"], [task_id])

    def test_failed_task_creation_rolls_back_insert_and_event(self):
        before = self.runtime.snapshot()
        with patch.object(self.runtime, "event", side_effect=RuntimeError("Cannot append event")):
            with self.assertRaises(RuntimeError):
                self.task()
        state = self.runtime.snapshot()
        self.assertEqual(state["tasks"], before["tasks"])
        self.assertEqual(state["events"], before["events"])

    def test_schedule_only_runs_when_due_and_missed_intervals_coalesce(self):
        epoch = 1700000000
        with patch("aihq.runtime.time.time", return_value=epoch):
            schedule_id = self.runtime.action({"action": "schedule.create", "name": "Daily review",
                                                "workflow": "daily-digest", "interval_seconds": 60})["id"]
        self.assertEqual(self.runtime.tick(now=epoch + 59)["processed"], [])
        first = self.runtime.tick(now=epoch + 60)["processed"]
        self.assertEqual(len(first), 1)
        self.assertEqual(self.runtime.tick(now=epoch + 60)["processed"], [])
        overdue = self.runtime.tick(now=epoch + 6000)["processed"]
        self.assertEqual(len(overdue), 1)
        self.assertNotEqual(first, overdue)
        self.assertEqual(self.runtime.tick(now=epoch + 6000)["processed"], [])
        with self.runtime.connect() as connection:
            row = connection.execute("SELECT next_run FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            self.assertEqual(row["next_run"], epoch + 6060)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 2)
        state = self.runtime.snapshot()
        self.assertEqual(len(state["memories"]), 1)
        self.assertEqual(state["memories"][0]["key"], "latest-digest")
        self.assertEqual({task["status"] for task in state["tasks"]}, {"succeeded"})

    def test_concurrent_workers_claim_due_schedule_and_queued_tasks_once(self):
        epoch = 1700000000
        with patch("aihq.runtime.time.time", return_value=epoch):
            self.runtime.action({"action": "schedule.create", "name": "Review",
                                 "workflow": "daily-digest", "interval_seconds": 60})
        queued = {self.task(payload={"key": f"key-{index}", "content": "Once"}) for index in range(8)}
        workers = [Runtime(root=self.repo, home=self.home) for _ in range(4)]
        barrier = threading.Barrier(len(workers))

        def work(runtime):
            barrier.wait(timeout=10)
            return runtime.tick(now=epoch + 600, max_tasks=20)["processed"]

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            results = list(pool.map(work, workers))
        processed = [task_id for result in results for task_id in result]
        self.assertEqual(len(processed), 9)
        self.assertEqual(len(set(processed)), 9)
        self.assertTrue(queued.issubset(processed))
        state = self.runtime.snapshot()
        self.assertEqual(len(state["memories"]), 9)
        self.assertEqual(len([event for event in state["events"] if event["event"] == "task_succeeded"]), 9)
        self.assertEqual(self.runtime.tick(now=epoch + 600)["processed"], [])

    def test_max_tasks_leaves_remaining_work_for_next_tick(self):
        queued = {self.task(payload={"key": f"key-{index}", "content": "Queued"}) for index in range(3)}
        first = self.runtime.tick(max_tasks=1)["processed"]
        second = self.runtime.tick(max_tasks=20)["processed"]
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertEqual(set(first + second), queued)

    def test_external_task_requires_approval_and_explicit_external_worker(self):
        task_id = self.external_task()
        self.assertEqual(self.task_state(task_id)["status"], "awaiting_approval")
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [])
        self.approval(task_id)
        self.assertEqual(self.runtime.tick()["processed"], [])
        self.assertEqual(self.runtime.action({"action": "worker.tick", "allow_external": True})["processed"], [])
        self.assertEqual(self.task_state(task_id)["status"], "ready")
        self.execute.assert_not_called()
        self.execute.side_effect = None
        self.execute.return_value = "Mock review completed"
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [task_id])
        state = self.task_state(task_id)
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual(state["result"], "Mock review completed")
        approval = next(item for item in self.runtime.snapshot()["approvals"] if item["task_id"] == task_id)
        self.execute.assert_called_once_with(state["payload"], self.repo.resolve(),
                                             expected_binding=approval["prompt_hash"], brain=self.runtime.brain)
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [])

    def test_approval_is_one_shot_and_rejection_or_cancel_never_executes(self):
        for decision in ("rejected", "cancel"):
            task_id = self.external_task()
            if decision == "cancel":
                self.runtime.action({"action": "task.cancel", "id": task_id})
            else:
                self.approval(task_id, decision)
            with self.assertRaises(ValueError):
                self.approval(task_id)
            self.assertEqual(self.task_state(task_id)["status"], "canceled")
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [])
        self.execute.assert_not_called()

    def test_approval_cannot_be_granted_for_changed_prompt(self):
        task_id = self.external_task()
        with self.runtime.connect() as connection:
            payload = json.loads(connection.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            payload["prompt"] = "Different unapproved request"
            connection.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload), task_id))
        with self.assertRaisesRegex(ValueError, "context changed"):
            self.approval(task_id)
        self.assertEqual(self.task_state(task_id)["status"], "awaiting_approval")
        self.execute.assert_not_called()

    def test_approval_cannot_be_granted_for_changed_brain(self):
        task_id = self.external_task()
        with (self.repo / "AGENTS.md").open("a") as stream:
            stream.write("\nNew approval policy\n")
        self.runtime.reconcile()
        with self.assertRaisesRegex(ValueError, "context changed"):
            self.approval(task_id)

    def test_approval_binds_to_executable_bytes_at_decision_and_execution(self):
        binary = Path(self.temporary.name) / "fake-codex"
        binary.write_bytes(b"Never executed: version 1")
        with patch("aihq.adapters.shutil.which", return_value=str(binary)):
            before_approval = self.external_task()
            binary.write_bytes(b"Never executed: version 2")
            with self.assertRaisesRegex(ValueError, "context changed"):
                self.approval(before_approval)
            before_execution = self.external_task()
            self.approval(before_execution)
            binary.write_bytes(b"Never executed: version 3")
            self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [before_execution])
        state = self.task_state(before_execution)
        self.assertEqual(state["status"], "failed")
        self.assertIn("stale", state["error"])
        self.execute.assert_not_called()

    def test_reconciled_brain_change_does_not_restore_old_approval(self):
        task_id = self.external_task()
        self.approval(task_id)
        (self.repo / "vault" / "new-policy.md").write_text("Require a fresh review.")
        self.runtime.reconcile()
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [task_id])
        self.assertEqual(self.task_state(task_id)["status"], "failed")
        self.execute.assert_not_called()

    def test_missing_approval_cannot_be_bypassed_by_ready_status(self):
        task_id = self.external_task()
        with self.runtime.connect() as connection:
            connection.execute("DELETE FROM approvals WHERE task_id=?", (task_id,))
            connection.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
        self.runtime.tick(allow_external=True)
        self.assertEqual(self.task_state(task_id)["status"], "failed")
        self.execute.assert_not_called()

    def test_external_failure_is_persisted_and_local_work_continues(self):
        task_id = self.external_task()
        self.approval(task_id)
        local_task = self.task()
        self.execute.side_effect = TimeoutError("Mock provider timed out")
        self.assertEqual(set(self.runtime.tick(allow_external=True)["processed"]), {task_id, local_task})
        self.assertEqual(self.task_state(task_id)["status"], "failed")
        self.assertIn("timed out", self.task_state(task_id)["error"])
        self.assertEqual(self.task_state(local_task)["status"], "succeeded")
        self.assertEqual(self.runtime.tick(allow_external=True)["processed"], [])

    def test_concurrent_workers_do_not_execute_same_approved_external_task(self):
        task_id = self.external_task()
        self.approval(task_id)
        started = threading.Event()
        release = threading.Event()

        def execute(payload, root, **kwargs):
            self.assertEqual(kwargs["brain"], self.runtime.brain)
            self.assertEqual(kwargs["expected_binding"], approved_binding)
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError("Test did not release the mocked adapter")
            return "One mock execution"

        approved_binding = next(item["prompt_hash"] for item in self.runtime.snapshot()["approvals"] if item["task_id"] == task_id)
        self.execute.side_effect = execute
        other = Runtime(root=self.repo, home=self.home)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.runtime.tick, allow_external=True)
            try:
                self.assertTrue(started.wait(timeout=10))
                self.assertEqual(self.task_state(task_id)["status"], "running")
                self.assertEqual(other.tick(allow_external=True)["processed"], [])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)["processed"], [task_id])
        self.execute.assert_called_once()
        self.assertEqual(self.task_state(task_id)["status"], "succeeded")

    @patch("aihq.adapters.binding", return_value="pagination-only-binding")
    def test_pending_approval_retains_task_and_exact_prompt_beyond_recent_hundred(self, binding):
        task_id = self.external_task()
        for index in range(105):
            self.task(title=f"Later local task {index}")
        state = self.runtime.snapshot()
        approval = next(item for item in state["approvals"] if item["task_id"] == task_id)
        linked = next(item for item in state["tasks"] if item["id"] == task_id)
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(linked["status"], "awaiting_approval")
        self.assertEqual(linked["payload"]["prompt"], "Review only the local specification.")
        self.assertEqual(len({item["id"] for item in state["tasks"]}), len(state["tasks"]))
        self.assertEqual(len(state["tasks"]), 101)
        binding.assert_called_once()
        self.approval(task_id, "rejected")
        self.execute.assert_not_called()

    @patch("aihq.adapters.binding", return_value="pagination-only-binding")
    def test_pending_approval_is_not_hidden_by_more_than_hundred_decided_approvals(self, binding):
        pending_id = self.external_task()
        for _ in range(105):
            task_id = self.external_task()
            self.runtime.action({"action": "task.cancel", "id": task_id})
        state = self.runtime.snapshot()
        self.assertEqual(len(state["approvals"]), 100)
        self.assertEqual(state["approvals"][0]["task_id"], pending_id)
        self.assertEqual(state["approvals"][0]["status"], "pending")
        linked = next(item for item in state["tasks"] if item["id"] == pending_id)
        self.assertEqual(linked["payload"]["prompt"], "Review only the local specification.")
        self.assertEqual(binding.call_count, 106)
        self.approval(pending_id, "rejected")
        self.execute.assert_not_called()

    def test_stale_brain_blocks_worker_lifecycle_and_records_until_reconcile(self):
        self.lifecycle("install")
        self.lifecycle("enable")
        task_id = self.task()
        filename = self.repo / "apps" / "project-board" / "app.json"
        manifest = json.loads(filename.read_text())
        write_json(filename, {**manifest, "description": "Changed valid declaration"})
        self.assertTrue(self.runtime.snapshot()["brain"]["drift"])
        for action in (
            {"action": "worker.tick"},
            {"action": "app.lifecycle", "id": "project-board", "transition": "pause"},
            {"action": "app.record", "id": "project-board", "title": "Unreconciled write"},
        ):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "reconcile"):
                self.runtime.action(action)
        self.assertEqual(self.task_state(task_id)["status"], "ready")
        self.runtime.reconcile()
        self.assertFalse(self.runtime.snapshot()["brain"]["drift"])
        self.assertEqual(next(app for app in self.runtime.snapshot()["apps"] if app["id"] == "project-board")["status"], "available")
        self.lifecycle("install")
        self.lifecycle("enable")
        self.assertEqual(self.runtime.tick()["processed"], [task_id])

    def test_removed_workflow_disables_schedule_and_removed_app_retains_records(self):
        self.lifecycle("install")
        self.lifecycle("enable")
        self.runtime.action({"action": "app.record", "id": "project-board", "title": "Keep me"})
        self.runtime.action({"action": "schedule.create", "name": "Digest", "workflow": "daily-digest", "interval_seconds": 60})
        (self.repo / "workflows" / "daily-digest.json").unlink()
        (self.repo / "apps" / "project-board" / "app.json").unlink()
        self.runtime.reconcile()
        state = self.runtime.snapshot()
        self.assertFalse(state["schedules"][0]["enabled"])
        self.assertNotIn("project-board", {app["id"] for app in state["apps"]})
        self.assertEqual(state["records"]["project-board"][0]["title"], "Keep me")
        with self.assertRaises(ValueError):
            self.lifecycle("enable")

    def test_restoring_identical_app_definition_after_removal_allows_reinstall(self):
        self.lifecycle("install")
        self.lifecycle("enable")
        record = self.runtime.action({"action": "app.record", "id": "project-board", "title": "Keep through Git revert"})
        filename = self.repo / "apps" / "project-board" / "app.json"
        original = filename.read_bytes()
        filename.unlink()
        self.runtime.reconcile()
        with self.runtime.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM apps WHERE id='project-board'").fetchone()[0], "unavailable")
        filename.write_bytes(original)
        self.runtime.reconcile()
        state = self.runtime.snapshot()
        self.assertEqual(next(app for app in state["apps"] if app["id"] == "project-board")["status"], "available")
        self.lifecycle("install")
        self.lifecycle("enable")
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.assertEqual(self.runtime.snapshot()["records"]["project-board"][0]["id"], record["id"])

    def test_handoff_excludes_private_memory_records_approvals_and_credentials(self):
        self.runtime.action({"action": "memory.put", "key": "private", "content": "PRIVATE_MEMORY_SENTINEL"})
        with self.runtime.connect() as connection:
            connection.execute("INSERT INTO records(id,app_id,title,status,note,created) VALUES (?,?,?,?,?,?)",
                               ("handoff-fixture", "project-board", "Private record", "open", "PRIVATE_RECORD_SENTINEL", 1700000000))
        task_id = self.external_task()
        self.approval(task_id)
        (self.home / "secrets" / "credential").write_text("PRIVATE_CREDENTIAL_SENTINEL")
        packet = self.runtime.handoff()
        self.assertIsNone(packet["task"])
        self.assertNotIn("PRIVATE_MEMORY_SENTINEL", json.dumps(packet))
        self.assertNotIn("PRIVATE_RECORD_SENTINEL", json.dumps(packet))
        self.assertNotIn("PRIVATE_CREDENTIAL_SENTINEL", json.dumps(packet))
        self.assertNotIn("approvals", packet)
        self.assertIn("Reauthorize", packet["permissions"])
        selected = self.runtime.handoff(task_id)
        self.assertEqual(selected["task"]["payload"]["prompt"], self.task_state(task_id)["payload"]["prompt"])
        self.assertIn("explicitly selected task", selected["private_data"])
        with self.assertRaises(ValueError):
            self.runtime.handoff("missing-task")
        self.execute.assert_not_called()

    def test_invalid_actions_task_payloads_and_intervals_are_atomic(self):
        before = self.runtime.snapshot()
        actions = [None, [], {"action": "unknown"},
                   {"action": "memory.put", "key": "", "content": "bad"},
                   {"action": "task.create", "title": "Bad", "operation": "shell.execute", "payload": {}},
                   {"action": "task.create", "title": "Bad", "operation": "digest.create", "payload": {"command": "whoami"}},
                   {"action": "task.create", "title": "Bad", "operation": "memory.put", "payload": []},
                   {"action": "task.create", "title": "Bad", "operation": "agent.prompt", "payload": {"adapter": "devin", "prompt": "Do work"}},
                   {"action": "app.lifecycle", "id": "../escape", "transition": "install"}]
        actions.extend({"action": "schedule.create", "name": "Bad interval", "workflow": "daily-digest", "interval_seconds": interval}
                       for interval in (True, 59, 31536001, "60", 60.0))
        for action in actions:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.runtime.action(action)
        after = self.runtime.snapshot()
        for field in ("tasks", "memories", "events", "schedules", "approvals"):
            self.assertEqual(after[field], before[field])

    def test_runtime_home_rejects_repo_home_overlap_and_symlinks(self):
        for home in (Path("relative-home"), self.repo / "data", self.repo, self.repo.parent, Path.home()):
            with self.subTest(home=home), self.assertRaises(ValueError):
                Runtime(root=self.repo, home=home)
        target = Path(self.temporary.name) / "target"
        target.mkdir()
        link = Path(self.temporary.name) / "linked-home"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            Runtime(root=self.repo, home=link)

    def test_runtime_rejects_symlink_subdirectories_and_database(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        for name in ("runtime", "private", "secrets", "logs", "exports", "projects", "knowledge", "inbox", "backups", "approvals"):
            home = Path(self.temporary.name) / f"bad-{name}"
            home.mkdir()
            (home / name).symlink_to(outside, target_is_directory=True)
            with self.subTest(name=name), self.assertRaises(ValueError):
                Runtime(root=self.repo, home=home)
            self.assertFalse((home / "runtime" / "os.sqlite3").exists())
        self.assertEqual(list(outside.iterdir()), [])
        home = Path(self.temporary.name) / "bad-database"
        (home / "runtime").mkdir(parents=True)
        outside_database = outside / "os.sqlite3"
        outside_database.write_bytes(b"untouched")
        (home / "runtime" / "os.sqlite3").symlink_to(outside_database)
        with self.assertRaises(ValueError):
            Runtime(root=self.repo, home=home)
        self.assertEqual(outside_database.read_bytes(), b"untouched")

    def test_seed_rejects_symlinked_projects_before_any_workspace_writes(self):
        home = Path(self.temporary.name) / "seed-home"
        outside = Path(self.temporary.name) / "seed-outside"
        home.mkdir()
        outside.mkdir()
        (home / "projects").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            seed(home)
        self.assertEqual({item.name for item in home.iterdir()}, {"projects"})
        self.assertEqual(list(outside.iterdir()), [])

    def test_seed_preflights_late_file_and_nested_symlinks_before_earlier_writes(self):
        for relative in ("runtime/README.md", "projects/first-project/PROJECT.md"):
            with self.subTest(relative=relative):
                home = Path(self.temporary.name) / ("seed-" + relative.replace("/", "-"))
                filename = home / relative
                filename.parent.mkdir(parents=True)
                outside = Path(self.temporary.name) / (home.name + "-outside.md")
                outside.write_text("Original private document")
                filename.symlink_to(outside)
                before = {str(item.relative_to(home)) for item in home.rglob("*")}
                with self.assertRaises(ValueError):
                    seed(home)
                self.assertEqual({str(item.relative_to(home)) for item in home.rglob("*")}, before)
                self.assertFalse((home / "README.md").exists())
                self.assertEqual(outside.read_text(), "Original private document")

    def test_unknown_database_schema_is_not_silently_overwritten(self):
        with self.runtime.connect() as connection:
            self.runtime.set_meta(connection, "schema_version", 99)
        with self.assertRaisesRegex(ValueError, "Unsupported local database schema"):
            Runtime(root=self.repo, home=self.home)
        with closing(sqlite3.connect(self.runtime.database)) as connection:
            self.assertEqual(json.loads(connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]), 99)


if __name__ == "__main__":
    unittest.main()
