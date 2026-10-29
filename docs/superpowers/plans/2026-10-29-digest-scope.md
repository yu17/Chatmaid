# Digest Scope, Categories, Style Samples and Owner-Reply Examples — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every conversation gets a summary and a model-assigned category in a queue that puts real threads first, with a progress line and clean stop/resume; the style profile and the drafter's examples come from the owner's own words; lookup can filter by category and return threads; live triage learns purely promotional senders.

**Architecture:** One shared quote stripper feeds the style pass, the digest renderer, routine C and the owner-example embeddings. The digest reads its queue from a no-model `sender_stat` pre-pass in the store, prepends one sender-context line to each chunk, and stores the category the `chunk_summary` prompt now returns. A `Progress` line and a `StopRequested` signal exception wrap the three model passes; every digest write already sits in a per-chunk transaction, so a stop costs at most the chunk in flight. Vectors move to a `vec0` table partitioned by kind so owner messages, summaries and memories are searched separately.

**Tech Stack:** Python 3.14, uv, sqlite3 + sqlite-vec 0.1.9 (partition keys; schema v3 via the migration ladder), `openai` SDK against LM Studio, stdlib `signal`, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-29-digest-scope-design.md` (sections 5.1 to 5.9). Builds on plans 1a and 1b (`docs/superpowers/plans/2026-10-22-*.md`), both merged.

## Global Constraints

- Everything in plan 1a's and 1b's Global Constraints still holds: read-only sources, no body copies in the index beyond 200-character previews (ruling R7), one model `lmstudio.model` for every call, thinking on, no sampling parameters and no `max_tokens` sent, token file never logged, stable thread keys, single writer lock.
- Thinking stays on for every digest call (spec section 4); no per-call think switch is added.
- Nothing is excluded from the digest by a rule: `InitOptions.skip_bulk` defaults to `False`, `init --skip-bulk` restores the old behaviour. Filtering is by stored category at query time.
- Events are kept only for categories `conversation` and `event`; facts only for `conversation`. The code drops the rest whatever the model returns.
- Summary length rule (120 words for `conversation`/`event`, 25 otherwise) is counted, never enforced: no truncation, no retry, no pending state. A word is a whitespace-separated token; 2.5 CJK characters count as one word.
- The first message of a conversation (earliest `sent_at`) is rendered whole; every later one is stripped; routine C strips the owner's new message.
- A stop (SIGINT/SIGTERM) loses at most the conversation in flight: every digest write is inside `store.transaction()`; the CLI exits 130 with "stopped; run the same command to resume".
- Progress goes to stderr: redrawn in place when stderr is a TTY, one line every 50 conversations otherwise.
- Schema changes only through `schema_v3.sql` (ruling R20); the v3 step drops `vec_embedding` and empties `embedding_item` so the embed pass re-embeds under the partitioned table; summaries and memories survive.
- Commit after every task with the message prefix shown; `uv run pytest -q -W error` passes before each commit.

## Review Focus

1. Ctrl-C lands inside a chunk's transaction (between `add_memories` and `set_checkpoint`): the transaction rolls back, no memory row survives without its checkpoint, the next run redoes that chunk. Pinned in Task 6 (`test_stop_inside_chunk_transaction_rolls_back`).
2. A reply whose body is entirely quoted history: it is dropped from the chunks, the conversation is still digested from the messages that remain, and a conversation whose first message is the only non-empty one still gets a summary. Pinned in Task 1 (`test_strip_quoted_keeps_first_and_drops_emptied`) and Task 4 (`test_digest_strips_quoted_history_except_first`).
3. A conversation with no non-owner sender, or whose sender has no identity row: the sender-context line falls back to the owner line and the digest proceeds. Pinned in Task 3 (`test_sender_context_for_owner_only_conversation`) and Task 4 (`test_digest_sender_context_line`).
4. An existing v2 index with summaries and vectors is opened by the new code: it upgrades, keeps the summaries, drops the vectors, and `embed` re-embeds everything. Pinned in Task 2 (`test_v3_upgrade_drops_vectors_and_keeps_summaries`).
5. The model answers a category outside the six, or returns events for a promotional mail: the summary is stored with a NULL category (counted as `unknown`) and the events are dropped. Pinned in Task 4 (`test_digest_invalid_category_is_null`, `test_digest_stores_category_and_drops_junk_memories`).

---

## File Structure

- Create `src/chatmaid/quoting.py`: `strip_reply_chrome` (moved from `reply/flow.py`) and `strip_quoted` over message lists.
- Create `src/chatmaid/store/schema_v3.sql`: `conversation.category`, `sender_stat`, vector table reset.
- Modify `src/chatmaid/store/db.py`: partitioned vectors, category, sender statistics, digest queue, learned-bulk lookup, listing helpers.
- Create `src/chatmaid/init/categories.py`: the category list, precedence, output rules, the sender-context line.
- Modify `src/chatmaid/init/chunking.py`: `count_words`.
- Modify `src/chatmaid/llm/prompts/chunk_summary.md`, `reply_draft.md`.
- Modify `src/chatmaid/init/pipeline.py`: queue, stripping, category, stats, progress, stop.
- Modify `src/chatmaid/init/passes.py`: stripped style samples, owner-message embeddings, progress, stop.
- Create `src/chatmaid/init/progress.py`: the progress line.
- Create `src/chatmaid/stop.py`: `StopRequested`, `stop_on_signal`.
- Modify `src/chatmaid/reply/flow.py`, `reply/context.py`, `triage/classify.py`, `lookup.py`, `cli.py`, `config.py`, `llm/fake.py`, `config.example.toml`, `README.md`.
- Tests: new `tests/test_quoting.py`, `tests/test_senders.py`, `tests/test_progress.py`, `tests/test_stop.py`; modified `tests/test_store.py`, `test_digest.py`, `test_style_embed.py`, `test_flow.py`, `test_context.py`, `test_lookup.py`, `test_triage.py`, `test_config.py`, `test_cli_read.py`, `conftest.py`.

---

### Task 1: Shared quote stripper

**Files:**
- Create: `src/chatmaid/quoting.py`
- Modify: `src/chatmaid/reply/flow.py:40-57` (remove `_ATTRIBUTION_RE` and `strip_reply_chrome`, import from `chatmaid.quoting`)
- Create: `tests/test_quoting.py`; Modify: `tests/test_flow.py:136-149` (delete `test_strip_reply_chrome_cases`, it moves)

**Interfaces:**
- Produces: `strip_reply_chrome(text: str) -> str` (behaviour unchanged from ruling R16: cut at the first attribution line, English or Chinese, the first `>` line, or a `-- `/`--` signature line); `strip_quoted(msgs: list[NormalizedMessage], *, keep_first: bool = True) -> list[NormalizedMessage]`: copies (`dataclasses.replace`) with stripped `text`; the first element stays whole when `keep_first`; elements whose stripped text is blank are dropped (the first is never dropped when kept whole). Callers pass messages oldest first.

- [ ] **Step 1: Write the failing tests** in `tests/test_quoting.py`: move the parametrized `test_strip_reply_chrome_cases` table verbatim (importing from `chatmaid.quoting`), and add:

```python
def test_strip_quoted_keeps_first_and_drops_emptied(sample_message):
    first = replace(sample_message, text="Can we meet?\n> old quote")
    reply = replace(sample_message, text="Yes.\n\nOn Mon, Ann wrote:\n> Can we meet?")
    only_quote = replace(sample_message, text="> Can we meet?\n> Yes.")
    out = strip_quoted([first, reply, only_quote])
    assert [m.text for m in out] == ["Can we meet?\n> old quote", "Yes.\n\n"]
    assert [m.text for m in strip_quoted([reply], keep_first=False)] == ["Yes.\n\n"]
    assert strip_quoted([only_quote], keep_first=False) == []
