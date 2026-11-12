"""Helpers shared by the command groups and the web UI."""

from datetime import datetime, timezone

from chatmaid.cloud.registry import make_backend
from chatmaid.config import ConfigError, read_token  # noqa: F401  (re-exported; callers use it through this module)
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import MissingAccount, load_accounts
from chatmaid.identity import OwnerIdentity
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import CANNED, FakeLLM
from chatmaid.llm.lmstudio import LMStudioClient
from chatmaid.reply.flow import ReplyFlow
from chatmaid.store import Store


def lmstudio_client(cfg, token: str) -> LMStudioClient:
    lm = cfg.lmstudio
    return LMStudioClient(lm.base_url, token, timeout_s=lm.timeout_s, think=lm.think)


def parse_since(text: str) -> int:
    day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(day.timestamp())


def open_store(cfg) -> Store:
    cfg.data.dir.mkdir(parents=True, exist_ok=True)
    return Store(cfg.index_path, embedding_dim=cfg.lmstudio.embedding_dim)


class NoLLM:
    """Stands in when no requested pass talks to a model (ingest only): no token is needed."""

    def complete(self, **_) -> str:
        raise LLMError("this run was started without an LM Studio client")

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMError("this run was started without an LM Studio client")



def day(ts: int | None) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%Y-%m-%d")



def open_flow(
    cfg, *, need_llm: bool, fake_llm: bool = False
) -> tuple[Store, EvolutionConnector, ReplyFlow]:
    """Build store, connector and ReplyFlow; ConfigError (missing token) propagates before anything opens."""
    if fake_llm:
        llm = FakeLLM(dict(CANNED))
    elif not need_llm:
        llm = NoLLM()
    else:
        llm = lmstudio_client(cfg, read_token(cfg))
    try:
        accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    except MissingAccount as e:
        raise ConfigError(str(e)) from e
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    connector = EvolutionConnector(cfg.email, accounts, owner)
    try:
        store = open_store(cfg)
    except BaseException:
        connector.close()
        raise
    flow = ReplyFlow(store, connector, llm, cfg, backend_factory=lambda name: make_backend(cfg, name))
    return store, connector, flow
