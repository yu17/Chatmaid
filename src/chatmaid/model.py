"""Normalized message model shared by connectors and the store."""

import json
from dataclasses import dataclass

PREVIEW_LENGTH = 200


@dataclass(frozen=True)
class SourceRef:
    source: str
    account: str
    folder: str
    uid: str

    def key(self) -> str:
        return json.dumps([self.source, self.account, self.folder, self.uid], ensure_ascii=False)

    @classmethod
    def from_key(cls, s: str) -> "SourceRef":
        source, account, folder, uid = json.loads(s)
        return cls(source, account, folder, uid)


@dataclass(frozen=True)
class Address:
    name: str
    email: str  # already lowercased by the caller


@dataclass(frozen=True)
class NormalizedMessage:
    ref: SourceRef
    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    sender: Address
    recipients: tuple[Address, ...]
    sent_at: int
    subject: str
    text: str
    preview: str
    has_attachments: bool
    attachments: tuple[str, ...]
    size: int
    is_owner: bool
    bulk: bool
    labels: tuple[str, ...]
    body_missing: bool = False
    kind: str = "email"


def make_preview(text: str) -> str:
    return " ".join(text.split())[:PREVIEW_LENGTH]
