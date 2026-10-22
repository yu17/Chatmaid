# Phase 1b: Reply-Draft Flow, Pseudonymized Cloud Refine, Draft CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every incoming mail the daemon records a triage decision and, when a reply is needed, stores one to three local-model draft candidates built from the index; the owner can regenerate with feedback, ask for a consent-gated pseudonymized cloud improvement through `codex` or `claude`, and approve, edit or dismiss, all from the CLI (the web UI is plan 1c).

**Architecture:** A `ReplyFlow` object sits behind the watcher's `on_routed` hook. Routine B (`on_incoming`) runs rule triage, then small-model triage, builds a `ReplyContext` from the store (contact card, summary, hot-window messages, memories by vector search, style profile), drafts with the main model through the existing validated-JSON call path with one optional history-lookup round, and stores `draft` + `draft_version` rows. Routine C (`on_owner_message`) resolves a pending draft against what the owner actually sent and folds the owner's message into the conversation summary and memories. Cloud improvement is two explicit steps, `propose_cloud` (pseudonymize, store the exact payload and the placeholder map as a `cloud_call` row) and `send_cloud` (run the stored payload through a `CloudBackend` subprocess, re-hydrate, store the version and the audit fields); nothing is sent without the second step.

**Tech Stack:** Python 3.14, uv, sqlite3 + sqlite-vec (schema v2 via a migration ladder), `openai` SDK against LM Studio, `subprocess` for the `codex` and `claude` CLIs, `fcntl.flock`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-21-phase1-design.md` (sections 5 `draft`/`cloud_call`, 6 routines B and C, 7 cloud, 9 draft actions, 10 step 3, 11 items 5–6, 13 pseudonymizer). Builds on plan 1a (`docs/superpowers/plans/2026-10-22-phase1-core-email.md`) and its final review's parked minors. Out of scope here: the web UI (plan 1c), routine D's maintenance jobs (hot-window rollover, memory consolidation, style refresh), the `evolution 'mailto:'` hand-off, WeChat.

## Global Constraints

- Everything in plan 1a's Global Constraints still holds: read-only sources, no body copies in the index beyond 200-character previews, hot window 365 days, `data/` layout, token handling, thinking control, stable thread keys.
- Nothing sends mail, writes to Evolution, or writes to a mail server. "Approve" records approval only.
- Cloud consent (spec §9, §11.5): a cloud call exists only as a stored `cloud_call` row created by `propose_cloud`; `send_cloud` transmits that row's `payload` byte-for-byte and nothing else; no code path calls a backend without a prior proposal; the CLI prints the payload before asking. The watcher never calls a cloud backend.
- What crosses to the cloud is the pseudonymized previous draft, the owner's comment, the conversation summary and at most 1,500 characters of the message being answered. Placeholders are `[PERSON_n]`, `[EMAIL_n]`, `[PHONE_n]`, `[AMOUNT_n]` (amounts only when `cloud.redact_amounts = true`; research: keep amounts and dates unless flagged). The placeholder map is stored in `cloud_call.mapping_json` (local, unencrypted per §11) and never sent.
- Cloud CLIs run tool-less and stateless, verified 2026-10-22 on this machine (codex-cli 0.163.0, Claude Code 2.1.308):
  - codex: `codex exec --ephemeral --ignore-user-config --ignore-rules -s read-only -C <empty temp dir> --skip-git-repo-check --color never --output-schema <schema file> -o <out file> -m <model> <prompt>`; the out file holds the schema-shaped JSON. `--ignore-user-config` skips the owner's hooks and MCP servers (auth still comes from `CODEX_HOME`) and drops the owner's model default, so `-m` is always passed; default `cloud.codex_model = "gpt-6-astra"` (the owner's configured model).
  - claude: `claude -p --tools "" --no-session-persistence --setting-sources "" --disable-slash-commands --strict-mcp-config --output-format json --json-schema <schema json> [--model <m>] <prompt>` with cwd = an empty temp dir; the stdout envelope's `structured_output` is the result, `is_error`/`subtype` signal failure. **`--bare` is not used**: it restricts auth to `ANTHROPIC_API_KEY` and the owner is logged in through OAuth (the design's §7 command line is corrected in Task 13). Both subprocesses get `stdin=DEVNULL`, a timeout of `cloud.timeout_s`, and an environment without `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` (otherwise claude refuses to start inside a Claude Code session).
- Model roles: triage and PII tagging `model_small` with `grammar=True`; drafting and revising `cfg.draft_model` (`model_main` unless `draft.model_role = "heavy"`) with `grammar=False`; summary updates as in digest (chunk `model_small` grammar, merge `model_main` prefill); embeddings `model_embedding`.
- The LLM flag `think` is renamed `grammar` (Task 1): `grammar=True` sends the JSON-schema `response_format` and no prefill (non-thinking models); `grammar=False` sends the empty-think prefill and no `response_format` (thinking models). Semantics are unchanged from ruling R9.
- One pending draft per conversation: a new incoming message in a conversation with a pending draft marks it `superseded` before drafting again.
- Draft statuses: `pending`, `approved`, `edited`, `dismissed`, `superseded`. Cloud call statuses: `proposed`, `declined`, `sent`, `failed`. Triage categories: `needs_reply`, `informational`, `bulk`, `system`, `unknown`. Conversation tiers: `family`, `work`, `finance`, `casual`, `bulk`.
- Draft texts (generated by chatmaid) are stored in full; the owner's own sent text is referenced by `outcome_ref` plus a 200-character `outcome_preview`, never copied.
- Real-model smoke tests (Task 13) run against a scratch index in the worktree's own `data/` directory, never against the main checkout's `data/`; cloud backends are exercised only through `cloud-check`'s canned payload, never with real content.
- Commit after every task with the message prefix shown in the task; `uv run pytest -q -W error` must pass before each commit.

## Review Focus

1. Incoming mail whose body is still not cached after the watcher's three waits (`body_missing=True`): triage records `unknown` by rule, no model is called, no draft is created. Pinned in Task 5 (`test_rule_body_missing_is_unknown`) and Task 10 (`test_body_missing_incoming_makes_no_draft`).
2. A second message arrives in a conversation that already has a pending draft: the old draft becomes `superseded`, exactly one draft stays pending. Pinned in Task 10 (`test_new_incoming_supersedes_pending_draft`).
3. The cloud reply drops or invents placeholders: re-hydration still returns text, unknown placeholders are listed in the `cloud_sent` event and the version rationale, nothing raises. Pinned in Task 8 (`test_rehydrate_restores_and_reports_unknown`) and Task 11 (`test_send_reports_unknown_placeholders`).
4. LM Studio is down or the model is cold during `watch`: the message is stored and routed, the flow logs `draft_failed`, the watcher keeps running. Pinned in Task 10 (`test_llm_failure_logs_draft_failed_and_keeps_message`).
5. The cloud CLI hangs, is logged out, or returns an error envelope: the call is marked `failed` with the error text, the draft stays `pending`, the CLI exits non-zero with the message. Pinned in Task 9 (`test_timeout_raises`, `test_claude_error_envelope_raises`) and Task 11 (`test_cloud_failure_marks_failed`).

---

## File Structure

```
src/chatmaid/llm/base.py                  LLM protocol: think → grammar
src/chatmaid/llm/lmstudio.py              same rename
src/chatmaid/llm/fake.py                  FakeLLM: sequenced responses per prompt
src/chatmaid/llm/jsoncall.py              complete_json, validated, schema_skeleton (moved out of InitPipeline)
src/chatmaid/llm/prompts/triage.md        new prompts (system text + schema), one file each
src/chatmaid/llm/prompts/reply_draft.md
src/chatmaid/llm/prompts/reply_revise.md
src/chatmaid/llm/prompts/pii_tag.md
src/chatmaid/llm/prompts/cloud_refine.md
src/chatmaid/lock.py                      exclusive(path) flock guard
src/chatmaid/store/schema_v2.sql          triage, draft, draft_version, cloud_call
src/chatmaid/store/db.py                  migration ladder + draft/triage/cloud methods
src/chatmaid/config.py                    [draft] and [cloud] sections
src/chatmaid/triage/classify.py           rule_triage, model_triage, triage_message
src/chatmaid/reply/__init__.py
src/chatmaid/reply/context.py             ReplyContext, build_context
src/chatmaid/reply/drafting.py            draft_reply (with lookup round), revise_reply
src/chatmaid/reply/flow.py                ReplyFlow: routines B and C, regenerate, resolve, cloud steps
src/chatmaid/cloud/__init__.py
src/chatmaid/cloud/pseudonym.py           Pseudonymizer, tag_persons, deny_list_for
src/chatmaid/cloud/base.py                CloudBackend protocol, CloudError, CLOUD_SCHEMA
src/chatmaid/cloud/codex.py               CodexBackend
src/chatmaid/cloud/claude.py              ClaudeBackend
src/chatmaid/cloud/fake.py                FakeCloud
src/chatmaid/cloud/registry.py            make_backend
src/chatmaid/cli.py                       drafts, draft …, cloud-calls, cloud-check; watch wiring
tests/conftest.py                         add_row(extra_headers=…), flow_env
tests/test_jsoncall.py … tests/test_cli_drafts.py   one module per new source module
```

## Test fixtures (added to `tests/conftest.py` in Task 10, used by Tasks 10–12)

- `add_row(...)` gains `extra_headers: str = ""`, raw header lines (each ending in `\n`) written after the generated ones.
- `flow_env(watch_env)`: a `FakeLLM(dict(CANNED), dim=4)` where `chatmaid.llm.fake.CANNED` (added in Task 10, also behind the CLI's `--fake-llm`) is
  - `triage`: `{"category":"needs_reply","tier":"work","reason":"asks a question"}`
  - `reply_draft`: `{"candidates":[{"text":"Sure, Friday works.","rationale":"short confirmation"},{"text":"Friday is fine, thanks.","rationale":"warmer"}],"lookup":""}`
  - `reply_revise`: `{"text":"Sure, Friday works. Thanks for the reminder.","rationale":"added thanks"}`
  - `chunk_summary`: `{"summary":"S1","events":[],"facts":[{"about":"Ann","text":"likes tea"}]}`
  - `rolling_summary`: `{"summary":"S"}`
  - `pii_tag`: `{"persons":[]}`

  plus `flow = ReplyFlow(store, connector, llm, config, now=lambda: 1_700_001_000, backend_factory=lambda name: FakeCloud(name or "codex", "m", {"text": "[PERSON_1], Friday works.", "rationale": "ok"}))`, `watcher.on_routed = flow.dispatch`, and a helper `arrive(uid, *, sender="Ann <ann@x.org>", to="me@x.org", body="Can we meet Friday?", folder="INBOX", message_id=None, in_reply_to=None, date=1_700_000_500, subject="meeting", extra_headers="") -> sqlite3.Row` (`message_id` defaults to `f"<m{uid}@x>"`) that calls `add_row`, runs `watcher._handle("timer")` and returns `store.message_by_ref(SourceRef("evolution", "a1", folder, uid).key())`. Returns `SimpleNamespace(**vars(watch_env), llm=llm, flow=flow, arrive=arrive)`.

---

### Task 1: LLM call plumbing: `grammar` flag, `jsoncall` module, sequenced FakeLLM

**Files:**
- Modify: `src/chatmaid/llm/base.py`, `src/chatmaid/llm/lmstudio.py`, `src/chatmaid/llm/fake.py`, `src/chatmaid/init/pipeline.py`, `src/chatmaid/init/passes.py`, `src/chatmaid/cli.py`
- Create: `src/chatmaid/llm/jsoncall.py`
- Test: `tests/test_llm.py`, `tests/test_style_embed.py`, `tests/test_digest.py` (`c["think"]` → `c["grammar"]`), `tests/test_jsoncall.py`

**Interfaces:**
- Produces: `LLM.complete(*, system: str, user: str, model: str, json_schema: dict | None = None, max_tokens: int = 1024, temperature: float = 0.3, grammar: bool = False) -> str` (same for `LMStudioClient` and `FakeLLM`; `think` is gone everywhere).
- Produces: `jsoncall.complete_json(llm: LLM, prompt: Prompt, user: str, model: str, *, grammar: bool, max_tokens: int = 2048) -> dict`, `jsoncall.validated(value, schema: dict)`, `jsoncall.schema_skeleton(schema: dict) -> str`, `jsoncall.RETRY_NOTE`. Behaviour is `InitPipeline._complete_json` moved verbatim: skeleton hint appended to `user` when `grammar` is False, one retry showing the bad reply, then `LLMError`.
- Produces: `InitPipeline._complete_json(prompt, user, model, *, grammar, max_tokens=DIGEST_MAX_TOKENS)` delegating to `jsoncall.complete_json` (kept so `passes.py` and its tests do not move).
- Produces: `FakeLLM(responses: dict[str, str | list[str]] | None = None, *, dim: int = 4)`; a list is consumed one element per call of that prompt, the last element repeats.

- [ ] **Step 1: Rename the flag in the tests**

In `tests/test_llm.py`, `tests/test_style_embed.py` and `tests/test_digest.py` replace every `think=` / `["think"]` with `grammar=` / `["grammar"]` (same values); `test_schema_skeleton_chunk_summary` imports `schema_skeleton` from `chatmaid.llm.jsoncall`. Add to `tests/test_llm.py`:

```python
def test_fake_llm_sequence_responses():
    llm = FakeLLM({"p": ['{"a":1}', '{"a":2}']})
    out = [llm.complete(system="# p\nx", user="u", model="m") for _ in range(3)]
    assert out == ['{"a":1}', '{"a":2}', '{"a":2}']
