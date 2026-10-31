"use strict";
var __assign = (this && this.__assign) || function () {
    __assign = Object.assign || function(t) {
        for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p))
                t[p] = s[p];
        }
        return t;
    };
    return __assign.apply(this, arguments);
};
Object.defineProperty(exports, "__esModule", { value: true });
var testing_1 = require("claude-code/testing");
var mirror_1 = require("../hooks/mirror");
var n = 0;
var ctx = function (continues) {
    if (continues === void 0) { continues = false; }
    return ({ workerId: 'W', showReasoning: true, newId: function () { return 'toolu_' + (++n); }, continuesAfter: function () { return continues; } });
};
var ev = function (seq, kind, data, item, generation) {
    if (data === void 0) { data = {}; }
    if (item === void 0) { item = null; }
    if (generation === void 0) { generation = 1; }
    return ({ seq: seq, kind: kind, data: data, item: item, generation: generation, task_id: 'T' + generation });
};
(0, testing_1.test)('streamed deltas join one text block and a message already streamed is not repeated', function () {
    var open = (0, mirror_1.openStep)();
    var p = (0, mirror_1.planBatch)([ev(1, 'message.delta', { text: 'Hello ' }, 'm1'), ev(2, 'message.delta', { text: 'world' }, 'm1'), ev(3, 'message.completed', { text: 'Hello world' }, 'm1')], open, ctx());
    (0, testing_1.expect)(p.chunks).toEqual([{ kind: 'text', index: 0, text: 'Hello ' }, { kind: 'text', index: 0, text: 'world' }]);
    (0, testing_1.expect)(p.consumed).toBe(3);
    (0, testing_1.expect)(p.end).toBe(null);
    (0, testing_1.expect)(open.answer).toBe('Hello world');
});
(0, testing_1.test)('a tool start ends the step with a mirror call naming worker, generation and item', function () {
    var p = (0, mirror_1.planBatch)([ev(1, 'message.completed', { text: 'Running tests.' }, 'm1'), ev(2, 'tool.started', { kind: 'exec', title: 'pytest -q', input: { command: 'pytest -q' } }, 'c1'), ev(3, 'message.delta', { text: 'after' }, 'm2')], (0, mirror_1.openStep)(), ctx());
    (0, testing_1.expect)(p.end).toBe('tool_use');
    (0, testing_1.expect)(p.consumed).toBe(2);
    var tool = p.chunks.find(function (c) { return c.kind === 'tool'; });
    (0, testing_1.expect)(tool.name).toBe(mirror_1.MIRROR_TOOL);
    var input = JSON.parse(p.chunks.find(function (c) { return c.kind === 'input'; }).json);
    (0, testing_1.expect)(input).toEqual({ kind: 'exec', title: 'pytest -q', detail: 'pytest -q', ref: 'W:1:c1' });
    (0, testing_1.expect)(p.refs['W:1:c1']).toMatchObject({ item: 'c1', kind: 'exec', startSeq: 2, generation: 1 });
    (0, testing_1.expect)(p.toolUses).toEqual([{ name: mirror_1.MIRROR_TOOL, input: input }]);
});
(0, testing_1.test)('parallel tool starts share one step, at most eight, and their completions are consumed', function () {
    var events = Array.from({ length: 10 }, function (_, i) { return ev(i + 1, 'tool.started', { kind: 'read', title: 'f' + i, input: { path: 'f' + i } }, 'r' + i); });
    events.splice(2, 0, ev(99, 'tool.completed', { status: 'ok' }, 'r0'));
    var p = (0, mirror_1.planBatch)(events, (0, mirror_1.openStep)(), ctx());
    (0, testing_1.expect)(p.toolUses.length).toBe(8);
    (0, testing_1.expect)(p.end).toBe('tool_use');
});
(0, testing_1.test)('a final candidate equal to the terminal text is shown once, in the final answer', function () {
    var _a;
    var open = (0, mirror_1.openStep)();
    var p = (0, mirror_1.planBatch)([ev(1, 'message.completed', { text: 'Done: A, B.', is_final_candidate: true }, 'm1'), ev(2, 'task.terminal', { status: 'completed', final_text: 'Done: A, B.' })], open, ctx());
    (0, testing_1.expect)(p.end).toBe('terminal');
    (0, testing_1.expect)(p.chunks).toEqual([]);
    (0, testing_1.expect)((_a = p.terminal) === null || _a === void 0 ? void 0 : _a.seq).toBe(2);
});
(0, testing_1.test)('a final candidate that differs from the terminal text is flushed before the answer', function () {
    var p = (0, mirror_1.planBatch)([ev(1, 'message.completed', { text: 'Interim.', is_final_candidate: true }, 'm1'), ev(2, 'task.terminal', { status: 'completed', final_text: 'Final.' })], (0, mirror_1.openStep)(), ctx());
    (0, testing_1.expect)(p.chunks).toEqual([{ kind: 'text', index: 0, text: 'Interim.' }]);
});
(0, testing_1.test)('a terminal followed by a forwarded follow-up continues the same run with a marker', function () {
    var p = (0, mirror_1.planBatch)([ev(1, 'task.terminal', { status: 'completed', final_text: 'one' }), ev(2, 'task.started', { origin: 'human-claude-view' }, null, 2)], (0, mirror_1.openStep)(), ctx(true));
    (0, testing_1.expect)(p.end).toBe(null);
    var text = p.chunks.map(function (c) { return c.text; }).join('');
    (0, testing_1.expect)(text).toContain('generation 1 completed; continuing');
    (0, testing_1.expect)(text).toContain('generation 2: your follow-up');
    (0, testing_1.expect)(p.folded.map(function (e) { return e.generation; })).toEqual([1]);
});
(0, testing_1.test)('generations folded into one run all reach its final answer, which the one notification carries', function () {
    var terminal = ev(9, 'task.terminal', { status: 'completed', final_text: 'two' }, null, 2);
    var text = (0, mirror_1.finalAnswer)(terminal, { provider: 'codex', workerId: 'W' }, 'mirror', [1, 2], true, [{ generation: 1, text: 'one' }]);
    (0, testing_1.expect)(text.indexOf('[generation 1]\none')).toBe(0);
    (0, testing_1.expect)(text).toContain('[generation 2]\ntwo');
    (0, testing_1.expect)(text).toContain('generations: 1, 2');
});
(0, testing_1.test)('gaps, fallbacks, plans and inputs are marked, never silent', function () {
    var p = (0, mirror_1.planBatch)([ev(1, 'gap', { reason: 'unmapped', from_seq: 4 }), ev(2, 'fallback.started', { from: { provider: 'claude' }, to: { provider: 'antigravity', model: 'gemini-4-argon-high' }, reason: 'QUOTA_EXHAUSTED' }),
        ev(3, 'plan.updated', { items: [{ text: 'a', status: 'completed' }, { text: 'b', status: 'pending' }] }), ev(4, 'input.queued', { mode: 'queue' })], (0, mirror_1.openStep)(), ctx());
    var text = p.chunks.map(function (c) { return c.text; }).join('');
    (0, testing_1.expect)(text).toContain('[activity unavailable from seq 4: unmapped]');
    (0, testing_1.expect)(text).toContain('provider fallback: claude → antigravity gemini-4-argon-high (QUOTA_EXHAUSTED)');
    (0, testing_1.expect)(text).toContain('- [x] a');
    (0, testing_1.expect)(text).toContain('queued for the worker');
});
(0, testing_1.test)('reasoning streams as thinking only when enabled; usage reports the cumulative delta', function () {
    var p = (0, mirror_1.planBatch)([ev(1, 'reasoning.delta', { text: 'hm' }), ev(2, 'usage', { output_tokens: 1500, cumulative: true })], (0, mirror_1.openStep)(), ctx(), 1000);
    (0, testing_1.expect)(p.chunks).toEqual([{ kind: 'thinking', index: 0, text: 'hm' }]);
    (0, testing_1.expect)(p.usage).toEqual({ delta: 500, cumulative: 1500 });
    var off = (0, mirror_1.planBatch)([ev(1, 'reasoning.delta', { text: 'hm' })], (0, mirror_1.openStep)(), __assign(__assign({}, ctx()), { showReasoning: false }));
    (0, testing_1.expect)(off.chunks).toEqual([]);
});
(0, testing_1.test)('a file change with no tool event becomes an immediate edit row', function () {
    var p = (0, mirror_1.planBatch)([ev(7, 'files.changed', { paths: ['a.py', 'b.py'], stats: { added: 3, removed: 1 }, diff: { text: '@@' } })], (0, mirror_1.openStep)(), ctx());
    (0, testing_1.expect)(p.end).toBe('tool_use');
    (0, testing_1.expect)(p.refs['W:1:seq7']).toMatchObject({ kind: 'edit', immediate: true });
});
(0, testing_1.test)('tool results replay output, exit, duration and diffs; a terminal without completion is interrupted', function () {
    var pending = { item: 'c1', kind: 'exec', startSeq: 2, title: 'pytest', detail: 'pytest', generation: 1 };
    var ok = (0, mirror_1.toolResult)('codex', pending, [ev(3, 'tool.completed', { status: 'ok', exit_code: 0, duration_ms: 90000, output: { text: '42 passed', truncated: false } }, 'c1')], 100);
    (0, testing_1.expect)(ok.text).toBe('[codex exec] exit 0 · 1m30s\n42 passed');
    var cut = (0, mirror_1.toolResult)('codex', pending, [ev(3, 'tool.completed', { status: 'ok', output: { text: 'x'.repeat(500), truncated: true, bytes: 900, artifact_id: 'A1' } }, 'c1')], 40);
    (0, testing_1.expect)(cut.text).toContain('[full output: npeer artifact-read A1]');
    (0, testing_1.expect)(cut.text).toContain('…');
    (0, testing_1.expect)((0, mirror_1.toolResult)('codex', pending, [ev(4, 'task.terminal', { status: 'interrupted' })], 100).status).toBe('interrupted');
});
(0, testing_1.test)('the final answer carries NativePeers references and says who delivered it', function () {
    var terminal = ev(9, 'task.terminal', { status: 'failed', final_text: 'partial', error: { code: 'QUOTA_EXHAUSTED', message: 'limit' } });
    var text = (0, mirror_1.finalAnswer)(terminal, { provider: 'codex', modelRequested: 'gpt-6.1-sol', modelObserved: 'gpt-6.1-sol', effort: 'high', profile: 'analysis', workerId: 'W', groupId: 'G', taskId: 'T', nativeId: 'N', elapsedMs: 161000, resultArtifact: 'R' }, 'native', [1]);
    (0, testing_1.expect)(text).toContain('— NativePeers · codex · gpt-6.1-sol · high · analysis · failed in 2m41s');
    (0, testing_1.expect)(text).toContain('error: QUOTA_EXHAUSTED limit');
    (0, testing_1.expect)(text).toContain('worker W · group G · task T · native N');
    (0, testing_1.expect)(text).toContain('artifacts: result R');
    (0, testing_1.expect)(text).toContain('delivered via NativePeers notice');
    (0, testing_1.expect)((0, mirror_1.finalAnswer)(terminal, { provider: 'codex', workerId: 'W', groupId: 'G' }, 'native', [1], false, [], 'wait')).toContain('result retained in NativePeers: npeer result G');
    (0, testing_1.expect)((0, mirror_1.finalAnswer)(terminal, { provider: 'codex', workerId: 'W' }, 'mirror', [1], true).startsWith('partial')).toBe(false);
});
