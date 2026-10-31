"""Probe scheduling boundaries without launching a native process."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/'probes/live_followup.py'
spec=importlib.util.spec_from_file_location('live_followup_probe',PATH)
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class FollowupProbeTests(unittest.TestCase):
    def test_worker_phases_use_one_exact_wire_model_and_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            model=object.__new__(probe.Model);model.run=Path(directory);model.token='fixture';model.parent_requests=0
            for phase in range(1,5):
                (model.run/f'release-{phase}').touch()
                payload=dict(model=probe.q.MODEL,reasoning=dict(effort=probe.q.EFFORT),input=[dict(role='user',content=[dict(type='input_text',text=f'NativePeers assigned worker title: fixture. NPF_TASK_{phase}_fixture')])])
                answer=model.answer(payload)
                self.assertEqual(answer['content'][0]['text'],f'NPF_RESULT_{phase}_fixture')
                self.assertTrue((model.run/f'entered-{phase}').exists())
                payload['model']='unexpected'
                with self.assertRaises(RuntimeError):model.answer(payload)
            self.assertEqual(model.parent_requests,0)

    def test_parent_ack_is_only_for_the_push_phase(self):
        model=object.__new__(probe.Model);model.token='fixture';model.parent_requests=0
        answer=model.answer(dict(input=[dict(role='user',content=[dict(type='input_text',text='NativePeers · Worker results NPF_RESULT_3_fixture')])]))
        self.assertEqual(answer['content'][0]['text'],'NPF_PUSH_RECEIVED_fixture')
        self.assertEqual(model.parent_requests,1)
