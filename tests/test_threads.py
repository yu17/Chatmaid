from chatmaid.threads import (
    ThreadIndex,
    normalize_subject,
    strip_subject_prefixes,
    thread_fallback_key,
)


def test_normalize_subject_strips_prefixes_in_two_languages():
    assert normalize_subject("Re: RE: 回复: Fwd:  Plan  B") == "plan b"


def test_thread_index_joins_by_references():
    t = ThreadIndex()
    t.add("<a@x>", [])
    t.add("<b@x>", ["<a@x>"])
    t.add("<c@x>", ["<b@x>"])
    assert t.key_for("<c@x>", ["<b@x>"], "fb") == t.key_for("<a@x>", [], "fb") == "<a@x>"


def test_thread_index_fallback_without_ids():
    assert ThreadIndex().key_for(None, [], "subj:x|a@x") == "subj:x|a@x"


def test_thread_fallback_key():
    assert thread_fallback_key("Re: Hi", ["b@x", "a@x", "a@x"]) == "subj:hi|a@x,b@x"


def test_thread_index_long_chain_is_fast():
    t = ThreadIndex()
    n = 20000
    t.add("id0", [])
    for i in range(1, n):
        t.add(f"id{i}", [f"id{i - 1}"])
    assert t.key_for(f"id{n - 1}", [f"id{n - 2}"], "fb") == "id0"


def test_thread_index_union_keeps_the_older_component_key():
    t = ThreadIndex()
    t.add("<m@x>", [])
    t.add("<z@x>", ["<y@x>"])
    assert t.key_for("<z@x>", [], "fb") == "<z@x>"  # the first id added to that component
    assert t.key_for("<z@x>", ["<m@x>"], "fb") == "<m@x>"  # <m@x>'s component is older
    assert t.key_for("<y@x>", [], "fb") == "<m@x>"


def test_thread_key_does_not_depend_on_ingest_order():
    root_first = ThreadIndex()
    assert root_first.key_for("<zzz@gmail.com>", [], "fb") == "<zzz@gmail.com>"
    assert root_first.key_for("<aaa@other.org>", ["<zzz@gmail.com>"], "fb") == "<zzz@gmail.com>"
    reply_first = ThreadIndex()
    assert reply_first.key_for("<aaa@other.org>", ["<zzz@gmail.com>"], "fb") == "<aaa@other.org>"
    assert reply_first.key_for("<zzz@gmail.com>", [], "fb") == "<aaa@other.org>"


def test_thread_index_replay_reproduces_keys():
    adds = [
        ("<c@x>", ["<b@x>"]), ("<a@x>", []), ("<b@x>", ["<a@x>"]),
        ("<d@x>", ["<zz@x>"]), ("<e@x>", ["<d@x>", "<c@x>"]), ("<f@x>", []),
    ]
    live = ThreadIndex()
    for message_id, refs in adds:
        live.key_for(message_id, refs, "fb")
    replay = ThreadIndex()
    for message_id, refs in adds:
        replay.add(message_id, refs)
    keys = [replay.key_for(m, [], "fb") for m, _ in adds]
    assert keys == [live.key_for(m, [], "fb") for m, _ in adds]
    assert keys == ["<c@x>"] * 5 + ["<f@x>"]


def test_thread_index_keys_of_ignores_unknown_ids():
    t = ThreadIndex()
    t.add("<b@x>", [])
    t.add("<a@x>", [])
    assert t.keys_of(["<a@x>", "<b@x>", "<nope@x>"]) == ["<b@x>", "<a@x>"]
    assert t.keys_of([]) == [] and t.key_for("<nope@x>", [], "fb") == "<nope@x>"


def test_strip_subject_prefixes_keeps_case():
    assert strip_subject_prefixes("Re: 回复:  Plan  B ") == "Plan B"
    assert normalize_subject("Re: Plan B") == "plan b"
