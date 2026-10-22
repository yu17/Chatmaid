"""Owner identity: which addresses count as "me"."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from chatmaid.connectors.evolution.sources import EvolutionAccount
from chatmaid.model import Address


def normalize_email(s: str) -> str:
    return s.strip().lower()


@dataclass(frozen=True)
class OwnerIdentity:
    addresses: frozenset[str]
    aliases: frozenset[str]

    @classmethod
    def from_accounts(
        cls,
        accounts: Iterable[EvolutionAccount],
        extra: Iterable[str],
        aliases: Iterable[str],
    ) -> OwnerIdentity:
        addrs = {normalize_email(a) for acct in accounts for a in acct.identity_addresses}
        addrs.update(normalize_email(e) for e in extra)
        return cls(frozenset(addrs), frozenset(a.lower() for a in aliases))

    def matches(self, addr: Address) -> bool:
        return normalize_email(addr.email) in self.addresses
