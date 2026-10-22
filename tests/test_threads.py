from chatmaid.threads import ThreadIndex, normalize_subject, thread_fallback_key


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


def test_thread_index_union_of_components_yields_smaller_id():
    t = ThreadIndex()
    t.add("<m@x>", [])
    t.add("<z@x>", ["<y@x>"])
    assert t.key_for("<z@x>", [], "fb") == "<y@x>"
    assert t.key_for("<z@x>", ["<m@x>"], "fb") == "<m@x>"