```

- [ ] **Step 2: Write `tests/test_jsoncall.py`**

```python
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.jsoncall import complete_json, schema_skeleton
from chatmaid.llm.prompts import Prompt

P = Prompt("p", "# p\nreturn json", {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})

def test_retries_once_on_invalid_json():
    llm = FakeLLM({"p": ["not json", '{"summary":"ok"}']})
    assert complete_json(llm, P, "u", "m", grammar=False) == {"summary": "ok"}
    assert len(llm.calls) == 2 and "not json" in llm.calls[1]["user"]

def test_raises_after_two_bad_replies():
    llm = FakeLLM({"p": ["{}", "{}"]})
    with pytest.raises(LLMError):
        complete_json(llm, P, "u", "m", grammar=False)

def test_skeleton_hint_only_without_grammar():
    llm = FakeLLM({"p": '{"summary":"ok"}'})
    complete_json(llm, P, "u", "m", grammar=False)
    complete_json(llm, P, "u", "m", grammar=True)
    assert schema_skeleton(P.schema) in llm.calls[0]["user"]
    assert schema_skeleton(P.schema) not in llm.calls[1]["user"]
```

- [ ] **Step 3: Run the three test modules to verify they fail**

Run: `uv run pytest tests/test_llm.py tests/test_jsoncall.py tests/test_style_embed.py -q`
Expected: failures on `grammar` (unexpected keyword) and `ModuleNotFoundError: chatmaid.llm.jsoncall`.

- [ ] **Step 4: Implement the rename, `jsoncall.py`, and the sequenced fake**

Move `_validated` (as `validated`), `schema_skeleton`, `_missing`, `_RETRY_NOTE` (as `RETRY_NOTE`) and the body of `_complete_json` into `src/chatmaid/llm/jsoncall.py`; `pipeline.py` imports `schema_skeleton` from there if anything else references it and keeps a two-line `_complete_json` delegate. Update every `think=` call site in `pipeline.py`, `passes.py`, `cli.py` (`llm-check` uses `grammar=True`).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q -W error`
Expected: all pass; `grep -rn "think" src tests` returns only comments about thinking models, no parameter named `think`.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "refactor: grammar flag, shared complete_json, sequenced FakeLLM"
```

---

### Task 2: Store migration ladder, single-writer lock, and the parked 1a minors

**Files:**
- Modify: `src/chatmaid/store/db.py`, `src/chatmaid/init/pipeline.py`, `src/chatmaid/cli.py`, `tests/test_daemon.py`
- Create: `src/chatmaid/lock.py`
- Test: `tests/test_store.py`, `tests/test_ingest.py`, `tests/test_lock.py`

**Interfaces:**
- Produces: `Store.SCHEMA_VERSION: int` (1 in this task); `Store.__init__` applies `schema_v{n}.sql` for every `n` in `range(user_version + 1, SCHEMA_VERSION + 1)` (each file ends with its own `PRAGMA user_version = n`).
- Produces: `Store.merge_conversations` sets `tracked = 1` on the kept conversation.
- Produces: `lock.Busy(Exception)`; `lock.exclusive(path: Path)` context manager: creates the file, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, raises `Busy(f"{path} is held by another chatmaid process")` on `BlockingIOError`, releases and closes on exit.
- Produces: `init` and `watch` run inside `exclusive(cfg.data.dir / "chatmaid.lock")`; on `Busy` they print its message to stderr and return 1.
- Produces: `IngestStats.messages` and `skipped_dup` count only committed batches.

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
def test_merge_resets_tracked(store):
    keep = store.upsert_conversation("email", "k1", "t", "thread")
    drop = store.upsert_conversation("email", "k2", "t", "thread")
    store.set_tracked(keep, False)
    with store.transaction():
        store.merge_conversations(keep, drop)
    assert store.conversation(keep)["tracked"] == 1

def test_store_reports_current_schema_version(store):
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == Store.SCHEMA_VERSION
```

