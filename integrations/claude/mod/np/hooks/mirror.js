"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MAX_TEXT = exports.MAX_EVENTS = exports.MAX_TOOLS = exports.MIRROR_TOOL = void 0;
exports.openStep = openStep;
exports.ref = ref;
exports.planBatch = planBatch;
exports.duration = duration;
exports.headTail = headTail;
exports.toolResult = toolResult;
exports.finalAnswer = finalAnswer;
exports.MIRROR_TOOL = 'mcp__np__mirror';
exports.MAX_TOOLS = 8;
exports.MAX_EVENTS = 512;
exports.MAX_TEXT = 262144;
function openStep() {
    return { block: 0, textBlock: null, answer: '', streamed: {}, tools: 0, chars: 0 };
}
var SKIP_AFTER_TOOL = new Set(['tool.progress', 'tool.completed', 'usage']);
function ref(workerId, generation, item) {
    return "".concat(workerId, ":").concat(generation, ":").concat(item);
}
function detailOf(kind, input) {
    if (!input || typeof input !== 'object')
        return String(input !== null && input !== void 0 ? input : '').slice(0, 300);
    if (typeof input.command === 'string')
        return input.command.slice(0, 300);
    if (Array.isArray(input.command))
        return input.command.join(' ').slice(0, 300);
    for (var _i = 0, _a = ['file_path', 'path', 'pattern', 'query', 'url']; _i < _a.length; _i++) {
        var key = _a[_i];
        if (typeof input[key] === 'string')
            return input[key].slice(0, 300);
    }
    return JSON.stringify(input).slice(0, 300);
}
function planBatch(events, open, ctx, lastCumulative) {
    var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t, _u, _v, _w, _x, _y, _z, _0;
    var out = { chunks: [], consumed: null, end: null, refs: {}, toolUses: [], usage: { delta: 0 }, folded: [] };
    var text = function (s) {
        if (!s)
            return;
        if (open.textBlock === null) {
            open.textBlock = open.block++;
            if (open.answer && !open.answer.endsWith('\n'))
                s = '\n\n' + s;
        }
        out.chunks.push({ kind: 'text', index: open.textBlock, text: s });
        open.answer += s;
        open.chars += s.length;
    };
    var flushHeld = function () {
        if (open.held !== undefined) {
            var held = open.held;
            open.held = undefined;
            text(held);
        }
    };
    var marker = function (s) { flushHeld(); open.textBlock = null; text(s + '\n'); open.textBlock = null; };
    var tool = function (kind, title, detail, r, pending) {
        flushHeld();
        open.textBlock = null;
        var index = open.block++, id = ctx.newId(), input = { kind: kind, title: title.slice(0, 200), detail: detail, ref: r };
        out.chunks.push({ kind: 'tool', index: index, id: id, name: exports.MIRROR_TOOL }, { kind: 'input', index: index, json: JSON.stringify(input) });
        out.toolUses.push({ name: exports.MIRROR_TOOL, input: input });
        out.refs[r] = pending;
        open.tools += 1;
    };
    for (var _i = 0, _1 = events.slice(0, exports.MAX_EVENTS); _i < _1.length; _i++) {
        var e = _1[_i];
        if (open.tools > 0 && e.kind !== 'tool.started' && !SKIP_AFTER_TOOL.has(e.kind))
            break;
        if (open.tools >= exports.MAX_TOOLS && e.kind === 'tool.started')
            break;
        if (open.chars > exports.MAX_TEXT && open.tools === 0 && out.chunks.length)
            break;
        var d = (_a = e.data) !== null && _a !== void 0 ? _a : {};
        switch (e.kind) {
            case 'message.delta':
                flushHeld();
                if (e.item)
                    open.streamed[e.item] = true;
                text(String((_b = d.text) !== null && _b !== void 0 ? _b : ''));
                break;
            case 'message.completed':
                if (e.item && open.streamed[e.item])
                    break;
                if (d.is_final_candidate) {
                    flushHeld();
                    open.held = String((_c = d.text) !== null && _c !== void 0 ? _c : '');
                }
                else {
                    flushHeld();
                    open.textBlock = null;
                    text(String((_d = d.text) !== null && _d !== void 0 ? _d : ''));
                }
                break;
            case 'reasoning.delta':
                if (ctx.showReasoning && d.text) {
                    flushHeld();
                    open.textBlock = null;
                    out.chunks.push({ kind: 'thinking', index: open.block++, text: String(d.text) });
                }
                break;
            case 'tool.started': {
                var item = (_e = e.item) !== null && _e !== void 0 ? _e : 'seq' + e.seq;
                var title = String((_g = (_f = d.title) !== null && _f !== void 0 ? _f : d.kind) !== null && _g !== void 0 ? _g : 'tool'), kind = String((_h = d.kind) !== null && _h !== void 0 ? _h : 'other');
                tool(kind, title, detailOf(kind, d.input), ref(ctx.workerId, e.generation, item), { item: item, kind: kind, startSeq: e.seq, title: title, detail: detailOf(kind, d.input), generation: e.generation });
                break;
            }
            case 'files.changed':
                if (!e.item) {
                    var paths = Array.isArray(d.paths) ? d.paths : [];
                    var title = "".concat(paths.slice(0, 3).join(', ')).concat(paths.length > 3 ? " +".concat(paths.length - 3) : '', " (+").concat((_k = (_j = d.stats) === null || _j === void 0 ? void 0 : _j.added) !== null && _k !== void 0 ? _k : 0, " \u2212").concat((_m = (_l = d.stats) === null || _l === void 0 ? void 0 : _l.removed) !== null && _m !== void 0 ? _m : 0, ")");
                    var item = 'seq' + e.seq;
                    tool('edit', title, paths.join(', ').slice(0, 300), ref(ctx.workerId, e.generation, item), { item: item, kind: 'edit', startSeq: e.seq, title: title, detail: paths.join(', ').slice(0, 300), generation: e.generation, immediate: true });
                }
                break;
            case 'plan.updated': {
                var items = Array.isArray(d.items) ? d.items : [];
                marker('↳ plan\n' + items.map(function (i) { var _a; return "- [".concat(i.status === 'completed' ? 'x' : ' ', "] ").concat(String((_a = i.text) !== null && _a !== void 0 ? _a : '')); }).join('\n'));
                break;
            }
            case 'usage': {
                var n = Number((_o = d.output_tokens) !== null && _o !== void 0 ? _o : 0);
                if (d.cumulative) {
                    var base = (_q = (_p = out.usage.cumulative) !== null && _p !== void 0 ? _p : lastCumulative) !== null && _q !== void 0 ? _q : 0;
                    if (n > base)
                        out.usage.delta += n - base;
                    out.usage.cumulative = Math.max(n, base);
                }
                else if (n > 0)
                    out.usage.delta += n;
                break;
            }
            case 'input.queued':
                marker(d.mode === 'steer' ? '↳ follow-up steered into the running turn' : d.mode === 'resume' ? '↳ follow-up resumes the worker' : '↳ follow-up queued for the worker (after its current turn)');
                break;
            case 'input.accepted':
                marker('↳ follow-up accepted');
                break;
            case 'task.started':
                if (e.generation > 1)
                    marker("\u21B3 generation ".concat(e.generation, ": ").concat(d.origin === 'human-claude-view' || d.origin === 'human' ? 'your follow-up' : d.origin === 'parent-model' ? 'follow-up from main' : 'follow-up'));
                break;
            case 'task.state':
                if (d.state === 'APPROVAL_BLOCKED' || d.state === 'WAITING')
                    marker("\u21B3 worker ".concat(String(d.state).toLowerCase().replace('_', ' ')).concat(d.reason ? ': ' + d.reason : ''));
                break;
            case 'fallback.started':
                marker("\u21B3 provider fallback: ".concat((_s = (_r = d.from) === null || _r === void 0 ? void 0 : _r.provider) !== null && _s !== void 0 ? _s : '?', " \u2192 ").concat((_u = (_t = d.to) === null || _t === void 0 ? void 0 : _t.provider) !== null && _u !== void 0 ? _u : '?', " ").concat((_w = (_v = d.to) === null || _v === void 0 ? void 0 : _v.model) !== null && _w !== void 0 ? _w : '', " (").concat((_x = d.reason) !== null && _x !== void 0 ? _x : 'fallback', ")").replace('  ', ' '));
                break;
            case 'gap':
                marker("[activity unavailable".concat(d.from_seq !== undefined ? " from seq ".concat(d.from_seq) : '', ": ").concat((_y = d.reason) !== null && _y !== void 0 ? _y : 'unknown', "]"));
                break;
            case 'task.terminal':
                if (ctx.continuesAfter(e.generation)) {
                    flushHeld();
                    marker("\u21B3 generation ".concat(e.generation, " ").concat((_z = d.status) !== null && _z !== void 0 ? _z : 'ended', "; continuing"));
                    out.folded.push(e);
                }
                else {
                    if (open.held !== undefined && open.held.trim() !== String((_0 = d.final_text) !== null && _0 !== void 0 ? _0 : '').trim())
                        flushHeld();
                    open.held = undefined;
                    out.end = 'terminal';
                    out.terminal = e;
                }
                break;
            default:
                break;
        }
        out.consumed = e.seq;
        if (out.end)
            break;
    }
    if (!out.end && open.tools > 0)
        out.end = 'tool_use';
    return out;
}
function duration(ms) {
    if (ms === undefined || !Number.isFinite(ms) || ms < 0)
        return '';
    var s = Math.round(ms / 1000);
    if (s < 60)
        return "".concat(s, "s");
    if (s < 3600)
        return "".concat(Math.floor(s / 60), "m").concat(String(s % 60).padStart(2, '0'), "s");
    return "".concat(Math.floor(s / 3600), "h").concat(String(Math.floor(s / 60) % 60).padStart(2, '0'), "m");
}
function headTail(text, limit) {
    if (text.length <= limit)
        return { text: text, truncated: false };
    var half = Math.max(0, Math.floor((limit - 3) / 2));
    return { text: text.slice(0, half) + '\n…\n' + text.slice(text.length - half), truncated: true };
}
// The mirror tool's result for one item: started, completed and any file change.
function toolResult(provider, pending, events, limit) {
    var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q;
    var done = events.find(function (e) { var _a; return e.kind === 'tool.completed' && ((_a = e.item) !== null && _a !== void 0 ? _a : '') === pending.item; });
    var files = events.filter(function (e) { var _a; return e.kind === 'files.changed' && (((_a = e.item) !== null && _a !== void 0 ? _a : 'seq' + e.seq) === pending.item); });
    var terminal = events.find(function (e) { return e.kind === 'task.terminal'; });
    var d = (_a = done === null || done === void 0 ? void 0 : done.data) !== null && _a !== void 0 ? _a : {};
    var status = done ? String((_b = d.status) !== null && _b !== void 0 ? _b : 'ok') : pending.immediate ? 'ok' : terminal ? 'interrupted' : 'running';
    var head = ["[".concat(provider, " ").concat(pending.kind, "]"), status === 'ok' && d.exit_code !== undefined ? "exit ".concat(d.exit_code) : status,
        duration(d.duration_ms)].filter(Boolean).join(' · ').replace('] · ', '] ');
    var lines = [head];
    var output = ((_c = d.output) === null || _c === void 0 ? void 0 : _c.text) ? headTail(String(d.output.text), limit) : null;
    if (output === null || output === void 0 ? void 0 : output.text)
        lines.push(output.text);
    for (var _i = 0, files_1 = files; _i < files_1.length; _i++) {
        var f = files_1[_i];
        var fd = (_d = f.data) !== null && _d !== void 0 ? _d : {};
        lines.push("".concat(((_e = fd.paths) !== null && _e !== void 0 ? _e : []).join(', '), " (+").concat((_g = (_f = fd.stats) === null || _f === void 0 ? void 0 : _f.added) !== null && _g !== void 0 ? _g : 0, " \u2212").concat((_j = (_h = fd.stats) === null || _h === void 0 ? void 0 : _h.removed) !== null && _j !== void 0 ? _j : 0, ")"));
        if ((_k = fd.diff) === null || _k === void 0 ? void 0 : _k.text)
            lines.push(headTail(String(fd.diff.text), limit).text);
        if ((_l = fd.diff) === null || _l === void 0 ? void 0 : _l.artifact_id)
            lines.push("[full diff: npeer artifact-read ".concat(fd.diff.artifact_id, "]"));
    }
    if (((_m = d.output) === null || _m === void 0 ? void 0 : _m.truncated) || (output === null || output === void 0 ? void 0 : output.truncated))
        lines.push(((_o = d.output) === null || _o === void 0 ? void 0 : _o.artifact_id) ? "[full output: npeer artifact-read ".concat(d.output.artifact_id, "]") : "[output truncated; ".concat((_q = (_p = d.output) === null || _p === void 0 ? void 0 : _p.bytes) !== null && _q !== void 0 ? _q : '?', " bytes in the worker's native history]"));
    return { text: lines.join('\n'), status: status };
}
// The final answer the parent reads in the notification (spec 7.5.1).
// `shown`: the step already streamed the worker's final text, so only the footer
// follows it; the step's whole answer is still what the parent reads.
function finalAnswer(terminal, s, owner, generations, shown, folded, mode) {
    var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m;
    if (shown === void 0) { shown = false; }
    if (folded === void 0) { folded = []; }
    var d = (_a = terminal.data) !== null && _a !== void 0 ? _a : {};
    var status = String((_b = d.status) !== null && _b !== void 0 ? _b : 'completed');
    var body = headTail(String((_c = d.final_text) !== null && _c !== void 0 ? _c : ''), 12288);
    var lines = [];
    // Earlier generations of this run were shown as they ended; the notification carries only this message, so it repeats them.
    for (var _i = 0, folded_1 = folded; _i < folded_1.length; _i++) {
        var f = folded_1[_i];
        lines.push("[generation ".concat(f.generation, "]"), f.text.trim() || '(no final text)', '');
    }
    if (folded.length)
        lines.push("[generation ".concat(terminal.generation, "]"));
    if (body.text.trim() && (!shown || folded.length))
        lines.push(body.truncated ? body.text + "\n[\u2026 truncated; full text: npeer result ".concat((_d = s.taskId) !== null && _d !== void 0 ? _d : s.workerId, " ]") : body.text);
    if (status === 'interrupted')
        lines.push('(interrupted — partial result above)');
    lines.push('');
    var model = s.modelObserved && s.modelRequested && s.modelObserved !== s.modelRequested
        ? "".concat(s.modelRequested, " (observed ").concat(s.modelObserved, ")") : (_f = (_e = s.modelObserved) !== null && _e !== void 0 ? _e : s.modelRequested) !== null && _f !== void 0 ? _f : 'default';
    lines.push("\u2014 NativePeers \u00B7 ".concat(s.provider, " \u00B7 ").concat(model).concat(s.effort ? ' · ' + s.effort : '').concat(s.profile ? ' · ' + s.profile : '', " \u00B7 ").concat(status).concat(s.elapsedMs !== undefined ? ' in ' + duration(s.elapsedMs) : ''));
    if ((_g = d.error) === null || _g === void 0 ? void 0 : _g.code)
        lines.push("error: ".concat(d.error.code, " ").concat(String((_h = d.error.message) !== null && _h !== void 0 ? _h : '').slice(0, 300)));
    lines.push(["worker ".concat(s.workerId), s.groupId && "group ".concat(s.groupId), s.taskId && "task ".concat(s.taskId), s.nativeId && "native ".concat(s.nativeId)].filter(Boolean).join(' · '));
    var refs = [s.resultArtifact && "result ".concat(s.resultArtifact), s.patchArtifact && "patch ".concat(s.patchArtifact)].filter(Boolean);
    var changes = ((_j = s.changes) === null || _j === void 0 ? void 0 : _j.files) ? "changes: ".concat(s.changes.files, " files (+").concat((_k = s.changes.added) !== null && _k !== void 0 ? _k : 0, " \u2212").concat((_l = s.changes.removed) !== null && _l !== void 0 ? _l : 0, ")").concat(s.changes.worktree ? ' in ' + s.changes.worktree : '') : '';
    if (refs.length || changes)
        lines.push([refs.length ? "artifacts: ".concat(refs.join(' · '), " (npeer artifact-read ID)") : '', changes].filter(Boolean).join(' · '));
    if (generations.length > 1)
        lines.push("generations: ".concat(generations.join(', ')));
    // A wait-mode route is never pushed: its result stays with NativePeers until asked for.
    if (owner === 'native')
        lines.push(mode === 'wait' ? "result retained in NativePeers: npeer result ".concat((_m = s.groupId) !== null && _m !== void 0 ? _m : s.workerId) : 'delivered via NativePeers notice');
    return lines.join('\n');
}
