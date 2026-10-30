"""Offline provenance, worker selection and fixture routing checks; no native launch."""
import importlib.util
import contextlib
import io
from types import SimpleNamespace
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / "probes/quiet_close_worker.py"
SPEC = importlib.util.spec_from_file_location("quiet_close_worker_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
canonical = probe.base


class QuietCloseWorkerProbeTests(unittest.TestCase):
    def test_activity_before_original_completion_is_allowed_but_later_activity_fails(self):
        final = "FINAL"
        completed = probe.datetime.fromisoformat("2026-10-30T12:00:01-04:00").timestamp()
        frames = [{"at": "2026-10-30T12:00:00-04:00", "lines": [final, "Working… esc to interrupt"]},
                  {"at": "2026-10-30T12:00:02-04:00", "lines": [final, "prompt"]}]
        tui = SimpleNamespace(frames=frames, screen=SimpleNamespace(display=frames[0]["lines"]), audit=None)
        probe.install_silent_audit(tui, final, "SUPPRESSED", completed)
        self.assertIsNotNone(tui.audit)
        self.assertIn("Working", tui.screen.display[-1])  # Stale precompletion screen is allowed.
        frames.append({"at": "2026-10-30T12:00:03-04:00", "lines": [final, "Working… esc to interrupt"]})
        with self.assertRaises(RuntimeError):
            probe.install_silent_audit(tui, final, "SUPPRESSED", completed)
        with self.assertRaises(RuntimeError):
            tui.audit(frames[-1])

    def test_cleanup_stops_every_resource_and_leaves_no_phase_running(self):
        order = []
        resources = [Mock(), Mock()]
        for n, resource in enumerate(resources):
            resource.stop.side_effect = lambda n=n: (order.append(n) or {"confirmed_dead": True, "output_eof": True})
        report = {"status": "RUNNING"}
        probe.stop_resources(resources, report)
        self.assertEqual(order, [1, 0])
        self.assertEqual(resources, [])
        probe.stop_resources(resources, report)  # Final cleanup is safe after per-phase cleanup.
        self.assertNotIn("cleanup_errors", report)

    def test_late_successful_request_during_phase_cleanup_fails_the_entire_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "nativepeers").mkdir()
            run = source / "run"
            run.mkdir()
            model = SimpleNamespace(parent_requests=2, records=[], errors=[])
            def phase(source, directory, binary, approval, name, report, resources):
                report.update(worker_id="owned-test-worker", parent_requests_after_final=0)
                self.assertEqual(probe.final_model_history(model, directory, "native", "FINAL", "SUPPRESSED", 2, True), 0)
                resource = Mock()
                def late_request():
                    model.parent_requests += 1  # A successful late request after the first sample.
                    return {"confirmed_dead": True, "output_eof": True}
                resource.stop.side_effect = late_request
                resources.append(resource)
                return lambda: probe.final_model_history(model, directory, "native", "FINAL", "SUPPRESSED", 2, True)
            actual = {"path": "/fixture/codex", "sha256": "a" * 64, "size": 1}
            with patch.object(probe, "PHASES", ("quiet",)), patch.object(probe, "phase_run", side_effect=phase), \
                    patch.object(probe.base, "prepare_run", return_value=run), \
                    patch.object(probe.base, "select_codex", return_value=(Path(actual["path"]), {})), \
                    patch.object(probe, "approved_record", return_value=actual), \
                    patch("nativepeers.compatibility.fingerprint", return_value=actual), \
                    patch.object(probe.base, "digest", return_value="b" * 64), \
                    patch.object(probe.base, "require_native_silence", return_value={}), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = probe.main(["--nativepeers-root", str(source), "--run", str(run)])
            self.assertEqual(code, 1)
            record = json.loads((run / "qualification.json").read_text())
            self.assertEqual(record["status"], "FAIL")
            self.assertIn("later parent model work", record["error"])

    def test_late_model_body_is_scanned_even_if_parent_counter_did_not_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            model = SimpleNamespace(parent_requests=2, records=[], errors=[])
            with patch.object(probe.base, "require_native_silence", return_value={}):
                self.assertEqual(probe.final_model_history(model, run, "native", "FINAL", "SUPPRESSED", 2, True), 0)
                probe.base.save(run / "model-0001.json", {"input": [{"role": "user", "content": "SUPPRESSED"}]})
                model.records.append({"number": 1})
                with self.assertRaises(RuntimeError):
                    probe.final_model_history(model, run, "native", "FINAL", "SUPPRESSED", 2, True)

    def test_help_never_checks_runtime_or_launches_native_or_network(self):
        with patch.object(probe.base, "select_codex", side_effect=AssertionError("runtime")), \
                patch.object(probe.Model, "__init__", side_effect=AssertionError("network")), \
                patch.object(probe.base.subprocess, "Popen", side_effect=AssertionError("native")):
            with self.assertRaises(SystemExit) as raised:
                probe.main(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_approval_requires_exact_installed_path_hash_size_and_no_revocation(self):
        actual = {"path": "/selected/codex", "sha256": "a" * 64, "size": 123}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            record = {**actual, "scope": "existing qualified record"}
            good = {"providers": {"codex": record}}
            probe.base.save(path, good)
            before = path.read_bytes()
            self.assertEqual(probe.approved_record(path, actual), record)
            self.assertEqual(path.read_bytes(), before)
            cases = [{"providers": {"codex": {**record, field: "changed"}}} for field in actual]
            cases += [{"providers": {"codex": record}, "retained": {"codex": {actual["sha256"]: {"revoked": True}}}},
                      {}, {"providers": {"codex": "invalid"}}]
            for document in cases:
                probe.base.save(path, document)
                with self.subTest(document=document), self.assertRaises(probe.base.Blocked):
                    probe.approved_record(path, actual)
            path.unlink()
            with self.assertRaises(probe.base.Blocked):
                probe.approved_record(path, actual)

    def test_approval_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.symlink_to(Path(directory) / "other.json")
            with self.assertRaises(probe.base.Blocked):
                probe.approved_record(path, {})

    def test_real_spawn_spec_uses_one_exact_analysis_worker_and_distinct_requests(self):
        specs = [probe.spawn_spec(phase, "token") for phase in probe.PHASES]
        self.assertEqual(len({spec["client_request_id"] for spec in specs}), 3)
        for spec in specs:
            self.assertEqual(spec["completion_mode"], "push")
            self.assertEqual(len(spec["workers"]), 1)
            worker = spec["workers"][0]
            self.assertEqual((worker["provider"], worker["model"], worker["reasoning_effort"]), ("codex", "gpt-6.1-sol", "xhigh"))
            self.assertEqual(worker["permission_profile"], "analysis")
            self.assertEqual(worker["workspace"], {"mode": "inherit-read-only"})
            self.assertNotIn("worker_id", worker)
            self.assertNotIn("fallbacks", worker)

    def accepted(self):
        envelope = {"message_id": "completion-1", "kind": "completion", "from": {"kind": "controller", "group_id": "real-group"},
                    "to": {"peer_key": "parent"}}
        outcome = {"message_id": "completion-1", "target": "parent", "state": "ACCEPTED_QUEUED",
                   "attempt": {"attempt_id": "attempt-1", "external_submission_id": "native-queue-1", "state": "ACCEPTED_QUEUED"},
                   "receipts": [{"stage": "ACCEPTED_QUEUED", "evidence_kind": "native-queue-admission", "source_native_id": "parent-native"}]}
        return outcome, envelope, {"data": [{"id": "native-queue-1"}]}

    def test_queued_proof_requires_real_controller_target_exact_native_id_and_unchanged_admission(self):
        outcome, envelope, queue = self.accepted()
        self.assertEqual(probe.require_accepted(outcome, envelope, queue, "real-group", "parent", "parent-native"), "native-queue-1")
        cases = [(outcome, {**envelope, "from": {"kind": "peer", "group_id": "real-group"}}, queue),
                 (outcome, envelope, {"data": [{"id": "replacement-native-id"}]}),
                 (outcome, envelope, {"data": []}),
                 ({**outcome, "state": "RETAINED"}, envelope, queue),
                 ({**outcome, "suppression": {"reason": "completion-phase-closed"}}, envelope, queue)]
        for before, frame, listing in cases:
            with self.subTest(before=before, frame=frame, listing=listing), self.assertRaises(RuntimeError):
                probe.require_accepted(before, frame, listing, "real-group", "parent", "parent-native")
        with self.assertRaises(RuntimeError):
            probe.require_accepted(outcome, envelope, queue, "another-group", "parent", "parent-native")
        with self.assertRaises(RuntimeError):
            probe.require_accepted(outcome, envelope, queue, "real-group", "parent", "another-native")

    def test_queued_helper_observes_acceptance_before_both_closes_and_final_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve()
            config, done, order = run / "config.json", run / "ready.json", []
            probe.base.save(config, {"run": str(run), "source": str(PATH.parents[2])})
            def cli(_, action, *args):
                order.append((action, *args))
                if action == "whoami":
                    return {"key": "fixture-parent"}
                if action == "spawn":
                    return {"group_id": "fixture-group"}
                if action == "notifications":
                    if args[0] == "close":
                        self.assertIn("observed-exact-accepted-id", order)
                        self.assertFalse(done.exists())
                    return {"state": "OPEN" if args[0] == "open" else "QUIET"}
                return [] if action == "inbox" else {"results": []}
            def observe(*_):
                self.assertFalse(done.exists())
                self.assertFalse((run / "closed.json").exists())
                order.append("observed-exact-accepted-id")
            with patch.dict(os.environ, {"NPEER_HOME": str(run / "peer-state"), "CODEX_HOME": str(run / "codex-home")}), \
                    patch.object(probe.base, "product_cli", side_effect=cli), patch.object(probe, "observe_accepted", side_effect=observe), \
                    patch.object(probe, "wait_file"):
                probe.helper(str(config), "queued", str(done))
            self.assertEqual(order.count(("notifications", "close")), 2)
            self.assertTrue(json.loads(done.read_text())["ok"])

    def test_failed_queued_id_proof_never_closes_or_reports_final_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve()
            config, done = run / "config.json", run / "ready.json"
            probe.base.save(config, {"run": str(run), "source": str(PATH.parents[2])})
            def cli(_, action, *args):
                return {"group_id": "fixture-group"} if action == "spawn" else {"key": "fixture-parent", "state": "OPEN"}
            with patch.dict(os.environ, {"NPEER_HOME": str(run / "peer-state"), "CODEX_HOME": str(run / "codex-home")}), \
                    patch.object(probe.base, "product_cli", side_effect=cli) as calls, \
                    patch.object(probe, "observe_accepted", side_effect=RuntimeError("wrong original queue ID")):
                with self.assertRaises(RuntimeError):
                    probe.helper(str(config), "queued", str(done))
            self.assertNotIn(("notifications", "close"), [tuple(call.args[1:]) for call in calls.call_args_list])
            self.assertEqual(sum(call.args[1] == "spawn" for call in calls.call_args_list), 1)
            self.assertFalse(json.loads(done.read_text())["ok"])

    @unittest.skipIf(canonical is None, "Canonical require_quiet_frame is unavailable")
    def test_canonical_quiet_frame_rejects_retention_rows_duplicate_finals_and_extra_footers(self):
        good = {"lines": ["FINAL", "Worked for 1s", "ready"]}
        cases = [{"lines": ["FINAL", "Blocked by hook"]},
                 {"lines": ["FINAL", "NativePeers notification retained"]},
                 {"lines": ["FINAL", "completion-phase-closed"]},
                 {"lines": ["FINAL", "FINAL"]},
                 {"lines": ["FINAL", "Worked for 1s", "Worked for 2s"]},
                 {"lines": ["FINAL", "Working..."]},
                 {"lines": ["FINAL", "WORKER_RESULT"]}]
        with patch.object(probe.base, "require_quiet_frame", canonical.require_quiet_frame, create=True):
            probe.require_silent_frame(good, "FINAL", "WORKER_RESULT")
            for frame in cases:
                with self.subTest(frame=frame), self.assertRaises(RuntimeError):
                    probe.require_silent_frame(frame, "FINAL", "WORKER_RESULT")

    def test_queued_suppression_keeps_original_attempt_receipts_and_follows_first_completed_final(self):
        before, _, _ = self.accepted()
        identity = {"native_session_id": "parent-native", "incarnation_id": "incarnation-1", "owner": {"pid": 100, "start": "birth"}}
        suppression = {"reason": "completion-phase-closed", "attempt_state": "ACCEPTED_QUEUED", "attempt_id": "attempt-1",
                       "native_id": "parent-native", "observed_incarnation": "incarnation-1", "owner": [100, "birth"]}
        event = {"at": 101, "payload_json": json.dumps(suppression)}
        after = {**copy.deepcopy(before), "suppression": suppression}
        probe.require_consumed(before, after, event, identity, 100)
        bad = copy.deepcopy(after)
        bad["receipts"].append({"stage": "INGESTED"})
        cases = [(bad, event), ({**after, "state": "RETAINED"}, event), (after, {**event, "at": 99}), (after, None)]
        for outcome, observed in cases:
            with self.subTest(outcome=outcome, observed=observed), self.assertRaises(RuntimeError):
                probe.require_consumed(before, outcome, observed, identity, 100)

    def model(self, directory, phase):
        model = object.__new__(probe.Model)
        model.run, model.phase, model.token = Path(directory), phase, "token"
        model.parent_requests, model.lock, model.plans = 0, threading.Lock(), {}
        return model

    def test_worker_request_uses_owned_barrier_without_counting_as_parent_input(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory, "quiet")
            payload = {"model": "gpt-6.1-sol", "reasoning": {"effort": "xhigh"}, "input": [
                {"role": "user", "content": "NativePeers assigned worker title: fixture. QCW_TASK_quiet_token"}]}
            with patch.object(probe, "wait_file") as barrier:
                response = model.answer(payload)
            barrier.assert_called_once_with(Path(directory) / "release")
            self.assertTrue((Path(directory) / "worker-entered").exists())
            self.assertEqual(model.parent_requests, 0)
            self.assertEqual(response["content"][0]["text"], "QCW_WORKER_RESULT_quiet_token")
            for key, value in (("model", "another-model"), ("reasoning", {"effort": "medium"})):
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    model.answer({**payload, key: value})

    def test_genuine_completion_frame_routes_to_expected_parent_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory, "open")
            response = model.answer({"input": [{"role": "user", "content":
                "NativePeers · Worker results\nQCW_WORKER_RESULT_open_token\n— NativePeers references —"}]})
            self.assertEqual(model.parent_requests, 1)
            self.assertEqual(response["content"][0]["text"], "QCW_PUSH_RECEIVED_open_token")

    def test_unknown_detached_birth_refuses_cleanup_and_is_never_signaled(self):
        from nativepeers import process
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "workers").mkdir()
            owner = {"pid": 100, "uid": 1, "start": "birth", "pgid": 100}
            probe.base.save(state / "workers/actual-manifest.json", {"owner": owner, "cleanup": None})
            tracker = probe.Detached(state)
            with patch.object(process, "owned_status", return_value="UNKNOWN"), \
                    patch.object(probe.base, "Family") as family:
                report = tracker.stop()
            family.assert_not_called()
            self.assertFalse(report["confirmed_dead"])
            self.assertFalse(report["output_eof"])
            self.assertEqual(report["members"][0]["state"], "UNKNOWN")

    def test_detached_dead_birth_still_requires_native_pipe_eof_record(self):
        from nativepeers import process
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "workers").mkdir()
            path = state / "workers/actual-manifest.json"
            owner = {"pid": 100, "uid": 1, "start": "birth", "pgid": 100}
            with patch.object(process, "owned_status", return_value="DEAD"):
                probe.base.save(path, {"owner": owner, "cleanup": {"output_eof": False}})
                self.assertFalse(probe.Detached(state).stop()["output_eof"])
                probe.base.save(path, {"owner": owner, "cleanup": {"output_eof": True}})
                self.assertFalse(probe.Detached(state).stop()["output_eof"])
                (state / "groups/actual-group").mkdir(parents=True)
                probe.base.save(state / "groups/actual-group/owner.json", {"owner": owner, "released": True, "cleanup_unresolved": []})
                report = probe.Detached(state).stop()
            self.assertTrue(report["confirmed_dead"])
            self.assertTrue(report["output_eof"])

    def test_detached_birth_race_is_not_added_to_owned_cleanup(self):
        from nativepeers import process
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "workers").mkdir()
            owner = {"pid": 100, "uid": 1, "start": "old-birth", "pgid": 100}
            probe.base.save(state / "workers/actual-manifest.json", {"owner": owner})
            tracker = probe.Detached(state)
            with patch.object(process, "owned_status", return_value="LIVE"), patch.object(probe.base, "Family") as family:
                family.return_value.birth = {**owner, "start": "replacement-birth"}
                with self.assertRaises(RuntimeError):
                    tracker.capture()
            self.assertEqual(tracker.families, {})
            family.return_value.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