`tests/test_lock.py`:
```python
def test_exclusive_blocks_second_holder(tmp_path):
    p = tmp_path / "x.lock"
    with exclusive(p):
        with pytest.raises(Busy):
            with exclusive(p):
                pass
    with exclusive(p):  # released after exit
        pass
```

`tests/test_ingest.py`:
```python
def test_rolled_back_batch_is_not_counted(fake_env, store, monkeypatch):
    real = store.set_checkpoint
    state = {"failed": False}
    def flaky(task, key, value):
        if task == "ingest" and not state["failed"]:
            state["failed"] = True
            raise sqlite3.OperationalError("database is locked")
        real(task, key, value)
    monkeypatch.setattr(store, "set_checkpoint", flaky)
    stats = InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config).ingest(InitOptions())
    assert stats.messages == stats.new_messages + stats.skipped_dup
```

`tests/test_daemon.py`: every `assert watch_env.watcher.handle_trigger("timer") == 0` in a test that does **not** inject a failure (`test_scan_survives_locked_db`, `test_unexpected_error_in_scan_does_not_kill_the_trigger`, `test_malformed_checkpoint_does_not_kill_the_trigger` keep the wrapper) becomes `assert watch_env.watcher._handle("timer") == 0`, so a bug inside `_handle` fails the test instead of being swallowed.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_lock.py tests/test_ingest.py -q`
Expected: `AttributeError: SCHEMA_VERSION`, `ModuleNotFoundError: chatmaid.lock`, tracked assertion fails, counted-messages assertion fails.

- [ ] **Step 3: Implement**

`db.py`: the ladder loop replacing the `user_version == 0` branch; `merge_conversations` adds `tracked = 1` to its final `UPDATE conversation`. `pipeline.ingest`: count `seen`/`skipped` into batch-local variables and add them to the totals after the `with store.transaction()` block completes. `lock.py` as specified. `cli.py`: wrap the bodies of `_init_command` and `_watch_command` (after config load) in `exclusive(...)`; catch `Busy`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q -W error`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "fix: schema ladder, writer lock for init/watch, merge resets tracked, honest ingest counts"
```

---

### Task 3: Schema v2 and the draft, triage and cloud-call store API

**Files:**
- Create: `src/chatmaid/store/schema_v2.sql`
- Modify: `src/chatmaid/store/db.py`
- Test: `tests/test_store_drafts.py`

**Interfaces:**
- Produces: `Store.SCHEMA_VERSION = 2`; `schema_v2.sql`:

```sql
CREATE TABLE triage (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL UNIQUE REFERENCES message(id),
    category TEXT NOT NULL,
    reason TEXT,
    decided_by TEXT NOT NULL,
    model TEXT,
    created_at INTEGER
);
CREATE TABLE cloud_call (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER REFERENCES draft(id),
    backend TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    mapping_json TEXT,
    comment TEXT,
    response TEXT,
    response_hash TEXT,
    error TEXT,
    created_at INTEGER,
    consented_at INTEGER,
    finished_at INTEGER
);
CREATE TABLE draft (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    message_id INTEGER NOT NULL REFERENCES message(id),
    status TEXT NOT NULL,
    language TEXT,
    context_json TEXT,
    final_text TEXT,
    outcome_ref TEXT,
    outcome_preview TEXT,
    created_at INTEGER,
    updated_at INTEGER
);
CREATE TABLE draft_version (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES draft(id),
    round INTEGER NOT NULL,
    candidate INTEGER NOT NULL,
    origin TEXT NOT NULL,
    text TEXT NOT NULL,
    rationale TEXT,
    comment TEXT,
    cloud_call_id INTEGER REFERENCES cloud_call(id),
    created_at INTEGER
);
CREATE INDEX idx_draft_conversation_status ON draft(conversation_id, status);
CREATE INDEX idx_draft_version_draft ON draft_version(draft_id, round, candidate);
PRAGMA user_version = 2;
```

- Produces (all on `Store`, rows are `sqlite3.Row`):
  - `message_by_ref(ref_key: str) -> Row | None`; `message(message_id: int) -> Row | None`
  - `contact(contact_id: int) -> Row | None`; `identities_of(contact_id: int) -> list[Row]` ordered by id
  - `set_conversation_tier(conversation_id: int, tier: str, *, only_if_unset: bool = True) -> None`
  - `memories_for(*, contact_id: int | None = None, conversation_id: int | None = None, limit: int = 20) -> list[Row]`: `superseded_by IS NULL AND (contact_id = ? OR conversation_id = ?)`, ordered `observed_at DESC NULLS LAST, id DESC`; no filter given → `[]`
  - `add_triage(message_id: int, category: str, reason: str, *, decided_by: str, model: str | None = None) -> int` (`INSERT … ON CONFLICT(message_id) DO UPDATE`); `triage_for(message_id: int) -> Row | None`
  - `create_draft(conversation_id: int, message_id: int, *, language: str, context: dict) -> int` (status `pending`, `created_at = updated_at = now`)
  - `add_draft_version(draft_id: int, *, round: int, candidate: int, origin: str, text: str, rationale: str | None, comment: str | None = None, cloud_call_id: int | None = None) -> int`
  - `draft(draft_id: int) -> Row | None`; `draft_versions(draft_id: int) -> list[Row]` ordered `round, candidate`; `next_round(draft_id: int) -> int` (max round + 1, 1 when none)
  - `drafts(*, status: str | None = "pending", limit: int = 50) -> list[Row]` newest `created_at, id` first; `status=None` means all
  - `pending_draft_for(conversation_id: int) -> Row | None`
  - `set_draft_status(draft_id: int, status: str, *, final_text: str | None = None, outcome_ref: str | None = None, outcome_preview: str | None = None) -> None` (COALESCE semantics for the optional fields, bumps `updated_at`)
  - `add_cloud_call(*, draft_id: int | None, backend: str, model: str | None, payload: str, mapping: dict[str, str], comment: str | None) -> int` (status `proposed`)
  - `cloud_call(call_id: int) -> Row | None`; `cloud_calls(*, limit: int = 50) -> list[Row]` newest first
  - `set_cloud_call_status(call_id: int, status: str, *, consented_at: int | None = None, response: str | None = None, response_hash: str | None = None, error: str | None = None) -> None` (sets `finished_at = now` for `sent`, `failed`, `declined`)
  - `counts()` gains `"drafts"` and `"cloud_calls"`.

- [ ] **Step 1: Write `tests/test_store_drafts.py`**

Tests, each a few lines over the `store` fixture plus a conversation and a message inserted with `upsert_conversation`/`upsert_message` (use the `sample_message` fixture):

- `test_v1_database_is_upgraded_to_v2(tmp_path)`: create `tmp_path/"old.sqlite"` with plain `sqlite3`, `executescript` the text of `schema_v1.sql` (via `importlib.resources`), close; `Store(path, embedding_dim=4)`; assert `PRAGMA user_version == 2` and `SELECT name FROM sqlite_master WHERE name='draft'` exists.
- `test_triage_upsert_replaces(store, …)`: add twice for the same message, second category wins, one row.
- `test_draft_lifecycle(store, …)`: `create_draft` → status pending; two versions round 1; `next_round == 2`; `pending_draft_for(cid)["id"] == draft_id`; `set_draft_status(draft_id, "approved", final_text="x")` → `pending_draft_for(cid) is None`, `drafts(status=None)` has one, `drafts()` empty.
- `test_cloud_call_lifecycle(store)`: `add_cloud_call(...)` → status proposed, `mapping_json` round-trips; `set_cloud_call_status(id, "sent", consented_at=1, response="r", response_hash="h")` → `finished_at` not None.
- `test_memories_for_filters_and_orders(store)`: three memories (contact A, conversation 1, unrelated) → `memories_for(contact_id=A)` 1 row, `memories_for(contact_id=A, conversation_id=1)` 2 rows, `memories_for()` empty.
- `test_set_conversation_tier_only_if_unset(store)`.
- `test_counts_include_drafts_and_cloud_calls(store)`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store_drafts.py -q`
Expected: AttributeErrors for the new methods; the upgrade test fails on `user_version == 1`.

- [ ] **Step 3: Implement `schema_v2.sql` and the methods**

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q -W error`
Expected: all pass (existing store tests unaffected; `counts()` callers in tests compare keys they know).

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: schema v2 with triage, drafts, versions and cloud-call audit"
```

---

### Task 4: Config sections `[draft]` and `[cloud]`

