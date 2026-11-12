"""What the web app needs from the outside world, as factories so tests can swap them."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from chatmaid.cli.common import lmstudio_client, open_store
from chatmaid.cloud.base import CloudBackend
from chatmaid.cloud.registry import make_backend
from chatmaid.config import Config, ConfigError, read_token
from chatmaid.connectors.base import Connector
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import MissingAccount, load_accounts
from chatmaid.identity import OwnerIdentity
from chatmaid.llm.base import LLM
from chatmaid.reply.flow import ReplyFlow
from chatmaid.store import Store


@dataclass
class Services:
    cfg: Config
    open_store: Callable[[], Store]
    open_connector: Callable[[], Connector]
    make_llm: Callable[[], LLM]  # raises ConfigError without a token
    make_backend: Callable[[str | None], CloudBackend]

    @classmethod
    def from_config(cls, cfg: Config) -> "Services":
        try:
            accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
        except MissingAccount as e:
            raise ConfigError(str(e)) from e
        owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
        return cls(
            cfg=cfg,
            open_store=lambda: open_store(cfg),
            open_connector=lambda: EvolutionConnector(cfg.email, accounts, owner),
            make_llm=lambda: lmstudio_client(cfg, read_token(cfg)),
            make_backend=lambda name: make_backend(cfg, name),
        )

    @contextmanager
    def session(self) -> Iterator[tuple[Store, Connector]]:
        """A store and a connector, both closed on exit."""
        store = self.open_store()
        try:
            connector = self.open_connector()
            try:
                yield store, connector
            finally:
                getattr(connector, "close", lambda: None)()
        finally:
            store.close()

    def flow(self, store: Store, connector: Connector, *, model: bool = True) -> ReplyFlow:
        """A reply flow; model=False for actions that never call the model (no token needed)."""
        llm = self.make_llm() if model else None
        return ReplyFlow(store, connector, llm, self.cfg, backend_factory=self.make_backend)
