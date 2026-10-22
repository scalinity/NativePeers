import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'budget.json';self.budget=LiveBudget(self.path)
        atomic_json(self.path,{'active_intervals':[],'active_seconds':0,'ceiling':{'active_seconds':60,'submitted_turns':8},'reservations':[],
                               'outstanding_native_requests':[],'consumed_turns':0,'reserved_turns':0})
        self.clock=10
        def now():
            self.clock+=.001
            return self.clock
        clock=patch('nativepeers.budget.time.time',side_effect=now);clock.start();self.addCleanup(clock.stop)

    def read(self):return json.loads(self.path.read_text())

    def test_one_request_batch_settles(self):
        request=self.budget.reserve('fixture');self.budget.admit(request);self.clock=14
        self.budget.settle(request,observed_turns=1,native_id='fixture',completed_at=13)
        data=self.read();self.assertEqual(data['reservations'][0]['state'],'settled');self.assertEqual(data['outstanding_native_requests'],[])
        self.assertAlmostEqual(data['active_seconds'],13-data['active_intervals'][0]['start'])
        self.assertEqual(data['consumed_turns'],1);self.assertEqual(data['reserved_turns'],0)

    def test_removed_caps_preserve_usage_and_still_settle(self):
        data=self.read();data['ceiling'].update(active_seconds=None,submitted_turns=None)
        data['consumed_turns']=148;data['delegation_reserve']=16
        data['active_intervals']=[{'start':1,'end':4001}];data['active_seconds']=4000
        atomic_json(self.path,data);self.clock=5000
        request=self.budget.reserve('authorized uncapped test',tranche='flash_fallback')
        self.budget.admit(request);self.budget.settle(request,observed_turns=1,completed_at=5001)
        data=self.read();self.assertEqual(data['consumed_turns'],149)
        self.assertEqual(data['reserved_turns'],0);self.assertEqual(data['outstanding_native_requests'],[])
        self.assertGreater(data['active_seconds'],4000)

    def test_configured_caps_remain_enforced(self):
        request=self.budget.reserve('all finite slots',count=8)
        with self.assertRaisesRegex(RuntimeError,'submitted turns'):self.budget.reserve('ninth')
        self.assertEqual(self.read()['reserved_turns'],8)

    def test_overlapping_reverse_settlement_uses_the_latest_native_completion(self):
        a=self.budget.reserve('first');self.budget.admit(a);self.clock=11
        b=self.budget.reserve('second');self.budget.admit(b)
        self.clock=18;self.budget.settle(b,completed_at=17)
        self.clock=19;self.budget.settle(a,completed_at=16)
        data=self.read();self.assertEqual(data['active_intervals'][0]['end'],17)
        self.assertAlmostEqual(data['active_seconds'],17-data['active_intervals'][0]['start'])
        self.clock=20;c=self.budget.reserve('third');self.budget.admit(c);self.clock=23;self.budget.settle(c,completed_at=22)
        data=self.read();self.assertEqual(len(data['active_intervals']),2)
        self.assertAlmostEqual(data['active_seconds'],sum(i['end']-i['start'] for i in data['active_intervals']))

    def test_legacy_first_admission_before_interval_start_can_settle(self):
        request=self.budget.reserve('legacy');self.budget.admit(request)
        data=self.read();data['active_intervals'][0].pop('reservation_ids',None)
        data['active_intervals'][0]['start']=data['reservations'][0]['admitted_at']+.00001;atomic_json(self.path,data)
        self.clock=14;self.budget.settle(request,completed_at=13)
        self.assertEqual(self.read()['active_intervals'][0]['end'],13)


    def test_definitely_unsubmitted_reservation_returns_capacity_and_closes_interval(self):
        request=self.budget.reserve('deferred');self.budget.admit(request);self.clock=14
        self.assertTrue(self.budget.release_unsubmitted(request))
        self.assertFalse(self.budget.release_unsubmitted(request))
        data=self.read();self.assertEqual(data['consumed_turns'],0);self.assertEqual(data['outstanding_native_requests'],[])
        self.assertEqual(data['reservations'][0]['state'],'not_admitted')
        self.assertIsNotNone(data['active_intervals'][0]['end'])
        self.assertEqual(data['reserved_turns'],0)