```

- [ ] **Step 2: Run** `uv run pytest tests/test_quoting.py -q`. Expected: FAIL, `ModuleNotFoundError: chatmaid.quoting`.
- [ ] **Step 3: Create `src/chatmaid/quoting.py`** with the regex and function moved from `flow.py` and `strip_quoted` as specified; make `flow.py` import `strip_reply_chrome` from it (line 153 keeps working).
- [ ] **Step 4: Run** `uv run pytest tests/test_quoting.py tests/test_flow.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `refactor: shared quote stripper in chatmaid.quoting`.

---

### Task 2: Schema v3: category, sender_stat, vectors partitioned by kind

**Files:**
- Create: `src/chatmaid/store/schema_v3.sql`
- Modify: `src/chatmaid/store/db.py:15-25` (`_COUNT_TABLES`), `:37` (`SCHEMA_VERSION = 3`), `:53-56` (vec table), `:146-192` (`merge_conversations`), `:205-223` (`set_conversation_summary`), `:244-268` (`conversations`), `:723-779` (embeddings)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `schema_v3.sql` =
  ```sql
  ALTER TABLE conversation ADD COLUMN category TEXT;
  CREATE INDEX idx_conversation_category ON conversation(category);
  CREATE TABLE sender_stat (
      identity_id INTEGER PRIMARY KEY REFERENCES identity(id),
      contact_id INTEGER NOT NULL REFERENCES contact(id),
      messages INTEGER NOT NULL,
      conversations INTEGER NOT NULL,
      first_seen INTEGER,
      last_seen INTEGER,
      known INTEGER NOT NULL,
      answered INTEGER NOT NULL,
      bulk_share REAL NOT NULL,
      categories_json TEXT,
      updated_at INTEGER
  );
  DROP TABLE IF EXISTS vec_embedding;
  DELETE FROM embedding_item;
  PRAGMA user_version = 3;
  ```
- `Store.__init__` creates `vec_embedding USING vec0(item_id INTEGER PRIMARY KEY, kind TEXT partition key, embedding float[N])`; `upsert_embedding` inserts `kind` too; `search_embeddings(vector, *, k=10, kind=None)` keeps its signature but filters with `AND kind = ?` inside the KNN query and fetches exactly `k` (no over-fetch).
- `set_conversation_summary(conversation_id, summary, *, tier=None, events=None, category: str | None = None)`: `category = COALESCE(?, category)`. New `set_conversation_category(conversation_id: int, category: str | None) -> None` (unconditional). `merge_conversations` also sets `category = NULL` on the kept row. `conversations(..., category: str | None = None)` adds `AND category = ?`. `_COUNT_TABLES["senders"] = "sender_stat"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_store.py`: change `test_schema_applied` to expect `"senders": 0` in the counts, and add:

```python
def test_v3_upgrade_drops_vectors_and_keeps_summaries(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True); sqlite_vec.load(conn)
    for n in (1, 2):
        conn.executescript(resources.files("chatmaid.store").joinpath(f"schema_v{n}.sql").read_text())
    conn.execute("CREATE VIRTUAL TABLE vec_embedding USING vec0(item_id INTEGER PRIMARY KEY, embedding float[4])")
    conn.execute("INSERT INTO conversation (platform, key, title, kind, summary) VALUES ('email','k','t','thread','S')")
    conn.execute("INSERT INTO embedding_item (kind, ref_id, text_hash) VALUES ('conversation', 1, 'h')")
    conn.execute("INSERT INTO vec_embedding (item_id, embedding) VALUES (1, ?)", (sqlite_vec.serialize_float32([1,0,0,0]),))
    conn.commit(); conn.close()
    s = Store(path, embedding_dim=4)
    try:
        assert s.counts()["embeddings"] == 0 and s.embedding_hash("conversation", 1) is None
        assert s.conversation(1)["summary"] == "S" and s.conversation(1)["category"] is None
        s.upsert_embedding("conversation", 1, "h", [1.0, 0, 0, 0])  # the partitioned table works
        assert s.search_embeddings([1.0, 0, 0, 0], k=1, kind="conversation")[0][:2] == ("conversation", 1)
    finally:
        s.close()


def test_search_embeddings_kind_partition_ignores_other_kinds(store):
    for i in range(1, 201):
        store.upsert_embedding("memory", i, f"h{i}", [1.0, 0.0, 0.0, 0.0])
    store.upsert_embedding("owner_message", 7, "o", [0.0, 0.0, 1.0, 0.0])
    assert store.search_embeddings([1.0, 0, 0, 0], k=1, kind="owner_message") == [("owner_message", 7, pytest.approx(1.4142, abs=1e-3))]


def test_set_conversation_summary_category_and_merge_clears_it(store, sample_message):
    a = store.upsert_conversation("email", "ka", "A", "thread")
    b = store.upsert_conversation("email", "kb", "B", "thread")
    store.set_conversation_summary(a, "S", category="event")
    store.set_conversation_summary(a, "S2")  # COALESCE keeps the category
    assert store.conversation(a)["category"] == "event"
    assert [c["id"] for c in store.conversations(category="event", exclude_bulk=False)] == [a]
    store.set_conversation_category(a, None)
    assert store.conversation(a)["category"] is None
    store.set_conversation_category(a, "promotional")
    with store.transaction():
        store.merge_conversations(a, b)
    assert store.conversation(a)["category"] is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q`. Expected: FAIL (no `schema_v3.sql`, no `category` column).
