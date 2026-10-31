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
Object.defineProperty(exports, "__esModule", { value: true });
exports.PANE_ID = void 0;
exports.openView = openView;
exports.mainView = mainView;
exports.openPane = openPane;
exports.stopAll = stopAll;
exports.interruptWorker = interruptWorker;
exports.stopWorker = stopWorker;
exports.copyResume = copyResume;
exports.renderBand = renderBand;
exports.renderPane = renderPane;
exports.renderToolUse = renderToolUse;
exports.renderUserMessage = renderUserMessage;
exports.renderSpinner = renderSpinner;
exports.statusLine = statusLine;
exports.statusText = statusText;
var agents_1 = require("./agents");
var bridge_1 = require("./bridge");
var mirror_1 = require("./mirror");
var shadow_1 = require("./shadow");
var state_1 = require("./state");
exports.PANE_ID = 'np-lineage';
var ACTIVE = ['queued', 'starting', 'running', 'waiting'];
function counts(workers) {
    return { total: workers.length, running: workers.filter(function (w) { return ACTIVE.includes(w.status); }).length,
        done: workers.filter(function (w) { return w.status === 'completed'; }).length, failed: workers.filter(function (w) { return ['failed', 'timed_out'].includes(w.status); }).length,
        stopped: workers.filter(function (w) { return ['canceled', 'interrupted'].includes(w.status); }).length };
}
function elapsed(w, now) {
    var _a;
    return w.startedAt ? (0, mirror_1.duration)(((_a = w.endedAt) !== null && _a !== void 0 ? _a : now) - w.startedAt) : '';
}
// Claude Code drops a finished agent's task about 30 s after it ends; its view is refused from then on.
function openView($_1, agentId_1) {
    return __awaiter(this, arguments, void 0, function ($, agentId, quiet) {
        var r;
        if (quiet === void 0) { quiet = false; }
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, $.ui.view({ agentId: agentId })];
                case 1:
                    r = _a.sent();
                    if ((r === null || r === void 0 ? void 0 : r.deny) && !quiet)
                        $.ui.toast('np: ' + r.deny);
                    return [2 /*return*/, r === null || r === void 0 ? void 0 : r.deny];
            }
        });
    });
}
// Esc in a worker's view stops that worker (gate G4), so the band offers a way back.
function mainView($) {
    return __awaiter(this, void 0, void 0, function () {
        var r;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, $.ui.view({})];
                case 1:
                    r = _a.sent();
                    if (r === null || r === void 0 ? void 0 : r.deny)
                        $.ui.toast('np: ' + r.deny);
                    return [2 /*return*/];
            }
        });
    });
}
function openPane($) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, $.ui.open({ id: exports.PANE_ID, title: 'NativePeers lineage', focus: true })];
                case 1:
                    _a.sent();
                    return [4 /*yield*/, (0, state_1.setUi)($, { paneOpen: true })];
                case 2:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function stopAll($) {
    return __awaiter(this, void 0, void 0, function () {
        var groups, _a, _i, groups_1, group, _b, _c, _d, _e, error_1;
        var _f;
        return __generator(this, function (_g) {
            switch (_g.label) {
                case 0:
                    _a = Set.bind;
                    return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 1:
                    groups = new (_a.apply(Set, [void 0, (_g.sent()).filter(function (w) { return ACTIVE.includes(w.status) && !w.parentWorkerId; }).map(function (w) { return w.groupId; })]))();
                    _i = 0, groups_1 = groups;
                    _g.label = 2;
                case 2:
                    if (!(_i < groups_1.length)) return [3 /*break*/, 8];
                    group = groups_1[_i];
                    _g.label = 3;
                case 3:
                    _g.trys.push([3, 6, , 7]);
                    _b = bridge_1.request;
                    _c = [$, 'POST', "/v1/groups/".concat(group, "/cancel")];
                    _f = {};
                    _e = (_d = "np-stop-all:".concat(group, ":")).concat;
                    return [4 /*yield*/, $.clock.now()];
                case 4: return [4 /*yield*/, _b.apply(void 0, _c.concat([(_f.client_request_id = _e.apply(_d, [_g.sent()]), _f.reason = 'Stop all from Claude Code', _f)]))];
                case 5:
                    _g.sent();
                    return [3 /*break*/, 7];
                case 6:
                    error_1 = _g.sent();
                    $.ui.toast('np: stop failed for ' + group + ': ' + String(error_1).slice(0, 120));
                    return [3 /*break*/, 7];
                case 7:
                    _i++;
                    return [3 /*break*/, 2];
                case 8:
                    if (groups.size)
                        $.ui.toast("np: stopping ".concat(groups.size, " group").concat(groups.size > 1 ? 's' : '', " and their descendants"));
                    return [2 /*return*/, groups.size];
            }
        });
    });
}
function interruptWorker($, s) {
    return __awaiter(this, void 0, void 0, function () {
        var r, _a, _b, _c, _d, error_2;
        var _e;
        var _f;
        return __generator(this, function (_g) {
            switch (_g.label) {
                case 0:
                    _g.trys.push([0, 3, , 4]);
                    _a = bridge_1.request;
                    _b = [$, 'POST', "/v1/workers/".concat(s.workerId, "/interrupt")];
                    _e = {};
                    _d = (_c = "np-int:".concat(s.agentId, ":").concat(s.generation, ":")).concat;
                    return [4 /*yield*/, $.clock.now()];
                case 1: return [4 /*yield*/, _a.apply(void 0, _b.concat([(_e.client_request_id = _d.apply(_c, [_g.sent()]), _e)]))];
                case 2:
                    r = _g.sent();
                    $.ui.toast("np: interrupt ".concat((_f = r.state) !== null && _f !== void 0 ? _f : 'requested', " (").concat(agents_1.TAGS[s.provider], ")"));
                    return [3 /*break*/, 4];
                case 3:
                    error_2 = _g.sent();
                    $.ui.toast('np: interrupt failed: ' + String(error_2).slice(0, 160));
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    });
}
function stopWorker($, s) {
    return __awaiter(this, void 0, void 0, function () {
        var error_3;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    _a.trys.push([0, 2, , 3]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'POST', "/v1/workers/".concat(s.workerId, "/cancel"), { client_request_id: "np-stop:".concat(s.agentId, ":").concat(s.generation), reason: 'Stopped from the np band' })];
                case 1:
                    _a.sent();
                    $.ui.toast("np: stopping ".concat(agents_1.TAGS[s.provider], " \u00B7 ").concat(s.title));
                    return [3 /*break*/, 3];
                case 2:
                    error_3 = _a.sent();
                    $.ui.toast('np: stop failed: ' + String(error_3).slice(0, 160));
                    return [3 /*break*/, 3];
                case 3: return [2 /*return*/];
            }
        });
    });
}
function copyResume($, s, surface) {
    return __awaiter(this, void 0, void 0, function () {
        var r, text, copied, error_4;
        var _a, _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    _c.trys.push([0, 3, , 4]);
                    return [4 /*yield*/, (0, bridge_1.request)($, 'GET', "/v1/workers/".concat(s.workerId, "/resume-command"))];
                case 1:
                    r = _c.sent();
                    text = String((_b = (_a = r.command) !== null && _a !== void 0 ? _a : r.shell) !== null && _b !== void 0 ? _b : JSON.stringify(r)).slice(0, 4000);
                    return [4 /*yield*/, $.ui.copy({ text: text, surface: surface })];
                case 2:
                    copied = _c.sent();
                    $.ui.toast((copied === null || copied === void 0 ? void 0 : copied.isCopied) ? 'np: resume command copied' : 'np: resume command — ' + text.slice(0, 300));
                    return [3 /*break*/, 4];
                case 3:
                    error_4 = _c.sent();
                    $.ui.toast('np: no resume command: ' + String(error_4).slice(0, 160));
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    });
}
function renderBand($, e, next) {
    return __awaiter(this, void 0, void 0, function () {
        var bridge, workers, index, faults, ui, viewed, _a, Box, Text, Button, now, s_1, w, c, head, status, rows, offers, _i, workers_1, w, _b, _c, _d;
        var _e, _f, _g;
        return __generator(this, function (_h) {
            switch (_h.label) {
                case 0: return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 1:
                    bridge = _h.sent();
                    return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 2:
                    workers = _h.sent();
                    return [4 /*yield*/, (0, state_1.getIndex)($)];
                case 3:
                    index = _h.sent();
                    return [4 /*yield*/, (0, state_1.getFaults)($)];
                case 4:
                    faults = _h.sent();
                    return [4 /*yield*/, (0, state_1.getUi)($)];
                case 5:
                    ui = _h.sent();
                    viewed = (_e = e.props.view) === null || _e === void 0 ? void 0 : _e.agentId;
                    if (e.props.hasSurvey || (ui === null || ui === void 0 ? void 0 : ui.bandCollapsed))
                        return [2 /*return*/, next(e)];
                    if (bridge.state === 'off' && !workers.length)
                        return [2 /*return*/, next(e)];
                    _a = $.ui.resolve(e), Box = _a.Box, Text = _a.Text, Button = _a.Button;
                    return [4 /*yield*/, $.clock.now()];
                case 6:
                    now = _h.sent();
                    if (!(viewed && index.includes(viewed))) return [3 /*break*/, 8];
                    return [4 /*yield*/, (0, shadow_1.loadShadow)($, viewed)];
                case 7:
                    s_1 = _h.sent();
                    if (s_1) {
                        w = workers.find(function (x) { return x.workerId === s_1.workerId; });
                        return [2 /*return*/, (<Box flexDirection="row" flexWrap="wrap">
          <Text dimColor>↳ Subagent of main · {s_1.provider} · {(_g = (_f = w === null || w === void 0 ? void 0 : w.modelObserved) !== null && _f !== void 0 ? _f : s_1.model) !== null && _g !== void 0 ? _g : 'default'}{s_1.effort ? ' · ' + s_1.effort : ''}{s_1.profile ? ' · ' + s_1.profile : ''} · {(0, mirror_1.duration)(now - s_1.startedAt)}{w ? ' · ' + w.status : ''} </Text>
          {s_1.workerId ? <Button key="interrupt" label="Interrupt" onPress={function () { return interruptWorker($, s_1); }}/> : null}
          {s_1.workerId ? <Button key="stop" label="Stop" onPress={function () { return stopWorker($, s_1); }}/> : null}
          {s_1.workerId ? <Button key="resume" label="Resume cmd" onPress={function (p) { return copyResume($, s_1, p.surface); }}/> : null}
          <Button key="pane" label="Pane" onPress={function () { return openPane($); }}/>
          <Button key="main" label="Main" hotkey="m" onPress={function () { return mainView($); }}/>
        </Box>)];
                    }
                    _h.label = 8;
                case 8:
                    c = counts(workers.filter(function (w) { return !w.parentWorkerId; }));
                    head = "\u27D0 np \u00B7 ".concat(c.total, " delegate").concat(c.total === 1 ? '' : 's', " \u00B7 ").concat(c.running, " running \u00B7 ").concat(c.done, " done").concat(c.stopped ? " \u00B7 ".concat(c.stopped, " stopped") : '').concat(c.failed ? " \u00B7 ".concat(c.failed, " failed") : '');
                    status = faults.fallthrough ? ' · agent types off — /np doctor' : bridge.state === 'starting' ? ' · bridge starting'
                        : bridge.state !== 'ready' ? " \u00B7 bridge ".concat(bridge.state, " \u2014 /np doctor") : '';
                    rows = workers.filter(function (w) { return !w.parentWorkerId; }).slice(-4);
                    offers = [];
                    _i = 0, workers_1 = workers;
                    _h.label = 9;
                case 9:
                    if (!(_i < workers_1.length)) return [3 /*break*/, 13];
                    w = workers_1[_i];
                    if (offers.length >= 2 || !ACTIVE.includes(w.status) || w.mirrorOwner === 'other')
                        return [3 /*break*/, 12];
                    _b = !w.agentId;
                    if (_b) return [3 /*break*/, 11];
                    _d = (_c = ['FAULTED', 'DETACHED', 'COMPLETED']).includes;
                    return [4 /*yield*/, (0, state_1.getMember)($, w.agentId)];
                case 10:
                    _b = _d.apply(_c, [(_h.sent()).phase]);
                    _h.label = 11;
                case 11:
                    if (_b)
                        offers.push(w);
                    _h.label = 12;
                case 12:
                    _i++;
                    return [3 /*break*/, 9];
                case 13: return [2 /*return*/, (<Box flexDirection="column">
      <Box flexDirection="row" columnGap="sm">
        <Box flexShrink={1}><Text dimColor wrap="truncate-end">{head}{status}</Text></Box>
        {c.running ? <Button key="stopall" label="Stop all" onPress={function () { return stopAll($); }}/> : null}
        <Button key="pane" label="Pane" onPress={function () { return openPane($); }}/>
      </Box>
      {rows.map(function (w, n) {
                            var _a, _b;
                            return (<Box key={'row-' + w.workerId} flexDirection="row" columnGap="sm">
          <Text color={ACTIVE.includes(w.status) ? 'suggestion' : w.status === 'completed' ? 'success' : 'error'}>{(_a = agents_1.TAGS[w.provider]) !== null && _a !== void 0 ? _a : w.provider}</Text>
          <Box flexShrink={1}><Text wrap="truncate-end">{(0, agents_1.modelLabel)((_b = w.modelObserved) !== null && _b !== void 0 ? _b : w.modelRequested, w.provider)}{w.effort ? ' ' + w.effort : ''} · {w.title} · {elapsed(w, now)} · {w.status}</Text></Box>
          {w.agentId && index.includes(w.agentId) ? <Button key={'open-' + w.workerId} label="Open" hotkey={String(n + 1)} onPress={function () { return openView($, w.agentId); }}/> : null}
        </Box>);
                        })}
      {offers.map(function (w) { return (<Box key={'offer-' + w.workerId} flexDirection="row" columnGap="sm">
          <Box flexShrink={1}><Text dimColor wrap="truncate-end">unmirrored: {agents_1.TAGS[w.provider]} {w.title}</Text></Box>
          <Button key={'attach:' + w.workerId} label="Attach" onPress={function () { return undefined; }}/>
        </Box>); })}
    </Box>)];
            }
        });
    });
}
function renderPane($, e, next) {
    return __awaiter(this, void 0, void 0, function () {
        var workers, index, ui, _a, Box, Text, Button, now, selected, depth;
        var _b, _c, _d, _e, _f;
        return __generator(this, function (_g) {
            switch (_g.label) {
                case 0: return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 1:
                    workers = _g.sent();
                    return [4 /*yield*/, (0, state_1.getIndex)($)];
                case 2:
                    index = _g.sent();
                    return [4 /*yield*/, (0, state_1.getUi)($)];
                case 3:
                    ui = _g.sent();
                    _a = $.ui.resolve(e), Box = _a.Box, Text = _a.Text, Button = _a.Button;
                    return [4 /*yield*/, $.clock.now()];
                case 4:
                    now = _g.sent();
                    selected = (_b = workers.find(function (w) { return w.workerId === (ui === null || ui === void 0 ? void 0 : ui.selected); })) !== null && _b !== void 0 ? _b : workers[workers.length - 1];
                    depth = function (w) { var d = 0, cur = w; while ((cur === null || cur === void 0 ? void 0 : cur.parentWorkerId) && d < 6) {
                        cur = workers.find(function (x) { return x.workerId === cur.parentWorkerId; });
                        d++;
                    } return d; };
                    return [2 /*return*/, (<Box flexDirection="column">
      <Text bold>NativePeers lineage</Text>
      {workers.length ? null : <Text dimColor>No workers for this session yet.</Text>}
      {workers.map(function (w) {
                                var _a;
                                return (<Box key={'tree-' + w.workerId} flexDirection="row">
          <Button key={'sel-' + w.workerId} plain label={"".concat('  '.repeat(depth(w))).concat((_a = agents_1.TAGS[w.provider]) !== null && _a !== void 0 ? _a : w.provider, " ").concat(w.title, " \u00B7 ").concat(w.status, " \u00B7 ").concat(elapsed(w, now))} onPress={function () { return (0, state_1.setUi)($, { paneOpen: true, selected: w.workerId }); }}/>
        </Box>);
                            })}
      {selected ? (<Box flexDirection="column">
          <Text dimColor>worker {selected.workerId} · group {selected.groupId}{selected.taskId ? ' · task ' + selected.taskId : ''} · generation {selected.generation}</Text>
          <Text dimColor>native {(_c = selected.nativeId) !== null && _c !== void 0 ? _c : '?'} · requested {(_d = selected.modelRequested) !== null && _d !== void 0 ? _d : 'default'} · observed {(_e = selected.modelObserved) !== null && _e !== void 0 ? _e : '?'}{selected.effort ? ' · ' + selected.effort : ''}{selected.profile ? ' · ' + selected.profile : ''}</Text>
          <Text dimColor>{selected.outputTokens ? "".concat(selected.outputTokens, " output tokens \u00B7 ") : ''}{(_f = selected.latest) !== null && _f !== void 0 ? _f : ''}{selected.mirrorOwner === 'other' ? ' · mirrored in another session' : ''}</Text>
          <Box flexDirection="row">
            {selected.agentId && index.includes(selected.agentId) ? <Button key="p-open" label="Open" onPress={function () { return openView($, selected.agentId); }}/> : null}
            {!selected.agentId && ACTIVE.includes(selected.status) ? <Button key={'attach:' + selected.workerId} label="Attach" onPress={function () { return undefined; }}/> : null}
          </Box>
        </Box>) : null}
    </Box>)];
            }
        });
    });
}
function renderToolUse($, e, next) {
    return __awaiter(this, void 0, void 0, function () {
        var input, _a, Box, Text;
        var _b, _c;
        return __generator(this, function (_d) {
            input = e.props.input;
            if (!input || typeof input !== 'object' || e.props.isExpanded)
                return [2 /*return*/, next(e)];
            _a = $.ui.resolve(e), Box = _a.Box, Text = _a.Text;
            return [2 /*return*/, (<Box flexDirection="row">
      <Text bold color={e.props.isErrored ? 'error' : undefined}>{(_b = input.kind) !== null && _b !== void 0 ? _b : 'tool'}</Text>
      <Text>  {String((_c = input.title) !== null && _c !== void 0 ? _c : '').slice(0, 200)}</Text>
      {e.props.isRunning ? <Text dimColor>  running…</Text> : null}
    </Box>)];
        });
    });
}
function renderUserMessage($, e, next) {
    return __awaiter(this, void 0, void 0, function () {
        var text, adoption, _a, Box, Text;
        var _b, _c, _d, _e;
        return __generator(this, function (_f) {
            text = String((_b = e.props.text) !== null && _b !== void 0 ? _b : '');
            if (e.props.isExpanded || ((_c = e.props.origin) === null || _c === void 0 ? void 0 : _c.kind) !== 'coordinator')
                return [2 /*return*/, next(e)];
            adoption = text.startsWith('NativePeers worker "');
            if (!adoption && text.split('\n').length <= 3)
                return [2 /*return*/, next(e)];
            _a = $.ui.resolve(e), Box = _a.Box, Text = _a.Text;
            return [2 /*return*/, (<Box flexDirection="row">
      <Text dimColor>{adoption ? 'Task from main · via NativePeers' : 'From main agent'} · </Text>
      <Text>{(_e = (_d = text.split('\n').find(function (l) { return l.trim(); })) === null || _d === void 0 ? void 0 : _d.slice(0, 160)) !== null && _e !== void 0 ? _e : ''}</Text>
      <Text dimColor> … (ctrl+o for the full brief)</Text>
    </Box>)];
        });
    });
}
function renderSpinner($, e, next) {
    return __awaiter(this, void 0, void 0, function () {
        var s;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, shadow_1.loadShadow)($, e.requestId)];
                case 1:
                    s = _a.sent();
                    if (!s || !s.latest)
                        return [2 /*return*/, next(e)];
                    return [2 /*return*/, next(__assign(__assign({}, e), { props: __assign(__assign({}, e.props), { message: "".concat(agents_1.TAGS[s.provider], ": ").concat(s.latest).slice(0, 120) }) }))];
            }
        });
    });
}
function statusLine($) {
    return __awaiter(this, void 0, void 0, function () {
        var workers, c;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 1:
                    workers = _a.sent();
                    c = counts(workers.filter(function (w) { return !w.parentWorkerId; }));
                    $.ui.status(c.running ? "\u25C9".concat(c.running, " running \u00B7 \u2713").concat(c.done, " \u00B7 \u2717").concat(c.failed) : undefined);
                    return [2 /*return*/];
            }
        });
    });
}
function statusText($) {
    return __awaiter(this, void 0, void 0, function () {
        var bridge, workers, list, faults, now, lines;
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0: return [4 /*yield*/, (0, state_1.getBridge)($)];
                case 1:
                    bridge = _b.sent();
                    return [4 /*yield*/, (0, state_1.getLineage)($)];
                case 2:
                    workers = _b.sent();
                    return [4 /*yield*/, (0, shadow_1.shadows)($)];
                case 3:
                    list = _b.sent();
                    return [4 /*yield*/, (0, state_1.getFaults)($)];
                case 4:
                    faults = _b.sent();
                    return [4 /*yield*/, $.clock.now()];
                case 5:
                    now = _b.sent();
                    lines = ["bridge: ".concat(bridge.state).concat(bridge.reason ? ' — ' + bridge.reason : '').concat(bridge.peer ? ' · peer ' + bridge.peer : ''), "fallthrough: ".concat(faults.fallthrough ? 'DETECTED — ' + ((_a = faults.reason) !== null && _a !== void 0 ? _a : '') : 'none'), "shadows: ".concat(list.length), ''];
                    workers.forEach(function (w, n) { var _a, _b; return lines.push("".concat(n + 1, ". ").concat((_a = agents_1.TAGS[w.provider]) !== null && _a !== void 0 ? _a : w.provider, " ").concat((0, agents_1.modelLabel)((_b = w.modelObserved) !== null && _b !== void 0 ? _b : w.modelRequested, w.provider)).concat(w.effort ? ' ' + w.effort : '', " \u00B7 ").concat(w.title, " \u00B7 ").concat(w.status, " \u00B7 ").concat(elapsed(w, now), " \u00B7 worker ").concat(w.workerId).concat(w.agentId ? '' : ' · unmirrored')); });
                    if (!workers.length)
                        lines.push('No NativePeers workers for this session.');
                    return [2 /*return*/, lines.join('\n')];
            }
        });
    });
}
