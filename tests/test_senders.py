import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from chatmaid.model import Address


@pytest.fixture
def senders_env(store, sample_message):
    me = store.upsert_identity("email", "me@x.org", "Me", is_owner=True)
    ids = {"me": me}
    for who in ("ann", "shop", "rare", "li"):
        ids[who] = store.upsert_identity("email", f"{who}@x.org", who.title(), is_owner=False)
    n = iter(range(100, 200))

    def conv(key, *rows):  # rows: (sender_name, is_owner, bulk)
        cid = store.upsert_conversation("email", key, key, "thread")
        for i, (who, is_owner, bulk) in enumerate(rows):
            msg = replace(sample_message, ref=replace(sample_message.ref, uid=str(next(n))),
                          message_id=None, sender=Address(who, f"{who}@x.org"),
                          sent_at=1_700_000_000 + i, is_owner=is_owner, bulk=bulk)
            store.add_participant(cid, store.contact_id_of(ids[who]))
            store.upsert_message(cid, msg, ids[who])
        return cid

    ann_cids = [conv(f"ann{i}", ("ann", False, False), ("me", True, False)) for i in range(2)]
    shop_cids = [conv(f"shop{i}", ("shop", False, True)) for i in range(5)]
    rare_cid = conv("rare", ("rare", False, False))
    owner_only_cid = conv("own", ("me", True, False))
    store.add_participant(owner_only_cid, store.contact_id_of(ids["li"]))
    return SimpleNamespace(store=store, ids=ids, ann_cids=ann_cids, shop_cids=shop_cids,
                           rare_cid=rare_cid, owner_only_cid=owner_only_cid)


def test_refresh_sender_stats_counts_and_flags(senders_env):
    st = senders_env.store
    assert st.refresh_sender_stats() == 3
    ann, shop, rare = (st.sender_stat(senders_env.ids[a]) for a in ("ann", "shop", "rare"))
    assert (ann["messages"], ann["conversations"], ann["known"], ann["answered"], ann["bulk_share"]) == (2, 2, 1, 1, 0.0)
    assert (shop["messages"], shop["known"], shop["answered"], shop["bulk_share"]) == (5, 0, 0, 1.0)
    assert rare["messages"] == 1 and st.sender_stat(senders_env.ids["me"]) is None


def test_digest_queue_groups_and_order(senders_env):
    st = senders_env.store
    st.refresh_sender_stats()
    rows = st.digest_queue(exclude_bulk=False)
    groups = [r["queue_group"] for r in rows]
    assert groups == sorted(groups) and groups[:2] == [1, 1]
    assert {r["queue_group"] for r in rows if r["id"] in senders_env.shop_cids} == {5}
    assert next(r["queue_group"] for r in rows if r["id"] == senders_env.owner_only_cid) == 3
    assert next(r["queue_group"] for r in rows if r["id"] == senders_env.rare_cid) == 4
    assert all(r["queue_group"] == 5 for r in st.digest_queue(exclude_bulk=False, group=5))
    assert st.digest_queue(exclude_bulk=True) and all(not r["bulk"] for r in st.digest_queue(exclude_bulk=True))


def test_sender_context_for_owner_only_conversation(senders_env):
    st = senders_env.store
    st.refresh_sender_stats()
    assert st.sender_context(senders_env.owner_only_cid) is None
    assert st.sender_context(senders_env.ann_cids[0])["identity_id"] == senders_env.ids["ann"]


def test_learned_bulk_needs_min_conversations_all_junk(senders_env):
    st = senders_env.store
    for cid in senders_env.shop_cids:
        st.set_conversation_summary(cid, "ad", category="promotional")
    st.refresh_sender_stats()
    assert json.loads(st.sender_stat(senders_env.ids["shop"])["categories_json"]) == {"promotional": 5}
    assert st.learned_bulk(senders_env.ids["shop"], min_conversations=5)
    assert not st.learned_bulk(senders_env.ids["shop"], min_conversations=6)
    st.set_conversation_summary(senders_env.shop_cids[0], "x", category="event")  # COALESCE keeps promotional
    st.set_conversation_category(senders_env.shop_cids[0], "event")
    st.refresh_sender_stats()
    assert not st.learned_bulk(senders_env.ids["shop"], min_conversations=5)
    assert not st.learned_bulk(None, min_conversations=1)
    assert st.sender_stats(limit=10)[0]["address"] == "shop@x.org"
    assert st.category_counts() == {"promotional": 4, "event": 1}