- [ ] **Step 3: Implement** the SQL file and the store changes listed under Interfaces. In `search_embeddings`, the KNN subquery becomes `SELECT item_id, distance FROM vec_embedding WHERE embedding MATCH ? AND k = ? [AND kind = ?] ORDER BY distance`, joined to `embedding_item` for `kind, ref_id`.
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS (the existing `test_embedding_upsert_replaces_and_kind_filter` still passes through the partition).
- [ ] **Step 5: Commit** `feat(store): schema v3 with conversation category, sender_stat and kind-partitioned vectors`.

---

### Task 3: Sender statistics, the digest queue and learned-bulk senders (store)

**Files:**
- Modify: `src/chatmaid/store/db.py` (new section `# --- senders and the digest queue ---` after `participant_contact_ids`)
- Create: `tests/test_senders.py`

**Interfaces:**
- Produces, on `Store`:
  - `refresh_sender_stats() -> int`: rebuilds `sender_stat` from the index inside one transaction (delete all, insert one row per sender identity that has at least one non-owner message) and returns the row count. Per identity: `messages` = its non-owner messages; `conversations` = distinct conversations holding one; `first_seen`/`last_seen` = min/max `sent_at`; `known` = 1 when its contact is a participant of any conversation that holds a message with `is_owner = 1`; `answered` = 1 when any conversation holds both one of its messages and an owner message; `bulk_share` = its messages with `bulk = 1` over `messages`; `categories_json` = JSON object `{category: count}` over its conversations whose `category` is not NULL (a conversation counts once for every sender in it); `updated_at` = now.
  - `sender_stat(identity_id: int) -> sqlite3.Row | None`.
  - `sender_context(conversation_id: int) -> sqlite3.Row | None`: the `sender_stat` row of the earliest (`sent_at, id`) non-owner message's sender; `None` when the conversation has no non-owner message or that sender has no stat row.
  - `digest_queue(*, exclude_bulk: bool, limit: int | None = None, group: int | None = None) -> list[sqlite3.Row]`: conversations with `summary IS NULL AND tracked != 0` (and `bulk = 0` when `exclude_bulk`), each row carrying an extra `queue_group` column: 1 = `message_count >= 2 AND last_owner_at IS NOT NULL`; 2 = `message_count >= 2`; 3 = no non-owner message, or the sender's `known = 1`; 4 = the sender's `messages < 5`; 5 = the rest. Ordered by `queue_group`, then (group 5 only) the sender's `messages` ascending, then `last_message_at DESC`. `group` keeps only that group.
  - `learned_bulk(identity_id: int | None, *, min_conversations: int) -> bool`: True when the identity's `categories_json` has at least `min_conversations` conversations in total and every one of them is `promotional`, `newsletter` or `system`; False for `None` or no row.
  - `sender_stats(*, limit: int, category: str | None = None) -> list[sqlite3.Row]`: `sender_stat` joined with `identity.address`, ordered by `messages DESC`; with `category`, only senders whose `categories_json` has that key.
  - `category_counts() -> dict[str, int]`: counts of summarized conversations per category, with key `"unknown"` for `summary IS NOT NULL AND category IS NULL`.

- [ ] **Step 1: Write the failing tests** in `tests/test_senders.py`. The fixture uses the `store` and `sample_message` fixtures and `dataclasses.replace`; `uid` values must be distinct and `sent_at` increasing within a conversation:

```python
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
```

- [ ] **Step 2: Run** `uv run pytest tests/test_senders.py -q`. Expected: FAIL, `AttributeError: refresh_sender_stats`.
- [ ] **Step 3: Implement** the seven methods. `digest_queue` computes the conversation's sender with a correlated subquery over `message` (earliest non-owner `sent_at, id`) joined to `sender_stat`; `category_counts` returns only non-zero keys.
- [ ] **Step 4: Run** `uv run pytest tests/test_senders.py tests/test_store.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): sender statistics, priority digest queue, learned-bulk lookup`.

---

### Task 4: Categories in the digest: prompt, word count, stripping, sender line, stats

**Files:**
- Create: `src/chatmaid/init/categories.py`
- Modify: `src/chatmaid/init/chunking.py` (add `count_words`), `src/chatmaid/llm/prompts/chunk_summary.md`, `src/chatmaid/init/pipeline.py:26-32` (`InitOptions`), `:108-114` (`DigestStats`), `:258-333` (digest), `src/chatmaid/llm/fake.py:12` (`CANNED["chunk_summary"]`)
- Modify tests: `tests/test_digest.py` (`PLAIN`, `test_prompts_load`, `test_digest_skips_bulk_conversations`), `tests/conftest.py:250` (`digested_env` strings), `tests/test_cli_read.py:16` (`cli_env` strings), `tests/test_style_embed.py:113-121` (`test_schema_skeleton_chunk_summary`), `tests/test_chunking.py`

**Interfaces:**
- `categories.py` produces: `CATEGORIES = ("conversation", "event", "transactional", "newsletter", "promotional", "system")` (engagement order); `normalize_category(value: object) -> str | None` (exact member or `None`); `most_engaged(a: str | None, b: str | None) -> str | None` (the earlier in `CATEGORIES`; `None` loses to anything); `allows_events(category) -> bool` (`conversation`, `event`); `allows_facts(category) -> bool` (`conversation`); `summary_limit(category) -> int` (120 for `conversation`/`event`, else 25); `sender_line(stat: sqlite3.Row | None) -> str` = `"Sender context: the owner; outgoing mail with no reply indexed."` for `None`, else `f"Sender context: {messages} message(s) from this sender since {first_seen as YYYY, UTC}; owner has written to them: {yes|no}; owner replied in a conversation with them: {yes|no}; bulk headers on {round(bulk_share * 100)}% of their mail."`.
- `chunking.py` produces `count_words(text: str) -> int` = whitespace-separated tokens after removing CJK characters, plus `ceil(cjk_chars / 2.5)`.
- `chunk_summary.md` system text (exact; keep the `# chunk_summary` first line):

```
# chunk_summary
You summarize one chunk of a conversation for the owner's private assistant and classify it. A "Sender context" line precedes the messages. Write in the conversation's main language. Return JSON only. Never invent details.

category, exactly one of: "conversation" (a person wrote to the owner or a group and an exchange is expected or under way), "event" (announces or invites to a dated happening: seminar, contest round, meeting, deadline, call for applications), "transactional" (receipts, invoices, confirmations, shipping, account and security notices about the owner), "newsletter" (periodic editorial content from an organisation: news digests, weekly bulletins), "promotional" (marketing, offers, advertising, recruitment and admissions marketing), "system" (automated machinery: bounces, calendar sync, ticket updates, password resets).
summary: a neutral account of what was discussed and decided; at most 120 words for conversation and event, at most 25 words for every other category.
events: dated happenings worth remembering, only for conversation and event; otherwise an empty list.
facts: stable facts about the people the owner deals with (roles, relationships, preferences, commitments), only for conversation and never about organisations or products; otherwise an empty list.
```
  Schema: the old one with `"category": {"type": "string", "enum": [the six]}` first and `"required": ["category", "summary", "events", "facts"]`.