**Files:**
- Modify: `src/chatmaid/config.py`, `config.example.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `DraftConfig(model_role: str, candidates: int, recent_messages: int, memories: int, message_chars: int)` defaults `"main", 2, 12, 8, 6000`; `CloudConfig(default_backend: str, codex_model: str, claude_model: str, timeout_s: int, redact_amounts: bool)` defaults `"codex", "gpt-6-astra", "", 300, False`; `Config.draft`, `Config.cloud`; `Config.draft_model -> str` (`lmstudio.model_heavy` when `draft.model_role == "heavy"`, else `lmstudio.model_main`).
- Produces: `ConfigError` when `draft.model_role` is not `main`/`heavy` or `cloud.default_backend` is not `codex`/`claude`.
- `config.example.toml` gains, with these exact keys:

```toml
[draft]
model_role = "main"        # "main" or "heavy": which LM Studio model writes drafts
candidates = 2             # 1..3 candidate replies per draft
recent_messages = 12       # hot-window messages in the context header
memories = 8               # memories per source (direct + vector) in the header
message_chars = 6000       # characters of the incoming message shown to the model

[cloud]
default_backend = "codex"  # "codex" or "claude"; every call still needs explicit consent
codex_model = "gpt-6-astra"
claude_model = ""          # empty: the claude CLI's default model
timeout_s = 300
redact_amounts = false     # also replace money amounts with [AMOUNT_n] placeholders
```

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`

`test_draft_and_cloud_defaults` (load the example; assert the defaults above and `cfg.draft_model == cfg.lmstudio.model_main`), `test_heavy_role_selects_heavy_model` (write a config with `model_role = "heavy"` to `tmp_path`), `test_invalid_model_role_rejected` and `test_invalid_backend_rejected` (`pytest.raises(ConfigError)`). `test_defaults_mirror_example_config` must keep passing, so `_DEFAULTS` gains both sections.

- [ ] **Step 2: Run `uv run pytest tests/test_config.py -q` to verify the new tests fail**

- [ ] **Step 3: Implement the dataclasses, defaults, validation and the example sections**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests config.example.toml
git commit -m "feat: draft and cloud config sections"
```

---

### Task 5: Triage

**Files:**
- Create: `src/chatmaid/triage/classify.py`, `src/chatmaid/llm/prompts/triage.md`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `jsoncall.complete_json` (Task 1), `prompts.load("triage")`.
- Produces: `CATEGORIES = ("needs_reply", "informational", "bulk", "system", "unknown")`, `TIERS = ("family", "work", "finance", "casual", "bulk")`; `@dataclass(frozen=True) Triage(category: str, tier: str | None, reason: str, decided_by: str, model: str | None = None)`.
- Produces: `rule_triage(msg: NormalizedMessage, conversation: sqlite3.Row | None) -> Triage | None`: `msg.body_missing` → `Triage("unknown", None, "body not cached", "rule")`; `msg.bulk` or `"spam" in msg.labels` or (`conversation` and `conversation["bulk"]`) → `("bulk", "bulk", "<which rule>", "rule")`; `"trash" in msg.labels` → `("informational", None, "trash folder", "rule")`; otherwise `None`.
- Produces: `model_triage(llm: LLM, model: str, msg: NormalizedMessage, summary: str | None, *, message_chars: int) -> Triage`: `complete_json(..., grammar=True, max_tokens=256)` with user text `From: {sender name <email>}\nSubject: {subject}\nConversation so far: {summary or "(none)"}\n\n{text[:message_chars]}`; a category outside the first four → `category="unknown"` with reason `f"model returned {value!r}"`; a tier outside `TIERS` → `None`; `decided_by="model"`, `model=model`.
- Produces: `triage_message(llm, model, msg, conversation, *, message_chars) -> Triage` = rule result or model result.
- Prompt `triage.md`: first line `# triage`; system text tells the model it classifies one incoming email for the owner's assistant into exactly one of `needs_reply` (a person expects an answer from the owner), `informational` (FYI, receipts, confirmations, replies that close a thread), `bulk` (newsletters, marketing, notifications), `system` (automated accounts, bounces, calendar machinery), plus the relationship tier, returning JSON only; schema block:

```json
{"type":"object","properties":{"category":{"type":"string"},"tier":{"type":"string"},"reason":{"type":"string"}},"required":["category","tier","reason"],"additionalProperties":false}
```

- [ ] **Step 1: Write `tests/test_triage.py`** using `sample_message` (from conftest) and `dataclasses.replace`:

`test_rule_bulk_header` (`replace(msg, bulk=True)` → category bulk, decided_by rule), `test_rule_body_missing_is_unknown`, `test_rule_spam_label_is_bulk`, `test_rule_bulk_conversation` (a fake conversation `{"bulk": 1}` passed as a dict-like row is fine: use `sqlite3.Row`-free `dict`), `test_rule_returns_none_for_ordinary_mail`, `test_model_triage_parses` (FakeLLM `triage` → `{"category":"needs_reply","tier":"work","reason":"r"}`; assert the user text contains the subject and `grammar is True` in `llm.calls[0]`), `test_model_invalid_category_is_unknown`, `test_model_not_called_when_rule_decides` (`len(llm.calls) == 0`).

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: rule and model triage for incoming mail"
```

---

### Task 6: Context header

**Files:**
- Create: `src/chatmaid/reply/__init__.py`, `src/chatmaid/reply/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `Store.memories_for`, `contact`, `identities_of`, `messages_for`, `style_profile`, `search_embeddings` (Task 3 and 1a); `detect_language`; `Connector.fetch`.
- Produces: `@dataclass ReplyContext(contact_card: str, summary: str, recent: list[tuple[int, str, str]], memories: list[str], style: str | None, language: str, tier: str | None, message: str)` with `to_json() -> dict`, `@classmethod from_json(d: dict) -> ReplyContext`, and `render() -> str` producing, in this order, the sections `## Contact`, `## Conversation summary` (`(none)` when empty), `## Recent messages` (oldest first, one line each `[YYYY-MM-DD who] text`, `(none)`), `## Relevant memories` (`- ` bullets), `## Owner's style` (omitted when `style` is None), `## Message to answer`.
- Produces: `build_context(store: Store, connector: Connector, llm: LLM, cfg: Config, conversation_id: int, msg: NormalizedMessage, *, now: int | None = None) -> ReplyContext`:
  - `language = detect_language(msg.text)`; `tier = conversation["tier"]`.
  - contact card from the sender identity's contact: `"{name or address} <{addr1}, {addr2}>; tier {tier or '?'}"` plus `; notes: …` when `contact["notes"]`.
  - recent: `store.messages_for(cid, since=now - cfg.data.hot_window_days * 86400, limit=cfg.draft.recent_messages, newest_first=True)` minus the row whose `ref_key == msg.ref.key()`; each row's text is `connector.fetch(ref).text` truncated to 1,500 characters, falling back to the row's `preview` on `KeyError`, `OSError`, `sqlite3.Error` or `body_missing`; the sender label is `store.identity_label(row["sender_identity_id"])`; returned oldest first.
  - memories: `store.memories_for(contact_id=sender contact, conversation_id=cid, limit=cfg.draft.memories)` texts, then vector neighbours of `llm.embed([msg.text[:2000]], model=cfg.lmstudio.model_embedding)[0]` through `store.search_embeddings(vec, k=cfg.draft.memories, kind="memory")` resolved with `store.memory`; `LLMError` from `embed` is logged and skipped; deduplicated preserving order, capped at `2 * cfg.draft.memories`.
  - style: `store.style_profile(language, tier or "default") or store.style_profile(language, "default")`.
  - message: `msg.text[:cfg.draft.message_chars]`.

- [ ] **Step 1: Write `tests/test_context.py`** over `digested_env` (summary `S`, memory `Ann: likes tea`), `now=1_700_001_000`, the incoming message = `digested_env.connector.fetch(SourceRef("evolution","a1","INBOX","1"))` and its conversation id from `store.message_by_ref(...)["conversation_id"]`:

- `test_context_has_summary_recent_memories_and_style`: set `store.set_style_profile("en", "default", "terse", [])`; assert `ctx.summary == "S"`, `"likes tea" in ctx.memories[0]`, `ctx.style == "terse"`, `ctx.language == "en"`, the recent list holds the Sent reply (`"thanks, see you tomorrow" in text`) and not the incoming message itself.
- `test_recent_excludes_messages_outside_hot_window`: `now = 1_700_000_100 + 400 * 86400` → recent empty.
- `test_unfetchable_recent_falls_back_to_preview`: `store.upsert_message(cid, replace(sample_message, ref=SourceRef("evolution","a1","INBOX","999"), message_id="<ghost@x>", sent_at=1_700_000_200), sender_id)` → that row appears with its preview text.
- `test_embed_failure_does_not_block_context` (`monkeypatch.setattr(llm, "embed", raising LLMError)` → context built, direct memories still present).
- `test_render_sections_order_and_roundtrip` (section headers appear in order; `ReplyContext.from_json(ctx.to_json()) == ctx`).

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: reply context header from contact, summary, hot window, memories and style"
```

---

### Task 7: Drafting and revising with the local model

**Files:**
- Create: `src/chatmaid/reply/drafting.py`, `src/chatmaid/llm/prompts/reply_draft.md`, `src/chatmaid/llm/prompts/reply_revise.md`
- Test: `tests/test_drafting.py`

**Interfaces:**
- Consumes: `ReplyContext` (Task 6), `complete_json` (Task 1), `lookup.search_history` (1a), `cfg.draft_model` (Task 4).
- Produces: `@dataclass(frozen=True) Candidate(text: str, rationale: str)`.
- Produces: `draft_reply(store: Store, connector: Connector, llm: LLM, cfg: Config, ctx: ReplyContext, *, sender_email: str) -> list[Candidate]`: user = `ctx.render() + f"\n\nWrite {cfg.draft.candidates} candidate replies as the owner."`; `complete_json(llm, prompts.load("reply_draft"), user, cfg.draft_model, grammar=False, max_tokens=2048)`; when `out["lookup"]` is non-empty and `out["candidates"]` is empty, run `search_history(store, connector, llm, query=out["lookup"], contact=sender_email, limit=5, embedding_model=cfg.lmstudio.model_embedding)`, append `"\n\n## History lookup for: {query}\n"` with one line per hit `[YYYY-MM-DD sender] subject — snippet` (or `(nothing found)`) and `"\nThe lookup is done; now write the candidates."`, and call once more; still no candidates → `LLMError("reply_draft: no candidates")`. Candidates with empty text are dropped; at most `cfg.draft.candidates` are returned.
- Produces: `revise_reply(llm: LLM, cfg: Config, ctx: ReplyContext, previous: str, comment: str) -> Candidate`: user = `ctx.render() + "\n\n## Previous draft\n" + previous + "\n\n## Owner's feedback\n" + comment`; prompt `reply_revise`, `grammar=False`, `max_tokens=1024`.
- Prompts: `reply_draft.md` (first line `# reply_draft`): the model writes replies **as the owner**, in the language of the message to answer, following the style section, never inventing facts, never adding signatures or subject lines; if an essential fact is missing from the header it sets `lookup` to a short search query and returns an empty `candidates` list, otherwise `lookup` is `""`; each candidate has a one-line `rationale`. Schema:

```json
{"type":"object","properties":{"candidates":{"type":"array","items":{"type":"object","properties":{"text":{"type":"string"},"rationale":{"type":"string"}},"required":["text","rationale"],"additionalProperties":false}},"lookup":{"type":"string"}},"required":["candidates","lookup"],"additionalProperties":false}
```

`reply_revise.md` (`# reply_revise`): rewrite the previous draft applying the owner's feedback, same voice and language, JSON only. Schema:

```json
{"type":"object","properties":{"text":{"type":"string"},"rationale":{"type":"string"}},"required":["text","rationale"],"additionalProperties":false}
```

- [ ] **Step 1: Write `tests/test_drafting.py`** with a hand-built `ReplyContext` and `digested_env` for the lookup round:

`test_draft_returns_candidates` (two candidates, `llm.calls[0]["grammar"] is False`, model == `cfg.draft_model`), `test_draft_runs_one_lookup_round` (`FakeLLM({"reply_draft": ['{"candidates":[],"lookup":"office"}', '{"candidates":[{"text":"ok","rationale":"r"}],"lookup":""}']})` → one candidate, two `reply_draft` calls, `"History lookup for: office"` in the second user text and `"thanks, see you tomorrow"`'s hit line in it), `test_draft_without_candidates_after_lookup_raises`, `test_draft_caps_candidates_and_drops_empty`, `test_revise_includes_previous_and_comment`.

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: local draft and revise calls with one history-lookup round"
```

---

### Task 8: Pseudonymizer

**Files:**
- Create: `src/chatmaid/cloud/__init__.py`, `src/chatmaid/cloud/pseudonym.py`, `src/chatmaid/llm/prompts/pii_tag.md`
- Test: `tests/test_pseudonym.py`

**Interfaces:**
- Produces: `EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")`, `PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d \-]{7,}\d(?![\w.])")`, `AMOUNT_RE = re.compile(r"(?:[$¥€£]|RMB|USD|CNY)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:元|美元|块钱|块)")`.
- Produces: `class Pseudonymizer`:
  - `__init__(self, deny: Iterable[tuple[str, str]], *, redact_amounts: bool = False)` where each pair is `(original, kind)` with kind `PERSON` or `EMAIL`; empty or one-character originals are ignored.
  - `redact(self, text: str) -> str`: deny-list originals first, longest first, ASCII originals matched case-insensitively as whole words, others as plain substrings; then `EMAIL_RE`, `PHONE_RE`, and `AMOUNT_RE` when enabled. The same original (case-insensitive) always yields the same placeholder; numbering per kind in first-seen order across calls: `[PERSON_1]`, `[EMAIL_1]`, `[PHONE_1]`, `[AMOUNT_1]`.
  - `mapping` property: `dict[str, str]` placeholder → original (first spelling seen).
  - `@staticmethod rehydrate(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]`: replaces every mapped placeholder; the list holds placeholders matching `\[(?:PERSON|EMAIL|PHONE|AMOUNT)_\d+\]` found in `text` but absent from `mapping` (left as they are).
- Produces: `tag_persons(llm: LLM, model: str, text: str) -> list[str]`: `complete_json` with prompt `pii_tag` (`# pii_tag`; "list the personal names of people mentioned in this text, JSON only"; schema `{"type":"object","properties":{"persons":{"type":"array","items":{"type":"string"}}},"required":["persons"],"additionalProperties":false}`), `grammar=True`, `max_tokens=256`; keeps names of at least two characters that occur in `text`.
- Produces: `deny_list_for(store: Store, cfg: Config, conversation_id: int) -> list[tuple[str, str]]`: owner `cfg.owner.name` and `cfg.owner.aliases` as PERSON; every identity of the owner contact and of each participant contact: `address` as EMAIL, `display_name` as PERSON; each participant `contact["name"]` as PERSON.

- [ ] **Step 1: Write `tests/test_pseudonym.py`**

```python
def test_names_and_emails_become_placeholders():
    p = Pseudonymizer([("Ann Lee", "PERSON"), ("ann@x.org", "EMAIL")])
    out = p.redact("Hi Ann Lee, mail me at ann@x.org or bob@y.com")
    assert out == "Hi [PERSON_1], mail me at [EMAIL_1] or [EMAIL_2]"
    assert p.mapping == {"[PERSON_1]": "Ann Lee", "[EMAIL_1]": "ann@x.org", "[EMAIL_2]": "bob@y.com"}
```

