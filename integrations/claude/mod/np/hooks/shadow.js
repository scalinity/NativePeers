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
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __generator = (this && this.__generator) || function (thisArg, body) {
    var _ = { label: 0, sent: function() { if (t[0] & 1) throw t[1]; return t[1]; }, trys: [], ops: [] }, f, y, t, g = Object.create((typeof Iterator === "function" ? Iterator : Object).prototype);
    return g.next = verb(0), g["throw"] = verb(1), g["return"] = verb(2), typeof Symbol === "function" && (g[Symbol.iterator] = function() { return this; }), g;
    function verb(n) { return function (v) { return step([n, v]); }; }
    function step(op) {
        if (f) throw new TypeError("Generator is already executing.");
        while (g && (g = 0, op[0] && (_ = 0)), _) try {
            if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done) return t;
            if (y = 0, t) op = [op[0] & 2, t.value];
            switch (op[0]) {
                case 0: case 1: t = op; break;
                case 4: _.label++; return { value: op[1], done: false };
                case 5: _.label++; y = op[1]; op = [0]; continue;
                case 7: op = _.ops.pop(); _.trys.pop(); continue;
                default:
                    if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) { _ = 0; continue; }
                    if (op[0] === 3 && (!t || (op[1] > t[0] && op[1] < t[3]))) { _.label = op[1]; break; }
                    if (op[0] === 6 && _.label < t[1]) { _.label = t[1]; t = op; break; }
                    if (t && _.label < t[2]) { _.label = t[2]; _.ops.push(op); break; }
                    if (t[2]) _.ops.pop();
                    _.trys.pop(); continue;
            }
            op = body.call(thisArg, _);
        } catch (e) { op = [6, e]; y = 0; } finally { f = t = 0; }
        if (op[0] & 5) throw op[1]; return { value: op[0] ? op[1] : void 0, done: true };
    }
};
var __await = (this && this.__await) || function (v) { return this instanceof __await ? (this.v = v, this) : new __await(v); }
var __asyncGenerator = (this && this.__asyncGenerator) || function (thisArg, _arguments, generator) {
    if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
    var g = generator.apply(thisArg, _arguments || []), i, q = [];
    return i = Object.create((typeof AsyncIterator === "function" ? AsyncIterator : Object).prototype), verb("next"), verb("throw"), verb("return", awaitReturn), i[Symbol.asyncIterator] = function () { return this; }, i;
    function awaitReturn(f) { return function (v) { return Promise.resolve(v).then(f, reject); }; }
    function verb(n, f) { if (g[n]) { i[n] = function (v) { return new Promise(function (a, b) { q.push([n, v, a, b]) > 1 || resume(n, v); }); }; if (f) i[n] = f(i[n]); } }
    function resume(n, v) { try { step(g[n](v)); } catch (e) { settle(q[0][3], e); } }
    function step(r) { r.value instanceof __await ? Promise.resolve(r.value.v).then(fulfill, reject) : settle(q[0][2], r); }
    function fulfill(value) { resume("next", value); }
    function reject(value) { resume("throw", value); }
    function settle(f, v) { if (f(v), q.shift(), q.length) resume(q[0][0], q[0][1]); }
};
var __rest = (this && this.__rest) || function (s, e) {
    var t = {};
    for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p) && e.indexOf(p) < 0)
        t[p] = s[p];
    if (s != null && typeof Object.getOwnPropertySymbols === "function")
        for (var i = 0, p = Object.getOwnPropertySymbols(s); i < p.length; i++) {
            if (e.indexOf(p[i]) < 0 && Object.prototype.propertyIsEnumerable.call(s, p[i]))
                t[p[i]] = s[p[i]];
        }
    return t;
};
var __spreadArray = (this && this.__spreadArray) || function (to, from, pack) {
    if (pack || arguments.length === 2) for (var i = 0, l = from.length, ar; i < l; i++) {
        if (ar || !(i in from)) {
            if (!ar) ar = Array.prototype.slice.call(from, 0, i);
            ar[i] = from[i];
        }
    }
    return to.concat(ar || Array.prototype.slice.call(from));
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.loadShadow = loadShadow;
exports.saveShadow = saveShadow;
exports.pruneStore = pruneStore;
exports.newShadow = newShadow;
exports.stepShadow = stepShadow;
exports.mirrorCall = mirrorCall;
exports.captureRow = captureRow;
exports.completeShadow = completeShadow;
exports.faultShadow = faultShadow;
exports.isShadowStep = isShadowStep;
exports.shadows = shadows;
var bridge_1 = require("./bridge");
var agents_1 = require("./agents");
var inputs_1 = require("./inputs");
var mirror_1 = require("./mirror");
var state_1 = require("./state");
var STEP_CAP_MS = 8 * 60 * 1000;
var QUIET_MS = 60 * 1000;
var STORE_LIMIT = 200;
function loadShadow($, agentId) {
    return __awaiter(this, void 0, void 0, function () {
        var held, stored;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    if (!agentId)
                        return [2 /*return*/, undefined];
                    return [4 /*yield*/, (0, state_1.getMember)($, agentId)];
                case 1:
                    held = _a.sent();
                    if (held.agentId === agentId)
                        return [2 /*return*/, held
                            // $.state is per session id: after /clear or --resume it reads empty, and the
                            // durable copy re-attaches the shadow (gate G10).
                        ];
                    return [4 /*yield*/, $.store.get('shadow:' + agentId)];
                case 2:
                    stored = _a.sent();
                    if (!(stored && stored.agentId === agentId)) return [3 /*break*/, 4];
                    return [4 /*yield*/, (0, state_1.setMember)($, stored)];
                case 3:
                    _a.sent();
                    return [2 /*return*/, stored];
                case 4: return [2 /*return*/, undefined];
            }
        });
    });
}
function saveShadow($, shadow) {
    return __awaiter(this, void 0, void 0, function () {
        var s;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    s = __assign(__assign({}, shadow), { inputs: shadow.inputs.slice(-50) });
                    return [4 /*yield*/, (0, state_1.setMember)($, s)];
                case 1:
                    _a.sent();
                    return [4 /*yield*/, $.store.set('shadow:' + s.agentId, s)];
                case 2:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
// Keeps the durable copies to the newest 200 and to 30 days.
function pruneStore($) {
    return __awaiter(this, void 0, void 0, function () {
        var keys, now, rows, _i, keys_1, key, _a, _b, _c, _d, _e, _f, n, row;
        var _g;
        var _h, _j;
        return __generator(this, function (_k) {
            switch (_k.label) {
                case 0: return [4 /*yield*/, $.store.keys()];
                case 1:
                    keys = (_k.sent()).filter(function (k) { return k.startsWith('shadow:'); });
                    return [4 /*yield*/, $.clock.now()];
                case 2:
                    now = _k.sent(), rows = [];
                    _i = 0, keys_1 = keys;
                    _k.label = 3;
                case 3:
                    if (!(_i < keys_1.length)) return [3 /*break*/, 6];
                    key = keys_1[_i];
                    _b = (_a = rows).push;
                    _g = { key: key };
                    _c = Number;
                    return [4 /*yield*/, $.store.get(key)];
                case 4:
                    _b.apply(_a, [(_g.at = _c.apply(void 0, [(_j = (_h = ((_k.sent()))) === null || _h === void 0 ? void 0 : _h.startedAt) !== null && _j !== void 0 ? _j : 0]), _g)]);
                    _k.label = 5;
                case 5:
                    _i++;
                    return [3 /*break*/, 3];
                case 6:
                    rows.sort(function (a, b) { return b.at - a.at; });
                    _d = 0, _e = rows.entries();
                    _k.label = 7;
                case 7:
                    if (!(_d < _e.length)) return [3 /*break*/, 10];
                    _f = _e[_d], n = _f[0], row = _f[1];
                    if (!(n >= STORE_LIMIT || now - row.at > 30 * 86400000)) return [3 /*break*/, 9];
                    return [4 /*yield*/, $.store.delete(row.key)];
                case 8:
                    _k.sent();
                    _k.label = 9;
                case 9:
                    _d++;
                    return [3 /*break*/, 7];
                case 10: return [2 /*return*/];
            }
        });
    });
}
function newShadow(agentId, provider, title, session, startedAt, extra) {
    if (extra === void 0) { extra = {}; }
    return __assign(__assign(__assign({}, state_1.NONE), { agentId: agentId, provider: provider, title: title, session: session, startedAt: startedAt, phase: 'BINDING', briefConsumed: true }), extra);
}
function toolId() {
    return 'toolu_01np' + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 12);
}
function result(e, answer, stopReason, toolUses, outputTokens, provider) {
    var usage = outputTokens > 0 ? { input_tokens: 0, output_tokens: outputTokens, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: (0, agents_1.shadowModel)(provider) } : null;
    return { turnId: e.turnId, index: e.index, answer: answer, toolUses: toolUses, stopReason: stopReason, usage: usage };
}
// Transport errors are retried with backoff; after five the step fails closed
// through the registration's .catch.
function poll($, path) {
    return __awaiter(this, void 0, void 0, function () {
        var backoff, attempt, error_1;
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    backoff = [500, 1000, 2000, 4000, 8000];
                    attempt = 0;
                    _b.label = 1;
                case 1:
                    _b.trys.push([1, 3, , 5]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', path)];
                case 2: return [2 /*return*/, _b.sent()];
                case 3:
                    error_1 = _b.sent();
                    if (!(error_1 instanceof bridge_1.BridgeError) || !['BRIDGE_UNREACHABLE', 'BRIDGE_DOWN'].includes(error_1.code) || attempt >= backoff.length)
                        throw error_1;
                    return [4 /*yield*/, (0, bridge_1.sleep)($, (_a = backoff[attempt]) !== null && _a !== void 0 ? _a : 8000)];
                case 4:
                    _b.sent();
                    return [3 /*break*/, 5];
                case 5:
                    attempt++;
                    return [3 /*break*/, 1];
                case 6: return [2 /*return*/];
            }
        });
    });
}
function bind($, s) {
    return __awaiter(this, void 0, void 0, function () {
        var b;
        var _a, _b, _c, _d, _e, _f, _g;
        return __generator(this, function (_h) {
            switch (_h.label) {
                case 0:
                    if (!!s.workerId) return [3 /*break*/, 5];
                    return [4 /*yield*/, poll($, "/v1/bindings/".concat(s.agentId, "?wait_ms=25000"))];
                case 1:
                    b = _h.sent();
                    if (!(b.state === 'bound')) return [3 /*break*/, 3];
                    Object.assign(s, { workerId: b.worker_id, groupId: b.group_id, taskId: b.task_id, generation: b.generation, model: (_a = b.model) !== null && _a !== void 0 ? _a : s.model,
                        effort: (_b = b.effort) !== null && _b !== void 0 ? _b : s.effort, profile: (_c = b.profile) !== null && _c !== void 0 ? _c : s.profile, phase: 'STREAMING' });
                    return [4 /*yield*/, saveShadow($, s)];
                case 2:
                    _h.sent();
                    return [3 /*break*/, 4];
                case 3:
                    if (b.state === 'failed') {
                        return [2 /*return*/, "NativePeers refused this worker: ".concat((_e = (_d = b.error) === null || _d === void 0 ? void 0 : _d.code) !== null && _e !== void 0 ? _e : 'SPAWN_FAILED', " ").concat(String((_g = (_f = b.error) === null || _f === void 0 ? void 0 : _f.message) !== null && _g !== void 0 ? _g : '').slice(0, 400))];
                    }
                    _h.label = 4;
                case 4: return [3 /*break*/, 0];
                case 5: return [2 /*return*/, undefined];
            }
        });
    });
}
function forward($, s, input, mode) {
    return __awaiter(this, void 0, void 0, function () {
        var r, error_2, code;
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    input.attempts += 1;
                    _b.label = 1;
                case 1:
                    _b.trys.push([1, 3, , 4]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', "/v1/workers/".concat(s.workerId, "/input"), { client_request_id: (0, inputs_1.requestId)(input.uuid), text: input.text, origin: input.origin, mode: mode, delivery_owner: 'mirror' })];
                case 2:
                    r = _b.sent();
                    Object.assign(input, { forwarded: true, disposition: r.disposition, reason: r.reason, generation: r.generation });
                    return [3 /*break*/, 4];
                case 3:
                    error_2 = _b.sent();
                    code = error_2 instanceof bridge_1.BridgeError ? error_2.code : 'TRANSPORT';
                    if (input.attempts >= 3 || !['BRIDGE_UNREACHABLE', 'BRIDGE_DOWN'].includes(code))
                        Object.assign(input, { forwarded: true, disposition: 'refused', reason: code });
                    return [3 /*break*/, 4];
                case 4: return [4 /*yield*/, saveShadow($, s)];
                case 5:
                    _b.sent();
                    if (input.disposition === 'refused')
                        return [2 /*return*/, "\u21B3 follow-up not delivered: ".concat((_a = input.reason) !== null && _a !== void 0 ? _a : 'refused', "\n")];
                    if (input.disposition === 'resumed')
                        return [2 /*return*/, '↳ resuming the worker with your follow-up\n'];
                    if (input.disposition === 'steered')
                        return [2 /*return*/, '↳ follow-up steered into the running turn\n'];
                    if (input.disposition === 'queued')
                        return [2 /*return*/, input.reason === 'steer-unsupported' ? '↳ queued for the worker (steering unsupported)\n' : '↳ queued for the worker (after its current turn)\n'];
                    return [2 /*return*/, ''];
            }
        });
    });
}
function summary($, s, terminal) {
    return __awaiter(this, void 0, void 0, function () {
        var base, r, _a;
        var _b;
        var _c, _d, _e, _f, _g, _h, _j;
        return __generator(this, function (_k) {
            switch (_k.label) {
                case 0:
                    _b = { provider: s.provider, modelRequested: s.model, effort: s.effort, profile: s.profile, workerId: (_c = s.workerId) !== null && _c !== void 0 ? _c : '?',
                        groupId: s.groupId, taskId: (_d = terminal.task_id) !== null && _d !== void 0 ? _d : s.taskId };
                    return [4 /*yield*/, $.clock.now()];
                case 1:
                    base = (_b.elapsedMs = (_k.sent()) - s.startedAt, _b.resultArtifact = (_e = terminal.data) === null || _e === void 0 ? void 0 : _e.result_artifact_id, _b);
                    _k.label = 2;
                case 2:
                    _k.trys.push([2, 4, , 5]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', "/v1/workers/".concat(s.workerId, "/result"))
                        // A fallback's replacement reports its own provider and requested model.
                    ];
                case 3:
                    r = _k.sent();
                    // A fallback's replacement reports its own provider and requested model.
                    return [2 /*return*/, __assign(__assign({}, base), { provider: (_f = r.provider) !== null && _f !== void 0 ? _f : base.provider, modelRequested: (_g = r.model_requested) !== null && _g !== void 0 ? _g : base.modelRequested, modelObserved: r.model_observed, nativeId: r.native_id, elapsedMs: (_h = r.elapsed_ms) !== null && _h !== void 0 ? _h : base.elapsedMs, resultArtifact: (_j = r.result_artifact_id) !== null && _j !== void 0 ? _j : base.resultArtifact, patchArtifact: r.patch_artifact, changes: r.changes })];
                case 4:
                    _a = _k.sent();
                    return [2 /*return*/, base];
                case 5: return [2 /*return*/];
            }
        });
    });
}
// One model step of a shadow, answered from the worker's activity. Every path
// returns a complete TurnStepResult: a bare return makes the hook absent and
// the engine would send a real request (X10).
function stepShadow($, e, s, o) {
    return __asyncGenerator(this, arguments, function stepShadow_1() {
        var open, usage, say, failure, _i, _a, input, note, moved, ctx, opened, quietSince, lastBeat, _loop_1, state_2;
        var _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t;
        return __generator(this, function (_u) {
            switch (_u.label) {
                case 0:
                    open = (0, mirror_1.openStep)();
                    usage = 0;
                    say = function (text) { var chunk = { kind: 'text', index: open.block++, text: text }; open.answer += text; return chunk; };
                    if (!(s.spawnError || (!s.workerId && s.phase === 'FAULTED'))) return [3 /*break*/, 7];
                    return [4 /*yield*/, __await(say("NativePeers could not start this worker: ".concat((_c = (_b = s.spawnError) !== null && _b !== void 0 ? _b : s.lastFault) !== null && _c !== void 0 ? _c : 'unknown')))];
                case 1: return [4 /*yield*/, _u.sent()];
                case 2:
                    _u.sent();
                    return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'end_turn', usage: null })];
                case 3: return [4 /*yield*/, _u.sent()];
                case 4:
                    _u.sent();
                    s.phase = 'COMPLETED';
                    return [4 /*yield*/, __await(saveShadow($, s))];
                case 5:
                    _u.sent();
                    return [4 /*yield*/, __await(result(e, open.answer, 'end_turn', [], 0, s.provider))];
                case 6: return [2 /*return*/, _u.sent()];
                case 7: return [4 /*yield*/, __await(bind($, s))];
                case 8:
                    failure = _u.sent();
                    if (!failure) return [3 /*break*/, 15];
                    return [4 /*yield*/, __await(say(failure))];
                case 9: return [4 /*yield*/, _u.sent()];
                case 10:
                    _u.sent();
                    return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'end_turn', usage: null })];
                case 11: return [4 /*yield*/, _u.sent()];
                case 12:
                    _u.sent();
                    s.phase = 'COMPLETED';
                    return [4 /*yield*/, __await(saveShadow($, s))];
                case 13:
                    _u.sent();
                    return [4 /*yield*/, __await(result(e, open.answer, 'end_turn', [], 0, s.provider))];
                case 14: return [2 /*return*/, _u.sent()];
                case 15:
                    if (!s.inflight) return [3 /*break*/, 18];
                    return [4 /*yield*/, __await(say("[mirror resumed after reload; up to ".concat(s.inflight.to - s.inflight.from, " activity events may be missing here \u2014 the native history is complete]\n")))];
                case 16: return [4 /*yield*/, _u.sent()];
                case 17:
                    _u.sent();
                    s.inflight = undefined;
                    _u.label = 18;
                case 18:
                    _i = 0, _a = (0, inputs_1.unforwarded)(s.inputs);
                    _u.label = 19;
                case 19:
                    if (!(_i < _a.length)) return [3 /*break*/, 24];
                    input = _a[_i];
                    return [4 /*yield*/, __await(forward($, s, input, o.followupMode === 'steer-if-supported' && o.steer ? 'steer' : 'queue'))];
                case 20:
                    note = _u.sent();
                    if (!note) return [3 /*break*/, 23];
                    return [4 /*yield*/, __await(say(note))];
                case 21: return [4 /*yield*/, _u.sent()];
                case 22:
                    _u.sent();
                    _u.label = 23;
                case 23:
                    _i++;
                    return [3 /*break*/, 19];
                case 24:
                    ctx = { workerId: s.workerId, showReasoning: o.showReasoning, newId: toolId,
                        continuesAfter: function (g) { return (moved && moved.from_generation === g) ||
                            s.inputs.some(function (i) { return i.forwarded && i.generation !== undefined && i.generation > g && !s.terminals.includes(i.generation); }); } };
                    return [4 /*yield*/, __await($.clock.now())];
                case 25:
                    opened = _u.sent();
                    quietSince = opened, lastBeat = opened;
                    _loop_1 = function () {
                        var backfill, batch, events, plan, _v, events_1, ev, _loop_2, _w, _x, f, _y, _z, chunk, shown, _0, _1, _2, _3, _4, _5, t, owner, surfaced, _6, folded, final, text, _7, _8, _9, now, r, input, index, id, _10;
                        var _11;
                        return __generator(this, function (_12) {
                            switch (_12.label) {
                                case 0:
                                    backfill = s.cursor === 0 && s.deliveryOwner === 'native';
                                    return [4 /*yield*/, __await(poll($, "/v1/workers/".concat(s.workerId, "/activity?after=").concat(s.cursor, "&wait_ms=25000&max=256")))];
                                case 1:
                                    batch = _12.sent();
                                    events = Array.isArray(batch.events) ? batch.events : [];
                                    moved = batch.continued_by;
                                    plan = (0, mirror_1.planBatch)(events, open, ctx, s.usageCumulative);
                                    if (!(plan.consumed !== null)) return [3 /*break*/, 4];
                                    for (_v = 0, events_1 = events; _v < events_1.length; _v++) {
                                        ev = events_1[_v];
                                        if (ev.seq > plan.consumed)
                                            break;
                                        if (ev.kind === 'task.started')
                                            Object.assign(s, { generation: ev.generation, taskId: (_d = ev.task_id) !== null && _d !== void 0 ? _d : s.taskId });
                                        if (ev.kind === 'task.terminal' && !s.terminals.includes(ev.generation))
                                            s.terminals.push(ev.generation);
                                        if (ev.kind === 'tool.started')
                                            s.latest = String((_h = (_f = (_e = ev.data) === null || _e === void 0 ? void 0 : _e.title) !== null && _f !== void 0 ? _f : (_g = ev.data) === null || _g === void 0 ? void 0 : _g.kind) !== null && _h !== void 0 ? _h : '').slice(0, 120);
                                    }
                                    // Commit before yielding: a reload resumes from here and marks what may be lost.
                                    s.inflight = { from: s.cursor, to: plan.consumed };
                                    s.cursor = plan.consumed;
                                    s.pendingRefs = __assign(__assign({}, s.pendingRefs), plan.refs);
                                    if (plan.usage.cumulative !== undefined)
                                        s.usageCumulative = plan.usage.cumulative;
                                    _loop_2 = function (f) {
                                        if (!((_j = s.folded) !== null && _j !== void 0 ? _j : []).some(function (x) { return x.generation === f.generation; }))
                                            s.folded = __spreadArray(__spreadArray([], ((_k = s.folded) !== null && _k !== void 0 ? _k : []), true), [{ generation: f.generation, taskId: (_l = f.task_id) !== null && _l !== void 0 ? _l : undefined, text: String((_o = (_m = f.data) === null || _m === void 0 ? void 0 : _m.final_text) !== null && _o !== void 0 ? _o : '').slice(0, 12288) }], false);
                                    };
                                    for (_w = 0, _x = plan.folded; _w < _x.length; _w++) {
                                        f = _x[_w];
                                        _loop_2(f);
                                    }
                                    return [4 /*yield*/, __await(saveShadow($, s))];
                                case 2:
                                    _12.sent();
                                    return [4 /*yield*/, __await($.clock.now())];
                                case 3:
                                    quietSince = _12.sent();
                                    _12.label = 4;
                                case 4:
                                    usage += plan.usage.delta;
                                    _y = 0, _z = plan.chunks;
                                    _12.label = 5;
                                case 5:
                                    if (!(_y < _z.length)) return [3 /*break*/, 9];
                                    chunk = _z[_y];
                                    return [4 /*yield*/, __await(chunk)];
                                case 6: return [4 /*yield*/, _12.sent()];
                                case 7:
                                    _12.sent();
                                    _12.label = 8;
                                case 8:
                                    _y++;
                                    return [3 /*break*/, 5];
                                case 9:
                                    shown = plan.consumed !== null ? events.find(function (ev) { return ev.seq === plan.consumed; }) : undefined;
                                    if (!((shown === null || shown === void 0 ? void 0 : shown.at) && plan.chunks.length && !backfill)) return [3 /*break*/, 11];
                                    _1 = (_0 = $.ui).log;
                                    _2 = "np: display latency ".concat;
                                    _4 = (_3 = Math).round;
                                    return [4 /*yield*/, __await($.clock.now())];
                                case 10:
                                    _1.apply(_0, [_2.apply("np: display latency ", [_4.apply(_3, [(_12.sent()) - Date.parse(shown.at)]), "ms "]).concat(s.provider, " seq ").concat(shown.seq), { to: 'debug' }]);
                                    _12.label = 11;
                                case 11:
                                    if (!(plan.end === 'tool_use')) return [3 /*break*/, 16];
                                    Object.assign(s, { phase: 'TOOL_PENDING', inflight: undefined });
                                    return [4 /*yield*/, __await(saveShadow($, s))];
                                case 12:
                                    _12.sent();
                                    return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'tool_use', usage: null })];
                                case 13: return [4 /*yield*/, _12.sent()];
                                case 14:
                                    _12.sent();
                                    _5 = {};
                                    return [4 /*yield*/, __await(result(e, open.answer, 'tool_use', plan.toolUses, usage, s.provider))];
                                case 15: return [2 /*return*/, (_5.value = _12.sent(), _5)];
                                case 16:
                                    if (!(plan.end === 'terminal' && plan.terminal)) return [3 /*break*/, 29];
                                    t = plan.terminal;
                                    owner = t.generation === s.nativeGeneration ? 'native' : 'mirror';
                                    surfaced = void 0;
                                    _12.label = 17;
                                case 17:
                                    _12.trys.push([17, 19, , 20]);
                                    return [4 /*yield*/, __await((0, bridge_1.request)($, 'POST', '/v1/mirror/surfacing', { worker_id: s.workerId, task_id: t.task_id, generation: t.generation }))];
                                case 18:
                                    surfaced = _12.sent();
                                    return [3 /*break*/, 20];
                                case 19:
                                    _6 = _12.sent();
                                    return [3 /*break*/, 20];
                                case 20:
                                    if (owner === 'mirror') {
                                        // A lapsed lease already handed the route back: NativePeers delivers it,
                                        // so this answer must not claim the delivery and is not acked.
                                        if (surfaced && surfaced.owner === 'mirror' && surfaced.route !== 'MIRROR_SURFACING')
                                            owner = 'native';
                                        else
                                            s.surfaced = { generation: t.generation, taskId: (_p = t.task_id) !== null && _p !== void 0 ? _p : undefined };
                                    }
                                    return [4 /*yield*/, __await(surfaceFolded($, s))];
                                case 21:
                                    folded = _12.sent();
                                    final = String((_r = (_q = t.data) === null || _q === void 0 ? void 0 : _q.final_text) !== null && _r !== void 0 ? _r : '').trim();
                                    _7 = mirror_1.finalAnswer;
                                    _8 = [t];
                                    return [4 /*yield*/, __await(summary($, s, t))];
                                case 22:
                                    text = _7.apply(void 0, _8.concat([_12.sent(), owner, s.terminals, final.length > 0 && open.answer.trim().endsWith(final), folded, surfaced === null || surfaced === void 0 ? void 0 : surfaced.mode]));
                                    return [4 /*yield*/, __await(say((open.answer && !open.answer.endsWith('\n') ? '\n\n' : '') + text))];
                                case 23: return [4 /*yield*/, _12.sent()];
                                case 24:
                                    _12.sent();
                                    Object.assign(s, { phase: 'COMPLETING', inflight: undefined });
                                    return [4 /*yield*/, __await(saveShadow($, s))];
                                case 25:
                                    _12.sent();
                                    return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'end_turn', usage: usage > 0 ? { input_tokens: 0, output_tokens: usage, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: (0, agents_1.shadowModel)(s.provider) } : null })];
                                case 26: return [4 /*yield*/, _12.sent()];
                                case 27:
                                    _12.sent();
                                    _9 = {};
                                    return [4 /*yield*/, __await(result(e, open.answer, 'end_turn', [], usage, s.provider))];
                                case 28: return [2 /*return*/, (_9.value = _12.sent(), _9)];
                                case 29:
                                    if (!((moved === null || moved === void 0 ? void 0 : moved.state) === 'moved' && moved.worker_id && moved.worker_id !== s.workerId && ((_s = plan.consumed) !== null && _s !== void 0 ? _s : s.cursor) >= Number((_t = batch.last_seq) !== null && _t !== void 0 ? _t : 0))) return [3 /*break*/, 33];
                                    if (s.nativeGeneration === moved.from_generation)
                                        s.nativeGeneration = moved.generation;
                                    Object.assign(s, { workerId: moved.worker_id, taskId: moved.task_id, generation: moved.generation, cursor: 0, terminals: [], pendingRefs: {}, usageCumulative: undefined });
                                    ctx.workerId = moved.worker_id;
                                    moved = undefined;
                                    return [4 /*yield*/, __await(saveShadow($, s))];
                                case 30:
                                    _12.sent();
                                    return [4 /*yield*/, __await(say("\u21B3 continuing in replacement worker ".concat(s.workerId, "\n")))];
                                case 31: return [4 /*yield*/, _12.sent()];
                                case 32:
                                    _12.sent();
                                    return [2 /*return*/, "continue"];
                                case 33: return [4 /*yield*/, __await($.clock.now()
                                    // A single step is aborted by the engine after 600 s (gate G8): past eight
                                    // quiet minutes the step ends on a 'waiting' mirror call, which may hold.
                                    )];
                                case 34:
                                    now = _12.sent();
                                    if (!(now - opened > STEP_CAP_MS && open.tools === 0)) return [3 /*break*/, 43];
                                    s.waits += 1;
                                    r = "".concat(s.workerId, ":").concat(s.generation, ":wait").concat(s.waits);
                                    input = { kind: 'wait', title: "".concat(s.provider, " is still working"), detail: '', ref: r };
                                    s.pendingRefs = __assign(__assign({}, s.pendingRefs), (_11 = {}, _11[r] = { item: 'wait' + s.waits, kind: 'wait', startSeq: s.cursor, title: input.title, detail: '', generation: s.generation }, _11));
                                    Object.assign(s, { phase: 'TOOL_PENDING', inflight: undefined });
                                    return [4 /*yield*/, __await(saveShadow($, s))];
                                case 35:
                                    _12.sent();
                                    index = open.block++, id = toolId();
                                    return [4 /*yield*/, __await({ kind: 'tool', index: index, id: id, name: mirror_1.MIRROR_TOOL })];
                                case 36: return [4 /*yield*/, _12.sent()];
                                case 37:
                                    _12.sent();
                                    return [4 /*yield*/, __await({ kind: 'input', index: index, json: JSON.stringify(input) })];
                                case 38: return [4 /*yield*/, _12.sent()];
                                case 39:
                                    _12.sent();
                                    return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'tool_use', usage: null })];
                                case 40: return [4 /*yield*/, _12.sent()];
                                case 41:
                                    _12.sent();
                                    _10 = {};
                                    return [4 /*yield*/, __await(result(e, open.answer, 'tool_use', [{ name: mirror_1.MIRROR_TOOL, input: input }], usage, s.provider))];
                                case 42: return [2 /*return*/, (_10.value = _12.sent(), _10)];
                                case 43:
                                    if (!(o.showReasoning && now - quietSince > QUIET_MS && now - lastBeat > QUIET_MS)) return [3 /*break*/, 46];
                                    lastBeat = now;
                                    return [4 /*yield*/, __await({ kind: 'thinking', index: open.block++, text: "".concat(s.provider, ": still working (").concat(Math.round((now - s.startedAt) / 1000), "s)") })];
                                case 44: return [4 /*yield*/, _12.sent()];
                                case 45:
                                    _12.sent();
                                    _12.label = 46;
                                case 46: return [2 /*return*/];
                            }
                        });
                    };
                    _u.label = 26;
                case 26:
                    if (!true) return [3 /*break*/, 28];
                    return [5 /*yield**/, _loop_1()];
                case 27:
                    state_2 = _u.sent();
                    if (typeof state_2 === "object")
                        return [2 /*return*/, state_2.value];
                    return [3 /*break*/, 26];
                case 28: return [2 /*return*/];
            }
        });
    });
}
// The mirror tool: never executes anything; it waits for the recorded result
// of the worker's own tool call and replays it.
function mirrorCall($, agentId, input, limit, signal) {
    return __awaiter(this, void 0, void 0, function () {
        var s, pending, text, batch, batch, events, after, batch, fresh_2, _i, fresh_1, ev, fresh, _a, _b, _done, rest;
        var _c, _d, _e, _f, _g, _h;
        return __generator(this, function (_j) {
            switch (_j.label) {
                case 0: return [4 /*yield*/, loadShadow($, agentId)];
                case 1:
                    s = _j.sent();
                    pending = (_c = s === null || s === void 0 ? void 0 : s.pendingRefs) === null || _c === void 0 ? void 0 : _c[String((_d = input === null || input === void 0 ? void 0 : input.ref) !== null && _d !== void 0 ? _d : '')];
                    if (!s || !pending || pending.kind !== (input === null || input === void 0 ? void 0 : input.kind) || pending.title !== (input === null || input === void 0 ? void 0 : input.title) || pending.detail !== ((_e = input === null || input === void 0 ? void 0 : input.detail) !== null && _e !== void 0 ? _e : ''))
                        return [2 /*return*/, { deny: 'Internal NativePeers display tool: calls outside a NativePeers shadow are refused.' }];
                    if (!(pending.kind === 'wait')) return [3 /*break*/, 5];
                    _j.label = 2;
                case 2:
                    if (!!signal.aborted) return [3 /*break*/, 4];
                    return [4 /*yield*/, poll($, "/v1/workers/".concat(s.workerId, "/activity?after=").concat(s.cursor, "&wait_ms=20000&max=1"))];
                case 3:
                    batch = _j.sent();
                    if ((_f = batch.events) === null || _f === void 0 ? void 0 : _f.length)
                        return [3 /*break*/, 4];
                    return [3 /*break*/, 2];
                case 4:
                    text = "[".concat(s.provider, "] activity resumed");
                    return [3 /*break*/, 11];
                case 5:
                    if (!pending.immediate) return [3 /*break*/, 7];
                    return [4 /*yield*/, poll($, "/v1/workers/".concat(s.workerId, "/activity?after=").concat(pending.startSeq - 1, "&max=1"))];
                case 6:
                    batch = _j.sent();
                    text = (0, mirror_1.toolResult)(s.provider, pending, (_g = batch.events) !== null && _g !== void 0 ? _g : [], limit).text;
                    return [3 /*break*/, 11];
                case 7:
                    events = [], after = pending.startSeq;
                    _j.label = 8;
                case 8:
                    if (!!signal.aborted) return [3 /*break*/, 10];
                    return [4 /*yield*/, poll($, "/v1/workers/".concat(s.workerId, "/activity?after=").concat(after, "&item=").concat(encodeURIComponent(pending.item), "&wait_ms=20000&max=64"))];
                case 9:
                    batch = _j.sent();
                    fresh_2 = (_h = batch.events) !== null && _h !== void 0 ? _h : [];
                    events = events.concat(fresh_2);
                    for (_i = 0, fresh_1 = fresh_2; _i < fresh_1.length; _i++) {
                        ev = fresh_1[_i];
                        after = Math.max(after, ev.seq);
                    }
                    if (events.some(function (ev) { return ev.kind === 'tool.completed' || (ev.kind === 'task.terminal' && ev.generation === pending.generation); }))
                        return [3 /*break*/, 10];
                    return [3 /*break*/, 8];
                case 10:
                    text = (0, mirror_1.toolResult)(s.provider, pending, events, limit).text;
                    _j.label = 11;
                case 11: return [4 /*yield*/, loadShadow($, agentId)];
                case 12:
                    fresh = _j.sent();
                    if (!fresh) return [3 /*break*/, 14];
                    _a = fresh.pendingRefs, _b = String(input.ref), _done = _a[_b], rest = __rest(_a, [typeof _b === "symbol" ? _b : _b + ""]);
                    fresh.pendingRefs = rest;
                    if (!Object.keys(rest).length)
                        fresh.phase = 'STREAMING';
                    return [4 /*yield*/, saveShadow($, fresh)];
                case 13:
                    _j.sent();
                    _j.label = 14;
                case 14: return [2 /*return*/, { result: signal.aborted ? "[".concat(s.provider, " ").concat(pending.kind, "] interrupted") : text }];
            }
        });
    });
}
// session.append for a shadow: record a follow-up row once, by its uuid.
function captureRow($, agentId, row, uuid) {
    return __awaiter(this, void 0, void 0, function () {
        var s, found;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, loadShadow($, agentId)];
                case 1:
                    s = _a.sent();
                    if (!s)
                        return [2 /*return*/];
                    found = (0, inputs_1.followUp)(row);
                    if (!found)
                        return [2 /*return*/];
                    if (!(!s.briefConsumed && (0, inputs_1.isBrief)(found.text, s.brief))) return [3 /*break*/, 3];
                    s.briefConsumed = true;
                    return [4 /*yield*/, saveShadow($, s)];
                case 2:
                    _a.sent();
                    return [2 /*return*/];
                case 3:
                    if ((0, inputs_1.isBrief)(found.text, s.brief) && found.origin === 'parent-model' && row.door === 'prompt' && !s.inputs.length && s.phase === 'BINDING')
                        return [2 /*return*/];
                    if (s.inputs.some(function (i) { return i.uuid === uuid; }))
                        return [2 /*return*/];
                    s.inputs.push({ uuid: uuid, text: found.text, origin: found.origin, forwarded: false, attempts: 0 });
                    return [4 /*yield*/, saveShadow($, s)];
                case 4:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
// The mirror-owned generations folded into this run surface with its final answer; a handed-back or
// natively owned one is NativePeers' to deliver and is left out.
function surfaceFolded($, s) {
    return __awaiter(this, void 0, void 0, function () {
        var out, _i, _a, f, r, _b;
        var _c, _d;
        return __generator(this, function (_e) {
            switch (_e.label) {
                case 0:
                    out = [];
                    _i = 0, _a = (_c = s.folded) !== null && _c !== void 0 ? _c : [];
                    _e.label = 1;
                case 1:
                    if (!(_i < _a.length)) return [3 /*break*/, 7];
                    f = _a[_i];
                    if (f.generation === s.nativeGeneration)
                        return [3 /*break*/, 6];
                    r = void 0;
                    _e.label = 2;
                case 2:
                    _e.trys.push([2, 4, , 5]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/surfacing', { worker_id: s.workerId, task_id: f.taskId, generation: f.generation })];
                case 3:
                    r = _e.sent();
                    return [3 /*break*/, 5];
                case 4:
                    _b = _e.sent();
                    return [3 /*break*/, 5];
                case 5:
                    if (r && (r.owner !== 'mirror' || r.route !== 'MIRROR_SURFACING'))
                        return [3 /*break*/, 6];
                    s.surfacedFolded = __spreadArray(__spreadArray([], ((_d = s.surfacedFolded) !== null && _d !== void 0 ? _d : []), true), [{ generation: f.generation, taskId: f.taskId }], false);
                    out.push({ generation: f.generation, text: f.text });
                    _e.label = 6;
                case 6:
                    _i++;
                    return [3 /*break*/, 1];
                case 7:
                    s.folded = [];
                    return [2 /*return*/, out];
            }
        });
    });
}
// turn.complete for a shadow: acknowledge a surfaced answer, map a stop to the
// worker, and detect a fallthrough (a real model request for a shadow).
function completeShadow($, agentId, reason, isAborted, answer, usage) {
    return __awaiter(this, void 0, void 0, function () {
        var s, fell, _a, status_1, _b, _c, _i, _d, g, _e;
        var _f, _g, _h;
        return __generator(this, function (_j) {
            switch (_j.label) {
                case 0: return [4 /*yield*/, loadShadow($, agentId)];
                case 1:
                    s = _j.sent();
                    if (!s)
                        return [2 /*return*/];
                    fell = (usage && (Number(usage.input_tokens) > 0 || Number(usage.cache_creation_input_tokens) > 0)) || answer.includes(agents_1.SENTINEL) || reason === 'error';
                    if (!fell) return [3 /*break*/, 6];
                    s.fallthrough = true;
                    return [4 /*yield*/, (0, state_1.setFaults)($, { fallthrough: true, reason: "shadow ".concat(agentId, ": ").concat(reason !== null && reason !== void 0 ? reason : 'model output'), at: Date.now() })];
                case 2:
                    _j.sent();
                    $.ui.toast("np: a shadow made a model request (".concat(agents_1.TAGS[s.provider], "). NativePeers agent types are off until /np doctor passes."));
                    _j.label = 3;
                case 3:
                    _j.trys.push([3, 5, , 6]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/fallthrough', { claude_agent_id: agentId, worker_id: s.workerId, reason: reason !== null && reason !== void 0 ? reason : 'unknown' })];
                case 4:
                    _j.sent();
                    return [3 /*break*/, 6];
                case 5:
                    _a = _j.sent();
                    return [3 /*break*/, 6];
                case 6:
                    if (!isAborted) return [3 /*break*/, 15];
                    return [4 /*yield*/, $.agent.list()];
                case 7:
                    status_1 = (_f = (_j.sent()).find(function (a) { return a.id === agentId; })) === null || _f === void 0 ? void 0 : _f.status;
                    if (!(status_1 === 'killed' && s.workerId)) return [3 /*break*/, 14];
                    _j.label = 8;
                case 8:
                    _j.trys.push([8, 10, , 11]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', "/v1/workers/".concat(s.workerId, "/cancel"), { client_request_id: "np-kill:".concat(agentId, ":").concat(s.generation), reason: 'Stopped from Claude Code' })];
                case 9:
                    _j.sent();
                    return [3 /*break*/, 11];
                case 10:
                    _b = _j.sent();
                    return [3 /*break*/, 11];
                case 11:
                    _j.trys.push([11, 13, , 14]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/ack', { worker_id: s.workerId, task_id: s.taskId, generation: s.generation, outcome: 'killed' })];
                case 12:
                    _j.sent();
                    return [3 /*break*/, 14];
                case 13:
                    _c = _j.sent();
                    return [3 /*break*/, 14];
                case 14:
                    s.phase = 'COMPLETED';
                    return [3 /*break*/, 23];
                case 15:
                    if (!(s.surfaced || ((_g = s.surfacedFolded) === null || _g === void 0 ? void 0 : _g.length))) return [3 /*break*/, 22];
                    _i = 0, _d = __spreadArray(__spreadArray([], ((_h = s.surfacedFolded) !== null && _h !== void 0 ? _h : []), true), (s.surfaced ? [s.surfaced] : []), true);
                    _j.label = 16;
                case 16:
                    if (!(_i < _d.length)) return [3 /*break*/, 21];
                    g = _d[_i];
                    _j.label = 17;
                case 17:
                    _j.trys.push([17, 19, , 20]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/ack', { worker_id: s.workerId, task_id: g.taskId, generation: g.generation })];
                case 18:
                    _j.sent();
                    return [3 /*break*/, 20];
                case 19:
                    _e = _j.sent();
                    return [3 /*break*/, 20];
                case 20:
                    _i++;
                    return [3 /*break*/, 16];
                case 21:
                    s.surfaced = undefined;
                    s.surfacedFolded = undefined;
                    s.phase = 'COMPLETED';
                    return [3 /*break*/, 23];
                case 22:
                    if (s.phase === 'COMPLETING') {
                        s.phase = 'COMPLETED';
                    }
                    _j.label = 23;
                case 23: return [4 /*yield*/, saveShadow($, s)];
                case 24:
                    _j.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function faultShadow($, agentId, kind) {
    return __awaiter(this, void 0, void 0, function () {
        var s, _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0: return [4 /*yield*/, loadShadow($, agentId)];
                case 1:
                    s = _b.sent();
                    if (!s)
                        return [2 /*return*/];
                    Object.assign(s, { phase: 'FAULTED', lastFault: kind });
                    return [4 /*yield*/, saveShadow($, s)];
                case 2:
                    _b.sent();
                    _b.label = 3;
                case 3:
                    _b.trys.push([3, 5, , 6]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/fault', { claude_agent_id: s.agentId, worker_id: s.workerId, kind: kind, message: "turn.step ".concat(kind) })];
                case 4:
                    _b.sent();
                    return [3 /*break*/, 6];
                case 5:
                    _a = _b.sent();
                    return [3 /*break*/, 6];
                case 6: return [2 /*return*/];
            }
        });
    });
}
function isShadowStep($, agentId) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            if (agentId === undefined)
                return [2 /*return*/, undefined];
            return [2 /*return*/, loadShadow($, agentId)];
        });
    });
}
function shadows($) {
    return __awaiter(this, void 0, void 0, function () {
        var out, _i, _a, id, s;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    out = [];
                    _i = 0;
                    return [4 /*yield*/, (0, state_1.getIndex)($)];
                case 1:
                    _a = _b.sent();
                    _b.label = 2;
                case 2:
                    if (!(_i < _a.length)) return [3 /*break*/, 5];
                    id = _a[_i];
                    return [4 /*yield*/, loadShadow($, id)];
                case 3:
                    s = _b.sent();
                    if (s)
                        out.push(s);
                    _b.label = 4;
                case 4:
                    _i++;
                    return [3 /*break*/, 2];
                case 5: return [2 /*return*/, out];
            }
        });
    });
}