- `InitOptions`: `skip_bulk: bool = False`, new `queue_group: int | None = None`.
- `DigestStats(conversations, chunks, memories, failed, skipped, long: int = 0, categories: dict[str, int] = field(default_factory=dict))`; `categories` counts the stored category per completed conversation with key `"unknown"` for `None`.
- `digest()`: calls `store.refresh_sender_stats()` first and again after the loop; reads `store.digest_queue(exclude_bulk=opts.skip_bulk, limit=opts.limit_conversations, group=opts.queue_group)`.
- `_digest_conversation`: after the empty-text filter, `msgs = strip_quoted(msgs)` and raises `_NoCachedText` if that leaves nothing; the chunk user text is `sender_line(store.sender_context(cid)) + "\n\n" + render_messages(chunk)`; per chunk: `category = most_engaged(category, normalize_category(out["category"]))`, checkpoint state gains `"category"`, `out["events"]`/`out["facts"]` are replaced by `[]` when the chunk's own normalized category disallows them (before `add_memories` and before extending `events`), and `long` increments when `count_words(out["summary"]) > summary_limit(chunk category)` (log at debug with the conversation id). At the end `set_conversation_summary(cid, summary, events=events, category=category)`. Returns `(chunks_done, memories, category, long)`.

- [ ] **Step 1: Update the fakes and fixtures**: every `chunk_summary` string in `fake.py` `CANNED`, `conftest.py`, `test_cli_read.py` and `test_digest.py` `PLAIN` gains `"category":"conversation"`; `test_prompts_load` expects `required == ["category", "summary", "events", "facts"]`; `test_schema_skeleton_chunk_summary` expects the skeleton to start with `'{"category": "<string>", "summary": "<string>", '`. Rename `test_digest_skips_bulk_conversations` to `test_digest_includes_bulk_by_default_and_skip_bulk_option`: with `InitOptions()` every bulk conversation gets a summary; with `InitOptions(skip_bulk=True)` on a fresh index none does.
- [ ] **Step 2: Write the failing tests** in `tests/test_digest.py` (and `test_count_words_cjk` in `tests/test_chunking.py`):

```python
def test_count_words_cjk():
    assert count_words("one two  three") == 3
    assert count_words("明天见谢谢") == 2       # 5 CJK characters: ceil(5 / 2.5)
    assert count_words("明天见，谢谢") == 3      # the full-width comma is left over as one token
    assert count_words("meet 明天 at 3") == 4    # three tokens plus ceil(2 / 2.5)
    assert count_words("") == 0
```

```python
def test_digest_stores_category_and_drops_junk_memories(ingested_env):
    llm = FakeLLM({"chunk_summary": json.dumps({"category": "promotional", "summary": "sale",
        "events": [{"date": "2024-10-01", "text": "sale ends"}], "facts": [{"about": "Shop", "text": "sells"}]}),
        "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.memories == 0 and st.categories.get("promotional", 0) == st.conversations
    rows = ingested_env.store.conversations(needing_summary=False, exclude_bulk=False)
    assert all(r["category"] == "promotional" and json.loads(r["events_json"]) == [] for r in rows)


def test_digest_invalid_category_is_null(ingested_env):
    llm = FakeLLM({"chunk_summary": '{"category":"spam","summary":"x","events":[],"facts":[]}', "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.categories == {"unknown": st.conversations}
    assert all(r["category"] is None for r in ingested_env.store.conversations(needing_summary=False, exclude_bulk=False))


def test_digest_category_most_engaged_across_chunks(ingested_env):
    store = ingested_env.store
    multi = next(c for c in store.conversations() if len(store.message_refs_for(c["id"])) > 1)
    llm = FakeLLM({"chunk_summary": ['{"category":"event","summary":"a","events":[],"facts":[]}',
                                     '{"category":"conversation","summary":"b","events":[],"facts":[]}'],
                   "rolling_summary": '{"summary":"S"}'})
    pipeline(ingested_env, llm).digest(InitOptions(chunk_chars=1, limit_conversations=1))
    assert store.conversation(multi["id"])["category"] == "conversation"


def test_digest_strips_quoted_history_except_first(watch_env):
    env = watch_env  # its add_row writes a row and body into the fixture cache
    env.add_row("INBOX", uid="20", message_id="<r20@x>", sender="Ann <ann@x.org>", to="me@x.org",
                date=1_600_000_100, body="Yes.\n> hello there", in_reply_to="<a@x>")
    llm = FakeLLM(PLAIN)
    p = InitPipeline(env.store, env.connector, llm, env.config)
    p.ingest(InitOptions())
    p.digest(InitOptions())
    chunk_calls = [c["user"] for c in llm.calls if c["system"].startswith("# chunk_summary")]
    thread = next(u for u in chunk_calls if "Yes." in u)
    assert "hello there" in thread and "> hello there" not in thread


def test_digest_sender_context_line(ingested_env):
    llm = FakeLLM(PLAIN)
    pipeline(ingested_env, llm).digest(InitOptions())
    users = [c["user"] for c in llm.calls if c["system"].startswith("# chunk_summary")]
    assert all(u.startswith("Sender context: ") for u in users)
    assert any("owner has written to them: yes" in u for u in users)
    assert any(u.startswith("Sender context: the owner; outgoing mail with no reply indexed.") for u in users)


def test_digest_counts_long_summaries(ingested_env):
    llm = FakeLLM({"chunk_summary": json.dumps({"category": "promotional", "summary": " ".join(["w"] * 26), "events": [], "facts": []}),
                   "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert st.long == 1 and st.conversations == 1
```

