"""Evolution connector: reads Evolution's mail cache and the local Maildir Sent."""

import logging
import mailbox
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from chatmaid.config import EmailConfig
from chatmaid.connectors.evolution import rfc822
from chatmaid.connectors.evolution.cache import EvolutionCache, FolderInfo, MessageRow
from chatmaid.connectors.evolution.rfc822 import ParsedMail, parse_addresses
from chatmaid.connectors.evolution.sources import EvolutionAccount
from chatmaid.identity import OwnerIdentity
from chatmaid.model import Address, NormalizedMessage, SourceRef, make_preview
from chatmaid.triage.rules import is_bulk

log = logging.getLogger(__name__)

LOCAL_ACCOUNT = "local"
LOCAL_FOLDER = "Sent"
_INBOX_NAMES = ("INBOX", "Inbox", "收件箱")
_SPAM_MARKS = ("Spam", "Junk", "垃圾")
_TRASH_MARKS = ("Trash", "Deleted", "已删除")


def _labels(folder_name: str) -> tuple[str, ...]:
    if any(m in folder_name for m in _SPAM_MARKS):
        return ("spam",)
    if any(m in folder_name for m in _TRASH_MARKS):
        return ("trash",)
    return ()


def _order_folders(
    folders: list[FolderInfo], sent: str | None, skip: tuple[str, ...]
) -> list[FolderInfo]:
    kept = [f for f in folders if f.name not in skip]
    sent_f = [f for f in kept if sent is not None and f.name == sent]
    inbox = [f for f in kept if f.name in _INBOX_NAMES and f not in sent_f][:1]
    taken = {f.folder_id for f in sent_f + inbox}
    rest = sorted((f for f in kept if f.folder_id not in taken), key=lambda f: f.name)
    return sent_f + inbox + rest


