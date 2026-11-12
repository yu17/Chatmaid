import dataclasses

import pytest

from chatmaid import views
from chatmaid.model import SourceRef

SENT = "[Gmail]/Sent Mail"


def ids(rows, keep=None):
    return [r.message_id for r in rows if keep is None or r.message_id in keep]


def test_inbox_filters(flow_env):
    env = flow_env
    env.llm.responses["triage"] = '{"category":"informational","tier":"work","reason":"fyi"}'
    info = env.arrive("100", sender="Bob <bob@x.org>", subject="fyi", date=1_700_000_100)
    env.llm.responses["triage"] = '{"category":"needs_reply","tier":"work","reason":"asks"}'
    need = env.arrive("101", sender="Ann <ann@x.org>", subject="meeting", date=1_700_000_200)
    promo = env.arrive("102", sender="Pat <pat@x.org>", subject="sale", date=1_700_000_300)
    env.store.set_conversation_category(promo["conversation_id"], "promotional")
    labels = views.account_labels(env.connector)
    assert labels == {"a1": "me@x.org"}
    store = env.store
    mine = {info["id"], need["id"], promo["id"]}

    default = views.inbox(store, account_labels=labels)
    assert ids(default, mine) == [need["id"], info["id"]]
    row = next(r for r in default if r.message_id == need["id"])
    assert (row.sender, row.subject, row.account, row.triage) == (
        "Ann", "meeting", "me@x.org", "needs_reply"
    )
    assert row.draft_id is not None
    assert next(r for r in default if r.message_id == info["id"]).draft_id is None
    assert ids(views.inbox(store, filter="all", account_labels=labels), mine) == [
        promo["id"], need["id"], info["id"]
    ]
    nr = views.inbox(store, filter="needs_reply", account_labels={})
    assert ids(nr, mine) == [promo["id"], need["id"]] and nr[0].account == "a1"
    assert ids(views.inbox(store, filter="drafts", account_labels=labels), mine) == [
        promo["id"], need["id"]
    ]
    assert ids(views.inbox(store, filter="promotional", account_labels=labels), mine) == [promo["id"]]
    paged = views.inbox(store, filter="all", before=need["sent_at"], account_labels=labels)
    assert ids(paged, mine) == [info["id"]]
    assert len(views.inbox(store, filter="all", limit=1, account_labels=labels)) == 1


def test_inbox_excludes_owner_messages(flow_env):
    env = flow_env
    env.arrive("100")
    own = env.arrive("200", sender="me@x.org", to="ann@x.org", folder=SENT, in_reply_to="<m100@x>")
    got = ids(views.inbox(env.store, filter="all", account_labels={}))
    assert own["id"] not in got


def test_message_view_falls_back_to_preview_when_gone(digested_env, monkeypatch):
    env = digested_env
    row = env.store._conn.execute("SELECT * FROM message WHERE is_owner = 0 LIMIT 1").fetchone()
    v = views.message_view(env.store, env.connector, row["id"])
    assert v.available and v.full_text and v.sender and isinstance(v.recipients, list)
    assert v.own_text

    def gone(ref):
        raise KeyError(ref.key())

    monkeypatch.setattr(env.connector, "fetch", gone)
    v = views.message_view(env.store, env.connector, row["id"])
    assert not v.available and v.preview == row["preview"]
    assert v.full_text == "" and v.own_text == ""
    with pytest.raises(KeyError):
        views.message_view(env.store, env.connector, 99999)


def test_conversation_view_pages_newest_first(store, sample_message):
    ident = store.upsert_identity("evolution", "sender@x.org", "Sender")
    conv = store.upsert_conversation("evolution", "k1", "Topic", "email")
    for i in range(60):
        m = dataclasses.replace(
            sample_message, ref=SourceRef("evolution", "acct1", "INBOX", str(i)),
            message_id=f"<m{i}@x>", sent_at=1_700_000_000 + i,
        )
        store.upsert_message(conv, m, ident)
    v = views.conversation_view(store, conv)
    assert len(v.messages) == 50
    assert v.messages[0].sent_at == 1_700_000_059
    assert v.older_before == v.messages[-1].sent_at == 1_700_000_010
    assert v.title == "Topic" and v.messages[0].sender == "Sender"
    older = views.conversation_view(store, conv, before=v.older_before)
    assert len(older.messages) == 10 and older.older_before is None


def test_status_view_reads_counts_failures_and_untriaged(digested_env):
    store = digested_env.store
    store.log_event("reply", "draft_failed", "r", {"error": "x"})
    store.log_event("reply", "triage", "r", {})
    v = views.status_view(store, max_age_days=14, now=1_700_000_000)
    assert v["counts"] == store.counts()
    assert v["with_summary"] + v["without_summary"] == v["counts"]["conversations"]
    assert len(v["recent_events"]) == 2
    assert [e["kind"] for e in v["failures"]] == ["draft_failed"]
    assert v["untriaged"] == []
