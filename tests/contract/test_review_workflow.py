"""Disposable report/TODO accounting; these checks do not simulate native acceptance."""
from collections import Counter
import json
from pathlib import Path
import unittest

from nativepeers.worker_report import extract

ROOT=Path(__file__).resolve().parents[2]


class ReviewWorkflowExerciseTests(unittest.TestCase):
    def setUp(self):
        self.exercise=json.loads((ROOT/'examples/review-parent-exercise.json').read_text())

    def test_every_actionable_id_has_one_attributed_todo_and_disposition(self):
        expected=[];suggestions=[]
        for report in self.exercise['reports']:
            findings,checks,limits=extract(report['text'])
            self.assertEqual(limits,[]);self.assertEqual(checks[0]['outcome'],'not_run')
            for item in findings:
                source=report['task']+':'+item['summary'].split(':')[0]
                expected.append(source)
                if item['severity']=='info':suggestions.append(source)
        actual=[source for todo in self.exercise['todos'] for source in todo['sources']]
        self.assertEqual(Counter(actual),Counter(expected))
        self.assertTrue(all(count==1 for count in Counter(actual).values()))
        self.assertTrue(set(suggestions)<=set(actual))
        self.assertEqual(len(self.exercise['todos']),len(expected)-1)  # one duplicate, both attributions survive
        for todo in self.exercise['todos']:
            self.assertIn(todo['disposition'],('fixed and verified','already satisfied','rejected as incorrect/inapplicable','pending approval/blocked'))
            self.assertTrue(todo['evidence'])
        self.assertFalse(self.exercise['complete'])
        self.assertTrue(any(todo['disposition']=='pending approval/blocked' for todo in self.exercise['todos']))

    def test_suggestion_fix_and_evidence_backed_already_satisfied_and_rejection(self):
        before={};after={};current={}
        exec(self.exercise['toy_before'],before);exec(self.exercise['toy_after'],after);exec(self.exercise['toy_current'],current)
        self.assertNotEqual(before['label'](self.exercise['originating_task']),self.exercise['originating_task'])
        self.assertEqual(after['label'](self.exercise['originating_task']),self.exercise['originating_task'])
        self.assertEqual(current['total'](1,2),3)
        for value in (None,[], ''):
            with self.assertRaises(ValueError):current['validate'](value)

    def test_origin_detour_and_privileged_cases_grant_no_actions(self):
        cases={case['name']:case for case in self.exercise['scope_cases']}
        origin=cases['origin plus detour']
        self.assertEqual(origin['final_task'],origin['originating_task'])
        self.assertNotIn(origin['originating_task'],origin['review_coverage'])
        self.assertEqual(origin['origin_review'],'NOT_RUN');self.assertFalse(origin['complete'])
        proposal=cases['privileged authorized proposal']
        for field in ('proposal','validation','recovery'):self.assertTrue(proposal[field])
        self.assertLess(proposal['sequence'].index('read-only independent review'),proposal['sequence'].index('parent /address'))
        self.assertEqual(proposal['execution'],'NOT_RUN')
        self.assertFalse(cases['missing provider']['complete'])
        self.assertIn('missing independent review',cases['missing provider']['execution'])
        self.assertFalse(cases['review only']['authorized'])
        self.assertEqual(cases['trivial diagnostic']['worker_launches'],0)
        for case in cases.values():self.assertEqual(case['actions'],[])
