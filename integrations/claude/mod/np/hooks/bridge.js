"use strict";
var __extends = (this && this.__extends) || (function () {
    var extendStatics = function (d, b) {
        extendStatics = Object.setPrototypeOf ||
            ({ __proto__: [] } instanceof Array && function (d, b) { d.__proto__ = b; }) ||
            function (d, b) { for (var p in b) if (Object.prototype.hasOwnProperty.call(b, p)) d[p] = b[p]; };
        return extendStatics(d, b);
    };
    return function (d, b) {
        if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
        extendStatics(d, b);
        function __() { this.constructor = d; }
        d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
    };
})();
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
Object.defineProperty(exports, "__esModule", { value: true });
exports.BridgeError = void 0;
exports.connected = connected;
exports.request = request;
exports.sleep = sleep;
exports.launch = launch;
exports.heartbeat = heartbeat;
exports.shutdown = shutdown;
exports.followLineage = followLineage;
var state_1 = require("./state");
var BridgeError = /** @class */ (function (_super) {
    __extends(BridgeError, _super);
    function BridgeError(code, message) {
        var _this = _super.call(this, message) || this;
        _this.code = code;
        return _this;
    }
    return BridgeError;
}(Error));
exports.BridgeError = BridgeError;
// The token lives only in module memory: never in $.state, $.store or a log.
var connection;
var launches = 0;
function connected() {
    return connection !== undefined;
}
function request($, method, path, body) {
    return __awaiter(this, void 0, void 0, function () {
        var conn, response, error_1, envelope;
        var _a, _b, _c, _d;
        return __generator(this, function (_e) {
            switch (_e.label) {
                case 0:
                    conn = connection;
                    if (!conn)
                        throw new BridgeError('BRIDGE_DOWN', 'NativePeers bridge is not running');
                    _e.label = 1;
                case 1:
                    _e.trys.push([1, 3, , 4]);
                    return [4 /*yield*/, $.http.fetch('http://np-bridge' + path, __assign({ method: method, socketPath: conn.socket, headers: { Authorization: 'Bearer ' + conn.token, 'Content-Type': 'application/json' } }, (body === undefined ? {} : { body: JSON.stringify(body) })))];
                case 2:
                    response = _e.sent();
                    return [3 /*break*/, 4];
                case 3:
                    error_1 = _e.sent();
                    throw new BridgeError('BRIDGE_UNREACHABLE', String(error_1).slice(0, 200));
                case 4:
                    try {
                        envelope = JSON.parse(response.text);
                    }
                    catch (_f) {
                        throw new BridgeError('PROTOCOL_ERROR', "bridge answered HTTP ".concat(response.status, " without JSON"));
                    }
                    if (!envelope || envelope.ok !== true)
                        throw new BridgeError((_b = (_a = envelope === null || envelope === void 0 ? void 0 : envelope.error) === null || _a === void 0 ? void 0 : _a.code) !== null && _b !== void 0 ? _b : 'BRIDGE_ERROR', String((_d = (_c = envelope === null || envelope === void 0 ? void 0 : envelope.error) === null || _c === void 0 ? void 0 : _c.message) !== null && _d !== void 0 ? _d : 'bridge error').slice(0, 300));
                    return [2 /*return*/, envelope.data];
            }
        });
    });
}
function sleep($, ms) {
    return __awaiter(this, void 0, void 0, function () {
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, $.process.run(['/bin/sleep', String(ms / 1000)], { timeoutMs: ms + 10000 })];
                case 1:
                    _a.sent();
                    return [2 /*return*/];
            }
        });
    });
}
function pump($, child, generation, socket) {
    return __awaiter(this, void 0, void 0, function () {
        var ended, piece, error_2, _a, _b;
        var _c;
        return __generator(this, function (_d) {
            switch (_d.label) {
                case 0:
                    _d.trys.push([0, 4, , 5]);
                    _d.label = 1;
                case 1:
                    if (!true) return [3 /*break*/, 3];
                    return [4 /*yield*/, child.next()];
                case 2:
                    piece = _d.sent();
                    if (piece.done) {
                        ended = piece.value;
                        return [3 /*break*/, 3];
                    }
                    return [3 /*break*/, 1];
                case 3: return [3 /*break*/, 5];
                case 4:
                    error_2 = _d.sent();
                    ended = String(error_2);
                    return [3 /*break*/, 5];
                case 5:
                    if ((connection === null || connection === void 0 ? void 0 : connection.generation) !== generation)
                        return [2 /*return*/];
                    connection = undefined;
                    _a = state_1.bridgeExited;
                    _b = [$, socket];
                    _c = { state: 'down', reason: 'bridge exited ' + JSON.stringify(ended !== null && ended !== void 0 ? ended : null).slice(0, 120) };
                    return [4 /*yield*/, $.clock.now()];
                case 6: 
                // After a hot reload the previous instance's bridge exits beside this one's: only its own bridge's exit marks the state down.
                return [4 /*yield*/, _a.apply(void 0, _b.concat([(_c.since = _d.sent(), _c)]))];
                case 7:
                    // After a hot reload the previous instance's bridge exits beside this one's: only its own bridge's exit marks the state down.
                    _d.sent();
                    return [2 /*return*/];
            }
        });
    });
}
// Starts `npeer bridge` for this session and reads its one ready line. The
// child lives as long as `pump` reads it: the engine kills it when the module
// unloads or reloads.
function launch($, session, cwd, npeer) {
    return __awaiter(this, void 0, void 0, function () {
        var generation, _a, _b, child, out, err, ready, guard, piece, line, error_3, reason, _c, _d, _token, hello, _e, _f;
        var _g, _h, _j;
        var _k, _l, _m, _o;
        return __generator(this, function (_p) {
            switch (_p.label) {
                case 0:
                    generation = ++launches;
                    _a = state_1.setBridge;
                    _b = [$];
                    _g = { state: 'starting' };
                    return [4 /*yield*/, $.clock.now()];
                case 1: return [4 /*yield*/, _a.apply(void 0, _b.concat([(_g.since = _p.sent(), _g)]))];
                case 2:
                    _p.sent();
                    child = $.process.spawn({ argv: [npeer, 'bridge', '--claude-session', session, '--protocol', '1', '--json'], cwd: cwd });
                    out = '', err = '';
                    guard = $.clock.after(15000, function () { if (!ready)
                        void child.return(undefined); });
                    _p.label = 3;
                case 3:
                    _p.trys.push([3, 7, 8, 9]);
                    _p.label = 4;
                case 4:
                    if (!!ready) return [3 /*break*/, 6];
                    return [4 /*yield*/, child.next()];
                case 5:
                    piece = _p.sent();
                    if (piece.done)
                        return [3 /*break*/, 6];
                    if (piece.value.stream === 'stderr') {
                        err = (err + piece.value.text).slice(-2000);
                        return [3 /*break*/, 4];
                    }
                    out += piece.value.text;
                    line = out.indexOf('\n');
                    if (line >= 0) {
                        try {
                            ready = JSON.parse(out.slice(0, line));
                        }
                        catch (_q) {
                            return [3 /*break*/, 6];
                        }
                    }
                    if (out.length > 65536)
                        return [3 /*break*/, 6];
                    return [3 /*break*/, 4];
                case 6: return [3 /*break*/, 9];
                case 7:
                    error_3 = _p.sent();
                    err = (err + String(error_3)).slice(-2000);
                    return [3 /*break*/, 9];
                case 8:
                    guard.cancel();
                    return [7 /*endfinally*/];
                case 9:
                    if (!(!ready || ready.ready !== true || ready.protocol !== 1 || typeof ready.socket !== 'string' || typeof ready.token !== 'string')) return [3 /*break*/, 12];
                    void child.return(undefined);
                    reason = (((_k = ready === null || ready === void 0 ? void 0 : ready.error) === null || _k === void 0 ? void 0 : _k.code) ? "".concat(ready.error.code, ": ").concat((_l = ready.error.message) !== null && _l !== void 0 ? _l : '') : err.trim().split('\n').pop() || 'no ready line').slice(0, 300);
                    _c = state_1.setBridge;
                    _d = [$];
                    _h = { state: 'down', reason: reason };
                    return [4 /*yield*/, $.clock.now()];
                case 10: return [4 /*yield*/, _c.apply(void 0, _d.concat([(_h.since = _p.sent(), _h)]))];
                case 11:
                    _p.sent();
                    return [2 /*return*/, { reason: reason }];
                case 12:
                    connection = { socket: ready.socket, token: ready.token, generation: generation };
                    _token = ready.token, hello = __rest(ready, ["token"]);
                    _e = state_1.setBridge;
                    _f = [$];
                    _j = { state: 'ready', peer: hello.peer, version: hello.version, steer: (_o = (_m = hello.capabilities) === null || _m === void 0 ? void 0 : _m.steer) !== null && _o !== void 0 ? _o : {}, socket: ready.socket };
                    return [4 /*yield*/, $.clock.now()];
                case 13: return [4 /*yield*/, _e.apply(void 0, _f.concat([(_j.since = _p.sent(), _j)]))];
                case 14:
                    _p.sent();
                    void pump($, child, generation, ready.socket);
                    return [2 /*return*/, { hello: hello }];
            }
        });
    });
}
function heartbeat($) {
    return __awaiter(this, void 0, void 0, function () {
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    _b.trys.push([0, 2, , 3]);
                    return [4 /*yield*/, request($, 'POST', '/v1/heartbeat', {})];
                case 1:
                    _b.sent();
                    return [3 /*break*/, 3];
                case 2:
                    _a = _b.sent();
                    return [3 /*break*/, 3];
                case 3: return [2 /*return*/];
            }
        });
    });
}
function shutdown($) {
    return __awaiter(this, void 0, void 0, function () {
        var _a;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    _b.trys.push([0, 2, , 3]);
                    return [4 /*yield*/, request($, 'POST', '/v1/shutdown', {})];
                case 1:
                    _b.sent();
                    return [3 /*break*/, 3];
                case 2:
                    _a = _b.sent();
                    return [3 /*break*/, 3];
                case 3:
                    connection = undefined;
                    return [2 /*return*/];
            }
        });
    });
}
// The lineage long-poll. It only updates $.state: it never spawns a shadow,
// because a spawn outside a hook frame skips every hook of this plugin (G5).
function followLineage($) {
    return __awaiter(this, void 0, void 0, function () {
        var cursor, generation, data, _a;
        var _b;
        return __generator(this, function (_c) {
            switch (_c.label) {
                case 0:
                    cursor = '';
                    generation = connection === null || connection === void 0 ? void 0 : connection.generation;
                    _c.label = 1;
                case 1:
                    if (!(connection && connection.generation === generation)) return [3 /*break*/, 9];
                    _c.label = 2;
                case 2:
                    _c.trys.push([2, 6, , 8]);
                    return [4 /*yield*/, request($, 'GET', "/v1/lineage?wait_ms=25000".concat(cursor ? '&cursor=' + encodeURIComponent(cursor) : ''))];
                case 3:
                    data = _c.sent();
                    cursor = (_b = data.cursor) !== null && _b !== void 0 ? _b : cursor;
                    if (!(data.changed !== false && Array.isArray(data.workers))) return [3 /*break*/, 5];
                    return [4 /*yield*/, (0, state_1.setLineage)($, data.workers)];
                case 4:
                    _c.sent();
                    _c.label = 5;
                case 5: return [3 /*break*/, 8];
                case 6:
                    _a = _c.sent();
                    return [4 /*yield*/, sleep($, 2000)];
                case 7:
                    _c.sent();
                    return [3 /*break*/, 8];
                case 8: return [3 /*break*/, 1];
                case 9: return [2 /*return*/];
            }
        });
    });
}