- [ ] **Step 3: Run** `uv run pytest tests/test_digest.py tests/test_chunking.py -q`. Expected: FAIL (`categories.py` missing, old schema).
- [ ] **Step 4: Implement** `categories.py`, `count_words`, the prompt, `InitOptions`/`DigestStats`, and the digest changes under Interfaces.
- [ ] **Step 5: Run** `uv run pytest -q -W error`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(digest): categories, sender context, quote stripping, priority queue, bulk included`.

---

### Task 5: Routine C strips the owner's message and marks the conversation

**Files:**
- Modify: `src/chatmaid/reply/flow.py:172-195` (`update_summary`)
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `strip_quoted` (Task 1), `set_conversation_category` (Task 2), `allows_events`/`allows_facts` (Task 4).
- `update_summary` renders `strip_quoted([msg], keep_first=False)` (returns False without a model call when that is empty), applies the same events/facts gate as the digest to `out`, and, inside the existing transaction, calls `store.set_conversation_category(conversation_id, "conversation")`.

- [ ] **Step 1: Write the failing test** in `tests/test_flow.py` next to `test_owner_message_updates_summary_and_memories`:

```python
def test_owner_message_strips_quoted_history_and_sets_category(flow_env):
    env = flow_env
    cid = env.arrive("100")["conversation_id"]
    env.store.set_conversation_summary(cid, "S0", category="event")
    owner_reply(env, "200", "Sure.\n\nOn Mon, Ann wrote:\n> Can we meet Friday?", "<m100@x>")
    user = calls(env, "chunk_summary")[0]["user"]
    assert "Sure." in user and "Can we meet Friday?" not in user
    assert env.store.conversation(cid)["category"] == "conversation"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_flow.py -q -k strips_quoted`. Expected: FAIL (quoted text present, category still `event`).
- [ ] **Step 3: Implement** the three changes in `update_summary`.
- [ ] **Step 4: Run** `uv run pytest tests/test_flow.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `fix(reply): routine C strips quoted history and marks the conversation`.

---

### Task 6: Progress line, graceful stop and resume

**Files:**
- Create: `src/chatmaid/init/progress.py`, `src/chatmaid/stop.py`
- Modify: `src/chatmaid/init/pipeline.py` (`InitOptions.passes` default, `DigestStats.stopped`, `InitPipeline.__init__`, `run`, `digest`), `src/chatmaid/init/passes.py` (`run_style`, `run_embed`), `src/chatmaid/cli.py:131-160` (`_init_locked`), `:163-172` (`register_init`)
- Create: `tests/test_progress.py`, `tests/test_stop.py`; Modify: `tests/test_digest.py`, `tests/test_style_embed.py`, `tests/test_cli_read.py`

**Interfaces:**
- `progress.py` produces `class Progress` with `__init__(self, name: str, total: int, *, stream: TextIO | None = None, every: int = 50, window: int = 50, clock: Callable[[], float] = time.monotonic)` (stream defaults to `sys.stderr`), `update(self, done: int, label: str = "") -> None`, `finish(self, summary: str = "") -> None`. Line = `f"{name} {done}/{total} {pct:3d}% [{bar}] {rate}/h eta {eta} elapsed {elapsed} | {label[:40]}"` with a 20-character `#`/`-` bar, `rate` = conversations per hour over the last `window` updates (`"--"` before two updates), `eta`/`elapsed` as `"{h}h{mm:02d}m"`. When `stream.isatty()`: each `update` writes `"\r" + line` padded to the longest line so far and flushes, no newline until `finish` (which writes `"\n" + summary + "\n"`). Otherwise: a newline-terminated line only when `done % every == 0` or `done == total`, and `finish` writes the summary line.
- `stop.py` produces `class StopRequested(BaseException)` and `@contextmanager stop_on_signal() -> Iterator[None]`: installs handlers for `SIGINT` and `SIGTERM`; the first signal restores the previous handlers and raises `StopRequested` (so a blocking model call is interrupted and a second signal gets the default action); the previous handlers are restored on exit in every case.
- `InitPipeline.__init__(self, store, connector, llm, cfg, *, progress_stream: TextIO | None = None)`; attribute `stopped: bool = False`; `_progress(self, name: str, total: int) -> Progress`.
- `InitOptions.passes` default becomes `("ingest", "style", "digest", "embed")`; `DigestStats` gains `stopped: bool = False`.
- `digest()`: `progress.update(done, f"{row['id']} {row['title']}")` before each conversation; `except StopRequested: self.stopped = True; break`; `finish` with the stats line; returns `stopped=self.stopped`. `run()` breaks out of the pass loop when `self.stopped`. `run_style` catches `StopRequested` around the model call (sets `pipeline.stopped`, returns what is done); `run_embed` catches it around `llm.embed` (sets `pipeline.stopped`, returns the count so far); both report progress (languages; batches).
- CLI: `_init_locked` wraps the run in `with stop_on_signal():`, passes `--skip-bulk`/`--queue-group` into `InitOptions`, prints each pass's stats as before, and when `pipeline.stopped` prints `stopped; run the same command to resume` to stderr and returns 130. `register_init` adds `--skip-bulk` (store_true) and `--queue-group` (int, choices 1..5).

- [ ] **Step 1: Write the failing tests**:

