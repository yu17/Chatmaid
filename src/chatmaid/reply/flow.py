"""Reply flow: triage incoming mail, draft replies, resolve drafts from the owner's own mail."""

import logging
import time
from collections.abc import Callable

from chatmaid.cloud.base import CloudBackend
from chatmaid.cloud.registry import make_backend
from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.llm.base import LLM, LLMError
from chatmaid.model import NormalizedMessage
from chatmaid.reply.actions import DraftActionsMixin
from chatmaid.reply.cloudflow import CloudMixin, CloudProposal
from chatmaid.reply.incoming import IncomingMixin
from chatmaid.reply.owner import OwnerMixin
from chatmaid.store import Store

__all__ = ["CloudProposal", "ReplyFlow"]

log = logging.getLogger(__name__)


class ReplyFlow(IncomingMixin, OwnerMixin, DraftActionsMixin, CloudMixin):
    def __init__(
        self,
        store: Store,
        connector: Connector,
        llm: LLM,
        cfg: Config,
        *,
        now: Callable[[], int] | None = None,
        backend_factory: Callable[[str | None], CloudBackend] | None = None,
    ) -> None:
        self.store = store
        self.connector = connector
        self.llm = llm
        self.cfg = cfg
        self.now = now or (lambda: int(time.time()))
        self.backend_factory = backend_factory or (lambda name: make_backend(cfg, name))

    # -- watcher callback ------------------------------------------------

    def dispatch(self, route: str, conversation_id: int, msg: NormalizedMessage) -> None:
        owner = route == "owner"
        try:
            if owner:
                self.on_owner_message(conversation_id, msg)
            else:
                self.on_incoming(conversation_id, msg)
        except LLMError as e:
            kind = "owner_failed" if owner else "draft_failed"
            row = self.store.message_by_ref(msg.ref.key())
            message_id = row["id"] if row is not None else None
            retry = f"; retry: chatmaid draft run --message {message_id}" if not owner else ""
            log.warning("reply flow %s for message %s: %s%s", kind, message_id, e, retry)
            self.store.log_event(
                "reply", kind, msg.ref.key(), {"error": str(e), "message_id": message_id}
            )