plus `test_same_name_same_placeholder_across_calls` ("ann lee" and "Ann Lee" → `[PERSON_1]` in two `redact` calls), `test_longest_match_first` (deny `Ann` and `Ann Lee`: "Ann Lee and Ann" → `[PERSON_1] and [PERSON_2]`), `test_whole_word_for_ascii` ("Annie" is not touched by deny `Ann`), `test_chinese_name_is_substring_matched` (`("李强","PERSON")`, "李强明天来" → `[PERSON_1]明天来`), `test_phone_and_amount` (`"+86 138 0013 8000"` → `[PHONE_1]`; `"¥1,200"` untouched by default and `[AMOUNT_1]` with `redact_amounts=True`), `test_rehydrate_restores_and_reports_unknown` (`rehydrate("[PERSON_1] owes [AMOUNT_9]", {"[PERSON_1]": "Ann"}) == ("Ann owes [AMOUNT_9]", ["[AMOUNT_9]"])`), `test_tag_persons_filters_absent_and_short_names` (FakeLLM `pii_tag` → `{"persons":["Ann","Zed","李"]}` on text "Ann and 李强" → `["Ann"]`), `test_deny_list_for_includes_owner_and_participants` (over `ingested_env`: contains `("Jiaqi","PERSON")`, `("fishy","PERSON")`, `("me@x.org","EMAIL")`, `("ann@x.org","EMAIL")`, `("Ann","PERSON")`).

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: reversible pseudonymizer with contact deny-list and small-model name tagging"
```

---

### Task 9: Cloud backends (`codex`, `claude`, fake) and `cloud-check`

**Files:**
- Create: `src/chatmaid/cloud/base.py`, `src/chatmaid/cloud/codex.py`, `src/chatmaid/cloud/claude.py`, `src/chatmaid/cloud/fake.py`, `src/chatmaid/cloud/registry.py`
- Modify: `src/chatmaid/cli.py`
- Test: `tests/test_cloud_backends.py`

**Interfaces:**
- Produces (`base.py`): `class CloudError(Exception)`; `class CloudBackend(Protocol): name: str; model: str; def complete(self, prompt: str, schema: dict) -> dict`; `CLOUD_SCHEMA = {"type":"object","properties":{"text":{"type":"string"},"rationale":{"type":"string"}},"required":["text","rationale"],"additionalProperties":False}`; `scrubbed_env() -> dict[str, str]` (a copy of `os.environ` without `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT`); `run_cli(argv: list[str], *, cwd: Path, timeout_s: int, runner=subprocess.run) -> subprocess.CompletedProcess` calling `runner(argv, cwd=cwd, env=scrubbed_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s)` and converting `subprocess.TimeoutExpired` and `FileNotFoundError` into `CloudError`.
- Produces (`codex.py`): `CodexBackend(model: str, *, timeout_s: int, runner=subprocess.run)` with `name = "codex"`; `complete` creates a `tempfile.TemporaryDirectory`, writes `schema.json` there, creates `empty/` inside it, runs `["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "-s", "read-only", "-C", str(empty), "--skip-git-repo-check", "--color", "never", "--output-schema", str(schema_path), "-o", str(out_path), *(["-m", model] if model else []), prompt]` with `cwd=empty`; non-zero return → `CloudError(f"codex exited {rc}: {stderr[-500:]}")`; reads `out_path` as JSON (missing or invalid → `CloudError`).
- Produces (`claude.py`): `ClaudeBackend(model: str, *, timeout_s: int, runner=subprocess.run)` with `name = "claude"`; argv `["claude", "-p", "--tools", "", "--no-session-persistence", "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config", "--output-format", "json", "--json-schema", json.dumps(schema), *(["--model", model] if model else []), prompt]`, `cwd` = an empty temp dir; parses stdout JSON; `is_error` true or `subtype != "success"` or missing `structured_output` → `CloudError(envelope.get("result") or "claude returned no structured output")`; returns `structured_output`.
- Produces (`fake.py`): `FakeCloud(name: str = "fake", model: str = "fake", response: dict | Exception | None = None)` recording `self.prompts: list[str]`; raises `response` when it is an exception, else returns it (default `{"text": "ok", "rationale": "fake"}`).
- Produces (`registry.py`): `make_backend(cfg: Config, name: str | None = None) -> CloudBackend` (`name or cfg.cloud.default_backend`; `codex` → `CodexBackend(cfg.cloud.codex_model, timeout_s=cfg.cloud.timeout_s)`, `claude` → `ClaudeBackend(cfg.cloud.claude_model, …)`, else `CloudError(f"unknown cloud backend {name!r}")`).
- Produces (`cli.py`): `chatmaid cloud-check [--backend codex|claude]` sends the canned prompt `Reply with text "ok" and a one-word rationale. Keep placeholders like [PERSON_1] verbatim: [PERSON_1] says hi.` with `CLOUD_SCHEMA`, prints `backend=<name> model=<model>` then the two fields; `CloudError` → message on stderr, exit 1.

- [ ] **Step 1: Write `tests/test_cloud_backends.py`** with a `runner` fake that records `argv`, `cwd`, `env`, `kwargs` and returns a `subprocess.CompletedProcess`:

`test_codex_command_shape_and_output_file` (runner writes `{"text":"t","rationale":"r"}` to the `-o` path; assert the argv prefix through `--color never`, `-m gpt-x` present, cwd is an empty directory, `stdin is subprocess.DEVNULL`, result dict), `test_codex_omits_model_flag_when_empty`, `test_codex_nonzero_exit_raises`, `test_claude_command_shape_and_structured_output` (stdout = `json.dumps({"type":"result","subtype":"success","is_error":False,"structured_output":{"text":"t","rationale":"r"}})`), `test_claude_error_envelope_raises` (`{"is_error":True,"result":"Not logged in"}` → `CloudError` whose message contains `Not logged in`), `test_env_scrubbed_of_claudecode` (`monkeypatch.setenv("CLAUDECODE","1")`; recorded env lacks it), `test_timeout_raises` (runner raises `subprocess.TimeoutExpired`), `test_registry_default_is_codex_with_configured_model`, `test_cli_cloud_check_prints_fields` (monkeypatch `chatmaid.cli.make_backend` → `FakeCloud`).

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: codex and claude cloud backends behind one interface, cloud-check command"
```

---

### Task 10: `ReplyFlow` routines B and C, regenerate, resolve

**Files:**
- Create: `src/chatmaid/reply/flow.py`
- Modify: `src/chatmaid/init/pipeline.py` (`_render` → module-level `render_messages(msgs)`, `_add_memories` → module-level `add_memories(store, out, source_ref, cid, contact_id) -> int`; the pipeline calls them), `src/chatmaid/llm/fake.py` (`CANNED: dict[str, str]` holding the six responses listed under Test fixtures), `tests/conftest.py` (fixtures above)
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: Tasks 3–7, `store_message`/`build_thread_index` (1a), `make_preview`.
- Produces: `class ReplyFlow`:
  - `__init__(self, store: Store, connector: Connector, llm: LLM, cfg: Config, *, now: Callable[[], int] | None = None, backend_factory: Callable[[str | None], CloudBackend] | None = None)` (`now` defaults to `lambda: int(time.time())`; `backend_factory` defaults to `lambda name: make_backend(cfg, name)` and is used in Task 11).
  - `dispatch(self, route: str, conversation_id: int, msg: NormalizedMessage) -> None`: `"owner"` → `on_owner_message`, else `on_incoming`; `LLMError` is caught and logged as event `("reply", "draft_failed" | "owner_failed", msg.ref.key(), {"error": str(e)})`; other exceptions propagate (the watcher logs them).
  - `on_incoming(self, conversation_id: int, msg: NormalizedMessage) -> int | None` (draft id): `row = store.message_by_ref(msg.ref.key())` (`None` → `ValueError`); `msg.is_owner` → `None`; `build_context` is called with `now=self.now()`; `t = triage_message(llm, cfg.lmstudio.model_small, msg, store.conversation(cid), message_chars=cfg.draft.message_chars)`; `store.add_triage(row["id"], t.category, t.reason, decided_by=t.decided_by, model=t.model)`; `t.tier` → `store.set_conversation_tier(cid, t.tier)`; event `("reply", "triage", ref, {"category", "reason", "decided_by"})`; category ≠ `needs_reply` → `None`. Otherwise: `pending = store.pending_draft_for(cid)` → `set_draft_status(pending["id"], "superseded")`; `ctx = build_context(...)`; `cands = draft_reply(..., sender_email=msg.sender.email)`; in one `store.transaction()`: `create_draft(cid, row["id"], language=ctx.language, context=ctx.to_json())`, one `add_draft_version(round=1, candidate=i, origin="local", text, rationale)` per candidate, event `("reply", "draft", ref, {"draft_id": id, "candidates": n})`; return the id.
  - `on_owner_message(self, conversation_id: int, msg: NormalizedMessage) -> None`: `pending = store.pending_draft_for(cid)`; if any: compare `" ".join(msg.text.split())` with each version's normalised text; equal → `approved` else `edited`; `set_draft_status(id, status, final_text=<the matching version text or None>, outcome_ref=msg.ref.key(), outcome_preview=make_preview(msg.text))`; event `("reply", "draft_resolved", ref, {"draft_id", "status"})`. Then `update_summary(cid, msg)`.
  - `update_summary(self, conversation_id: int, msg: NormalizedMessage) -> bool`: conversation summary `None` or `msg.text.strip()` empty → `False` (digest will cover it); else `out = complete_json(llm, prompts.load("chunk_summary"), render_messages([msg]), cfg.lmstudio.model_small, grammar=True)`, `merged = complete_json(llm, prompts.load("rolling_summary"), f"Previous summary:\n{summary}\n\nNew chunk summary:\n{out['summary']}", cfg.lmstudio.model_main, grammar=False)`; in one transaction `set_conversation_summary(cid, merged["summary"], events=existing events + out["events"])` and `add_memories(store, out, msg.ref.key(), cid, contact_id)` where `contact_id` is the single non-owner participant or `None`; returns `True`. (Routine C's "add a style sample" needs no code: the owner message is in the store, and the style pass scans the newest owner messages.)
  - `regenerate(self, draft_id: int, comment: str, *, from_version: int | None = None) -> int` (new version id): draft must be `pending` (`ValueError` otherwise); `previous` = the text of `from_version` or of the latest round's candidate 1; `ctx = ReplyContext.from_json(json.loads(draft["context_json"]))`; `cand = revise_reply(...)`; `add_draft_version(draft_id, round=store.next_round(draft_id), candidate=1, origin="local", text=cand.text, rationale=cand.rationale, comment=comment)`; event `("reply", "draft_regenerated", …)`.
  - `resolve(self, draft_id: int, action: str, *, text: str | None = None, version_id: int | None = None) -> None`: pending only; `approve` → status `approved`, `final_text` = `text` or the chosen version's text (`version_id`, else latest round candidate 1); `edit` → `text` required (`ValueError`), adds a version `origin="owner"` with that text, status `edited`, `final_text=text`; `dismiss` → `dismissed`; other → `ValueError`.
  - `latest_text(self, draft_id: int) -> str`.

- [ ] **Step 1: Add the fixtures to `tests/conftest.py`** (section "Test fixtures" above) and write `tests/test_flow.py`:

- `test_incoming_needs_reply_creates_draft_with_two_candidates`: `row = env.arrive("100")`; `drafts()` has one, `draft_versions` two with `origin == "local"`, `triage_for(row["id"])["category"] == "needs_reply"`, conversation tier `work`, event kinds include `triage` and `draft`.
- `test_bulk_incoming_is_recorded_by_rule_without_model_call`: `arrive("100", sender="news@x.org", extra_headers="List-Unsubscribe: <mailto:u@x>\n")` → triage `bulk`/`rule`, no draft, no `triage` call in `llm.calls`.
- `test_body_missing_incoming_makes_no_draft`: `env.add_row("INBOX", uid="100", message_id="<m100@x>", sender="Ann <ann@x.org>", to="me@x.org", date=1_700_000_500, body="x")`, then delete `env.cache_dir / "a1" / "folders" / "INBOX" / "cur" / "00" / "100"`, then call `env.watcher._handle("timer")` four times (three body waits, then routed as is) → triage `unknown` by rule, no draft, no `triage` entry in `env.llm.calls`.
- `test_informational_makes_no_draft` (`env.llm.responses["triage"] = '{"category":"informational","tier":"work","reason":"fyi"}'`).
- `test_new_incoming_supersedes_pending_draft` (`arrive("100")`, `arrive("101", in_reply_to="<m100@x>")` with the first message's Message-ID → first draft `superseded`, one pending).
- `test_owner_reply_matching_candidate_resolves_approved`: after `arrive("100")`, `arrive("200", sender="me@x.org", to="ann@x.org", folder="[Gmail]/Sent Mail", body="Sure, Friday works.", in_reply_to=<id of 100>)` → status `approved`, `outcome_ref` set, `outcome_preview == "Sure, Friday works."`, event `draft_resolved`.
- `test_owner_reply_with_other_text_resolves_edited`.
- `test_owner_message_updates_summary_and_memories`: `set_conversation_summary(cid, "S0")` before the owner reply → summary `"S"`, memory count +1, no update when summary was `None`.
- `test_regenerate_adds_round_two_with_comment` (`origin == "local"`, `comment == "shorter"`, `"## Previous draft"` and `"shorter"` in the `reply_revise` call's user text).
- `test_resolve_edit_adds_owner_version_and_final_text`; `test_resolve_dismiss`; `test_resolve_non_pending_raises`.
- `test_llm_failure_logs_draft_failed_and_keeps_message` (`env.llm.fail_next(2)` before `arrive` → message row exists, no draft, an event `draft_failed`, `_handle` returned 1).
- `test_dispatch_owner_route_never_drafts`.

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: reply flow: triage, draft, owner-resolution, regenerate and resolve"
```

---

### Task 11: Cloud improvement: propose, send, decline

**Files:**
- Modify: `src/chatmaid/reply/flow.py`
- Create: `src/chatmaid/llm/prompts/cloud_refine.md`
- Test: `tests/test_flow_cloud.py`

**Interfaces:**
- Consumes: `Pseudonymizer`, `tag_persons`, `deny_list_for` (Task 8); `CloudBackend`, `CLOUD_SCHEMA`, `CloudError` (Task 9); Task 3 cloud-call methods.
- Produces: `@dataclass(frozen=True) CloudProposal(call_id: int, backend: str, model: str, payload: str)`.
- Produces: `ReplyFlow.propose_cloud(self, draft_id: int, *, backend: str | None = None, comment: str | None = None, from_version: int | None = None) -> CloudProposal`: draft must be `pending`; `b = self.backend_factory(backend)`; `previous` as in `regenerate`; `ctx = ReplyContext.from_json(...)`; deny = `deny_list_for(store, cfg, cid)` + `(name, "PERSON")` for each `tag_persons(llm, cfg.lmstudio.model_small, previous + "\n" + ctx.message)` (an `LLMError` here is logged and skipped); `p = Pseudonymizer(deny, redact_amounts=cfg.cloud.redact_amounts)`; `payload = prompts.load("cloud_refine").system + "\n\n## Previous draft\n" + p.redact(previous) + "\n\n## Owner's feedback\n" + (p.redact(comment) if comment else "(none)") + "\n\n## Conversation summary\n" + (p.redact(ctx.summary) or "(none)") + "\n\n## Message being answered\n" + p.redact(ctx.message[:1500])`; `call_id = store.add_cloud_call(draft_id=draft_id, backend=b.name, model=b.model, payload=payload, mapping=p.mapping, comment=comment)`; event `("cloud", "proposed", str(call_id), {"backend", "draft_id", "chars": len(payload)})`.
- Produces: `ReplyFlow.send_cloud(self, call_id: int) -> int` (new version id): call status must be `proposed` (`ValueError`); `b = self.backend_factory(call["backend"])`; `out = b.complete(call["payload"], CLOUD_SCHEMA)`; `CloudError` → `set_cloud_call_status(call_id, "failed", error=str(e))`, event `("cloud", "failed", …)`, re-raise; otherwise `response = json.dumps(out, ensure_ascii=False)`, `text, unknown = Pseudonymizer.rehydrate(out["text"], mapping)`, `rationale, _ = Pseudonymizer.rehydrate(out["rationale"], mapping)`; in one transaction: `set_cloud_call_status(call_id, "sent", consented_at=now(), response=response, response_hash=sha256(response).hexdigest())`, `add_draft_version(draft_id, round=next_round, candidate=1, origin=f"cloud:{backend}", text=text, rationale=rationale + (f" [unknown placeholders: {', '.join(unknown)}]" if unknown else ""), comment=call["comment"], cloud_call_id=call_id)`, event `("cloud", "sent", str(call_id), {"version_id", "unknown_placeholders": unknown})`.
- Produces: `ReplyFlow.decline_cloud(self, call_id: int) -> None`: `proposed` → `declined` plus event `("cloud", "declined", …)`.
- Prompt `cloud_refine.md` (`# cloud_refine`): "You improve a reply draft written by the owner of a personal mail assistant. Keep the owner's voice, language and intent; apply the feedback if given; keep every placeholder such as [PERSON_1] or [EMAIL_2] exactly as written; return JSON with `text` (the improved draft) and `rationale` (one line)." Schema = `CLOUD_SCHEMA`.

- [ ] **Step 1: Write `tests/test_flow_cloud.py`** over `flow_env` with `env.arrive("100")` and `draft_id = env.store.drafts()[0]["id"]`; the fake backend returns `{"text": "[PERSON_1], Friday works.", "rationale": "ok"}`:

- `test_propose_redacts_participants_and_owner` (`env.llm.responses["reply_draft"]` already yields candidates; set the candidate text through `env.flow.resolve`? no: call `propose_cloud` on the stored draft and assert `"Ann" not in payload`, `"ann@x.org" not in payload`, `"me@x.org" not in payload`, `"[EMAIL_" in payload or "[PERSON_" in payload`, the call row is `proposed` with a mapping that maps back to `ann@x.org`).
- `test_payload_contains_sections_and_comment` (`comment="shorter"` → `"## Owner's feedback\nshorter"` in payload; `"## Message being answered"` present).
- `test_send_uses_stored_payload_verbatim_and_rehydrates` (`FakeCloud.prompts[0] == call["payload"]`; version `origin == "cloud:codex"`; `"Ann" in version text` once the mapping holds `[PERSON_1] → Ann` — add `("Ann", "PERSON")` by making the draft text mention `Ann`: set `env.llm.responses["reply_draft"]` to a candidate `"Thanks Ann, Friday works."` before `arrive`); status `sent`, `response_hash == sha256(response).hexdigest()`, `consented_at == 1_700_001_000`.
- `test_send_reports_unknown_placeholders` (backend returns `"[PERSON_7] ok"` → version text keeps `[PERSON_7]`, rationale ends with `[unknown placeholders: [PERSON_7]]`, event payload lists it).
- `test_decline_marks_declined_and_adds_no_version`; `test_send_twice_raises`; `test_cloud_failure_marks_failed_and_draft_stays_pending` (`backend_factory` returning `FakeCloud(response=CloudError("Not logged in"))` → `pytest.raises(CloudError)`, call `failed` with the error text, draft `pending`).
- `test_watcher_never_calls_cloud` (after `arrive`, `FakeCloud.prompts == []` and `cloud_calls() == []`).
- `test_propose_on_non_pending_raises`.

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: consent-gated cloud improvement with pseudonymized payload and audit"
```

---

### Task 12: CLI: drafts, draft actions, cloud-calls, watch wiring

**Files:**
- Modify: `src/chatmaid/cli.py`
- Test: `tests/test_cli_drafts.py`

**Interfaces:**
- Produces: `_open_flow(cfg: Config, *, need_llm: bool, fake_llm: bool = False) -> tuple[Store, EvolutionConnector, ReplyFlow]`: builds the store (`_open_store`), accounts/owner/connector as `_watch_command` does, and the LLM: `FakeLLM()` when `fake_llm`, `_NoLLM()` when not `need_llm`, else `LMStudioClient` from the token (`ConfigError` propagates; callers print it and return 1).
- Produces these commands (all through `register_<name>`; `draft` has its own sub-subparsers via `p.add_subparsers(dest="draft_command", required=True)`):
  - `drafts [--status pending|all] [--limit 50]`: header `id  conv  status  created  subject  text`, one line per draft (`created` as `YYYY-MM-DD`, `subject` from the message row, `text` = first 60 characters of the latest version's text).
  - `draft show <id>`: status, language, conversation id, triage line, then each version `v<version_id>  round <r>.<c>  <origin>  [comment: …]` followed by the indented text, then each cloud call `call <id>  <backend>/<model>  <status>`.
  - `draft run --message <message_row_id> [--fake-llm]`: fetches the row's ref through the connector and calls `flow.on_incoming(row["conversation_id"], msg)`; prints `draft <id>` or `no draft: <category> (<reason>)`; an owner message → stderr `message <id> is the owner's` exit 1; unknown id → exit 1.
  - `draft regenerate <id> --comment TEXT [--from VERSION] [--fake-llm]`: prints `v<id>` and the new text.
  - `draft approve <id> [--version V]`, `draft edit <id> (--text TEXT | --file PATH)`, `draft dismiss <id>`; `ValueError` from the flow → stderr, exit 1.
  - `draft cloud <id> [--backend codex|claude] [--comment TEXT] [--from VERSION] [--fake-llm]`: `propose_cloud`, then prints exactly:
    ```
    ----- payload to <backend> (<model>), <n> chars -----
    <payload>
    ----- end of payload; proposal <call_id> -----
    ```
    If `sys.stdin.isatty()`: asks `Send this to <backend>? [y/N] `; `y`/`yes` → `send_cloud`, prints `v<id>` and the text; anything else → `decline_cloud`, prints `declined`. Not a TTY: prints `run: chatmaid draft send <call_id>  (or: chatmaid draft decline <call_id>)`.
  - `draft send <call_id>` and `draft decline <call_id>`: as named; `CloudError` → stderr, exit 1.
  - `cloud-calls [--limit 50]`: `id  draft  backend  model  status  created  chars`.
  - `watch` gains `--no-draft`; without it the command builds the LM Studio client (missing token → stderr, exit 1), a `ReplyFlow`, and sets `watcher.on_routed` to a function that prints the 1a line and then calls `flow.dispatch(route, cid, msg)`.
- `make_backend` is imported into `cli.py` as a module attribute so tests can monkeypatch `chatmaid.cli.make_backend`; `_open_flow` passes `backend_factory=lambda name: make_backend(cfg, name)`.

- [ ] **Step 1: Write `tests/test_cli_drafts.py`**

Fixture `cli_draft_env(fake_env)`: like `cli_env`, ingest into `fake_env.config.index_path`, look up `row_id = store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "1").key())["id"]` (Ann's message), close the store, and return `fake_env` plus `row_id`. `--fake-llm` on the CLI builds `FakeLLM(dict(CANNED))`, so `draft run --message <row_id> --fake-llm` produces draft 1 with two candidates. Tests:

`test_cli_draft_run_and_list` (exit 0, prints `draft 1`; `drafts` lists it with status pending and the subject), `test_cli_draft_show_lists_versions`, `test_cli_regenerate_adds_version`, `test_cli_approve_edit_dismiss` (approve → status approved; a second `approve` → exit 1; `edit --text` on another draft), `test_cli_cloud_propose_non_tty_then_send` (monkeypatch `chatmaid.cli.make_backend` to return `FakeCloud`; `draft cloud` prints the payload block and `proposal 1`, `cloud-calls` shows `proposed`; `draft send 1` → `v`, status `sent`), `test_cli_cloud_decline`, `test_cli_watch_without_token_fails_cleanly` (config with a missing token file → exit 1, stderr mentions the token file; `--no-draft` does not need the token: assert it is accepted by `build_parser`).

- [ ] **Step 2: Run the new test module to verify it fails**

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run `uv run pytest -q -W error`; all pass**

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: draft CLI (list, show, run, regenerate, approve, edit, dismiss, cloud), watch drafts"
```

---

### Task 13: Real-model smoke test, docs, README

**Files:**
- Modify: `README.md`, `config.example.toml` (comments only if needed), `docs/superpowers/specs/2026-10-21-phase1-design.md` (§7 cloud command lines), `docs/research/README.md` (one line)
- No source changes expected; any fix found here goes in its own commit with a failing test first.

- [ ] **Step 1: Scratch config in the worktree**

In the worktree root: `mkdir -p data && cp config.example.toml data/config.toml`; set `token_file = "/mnt/Apps/Projects/chatmaid/LMStudio_token.txt"` (absolute: the token stays in the main checkout). Confirm `uv run chatmaid --config data/config.toml status` reports an empty index at the worktree's `data/index.sqlite`. Never pass the main checkout's config.

- [ ] **Step 2: Local pipeline on recent mail**

```
uv run chatmaid --config data/config.toml llm-check
uv run chatmaid --config data/config.toml init --pass ingest --since 2026-09-01
uv run chatmaid --config data/config.toml init --pass digest --limit 15
uv run chatmaid --config data/config.toml init --pass style
uv run chatmaid --config data/config.toml init --pass embed
```

Record wall time per pass. If LM Studio is unreachable, stop here and report; do not fake it.

- [ ] **Step 3: Draft on a real message**

Pick one recent non-bulk incoming message from a person (`conversations`, `show`, then the message row id via `sqlite3 data/index.sqlite "select id, subject from message where is_owner=0 and bulk=0 order by sent_at desc limit 10"`). Run `draft run --message <id>`, then `draft show`, then `draft regenerate <id> --comment "shorter"`. Record the triage category and reason, draft wall time, candidate count, and whether the language and style match the thread. Try `draft cloud <id>` **only up to the payload print**, answer `N` at the prompt (that exercises pseudonymization and the decline path on real content without sending anything); paste the redacted payload shape (not the content) into the report and confirm no raw email address or participant name survived.

- [ ] **Step 4: Cloud backends with the canned payload**

`uv run chatmaid --config data/config.toml cloud-check --backend codex` and `--backend claude`. Both must print `text=ok`-like output; record models and wall time. These are the only cloud calls this task makes.

- [ ] **Step 5: Watch for one minute against the scratch index**

`timeout 60 uv run chatmaid --config data/config.toml watch --interval 20`; expect the banner, no tracebacks; `status` afterwards shows the same counts unless mail actually arrived.

- [ ] **Step 6: Docs**

README: Status paragraph (phase 1b: triage and drafts, cloud improvement by consent, CLI); the Running table gains `drafts`, `draft …`, `cloud-calls`, `cloud-check`, and `watch --no-draft`; a "Draft flow" section (what is stored, statuses, what `approve` means); a "Cloud backends" section with the exact command lines from Global Constraints, the consent rule, and the note that `--bare` cannot be used with OAuth; Layout tree gains `reply/`, `cloud/`, `lock.py`, `schema_v2.sql`. Spec §7: replace the `claude -p --bare …` line with the verified form and a one-line reason. Remove `data/config.toml` and `data/index.sqlite*` from the worktree before the final commit (they are gitignored, but the scratch index must not travel).

- [ ] **Step 7: Full suite and commit**

Run: `uv run pytest -q -W error`
Expected: all pass.

```bash
git add README.md config.example.toml docs
git commit -m "docs: phase 1b draft flow, cloud backends and consent rule"
```

Report the measurements from Steps 2–5 in the task's completion message.
