"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var testing_1 = require("claude-code/testing");
var agents_1 = require("../hooks/agents");
var inputs_1 = require("../hooks/inputs");
(0, testing_1.test)('a shadow type has only the mirror tool, an unserved model and an explicit permission mode', function () {
    var spec = (0, agents_1.agentSpec)('codex');
    (0, testing_1.expect)(spec.tools).toEqual(['mcp__np__mirror']);
    (0, testing_1.expect)(spec.model).toBe('np-mirror-codex');
    (0, testing_1.expect)((0, agents_1.shadowModel)('grok')).toBe('np-mirror-grok');
    (0, testing_1.expect)(spec.permissionMode).toBe('dontAsk');
    (0, testing_1.expect)(spec.background).toBe(true);
    (0, testing_1.expect)(spec.omitClaudeMd).toBe(true);
});
(0, testing_1.test)('the np header is strict JSON with known keys and is stripped from the brief', function () {
    var ok = (0, agents_1.parseHeader)('```np\n{"model":"gpt-6.1-sol","effort":"high","title":"Review"}\n```\nDo the review.');
    (0, testing_1.expect)(ok).toEqual({ header: { model: 'gpt-6.1-sol', effort: 'high', title: 'Review' }, brief: 'Do the review.' });
    (0, testing_1.expect)((0, agents_1.parseHeader)('Just a brief.')).toEqual({ header: {}, brief: 'Just a brief.' });
    // The forms a model writes from the compose section's one-line example.
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np {"model":"gpt-6.1-sol","effort":"high"}\n```\nDo it.')).toEqual({ header: { model: 'gpt-6.1-sol', effort: 'high' }, brief: 'Do it.' });
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np {"effort":"low"}```\nDo it.')).toEqual({ header: { effort: 'low' }, brief: 'Do it.' });
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np {"effort":"low"}\nDo it.').error).toContain('not closed');
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np\n{"colour":"red"}\n```\nx').error).toContain('unknown np header key "colour"');
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np\n{model: x}\n```\nx').error).toContain('not strict JSON');
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np\n{"profile":"trusted"}\n```\nx').error).toContain('analysis or workspace');
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np\n{"model":"m"}\n```\n   ').error).toContain('empty');
    (0, testing_1.expect)((0, agents_1.parseHeader)('```np\n' + JSON.stringify({ title: 'x'.repeat(5000) }) + '\n```\nx').error).toContain('4 KiB');
});
(0, testing_1.test)('labels read like model names', function () {
    (0, testing_1.expect)((0, agents_1.modelLabel)('gpt-6.1-sol', 'codex')).toBe('GPT-6.1 Sol');
    (0, testing_1.expect)((0, agents_1.modelLabel)('claude-sonnet-5-5', 'claude')).toBe('Sonnet 5.5');
    (0, testing_1.expect)((0, agents_1.modelLabel)('grok-4.7', 'grok')).toBe('Grok 4.7');
    (0, testing_1.expect)((0, agents_1.modelLabel)('gemini-4-argon-high', 'antigravity')).toBe('Gemini 4 Argon high');
    (0, testing_1.expect)((0, agents_1.modelLabel)(undefined, 'codex')).toBe('codex default');
    (0, testing_1.expect)((0, agents_1.describe)('codex', 'gpt-6.1-sol', 'high', 'Codebase review')).toBe('GPT-6.1 Sol high · Codebase review');
    (0, testing_1.expect)((0, agents_1.titleFrom)({}, '  Review   the code ', 'brief')).toBe('Review the code');
});
(0, testing_1.test)('worker specs default to the read-only analysis profile and never invent a model', function () {
    (0, testing_1.expect)((0, agents_1.workerSpec)('codex', {}, 'b', 't', '/w')).toEqual({ provider: 'codex', title: 't', task: 'b', permission_profile: 'analysis', workspace: { mode: 'inherit-read-only' }, cwd: '/w' });
    (0, testing_1.expect)((0, agents_1.workerSpec)('claude', { profile: 'workspace', model: 'sonnet', effort: 'high' }, 'b', 't', undefined))
        .toEqual({ provider: 'claude', title: 't', task: 'b', permission_profile: 'workspace', workspace: { mode: 'isolated-worktree' }, model: 'sonnet', reasoning_effort: 'high' });
});
(0, testing_1.test)('engine framing is stripped from typed and parent follow-ups (gate G3)', function () {
    (0, testing_1.expect)((0, inputs_1.stripFraming)('<system-reminder>\nThe user sent a new message while you were working:\nalso check the tests\n\nThis is how Claude Code surfaces messages the user sends mid-turn — within the running turn. Address the message above.\n</system-reminder>')).toBe('also check the tests');
    (0, testing_1.expect)((0, inputs_1.stripFraming)('The user sent a new message while you were working:\nsecond\n\nThis is how Claude Code surfaces messages the user sends mid-turn, rather than as a separate turn.')).toBe('second');
    (0, testing_1.expect)((0, inputs_1.stripFraming)('The coordinator sent a message while you were working:\nfollow up\n\nAddress this before completing your current task.')).toBe('follow up');
    (0, testing_1.expect)((0, inputs_1.stripFraming)('plain text')).toBe('plain text');
});
(0, testing_1.test)('only prompt and delivery rows from a person or the parent are follow-ups', function () {
    var row = function (door, kind, content, type) {
        if (type === void 0) { type = 'user'; }
        return ({ door: door, origin: { kind: kind }, message: { type: type, content: content } });
    };
    (0, testing_1.expect)((0, inputs_1.followUp)(row('prompt', 'composer', [{ type: 'text', text: 'hi' }]))).toEqual({ text: 'hi', origin: 'human-claude-view' });
    (0, testing_1.expect)((0, inputs_1.followUp)(row('delivery', 'composer', [{ type: 'text', text: 'hi' }], 'attachment'))).toEqual({ text: 'hi', origin: 'human-claude-view' });
    (0, testing_1.expect)((0, inputs_1.followUp)(row('prompt', 'coordinator', [{ type: 'text', text: 'go' }]))).toEqual({ text: 'go', origin: 'parent-model' });
    (0, testing_1.expect)((0, inputs_1.followUp)(row('queue', 'composer', [{ type: 'text', text: 'hi' }]))).toBe(null);
    (0, testing_1.expect)((0, inputs_1.followUp)(row('tool-result', 'tool', [{ type: 'tool_result', content: 'x' }]))).toBe(null);
    (0, testing_1.expect)((0, inputs_1.followUp)(row('prompt', 'engine', [{ type: 'text', text: 'x' }]))).toBe(null);
    (0, testing_1.expect)((0, inputs_1.provenance)('model')).toBe('parent-model');
    (0, testing_1.expect)((0, inputs_1.provenance)('task-notification')).toBe(null);
});