```python
# tests/test_progress.py
class Tty(io.StringIO):
    def isatty(self): return True

def test_progress_tty_redraws_in_place():
    out = Tty(); t = [0.0]
    p = Progress("digest", 4, stream=out, clock=lambda: t[0])
    p.update(0, "1 first"); t[0] = 3600; p.update(2, "3 third"); p.finish("done")
    text = out.getvalue()
    assert text.count("\r") == 2 and text.count("\n") == 2
    assert "digest 2/4  50% [##########----------] 2/h eta 1h00m elapsed 1h00m | 3 third" in text

def test_progress_non_tty_prints_every_50_and_finish():
    out = io.StringIO()
    p = Progress("digest", 120, stream=out, every=50)
    for i in range(1, 121): p.update(i)
    p.finish("summary")
    lines = out.getvalue().splitlines()
    assert [l.split()[1] for l in lines[:3]] == ["50/120", "100/120", "120/120"]
    assert lines[3] == "summary" and len(lines) == 4
    assert "\r" not in out.getvalue()

def test_progress_label_is_cut_to_40_chars():
    out = Tty(); p = Progress("x", 1, stream=out); p.update(0, "a" * 60)
    assert "a" * 41 not in out.getvalue()

# tests/test_stop.py
def test_first_signal_raises_stop_requested_and_restores_default():
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(StopRequested):
        with stop_on_signal():
            signal.raise_signal(signal.SIGINT)
    assert signal.getsignal(signal.SIGINT) is before

def test_handlers_restored_after_normal_exit():
    before = signal.getsignal(signal.SIGTERM)
    with stop_on_signal():
        assert signal.getsignal(signal.SIGTERM) is not before
    assert signal.getsignal(signal.SIGTERM) is before

# tests/test_digest.py
def test_digest_stops_between_conversations_and_resumes(ingested_env):
    llm = FakeLLM(PLAIN); n = {"calls": 0}; orig = llm.complete
    def flaky(**kw):
        n["calls"] += 1
        if n["calls"] == 2: raise StopRequested()
        return orig(**kw)
    llm.complete = flaky
    p = pipeline(ingested_env, llm)
    st = p.digest(InitOptions())
    assert st.stopped and p.stopped and st.conversations == 1
    st2 = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    assert not st2.stopped and st2.conversations >= 1 and st2.skipped == 1

def test_stop_inside_chunk_transaction_rolls_back(ingested_env, monkeypatch):
    store = ingested_env.store
    def boom(task, key, state): raise StopRequested()
    monkeypatch.setattr(store, "set_checkpoint", boom)
    st = pipeline(ingested_env, FakeLLM(dict(CANNED))).digest(InitOptions(limit_conversations=1))
    assert st.stopped and st.conversations == 0 and store.memories() == []
    assert all(c["summary"] is None for c in store.conversations(exclude_bulk=False))

def test_run_stops_after_a_stopped_pass(ingested_env):
    llm = FakeLLM(PLAIN); orig = llm.complete
    def stop_now(**kw): raise StopRequested()
    llm.complete = stop_now
    p = pipeline(ingested_env, llm)
    out = p.run(InitOptions(passes=("digest", "embed")))
    assert list(out) == ["digest"] and p.stopped

def test_progress_is_written_to_the_given_stream(ingested_env):
    out = io.StringIO()
    p = InitPipeline(ingested_env.store, ingested_env.connector, FakeLLM(PLAIN), ingested_env.config, progress_stream=out)
    p.digest(InitOptions())
    assert out.getvalue().startswith("digest ")

# tests/test_style_embed.py
def test_embed_stop_keeps_completed_batches(ingested_env):
    store = ingested_env.store
    for i in range(40):
        store.add_memory(kind="fact", text=f"m{i}", source_ref="r", observed_at=None)
    llm = FakeLLM(dim=4); orig = llm.embed; n = {"batches": 0}
    def flaky(texts, *, model):
        n["batches"] += 1
        if n["batches"] == 2: raise StopRequested()
        return orig(texts, model=model)
    llm.embed = flaky
    pipe = InitPipeline(store, ingested_env.connector, llm, ingested_env.config)
    assert pipe.embed(InitOptions()) == 32 and pipe.stopped
    assert store.counts()["embeddings"] == 32

# tests/test_cli_read.py
def test_cli_init_stopped_exits_130(fake_env, monkeypatch, capsys):
    def stopped_digest(self, opts):
        self.stopped = True
        return DigestStats(0, 0, 0, 0, 0, stopped=True)
    monkeypatch.setattr("chatmaid.cli.InitPipeline.digest", stopped_digest)
    rc = main(["--config", str(fake_env.config_path), "init", "--pass", "digest", "--fake-llm"])
    assert rc == 130 and "stopped; run the same command to resume" in capsys.readouterr().err
```

- [ ] **Step 2: Run** `uv run pytest tests/test_progress.py tests/test_stop.py tests/test_digest.py tests/test_style_embed.py tests/test_cli_read.py -q`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** `progress.py`, `stop.py`, the pipeline/passes changes and the CLI changes under Interfaces. In `run_embed`, the per-batch `upsert_embedding` loop stays as is (each upsert is its own transaction).
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS. Then run `uv run chatmaid init --pass digest --fake-llm --limit 3` against a throwaway config whose `data.dir` points into the worktree's own scratch directory and confirm the progress line redraws on the terminal.
- [ ] **Step 5: Commit** `feat(init): progress line, graceful stop on SIGINT/SIGTERM, resumable passes`.

---

### Task 7: Style samples stripped; owner messages embedded

**Files:**
- Modify: `src/chatmaid/init/passes.py:26-57` (`run_style`), `:60-88` (`run_embed`), `src/chatmaid/store/db.py` (add `owner_messages`)
- Test: `tests/test_style_embed.py`

**Interfaces:**
- `Store.owner_messages() -> list[sqlite3.Row]`: `id, ref_key, conversation_id` of messages with `is_owner = 1 AND bulk = 0 AND body_missing = 0`, ordered by `id`.
- `run_style`: `text = strip_reply_chrome(fetched.text).strip()` before the `STYLE_MIN_CHARS` check and the language grouping; the sample shown is the stripped text.
- `run_embed`: after summaries and memories, one item per `owner_messages()` row: `("owner_message", row["id"], strip_reply_chrome(connector.fetch(ref).text).strip()[:2000])`, skipping `KeyError` and blank texts; the hash/upsert path is unchanged.

- [ ] **Step 1: Write the failing tests**:

```python
def test_style_samples_are_stripped(watch_env):
    env = watch_env
    env.add_row("[Gmail]/Sent Mail", uid="50", message_id="<s50@x>", sender="me@x.org", to="ann@x.org",
                date=1_700_000_900, body="Thanks a lot for the quick help today.\n\nOn Mon, Ann wrote:\n> QUOTEDTEXT")
    llm = FakeLLM({"style_profile": '{"profile": "P"}'})
    pipe = InitPipeline(env.store, env.connector, llm, env.config)
    pipe.ingest(InitOptions())
    pipe.style(InitOptions(), min_samples=1)
    user = next(c["user"] for c in llm.calls if c["system"].startswith("# style_profile"))
    assert "quick help today" in user and "QUOTEDTEXT" not in user


def test_embed_includes_owner_messages_by_kind(digested_env):
    store = digested_env.store
    assert store.search_embeddings([0.5] * 4, k=5, kind="owner_message")
    row = store.owner_messages()[0]
    text = strip_reply_chrome(digested_env.connector.fetch(SourceRef.from_key(row["ref_key"])).text).strip()[:2000]
    assert store.embedding_hash("owner_message", row["id"]) == hashlib.sha256(text.encode()).hexdigest()
```
  and update `test_embed_is_incremental`'s `expected` to add `len(store.owner_messages())`.

- [ ] **Step 2: Run** `uv run pytest tests/test_style_embed.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the three changes.
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(init): style samples from the owner's own words; owner messages embedded`.

---

### Task 8: The owner's similar past replies in the drafting context

**Files:**
- Modify: `src/chatmaid/config.py:46-53` (`DraftConfig.examples`), `:92-98` (default `"examples": 3`), `:191-197`; `config.example.toml` `[draft]`; `src/chatmaid/reply/context.py`; `src/chatmaid/llm/prompts/reply_draft.md`
- Test: `tests/test_context.py`, `tests/test_config.py`

