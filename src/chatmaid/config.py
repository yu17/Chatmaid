"""Configuration loading. Defaults mirror config.example.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class DataConfig:
    dir: Path
    hot_window_days: int


@dataclass(frozen=True)
class OwnerConfig:
    name: str
    aliases: tuple[str, ...]
    extra_addresses: tuple[str, ...]


@dataclass(frozen=True)
class EmailConfig:
    evolution_config_dir: Path
    evolution_cache_dir: Path
    accounts: tuple[str, ...]
    skip_folders: tuple[str, ...]
    local_maildir_sent: Path | None


@dataclass(frozen=True)
class LMStudioConfig:
    base_url: str
    token_file: Path
    model: str  # the one local model for every call
    think: bool  # reasoning on (schema enforced) or off (empty think block prefilled)
    temperature: float
    model_embedding: str
    embedding_dim: int
    timeout_s: int


@dataclass(frozen=True)
class DraftConfig:
    candidates: int
    recent_messages: int
    memories: int
    message_chars: int
    max_age_days: int


@dataclass(frozen=True)
class CloudConfig:
    default_backend: str
    codex_model: str
    claude_model: str
    timeout_s: int
    redact_amounts: bool


@dataclass(frozen=True)
class Config:
    data: DataConfig
    owner: OwnerConfig
    email: EmailConfig
    lmstudio: LMStudioConfig
    draft: DraftConfig
    cloud: CloudConfig

    @property
    def index_path(self) -> Path:
        return self.data.dir / "index.sqlite"


_DEFAULT_ACCOUNTS = (
    "2bb8bc493ea9a64e270c733fd29ed16b853eefcb",
    "8a19039da6e94f226343e4c2ad4bf97d031a7f8e",
    "f71772b19449d2cd7282d5176e2f4178935719e7",
    "19f397267c6bc963a9feca8074db17099796fbfd",
    "5cf1ac3f38477a31b4698179ffc7c571355c57af",
)

_DEFAULTS: dict[str, dict[str, Any]] = {
    "data": {"dir": "data", "hot_window_days": 365},
    "owner": {"name": "Jiaqi", "aliases": ["jiaqi", "fishy"], "extra_addresses": []},
    "email": {
        "evolution_config_dir": "~/.config/evolution/sources",
        "evolution_cache_dir": "~/.cache/evolution/mail",
        "accounts": list(_DEFAULT_ACCOUNTS),
        "skip_folders": ["[Gmail]/All Mail", "[Gmail]/Important", "[Gmail]/Starred"],
        "local_maildir_sent": "~/.local/share/evolution/mail/local/.Sent",
    },
    "lmstudio": {
        "base_url": "http://127.0.0.1:40090/v1",
        "token_file": "LMStudio_token.txt",
        "model": "qwen3.5-122b-a10b",
        "think": True,
        "temperature": 0.6,
        "model_embedding": "text-embedding-qwen3-embedding-0.6b",
        "embedding_dim": 1024,
        "timeout_s": 600,
    },
    "draft": {
        "candidates": 2,
        "recent_messages": 12,
        "memories": 8,
        "message_chars": 6000,
        "max_age_days": 14,
    },
    "cloud": {
        "default_backend": "codex",
        "codex_model": "gpt-6-astra",
        "claude_model": "",
        "timeout_s": 300,
        "redact_amounts": False,
    },
}


def _resolve(value: str, root: Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else root / p


def load(path: Path, *, project_root: Path | None = None) -> Config:
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid config {path}: {e}") from e

    if project_root is None:
        if path.parent.name == "data":
            project_root = path.resolve().parent.parent
        else:
            project_root = Path.cwd()

    def section(name: str) -> dict[str, Any]:
        return {**_DEFAULTS[name], **raw.get(name, {})}

    d, o, e, lm = section("data"), section("owner"), section("email"), section("lmstudio")
    dr, cl = section("draft"), section("cloud")
    for stale in ("model_small", "model_main", "model_heavy"):
        if stale in raw.get("lmstudio", {}):
            raise ConfigError(
                f"lmstudio.{stale} was replaced by lmstudio.model, the one model for every call"
            )
    if "model_role" in raw.get("draft", {}):
        raise ConfigError("draft.model_role was removed: every call uses lmstudio.model")
    if not isinstance(lm["think"], bool):
        raise ConfigError(f"lmstudio.think must be true or false, got {lm['think']!r}")
    if isinstance(lm["temperature"], bool) or not isinstance(lm["temperature"], (int, float)):
        raise ConfigError(f"lmstudio.temperature must be a number, got {lm['temperature']!r}")
    if cl["default_backend"] not in ("codex", "claude"):
        raise ConfigError(
            f"cloud.default_backend must be 'codex' or 'claude', got {cl['default_backend']!r}"
        )
    sent = e["local_maildir_sent"]
    return Config(
        data=DataConfig(dir=_resolve(d["dir"], project_root), hot_window_days=d["hot_window_days"]),
        owner=OwnerConfig(
            name=o["name"],
            aliases=tuple(o["aliases"]),
            extra_addresses=tuple(o["extra_addresses"]),
        ),
        email=EmailConfig(
            evolution_config_dir=_resolve(e["evolution_config_dir"], project_root),
            evolution_cache_dir=_resolve(e["evolution_cache_dir"], project_root),
            accounts=tuple(e["accounts"]),
            skip_folders=tuple(e["skip_folders"]),
            local_maildir_sent=_resolve(sent, project_root) if sent else None,
        ),
        lmstudio=LMStudioConfig(
            base_url=lm["base_url"],
            token_file=_resolve(lm["token_file"], project_root),
            model=lm["model"],
            think=lm["think"],
            temperature=float(lm["temperature"]),
            model_embedding=lm["model_embedding"],
            embedding_dim=lm["embedding_dim"],
            timeout_s=lm["timeout_s"],
        ),
        draft=DraftConfig(
            candidates=dr["candidates"],
            recent_messages=dr["recent_messages"],
            memories=dr["memories"],
            message_chars=dr["message_chars"],
            max_age_days=dr["max_age_days"],
        ),
        cloud=CloudConfig(
            default_backend=cl["default_backend"],
            codex_model=cl["codex_model"],
            claude_model=cl["claude_model"],
            timeout_s=cl["timeout_s"],
            redact_amounts=cl["redact_amounts"],
        ),
    )


def read_token(cfg: Config) -> str:
    try:
        return cfg.lmstudio.token_file.read_text().strip()
    except FileNotFoundError as e:
        raise ConfigError(f"LM Studio token file not found: {cfg.lmstudio.token_file}") from e
