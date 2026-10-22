# Phase 1a: Core Store, Evolution Email Connector, Init and Watch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `chatmaid` CLI that reads the owner's five Evolution mail accounts read-only, builds the index (contacts, conversations, summaries, memories, style profile, embeddings) with a resumable `init`, watches for new mail, and answers history lookups.

**Architecture:** One Python package. Connectors normalize a source into `NormalizedMessage` values and never copy bodies; the `Store` (SQLite + sqlite-vec) holds references and derived data; the init pipeline runs four checkpointed passes (ingest, digest, style, embed) over the connector using an `LLM` protocol backed by LM Studio; the watcher merges D-Bus, file-change and timer triggers into one scan loop that routes owner messages and incoming messages. The reply-draft flow (plan 1b) and web UI (plan 1c) build on these interfaces.

**Tech Stack:** Python 3.14, uv, sqlite3 (stdlib) + `sqlite-vec`, `openai` SDK against LM Studio's OpenAI-compatible endpoint, `dbus-fast` (session-bus signals), `watchfiles` (inotify), stdlib `email`/`mailbox`/`configparser`/`tomllib`/`argparse`, `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-10-21-phase1-design.md` (sections 5, 6 routines A and D, 7 local models, 8, 10 steps 1–2). Research: `docs/research/evolution-email-access.md`.

## Global Constraints