**Interfaces:**
- `DraftConfig.examples: int` (default 3; `ConfigError` when not an int `>= 0`).
- `ReplyContext.examples: list[tuple[str, int, str, str]]` = `(ref_key, sent_at, to, text)`; `to_json` stores `[ref_key, sent_at, to, make_preview(text)]`; `from_json` restores; `render` adds, after the style section (or after memories when there is no style) and before the message: `## Your similar past replies` with lines `[{YYYY-MM-DD} to {to}] {text}`, or `(none)`.
- `context.py` internals: `_query_vector(llm, cfg, msg) -> list[float] | None` (embeds `msg.text[:2000]`, logs and returns `None` on `LLMError`); `_memories(store, cfg, conversation_id, contact_id, vec)` (no longer embeds itself); `_examples(store, connector, cfg, conversation_id, vec) -> list[tuple[str, int, str, str]]`: `search_embeddings(vec, k=cfg.draft.examples * 3, kind="owner_message")`, skip messages of `conversation_id`, fetch through the connector (skip `KeyError`, `OSError`, `sqlite3.Error`), `text = strip_reply_chrome(msg.text).strip()[:800]`, skip blank, `to` = first recipient's name or email (or `"?"`), stop at `cfg.draft.examples`; empty when `vec is None` or `examples == 0`.
- `reply_draft.md` system text gains the sentence: `If the header shows the owner's similar past replies, match their tone, length and habits; never copy their content.`

- [ ] **Step 1: Write the failing tests** in `tests/test_context.py`, in the file's own style (`env` yields `(e, msg, row)` and `build(e, msg, row)` calls `build_context`; the index is digested and, since Task 7, its owner messages are embedded):

```python
def test_context_includes_owner_examples_excluding_current_conversation(env):
    e, msg, row = env
    ctx = build(e, msg, row)
    assert 1 <= len(ctx.examples) <= e.config.draft.examples
    assert all(e.store.message_by_ref(x[0])["conversation_id"] != row["conversation_id"] for x in ctx.examples)
    text = ctx.render()
    assert text.index("## Your similar past replies") < text.index("## Message to answer")


def test_context_examples_are_stripped_and_cut_to_800(env, monkeypatch):
    e, msg, row = env
    monkeypatch.setattr(e.connector, "fetch", lambda ref: replace(msg, text="x" * 900 + "\n> quoted", ref=ref))
    ctx = build(e, msg, row)
    assert ctx.examples and all(len(x[3]) == 800 and "quoted" not in x[3] for x in ctx.examples)


def test_context_json_stores_example_previews_only(env):
    e, msg, row = env
    d = build(e, msg, row).to_json()
    assert d["examples"] and all(len(x[3]) <= 200 for x in d["examples"])
    assert ReplyContext.from_json(d).examples == [tuple(x) for x in d["examples"]]


def test_zero_examples_disables_the_section(env):
    e, msg, row = env
    cfg = replace(e.config, draft=replace(e.config.draft, examples=0))
    ctx = build_context(e.store, e.connector, FakeLLM(dim=4), cfg, row["conversation_id"], msg, now=NOW)
    assert ctx.examples == [] and "(none)" in ctx.render().split("## Your similar past replies")[1]
```
  and in `tests/test_config.py`:

```python
def test_draft_examples_default_and_validation(tmp_path):
    assert load(Path("config.example.toml"), project_root=tmp_path).draft.examples == 3
    p = tmp_path / "config.toml"
    p.write_text('[draft]\nexamples = -1\n')
    with pytest.raises(ConfigError, match="draft.examples"):
        load(p, project_root=tmp_path)
```
  Keep `_DEFAULTS` and `config.example.toml` in step (`test_defaults_mirror_example_config` checks it). Update `test_render_sections_order_and_roundtrip` for the new section.

- [ ] **Step 2: Run** `uv run pytest tests/test_context.py tests/test_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the config field, the context changes and the prompt sentence.
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS (`test_context_json_holds_previews_only` in `test_flow.py` still holds).
- [ ] **Step 5: Commit** `feat(reply): the owner's similar past replies as examples in the drafting context`.

---

### Task 9: Lookup by category and by thread; CLI listings and `senders`

**Files:**
- Modify: `src/chatmaid/lookup.py`, `src/chatmaid/cli.py` (`_status_command`, `_conversations_command`, `_show_command`, `_lookup_command`, `register_lookup`, new `_senders_command`/`register_senders`, `build_parser`)
- Test: `tests/test_lookup.py`, `tests/test_cli_read.py`

**Interfaces:**
- `Hit` gains `category: str | None = None`. `search_history(..., categories: tuple[str, ...] = ())`: hits whose conversation's `category` is not in `categories` are dropped (when `categories` is non-empty).
- New `@dataclass ThreadHit(conversation_id: int, last_message_at: int, category: str | None, title: str, summary: str, score: float)` and `search_threads(store, llm, *, query: str, categories: tuple[str, ...] = (), limit: int = 20, embedding_model: str) -> list[ThreadHit]`: embeds the query, `search_embeddings(vec, k=limit * 3 if categories else limit, kind="conversation")`, filters by category, `summary` = first 160 characters, `score = 1 / (1 + distance)`, sorted by score, `[:limit]`.
- CLI: `lookup` gains `--category` (`action="append"`, `choices=CATEGORIES`) and `--threads` (prints `f"{id}  {_day(last)}  {category or '-'}  {title}  —  {summary}"`; with `--no-vector` prints `thread search needs the embedding model` to stderr and returns 2). `conversations` gains `--category` and prints the category as a new column after `count`. `show` prints `Category: ...` after `Title`. `status` prints one `category_<name>=<n>` line per key of `store.category_counts()`. New `senders` command (`--limit N` default 30, `--category`): header `address  messages  convs  known  replied  categories`, rows from `store.sender_stats(...)`, `categories` rendered as `promotional:12 event:1` (or `-`).

- [ ] **Step 1: Write the failing tests**:

