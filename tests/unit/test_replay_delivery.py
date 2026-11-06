"""Keep the replay's ownership answers faithful to the real route contract."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('np_replay_delivery', Path(__file__).parents[1]/'integration/claude_mod/replay_backend.py')
replay = importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


class ReplayDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_fallback_preserves_the_current_mirror_route(self):
        script = {'events': [{'kind': 'task.terminal', 'data': {'status': 'failed'}}],
                  'fallback_to': {'provider': 'antigravity', 'events': [{'kind': 'task.terminal', 'data': {'status': 'completed'}}]}}
        backend = replay.ReplayBackend({'delay_ms': 0})
        worker = replay.Worker('W1', 'agent', 'codex', {}, script);backend.workers[worker.id] = worker
        await backend.play(worker, script['events'], origin='parent-model')
        replacement = backend.workers['W2']
        self.assertIs(replacement.routes[1], worker.routes[1])
        reply = await backend.mirror('surfacing', {'worker_id': replacement.id, 'generation': 1})
        self.assertEqual((reply['owner'], reply['route'], reply['changed']), ('mirror', 'MIRROR_SURFACING', True))
        self.assertFalse((await backend.mirror('surfacing', {'worker_id': replacement.id, 'generation': 1}))['changed'])
