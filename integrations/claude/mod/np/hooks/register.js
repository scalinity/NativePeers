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
var __asyncValues = (this && this.__asyncValues) || function (o) {
    if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
    var m = o[Symbol.asyncIterator], i;
    return m ? m.call(o) : (o = typeof __values === "function" ? __values(o) : o[Symbol.iterator](), i = {}, verb("next"), verb("throw"), verb("return"), i[Symbol.asyncIterator] = function () { return this; }, i);
    function verb(n) { i[n] = o[n] && function (v) { return new Promise(function (resolve, reject) { v = o[n](v), settle(resolve, reject, v.done, v.value); }); }; }
    function settle(resolve, reject, d, v) { Promise.resolve(v).then(function(v) { resolve({ value: v, done: d }); }, reject); }
};
var __await = (this && this.__await) || function (v) { return this instanceof __await ? (this.v = v, this) : new __await(v); }
var __asyncDelegator = (this && this.__asyncDelegator) || function (o) {
    var i, p;
    return i = {}, verb("next"), verb("throw", function (e) { throw e; }), verb("return"), i[Symbol.iterator] = function () { return this; }, i;
    function verb(n, f) { i[n] = o[n] ? function (v) { return (p = !p) ? { value: __await(o[n](v)), done: false } : f ? f(v) : v; } : f; }
};
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
var __spreadArray = (this && this.__spreadArray) || function (to, from, pack) {
    if (pack || arguments.length === 2) for (var i = 0, l = from.length, ar; i < l; i++) {
        if (ar || !(i in from)) {
            if (!ar) ar = Array.prototype.slice.call(from, 0, i);
            ar[i] = from[i];
        }
    }
    return to.concat(ar || Array.prototype.slice.call(from));
};
var __values = (this && this.__values) || function(o) {
    var s = typeof Symbol === "function" && Symbol.iterator, m = s && o[s], i = 0;
    if (m) return m.call(o);
    if (o && typeof o.length === "number") return {
        next: function () {
            if (o && i >= o.length) o = void 0;
            return { value: o && o[i++], done: !o };
        }
    };
    throw new TypeError(s ? "Object is not iterable." : "Symbol.iterator is not defined.");
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.register = void 0;
var agents_1 = require("./agents");
var bridge_1 = require("./bridge");
var mirror_1 = require("./mirror");
var shadow_1 = require("./shadow");
var state_1 = require("./state");
var ui_1 = require("./ui");
var MIN_ENGINE = [2, 1, 309];
var CATALOG = 'mcp__np__catalog';
var COMMAND = { name: 'np', description: 'NativePeers lineage: status, pane, open, stop, interrupt, attach, reattach, doctor, resume-cmd', argumentHint: '[pane|open N|stop N|stop all|interrupt N|attach N|all|reattach|doctor|resume-cmd N]', immediate: true };
var MODEL_PREFIX = 'np-mirror-';
var SECTION = [
    'NativePeers agent types (np:codex, np:claude, np:antigravity, np:grok) delegate to independent native CLI workers whose live transcripts appear in the tasks list.',
    'Start the Agent prompt with an optional header: ```np {"model":"...","effort":"...","profile":"analysis|workspace","title":"..."}``` then a newline and the brief.',
    'Call mcp__np__catalog once before choosing exact models and efforts; never guess a model id.',
    'Briefs must be self-contained: the worker sees only the brief, not this conversation.',
    'Workers run in the background: for fan-out, issue the Agent calls together, end your turn, and let the notifications arrive; do not poll.',
    'Follow up with SendMessage to the agent; results carry NativePeers ids for `npeer result` and `npeer artifact-read`.',
].join('\n');
var activated = false;
var inert = false;
var rebind = false;
var place;
var timers = false;
var offered = new Set();
function atLeast(version, minimum) {
    var _a, _b;
    var parts = version.split(/[.+-]/).map(Number);
    for (var n = 0; n < minimum.length; n++) {
        var have = (_a = parts[n]) !== null && _a !== void 0 ? _a : 0, need = (_b = minimum[n]) !== null && _b !== void 0 ? _b : 0;
        if (have > need)
            return true;
        if (have < need)
            return false;
    }
    return true;
}
function workerMode($) {
    return __awaiter(this, void 0, void 0, function () {
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    _a = Boolean;
                    return [4 /*yield*/, $.env.get('NPEER_BOOTSTRAP')];
                case 1: return [2 /*return*/, _a.apply(void 0, [_b.sent()])];
            }
        });
    });
}
function activate($, cwd, o) {
    return __awaiter(this, void 0, void 0, function () {
        var version, launched, _a, _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    if (activated || inert)
                        return [2 /*return*/];
                    activated = true;
                    return [4 /*yield*/, $.session.version()];
                case 1:
                    version = (_c.sent()).version;
                    if (!(!atLeast(version, MIN_ENGINE) || !version.startsWith('2.'))) return [3 /*break*/, 3];
                    return [4 /*yield*/, (0, state_1.setBridge)($, { state: 'degraded', reason: "Claude Code ".concat(version, " is outside the verified range (minimum 2.1.309)"), since: 0 })];
                case 2:
                    _c.sent();
                    return [2 /*return*/];
                case 3:
                    place = { cwd: cwd, npeer: o.npeerPath || '/Users/danny/.local/bin/npeer' };
                    _a = bridge_1.launch;
                    _b = [$];
                    return [4 /*yield*/, $.session.id()];
                case 4: return [4 /*yield*/, _a.apply(void 0, _b.concat([_c.sent(), cwd, place.npeer]))];
                case 5:
                    launched = _c.sent();
                    if ('reason' in launched) {
                        $.ui.toast("np: NativePeers bridge unavailable \u2014 ".concat(launched.reason, "; /np doctor"));
                        return [2 /*return*/];
                    }
                    return [4 /*yield*/, ready($, launched.hello)];
                case 6:
                    _c.sent();
                    void (0, bridge_1.followLineage)($);
                    return [4 /*yield*/, (0, shadow_1.pruneStore)($)];
                case 7:
                    _c.sent();
                    return [2 /*return*/];
            }
        });
    });
}
// Runs on every bridge start: the first registers the tools and timers, and each
// registers the agent types its bridge newly lists, so a bridge that was down when
// the session began, or one that now lists a renewed provider, offers them.
function ready($, hello) {
    return __awaiter(this, void 0, void 0, function () {
        var usable, _i, PROVIDERS_1, provider;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    usable = Array.isArray(hello.providers) ? hello.providers : __spreadArray([], agents_1.PROVIDERS, true);
                    _i = 0, PROVIDERS_1 = agents_1.PROVIDERS;
                    _a.label = 1;
                case 1:
                    if (!(_i < PROVIDERS_1.length)) return [3 /*break*/, 4];
                    provider = PROVIDERS_1[_i];
                    if (!(usable.includes(provider) && !offered.has(provider))) return [3 /*break*/, 3];
                    return [4 /*yield*/, $.agent.register((0, agents_1.agentSpec)(provider))];
                case 2:
                    _a.sent();
                    offered.add(provider);
                    _a.label = 3;
                case 3:
                    _i++;
                    return [3 /*break*/, 1];
                case 4:
                    if (timers)
                        return [2 /*return*/];
                    timers = true;
                    return [4 /*yield*/, $.tool.register({ name: 'mirror', description: 'Internal: renders a NativePeers worker\'s activity. Calls outside a NativePeers shadow are refused.', isDeferred: true,
                            inputSchema: { type: 'object', properties: { kind: { type: 'string' }, title: { type: 'string' }, detail: { type: 'string' }, ref: { type: 'string' } }, required: ['kind', 'title', 'ref'] } })];
                case 5:
                    _a.sent();
                    return [4 /*yield*/, $.tool.register({ name: 'catalog', description: 'Read-only NativePeers catalog: providers, exact model ids with their efforts, the permission profiles this session may use, and a quota summary. Call once before choosing np:* models.', isDeferred: true,
                            inputSchema: { type: 'object', properties: { provider: { type: 'string', enum: __spreadArray([], agents_1.PROVIDERS, true) } } } })];
                case 6:
                    _a.sent();
                    $.clock.every(15000, function () { return (0, bridge_1.heartbeat)($); });
                    $.clock.every(5000, function () { return (0, ui_1.statusLine)($); });
                    return [2 /*return*/];
            }
        });
    });
}
function pathA($, e, next, o) {
    return __awaiter(this, void 0, void 0, function () {
        var r_1, provider, parsed, title, worker, check, error_1, model, effort, r, session, s, _a, _b, error_2;
        var _c, _d, _e;
        return __generator(this, function (_f) {
            switch (_f.label) {
                case 0:
                    if (!e.resume) return [3 /*break*/, 4];
                    return [4 /*yield*/, next(e)];
                case 1:
                    r_1 = _f.sent();
                    if (!r_1.agentId) return [3 /*break*/, 3];
                    return [4 /*yield*/, (0, shadow_1.loadShadow)($, r_1.agentId)];
                case 2:
                    _f.sent();
                    _f.label = 3;
                case 3: return [2 /*return*/, r_1];
                case 4:
                    if (e.isTeammate || e.workflow)
                        return [2 /*return*/, { deny: 'np agents are not available in teams or workflows (v1)' }];
                    if (e.parentAgentId && !o.allowFromClaudeSubagents)
                        return [2 /*return*/, { deny: 'np agents can be started only from the main conversation' }];
                    provider = String(e.subagentType).slice(3);
                    if (!(0, agents_1.isProvider)(provider))
                        return [2 /*return*/, { deny: "unknown NativePeers provider \"".concat(provider, "\"") }];
                    return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 5:
                    if ((_f.sent()).state !== 'ready')
                        return [2 /*return*/, { deny: 'NativePeers bridge unavailable — run /np doctor' }];
                    return [4 /*yield*/, (0, state_1.getFaults)($)];
                case 6:
                    if ((_f.sent()).fallthrough)
                        return [2 /*return*/, { deny: 'NativePeers agent types are off after a detected fallthrough — run /np doctor' }];
                    parsed = (0, agents_1.parseHeader)(String((_c = e.prompt) !== null && _c !== void 0 ? _c : ''));
                    if ('error' in parsed)
                        return [2 /*return*/, { deny: "".concat(parsed.error, "; ").concat(CATALOG, " lists providers, models and efforts") }];
                    title = (0, agents_1.titleFrom)(parsed.header, e.description, parsed.brief);
                    worker = (0, agents_1.workerSpec)(provider, parsed.header, parsed.brief, title, e.cwd);
                    if (!rebind) return [3 /*break*/, 8];
                    return [4 /*yield*/, rebindSession($)];
                case 7:
                    _f.sent();
                    _f.label = 8;
                case 8:
                    _f.trys.push([8, 10, , 11]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/spawn', { check_only: true, worker: worker })];
                case 9:
                    check = _f.sent();
                    return [3 /*break*/, 11];
                case 10:
                    error_1 = _f.sent();
                    return [2 /*return*/, { deny: "".concat(error_1 instanceof bridge_1.BridgeError ? error_1.code : 'BRIDGE_ERROR', ": ").concat(String(error_1.message).slice(0, 400), " (").concat(CATALOG, " lists what this session may use)") }];
                case 11:
                    model = (_d = parsed.header.model) !== null && _d !== void 0 ? _d : check === null || check === void 0 ? void 0 : check.model, effort = (_e = parsed.header.effort) !== null && _e !== void 0 ? _e : check === null || check === void 0 ? void 0 : check.effort;
                    return [4 /*yield*/, next(__assign(__assign({}, e), { prompt: parsed.brief, description: (0, agents_1.describe)(provider, model, effort, title), model: (0, agents_1.shadowModel)(provider) }))];
                case 12:
                    r = _f.sent();
                    if (r.deny || !r.agentId)
                        return [2 /*return*/, r];
                    return [4 /*yield*/, $.session.id()];
                case 13:
                    session = _f.sent();
                    _a = shadow_1.newShadow;
                    _b = [r.agentId, provider, title, session];
                    return [4 /*yield*/, $.clock.now()];
                case 14:
                    s = _a.apply(void 0, _b.concat([_f.sent(), { model: model, effort: effort, profile: worker.permission_profile, brief: parsed.brief, deliveryOwner: 'mirror' }]));
                    return [4 /*yield*/, (0, shadow_1.saveShadow)($, s)];
                case 15:
                    _f.sent();
                    _f.label = 16;
                case 16:
                    _f.trys.push([16, 18, , 20]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/spawn', { client_request_id: "np:".concat(session, ":").concat(r.agentId), claude_agent_id: r.agentId, worker: worker, mirror: { owner: 'claude-mod', claude_session: session, claude_agent_id: r.agentId } })];
                case 17:
                    _f.sent();
                    return [3 /*break*/, 20];
                case 18:
                    error_2 = _f.sent();
                    s.spawnError = "".concat(error_2 instanceof bridge_1.BridgeError ? error_2.code : 'BRIDGE_ERROR', ": ").concat(String(error_2.message).slice(0, 400));
                    return [4 /*yield*/, (0, shadow_1.saveShadow)($, s)];
                case 19:
                    _f.sent();
                    return [3 /*break*/, 20];
                case 20: return [2 /*return*/, r];
            }
        });
    });
}
// Starts the bridge again after it exited; leases it held stay valid for their
// TTL, so a restarted bridge for the same session picks them up.
function ensureBridge($) {
    return __awaiter(this, void 0, void 0, function () {
        var launched, _a, _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    if ((0, bridge_1.connected)() || !place)
                        return [2 /*return*/, ''];
                    _a = bridge_1.launch;
                    _b = [$];
                    return [4 /*yield*/, $.session.id()];
                case 1: return [4 /*yield*/, _a.apply(void 0, _b.concat([_c.sent(), place.cwd, place.npeer]))];
                case 2:
                    launched = _c.sent();
                    if ('reason' in launched)
                        return [2 /*return*/, "bridge restart failed: ".concat(launched.reason)];
                    return [4 /*yield*/, ready($, launched.hello)];
                case 3:
                    _c.sent();
                    void (0, bridge_1.followLineage)($);
                    return [2 /*return*/, 'bridge restarted'];
            }
        });
    });
}
function rebindSession($) {
    return __awaiter(this, void 0, void 0, function () {
        var _a, _b, _c;
        var _d;
        return __generator(this, function (_e) {
            switch (_e.label) {
                case 0:
                    _e.trys.push([0, 3, , 4]);
                    _a = bridge_1.request;
                    _b = [$, 'POST', '/v1/rebind'];
                    _d = {};
                    return [4 /*yield*/, $.session.id()];
                case 1: return [4 /*yield*/, _a.apply(void 0, _b.concat([(_d.claude_session = _e.sent(), _d)]))];
                case 2:
                    _e.sent();
                    rebind = false;
                    return [3 /*break*/, 4];
                case 3:
                    _c = _e.sent();
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    });
}
// A shadow for an existing worker (Path B adoption, Path C attach and
// re-attach). Runs only inside a hook frame: a spawn from a timer or a press
// closure skips every hook of this plugin (gate G5).
// A worker whose shadow still runs here keeps it: a second shadow would mirror it twice and hand its delivery back.
function mirroredHere($, w) {
    return __awaiter(this, void 0, void 0, function () {
        var s;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    if (!w.agentId)
                        return [2 /*return*/, false];
                    return [4 /*yield*/, (0, shadow_1.loadShadow)($, w.agentId)];
                case 1:
                    s = _a.sent();
                    if (!s || ['COMPLETED', 'FAULTED', 'DETACHED'].includes(s.phase))
                        return [2 /*return*/, false];
                    return [4 /*yield*/, $.agent.list()];
                case 2: return [2 /*return*/, (_a.sent()).some(function (a) { return a.id === w.agentId && ['pending', 'running', 'waiting'].includes(a.status); })];
            }
        });
    });
}
function attachWorker($, w, origin, backfill) {
    return __awaiter(this, void 0, void 0, function () {
        var error_3, brief, r, session, s, _a, _b, _c, error_4;
        var _d, _e, _f;
        return __generator(this, function (_g) {
            switch (_g.label) {
                case 0:
                    _g.trys.push([0, 2, , 3]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/attach', { worker_id: w.workerId, check_only: true })];
                case 1:
                    _g.sent();
                    return [3 /*break*/, 3];
                case 2:
                    error_3 = _g.sent();
                    return [2 /*return*/, "".concat(w.title, ": ").concat(error_3 instanceof bridge_1.BridgeError ? error_3.code : 'BRIDGE_ERROR')];
                case 3:
                    brief = (0, agents_1.adoptionBrief)(w.title, w.provider, w.modelRequested, w.effort, w.groupId, (_d = w.latest) !== null && _d !== void 0 ? _d : '');
                    return [4 /*yield*/, $.agent.spawn({ subagentType: 'np:' + w.provider, prompt: brief, description: (0, agents_1.describe)(w.provider, w.modelRequested, w.effort, w.title) })];
                case 4:
                    r = _g.sent();
                    if (!r.agentId)
                        return [2 /*return*/, "".concat(w.title, ": ").concat((_e = r.deny) !== null && _e !== void 0 ? _e : 'not started')];
                    return [4 /*yield*/, $.session.id()];
                case 5:
                    session = _g.sent();
                    _a = shadow_1.newShadow;
                    _b = [r.agentId, w.provider, w.title, session];
                    if (!((_f = w.startedAt) !== null && _f !== void 0)) return [3 /*break*/, 6];
                    _c = _f;
                    return [3 /*break*/, 8];
                case 6: return [4 /*yield*/, $.clock.now()];
                case 7:
                    _c = (_g.sent());
                    _g.label = 8;
                case 8:
                    s = _a.apply(void 0, _b.concat([_c, { workerId: w.workerId, groupId: w.groupId, taskId: w.taskId,
                            generation: w.generation, model: w.modelRequested, effort: w.effort, profile: w.profile, brief: brief, deliveryOwner: 'native', nativeGeneration: w.generation, phase: 'STREAMING', cursor: backfill ? 0 : 0 }]));
                    return [4 /*yield*/, (0, shadow_1.saveShadow)($, s)];
                case 9:
                    _g.sent();
                    _g.label = 10;
                case 10:
                    _g.trys.push([10, 12, , 14]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', '/v1/mirror/attach', { client_request_id: "np-attach:".concat(session, ":").concat(w.workerId, ":").concat(r.agentId), worker_id: w.workerId, claude_agent_id: r.agentId, display_only: true, origin: origin })];
                case 11:
                    _g.sent();
                    return [3 /*break*/, 14];
                case 12:
                    error_4 = _g.sent();
                    s.spawnError = "".concat(error_4 instanceof bridge_1.BridgeError ? error_4.code : 'BRIDGE_ERROR', ": ").concat(String(error_4.message).slice(0, 300));
                    return [4 /*yield*/, (0, shadow_1.saveShadow)($, s)];
                case 13:
                    _g.sent();
                    return [3 /*break*/, 14];
                case 14: return [2 /*return*/, "".concat(w.title, ": attached")];
            }
        });
    });
}
function adopt($, since, o) {
    return __awaiter(this, void 0, void 0, function () {
        var data, _a, workers, _i, workers_1, w;
        var _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    if (o.adoption === 'off')
                        return [2 /*return*/];
                    _c.label = 1;
                case 1:
                    _c.trys.push([1, 3, , 4]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', '/v1/lineage?wait_ms=0&fresh=1')];
                case 2:
                    data = _c.sent();
                    return [3 /*break*/, 4];
                case 3:
                    _a = _c.sent();
                    return [2 /*return*/];
                case 4:
                    workers = Array.isArray(data.workers) ? data.workers : [];
                    return [4 /*yield*/, (0, state_1.setLineage)($, workers)];
                case 5:
                    _c.sent();
                    if (o.adoption === 'ask')
                        return [2 /*return*/];
                    _i = 0, workers_1 = workers;
                    _c.label = 6;
                case 6:
                    if (!(_i < workers_1.length)) return [3 /*break*/, 9];
                    w = workers_1[_i];
                    if (!(!w.agentId && !w.mirrorOwner && !w.parentWorkerId && ((_b = w.startedAt) !== null && _b !== void 0 ? _b : 0) >= since - 2000)) return [3 /*break*/, 8];
                    return [4 /*yield*/, attachWorker($, w, 'adoption', true)];
                case 7:
                    _c.sent();
                    _c.label = 8;
                case 8:
                    _i++;
                    return [3 /*break*/, 6];
                case 9: return [2 /*return*/];
            }
        });
    });
}
function observeSpawn($, e, next, o) {
    return __awaiter(this, void 0, void 0, function () {
        var since, r, isBash;
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0: return [4 /*yield*/, $.clock.now()];
                case 1:
                    since = _b.sent();
                    return [4 /*yield*/, next(e)];
                case 2:
                    r = _b.sent();
                    isBash = e.tool === 'Bash';
                    if (isBash && (!/\bnpeer\s+(spawn|resume-worker)\b/.test(String((_a = e.command) !== null && _a !== void 0 ? _a : '')) || /--check\b/.test(String(e.command))))
                        return [2 /*return*/, r];
                    return [4 /*yield*/, adopt($, since, o)];
                case 3:
                    _b.sent();
                    return [2 /*return*/, r];
            }
        });
    });
}
function command($, args, o, surface) {
    return __awaiter(this, void 0, void 0, function () {
        var _a, _b, verb, _c, arg, workers, pick, restarted, _d, w, denied, _e, w, s, _f, target, restarted, active, chosen, out, _i, chosen_1, w, _g, _h, _j;
        var _k, _l, _m;
        return __generator(this, function (_o) {
            switch (_o.label) {
                case 0:
                    _a = args.trim().split(/\s+/), _b = _a[0], verb = _b === void 0 ? '' : _b, _c = _a[1], arg = _c === void 0 ? '' : _c;
                    return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 1:
                    workers = _o.sent();
                    pick = function (n) { return workers[Number(n) - 1]; };
                    if (!!verb) return [3 /*break*/, 3];
                    _k = {};
                    return [4 /*yield*/, (0, ui_1.statusText)($)];
                case 2: return [2 /*return*/, (_k.text = _o.sent(), _k)];
                case 3:
                    if (!(verb === 'pane')) return [3 /*break*/, 5];
                    return [4 /*yield*/, (0, ui_1.openPane)($)];
                case 4:
                    _o.sent();
                    return [2 /*return*/, { text: 'lineage pane opened' }];
                case 5:
                    if (!(verb === 'doctor')) return [3 /*break*/, 8];
                    return [4 /*yield*/, ensureBridge($)];
                case 6:
                    restarted = _o.sent();
                    _l = {};
                    _d = (restarted ? restarted + '\n' : '');
                    return [4 /*yield*/, doctor($, o)];
                case 7: return [2 /*return*/, (_l.text = _d + (_o.sent()), _l)];
                case 8:
                    if (!(verb === 'open')) return [3 /*break*/, 10];
                    w = pick(arg);
                    if (!(w === null || w === void 0 ? void 0 : w.agentId))
                        return [2 /*return*/, { text: 'no mirrored worker ' + arg }];
                    return [4 /*yield*/, (0, ui_1.openView)($, w.agentId, true)];
                case 9:
                    denied = _o.sent();
                    return [2 /*return*/, { text: !denied ? "viewing ".concat(w.title) : "".concat(w.title, " cannot be opened: ").concat(denied) +
                                (['completed', 'failed', 'canceled', 'interrupted', 'timed_out'].includes(w.status) ? ". Claude Code drops a finished agent about 30 s after it ends; /np pane shows it, and the parent can still follow up with SendMessage" : '') }];
                case 10:
                    if (!(verb === 'stop' && arg === 'all')) return [3 /*break*/, 12];
                    _m = {};
                    _e = "stopping ".concat;
                    return [4 /*yield*/, (0, ui_1.stopAll)($)];
                case 11: return [2 /*return*/, (_m.text = _e.apply("stopping ", [_o.sent(), " group(s)"]), _m)];
                case 12:
                    if (!(verb === 'stop' || verb === 'interrupt' || verb === 'resume-cmd')) return [3 /*break*/, 22];
                    w = pick(arg);
                    if (!(w === null || w === void 0 ? void 0 : w.agentId)) return [3 /*break*/, 14];
                    return [4 /*yield*/, (0, shadow_1.loadShadow)($, w.agentId)];
                case 13:
                    _f = _o.sent();
                    return [3 /*break*/, 15];
                case 14:
                    _f = undefined;
                    _o.label = 15;
                case 15:
                    s = _f;
                    if (!w)
                        return [2 /*return*/, { text: 'no worker ' + arg }];
                    target = s !== null && s !== void 0 ? s : (0, shadow_1.newShadow)('', w.provider, w.title, '', 0, { workerId: w.workerId, generation: w.generation });
                    if (!(verb === 'stop')) return [3 /*break*/, 17];
                    return [4 /*yield*/, (0, ui_1.stopWorker)($, target)];
                case 16:
                    _o.sent();
                    return [3 /*break*/, 21];
                case 17:
                    if (!(verb === 'interrupt')) return [3 /*break*/, 19];
                    return [4 /*yield*/, (0, ui_1.interruptWorker)($, target)];
                case 18:
                    _o.sent();
                    return [3 /*break*/, 21];
                case 19: return [4 /*yield*/, (0, ui_1.copyResume)($, target, surface)];
                case 20:
                    _o.sent();
                    _o.label = 21;
                case 21: return [2 /*return*/, { text: "".concat(verb, " ").concat(w.title) }];
                case 22:
                    if (!(verb === 'attach' || verb === 'reattach')) return [3 /*break*/, 31];
                    return [4 /*yield*/, ensureBridge($)];
                case 23:
                    restarted = _o.sent();
                    if (restarted.startsWith('bridge restart failed'))
                        return [2 /*return*/, { text: restarted }];
                    active = workers.filter(function (w) { return ['queued', 'starting', 'running', 'waiting'].includes(w.status) && w.mirrorOwner !== 'other'; });
                    chosen = verb === 'reattach' ? active : arg === 'all' ? active.filter(function (w) { return !w.agentId; }) : [pick(arg)].filter(Boolean);
                    out = [];
                    _i = 0, chosen_1 = chosen;
                    _o.label = 24;
                case 24:
                    if (!(_i < chosen_1.length)) return [3 /*break*/, 30];
                    w = chosen_1[_i];
                    _h = (_g = out).push;
                    return [4 /*yield*/, mirroredHere($, w)];
                case 25:
                    if (!(_o.sent())) return [3 /*break*/, 26];
                    _j = "".concat(w.title, ": already mirrored");
                    return [3 /*break*/, 28];
                case 26: return [4 /*yield*/, attachWorker($, w, verb, true)];
                case 27:
                    _j = _o.sent();
                    _o.label = 28;
                case 28:
                    _h.apply(_g, [_j]);
                    _o.label = 29;
                case 29:
                    _i++;
                    return [3 /*break*/, 24];
                case 30: return [2 /*return*/, { text: out.length ? out.join('; ') : 'nothing to attach' }];
                case 31: return [2 /*return*/, { text: 'unknown subcommand. Try /np, /np pane, /np open N, /np stop N|all, /np interrupt N, /np attach N|all, /np reattach, /np doctor, /np resume-cmd N' }];
            }
        });
    });
}
function doctor($, o) {
    return __awaiter(this, void 0, void 0, function () {
        var version, bridge, faults, lines, hello, error_5;
        var _a, _b, _c, _d, _e;
        return __generator(this, function (_f) {
            switch (_f.label) {
                case 0: return [4 /*yield*/, $.session.version()];
                case 1:
                    version = _f.sent();
                    return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 2:
                    bridge = _f.sent();
                    return [4 /*yield*/, (0, state_1.getFaults)($)];
                case 3:
                    faults = _f.sent();
                    lines = ["engine: Claude Code ".concat(version.version, " (minimum ").concat(MIN_ENGINE.join('.'), ")"), "npeer: ".concat(o.npeerPath || '/Users/danny/.local/bin/npeer'), "bridge: ".concat(bridge.state).concat(bridge.reason ? ' — ' + bridge.reason : '')];
                    if (!(bridge.state === 'ready')) return [3 /*break*/, 10];
                    _f.label = 4;
                case 4:
                    _f.trys.push([4, 8, , 9]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', '/v1/hello')];
                case 5:
                    hello = _f.sent();
                    lines.push("peer: ".concat(hello.peer, " \u00B7 protocol ").concat(hello.protocol, " \u00B7 providers ").concat(((_a = hello.providers) !== null && _a !== void 0 ? _a : []).join(', ')), "activity schema: ".concat((_c = (_b = hello.capabilities) === null || _b === void 0 ? void 0 : _b.activity_schema) !== null && _c !== void 0 ? _c : '?'));
                    if (!faults.fallthrough) return [3 /*break*/, 7];
                    return [4 /*yield*/, (0, state_1.setFaults)($, { fallthrough: false })];
                case 6:
                    _f.sent();
                    lines.push("fallthrough: cleared (was: ".concat((_d = faults.reason) !== null && _d !== void 0 ? _d : 'unknown', "); np agent types are offered again"));
                    _f.label = 7;
                case 7: return [3 /*break*/, 9];
                case 8:
                    error_5 = _f.sent();
                    lines.push('hello failed: ' + String(error_5).slice(0, 200));
                    return [3 /*break*/, 9];
                case 9: return [3 /*break*/, 11];
                case 10:
                    if (faults.fallthrough)
                        lines.push("fallthrough: ".concat((_e = faults.reason) !== null && _e !== void 0 ? _e : 'detected', "; stays off until the bridge is ready"));
                    _f.label = 11;
                case 11: return [2 /*return*/, lines.join('\n')];
            }
        });
    });
}
var register = function (on, options) {
    var o = options;
    on('session.start', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r, _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _b.sent();
                    return [4 /*yield*/, workerMode($)];
                case 2:
                    if (_b.sent()) {
                        inert = true;
                        return [2 /*return*/, r];
                    }
                    // The desktop composer accepts the commands a session has at start; activation there waits for its surface.
                    return [4 /*yield*/, $.command.register(COMMAND)];
                case 3:
                    // The desktop composer accepts the commands a session has at start; activation there waits for its surface.
                    _b.sent();
                    _a = e.isInteractive;
                    if (_a) return [3 /*break*/, 5];
                    return [4 /*yield*/, $.env.get('NP_MOD_HEADLESS')];
                case 4:
                    _a = (_b.sent()) === '1';
                    _b.label = 5;
                case 5:
                    if (!_a) return [3 /*break*/, 7];
                    return [4 /*yield*/, activate($, e.cwd, o)];
                case 6:
                    _b.sent();
                    _b.label = 7;
                case 7: return [2 /*return*/, r];
            }
        });
    }); });
    // A desktop-hosted session can start with no surface and attach one later.
    on('session.attach', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r, _a, _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _c.sent();
                    if (!(!inert && (e.surface === 'desktop' || e.surface === 'vscode'))) return [3 /*break*/, 4];
                    _a = activate;
                    _b = [$];
                    return [4 /*yield*/, $.session.cwd()];
                case 2: return [4 /*yield*/, _a.apply(void 0, _b.concat([_c.sent(), o]))];
                case 3:
                    _c.sent();
                    _c.label = 4;
                case 4: return [2 /*return*/, r];
            }
        });
    }); });
    on('session.end', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _a.sent();
                    if (!(e.reason === 'clear')) return [3 /*break*/, 2];
                    rebind = true;
                    return [3 /*break*/, 4];
                case 2: return [4 /*yield*/, (0, bridge_1.shutdown)($)];
                case 3:
                    _a.sent();
                    _a.label = 4;
                case 4: return [2 /*return*/, r];
            }
        });
    }); });
    on('agent.offer', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var _a;
        var _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    if (!String(e.agent).startsWith('np:'))
                        return [2 /*return*/, next(e)];
                    _b = {};
                    return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 1:
                    _a = (_c.sent()).state === 'ready';
                    if (!_a) return [3 /*break*/, 3];
                    return [4 /*yield*/, (0, state_1.getFaults)($)];
                case 2:
                    _a = !(_c.sent()).fallthrough;
                    _c.label = 3;
                case 3: return [2 /*return*/, (_b.isOffered = _a, _b)];
            }
        });
    }); }).catch(function ($, e, next) { return String(e.agent).startsWith('np:') ? { isOffered: false } : next(e); });
    // Path A is the engine's own Agent call. A spawn this mod raised itself
    // (adoption, attach) passes through: rewriting it would detach it from the
    // frame that raised it.
    on('agent.spawn', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { var _a; return __generator(this, function (_b) {
        return [2 /*return*/, String(e.subagentType).startsWith('np:') && ((_a = next.origin) === null || _a === void 0 ? void 0 : _a.plugin) !== 'np' ? pathA($, e, next, o) : next(e)];
    }); }); })
        .catch(function ($, e, next) { return next.called ? next(e) : { deny: 'NativePeers spawn failed; run /np doctor' }; });
    on('prompt.compose', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _a.sent();
                    return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 2:
                    if ((_a.sent()).state !== 'ready')
                        return [2 /*return*/, r];
                    return [2 /*return*/, __assign(__assign({}, r), { sections: __spreadArray(__spreadArray([], r.sections, true), [{ id: 'np-lineage', text: SECTION, scope: 'session' }], false) })];
            }
        });
    }); });
    on('turn.step', function npStep($, e, next) {
        return __asyncGenerator(this, arguments, function npStep_1() {
            var s, n, text, steer, _a, r;
            var _b;
            return __generator(this, function (_c) {
                switch (_c.label) {
                    case 0:
                        if (!(e.agentId === undefined || inert)) return [3 /*break*/, 4];
                        return [5 /*yield**/, __values(__asyncDelegator(__asyncValues(next(e))))];
                    case 1: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 2: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 3: return [2 /*return*/, _c.sent()];
                    case 4: return [4 /*yield*/, __await((0, shadow_1.loadShadow)($, e.agentId)
                        // A step on a np-mirror model is a shadow's, recorded or not: it is never
                        // passed through. The record may land a moment after the spawn resolves.
                        )];
                    case 5:
                        s = _c.sent();
                        n = 0;
                        _c.label = 6;
                    case 6:
                        if (!(!s && e.model.startsWith(MODEL_PREFIX) && n < 20)) return [3 /*break*/, 10];
                        return [4 /*yield*/, __await((0, bridge_1.sleep)($, 250))];
                    case 7:
                        _c.sent();
                        return [4 /*yield*/, __await((0, shadow_1.loadShadow)($, e.agentId))];
                    case 8:
                        s = _c.sent();
                        _c.label = 9;
                    case 9:
                        n++;
                        return [3 /*break*/, 6];
                    case 10:
                        if (!!s) return [3 /*break*/, 20];
                        if (!!e.model.startsWith(MODEL_PREFIX)) return [3 /*break*/, 14];
                        return [5 /*yield**/, __values(__asyncDelegator(__asyncValues(next(e))))];
                    case 11: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 12: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 13: return [2 /*return*/, _c.sent()];
                    case 14:
                        text = 'NativePeers has no record of this shadow; its worker is unaffected. /np reattach shows it again.';
                        return [4 /*yield*/, __await({ kind: 'text', index: 0, text: text })];
                    case 15: return [4 /*yield*/, _c.sent()];
                    case 16:
                        _c.sent();
                        return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'end_turn', usage: null })];
                    case 17: return [4 /*yield*/, _c.sent()];
                    case 18:
                        _c.sent();
                        return [4 /*yield*/, __await({ turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null })];
                    case 19: return [2 /*return*/, _c.sent()];
                    case 20:
                        _a = Boolean;
                        return [4 /*yield*/, __await((0, state_1.getBridge)($))];
                    case 21:
                        steer = _a.apply(void 0, [(_b = (_c.sent()).steer) === null || _b === void 0 ? void 0 : _b[s.provider]]);
                        return [5 /*yield**/, __values(__asyncDelegator(__asyncValues((0, shadow_1.stepShadow)($, e, s, { showReasoning: o.showReasoning !== 'off', followupMode: o.followupMode === 'steer-if-supported' ? 'steer-if-supported' : 'queue', steer: steer }))))
                            // Own time for the performance budget (spec 11): waits on $ calls do not count.
                        ];
                    case 22: return [4 /*yield*/, __await.apply(void 0, [_c.sent()
                            // Own time for the performance budget (spec 11): waits on $ calls do not count.
                        ])];
                    case 23:
                        r = _c.sent();
                        // Own time for the performance budget (spec 11): waits on $ calls do not count.
                        $.ui.log("np: step own-time ".concat(Math.round(next.budget.ms - next.budget.remainingMs), "ms ").concat(s.provider, " ").concat(e.agentId, " step ").concat(e.index), { to: 'debug' });
                        return [4 /*yield*/, __await(r)];
                    case 24: return [2 /*return*/, _c.sent()];
                }
            });
        });
    }).catch(function npStepFault($, e, next) {
        return __asyncGenerator(this, arguments, function npStepFault_1() {
            var kind, text;
            var _a, _b;
            return __generator(this, function (_c) {
                switch (_c.label) {
                    case 0:
                        if (!(e.agentId === undefined || !e.model.startsWith(MODEL_PREFIX))) return [3 /*break*/, 4];
                        return [5 /*yield**/, __values(__asyncDelegator(__asyncValues(next(e))))];
                    case 1: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 2: return [4 /*yield*/, __await.apply(void 0, [_c.sent()])];
                    case 3: return [2 /*return*/, _c.sent()];
                    case 4:
                        kind = (_b = (_a = next.error) === null || _a === void 0 ? void 0 : _a.kind) !== null && _b !== void 0 ? _b : 'unknown';
                        text = "\n[NativePeers mirror fault: ".concat(kind, ". The worker is unaffected; /np reattach or /np doctor.]");
                        return [4 /*yield*/, __await({ kind: 'text', index: 0, text: text })];
                    case 5: return [4 /*yield*/, _c.sent()];
                    case 6:
                        _c.sent();
                        return [4 /*yield*/, __await({ kind: 'stop', stopReason: 'end_turn', usage: null })];
                    case 7: return [4 /*yield*/, _c.sent()];
                    case 8:
                        _c.sent();
                        void (0, shadow_1.faultShadow)($, e.agentId, kind);
                        $.ui.toast('np: mirror fault (' + kind + '); the worker keeps running');
                        return [4 /*yield*/, __await({ turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null })];
                    case 9: return [2 /*return*/, _c.sent()];
                }
            });
        });
    });
    on('tool.call', { tool: mirror_1.MIRROR_TOOL }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { var _a; return __generator(this, function (_b) {
        return [2 /*return*/, (0, shadow_1.mirrorCall)($, e.agentId, e, Number((_a = o.mirrorOutputBytes) !== null && _a !== void 0 ? _a : 8192), next.signal)];
    }); }); })
        .catch(function () { return ({ deny: 'NativePeers mirror unavailable' }); });
    on('tool.call', { tool: CATALOG }, function ($, e) { return __awaiter(void 0, void 0, void 0, function () {
        var _a, _b;
        var _c;
        return __generator(this, function (_d) {
            switch (_d.label) {
                case 0:
                    _c = {};
                    _b = (_a = JSON).stringify;
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', '/v1/catalog' + (e.provider ? '?provider=' + e.provider : ''))];
                case 1: return [2 /*return*/, (_c.result = _b.apply(_a, [_d.sent()]), _c)];
            }
        });
    }); })
        .catch(function () { return ({ deny: 'NativePeers catalog unavailable — run /np doctor' }); });
    on('tool.call', { tool: 'Bash' }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, observeSpawn($, e, next, o)];
    }); }); }).catch(function ($, e, next) { return next(e); });
    // A pattern, not names: the nativepeers MCP server may be absent from the session the types were written in.
    on('tool.call', { tool: /^mcp__nativepeers__worker_(spawn|resume)$/ }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, observeSpawn($, e, next, o)];
    }); }); }).catch(function ($, e, next) { return next(e); });
    on('tool.check', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var s, _a;
        var _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    if (!e.agentId) return [3 /*break*/, 2];
                    return [4 /*yield*/, (0, shadow_1.loadShadow)($, e.agentId)];
                case 1:
                    _a = _c.sent();
                    return [3 /*break*/, 3];
                case 2:
                    _a = undefined;
                    _c.label = 3;
                case 3:
                    s = _a;
                    if (s)
                        return [2 /*return*/, e.tool === mirror_1.MIRROR_TOOL && s.pendingRefs[String((_b = e.input) === null || _b === void 0 ? void 0 : _b.ref)] ? { decision: 'allow', reason: 'NativePeers mirror replay' }
                                : { decision: 'deny', reason: 'NativePeers shadows have no tools but the mirror' }];
                    if (e.tool === mirror_1.MIRROR_TOOL)
                        return [2 /*return*/, { decision: 'deny', reason: 'Internal NativePeers display tool' }];
                    return [2 /*return*/, next(e)];
            }
        });
    }); }).catch(function ($, e, next) { return e.tool === mirror_1.MIRROR_TOOL ? { decision: 'deny' } : next(e); });
    on('session.append', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _a.sent();
                    if (!e.agentId) return [3 /*break*/, 3];
                    return [4 /*yield*/, (0, shadow_1.captureRow)($, e.agentId, e, e.uuid)];
                case 2:
                    _a.sent();
                    _a.label = 3;
                case 3: return [2 /*return*/, r];
            }
        });
    }); }).catch(function ($, e, next) { return next(e); });
    on('session.compact', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        switch (_a.label) {
            case 0: return [4 /*yield*/, (0, shadow_1.loadShadow)($, e.agentId)];
            case 1: return [2 /*return*/, (_a.sent()) ? { skip: 'NativePeers shadow transcripts are not compacted' } : next(e)];
        }
    }); }); })
        .catch(function ($, e, next) { return next(e); });
    on('turn.complete', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var r;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, next(e)];
                case 1:
                    r = _a.sent();
                    if (!(e.agentId && !inert)) return [3 /*break*/, 3];
                    return [4 /*yield*/, (0, shadow_1.completeShadow)($, e.agentId, e.reason, e.isAborted, e.answer, e.usage)];
                case 2:
                    _a.sent();
                    _a.label = 3;
                case 3: return [2 /*return*/, r];
            }
        });
    }); });
    on('command.run', { command: 'np' }, function ($, e) { return __awaiter(void 0, void 0, void 0, function () { var _a, _b; return __generator(this, function (_c) {
        return [2 /*return*/, command($, e.args, o, String((_b = (_a = e.presentation) === null || _a === void 0 ? void 0 : _a.surface) !== null && _b !== void 0 ? _b : 'terminal'))];
    }); }); });
    on('ui.press', function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () {
        var w, _a, _b, _c;
        return __generator(this, function (_d) {
            switch (_d.label) {
                case 0:
                    if (e.plugin !== 'np' || !e.element.startsWith('attach:'))
                        return [2 /*return*/, next(e)];
                    return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 1:
                    w = (_d.sent()).find(function (x) { return x.workerId === e.element.slice(7); });
                    if (!w) return [3 /*break*/, 3];
                    _b = (_a = $.ui).toast;
                    _c = 'np: ';
                    return [4 /*yield*/, attachWorker($, w, 'ui.press', true)];
                case 2:
                    _b.apply(_a, [_c + (_d.sent())]);
                    _d.label = 3;
                case 3: return [2 /*return*/, { element: e.element }];
            }
        });
    }); });
    on('ui.render', { component: 'AbovePrompt' }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, o.band === 'off' ? next(e) : (0, ui_1.renderBand)($, e, next)];
    }); }); });
    on('ui.render', { component: 'Pane', requestId: ui_1.PANE_ID }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, ui_1.renderPane)($, e, next)];
    }); }); });
    on('ui.render', { component: 'ToolUse', props: { tool: mirror_1.MIRROR_TOOL } }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, ui_1.renderToolUse)($, e, next)];
    }); }); });
    on('ui.render', { component: 'UserMessage', props: { origin: { kind: 'coordinator' } } }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, ui_1.renderUserMessage)($, e, next)];
    }); }); });
    on('ui.render', { component: 'Spinner' }, function ($, e, next) { return __awaiter(void 0, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, ui_1.renderSpinner)($, e, next)];
    }); }); });
};
exports.register = register;
