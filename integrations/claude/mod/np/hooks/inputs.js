"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FRAMINGS = void 0;
exports.stripFraming = stripFraming;
exports.provenance = provenance;
exports.followUp = followUp;
exports.isBrief = isBrief;
exports.unforwarded = unforwarded;
exports.requestId = requestId;
// Framings observed on Claude Code 2.1.314 (gate G3). A typed message reaches a
// running shadow as a 'delivery' attachment wrapped in <system-reminder>, a
// completed one as a 'prompt' row; the parent's SendMessage as 'coordinator'.
exports.FRAMINGS = [
    { prefix: 'The user sent a new message while you were working:\n',
        suffixes: ['\n\nThis is how Claude Code surfaces messages the user sends mid-turn'] },
    { prefix: 'The coordinator sent a message while you were working:\n',
        suffixes: ['\n\nAddress this before completing your current task.'] },
];
function stripFraming(text) {
    var _a;
    var out = text.trim();
    var wrapped = /^<system-reminder>\s*([\s\S]*?)\s*<\/system-reminder>$/.exec(out);
    if (wrapped)
        out = (_a = wrapped[1]) !== null && _a !== void 0 ? _a : out;
    for (var _i = 0, FRAMINGS_1 = exports.FRAMINGS; _i < FRAMINGS_1.length; _i++) {
        var framing = FRAMINGS_1[_i];
        if (!out.startsWith(framing.prefix))
            continue;
        out = out.slice(framing.prefix.length);
        for (var _b = 0, _c = framing.suffixes; _b < _c.length; _b++) {
            var suffix = _c[_b];
            var at = out.lastIndexOf(suffix);
            if (at >= 0)
                out = out.slice(0, at);
        }
        break;
    }
    return out.trim();
}
function provenance(originKind) {
    if (originKind === 'composer')
        return 'human-claude-view';
    if (originKind === 'coordinator' || originKind === 'model')
        return 'parent-model';
    return null;
}
// A follow-up row: a user row or a delivered attachment with text and no tool
// result, by the prompt or delivery door. Never 'queue': that is the message
// queue's record of a message that also arrives as a row.
function followUp(row) {
    var _a;
    if (row.door !== 'prompt' && row.door !== 'delivery')
        return null;
    if (row.message.type !== 'user' && row.message.type !== 'attachment')
        return null;
    var origin = provenance((_a = row.origin) === null || _a === void 0 ? void 0 : _a.kind);
    if (!origin)
        return null;
    var blocks = Array.isArray(row.message.content) ? row.message.content : [];
    if (blocks.some(function (b) { return b.type === 'tool_result'; }))
        return null;
    var raw = blocks.filter(function (b) { return b.type === 'text' && typeof b.text === 'string'; }).map(function (b) { return b.text; }).join('\n');
    if (!raw.trim())
        return null;
    var text = stripFraming(raw);
    return text ? { text: text, origin: origin } : null;
}
function isBrief(text, brief) {
    return brief !== undefined && text.trim() === brief.trim();
}
function unforwarded(inputs) {
    return inputs.filter(function (i) { return !i.forwarded && i.attempts < 3; });
}
function requestId(uuid) {
    return 'np-in:' + uuid;
}
