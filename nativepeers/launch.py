"""Explicit launch context, preserving the provider's native authentication."""
import os


def launch_environment(provider: str) -> dict[str, str]:
    common = {"HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "TERM",
              "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    provider_names = {
        "codex": {"CODEX_HOME", "OPENAI_API_KEY", "OPENAI_BASE_URL"},
        "claude": {"CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                   "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                   "CLAUDE_CODE_USE_FOUNDRY", "AWS_PROFILE", "AWS_REGION", "GOOGLE_CLOUD_PROJECT"},
        "antigravity": set(),
    }
    if provider not in provider_names:
        raise ValueError("Unsupported provider")
    # A documented nested-session guard is never silently removed to force startup.
    if provider == "claude" and os.getenv("CLAUDECODE"):
        raise RuntimeError("CAPABILITY_MISMATCH: nested Claude launch contract requires verification")
    allowed = common | provider_names[provider]
    return {k: v for k, v in os.environ.items() if k in allowed or k.startswith("LC_")}


def provider_home(provider):
    from pathlib import Path
    if provider == 'antigravity':
        from .antigravity import native_home
        return native_home().resolve()
    variable,default={'codex':('CODEX_HOME','.codex'),'claude':('CLAUDE_CONFIG_DIR','.claude')}[provider]
    return Path(os.environ.get(variable,Path.home()/default)).expanduser().resolve()


def home_environment(provider,home):
    variable={'codex':'CODEX_HOME','claude':'CLAUDE_CONFIG_DIR'}.get(provider)
    return {variable:str(home)} if variable else {}


def recorded_home(journal,row):
    """Recover legacy leases from their registered identity, never a new ambient home."""
    import json
    from pathlib import Path
    from .errors import require
    lease=json.loads(row.get('lease_json') or '{}')
    if lease.get('provider_home'):
        home=Path(lease['provider_home'])
        require(home.is_absolute() and str(home.resolve())==str(home),'OWNERSHIP_CONFLICT','Recorded provider home no longer resolves to the same directory')
        return home
    if row.get('peer_key'):
        from .registry import Registry
        return Path(Registry(journal).get(row['peer_key'],live=False)['home'])
    return provider_home(row['provider'])
