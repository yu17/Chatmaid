"""Discover Evolution mail accounts from its GKeyFile-format ``*.source`` files."""

import argparse
import configparser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from chatmaid.config import load as load_config


class MissingAccount(Exception):
    def __init__(self, uid: str):
        super().__init__(f"no Evolution .source file for account {uid!r}")
        self.uid = uid


@dataclass(frozen=True)
class EvolutionAccount:
    uid: str
    display_name: str
    backend: str
    enabled: bool
    identity_uid: str | None
    identity_addresses: tuple[str, ...]
    sent_folder: str | None
    drafts_folder: str | None


def parse_folder_uri(uri: str) -> tuple[str, str]:
    """Split ``folder://<uid>/<percent-encoded path>`` into (uid, decoded path)."""
    rest = uri.removeprefix("folder://")
    uid, _, path = rest.partition("/")
    return uid, unquote(path)


def _read(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # GKeyFile keys are case-sensitive; keep them as written
    cp.read(path, encoding="utf-8")
    return cp


def _folder(cp: configparser.ConfigParser, group: str, key: str) -> str | None:
    value = cp.get(group, key, fallback="").strip()
    return parse_folder_uri(value)[1] if value else None


def load_accounts(config_dir: Path, uids: Sequence[str]) -> list[EvolutionAccount]:
    config_dir = Path(config_dir)
    accounts = []
    for uid in uids:
        path = config_dir / f"{uid}.source"
        if not path.is_file():
            raise MissingAccount(uid)
        acct = _read(path)
        identity_uid = acct.get("Mail Account", "IdentityUid", fallback="").strip() or None
        addresses: tuple[str, ...] = ()
        sent = drafts = None
        identity_path = config_dir / f"{identity_uid}.source" if identity_uid else None
        if identity_path is not None and identity_path.is_file():
            ident = _read(identity_path)
            address = ident.get("Mail Identity", "Address", fallback="").strip().lower()
            addresses = (address,) if address else ()
            sent = _folder(ident, "Mail Submission", "SentFolder")
            drafts = _folder(ident, "Mail Composition", "DraftsFolder")
        accounts.append(
            EvolutionAccount(
                uid=uid,
                display_name=acct.get("Data Source", "DisplayName", fallback=""),
                backend=acct.get("Mail Account", "BackendName", fallback=""),
                enabled=acct.getboolean("Data Source", "Enabled", fallback=True),
                identity_uid=identity_uid,
                identity_addresses=addresses,
                sent_folder=sent,
                drafts_folder=drafts,
            )
        )
    return accounts


def _accounts_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    for a in load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts):
        print(
            f"{a.uid[:8]}  {a.backend}  {a.display_name}  "
            f"sent={a.sent_folder or '-'}  identities={len(a.identity_addresses)}"
        )
    return 0


def register_accounts(subparsers) -> None:
    p = subparsers.add_parser("accounts", help="list configured Evolution mail accounts")
    p.set_defaults(handler=_accounts_command)
