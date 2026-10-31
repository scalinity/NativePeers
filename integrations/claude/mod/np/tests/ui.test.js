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
var testing_1 = require("claude-code/testing");
var workers = [
    { workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', modelRequested: 'gpt-6.1-sol', effort: 'high', title: 'Codebase review', status: 'running', startedAt: 1000, agentId: 'a1' },
    { workerId: 'W2', groupId: 'G2', generation: 1, provider: 'grok', modelRequested: 'grok-4.7', title: 'Easy wins', status: 'completed', startedAt: 1000, endedAt: 61000 },
];
var shadow = { agentId: 'a1', workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', model: 'gpt-6.1-sol', effort: 'high', profile: 'analysis', title: 'Codebase review',
    phase: 'STREAMING', deliveryOwner: 'mirror', cursor: 4, pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: 's', startedAt: 1000, waits: 0, latest: 'pytest -q' };
function state(on) {
    on('clock.now', function () { return ({ value: 61000 }); });
    on('state.get', function ($, e) {
        var values = {
            bridge: { state: 'ready', since: 1, peer: 'np1_x' },
            lineage: { shape: 'np-lineage-1', value: workers },
            shadows: ['a1'],
            faults: { fallthrough: false },
            ui: { bandCollapsed: false, paneOpen: false },
            shadow: e.id === 'a1' ? { shape: 'np-shadow-1', value: shadow } : undefined,
        };
        var value = values[e.key];
        return { value: value === undefined ? { value: undefined, version: 0 } : { value: value, version: 1 } };
    });
}
var band = function (view) { return ({
    plugin: 'np', component: 'AbovePrompt', requestId: 'above-prompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 8, bodyColumns: 120, scroll: { offset: 0, bodyRows: 8 }, view: view },
}); };
(0, testing_1.test)('the band summarizes delegates and offers Stop all and Open, on terminal and desktop', function ($, on) { return __awaiter(void 0, void 0, void 0, function () {
    var _i, _a, surface, ui, _b, _c, _d, _e, _f;
    return __generator(this, function (_g) {
        switch (_g.label) {
            case 0:
                state(on);
                _i = 0, _a = ['terminal', 'desktop'];
                _g.label = 1;
            case 1:
                if (!(_i < _a.length)) return [3 /*break*/, 10];
                surface = _a[_i];
                return [4 /*yield*/, $.ui.mount(__assign(__assign({}, band({})), { surface: surface }))];
            case 2:
                ui = _g.sent();
                _b = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Text', text: /⟐ np · 2 delegates · 1 running · 1 done/ })];
            case 3:
                _b.apply(void 0, [(_g.sent())]).toBeTruthy();
                _c = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Button', key: 'stopall' })];
            case 4:
                _c.apply(void 0, [_g.sent()]).toBeTruthy();
                _d = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Button', key: 'open-W1' })];
            case 5:
                _d.apply(void 0, [_g.sent()]).toBeTruthy();
                _e = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Button', key: 'open-W2' })];
            case 6:
                _e.apply(void 0, [_g.sent()]).toBeFalsy();
                _f = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Text', text: /GPT-6.1 Sol high · Codebase review/ })];
            case 7:
                _f.apply(void 0, [_g.sent()]).toBeTruthy();
                return [4 /*yield*/, ui.unmount()];
            case 8:
                _g.sent();
                _g.label = 9;
            case 9:
                _i++;
                return [3 /*break*/, 1];
            case 10: return [2 /*return*/];
        }
    });
}); });
(0, testing_1.test)('viewing a shadow shows its worker header with Interrupt, Stop and Resume cmd', function ($, on) { return __awaiter(void 0, void 0, void 0, function () {
    var _i, _a, surface, ui, _b, _c, _d, key, _e;
    return __generator(this, function (_f) {
        switch (_f.label) {
            case 0:
                state(on);
                _i = 0, _a = ['terminal', 'desktop'];
                _f.label = 1;
            case 1:
                if (!(_i < _a.length)) return [3 /*break*/, 10];
                surface = _a[_i];
                return [4 /*yield*/, $.ui.mount(__assign(__assign({}, band({ agentId: 'a1' })), { surface: surface }))];
            case 2:
                ui = _f.sent();
                _b = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Text', text: /↳ Subagent of main · codex · gpt-6.1-sol · high · analysis/ })];
            case 3:
                _b.apply(void 0, [_f.sent()]).toBeTruthy();
                _c = 0, _d = ['interrupt', 'stop', 'resume', 'pane', 'main'];
                _f.label = 4;
            case 4:
                if (!(_c < _d.length)) return [3 /*break*/, 7];
                key = _d[_c];
                _e = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Button', key: key })];
            case 5:
                _e.apply(void 0, [_f.sent()]).toBeTruthy();
                _f.label = 6;
            case 6:
                _c++;
                return [3 /*break*/, 4];
            case 7: return [4 /*yield*/, ui.unmount()];
            case 8:
                _f.sent();
                _f.label = 9;
            case 9:
                _i++;
                return [3 /*break*/, 1];
            case 10: return [2 /*return*/];
        }
    });
}); });
(0, testing_1.test)('the lineage pane lists workers and the selected worker\'s identities', function ($, on) { return __awaiter(void 0, void 0, void 0, function () {
    var _i, _a, surface, ui, _b, _c, _d;
    return __generator(this, function (_e) {
        switch (_e.label) {
            case 0:
                state(on);
                _i = 0, _a = ['terminal', 'desktop'];
                _e.label = 1;
            case 1:
                if (!(_i < _a.length)) return [3 /*break*/, 8];
                surface = _a[_i];
                return [4 /*yield*/, $.ui.mount({ plugin: 'np', component: 'Pane', requestId: 'np-lineage', surface: surface, props: { title: 'NativePeers lineage', isFocused: true, bodyColumns: 100, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } })];
            case 2:
                ui = _e.sent();
                _b = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Text', text: /NativePeers lineage/ })];
            case 3:
                _b.apply(void 0, [_e.sent()]).toBeTruthy();
                _c = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Button', key: 'sel-W1' })];
            case 4:
                _c.apply(void 0, [_e.sent()]).toBeTruthy();
                _d = testing_1.expect;
                return [4 /*yield*/, ui.find({ type: 'Text', text: /worker W2 · group G2/ })];
            case 5:
                _d.apply(void 0, [_e.sent()]).toBeTruthy();
                return [4 /*yield*/, ui.unmount()];
            case 6:
                _e.sent();
                _e.label = 7;
            case 7:
                _i++;
                return [3 /*break*/, 1];
            case 8: return [2 /*return*/];
        }
    });
}); });