class EvolutionConnector:
    name = "evolution"

    def __init__(self, cfg: EmailConfig, accounts: list[EvolutionAccount], owner: OwnerIdentity):
        self._cfg = cfg
        self._accounts = list(accounts)
        self._owner = owner
        self._caches: dict[str, EvolutionCache] = {}

    @property
    def owner(self) -> OwnerIdentity:
        return self._owner

    @property
    def accounts(self) -> list[EvolutionAccount]:
        return list(self._accounts)

    # -- resources -------------------------------------------------------
    def _cache(self, account_uid: str) -> EvolutionCache:
        if account_uid not in self._caches:
            self._caches[account_uid] = EvolutionCache(self._cfg.evolution_cache_dir, account_uid)
        return self._caches[account_uid]

    def close(self) -> None:
        for c in self._caches.values():
            c.close()
        self._caches.clear()

    def _folders(self, account: EvolutionAccount) -> list[FolderInfo]:
        return _order_folders(
            self._cache(account.uid).folders(), account.sent_folder, self._cfg.skip_folders
        )

    def _local_dir(self) -> Path | None:
        p = self._cfg.local_maildir_sent
        return p if p is not None and Path(p).exists() else None

    # -- building --------------------------------------------------------
    def _from_row(
        self, account: EvolutionAccount, cache: EvolutionCache, folder: FolderInfo, row: MessageRow
    ) -> NormalizedMessage:
        is_sent = account.sent_folder is not None and folder.name == account.sent_folder
        ref = SourceRef("evolution", account.uid, folder.name, row.uid)
        body = cache.body_path(folder, row.uid)
        if body is None:
            senders = parse_addresses(row.mail_from)
            sender = senders[0] if senders else Address("", "")
            return NormalizedMessage(
                ref=ref, message_id=None, in_reply_to=None, references=(), sender=sender,
                recipients=parse_addresses(row.mail_to) + parse_addresses(row.mail_cc),
                sent_at=row.dsent or row.dreceived, subject=row.subject, text="", preview="",
                has_attachments=False, attachments=(), size=row.size,
                is_owner=self._owner.matches(sender) or is_sent, bulk=False,
                labels=_labels(folder.name), body_missing=True,
            )
        parsed = rfc822.parse_file(body)
        return self._from_parsed(
            ref, parsed, row.dsent or row.dreceived, row.size, parsed.subject or row.subject,
            is_sent, _labels(folder.name),
        )

    def _from_parsed(
        self, ref: SourceRef, parsed: ParsedMail, fallback_date: int, size: int, subject: str,
        force_owner: bool, labels: tuple[str, ...],
    ) -> NormalizedMessage:
        return NormalizedMessage(
            ref=ref, message_id=parsed.message_id, in_reply_to=parsed.in_reply_to,
            references=parsed.references, sender=parsed.from_, recipients=parsed.to + parsed.cc,
            sent_at=parsed.date if parsed.date else fallback_date, subject=subject,
            text=parsed.text, preview=make_preview(parsed.text),
            has_attachments=bool(parsed.attachments), attachments=parsed.attachments, size=size,
            is_owner=self._owner.matches(parsed.from_) or force_owner, bulk=is_bulk(parsed),
            labels=labels,
        )

    def _local_message(self, key: str, data: bytes) -> NormalizedMessage:
        ref = SourceRef("evolution", LOCAL_ACCOUNT, LOCAL_FOLDER, key)
        parsed = rfc822.parse_bytes(data)
        return self._from_parsed(ref, parsed, 0, len(data), parsed.subject, True, ())

    # -- Connector protocol ----------------------------------------------
    def iter_messages(
        self, *, since: int | None = None, after: dict[str, int] | None = None
    ) -> Iterator[NormalizedMessage]:
        after = after or {}
        for account in self._accounts:
            cache = self._cache(account.uid)
            seen: set[str] = set()
            for folder in self._folders(account):
                try:
                    for row in cache.iter_rows(folder, after_uid=after.get(f"{account.uid}/{folder.name}")):
                        msg = self._from_row(account, cache, folder, row)
                        if msg.message_id is not None:
                            if msg.message_id in seen:
                                continue
                            seen.add(msg.message_id)
                        if since is not None and msg.sent_at < since:
                            continue
                        yield msg
                except sqlite3.OperationalError as e:
                    log.warning("skipping %s/%s: %s", account.uid, folder.name, e)
        local = self._local_dir()
        if local is not None:
            mbox = mailbox.Maildir(local, factory=None, create=False)
            try:
                seen_local: set[str] = set()
                for key in mbox.iterkeys():
                    msg = self._local_message(key, mbox.get_bytes(key))
                    if msg.message_id is not None:
                        if msg.message_id in seen_local:
                            continue
                        seen_local.add(msg.message_id)
                    if since is not None and msg.sent_at < since:
                        continue
                    yield msg
            finally:
                mbox.close()

    def fetch(self, ref: SourceRef) -> NormalizedMessage:
        if ref.account == LOCAL_ACCOUNT:
            local = self._local_dir()
            if local is None:
                raise KeyError(ref.key())
            mbox = mailbox.Maildir(local, factory=None, create=False)
            try:
                return self._local_message(ref.uid, mbox.get_bytes(ref.uid))
            except KeyError:
                raise KeyError(ref.key()) from None
            finally:
                mbox.close()
        account = next((a for a in self._accounts if a.uid == ref.account), None)
        if account is None:
            raise KeyError(ref.key())
        cache = self._cache(account.uid)
        folder = next((f for f in cache.folders() if f.name == ref.folder), None)
        if folder is None:
            raise KeyError(ref.key())
        row = cache.row(folder, ref.uid)
        if row is None:
            raise KeyError(ref.key())
        return self._from_row(account, cache, folder, row)

    def scan_new(self, state: dict[str, int]) -> tuple[list[SourceRef], dict[str, int]]:
        new_state = dict(state)
        refs: list[SourceRef] = []
        for account in self._accounts:
            cache = self._cache(account.uid)
            for folder in self._folders(account):
                key = f"{account.uid}/{folder.name}"
                try:
                    rows = list(cache.iter_rows(folder, after_uid=state.get(key)))
                    top = cache.max_uid(folder)
                except sqlite3.OperationalError as e:
                    log.warning("skipping %s: %s", key, e)
                    continue
                refs.extend(SourceRef("evolution", account.uid, folder.name, r.uid) for r in rows)
                if top is not None:
                    new_state[key] = top
        return refs, new_state

    def folder_stats(
        self, account_uid: str | None = None
    ) -> Iterator[tuple[str, str, int, int, int, int]]:
        """Per account and folder: (account, folder, rows, bodies, owner, bulk). No dedup."""
        for account in self._accounts:
            if account_uid is not None and account.uid != account_uid:
                continue
            cache = self._cache(account.uid)
            for folder in self._folders(account):
                rows = bodies = owner = bulk = 0
                try:
                    for row in cache.iter_rows(folder):
                        msg = self._from_row(account, cache, folder, row)
                        rows += 1
                        bodies += not msg.body_missing
                        owner += msg.is_owner
                        bulk += msg.bulk
                except sqlite3.OperationalError as e:
                    log.warning("skipping %s/%s: %s", account.uid, folder.name, e)
                    continue
                yield account.uid, folder.name, rows, bodies, owner, bulk
