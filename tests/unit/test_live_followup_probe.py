"""Probe scheduling boundaries without launching a native process."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import io
import json
import sys
import copy
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'probes/live_followup.py'
spec=importlib.util.spec_from_file_location('live_followup_probe',PATH)
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class FollowupProbeTests(unittest.TestCase):
    def test_qualification_requires_exact_phases_models_native_history_and_receipts(self):
        result=dict(results=[dict(completion_phase=n,task_id=str(n),status='completed',model_requested=probe.q.MODEL,model_observed=probe.q.MODEL,summary=f'NPF_RESULT_{n}_fixture',worker=dict(native_session_id='one-history')) for n in range(1,5)],
                    notifications=[dict(completion_generation=n,state=state) for n,state in enumerate(('UNCLAIMED','NOTIFICATION_UNKNOWN','PUSH_ACCEPTED','RETAINED'),1)])
        stages={3:['ACCEPTED_QUEUED','INGESTED'],4:['RETAINED']}
        self.assertEqual(probe.validate_result(result,'fixture',stages),'one-history')
        for invalid in ({3:['INGESTED'],4:['RETAINED']},{3:['ACCEPTED_QUEUED'],4:['RETAINED']},{3:stages[3],4:['RETAINED','ACCEPTED_QUEUED']},{3:stages[3],4:['RETAINED','INGESTED']},{3:stages[3],4:[]}):
            with self.assertRaises(RuntimeError):probe.validate_result(result,'fixture',invalid)
        for field,value in [('status','failed'),('model_observed','other'),('summary','wrong'),('completion_phase',3),('task_id','1')]:
            invalid=copy.deepcopy(result);invalid['results'][3][field]=value
            with self.assertRaises(RuntimeError):probe.validate_result(invalid,'fixture',stages)
        invalid=copy.deepcopy(result);invalid['results'][3]['worker']['native_session_id']='other-history'
        with self.assertRaises(RuntimeError):probe.validate_result(invalid,'fixture',stages)
        invalid=copy.deepcopy(result);invalid['notifications'][3]['state']='PUSH_ACCEPTED'
        with self.assertRaises(RuntimeError):probe.validate_result(invalid,'fixture',stages)
        result['results'].reverse();result['notifications'].reverse()
        self.assertEqual(probe.validate_result(result,'fixture',stages),'one-history')

    def test_candidate_source_refusal_records_blocked_without_native_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run=root/'blocked';run.mkdir()
            with patch.object(sys,'argv',['probe','--nativepeers-root',str(root),'--run',str(run)]), \
                 patch.object(probe.base,'prepare_run',return_value=run), \
                 patch.object(probe.base,'select_codex') as select,patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(probe.main(),2)
            select.assert_not_called()
            self.assertEqual(json.loads((run/'qualification.json').read_text())['status'],'BLOCKED')

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
