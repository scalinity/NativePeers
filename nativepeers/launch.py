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
