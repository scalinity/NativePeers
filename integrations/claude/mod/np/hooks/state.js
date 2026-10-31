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
exports.NONE = void 0;
exports.getBridge = getBridge;
exports.setBridge = setBridge;
exports.bridgeExited = bridgeExited;
exports.getLineage = getLineage;
exports.setLineage = setLineage;
exports.getFaults = getFaults;
exports.setFaults = setFaults;
exports.getUi = getUi;
exports.setUi = setUi;
exports.getIndex = getIndex;
exports.getMember = getMember;
exports.setMember = setMember;
// Every $.state value of the mod and the only reads and writes of them. The
// validator resolves a read or update target only to an atom of the same file.
var claude_code_1 = require("claude-code");
var bridgeAtom = (0, claude_code_1.atom)({ plugin: 'np', key: 'bridge' }, { state: 'off', since: 0 });
var lineageAtom = (0, claude_code_1.atom)({ plugin: 'np', key: 'lineage' }, [], { shape: 'np-lineage-1' });
var indexAtom = (0, claude_code_1.atom)({ plugin: 'np', key: 'shadows' }, []);
var faultsAtom = (0, claude_code_1.atom)({ plugin: 'np', key: 'faults' }, { fallthrough: false });
var uiAtom = (0, claude_code_1.atom)({ plugin: 'np', key: 'ui' }, { bandCollapsed: false, paneOpen: false });
var family = { plugin: 'np', key: 'shadow' };
exports.NONE = { agentId: '', generation: 0, provider: 'codex', title: '', phase: 'DETACHED', deliveryOwner: 'mirror', cursor: 0,
    pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: '', startedAt: 0, waits: 0 };
function getBridge($) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, claude_code_1.read)($, bridgeAtom)];
    }); });
}
function setBridge($, value) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        switch (_a.label) {
            case 0: return [4 /*yield*/, (0, claude_code_1.update)($, bridgeAtom, function () { return value; })];
            case 1:
                _a.sent();
                return [2 /*return*/];
        }
    }); });
}
function bridgeExited($, socket, value) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, claude_code_1.update)($, bridgeAtom, function (current) { return current.socket === socket ? value : current; })];
                case 1:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function getLineage($) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, claude_code_1.read)($, lineageAtom)];
    }); });
}
// The bridge names a worker's shadow only while its lease lives; a lease that lapsed during a bridge outage keeps this
// session's own shadow link unless another session now mirrors the worker.
function setLineage($, value) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, claude_code_1.update)($, lineageAtom, function (prev) { return value.map(function (w) {
                        var _a;
                        return w.agentId || w.mirrorOwner === 'other' ? w
                            : __assign(__assign({}, w), { agentId: (_a = prev.find(function (p) { return p.workerId === w.workerId; })) === null || _a === void 0 ? void 0 : _a.agentId });
                    }); })];
                case 1:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function getFaults($) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, claude_code_1.read)($, faultsAtom)];
    }); });
}
function setFaults($, value) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        switch (_a.label) {
            case 0: return [4 /*yield*/, (0, claude_code_1.update)($, faultsAtom, function () { return value; })];
            case 1:
                _a.sent();
                return [2 /*return*/];
        }
    }); });
}
function getUi($) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, claude_code_1.read)($, uiAtom)];
    }); });
}
function setUi($, change) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, claude_code_1.update)($, uiAtom, function (ui) { return (__assign(__assign({}, ui), change)); })];
                case 1:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function getIndex($) {
    return __awaiter(this, void 0, void 0, function () { return __generator(this, function (_a) {
        return [2 /*return*/, (0, claude_code_1.read)($, indexAtom)];
    }); });
}
function getMember($, agentId) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            return [2 /*return*/, (0, claude_code_1.read)($, (0, claude_code_1.atom)(__assign(__assign({}, family), { id: agentId }), exports.NONE, { shape: 'np-shadow-1' }))];
        });
    });
}
function setMember($, value) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, (0, claude_code_1.update)($, (0, claude_code_1.atom)(__assign(__assign({}, family), { id: value.agentId }), exports.NONE, { shape: 'np-shadow-1' }), function () { return value; })];
                case 1:
                    _a.sent();
                    return [4 /*yield*/, (0, claude_code_1.update)($, indexAtom, function (list) { return list.includes(value.agentId) ? list : __spreadArray(__spreadArray([], list, true), [value.agentId], false); })];
                case 2:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
