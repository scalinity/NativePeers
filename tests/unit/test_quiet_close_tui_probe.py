"""Offline checks for the standalone acceptance harness; no candidate is launched."""
import gzip
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


PROBE = Path(__file__).resolve().parents[1] / "probes/quiet_close_tui.py"
SPEC = importlib.util.spec_from_file_location("quiet_close_tui_probe", PROBE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class QuietCloseTuiProbeTests(unittest.TestCase):
    def test_quiet_render_rejects_hook_rows_and_extra_work_footer(self):
        final = "IDENTIFIABLE_FINAL"
        for text in ("Blocked by hook", "NativePeers notification retained", "completion-phase-closed",
                     "Worked for 1s\nWorked for 2s", "Working… esc to interrupt"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                probe.require_quiet_frame({"lines": [final, text]}, final)
        probe.require_quiet_frame({"chat_tail": ["Worked for 1s", "Blocked by hook", final],
                                   "lines": ["Worked for 2s", "prompt"]}, final)
        with self.assertRaises(RuntimeError):
            probe.require_quiet_frame({"lines": [final, "Worked for 1s", final, "Worked for 2s"]}, final)

    def test_reopen_silence_uses_the_new_permitted_response_as_its_anchor(self):
        frame = {"lines": ["WARM_FINAL", "Worked for 1s", "human reopen", "OPEN_FINAL", "Worked for 2s"]}
        with self.assertRaises(RuntimeError):
            probe.require_quiet_frame(frame, "WARM_FINAL")
        probe.require_quiet_frame(frame, "OPEN_FINAL")
        with self.assertRaises(RuntimeError):
            probe.require_quiet_frame({"lines": frame["lines"] + ["Worked for 3s"]}, "OPEN_FINAL")

    def test_reopen_audits_current_and_completion_wait_frames_without_later_output(self):
        import inspect, textwrap
        from types import SimpleNamespace
        source = inspect.getsource(probe.run_probe)
        start = source.index('    receiver.audit = audit_body\n    opened = command(receiver, "open")')
        end = source.index('    cold_rows, cold_done, cold_final = begin_batch("cold")', start)
        block = compile(textwrap.dedent(source[start:end]), "actual-reopen-block", "exec")
        for stage in ("current", "completion-wait"):
            for forbidden in ("NativePeers notification retained", "Worked for 3s"):
                with self.subTest(stage=stage, forbidden=forbidden), tempfile.TemporaryDirectory() as tmp:
                    run = Path(tmp)
                    (run / "close-warm-policy.json").write_text(json.dumps({"message_cutoff": 7}))
                    clean = {"lines": ["OPEN_FINAL", "Worked for 1s"]}
                    dirty = {"lines": clean["lines"] + [forbidden]}
                    receiver = SimpleNamespace(audit=None, frames=[dirty if stage == "current" else clean])
                    quiet_final = ["WARM_FINAL"]
                    def pump(predicate, description):
                        receiver.frames.append(dirty)
                        receiver.audit(dirty)  # No further terminal output after this frame.
                    environment = dict(receiver=receiver, audit_body=lambda _: None,
                        audit_quiet=lambda frame: probe.require_quiet_frame(frame, quiet_final[0]),
                        command=lambda *_: {"state": "OPEN", "message_cutoff": 7},
                        quiet_final=quiet_final, report={"commands": [{"final": "OPEN_FINAL"}]},
                        pump_until=pump, native_tail=lambda *_: {}, native_id="owned-native", run=run,
                        json=json, model=SimpleNamespace(count=lambda: 2), args=SimpleNamespace(drain_seconds=0),
                        dwell=lambda _: None, save=lambda *_: None, require_native_silence=lambda *_: {},
                        all_rows=[], queue_empty=lambda _: None, verify_originals=lambda *_: None, warm_rows=[])
                    with self.assertRaises(RuntimeError):
                        exec(block, environment)

    def test_constructor_failure_stops_only_its_popen_child_and_retains_uncertainty(self):
        for kind in (probe.Tui, probe.Metadata):
            with self.subTest(kind=kind.__name__), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp)
                process = Mock(pid=123)
                process.poll.side_effect = [None, 0]
                process.wait.return_value = 0
                resource = object.__new__(kind)
                with patch.object(probe.subprocess, "Popen", return_value=process), \
                        patch("nativepeers.process.identity", side_effect=PermissionError("birth denied")), \
                        patch.object(probe.pty, "openpty", return_value=(9, 10)), \
                        patch.object(probe.fcntl, "ioctl"), patch.object(probe.os, "close") as close:
                    with self.assertRaises(PermissionError):
                        if kind is probe.Tui:
                            resource.__init__(["owned-codex"], run, {}, run, "owned", Mock())
                        else:
                            resource.__init__(Path("owned-codex"), run, {}, run, "owned")
                    if kind is probe.Tui:
                        self.assertTrue(resource.raw.closed and resource.timeline.closed)
                        self.assertEqual({call.args[0] for call in close.call_args_list}, {9, 10})
                    else:
                        self.assertTrue(resource.log.closed and resource.errors.closed)
                process.send_signal.assert_called_once_with(probe.signal.SIGTERM)
                process.wait.assert_called_once_with(timeout=probe.STOP_WAIT)
                record = json.loads((run / "owned.constructor-failure.json").read_text())
                self.assertEqual(record["status"], "FAIL")
                self.assertEqual(record["descendant_cleanup"], "UNVERIFIED")
                self.assertFalse(record["output_eof"])

    def test_help_never_launches_native_or_network(self):
        with patch.object(probe.subprocess, "Popen", side_effect=AssertionError("native launch")), \
                patch.object(probe.Model, "__init__", side_effect=AssertionError("network")):
            with self.assertRaises(SystemExit) as raised:
                probe.parser().parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_private_environment_drops_credentials_proxy_and_outer_binding(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret",
                                     "NPEER_BOOTSTRAP": "outer", "NPEER_BINDING": "outer",
                                     "CODEX_THREAD_ID": "outer", "HTTPS_PROXY": "external"}):
            env = probe.private_env(Path("/run/home"), Path("/run/state"), Path("/run"), Path("/source"))
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NPEER_BOOTSTRAP", "NPEER_BINDING", "CODEX_THREAD_ID", "HTTPS_PROXY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["CODEX_HOME"], "/run/home")
        self.assertEqual(env["NPEER_HOME"], "/run/state")

    def test_model_body_decoding_is_strict_and_preserves_unicode(self):
        value = {"input": [{"role": "user", "content": "retained — δ"}]}
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.assertEqual(probe.decode_body(raw, None), value)
        self.assertEqual(probe.decode_body(gzip.compress(raw), "gzip"), value)
        for raw, encoding in ((b"bad", None), (b"bad", "gzip"), (b"{}", "br")):
            with self.subTest(encoding=encoding), self.assertRaises(Exception):
                probe.decode_body(raw, encoding)

    def test_evidence_path_never_overwrites_or_escapes_or_traverses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            existing = root / "existing"
            existing.mkdir()
            link = root / "link"
            link.symlink_to(existing, target_is_directory=True)
            for path in (str(existing), "relative", str(root / ".." / "escape"), str(link / "fresh")):
                with self.subTest(path=path), self.assertRaises(probe.Blocked):
                    probe.prepare_run(path, root)
            with patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(probe.Blocked):
                    probe.prepare_run(str(root / "not-ignored"), root)
            self.assertFalse((root / "not-ignored").exists())

    def test_ignored_evidence_directory_is_created_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as git:
                result = probe.prepare_run(str(root / "fresh"), root)
            self.assertEqual(result.stat().st_mode & 0o777, 0o700)
            self.assertIn("check-ignore", git.call_args.args[0])

    def test_nonselected_candidate_is_never_launched_or_silently_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            selected = root / ".local/share/codex-mcp-manager/current/bin/codex"
            selected.parent.mkdir(parents=True)
            selected.write_text("selected package")
            candidate = root / "candidate"
            candidate.write_text("unselected candidate")
            with patch.object(probe.Path, "home", return_value=root), \
                    patch.object(probe.subprocess, "Popen", side_effect=AssertionError("launch")):
                self.assertEqual(probe.select_codex(str(selected))[0], selected)
                with self.assertRaises(probe.Blocked):
                    probe.select_codex(str(candidate))

    def test_known_normal_launcher_resolution_refuses_official_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state = root / ".local/share/codex-mcp-manager"
            selected = state / "current/bin/codex"
            selected.parent.mkdir(parents=True)
            selected.write_text("selected")
            launcher = root / "codex-launcher"
            launcher.write_text('exec "$state/current/bin/codex" "$@"\n')
            normal = root / ".local/bin/codex"
            normal.parent.mkdir(parents=True)
            normal.symlink_to(launcher)
            with patch.object(probe.Path, "home", return_value=root):
                self.assertEqual(probe.select_codex(str(normal))[0], selected)
                (state / "official-mode").touch()
                with self.assertRaises(probe.Blocked):
                    probe.select_codex(str(normal))

    def test_metadata_client_cannot_load_or_run_an_alternative_consumer(self):
        rpc = object.__new__(probe.Metadata)
        for method in ("thread/start", "thread/resume", "turn/start", "thread/queue/add", "thread/queue/start"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                rpc.call(method, {})

    def model(self):
        model = object.__new__(probe.Model)
        model.lock = threading.Lock()
        model.plans = {}
        return model

    def test_fixture_uses_native_shell_without_login_and_waits_for_positive_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = Path(tmp) / "done.json"
            model = self.model()
            model.plan("CONTROL", "python helper", done, "FINAL")
            payload = {"input": [{"role": "user", "content": "CONTROL"}],
                       "tools": [{"name": "exec_command"}, {"name": "write_stdin"}]}
            first = model.answer(payload)
            self.assertEqual(first["name"], "exec_command")
            self.assertFalse(json.loads(first["arguments"])["login"])
            payload["input"].append({"type": "function_call_output", "output": "Process running with session ID 412"})
            self.assertEqual(model.answer(payload)["name"], "write_stdin")
            probe.save(done, {"ok": True})
            self.assertEqual(model.answer(payload)["content"][0]["text"], "FINAL")

    def test_failed_helper_and_missing_session_never_produce_success_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = Path(tmp) / "done.json"
            model = self.model()
            model.plan("CONTROL", "helper", done, "FINAL")
            payload = {"input": [{"role": "user", "content": "CONTROL"}], "tools": [{"name": "exec_command"}]}
            model.answer(payload)
            with self.assertRaises(RuntimeError):
                model.answer(payload)
            probe.save(done, {"ok": False, "error": "native close failed"})
            with self.assertRaises(RuntimeError):
                model.answer(payload)

    def test_late_frame_cannot_match_a_previous_control_in_history(self):
        model = self.model()
        model.plan("OLD_CONTROL", "helper", Path("/unused"), "FINAL")
        payload = {"input": [{"role": "user", "content": "OLD_CONTROL"},
                             {"role": "user", "content": "QCTUI_PROGRESS_01"}]}
        response = model.answer(payload)
        self.assertEqual(response["content"][0]["text"], "QCTUI_UNPLANNED_MODEL_INPUT")

    def test_fresh_batches_have_disjoint_requests_bodies_and_expired_tail(self):
        warm = probe.batch_specs("fixture", "warm")
        cold = probe.batch_specs("fixture", "cold")
        for batch in (warm, cold):
            self.assertEqual(len(batch), 22)
            self.assertEqual([row["kind"] for row in batch].count("progress"), 17)
            self.assertEqual([row["kind"] for row in batch].count("completion"), 5)
            self.assertEqual([n for n, row in enumerate(batch) if row["expires_seconds"] == 5], [0, 1, 15, 16])
        for field in ("request_id", "body"):
            self.assertFalse({row[field] for row in warm} & {row[field] for row in cold})
        with self.assertRaises(ValueError):
            probe.batch_specs("fixture", "old")

    def test_unsupported_helper_action_cannot_reach_native_or_router(self):
        with patch.object(probe.subprocess, "Popen", side_effect=AssertionError("native launch")):
            with self.assertRaises(ValueError):
                asyncio.run(probe.helper_action({}, "old-frame-resubmission", "sender"))

    def test_native_queue_proof_requires_complete_unique_original_ids(self):
        self.assertEqual(probe.queue_ids({"data": [{"id": "original-1"}, {"id": "original-2"}], "nextCursor": None}),
                         {"original-1", "original-2"})
        self.assertEqual(probe.queue_ids({"data": []}), set())
        for value in ({}, {"data": [], "nextCursor": "more"}, {"data": [{}]},
                      {"data": [{"id": "same"}, {"id": "same"}]}, {"data": ["not-an-item"]}):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                probe.queue_ids(value)
        rows = [{"outcome": {"attempt": {"external_submission_id": "native-original"}}}]
        self.assertEqual(probe.original_queue_ids(rows), {"native-original"})
        with self.assertRaises(RuntimeError):
            probe.original_queue_ids(rows + rows)

    def test_dynamic_hook_inventory_includes_queued_input_and_rejects_missing_or_duplicate_events(self):
        events = ["SessionStart", "SessionEnd", "UserPromptSubmit", "QueuedInput"]
        groups = {event: {} for event in events}
        hooks = [{"sourcePath": "/private/hooks.json", "command": "product-hook", "eventName": event,
                  "key": "hook-" + event} for event in events]
        listing = {"data": [{"hooks": hooks}]}
        self.assertEqual(len(probe.inventory_hooks(groups, listing, Path("/private/hooks.json"), "product-hook")), 4)
        for inventory in (hooks[:3], hooks[:3] + [hooks[0]]):
            with self.assertRaises(probe.Blocked):
                probe.inventory_hooks(groups, {"data": [{"hooks": inventory}]}, Path("/private/hooks.json"), "product-hook")

    def test_cold_cutover_refuses_replacement_ids_full_drain_and_missing_expired_or_completion_survivors(self):
        rows = [{"kind": row["kind"], "envelope": {"expires_at": row["expires_seconds"]},
                 "outcome": {"message_id": f"message-{n}", "attempt": {"external_submission_id": f"native-{n}"}}}
                for n, row in enumerate(probe.batch_specs("fixture", "cold"))]
        events = [{"subject_id": f"message-{n}"} for n in range(3)]
        frozen = {f"native-{n}" for n in range(3, 22)}
        pending = probe.cold_survivors(rows, frozen, frozen, events, 10)
        self.assertEqual(len(pending), 19)
        for before, after, observations in (
                (frozen, (frozen - {"native-3"}) | {"replacement"}, events),
                (set(), set(), events),
                (frozen, frozen, [{"subject_id": f"message-{n}"} for n in range(22)]),
                ({"native-17"}, {"native-17"}, events),
                ({"native-15"}, {"native-15"}, events)):
            with self.subTest(before=before, after=after), self.assertRaises(RuntimeError):
                probe.cold_survivors(rows, before, after, observations, 10)

    def family(self, records):
        family = object.__new__(probe.Family)
        family.process = Mock()
        family.process.poll.return_value = 0
        family.birth = records[0]
        family.members = {(row["pid"], row["start"]): row for row in records}
        family.last_capture, family.cleanup = 0, None
        family.capture_errors, family.signals = [], []
        return family

    def test_cleanup_waits_for_the_owned_child_to_be_reaped_after_identity_disappears(self):
        from nativepeers import process
        family = self.family([{"pid": 100, "start": "owner", "pgid": 100}])
        polls = [None, None, None, None, 0]
        family.process.poll.side_effect = lambda: polls.pop(0) if polls else 0
        with patch.object(process, "descendants", return_value=[]), \
                patch.object(process, "owned_status", return_value="DEAD"), \
                patch.object(process, "signal_captured") as sent, \
                patch.object(probe, "process_rows", return_value=[]), patch.object(probe.time, "sleep") as sleep:
            result = family.stop(abrupt=True)
        self.assertTrue(result["confirmed_dead"])
        self.assertIsNotNone(result["exit_code"])
        self.assertTrue(sleep.called)
        sent.assert_not_called()

    def test_cleanup_tracks_descendants_through_a_retained_child_after_owner_death(self):
        from nativepeers import process
        owner = {"pid": 100, "start": "owner", "pgid": 100}
        child = {"pid": 101, "start": "child", "pgid": 100}
        grandchild = {"pid": 102, "start": "grandchild", "pgid": 102}
        family = self.family([owner, child])
        states = {100: "DEAD", 101: "LIVE", 102: "LIVE"}
        def signal_birth(record, _):
            states[record["pid"]] = "DEAD"
            return True
        with patch.object(process, "descendants", return_value=[grandchild]) as descendants, \
                patch.object(process, "owned_status", side_effect=lambda row: states[row["pid"]]), \
                patch.object(process, "signal_captured", side_effect=signal_birth) as sent, \
                patch.object(probe, "process_rows", return_value=[]), patch.object(probe, "STOP_WAIT", 0):
            result = family.stop(abrupt=True)
        self.assertTrue(result["confirmed_dead"])
        self.assertIn(child, descendants.call_args.args[0])
        self.assertEqual({call.args[0]["pid"] for call in sent.call_args_list}, {101, 102})
        self.assertEqual({row["owner"]["pid"] for row in result["members"]}, {100, 101, 102})

    def test_unknown_liveness_or_unverified_former_group_process_refuses_cleanup_without_signaling_it(self):
        from nativepeers import process
        owner = {"pid": 100, "start": "owner", "pgid": 100}
        child = {"pid": 101, "start": "child", "pgid": 100}
        family = self.family([owner, child])
        with patch.object(process, "descendants", return_value=[]), \
                patch.object(process, "owned_status", side_effect=lambda row: "DEAD" if row["pid"] == 100 else "UNKNOWN"), \
                patch.object(process, "signal_captured") as sent, patch.object(probe, "STOP_WAIT", 0), \
                patch.object(probe, "process_rows", return_value=[{"pid": 999, "ppid": 1, "pgid": 100}]):
            result = family.stop()
        self.assertFalse(result["confirmed_dead"])
        self.assertEqual(result["unknown_liveness"][0]["owner"], child)
        self.assertEqual(result["unverified_in_former_group"][0]["pid"], 999)
        sent.assert_not_called()

    def test_cleanup_discovers_a_grandchild_from_a_live_child_after_the_leader_dies(self):
        from nativepeers import process
        owner = {"pid": 100, "start": "owner", "pgid": 100}
        child = {"pid": 101, "start": "child", "pgid": 101}
        grandchild = {"pid": 102, "start": "grandchild", "pgid": 102}
        family = self.family([owner, child])
        states = {100: "LIVE", 101: "LIVE", 102: "LIVE"}
        observations = []
        def discover(records):
            observations.append(states[100])
            return [grandchild] if states[100] == "DEAD" else []
        def signal_birth(record, sent):
            if record["pid"] == 100 or sent == probe.signal.SIGKILL:
                states[record["pid"]] = "DEAD"
            return True
        with patch.object(process, "descendants", side_effect=discover), \
                patch.object(process, "owned_status", side_effect=lambda row: states[row["pid"]]), \
                patch.object(process, "signal_captured", side_effect=signal_birth) as sent, \
                patch.object(probe, "process_rows", return_value=[]), patch.object(probe, "STOP_WAIT", .01), \
                patch.object(probe.time, "sleep"):
            result = family.stop()
        self.assertTrue(result["confirmed_dead"])
        self.assertEqual(observations[0], "LIVE")
        self.assertIn("DEAD", observations)
        self.assertIn((102, probe.signal.SIGKILL), [(call.args[0]["pid"], call.args[1]) for call in sent.call_args_list])

    def test_failed_final_process_inspection_never_counts_as_dead_proof(self):
        from nativepeers import process
        family = self.family([{"pid": 100, "start": "owner", "pgid": 100}])
        with patch.object(process, "descendants", return_value=[]), \
                patch.object(process, "owned_status", return_value="DEAD"), patch.object(probe, "STOP_WAIT", 0), \
                patch.object(probe, "process_rows", side_effect=PermissionError("inspection denied")):
            result = family.stop()
        self.assertFalse(result["confirmed_dead"])
        self.assertIn("inspection denied", result["unknown_liveness"][0]["error"])

    def test_dead_native_family_does_not_imply_output_eof(self):
        tui = object.__new__(probe.Tui)
        tui.eof, tui.master = False, 9
        tui.raw, tui.timeline = Mock(), Mock()
        with patch.object(probe.Family, "stop", return_value={"confirmed_dead": True, "output_eof": None}), \
                patch.object(probe.time, "monotonic", side_effect=[0, 4]), patch.object(probe.os, "close"):
            result = tui.stop()
        self.assertTrue(result["confirmed_dead"])
        self.assertFalse(result["output_eof"])
        self.assertFalse(result["pty_eof"])

    def test_native_completion_boundary_cannot_hide_queued_persistence_or_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "sessions").mkdir()
            history = home / "sessions/owned.jsonl"
            header = {"type": "session_meta", "payload": {"id": "owned-native"}}
            done = {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "FINAL"}}
            def write(*records):
                history.write_text("".join(json.dumps(record) + "\n" for record in records))
            write(header)
            self.assertIsNone(probe.native_tail(home, "owned-native", "FINAL"))
            write(header, done)
            self.assertEqual(probe.require_native_silence(home, "owned-native", "FINAL", ["LATE_BODY"])["after"], [])
            write(header, done, {"type": "event_msg", "payload": {"type": "task_started"}})
            with self.assertRaises(RuntimeError):
                probe.require_native_silence(home, "owned-native", "FINAL", [])
            write(header, done, {"type": "event_msg", "payload": {"type": "task_started"}}, done)
            with self.assertRaises(RuntimeError):
                probe.require_native_silence(home, "owned-native", "FINAL", [])
            write(header, done, done)
            with self.assertRaises(RuntimeError):
                probe.require_native_silence(home, "owned-native", "FINAL", [])
            write(header, {"type": "response_item", "payload": {"text": "LATE_BODY"}}, done)
            with self.assertRaises(RuntimeError):
                probe.require_native_silence(home, "owned-native", "FINAL", ["LATE_BODY"])


if __name__ == "__main__":
    unittest.main()