```python
# tests/test_lookup.py
def test_search_history_filters_by_category(digested_env):
    store = digested_env.store
    hit = search_history(store, digested_env.connector, None, query="hello")[0]
    store.set_conversation_category(hit.conversation_id, "promotional")
    assert search_history(store, digested_env.connector, None, query="hello", categories=("conversation",)) == []
    assert search_history(store, digested_env.connector, None, query="hello", categories=("promotional",))[0].category == "promotional"

def test_search_threads_ranks_conversations(digested_env):
    hits = search_threads(digested_env.store, FakeLLM(dim=4), query="S1", embedding_model="e")
    assert hits and hits[0].summary.startswith("S") and all(h.category == "conversation" for h in hits)
    assert search_threads(digested_env.store, FakeLLM(dim=4), query="S1", categories=("event",), embedding_model="e") == []

# tests/test_cli_read.py
def test_cli_lookup_threads_and_category(cli_env, capsys, monkeypatch):
    monkeypatch.setattr("chatmaid.cli._lmstudio_client", lambda cfg, token: FakeLLM(dim=4))
    monkeypatch.setattr("chatmaid.cli.read_token", lambda cfg: "t")
    cfg = ["--config", str(cli_env.config_path)]
    assert main([*cfg, "lookup", "S1", "--threads"]) == 0 and "conversation" in capsys.readouterr().out
    assert main([*cfg, "lookup", "hello", "--no-vector", "--category", "event"]) == 0 and capsys.readouterr().out == ""
    assert main([*cfg, "lookup", "S1", "--threads", "--no-vector"]) == 2

def test_cli_senders_and_status_show_categories(cli_env, capsys):
    cfg = ["--config", str(cli_env.config_path)]
    assert main([*cfg, "status"]) == 0 and "category_conversation=" in capsys.readouterr().out
    assert main([*cfg, "senders", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("address") and "ann@x.org" in out
    assert main([*cfg, "conversations", "--category", "conversation"]) == 0 and "conversation" in capsys.readouterr().out
```
  (`cli_env`'s digest already calls `refresh_sender_stats()`, so `senders` has rows.)

- [ ] **Step 2: Run** `uv run pytest tests/test_lookup.py tests/test_cli_read.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the lookup and CLI changes under Interfaces; register `senders` in `build_parser`.
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(cli): lookup by category and by thread, senders listing, categories in status/show/conversations`.

---

### Task 10: Learned bulk senders in live triage

**Files:**
- Modify: `src/chatmaid/config.py` (new `TriageConfig(learned_bulk_min: int)`, `Config.triage`, default `{"triage": {"learned_bulk_min": 10}}`, `ConfigError` unless an int `>= 1`), `config.example.toml` (new `[triage]` section with the comment `# senders whose summarized conversations were all promotional/newsletter/system this many times are triaged bulk by rule`), `src/chatmaid/triage/classify.py:24-44` (`rule_triage`), `:63-80` (`triage_message`), `src/chatmaid/reply/flow.py:104-109`
- Test: `tests/test_triage.py`, `tests/test_flow.py`, `tests/test_config.py`

**Interfaces:**
- `rule_triage(msg, conversation, *, now, max_age_days, learned_bulk: bool = False)`: right after the `msg.bulk` check, `if learned_bulk: return Triage("bulk", "bulk", "learned bulk sender", "rule")`. `triage_message(..., learned_bulk: bool = False)` passes it through.
- `ReplyFlow.on_incoming` computes `learned = store.learned_bulk(row["sender_identity_id"], min_conversations=cfg.triage.learned_bulk_min)` and passes `learned_bulk=learned`.

- [ ] **Step 1: Write the failing tests**:

```python
# tests/test_triage.py
def test_rule_learned_bulk_sender(sample_message):
    t = rule_triage(sample_message, None, now=RECENT["now"], max_age_days=14, learned_bulk=True)
    assert t == Triage("bulk", "bulk", "learned bulk sender", "rule")

# tests/test_flow.py
def test_learned_bulk_sender_is_triaged_by_rule(flow_env, monkeypatch):
    env = flow_env
    monkeypatch.setattr(env.store, "learned_bulk", lambda identity_id, *, min_conversations: True)
    row = env.arrive("100")
    t = env.store.triage_for(row["id"])
    assert (t["category"], t["decided_by"], t["reason"]) == ("bulk", "rule", "learned bulk sender")
    assert calls(env, "triage") == [] and env.store.pending_draft_for(row["conversation_id"]) is None

# tests/test_config.py
def test_triage_section_defaults_and_validation(tmp_path):
    assert load(Path("config.example.toml"), project_root=tmp_path).triage.learned_bulk_min == 10
    p = tmp_path / "config.toml"
    p.write_text('[triage]\nlearned_bulk_min = 0\n')
    with pytest.raises(ConfigError, match="triage.learned_bulk_min"):
        load(p, project_root=tmp_path)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_triage.py tests/test_flow.py tests/test_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the config section, the rule and the flow call.
- [ ] **Step 4: Run** `uv run pytest -q -W error`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(triage): senders the digest found purely promotional are bulk by rule`.

---

### Task 11: Documentation and the real-model smoke run

**Files:**
- Modify: `README.md` (Running table: `init` flags `--skip-bulk`, `--queue-group N`; `conversations --category`; `lookup --category`, `--threads`; new `senders` row; Notes: a bullet on the digest queue, categories, the sender-context line, quoted history stripping, and the progress line with stop/resume; Draft flow: one sentence on the owner's past replies as examples; Known limitations: replace the last bullet's "stops between messages" wording if `watch` is unchanged, otherwise leave), `config.example.toml` (done in Tasks 8 and 10; verify), `docs/superpowers/specs/2026-10-29-digest-scope-design.md` status line → `implemented`.
- No code changes.

- [ ] **Step 1: Update the README and the spec status** as listed; keep every claim consistent with the code (`--queue-group` is a builder-added option: say "process only queue group N, for smoke runs or to run groups 1 and 2 first").
- [ ] **Step 2: Smoke run on a scratch index** (never `data/index.sqlite`): copy `data/config.toml` to a scratch directory, set `[data] dir` to a scratch path, then:

```bash
uv run chatmaid --config <scratch>/config.toml init --pass ingest
for g in 1 2 3 4 5; do uv run chatmaid --config <scratch>/config.toml init --pass digest --limit 4 --queue-group $g; done
uv run chatmaid --config <scratch>/config.toml conversations --limit 20 --all
uv run chatmaid --config <scratch>/config.toml senders --limit 10
uv run chatmaid --config <scratch>/config.toml status
```
  Expected: 20 summaries; group 1 rows mostly `conversation`, group 5 rows mostly `promotional`/`newsletter`; no memory rows from `promotional` conversations (`SELECT COUNT(*) FROM memory m JOIN conversation c ON c.id = m.conversation_id WHERE c.category = 'promotional'` is 0); the progress line redraws on the terminal. Then start `init --pass digest --limit 50`, press Ctrl-C after two or three conversations, confirm exit status 130 and the "stopped; run the same command to resume" line, rerun and confirm it continues without re-summarizing the finished ones (`status` counts).
- [ ] **Step 3: Record** the per-group timings and the category spread in the final report (they feed the owner's launch decision); run `uv run pytest -q -W error` one last time.
- [ ] **Step 4: Commit** `docs: digest queue, categories, progress and stop/resume; spec marked implemented`.
