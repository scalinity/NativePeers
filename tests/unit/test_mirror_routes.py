"""The mirror lease and the delivery route of a mirror-owned generation (CLAUDE_LINEAGE_MOD §6.4, §8.3), including races."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers import mirror
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.notifications import claim_wait

SESSION, OTHER = 'session-a', 'session-b'


class RouteCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.j = Journal(Path(directory.name));self.addCleanup(self.j.close)

    def worker(self, *, owner='mirror', mode='wait', lease=True, tasks=1):
        group, worker = new_id(), new_id();task_ids = [new_id() for _ in range(tasks)]
        policy = dict(initial_tasks=task_ids[:1], phases={str(i + 1): [t] for i, t in enumerate(task_ids)}, completion_mode=mode)
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, 'parent', canonical(policy).decode(), 'RUNNING', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (worker, group, None, 'codex', None, 'w', 'm', None, None, '/', None, 'RUNNING', 1, '{}'))
        with self.j.transaction() as db:
            for phase, task in enumerate(task_ids, 1):
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, 'COMPLETED', None, None, 1, '{}'))
                db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)', (group, phase, 'parent', mode, mirror.route_state(owner), None, None, new_id(), 1))
                mirror.record(db, task, group, phase, owner, session=SESSION, agent='agent1')
            if lease:mirror.grant(db, worker, SESSION, 'agent1', 'parent', display_only=False)
        return group, worker, task_ids

    def state(self, group, phase=1):
        return self.j.one('SELECT state FROM completion_routes WHERE group_id=? AND completion_generation=?', (group, phase))['state']

    def lapse(self, worker):
        self.j.db.execute('UPDATE mirror_leases SET expires_at=? WHERE worker_id=?', (time.time() - 1, worker))


class LeaseTests(RouteCase):
    def test_a_second_session_is_refused_while_the_lease_lives_and_may_take_it_after_expiry(self):
        _, worker, _ = self.worker()
        with self.assertRaises(PeerError) as refused:
            mirror.attach(self.j, worker, OTHER, 'agent2', 'parent', sessions=[OTHER])
        self.assertEqual(refused.exception.code, 'MIRROR_OWNED')
        with self.assertRaises(PeerError):
            mirror.attach(self.j, worker, OTHER, None, 'parent', sessions=[OTHER], check_only=True)
        self.assertEqual(mirror.attach(self.j, worker, SESSION, 'agent3', 'parent', sessions=[SESSION])['claude_agent_id'], 'agent3')
        self.lapse(worker)
        taken = mirror.attach(self.j, worker, OTHER, 'agent2', 'parent', sessions=[OTHER])
        self.assertEqual((taken['mirror_owner'], mirror.live_lease(self.j, worker)['claude_session']), ('this', OTHER))

    def test_renewal_extends_only_live_leases_so_a_restarted_bridge_readopts_its_own(self):
        _, worker, _ = self.worker()
        self.j.db.execute('UPDATE mirror_leases SET expires_at=? WHERE worker_id=?', (time.time() + 2, worker))
        self.assertEqual(mirror.renew(self.j, [SESSION]), 1)
        self.assertGreater(mirror.live_lease(self.j, worker)['expires_at'], time.time() + 25)
        self.lapse(worker)
        self.assertEqual(mirror.renew(self.j, [SESSION]), 0)
        self.assertIsNone(mirror.live_lease(self.j, worker))

    def test_ownership_requires_the_creating_sessions_live_lease(self):
        _, worker, _ = self.worker()
        self.assertEqual(mirror.owner_for(self.j, worker, SESSION, 'mirror'), 'mirror')
        self.assertEqual(mirror.owner_for(self.j, worker, OTHER, 'mirror'), 'native')
        self.assertEqual(mirror.owner_for(self.j, worker, SESSION, 'native'), 'native')
        self.lapse(worker)
        self.assertEqual(mirror.owner_for(self.j, worker, SESSION, 'mirror'), 'native')


class RouteTests(RouteCase):
    def test_surfacing_then_ack_delivers_and_nothing_pushes_or_claims_meanwhile(self):
        group, worker, (task,) = self.worker()
        self.assertFalse(claim_wait(self.j, group, 1, 1, 'waiter', 5))
        self.assertEqual(mirror.surfacing(self.j, worker, [SESSION], generation=1)['route'], mirror.SURFACING)
        self.assertFalse(claim_wait(self.j, group, 1, 1, 'waiter', 5))
        self.assertEqual(mirror.ack(self.j, worker, [SESSION], task_id=task, generation=1)['route'], mirror.DELIVERED)
        self.assertEqual(mirror.expire(self.j), [])
        self.assertEqual(self.state(group), mirror.DELIVERED)

    def test_expiry_before_surfacing_retains_a_wait_route_and_surfacing_then_changes_nothing(self):
        group, worker, _ = self.worker()
        self.lapse(worker)
        # The lease lapsed before the sweep: surfacing cannot win against it.
        late = mirror.surfacing(self.j, worker, [SESSION], generation=1)
        self.assertEqual((late['changed'], late['route']), (False, mirror.PENDING))
        moved = mirror.expire(self.j)
        self.assertEqual([(m['from'], m['to']) for m in moved], [(mirror.PENDING, 'UNCLAIMED')])
        self.assertEqual(self.state(group), 'UNCLAIMED')
        self.assertEqual(mirror.ack(self.j, worker, [SESSION], generation=1)['route'], 'UNCLAIMED')
        self.assertTrue(claim_wait(self.j, group, 1, 1, 'waiter', 5))

    def test_expiry_before_surfacing_rearms_push_only_with_a_live_supervisor(self):
        for supervised, expected in ((True, 'UNCLAIMED'), (False, 'RETAINED')):
            with self.subTest(supervised=supervised):
                group, worker, _ = self.worker(mode='push')
                self.lapse(worker)
                mirror.expire(self.j, group_id=group, supervised=supervised)
                self.assertEqual(self.state(group), expected)

    def test_expiry_after_surfacing_is_unknown_never_pushed_and_a_late_ack_settles_it(self):
        group, worker, _ = self.worker(mode='push')
        mirror.surfacing(self.j, worker, [SESSION], generation=1)
        self.lapse(worker)
        self.assertEqual(mirror.expire(self.j, supervised=True)[0]['to'], 'NOTIFICATION_UNKNOWN')
        self.assertEqual(self.state(group), 'NOTIFICATION_UNKNOWN')
        self.assertEqual(mirror.ack(self.j, worker, [SESSION], generation=1)['route'], mirror.DELIVERED)

    def test_a_killed_shadow_acks_without_surfacing_and_a_plain_ack_needs_it(self):
        group, worker, _ = self.worker()
        self.assertFalse(mirror.ack(self.j, worker, [SESSION], generation=1)['changed'])
        self.assertEqual(mirror.ack(self.j, worker, [SESSION], generation=1, outcome='killed')['route'], mirror.DELIVERED)

    def test_another_session_cannot_move_a_route_and_a_native_route_is_left_alone(self):
        _, worker, _ = self.worker()
        with self.assertRaises(PeerError) as refused:
            mirror.surfacing(self.j, worker, [OTHER], generation=1)
        self.assertEqual(refused.exception.code, 'APPROVAL_BLOCKED')
        group, native, _ = self.worker(owner='native', lease=False)
        self.assertEqual(mirror.surfacing(self.j, native, [SESSION], generation=1)['changed'], False)
        self.assertEqual(self.state(group), 'UNCLAIMED')

    def test_generation_and_task_must_agree(self):
        _, worker, tasks = self.worker(tasks=2)
        self.assertEqual(mirror.route(self.j, worker, generation=2)['task_id'], tasks[1])
        self.assertEqual(mirror.task_generation(self.j, tasks[1]), 2)
        with self.assertRaises(PeerError):
            mirror.route(self.j, worker, task_id=tasks[0], generation=2)

    def test_released_lease_falls_back_at_once(self):
        group, worker, _ = self.worker()
        released = mirror.release(self.j, worker, [SESSION])
        self.assertTrue(released['released']);self.assertEqual(released['fallback'][0]['to'], 'UNCLAIMED')
        self.assertEqual(self.state(group), 'UNCLAIMED')


class HandbackTests(RouteCase):
    def test_a_display_only_reattach_inside_the_ttl_hands_pending_routes_back(self):
        # The bridge died and restarted within the lease TTL; the old shadow cannot surface any more.
        group, worker, _ = self.worker()
        attached = mirror.attach(self.j, worker, SESSION, 'agent2', 'parent', sessions=[SESSION], display_only=True)
        self.assertTrue(attached['attached'])
        self.assertEqual(self.state(group), 'UNCLAIMED')
        self.assertEqual(mirror.surfacing(self.j, worker, [SESSION], generation=1)['changed'], False)

    def test_a_reattach_after_surfacing_leaves_the_route_unknown_and_a_check_changes_nothing(self):
        group, worker, _ = self.worker()
        mirror.surfacing(self.j, worker, [SESSION], generation=1)
        mirror.attach(self.j, worker, SESSION, 'agent2', 'parent', sessions=[SESSION], display_only=True, check_only=True)
        self.assertEqual(self.state(group), mirror.SURFACING)
        mirror.attach(self.j, worker, SESSION, 'agent2', 'parent', sessions=[SESSION], display_only=True)
        self.assertEqual(self.state(group), 'NOTIFICATION_UNKNOWN')


class RaceTests(RouteCase):
    def race(self, surfacer, sweeper):
        """Surfacing and the expiry sweep from two connections, released together."""
        group, worker, _ = self.worker()
        self.j.db.execute('UPDATE mirror_leases SET expires_at=? WHERE worker_id=?', (time.time() + 0.02, worker))
        start = threading.Barrier(2);results = {}
        def run(name, action):
            journal = Journal(self.j.root)
            try:
                start.wait()
                deadline = time.monotonic() + 0.3
                while time.monotonic() < deadline:
                    results.setdefault(name, []).append(action(journal, worker))
            finally:
                journal.close()
        threads = [threading.Thread(target=run, args=('surface', surfacer)), threading.Thread(target=run, args=('sweep', sweeper))]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        return group, worker, results

    def test_expiry_and_surfacing_settle_on_one_outcome(self):
        for _ in range(5):
            group, worker, results = self.race(lambda j, w: mirror.surfacing(j, w, [SESSION], generation=1)['changed'], lambda j, w: mirror.expire(j))
            surfaced = sum(results['surface']);fallbacks = [m for batch in results['sweep'] for m in batch]
            self.assertLessEqual(surfaced, 1)
            final = self.state(group)
            if surfaced:
                self.assertEqual([(m['from'], m['to']) for m in fallbacks], [(mirror.SURFACING, 'NOTIFICATION_UNKNOWN')])
            else:
                self.assertEqual([(m['from'], m['to']) for m in fallbacks], [(mirror.PENDING, 'UNCLAIMED')])
            self.assertIn(final, ('NOTIFICATION_UNKNOWN', 'UNCLAIMED'))

    def test_ack_racing_expiry_after_surfacing_never_pushes(self):
        group, worker, _ = self.worker(mode='push')
        mirror.surfacing(self.j, worker, [SESSION], generation=1)
        self.lapse(worker)
        start = threading.Barrier(2)
        def act(action):
            journal = Journal(self.j.root)
            try:start.wait();action(journal)
            finally:journal.close()
        threads = [threading.Thread(target=act, args=(lambda j: mirror.ack(j, worker, [SESSION], generation=1),)),
                   threading.Thread(target=act, args=(lambda j: mirror.expire(j, supervised=True),))]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(self.state(group), mirror.DELIVERED)


class SchemaTests(unittest.TestCase):
    def test_the_mirror_field_is_optional_exact_and_old_specs_stay_valid(self):
        import jsonschema
        from nativepeers.protocol import valid
        schema = json.loads((Path(__file__).resolve().parents[2] / 'schemas/spawn.v1.json').read_text())
        def spec(**mirror_fields):
            worker = dict(provider='codex', title='t', task='x', **({'mirror': mirror_fields} if mirror_fields else {}))
            return {'workers': [worker], 'completion_mode': 'wait'}
        good = dict(owner='claude-mod', claude_session=new_id(), claude_agent_id='a1b2')
        cases = [(spec(), True), (spec(**good), True), (spec(**dict(good, owner='other')), False), (spec(**dict(good, extra=1)), False),
                 (spec(owner='claude-mod', claude_session='s'), False), (spec(**dict(good, claude_agent_id='')), False)]
        for value, ok in cases:
            with self.subTest(value=value):
                self.assertEqual(jsonschema.Draft202012Validator(schema).is_valid(value), ok)
                self.assertEqual(valid(value, schema), ok)
        self.assertFalse(jsonschema.Draft202012Validator(schema).is_valid(spec(**dict(good, claude_agent_id='has spaces'))))


class SupervisorDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """The supervisor never pushes a leased mirror-owned generation, and pushes once after its lease lapses before surfacing."""

    async def test_no_push_frame_while_leased_then_the_push_fallback(self):
        from nativepeers.routing import Router
        from nativepeers.supervisor import Group
        directory = tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        j = Journal(Path(directory.name));self.addCleanup(j.close)
        group, worker, task = new_id(), new_id(), new_id()
        policy = dict(initial_tasks=[task], phases={'1': [task]}, completion_mode='push', parent_incarnation=new_id(), parent_owner={}, created_at=time.time(),
                      wall_time_seconds=60, group_wall_seconds=60, followup_grace_seconds=0, survive_parent=True)
        j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, 'parent', canonical(policy).decode(), 'RUNNING', '{}'))
        j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (worker, group, None, 'codex', 'native', 'w', 'm', None, None, '/', None, 'IDLE_GRACE', 1, '{}'))
        artifact = __import__('nativepeers.artifacts', fromlist=['Artifacts']).Artifacts(j).put(canonical(dict(task_id=task, status='completed', summary='done')), worker, ['parent'], media_type='application/json')
        j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, 'COMPLETED', artifact, None, 1, '{}'))
        with j.transaction() as db:
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)', (group, 1, 'parent', 'push', mirror.PENDING, None, None, new_id(), 1))
            mirror.record(db, task, group, 1, 'mirror', session=SESSION, agent='agent1');mirror.grant(db, worker, SESSION, 'agent1', 'parent', display_only=False)
        supervisor = Group(j, group)
        sent = AsyncMock(return_value={'state': 'INGESTED', 'message_id': new_id()})
        with patch.object(Router, 'send', sent), patch('nativepeers.delegation.Delegation.result', return_value={'tasks':supervisor.tasks(),'results': [dict(task_id=task, status='completed', summary='done', result_artifact_id=artifact,
                                                                                                                 worker=dict(provider='codex', native_session_id='native', title='w'))]}):
            await supervisor.notify(1)
            sent.assert_not_awaited()
            mirror.expire(j, group_id=group, supervised=True)
            self.assertEqual(j.one('SELECT state FROM completion_routes')['state'], mirror.PENDING)
            j.db.execute('UPDATE mirror_leases SET expires_at=?', (time.time() - 1,))
            mirror.expire(j, group_id=group, supervised=True)
            await supervisor.notify(1);await supervisor.notify(1)
        self.assertEqual(sent.await_count, 1)
        self.assertEqual(sent.await_args.kwargs['kind'], 'completion')
        self.assertEqual(j.one('SELECT state FROM completion_routes')['state'], 'PUSH_ACCEPTED')


if __name__ == '__main__':
    unittest.main()
