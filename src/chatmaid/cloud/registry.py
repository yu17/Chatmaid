"""Pick a cloud backend by name from the configuration."""

from chatmaid.cloud.base import CloudBackend, CloudError
from chatmaid.cloud.claude import ClaudeBackend
from chatmaid.cloud.codex import CodexBackend
from chatmaid.config import Config


def make_backend(cfg: Config, name: str | None = None) -> CloudBackend:
    name = name or cfg.cloud.default_backend
    if name == "codex":
        return CodexBackend(cfg.cloud.codex_model, timeout_s=cfg.cloud.timeout_s)
    if name == "claude":
        return ClaudeBackend(cfg.cloud.claude_model, timeout_s=cfg.cloud.timeout_s)
    raise CloudError(f"unknown cloud backend {name!r}")
