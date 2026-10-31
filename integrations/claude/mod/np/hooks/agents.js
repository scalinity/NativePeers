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
exports.HEADER_KEYS = exports.SHADOW_PROMPT = exports.SENTINEL = exports.TAGS = exports.PROVIDERS = void 0;
exports.shadowModel = shadowModel;
exports.isProvider = isProvider;
exports.agentSpec = agentSpec;
exports.parseHeader = parseHeader;
exports.modelLabel = modelLabel;
exports.describe = describe;
exports.titleFrom = titleFrom;
exports.workerSpec = workerSpec;
exports.adoptionBrief = adoptionBrief;
exports.PROVIDERS = ['codex', 'claude', 'antigravity', 'grok'];
exports.TAGS = { codex: 'cx', claude: 'cl', antigravity: 'ag', grok: 'gk' };
exports.SENTINEL = 'NativePeers mirror unavailable';
exports.SHADOW_PROMPT = "You are a display placeholder for an external NativePeers worker. Reply exactly: \"".concat(exports.SENTINEL, " \u2014 run /np doctor.\" Do nothing else.");
// A model id no API serves. A shadow never calls a model; if a fault ever let a
// step through, or when the engine's own agent summary asks the agent's model,
// the request is refused before any inference (gate G1, evidence/claude-lineage-m0.json).
function shadowModel(provider) {
    return 'np-mirror-' + provider;
}
function isProvider(value) {
    return exports.PROVIDERS.includes(value);
}
var WHEN = {
    codex: 'a Codex CLI worker (GPT models)',
    claude: 'a separate Claude Code CLI worker',
    antigravity: 'an Antigravity CLI worker (Gemini models)',
    grok: 'a Grok Build CLI worker',
};
function agentSpec(provider) {
    return {
        name: provider,
        description: "Delegate a self-contained task to ".concat(WHEN[provider], " owned by NativePeers; its live transcript appears here. ") +
            'Start the prompt with an optional ```np {"model","effort","profile","title"}``` JSON header (call mcp__np__catalog once for exact models), ' +
            'then a brief that stands alone: the worker sees only the brief. Results arrive as a background-agent notification; follow up with SendMessage.',
        prompt: exports.SHADOW_PROMPT,
        tools: ['mcp__np__mirror'],
        model: shadowModel(provider),
        effort: 'low',
        omitClaudeMd: true,
        background: true,
        permissionMode: 'dontAsk',
    };
}
exports.HEADER_KEYS = ['model', 'effort', 'profile', 'workspace', 'title', 'fallbacks', 'quota_policy', 'requirements'];
// The JSON may sit on the fence lines (```np {...}```), as the compose section shows it, or between them.
var HEADER = /^\s*```np[ \t]*(?:\r?\n)?([\s\S]*?)(?:\r?\n)?[ \t]*```[ \t]*(?:\r?\n|$)/;
function parseHeader(prompt) {
    var _a;
    var found = HEADER.exec(prompt);
    if (!found)
        return /^\s*```np\b/.test(prompt) ? { error: 'np header is not closed with ```' } : { header: {}, brief: prompt.trim() };
    var body = (_a = found[1]) !== null && _a !== void 0 ? _a : '';
    if (body.length > 4096)
        return { error: 'np header exceeds 4 KiB' };
    var value;
    try {
        value = JSON.parse(body);
    }
    catch (_b) {
        return { error: 'np header is not strict JSON' };
    }
    if (!value || typeof value !== 'object' || Array.isArray(value))
        return { error: 'np header must be a JSON object' };
    var header = value;
    for (var _i = 0, _c = Object.keys(header); _i < _c.length; _i++) {
        var key = _c[_i];
        if (!exports.HEADER_KEYS.includes(key))
            return { error: "unknown np header key \"".concat(key, "\"") };
    }
    for (var _d = 0, _e = ['model', 'effort', 'title', 'quota_policy']; _d < _e.length; _d++) {
        var key = _e[_d];
        if (header[key] !== undefined && (typeof header[key] !== 'string' || !header[key].trim()))
            return { error: "np header \"".concat(key, "\" must be a non-empty string") };
    }
    if (header.profile !== undefined && header.profile !== 'analysis' && header.profile !== 'workspace')
        return { error: 'np header "profile" must be analysis or workspace' };
    if (header.workspace !== undefined && typeof header.workspace !== 'string' && (typeof header.workspace !== 'object' || Array.isArray(header.workspace)))
        return { error: 'np header "workspace" must be a mode string or an object' };
    if (header.fallbacks !== undefined && (!Array.isArray(header.fallbacks) || header.fallbacks.some(function (f) { return !f || typeof f !== 'object' || typeof f.provider !== 'string'; })))
        return { error: 'np header "fallbacks" must be a list of {provider, model}' };
    if (header.requirements !== undefined && (typeof header.requirements !== 'object' || Array.isArray(header.requirements)))
        return { error: 'np header "requirements" must be an object' };
    if (typeof header.title === 'string' && header.title.length > 120)
        return { error: 'np header "title" exceeds 120 characters' };
    var brief = prompt.slice(found[0].length).trim();
    if (!brief)
        return { error: 'the brief after the np header is empty' };
    return { header: header, brief: brief };
}
var EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
// 'gpt-6.1-sol' -> 'GPT-6.1 Sol', 'claude-sonnet-5-5' -> 'Sonnet 5.5', 'grok-4.7' -> 'Grok 4.7'.
function modelLabel(model, provider) {
    if (!model)
        return provider + ' default';
    var parts = model.replace(/\[1m\]$/, '').split('-').filter(function (p) { return p && p !== 'claude'; });
    var out = [];
    for (var _i = 0, parts_1 = parts; _i < parts_1.length; _i++) {
        var part = parts_1[_i];
        var last = out[out.length - 1];
        if (/^\d+$/.test(part) && last && /^\d+(\.\d+)*$/.test(last) && !last.includes('.'))
            out[out.length - 1] = last + '.' + part;
        else if (part === 'gpt')
            out.push('GPT');
        else if (/^\d/.test(part) || EFFORTS.includes(part))
            out.push(part);
        else
            out.push(part.charAt(0).toUpperCase() + part.slice(1));
    }
    var label = out.join(' ').replace(/^GPT (\d)/, 'GPT-$1');
    return label || model;
}
function describe(provider, model, effort, title) {
    return "".concat(modelLabel(model, provider)).concat(effort ? ' ' + effort : '', " \u00B7 ").concat(title).slice(0, 200);
}
function titleFrom(header, description, brief) {
    var _a, _b, _c;
    var raw = (_c = (_b = (_a = header.title) !== null && _a !== void 0 ? _a : (description && description.trim())) !== null && _b !== void 0 ? _b : brief.split('\n')[0]) !== null && _c !== void 0 ? _c : '';
    return raw.replace(/\s+/g, ' ').trim().slice(0, 80) || 'NativePeers worker';
}
function workerSpec(provider, header, brief, title, cwd) {
    var _a, _b;
    var profile = (_a = header.profile) !== null && _a !== void 0 ? _a : 'analysis';
    var workspace = typeof header.workspace === 'string' ? { mode: header.workspace }
        : (_b = header.workspace) !== null && _b !== void 0 ? _b : { mode: profile === 'workspace' ? 'isolated-worktree' : 'inherit-read-only' };
    return __assign(__assign(__assign(__assign(__assign(__assign({ provider: provider, title: title, task: brief, permission_profile: profile, workspace: workspace }, (header.model ? { model: header.model } : {})), (header.effort ? { reasoning_effort: header.effort } : {})), (cwd ? { cwd: cwd } : {})), (header.fallbacks ? { fallbacks: header.fallbacks } : {})), (header.quota_policy ? { quota_policy: header.quota_policy } : {})), (header.requirements ? { requirements: header.requirements } : {}));
}
function adoptionBrief(title, provider, model, effort, groupId, task) {
    return "NativePeers worker \"".concat(title, "\" (").concat(provider, " ").concat(model !== null && model !== void 0 ? model : 'default').concat(effort ? ' ' + effort : '', "), group ").concat(groupId, ".\n\nTask:\n").concat(task);
}