- Python `>=3.14`, managed with `uv`; `uv run pytest` is the test command.
- Sources are read-only: Evolution databases are opened only with `file:<path>?mode=ro` URIs, `timeout=2.0`, `isolation_level=None` (autocommit), never `immutable=1`, never a transaction longer than one statement, never `journal_mode` changes.
- No message body is ever written to the index; the longest stored text per message is the 200-character `preview`.
- Data directory is `data/` at the project root (gitignored); the index is `data/index.sqlite`; config is `data/config.toml`.
- LM Studio token is read from the file named in config (`lmstudio.token_file`, default `LMStudio_token.txt`), never logged, never committed.
- Hot window is 365 days (`data.hot_window_days = 365`).
- In-scope accounts are the five Evolution source uids listed in config (`email.accounts`); owner addresses are derived from those accounts' identity sources plus `owner.aliases = ["jiaqi", "fishy"]`.
- Gmail label folders `[Gmail]/All Mail`, `[Gmail]/Important`, `[Gmail]/Starred` are skipped (`email.skip_folders`); messages are deduplicated per account by `Message-ID`.
- Per-folder watermark (controller rulings R6/R7, after discovering that Exchange and Microsoft 365 accounts use opaque base64 uids): when a folder's uids are all-digit strings, rows are ordered and filtered by `CAST(uid AS INTEGER)` and the watermark is the max uid; otherwise rows are ordered by `(dreceived, uid)`, filtered with `dreceived >= ?` (inclusive), and the watermark is the max `dreceived`. Watermarks are computed from the rows actually read, never from a separate `MAX()` query. Checkpoint and `scan_new` state values are these ints under the keys `"<account>/<folder>"`. Boundary duplicates are absorbed by the store (`ref_key`/`Message-ID` dedup in ingest; the watcher skips refs already present in the store). `rowid` is never used as a watermark because `VACUUM` renumbers it.
- `chatmaid init --reset-checkpoints` deletes the `ingest` checkpoints before running (ruling R8), so a complete-history ingest can follow an earlier `--since` run.
- Thinking control (ruling R9, after measuring LM Studio with Qwen3.5-35B-A3B): `LLM.complete(..., think: bool = False)`; with `think=False` the LM Studio client appends the assistant prefill `<think>\n\n</think>\n\n` (which disables Qwen reasoning) and omits `response_format` (LM Studio's grammar sampler cannot combine with a prefill); callers validate the returned JSON for the schema's required keys and retry once with a corrective user message before raising `LLMError`. With `think=True` the client sends the grammar-constrained `response_format` and no prefill. Digest and style calls use `think=False`. Model roles follow the design: chunk summaries `model_small`, rolling merges `model_main`, style profile `model_heavy`.
- A conversation with no fetchable messages is marked `tracked = 0` (ruling R10) and excluded from digest selection.
- No sending, no draft creation, no writes to Evolution or any server.
- Commit after every task with the message prefix shown in the task.

## Review Focus

1. Evolution holds a write lock on `folders.db` while the watcher scans: the scan must retry on the next trigger, never crash. Pinned in Task 13 (`test_scan_survives_locked_db`).
2. A body file with a non-UTF-8 charset (GBK), a missing `Date` header, or undecodable bytes: parsing returns a best-effort message, never raises; the connector falls back to the row's `dsent`/`dreceived` for the time. Pinned in Task 5 (`test_parse_gbk_body`, `test_parse_missing_date_is_none`, `test_parse_never_raises_on_garbage`) and Task 7 (`test_fetch_missing_body`).
3. The same message in `INBOX` and `[Gmail]/All Mail`: exactly one message row. Pinned in Task 7 (`test_iter_messages_dedups_across_folders`).
4. Mail from one owner address to another owner address: classified as owner-initiated, not incoming. Pinned in Task 13 (`test_route_owner_to_owner_is_owner`).
5. A cached body file that has expired (row exists, file missing): the message is still ingested with empty text and `body_missing=True`. Pinned in Task 7 (`test_fetch_missing_body`).

---

## File Structure

```
pyproject.toml                                  uv project, deps, scripts, pytest config
config.example.toml                             committed template; copied to data/config.toml
src/chatmaid/__init__.py                        __version__
src/chatmaid/cli.py                             argparse entry: accounts, scan, init, status, conversations, show, lookup, watch, llm-check
src/chatmaid/config.py                          Config dataclasses + load()
src/chatmaid/model.py                           SourceRef, Address, NormalizedMessage
src/chatmaid/store/schema_v1.sql                DDL
src/chatmaid/store/db.py                        Store
src/chatmaid/connectors/base.py                 Connector protocol
src/chatmaid/connectors/evolution/sources.py    .source parsing → EvolutionAccount
src/chatmaid/connectors/evolution/cache.py      folders.db + body-file access
src/chatmaid/connectors/evolution/rfc822.py     RFC822 → ParsedMail
src/chatmaid/connectors/evolution/connector.py  EvolutionConnector
src/chatmaid/connectors/evolution/events.py     EventSource (D-Bus, inotify, timer)
src/chatmaid/identity.py                        OwnerIdentity, normalize_email
src/chatmaid/threads.py                         ThreadIndex (union-find), normalize_subject
src/chatmaid/triage/rules.py                    is_bulk
src/chatmaid/llm/base.py                        LLM protocol, LLMError
src/chatmaid/llm/lmstudio.py                    LMStudioClient
src/chatmaid/llm/fake.py                        FakeLLM (tests and dry runs)
src/chatmaid/llm/prompts/chunk_summary.md       prompt templates (system text + JSON schema)
src/chatmaid/llm/prompts/rolling_summary.md
src/chatmaid/llm/prompts/style_profile.md
src/chatmaid/init/pipeline.py                   InitPipeline: ingest, digest, style, embed
src/chatmaid/init/chunking.py                   chunk_messages, detect_language
src/chatmaid/lookup.py                          search_history
src/chatmaid/daemon.py                          Watcher
tests/conftest.py                               fake Evolution cache + sources builders, FakeLLM fixture
tests/test_config.py … tests/test_daemon.py     one test module per source module
```

## Test fixtures (defined once in `tests/conftest.py`, used by Tasks 7–13)

`fake_env` builds, under `tmp_path`:

- Sources (Task 3 builder): account `a1`, backend `imapx`, display name `me@x.org`, identity `i1` with addresses `me@x.org` and `me2@x.org` (two `Address=` lines are not possible in one identity, so a second identity file `i2` for `me2@x.org` is attached through `owner.extra_addresses`), `sent_folder = "folder://a1/%5bGmail%5d/Sent%20Mail"`, `drafts_folder = "folder://a1/%5bGmail%5d/Drafts"`.
- Cache (Task 4 builder) for `a1`:
  - `INBOX`: uid `1` = Message-ID `<a@x>`, From `Ann <ann@x.org>`, To `me@x.org`, Date 1_600_000_000, body `hello there`; uid `2` = row only, subject `row subject`, `dsent` 1_700_000_000, no body file.
  - `[Gmail]/Sent Mail`: uid `3` = `<s3@x>`, From `me@x.org`, To `ann@x.org`, In-Reply-To `<a@x>`, Date 1_700_000_100, body `thanks, see you tomorrow at the office`; uid `5` = `<s5@x>`, From `me@x.org`, To `li@x.org`, Date 1_700_000_300, body `明天见，谢谢你的帮助，我们到时候再详细讨论项目的安排。`.
  - `[Gmail]/All Mail`: uid `9` = a byte-identical copy of INBOX uid 1 (same Message-ID).
  - `News`: uid `4` = `<n4@x>`, From `news@x.org`, `List-Unsubscribe: <mailto:u@x>`, Date 1_700_000_200, body `weekly digest`.
- Local Maildir at `tmp_path/local/.Sent` with one message `<l1@x>` From `me@x.org` To `ann@x.org`, Date 1_700_000_400, body `sent from local`.
- `data/config.toml` written from `config.example.toml` with `email.*` paths pointing at the fixtures, `email.accounts = ["a1"]`, `owner.extra_addresses = ["me2@x.org"]`, `lmstudio.embedding_dim = 4`.
- Returns `SimpleNamespace(config: Config, config_path: Path, cfg: EmailConfig, accounts, owner, connector, cache_dir, db_path = cache_dir/"a1"/"folders.db")`.

`store` = `Store(tmp_path / "index.sqlite", embedding_dim=4)`. `ingested_env` = `fake_env` plus `store` after `InitPipeline(...).ingest(InitOptions())`. `digested_env` = `ingested_env` after `digest` and `embed` with `FakeLLM({"chunk_summary": ..., "rolling_summary": ...}, dim=4)` (the canned JSON from Task 10). `watch_env` = `ingested_env` plus `Watcher(store, connector, EventSource([db_path], enable_dbus=False))`, a `message(sender, to, folder)` helper returning a `NormalizedMessage` whose `is_owner` is computed through `owner.matches`, an `add_row(folder, *, uid, message_id, sender, to, date, body, dreceived=None, in_reply_to=None)` helper that appends one row plus body file to the fake cache (writable in tests; `dsent=date`, `dreceived=dreceived or date`, saved_count updated), and an `add_folder(name, *, time_mode=False)` helper that creates an empty folder table (ruling R14). The watcher's scan state starts from the ingest checkpoints when no watch state exists, so the first trigger after ingest routes nothing.

---

### Task 1: Project skeleton and config

**Files:**
- Create: `pyproject.toml`, `config.example.toml`, `src/chatmaid/__init__.py`, `src/chatmaid/config.py`, `src/chatmaid/cli.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.load(path: Path) -> Config` where `Config` has fields `data: DataConfig(dir: Path, hot_window_days: int)`, `owner: OwnerConfig(name: str, aliases: tuple[str, ...], extra_addresses: tuple[str, ...])`, `email: EmailConfig(evolution_config_dir: Path, evolution_cache_dir: Path, accounts: tuple[str, ...], skip_folders: tuple[str, ...], local_maildir_sent: Path | None)`, `lmstudio: LMStudioConfig(base_url: str, token_file: Path, model_small: str, model_main: str, model_heavy: str, model_embedding: str, embedding_dim: int, timeout_s: int)`. All paths are expanded (`~`) and resolved relative to the project root. `Config.index_path -> Path` returns `data.dir / "index.sqlite"`. `config.read_token(cfg) -> str` reads and strips `lmstudio.token_file`, raising `ConfigError` if missing.
- Produces: `cli.main(argv: list[str] | None = None) -> int` with subcommands added by later tasks; `chatmaid --version` prints `chatmaid <__version__>`.

- [ ] **Step 1: Write `pyproject.toml`**

`[project] name = "chatmaid"`, `requires-python = ">=3.14"`, dependencies `openai>=1.60`, `sqlite-vec>=0.1.6`, `dbus-fast>=2.30`, `watchfiles>=1.0`; `[project.scripts] chatmaid = "chatmaid.cli:main"`; `[dependency-groups] dev = ["pytest>=8", "pytest-asyncio>=0.25"]`; `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`; build backend `hatchling` with `packages = ["src/chatmaid"]`.

- [ ] **Step 2: Write `config.example.toml`** with exactly these keys and values

```toml
[data]
dir = "data"
hot_window_days = 365

[owner]
name = "Jiaqi"
aliases = ["jiaqi", "fishy"]
extra_addresses = []

[email]
evolution_config_dir = "~/.config/evolution/sources"
evolution_cache_dir = "~/.cache/evolution/mail"
accounts = [
  "2bb8bc493ea9a64e270c733fd29ed16b853eefcb",  # b5a7v1
  "8a19039da6e94f226343e4c2ad4bf97d031a7f8e",  # jiaqi.yu1
  "f71772b19449d2cd7282d5176e2f4178935719e7",  # jxy618.case
  "19f397267c6bc963a9feca8074db17099796fbfd",  # yjq17
  "5cf1ac3f38477a31b4698179ffc7c571355c57af",  # yu17.fishy
]
skip_folders = ["[Gmail]/All Mail", "[Gmail]/Important", "[Gmail]/Starred"]
local_maildir_sent = "~/.local/share/evolution/mail/local/.Sent"

[lmstudio]
base_url = "http://127.0.0.1:40090/v1"
token_file = "LMStudio_token.txt"
model_small = "qwen/qwen3-4b-2507"
model_main = "qwen/qwen3.5-35b-a3b"
model_heavy = "qwen/qwen3.5-35b-a3b"
model_embedding = "text-embedding-qwen3-embedding-0.6b"
embedding_dim = 1024
timeout_s = 600
```

- [ ] **Step 3: Write the failing tests**

```python
def test_load_example_config(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert cfg.data.hot_window_days == 365
    assert cfg.owner.aliases == ("jiaqi", "fishy")
    assert len(cfg.email.accounts) == 5
    assert cfg.email.skip_folders == ("[Gmail]/All Mail", "[Gmail]/Important", "[Gmail]/Starred")
    assert cfg.index_path == tmp_path / "data" / "index.sqlite"
    assert cfg.lmstudio.base_url == "http://127.0.0.1:40090/v1"

def test_read_token_strips_newline(tmp_path):
    (tmp_path / "LMStudio_token.txt").write_text("abc123\n")
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    assert read_token(cfg) == "abc123"

def test_read_token_missing_raises(tmp_path):
    cfg = load(Path("config.example.toml"), project_root=tmp_path)
    with pytest.raises(ConfigError):
        read_token(cfg)

def test_cli_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"chatmaid {__version__}"
```

- [ ] **Step 4: Create the environment and run tests to verify they fail**

Run: `uv sync --group dev` then `uv run pytest tests/test_config.py -v` — Expected: FAIL (import errors).

- [ ] **Step 5: Implement `config.py` and `cli.py`**

`load(path, *, project_root: Path | None = None)` uses `tomllib`; `project_root` defaults to the parent of `path`'s parent when `path` is under `data/`, else the current directory. Missing keys fall back to the example's values. `cli.main` builds an `argparse.ArgumentParser(prog="chatmaid")` with `--config` (default `data/config.toml`) and a `subparsers` object that later tasks extend through `register_<name>(subparsers)` functions; `--version` prints and returns 0.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v` — Expected: 4 passed.

- [ ] **Step 7: Verify the entry point**

Run: `uv run chatmaid --version` — Expected: `chatmaid 0.1.0`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock config.example.toml src/chatmaid tests/test_config.py
git commit -m "feat: project skeleton, config loader and CLI entry"
```

---

### Task 2: Data model and store (schema v1)

**Files:**
- Create: `src/chatmaid/model.py`, `src/chatmaid/store/schema_v1.sql`, `src/chatmaid/store/db.py`, `src/chatmaid/store/__init__.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces (model):
  - `SourceRef(source: str, account: str, folder: str, uid: str)` frozen dataclass; `key() -> str` returns `json.dumps([source, account, folder, uid], ensure_ascii=False)`; `SourceRef.from_key(s) -> SourceRef`.
  - `Address(name: str, email: str)` frozen; `email` already lowercased by the caller.
  - `NormalizedMessage(ref, message_id: str | None, in_reply_to: str | None, references: tuple[str, ...], sender: Address, recipients: tuple[Address, ...], sent_at: int, subject: str, text: str, preview: str, has_attachments: bool, attachments: tuple[str, ...], size: int, is_owner: bool, bulk: bool, labels: tuple[str, ...], body_missing: bool = False)`; `make_preview(text: str) -> str` collapses whitespace and truncates to 200 characters.
- Produces (store): `Store(path: Path, *, embedding_dim: int)`; `close()`; context-manager support; and these methods (all synchronous):
  - `upsert_identity(platform: str, address: str, display_name: str | None, *, is_owner: bool = False) -> int` (identity id; a new identity gets its own contact unless `is_owner`, in which case it joins the single owner contact)
  - `owner_contact_id() -> int`
  - `upsert_conversation(platform: str, key: str, title: str, kind: str) -> int`
  - `set_conversation_summary(conversation_id: int, summary: str, *, tier: str | None = None, events: list[dict] | None = None) -> None`
  - `add_participant(conversation_id: int, contact_id: int) -> None`
  - `upsert_message(conversation_id: int, msg: NormalizedMessage, sender_identity_id: int) -> int` (unique on `ref_key`; stores subject, preview, labels JSON, flags; never `text`)
  - `has_message_id(platform: str, account: str, message_id: str) -> bool`
  - `message_refs_for(conversation_id: int) -> list[tuple[SourceRef, int]]` ordered by `sent_at`
  - `messages_for(conversation_id: int, *, since: int | None = None, limit: int = 50) -> list[sqlite3.Row]`
  - `conversations(*, limit: int | None = None, needing_summary: bool | None = None, exclude_bulk: bool = True) -> list[sqlite3.Row]` ordered by `last_message_at DESC`; `needing_summary=True` returns rows without a summary, `False` rows with one, `None` all
  - `message_ids_for_account(platform: str, account: str) -> list[tuple[str, tuple[str, ...]]]` returning `(message_id, references)` pairs from `references_json` for rebuilding a `ThreadIndex`
  - `add_memory(*, kind: str, text: str, source_ref: str, observed_at: int | None, contact_id: int | None = None, conversation_id: int | None = None) -> int`
  - `set_style_profile(language: str, tier: str, text: str, sample_refs: list[str]) -> None`; `style_profile(language: str, tier: str) -> str | None`
  - `get_checkpoint(task: str, key: str) -> dict | None`; `set_checkpoint(task: str, key: str, state: dict) -> None`
  - `upsert_embedding(kind: str, ref_id: int, text_hash: str, vector: list[float]) -> None`; `search_embeddings(vector: list[float], *, k: int = 10, kind: str | None = None) -> list[tuple[str, int, float]]` (kind, ref_id, distance)
  - `log_event(source: str, kind: str, ref: str, payload: dict) -> int`; `events(*, limit: int = 100) -> list[sqlite3.Row]`
  - `counts() -> dict[str, int]` with keys `identities, contacts, conversations, messages, memories, embeddings, events`

- [ ] **Step 1: Write `schema_v1.sql`**

Tables: `contact(id, name, is_owner INTEGER DEFAULT 0, tier TEXT, notes TEXT, created_at)`; `identity(id, platform, address, display_name, contact_id REFERENCES contact, UNIQUE(platform, address))`; `conversation(id, platform, key UNIQUE, title, kind, tier, tracked INTEGER DEFAULT 1, bulk INTEGER DEFAULT 0, summary TEXT, summary_updated_at, events_json TEXT, last_message_at, last_owner_at, message_count INTEGER DEFAULT 0)`; `conversation_participant(conversation_id, contact_id, PRIMARY KEY(conversation_id, contact_id))`; `message(id, conversation_id, ref_key UNIQUE, platform, account, message_id, references_json, sender_identity_id, sent_at, kind, subject, preview, labels_json, has_attachments, size, is_owner, bulk, body_missing, UNIQUE(platform, account, message_id))` where `references_json` is the JSON list of `references` plus `in_reply_to`; `memory(id, kind, text, source_ref, observed_at, created_at, contact_id, conversation_id, superseded_by)`; `style_profile(language, tier, text, sample_refs_json, updated_at, PRIMARY KEY(language, tier))`; `checkpoint(task, key, state_json, updated_at, PRIMARY KEY(task, key))`; `embedding_item(id, kind, ref_id, text_hash, UNIQUE(kind, ref_id))`; `event_log(id, ts, source, kind, ref, payload_json)`; indexes on `message(conversation_id, sent_at)`, `message(sent_at)`, `memory(conversation_id)`, `memory(contact_id)`; `PRAGMA user_version = 1`. The vector table is created in code: `CREATE VIRTUAL TABLE IF NOT EXISTS vec_embedding USING vec0(item_id INTEGER PRIMARY KEY, embedding float[<dim>])`.

- [ ] **Step 2: Write the failing tests**

```python
def test_schema_applied(tmp_path):
    s = Store(tmp_path / "i.sqlite", embedding_dim=4)
    assert s.counts() == {"identities": 0, "contacts": 0, "conversations": 0, "messages": 0, "memories": 0, "embeddings": 0, "events": 0}

def test_owner_identities_share_one_contact(store):
    a = store.upsert_identity("email", "a@x.org", "A", is_owner=True)
    b = store.upsert_identity("email", "b@x.org", "B", is_owner=True)
    assert a != b and store.owner_contact_id() == store.contact_id_of(a) == store.contact_id_of(b)

def test_upsert_identity_is_idempotent(store):
    assert store.upsert_identity("email", "c@x.org", None) == store.upsert_identity("email", "c@x.org", "C")

def test_upsert_message_unique_on_ref_and_never_stores_text(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", sample_message.sender.email, None)
    m1 = store.upsert_message(cid, sample_message, sid)
    m2 = store.upsert_message(cid, sample_message, sid)
    assert m1 == m2
    row = store.messages_for(cid)[0]
    assert row["preview"] == make_preview(sample_message.text) and "text" not in row.keys()
    assert store.has_message_id("email", sample_message.ref.account, sample_message.message_id)

def test_conversation_counts_and_last_message(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", sample_message.sender.email, None)
    store.upsert_message(cid, sample_message, sid)
    row = store.conversations()[0]
    assert row["message_count"] == 1 and row["last_message_at"] == sample_message.sent_at

def test_checkpoint_roundtrip(store):
    assert store.get_checkpoint("ingest", "acct") is None
    store.set_checkpoint("ingest", "acct", {"INBOX": "42"})
    assert store.get_checkpoint("ingest", "acct") == {"INBOX": "42"}

def test_embedding_search_returns_nearest(store):
    store.upsert_embedding("memory", 1, "h1", [1.0, 0.0, 0.0, 0.0])
    store.upsert_embedding("memory", 2, "h2", [0.0, 1.0, 0.0, 0.0])
    assert store.search_embeddings([0.9, 0.1, 0.0, 0.0], k=1)[0][:2] == ("memory", 1)

def test_make_preview_collapses_and_truncates():
    assert make_preview("a\n\n  b" + "x" * 500) == ("a b" + "x" * 500)[:200]
```

`store` fixture: `Store(tmp_path / "i.sqlite", embedding_dim=4)`; `sample_message` fixture: a `NormalizedMessage` with `ref=SourceRef("evolution", "acct1", "INBOX", "7")`, `message_id="<m7@x.org>"`, `sent_at=1_700_000_000`, `text="Hello there"`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v` — Expected: FAIL (imports).

- [ ] **Step 4: Implement `model.py` and `store/db.py`**

`Store.__init__` connects with `sqlite3.connect(path, isolation_level=None)`, `row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, loads `sqlite_vec` (`enable_load_extension(True); sqlite_vec.load(conn)`), applies `schema_v1.sql` when `PRAGMA user_version` is 0, creates `vec_embedding`. `upsert_message` updates `conversation.message_count`, `last_message_at` (max) and `last_owner_at` when `is_owner`. `search_embeddings` uses `vec_embedding MATCH ? ORDER BY distance LIMIT k` joined to `embedding_item`, vector serialized with `sqlite_vec.serialize_float32`. Add `contact_id_of(identity_id: int) -> int`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v` — Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/model.py src/chatmaid/store tests/test_store.py
git commit -m "feat: normalized message model and SQLite store with schema v1"
```

---

### Task 3: Evolution account discovery (`.source` parsing) and `chatmaid accounts`

**Files:**
- Create: `src/chatmaid/connectors/__init__.py`, `src/chatmaid/connectors/evolution/__init__.py`, `src/chatmaid/connectors/evolution/sources.py`
- Modify: `src/chatmaid/cli.py` (register `accounts`)
- Test: `tests/test_sources.py`, fixture builder in `tests/conftest.py`

**Interfaces:**
- Produces: `EvolutionAccount(uid: str, display_name: str, backend: str, enabled: bool, identity_uid: str | None, identity_addresses: tuple[str, ...], sent_folder: str | None, drafts_folder: str | None)`; `load_accounts(config_dir: Path, uids: Sequence[str]) -> list[EvolutionAccount]` (raises `MissingAccount(uid)` for a uid without a `.source` file); `parse_folder_uri(uri: str) -> tuple[str, str]` returning `(account_uid, decoded_folder_name)` for `folder://<uid>/<percent-encoded path>`.
- Produces (conftest): `make_sources(tmp_path, accounts: list[dict]) -> Path` writing `<uid>.source` and `<identity_uid>.source` files from dicts with keys `uid, display_name, backend, identity_uid, address, sent_folder, drafts_folder`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_folder_uri_decodes_percent_and_brackets():
    assert parse_folder_uri("folder://2bb8/%5bGmail%5d/Sent%20Mail") == ("2bb8", "[Gmail]/Sent Mail")

def test_parse_folder_uri_decodes_utf8():
    assert parse_folder_uri("folder://19f3/%e5%b7%b2%e5%8f%91%e9%80%81%e9%82%ae%e4%bb%b6") == ("19f3", "已发送邮件")

def test_load_accounts_reads_identity_and_sent_folder(tmp_path):
    d = make_sources(tmp_path, [dict(uid="a1", display_name="me@x.org", backend="imapx", identity_uid="i1",
                                     address="me@x.org", sent_folder="folder://a1/%5bGmail%5d/Sent%20Mail", drafts_folder="folder://a1/Drafts")])
    [acct] = load_accounts(d, ["a1"])
    assert acct.backend == "imapx" and acct.identity_addresses == ("me@x.org",)
    assert acct.sent_folder == "[Gmail]/Sent Mail" and acct.drafts_folder == "Drafts" and acct.enabled

def test_load_accounts_missing_uid_raises(tmp_path):
    with pytest.raises(MissingAccount):
        load_accounts(make_sources(tmp_path, []), ["nope"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `sources.py` and the `accounts` command**

Parse with `configparser.ConfigParser(interpolation=None, strict=False)`; groups `[Data Source]` (`DisplayName`, `Enabled`), `[Mail Account]` (`BackendName`, `IdentityUid`); identity file groups `[Mail Identity]` (`Address`), `[Mail Submission]` (`SentFolder`), `[Mail Composition]` (`DraftsFolder`). Lowercase addresses. `chatmaid accounts` prints one line per configured account: `uid[:8] backend display_name sent=<folder> identities=<n>`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources.py -v` — Expected: 4 passed.

- [ ] **Step 5: Verify against the real configuration (read-only)**

Run: `cp config.example.toml data/config.toml` (once) then `uv run chatmaid accounts` — Expected: 5 lines; sent folders `[Gmail]/Sent Mail` for the three Gmail accounts, `Sent Items` for jiaqi.yu1, `已发送邮件` for yjq17; every account `identities>=1`.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/connectors src/chatmaid/cli.py tests/conftest.py tests/test_sources.py
git commit -m "feat: discover Evolution accounts, identities and Sent folders"
```

---

### Task 4: Evolution cache reader

**Files:**
- Create: `src/chatmaid/connectors/evolution/cache.py`
- Modify: `tests/conftest.py` (fake cache builder)
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `FolderInfo(folder_id: int, name: str, saved_count: int)`; `MessageRow(uid: str, flags: int, dsent: int, dreceived: int, subject: str, mail_from: str, mail_to: str, mail_cc: str, size: int)`; `EvolutionCache(cache_dir: Path, account_uid: str)` with `folders() -> list[FolderInfo]`, `iter_rows(folder: FolderInfo, *, after_uid: int | None = None) -> Iterator[MessageRow]` ordered by `CAST(uid AS INTEGER)`, `max_uid(folder) -> int | None`, `body_path(folder: FolderInfo, uid: str) -> Path | None` (searches `folders/<physical path>/cur/*/<uid>`), `folder_dir(name: str) -> Path` (each `/` becomes `/subfolders/`), `db_path -> Path`.
- Produces (conftest): `make_cache(tmp_path, account_uid: str, folders: dict[str, list[dict]]) -> Path` creating `folders.db` with the real schema (`keys`, `folders`, `messages_<id>`) and body files at `folders/<name>/cur/00/<uid>` from dicts `uid, flags, dsent, dreceived, subject, mail_from, mail_to, mail_cc, size, body` (body omitted means no file).

- [ ] **Step 1: Write the failing tests**

```python
def test_folders_and_rows(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": [dict(uid="1", subject="s1", body="From: a@x.org\n\nhi"), dict(uid="2", subject="s2", body="From: b@x.org\n\nyo")]})
    c = EvolutionCache(cache_dir, "a1")
    [inbox] = c.folders()
    assert inbox.name == "INBOX" and inbox.saved_count == 2
    assert [r.uid for r in c.iter_rows(inbox)] == ["1", "2"]
    assert [r.uid for r in c.iter_rows(inbox, after_uid=1)] == ["2"]
    assert c.max_uid(inbox) == 2

def test_body_path_and_missing(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": [dict(uid="1", body="x"), dict(uid="2")]})
    c = EvolutionCache(cache_dir, "a1"); [inbox] = c.folders()
    assert c.body_path(inbox, "1").read_text() == "x"
    assert c.body_path(inbox, "2") is None

def test_folder_dir_maps_slash_to_subfolders(tmp_path):
    c = EvolutionCache(tmp_path, "a1")
    assert c.folder_dir("[Gmail]/Sent Mail") == tmp_path / "a1" / "folders" / "[Gmail]" / "subfolders" / "Sent Mail"

def test_opens_read_only(tmp_path):
    cache_dir = make_cache(tmp_path, "a1", {"INBOX": []})
    c = EvolutionCache(cache_dir, "a1")
    with pytest.raises(sqlite3.OperationalError):
        c._conn.execute("INSERT INTO keys VALUES ('x','y')")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cache.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `cache.py`**

Connection per Global Constraints: `sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0, isolation_level=None)`, `row_factory = sqlite3.Row`. `iter_rows` selects `uid, flags, dsent, dreceived, subject, mail_from, mail_to, mail_cc, size FROM messages_<folder_id>` with `WHERE CAST(uid AS INTEGER) > ?` when `after_uid` is given, fetching in batches of 500 with `fetchmany`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cache.py -v` — Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/chatmaid/connectors/evolution/cache.py tests/conftest.py tests/test_cache.py
git commit -m "feat: read-only reader for Evolution's CamelStoreDB cache"
```

---

### Task 5: RFC822 parsing

**Files:**
- Create: `src/chatmaid/connectors/evolution/rfc822.py`
- Test: `tests/test_rfc822.py`

**Interfaces:**
- Produces: `ParsedMail(message_id: str | None, in_reply_to: str | None, references: tuple[str, ...], from_: Address, to: tuple[Address, ...], cc: tuple[Address, ...], date: int | None, subject: str, text: str, attachments: tuple[str, ...], list_headers: dict[str, str])` where `list_headers` holds lowercase keys `list-unsubscribe, list-id, precedence, auto-submitted` when present; `parse_bytes(data: bytes) -> ParsedMail`; `parse_file(path: Path) -> ParsedMail`; `html_to_text(html: str) -> str`; `parse_addresses(header: str) -> tuple[Address, ...]` (names unquoted, emails lowercased).

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_plain_with_threading_headers():
    p = parse_bytes(b"Message-ID: <a@x>\r\nIn-Reply-To: <b@x>\r\nReferences: <c@x> <b@x>\r\nFrom: Ann <ANN@X.org>\r\nTo: b@x.org, Cy <c@x.org>\r\nDate: Tue, 01 Oct 2024 10:00:00 +0000\r\nSubject: Re: hi\r\n\r\nbody text\r\n")
    assert p.message_id == "<a@x>" and p.in_reply_to == "<b@x>" and p.references == ("<c@x>", "<b@x>")
    assert p.from_ == Address("Ann", "ann@x.org") and p.to == (Address("", "b@x.org"), Address("Cy", "c@x.org"))
    assert p.date == 1727776800 and p.subject == "Re: hi" and p.text == "body text"

def test_parse_prefers_plain_over_html_and_lists_attachments(multipart_bytes):
    p = parse_bytes(multipart_bytes)   # fixture: alternative(text/plain "plain", text/html "<b>html</b>") + attachment "a.pdf"
    assert p.text == "plain" and p.attachments == ("a.pdf",)

def test_html_only_is_converted():
    p = parse_bytes(b"Content-Type: text/html\r\n\r\n<p>Hello<br>world</p><script>x()</script>")
    assert p.text == "Hello\nworld"

def test_parse_gbk_body():
    body = "你好".encode("gbk")
    p = parse_bytes(b"Content-Type: text/plain; charset=gbk\r\nSubject: =?gbk?b?" + base64.b64encode("测试".encode("gbk")) + b"?=\r\n\r\n" + body)
    assert p.text == "你好" and p.subject == "测试"

def test_parse_missing_date_is_none():
    p = parse_bytes(b"From: a@x.org\r\n\r\nx")
    assert p.date is None and p.text == "x"

def test_list_headers_collected():
    p = parse_bytes(b"List-Unsubscribe: <mailto:u@x>\r\nPrecedence: bulk\r\n\r\nx")
    assert p.list_headers == {"list-unsubscribe": "<mailto:u@x>", "precedence": "bulk"}

def test_parse_never_raises_on_garbage():
    p = parse_bytes(b"\xff\xfe\x00garbage")
    assert isinstance(p, ParsedMail) and p.message_id is None and p.from_ == Address("", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rfc822.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `rfc822.py`**

Use `email.message_from_bytes(data, policy=email.policy.default)`; `get_body(preferencelist=("plain", "html"))`; decode with the part's charset and `errors="replace"`; `html_to_text` is an `html.parser.HTMLParser` subclass that drops `script`/`style` content, turns `br`/`p`/`div`/`li`/`tr` into newlines, collapses runs of blank lines; attachments are parts with `is_attachment()` or a filename; dates via `email.utils.parsedate_to_datetime` → `int(timestamp())`, `None` on failure; wrap the whole parse in `try/except Exception` returning an empty `ParsedMail` with `text=""`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfc822.py -v` — Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/chatmaid/connectors/evolution/rfc822.py tests/test_rfc822.py
git commit -m "feat: RFC822 parsing with charset tolerance and HTML-to-text"
```

---

### Task 6: Threading, owner identity and bulk rules

**Files:**
- Create: `src/chatmaid/threads.py`, `src/chatmaid/identity.py`, `src/chatmaid/triage/__init__.py`, `src/chatmaid/triage/rules.py`
- Test: `tests/test_threads.py`, `tests/test_identity.py`, `tests/test_rules.py`

**Interfaces:**
- Produces: `normalize_subject(s: str) -> str` (strip leading `re:`, `fw:`, `fwd:`, `回复:`, `回覆:`, `转发:`, `轉發:`, `答复:` prefixes repeatedly, case-insensitive, collapse whitespace, lowercase); `ThreadIndex()` with `add(message_id: str | None, references: Iterable[str]) -> None` and `key_for(message_id: str | None, references: Iterable[str], fallback: str) -> str` returning the lexicographically smallest id in the message's connected component, or `fallback` when the message has no id and no references; `thread_fallback_key(subject: str, participants: Iterable[str]) -> str` = `"subj:" + normalize_subject(subject) + "|" + ",".join(sorted(participants))`.
- Produces: `normalize_email(s: str) -> str`; `OwnerIdentity(addresses: frozenset[str], aliases: frozenset[str])`; `OwnerIdentity.from_accounts(accounts: Iterable[EvolutionAccount], extra: Iterable[str], aliases: Iterable[str]) -> OwnerIdentity`; `OwnerIdentity.matches(addr: Address) -> bool` (email in `addresses`).
- Produces: `is_bulk(parsed: ParsedMail) -> bool`; `NOREPLY_RE = re.compile(r"^(no-?reply|do-?not-?reply|notifications?|newsletter|mailer-daemon|postmaster|bounce)", re.I)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_subject_strips_prefixes_in_two_languages():
    assert normalize_subject("Re: RE: 回复: Fwd:  Plan  B") == "plan b"

def test_thread_index_joins_by_references():
    t = ThreadIndex(); t.add("<a@x>", []); t.add("<b@x>", ["<a@x>"]); t.add("<c@x>", ["<b@x>"])
    assert t.key_for("<c@x>", ["<b@x>"], "fb") == t.key_for("<a@x>", [], "fb") == "<a@x>"

def test_thread_index_fallback_without_ids():
    assert ThreadIndex().key_for(None, [], "subj:x|a@x") == "subj:x|a@x"

def test_owner_identity_from_accounts():
    o = OwnerIdentity.from_accounts([EvolutionAccount(uid="a", display_name="", backend="imapx", enabled=True, identity_uid="i", identity_addresses=("Me@X.org",), sent_folder=None, drafts_folder=None)], extra=["me2@y.org"], aliases=["jiaqi"])
    assert o.matches(Address("", "me@x.org")) and o.matches(Address("", "me2@y.org")) and not o.matches(Address("", "x@x.org"))

@pytest.mark.parametrize("hdrs,sender,expected", [
    ({"list-unsubscribe": "<mailto:u@x>"}, "news@x.org", True),
    ({"precedence": "bulk"}, "a@x.org", True),
    ({"auto-submitted": "auto-generated"}, "a@x.org", True),
    ({}, "no-reply@x.org", True),
    ({}, "ann@x.org", False),
])
def test_is_bulk(hdrs, sender, expected):
    assert is_bulk(ParsedMail(None, None, (), Address("", sender), (), (), None, "", "", (), hdrs)) is expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_threads.py tests/test_identity.py tests/test_rules.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement the three modules**

`ThreadIndex` is a union-find over message-id strings with path compression; `key_for` unions the message with its references first, then returns `min(component)`. `is_bulk` is true when `list-unsubscribe` or `list-id` is present, `precedence` in `{bulk, list, junk}`, `auto-submitted` present and not `no`, or `NOREPLY_RE` matches the sender's local part.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_threads.py tests/test_identity.py tests/test_rules.py -v` — Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/chatmaid/threads.py src/chatmaid/identity.py src/chatmaid/triage tests/test_threads.py tests/test_identity.py tests/test_rules.py
git commit -m "feat: thread keys, owner identity and header-based bulk rules"
```

---

### Task 7: EvolutionConnector and `chatmaid scan --dry-run`

**Files:**
- Create: `src/chatmaid/connectors/base.py`, `src/chatmaid/connectors/evolution/connector.py`
- Modify: `src/chatmaid/cli.py` (register `scan`)
- Test: `tests/test_connector.py`

**Interfaces:**
- Produces: `Connector` protocol: `name: str`; `iter_messages(*, since: int | None = None) -> Iterator[NormalizedMessage]`; `fetch(ref: SourceRef) -> NormalizedMessage`; `scan_new(state: dict[str, int]) -> tuple[list[SourceRef], dict[str, int]]` where `state` maps `"<account>/<folder>"` to the last seen uid.
- Produces: `EvolutionConnector(cfg: EmailConfig, accounts: list[EvolutionAccount], owner: OwnerIdentity)` implementing `Connector` with `name = "evolution"`; folder order per account: Sent folder first, then `INBOX`/`Inbox`/`收件箱`, then remaining folders alphabetically, skipping `cfg.skip_folders`; labels: `("spam",)` for folders whose name contains `Spam`, `Junk` or `垃圾`, `("trash",)` for `Trash`, `Deleted`, `已删除`; `is_owner` is true when the sender matches `owner` or the folder is the account's Sent folder; `bulk` from `is_bulk`; `sent_at` is `ParsedMail.date` else `MessageRow.dsent` else `dreceived`; the local Maildir Sent (`cfg.local_maildir_sent`, read with `mailbox.Maildir(..., create=False)`) is exposed as account `"local"`, folder `"Sent"`, uid = the Maildir key. Dedup: within one `iter_messages` run, a `(account, message_id)` seen before is skipped; `scan_new` does not dedup (the store's unique index does).

- [ ] **Step 1: Write the failing tests**

```python
def test_iter_messages_dedups_across_folders(fake_env):   # fixture: two-folder cache where INBOX uid 1 and "[Gmail]/All Mail" uid 9 share Message-ID <a@x>
    msgs = list(fake_env.connector.iter_messages())
    assert [m.ref.folder for m in msgs] == ["INBOX"]

def test_skip_folders_are_not_read(fake_env):
    assert all(m.ref.folder not in fake_env.cfg.skip_folders for m in fake_env.connector.iter_messages())

def test_sent_folder_marks_owner(fake_env):   # Sent Mail uid 3 from me@x.org
    m = next(m for m in fake_env.connector.iter_messages() if m.ref.folder == "[Gmail]/Sent Mail")
    assert m.is_owner and m.sender.email == "me@x.org"

def test_fetch_missing_body(fake_env):   # INBOX uid 2 has a row but no body file
    m = fake_env.connector.fetch(SourceRef("evolution", "a1", "INBOX", "2"))
    assert m.body_missing and m.text == "" and m.subject == "row subject" and m.sent_at == 1700000000

def test_scan_new_returns_only_newer_uids(fake_env):
    refs, state = fake_env.connector.scan_new({})
    assert len(refs) >= 3 and state["a1/INBOX"] == 2
    refs2, _ = fake_env.connector.scan_new(state)
    assert refs2 == []

def test_local_maildir_sent_is_included(fake_env):   # fixture writes one Maildir message
    assert any(m.ref.account == "local" and m.is_owner for m in fake_env.connector.iter_messages())
```

`fake_env` fixture (conftest): builds sources + cache (Task 3/4 builders) + a Maildir at `tmp_path/local/.Sent`, an `EmailConfig` pointing at them, and returns `SimpleNamespace(cfg, accounts, owner, connector)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_connector.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `base.py`, `connector.py`, and `scan`**

`chatmaid scan [--account UID] [--dry-run]` prints, per account and folder, `rows=<n> bodies=<n> owner=<n> bulk=<n>` without touching the index. `fetch` builds the message from the row plus the parsed body when present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_connector.py -v` — Expected: 6 passed.

- [ ] **Step 5: Verify on the real cache (read-only)**

Run: `uv run chatmaid scan --account 5cf1ac3f38477a31b4698179ffc7c571355c57af --dry-run` — Expected: lines for `[Gmail]/Sent Mail` (rows=39), `INBOX` (rows=3934), `Notes`, `[Gmail]/Spam`, `[Gmail]/Trash`, no `[Gmail]/All Mail`; `bodies` equals `rows` for every folder; owner count for Sent equals its rows.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/connectors/base.py src/chatmaid/connectors/evolution/connector.py src/chatmaid/cli.py tests/conftest.py tests/test_connector.py
git commit -m "feat: Evolution connector with dedup, Sent detection and scan command"
```

---

### Task 8: LLM protocol, LM Studio client, FakeLLM, `chatmaid llm-check`

**Files:**
- Create: `src/chatmaid/llm/__init__.py`, `src/chatmaid/llm/base.py`, `src/chatmaid/llm/lmstudio.py`, `src/chatmaid/llm/fake.py`
- Modify: `src/chatmaid/cli.py` (register `llm-check`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `LLMError(Exception)`; `LLM` protocol with `complete(*, system: str, user: str, model: str, json_schema: dict | None = None, max_tokens: int = 1024, temperature: float = 0.3) -> str` and `embed(texts: list[str], *, model: str) -> list[list[float]]`.
- Produces: `LMStudioClient(base_url: str, token: str, *, timeout_s: int = 600)` implementing `LLM` through `openai.OpenAI(base_url=..., api_key=token, timeout=timeout_s, max_retries=1)`; `json_schema` is passed as `response_format={"type": "json_schema", "json_schema": {"name": "out", "schema": json_schema, "strict": True}}`; `list_models() -> list[str]`; any `openai` exception is re-raised as `LLMError(str(e))`.
- Produces: `FakeLLM(responses: dict[str, str] | None = None, *, dim: int = 4)` whose `complete` returns `responses[<first line of system, with a leading "# " stripped>]` or `"{}"` (every prompt file's system text begins with the line `# <prompt name>`, see Task 10), records every call in `calls: list[dict]`, and whose `embed` returns deterministic vectors derived from `hash(text)`; `FakeLLM.fail_next(n: int)` makes the next `n` calls raise `LLMError("timeout")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fake_llm_records_calls_and_fails_on_request():
    f = FakeLLM({"SUM": '{"summary": "s"}'}); f.fail_next(1)
    with pytest.raises(LLMError): f.complete(system="SUM\nmore", user="u", model="m")
    assert f.complete(system="SUM\nmore", user="u", model="m") == '{"summary": "s"}' and len(f.calls) == 2

def test_fake_embed_is_deterministic():
    f = FakeLLM(dim=4)
    assert f.embed(["a"], model="e") == f.embed(["a"], model="e") and len(f.embed(["a"], model="e")[0]) == 4

def test_lmstudio_client_builds_json_schema_request(monkeypatch):
    captured = {}
    class FakeCompletions:
        def create(self, **kw): captured.update(kw); return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))])
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok")
    monkeypatch.setattr(c._client.chat, "completions", FakeCompletions())
    assert c.complete(system="s", user="u", model="m", json_schema={"type": "object"}) == '{"a":1}'
    assert captured["response_format"]["json_schema"]["schema"] == {"type": "object"} and captured["model"] == "m"

def test_lmstudio_client_wraps_errors(monkeypatch):
    c = LMStudioClient("http://127.0.0.1:1/v1", "tok", timeout_s=1)
    with pytest.raises(LLMError):
        c.list_models()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement the three modules and `llm-check`**

`chatmaid llm-check` prints the model list and runs one `complete(system="Reply with the single word OK.", user="ping", model=cfg.lmstudio.model_small, max_tokens=8)` and prints the reply; exits 1 with the `LLMError` message when the server is unreachable or the token file is missing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v` — Expected: 4 passed.

- [ ] **Step 5: Verify against LM Studio**

Run: `uv run chatmaid llm-check` — Expected: the seven chat models and the embedding model listed, then a reply containing `OK` (first call may take about a minute while the model loads).

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/llm src/chatmaid/cli.py tests/test_llm.py
git commit -m "feat: LLM protocol, LM Studio client and FakeLLM"
```

---

### Task 9: Init pass 1 — ingest (resumable) and `chatmaid status`

**Files:**
- Create: `src/chatmaid/init/__init__.py`, `src/chatmaid/init/pipeline.py`
- Modify: `src/chatmaid/cli.py` (register `init`, `status`)
- Test: `tests/test_ingest.py`

**Interfaces:**
- Produces: `InitOptions(since: int | None = None, limit_conversations: int | None = None, skip_bulk: bool = True, chunk_chars: int = 9000, passes: tuple[str, ...] = ("ingest", "digest", "style", "embed"))`; `IngestStats(messages: int, new_messages: int, conversations: int, identities: int, skipped_dup: int)`; `InitPipeline(store: Store, connector: Connector, llm: LLM, cfg: Config)` with `ingest(opts: InitOptions) -> IngestStats` and `run(opts) -> dict[str, object]` executing the requested passes in order.
- Produces (module-level in `init/pipeline.py`, shared with Task 13): `build_thread_index(store: Store, platform: str, account: str) -> ThreadIndex` (rebuilt from `store.message_ids_for_account`) and `conversation_key(index: ThreadIndex, msg: NormalizedMessage) -> str` returning `"email:" + msg.ref.account + ":" + index.key_for(msg.message_id, msg.references + ((msg.in_reply_to,) if msg.in_reply_to else ()), thread_fallback_key(msg.subject, participant emails))`.
- Ingest algorithm (the part the signature does not determine): per account, per folder, read rows after the checkpoint `("ingest", "<account>/<folder>") -> {"last_uid": int}`; for each message: `store.upsert_identity` for sender (owner flag from `msg.is_owner` only when the sender matches `OwnerIdentity`) and recipients; conversation key from `conversation_key` with one `ThreadIndex` per account kept in memory for the run (built by `build_thread_index` at start and updated with each new message); title = `normalize_subject` of the earliest message or its raw subject; `conversation.bulk` is set when every message so far is bulk; checkpoint after each folder with the last uid.
- `chatmaid init [--since YYYY-MM-DD] [--limit N] [--pass ingest|digest|style|embed|all] [--fake-llm]` and `chatmaid status` (prints `store.counts()` and the number of conversations with and without a summary).

- [ ] **Step 1: Write the failing tests**

```python
def test_ingest_builds_conversations_and_identities(fake_env, store):
    p = InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config)
    st = p.ingest(InitOptions(passes=("ingest",)))
    assert st.new_messages >= 3 and st.conversations >= 2
    assert store.counts()["identities"] >= 2 and store.owner_contact_id()

def test_ingest_threads_reply_into_same_conversation(fake_env, store):   # fixture: INBOX uid 1 <a@x>, Sent uid 3 In-Reply-To <a@x>
    InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config).ingest(InitOptions())
    convs = store.conversations()
    assert any(r["message_count"] == 2 for r in convs)

def test_ingest_is_resumable_and_idempotent(fake_env, store):
    p = InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config)
    first = p.ingest(InitOptions()); second = p.ingest(InitOptions())
    assert second.new_messages == 0 and store.counts()["messages"] == first.new_messages
    assert store.get_checkpoint("ingest", "a1/INBOX")["last_uid"] == 2

def test_ingest_since_filters_old_messages(fake_env, store):   # fixture dates: uid 1 at 1_600_000_000, uid 2 at 1_700_000_000
    InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config).ingest(InitOptions(since=1_650_000_000))
    assert all(r["sent_at"] >= 1_650_000_000 for c in store.conversations() for r in store.messages_for(c["id"]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingest.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `pipeline.py` (ingest only) and the two commands**

Wrap each folder's processing so an `LLMError` or `sqlite3.OperationalError` logs and continues with the next folder; ingest makes no LLM calls.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ingest.py -v` — Expected: 4 passed.

- [ ] **Step 5: Verify on the real cache (read-only; writes only `data/index.sqlite`)**

Run: `uv run chatmaid init --pass ingest --since 2025-10-01` then `uv run chatmaid status` — Expected: messages in the low thousands, conversations fewer than messages, identities > 0, zero conversations with a summary; a second run reports `new_messages=0`.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/init src/chatmaid/store/db.py src/chatmaid/cli.py tests/test_ingest.py
git commit -m "feat: resumable ingest pass and status command"
```

---

### Task 10: Init pass 2 — digest (summaries and memories)

**Files:**
- Create: `src/chatmaid/init/chunking.py`, `src/chatmaid/llm/prompts/chunk_summary.md`, `src/chatmaid/llm/prompts/rolling_summary.md`, `src/chatmaid/llm/prompts/__init__.py`
- Modify: `src/chatmaid/init/pipeline.py`
- Test: `tests/test_digest.py`, `tests/test_chunking.py`

**Interfaces:**
- Produces: `detect_language(text: str) -> str` returning `"zh"` when at least 30 % of alphabetic characters are CJK, else `"en"`; `chunk_messages(msgs: list[NormalizedMessage], *, chunk_chars: int) -> list[list[NormalizedMessage]]` splitting in order so each chunk's total `len(text)` stays under `chunk_chars` (a single oversized message forms its own chunk, text truncated to `chunk_chars`).
- Produces: `prompts.load(name: str) -> Prompt(name: str, system: str, schema: dict)` reading `prompts/<name>.md` whose first line is `# <name>`, whose first fenced ```json block is the schema, and whose remaining text (including that first line) is the system text. Schemas: `chunk_summary` → `{"summary": str, "events": [{"date": str, "text": str}], "facts": [{"about": str, "text": str}]}`; `rolling_summary` → `{"summary": str}`.
- Produces: `DigestStats(conversations: int, chunks: int, memories: int, failed: int)`; `InitPipeline.digest(opts) -> DigestStats`: for conversations `needing_summary=True` (newest first, `exclude_bulk=opts.skip_bulk`, `limit=opts.limit_conversations`), fetch texts through `connector.fetch` for `store.message_refs_for(cid)`, chunk, call `chunk_summary` with `model_main` per chunk (user text = messages rendered as `[<date> <sender name or email>] <text>` lines), merge summaries with `rolling_summary`, write `set_conversation_summary` with events, `add_memory(kind="fact"|"event", ...)` with `source_ref` = the chunk's first message ref key and `contact_id` = the conversation's non-owner contact when exactly one exists; checkpoint `("digest", str(cid)) -> {"chunk": i, "summary": partial}` after each chunk; an `LLMError` marks the conversation failed and moves on.

- [ ] **Step 1: Write the failing tests**

```python
def test_detect_language():
    assert detect_language("你好，明天见") == "zh" and detect_language("see you tomorrow") == "en"

def test_chunk_messages_respects_budget(msgs_of_len):   # helper builds messages with given text lengths
    chunks = chunk_messages(msgs_of_len([4000, 4000, 4000, 20000]), chunk_chars=9000)
    assert [len(c) for c in chunks] == [2, 1, 1] and len(chunks[2][0].text) == 9000

def test_digest_writes_summary_and_memories(ingested_env):   # fixture: store after ingest + FakeLLM with canned chunk_summary/rolling_summary JSON
    llm = FakeLLM({"chunk_summary": json.dumps({"summary": "S1", "events": [{"date": "2024-10-01", "text": "met"}], "facts": [{"about": "Ann", "text": "likes tea"}]}), "rolling_summary": json.dumps({"summary": "S"})})
    st = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config).digest(InitOptions())
    assert st.conversations >= 1 and st.memories >= 1
    row = ingested_env.store.conversations(needing_summary=False)[0]
    assert row["summary"] in ("S", "S1")

def test_digest_resumes_after_llm_failure(ingested_env):
    llm = FakeLLM({"chunk_summary": '{"summary":"S1","events":[],"facts":[]}', "rolling_summary": '{"summary":"S"}'}); llm.fail_next(1)
    p = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    first = p.digest(InitOptions()); second = p.digest(InitOptions())
    assert first.failed == 1 and second.failed == 0 and second.conversations >= 1

def test_digest_skips_bulk_conversations(ingested_env):   # fixture includes a List-Unsubscribe thread
    llm = FakeLLM({"chunk_summary": '{"summary":"S1","events":[],"facts":[]}', "rolling_summary": '{"summary":"S"}'})
    InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config).digest(InitOptions())
    rows = ingested_env.store.conversations(exclude_bulk=False)
    bulk = [c for c in rows if c["bulk"]]
    assert bulk and all(c["summary"] is None for c in bulk)
    assert all(c["summary"] is not None for c in rows if not c["bulk"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_chunking.py tests/test_digest.py -v` — Expected: FAIL.

- [ ] **Step 3: Write the two prompt files and implement chunking and digest**

`chunk_summary.md` system text (exact copy): "You summarize one chunk of a personal conversation for the owner's private assistant. Write in the conversation's main language. Return JSON only: a neutral summary of what was discussed and decided (at most 120 words), dated events worth remembering, and stable facts about the people involved. Never invent details." `rolling_summary.md`: "Merge the previous summary and the new chunk summary into one summary of at most 150 words in the same language, keeping decisions, open questions and relationship context. Return JSON only."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_chunking.py tests/test_digest.py -v` — Expected: 5 passed.

- [ ] **Step 5: Verify on real data with a small limit**

Run: `uv run chatmaid init --pass digest --limit 3` then `uv run chatmaid status` — Expected: 3 conversations gain a summary; `uv run chatmaid show <id>` (Task 12) later displays it.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/init src/chatmaid/llm/prompts tests/test_chunking.py tests/test_digest.py
git commit -m "feat: digest pass with chunked summaries and memory extraction"
```

---

### Task 11: Init passes 3 and 4 — style profile and embeddings

**Files:**
- Create: `src/chatmaid/llm/prompts/style_profile.md`
- Modify: `src/chatmaid/init/pipeline.py`
- Test: `tests/test_style_embed.py`

**Interfaces:**
- Produces: `InitPipeline.style(opts) -> dict[str, int]` (samples per language): collect owner messages (`is_owner`, not bulk, `len(text) >= 20`) newest first up to 60 per language (`detect_language`), render them as a numbered list, call `style_profile` with `model_heavy`, store `set_style_profile(language, "default", text, sample_refs)`; schema `{"profile": str}`; system text (exact copy): "Describe how this person writes to others, from their own messages only: tone, formality, typical length, greetings and sign-offs, punctuation and emoji habits, code-switching between languages, and phrases they reuse. Write 150 to 250 words in the same language as the samples. Return JSON only."
- Produces: `InitPipeline.embed(opts) -> int` (items embedded): for every conversation with a summary and every memory lacking an `embedding_item` row or whose `text_hash` (sha256 of text) changed, call `llm.embed` in batches of 32 with `model_embedding`, `store.upsert_embedding(kind, ref_id, text_hash, vector)`; kinds `"conversation"` and `"memory"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_style_profile_per_language(ingested_env):   # fixture has owner messages in en and zh
    llm = FakeLLM({"style_profile": '{"profile": "P"}'})
    out = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config).style(InitOptions())
    assert set(out) >= {"en"} and ingested_env.store.style_profile("en", "default") == "P"

def test_embed_is_incremental(digested_env):
    p = InitPipeline(digested_env.store, digested_env.connector, FakeLLM(dim=4), digested_env.config)
    n1 = p.embed(InitOptions()); n2 = p.embed(InitOptions())
    assert n1 >= 1 and n2 == 0 and digested_env.store.counts()["embeddings"] == n1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_style_embed.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `style` and `embed`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_style_embed.py -v` — Expected: 2 passed.

- [ ] **Step 5: Verify on real data**

Run: `uv run chatmaid init --pass style` and `uv run chatmaid init --pass embed` — Expected: a profile for `en` (and `zh` if enough Chinese owner mail exists); `status` shows `embeddings` equal to summarized conversations plus memories.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/init/pipeline.py src/chatmaid/llm/prompts/style_profile.md tests/test_style_embed.py
git commit -m "feat: style profile and incremental embedding passes"
```

---

### Task 12: History lookup and the read CLI (`conversations`, `show`, `lookup`)

**Files:**
- Create: `src/chatmaid/lookup.py`
- Modify: `src/chatmaid/cli.py`, `src/chatmaid/store/db.py` (add `search_messages`)
- Test: `tests/test_lookup.py`, `tests/test_cli_read.py`

**Interfaces:**
- Produces: `Store.search_messages(*, text: str | None, sender: str | None, since: int | None, until: int | None, limit: int) -> list[sqlite3.Row]` matching `subject LIKE` or `preview LIKE` (`%text%`) and `identity.address LIKE` or `identity.display_name LIKE` for `sender`.
- Produces: `Hit(ref: SourceRef, sent_at: int, sender: str, subject: str, snippet: str, score: float, conversation_id: int)`; `search_history(store: Store, connector: Connector, llm: LLM | None, *, query: str, contact: str | None = None, since: int | None = None, until: int | None = None, limit: int = 20, embedding_model: str | None = None) -> list[Hit]`: metadata candidates from `search_messages` (score 1.0), plus, when `llm` and `embedding_model` are given, the top 10 `search_embeddings` conversations and memories whose most recent messages are added (score = 1 / (1 + distance)); for each candidate `connector.fetch` and take a 160-character snippet around the first case-insensitive occurrence of `query` (else the preview); sort by score then `sent_at` descending; truncate to `limit`.
- CLI: `chatmaid conversations [--limit 20] [--all]` (default excludes bulk) prints `id  last  count  title  summary[:60]`; `chatmaid show <id> [--limit 30]` prints the summary, participants and recent messages `date  sender  preview`; `chatmaid lookup "<query>" [--contact X] [--since D] [--until D] [--limit N] [--no-vector]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_history_finds_by_subject_and_snippet(digested_env):
    hits = search_history(digested_env.store, digested_env.connector, None, query="hello")
    assert hits and "hello" in hits[0].snippet.lower()

def test_search_history_vector_path_adds_conversation(digested_env):
    llm = FakeLLM(dim=4)
    hits = search_history(digested_env.store, digested_env.connector, llm, query="zzz-not-in-any-subject", embedding_model="e")
    assert hits  # from vector neighbours

def test_cli_conversations_and_show(digested_env, capsys):
    assert main(["--config", str(digested_env.config_path), "conversations", "--limit", "5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:3] == ["id", "last", "count"]          # header row
    first_id = lines[1].split()[0]
    assert main(["--config", str(digested_env.config_path), "show", first_id]) == 0
    out = capsys.readouterr().out
    assert re.search(r"\d{4}-\d{2}-\d{2}", out) and "participants" in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lookup.py tests/test_cli_read.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `lookup.py`, `search_messages`, and the three commands**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lookup.py tests/test_cli_read.py -v` — Expected: 3 passed.

- [ ] **Step 5: Verify on real data**

Run: `uv run chatmaid conversations --limit 10`, `uv run chatmaid show <an id>`, `uv run chatmaid lookup "invoice" --since 2025-01-01 --limit 5` — Expected: readable tables; lookup returns rows with dates, senders and snippets.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/lookup.py src/chatmaid/store/db.py src/chatmaid/cli.py tests/test_lookup.py tests/test_cli_read.py
git commit -m "feat: history lookup and read-only CLI views"
```

---

### Task 13: Event source and watcher (`chatmaid watch`)

**Files:**
- Create: `src/chatmaid/connectors/evolution/events.py`, `src/chatmaid/daemon.py`
- Modify: `src/chatmaid/cli.py` (register `watch`)
- Test: `tests/test_events.py`, `tests/test_daemon.py`

**Interfaces:**
- Produces: `EventSource(db_paths: list[Path], *, interval_s: int = 60, enable_dbus: bool = True)` with `async triggers() -> AsyncIterator[str]` yielding `"dbus"` on the session-bus signal `Newmail` (interface `org.gnome.evolution.mail.dbus.Signal`, path `/org/gnome/evolution/mail/newmail`, via `dbus_fast.aio.MessageBus` with an `add_match` rule and a message handler), `"file"` when `watchfiles.awatch` reports a change to any `db_paths` entry (debounced to one trigger per 2 s), and `"timer"` every `interval_s`; D-Bus connection failure logs a warning and the source continues with the other two.
- Produces: `Watcher(store: Store, connector: Connector, events: EventSource)` with `async run(stop: asyncio.Event) -> None`, `handle_trigger(kind: str) -> int` (number of messages routed) that calls `connector.scan_new(state)` with `state = store.get_checkpoint("watch", connector.name)`, falling back when absent to the ingest checkpoints' `last_uid` values under the same `"<account>/<folder>"` keys (ruling R14), else `{}`; refs whose `ref_key` the store already holds (`Store.has_ref(ref_key)`) or whose `Message-ID` is already present are skipped without an event, fetches each ref, routes with `route(msg) -> str` returning `"owner"` when `msg.is_owner` else `"incoming"`, then `on_owner_message(msg)` or `on_incoming(msg)`, and saves the new state; both handlers upsert identities, the conversation (same key derivation as ingest, reusing a `ThreadIndex` built from the store) and the message, then `store.log_event("evolution", "owner"|"incoming", msg.ref.key(), {"bulk": msg.bulk, "conversation_id": cid, "subject": msg.subject})`; `sqlite3.OperationalError` from `scan_new` is logged and the trigger is skipped.
- CLI: `chatmaid watch [--interval 60] [--no-dbus]` runs until SIGINT and prints one line per routed message `kind  conversation_id  sender  subject`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_event_source_timer_and_file(tmp_path):
    db = tmp_path / "folders.db"; db.write_bytes(b"")
    src = EventSource([db], interval_s=1, enable_dbus=False)
    it = src.triggers()
    first = await asyncio.wait_for(anext(it), 3)
    db.write_bytes(b"x")
    kinds = {first, await asyncio.wait_for(anext(it), 3)}
    assert kinds & {"timer", "file"}

def test_route_owner_to_owner_is_owner(watch_env):   # message From me@x.org To me2@x.org (both owner identities)
    msg = watch_env.message(sender="me@x.org", to="me2@x.org", folder="INBOX")
    assert watch_env.watcher.route(msg) == "owner"

def test_first_trigger_after_ingest_routes_nothing(watch_env):   # store already holds every fixture message
    assert watch_env.watcher.handle_trigger("timer") == 0 and watch_env.store.events() == []

def test_handle_trigger_routes_and_logs(watch_env):
    watch_env.watcher.handle_trigger("timer")                     # establish the watermark state
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", sender="ann@x.org", to="me@x.org", date=1_700_000_500, body="new question")
    watch_env.add_row("[Gmail]/Sent Mail", uid="8", message_id="<s8@x>", sender="me@x.org", to="ann@x.org", date=1_700_000_600, body="my answer")
    n = watch_env.watcher.handle_trigger("timer")
    kinds = sorted(e["kind"] for e in watch_env.store.events())
    assert n == 2 and kinds == ["incoming", "owner"]
    assert watch_env.watcher.handle_trigger("timer") == 0

def test_handle_trigger_time_mode_folder(watch_env):   # opaque uids: watermark is dreceived, inclusive boundary must not re-route
    watch_env.add_folder("Exchange Inbox", time_mode=True)
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("Exchange Inbox", uid="AQMkADMy1", message_id="<e1@x>", sender="bob@x.org", to="me@x.org", date=1_700_000_700, dreceived=1_700_000_700, body="hi")
    assert watch_env.watcher.handle_trigger("timer") == 1
    assert watch_env.watcher.handle_trigger("timer") == 0

def test_scan_survives_locked_db(watch_env):
    watch_env.watcher.handle_trigger("timer")
    watch_env.add_row("INBOX", uid="7", message_id="<n7@x>", sender="ann@x.org", to="me@x.org", date=1_700_000_500, body="new question")
    watch_env.add_row("[Gmail]/Sent Mail", uid="8", message_id="<s8@x>", sender="me@x.org", to="ann@x.org", date=1_700_000_600, body="my answer")
    lock = sqlite3.connect(watch_env.db_path); lock.execute("BEGIN EXCLUSIVE")
    try:
        assert watch_env.watcher.handle_trigger("timer") == 0
    finally:
        lock.rollback(); lock.close()
    assert watch_env.watcher.handle_trigger("timer") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_events.py tests/test_daemon.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `events.py`, `daemon.py`, and `watch`**

The `Watcher` reuses Task 9's module-level `build_thread_index` and `conversation_key` so both paths produce identical keys; it keeps one `ThreadIndex` per account for its lifetime and adds each routed message to it. Before routing a ref returned by `scan_new`, the watcher skips it when the store already holds that `ref_key` (boundary duplicates from the inclusive `dreceived` watermark, see Global Constraints); `scan_new` itself follows the watermark rule (R6/R7) and the test `test_handle_trigger_routes_and_logs` must also cover a folder with non-integer uids.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_events.py tests/test_daemon.py -v` — Expected: 4 passed.

- [ ] **Step 5: Verify live (receiving mail only; nothing is sent by chatmaid)**

Run: `uv run chatmaid watch --interval 30`; from the phone, send one email to one in-scope account and reply to it from Evolution. Expected: within a minute, one `incoming` line, and after the reply one `owner` line; `uv run chatmaid status` shows two new events. Stop with Ctrl-C.

- [ ] **Step 6: Commit**

```bash
git add src/chatmaid/connectors/evolution/events.py src/chatmaid/daemon.py src/chatmaid/init/pipeline.py src/chatmaid/cli.py tests/test_events.py tests/test_daemon.py
git commit -m "feat: event source and watcher routing owner and incoming mail"
```

---

### Task 14: Documentation, full test run, and branch wrap-up

**Files:**
- Modify: `README.md` (setup: `uv sync`, `cp config.example.toml data/config.toml`, token file, `lms` model names, command list), `docs/research/README.md` (no change unless links broke)
- Test: full suite

- [ ] **Step 1: Update `README.md`** with a "Running" section listing every command from Tasks 3–13 and the read-only guarantees from Global Constraints.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q` — Expected: all tests pass, no warnings about unclosed databases.

- [ ] **Step 3: Confirm the token and data files are ignored**

Run: `git status --short --ignored | grep -E "LMStudio_token|^!! data/"` — Expected: both listed as ignored.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: setup and command reference for phase 1a"
```

---

## Follow-on plans (not part of this document)

- **Plan 1b, reply-draft flow:** triage with `model_small`, context header assembly, drafting, the `draft` and `cloud_call` tables (schema v2), the pseudonymizer, the cloud provider interface with `codex exec` (default) and `claude -p`, and the feedback/regenerate and cloud-improvement loops. Depends on `Store`, `Connector.fetch`, `search_history`, `LLM`, `Watcher.on_incoming` from this plan.
- **Plan 1c, web UI:** FastAPI views over the same `Store`.
- **WeChat importer:** once the archive exists.
