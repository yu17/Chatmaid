from chatmaid.connectors.evolution.sources import EvolutionAccount
from chatmaid.identity import OwnerIdentity
from chatmaid.model import Address


def test_owner_identity_from_accounts():
    acct = EvolutionAccount(uid="a", display_name="", backend="imapx", enabled=True, identity_uid="i", identity_addresses=("Me@X.org",), sent_folder=None, drafts_folder=None)
    o = OwnerIdentity.from_accounts([acct], extra=["me2@y.org"], aliases=["Jiaqi"])
    assert o.matches(Address("", "me@x.org")) and o.matches(Address("", "me2@y.org")) and not o.matches(Address("", "x@x.org"))
    assert o.aliases == frozenset({"jiaqi"})
