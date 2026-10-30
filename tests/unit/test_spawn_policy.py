"""Local omitted defaults remain separate from explicitly configured ceilings."""
import json
from pathlib import Path
import tempfile
import unittest

from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.policy import load,spawn_limits


class SpawnPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.journal=Journal(Path(temporary.name)/'state');self.addCleanup(self.journal.close)

    def test_existing_policy_keys_still_set_ceilings_and_narrow_omitted_defaults(self):
        atomic_json(self.journal.root/'policy.json',{'task_wall_seconds':42,'max_task_turns':3,'group_wall_seconds':60})
        local=load(self.journal);limits=spawn_limits(local)
        self.assertEqual((local['task_wall_seconds'],local['max_task_turns'],local['group_wall_seconds']),(42,3,60))
        self.assertEqual((limits['wall_time_seconds']['effective'],limits['max_task_turns_per_worker']['effective']),(42,3))
        self.assertEqual(limits['max_native_turns_per_task']['effective'],128)
        self.assertEqual(limits['wall_time_seconds']['ceiling']['source'],'policy.json')

    def test_defaults_require_positive_integers_and_only_ceilings_allow_null(self):
        for key in ('task_wall_seconds_default','max_task_turns_default','native_max_turns_default',
                    'task_wall_seconds','max_task_turns','group_wall_seconds','native_max_turns'):
            for value in (0,-1,True,1.5,'8')+((None,) if key.endswith('_default') else ()):
                with self.subTest(key=key,value=value):
                    atomic_json(self.journal.root/'policy.json',{key:value})
                    with self.assertRaises(PeerError):load(self.journal)
        atomic_json(self.journal.root/'policy.json',{'task_wall_seconds':None,'max_task_turns':None,'group_wall_seconds':None,'native_max_turns':None})
        self.assertEqual(spawn_limits(load(self.journal))['wall_time_seconds']['effective'],1800)

    def test_example_policy_lists_the_shipped_defaults_and_loads(self):
        from nativepeers.policy import DEFAULTS
        example=json.loads((Path(__file__).resolve().parents[2]/'examples/policy.json').read_text())
        self.assertEqual(example,DEFAULTS)
        atomic_json(self.journal.root/'policy.json',example)
        self.assertEqual(load(self.journal)['native_max_turns_default'],128)
