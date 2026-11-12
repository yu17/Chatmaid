# Phase 1c: web UI and the 1b leftovers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone-first local web UI (`chatmaid serve`) to read a recent email with its thread and every related conversation, and to draft, revise, cloud-improve and approve a reply; plus the daemon fixes parked by plan 1b.

**Architecture:** The CLI and the reply flow are split into focused modules first (no behaviour change), then the 1b fixes land, then a read-model module (`views.py`) and a FastAPI app (`web/`) that opens its own `Store` and `EvolutionConnector` per request and runs model work on one background worker thread. Server-rendered Jinja2 templates, one small JS file, one CSS file, no external assets.

**Tech Stack:** Python 3.14, uv, SQLite (WAL) + sqlite-vec, FastAPI, uvicorn, Jinja2, python-multipart; tests with pytest and FastAPI's `TestClient` (httpx).

**Spec:** `docs/superpowers/specs/2026-11-12-phase1c-web-ui-design.md` (approved 2026-11-12), baseline `docs/superpowers/specs/2026-10-21-phase1-design.md` section 9.

## Global Constraints

- Read-only toward mail sources: nothing sends mail or writes to Evolution or any server. Approve records approval; the owner copies the text.
- Cloud calls only through `propose_cloud` → payload shown verbatim → separate Send (`send_cloud`) or Decline (`decline_cloud`).
- `LMStudio_token.txt` is never logged or rendered.
- No CDN assets, external fonts or analytics; every asset is served by the app.
- `[web]` config: `hosts` (list, default `["127.0.0.1"]`), `port` (default `8765`), `allowed_hosts` (list, default = `hosts`), `base_path` (default `""`). The owner adds WireGuard addresses to `hosts`; pages are plain HTTP.
- Requests whose `Host` (port stripped, IPv6 brackets stripped) is not in `allowed_hosts` get 400. Every POST carries the per-process CSRF token (form field `csrf`), else 403.
- One model job at a time from the UI (one worker thread); every page stays responsive while a job runs.
- `uv run pytest -q -W error` passes after every task; the suite starts at 399 tests.
- Commit trailer: `Co-Authored-By: <the model that wrote the commit> <noreply@anthropic.com>` (ruling R21).
- Out of scope: contact/identity merge, push notifications, systemd unit, WeChat.

## Review Focus

1. Mail content containing HTML or script (`<script>`, `<img onerror>`) in subject, sender name, body, summary or a draft must render as text everywhere → Task 7 and Task 8 tests post such content and assert it is escaped.
2. A message deleted from the source since ingest (Gmail purges Trash after 30 days) opened from the Inbox or a thread → the stored preview with a "no longer in the mail store" note, never a 500 → Task 8 test.
3. An action on a draft that is no longer pending (watch superseded it, or a second tab already approved) → the page shows the error message and the draft's current state, never a 500 → Task 9 test.
4. A second model job submitted while one runs → queued and shown as queued; a job that raises (`LLMError`, `CloudError`, `ValueError`) → shown as failed with its message, worker keeps running → Task 9 tests.
5. A conversation with hundreds of messages (the live index has one of 198) → the Message and Conversation pages render at most 50 messages, newest first, with a "show older" link → Task 8 test.

---

### Task 1: Split `cli.py` into a package

**Files:**
- Delete: `src/chatmaid/cli.py`
- Create: `src/chatmaid/cli/__init__.py` (`build_parser`, `main`), `src/chatmaid/cli/common.py`, `src/chatmaid/cli/index.py` (accounts, scan, llm-check, init, status), `src/chatmaid/cli/read.py` (conversations, senders, show, lookup, related), `src/chatmaid/cli/watch.py`, `src/chatmaid/cli/drafts.py` (drafts, draft *, cloud-calls, cloud-check)
- Modify: tests that monkeypatch `chatmaid.cli.*`

**Interfaces:**
- Produces: `chatmaid.cli.main(argv: list[str] | None = None) -> int` and `chatmaid.cli.build_parser()` unchanged (entry point `chatmaid = "chatmaid.cli:main"` unchanged). `chatmaid.cli.common` holds `lmstudio_client(cfg, token) -> LMStudioClient`, `read_token` (re-exported from `chatmaid.config`), `open_store(cfg) -> Store`, `open_flow(cfg, *, need_llm: bool, fake_llm: bool = False) -> tuple[Store, EvolutionConnector, ReplyFlow]`, `day(ts: int | None) -> str`, `parse_since(text: str) -> int`, `NoLLM`. Command modules import `from chatmaid.cli import common` and call `common.lmstudio_client(...)` / `common.read_token(...)` through the module, so tests patch `chatmaid.cli.common.lmstudio_client` and `chatmaid.cli.common.read_token`.

- [ ] **Step 1:** Move the code, one module per group above; each module keeps its `register_*` functions and `build_parser` calls them in today's order. No behaviour or output change.
- [ ] **Step 2:** Update monkeypatch targets in `tests/test_cli_read.py`, `tests/test_cli_drafts.py` and any other test patching `chatmaid.cli._lmstudio_client` / `chatmaid.cli.read_token` to the `common` names.
- [ ] **Step 3:** Run `uv run pytest -q -W error`; expected 399 passed. Run `uv run chatmaid --help`; expected the same 15 subcommands as before (accounts, scan, llm-check, init, status, conversations, senders, show, lookup, related, watch, cloud-check, drafts, draft, cloud-calls).
- [ ] **Step 4:** Commit `refactor(cli): split cli.py into a package by command group`.

### Task 2: Split `reply/flow.py` by routine

**Files:**
- Modify: `src/chatmaid/reply/flow.py` (keeps `ReplyFlow`, `CloudProposal`, `__init__`, `dispatch`)
- Create: `src/chatmaid/reply/incoming.py` (routine B: `on_incoming`), `src/chatmaid/reply/owner.py` (routine C: `on_owner_message`, `update_summary`), `src/chatmaid/reply/actions.py` (`_pending`, `_latest_first`, `_version`, `_context_for`, `latest_text`, `regenerate`, `resolve`), `src/chatmaid/reply/cloudflow.py` (`propose_cloud`, `_cloud_payload`, `_proposed_call`, `send_cloud`, `decline_cloud`)

**Interfaces:**
- Produces: `class ReplyFlow(IncomingMixin, OwnerMixin, DraftActionsMixin, CloudMixin)` in `flow.py` with the identical public API and constructor; each mixin lives in its module and uses `self.store`, `self.connector`, `self.llm`, `self.cfg`, `self.now`, `self.backend_factory`. Every existing import (`from chatmaid.reply.flow import ReplyFlow, CloudProposal`) keeps working; tests that monkeypatch module-level names in `chatmaid.reply.flow` are updated to the new module.

- [ ] **Step 1:** Move the methods into the mixins; no logic change.
- [ ] **Step 2:** Run `uv run pytest -q -W error`; expected 399 passed.
- [ ] **Step 3:** Commit `refactor(reply): split flow.py into routine and action mixins`.

### Task 3: Small 1b fixes — connector errors, phone regex, stop-time counters

**Files:**
- Modify: `src/chatmaid/cli/common.py` (`open_flow`), `src/chatmaid/cli/drafts.py` (`drafts` command), `src/chatmaid/cloud/pseudonym.py:28`, `src/chatmaid/daemon.py` (`_handle`)
- Test: `tests/test_cli_drafts.py`, `tests/test_pseudonym.py`, `tests/test_daemon.py`

**Interfaces:**
- Produces: `open_flow` raises `ConfigError` (not `MissingAccount`) when an account's `.source` file is missing.

- [ ] **Step 1: Write the failing tests**

```python
def test_drafts_reports_missing_account_without_traceback(fake_env, capsys):
    # point email.accounts at a uid with no .source file
    ...  # rewrite fake_env.config_path: accounts = ["nope"]
    assert main(["--config", str(fake_env.config_path), "drafts"]) == 1
    err = capsys.readouterr().err
    assert "no Evolution .source file for account 'nope'" in err and "Traceback" not in err

def test_phone_numbers_do_not_join_across_lines():
    p = Pseudonymizer([])
    out = p.redact("Order 123\n456 7890 thanks")
    assert all("\n" not in original for original in p.mapping().values())
    assert "Order 123\n" in out

def test_stop_mid_scan_keeps_body_wait_counters(watch_env):
    # two body-less refs pending with attempts 1; stop after the first is handled
    ...
    saved = watch_env.store.get_checkpoint("watch", watch_env.connector.name)
    assert saved["_pending"][second_ref_key] == 1  # carried over, not dropped
```

- [ ] **Step 2:** Run them; expected 3 failures (traceback / phone spans the newline / counter missing).
- [ ] **Step 3:** `open_flow` wraps `load_accounts` and re-raises `MissingAccount` as `ConfigError(str(e))`; `drafts` catches `ConfigError`, prints it, returns 1 (as `draft` does). `PHONE_RE` separator class becomes `[\d \t().\-/]` (no `\s`). In `_handle`, on stop, copy `pending[k]` into `still_pending` for every ref in `refs[i:]` that has an entry.
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `fix: missing-account error in drafts, phone regex across lines, keep body-wait counters on stop`.

### Task 4: Bounded approval match and outcome after UI approval

**Files:**
- Modify: `src/chatmaid/reply/owner.py` (`on_owner_message`), `src/chatmaid/store/db.py` (two methods)
- Test: `tests/test_flow.py`

**Interfaces:**
- Produces: `Store.unmatched_resolved_draft(conversation_id: int, *, before: int) -> sqlite3.Row | None` (newest draft with status `approved` or `edited`, `outcome_ref IS NULL`, `created_at <= before`); `Store.set_draft_outcome(draft_id: int, outcome_ref: str, outcome_preview: str) -> None` (status unchanged). Constants in `owner.py`: `APPROVE_SLACK_CHARS = 200`, `APPROVE_SLACK_RATIO = 0.2`.

- [ ] **Step 1: Write the failing tests** (use `flow_env`; create a pending draft with one version `"Friday works."`)

```python
def test_sent_mail_that_only_starts_with_the_draft_is_edited(flow_env): ...
    # owner sends "Friday works." + 300 new characters -> status "edited"
def test_short_addition_still_counts_as_approved(flow_env): ...
    # owner sends "Friday works. See you!" -> status "approved", final_text "Friday works."
def test_sent_mail_after_ui_approval_records_the_outcome(flow_env): ...
    # flow.resolve(draft_id, "approve"); then the owner's mail arrives
    # -> status stays "approved"; outcome_ref == sent ref key; outcome_preview set;
    # event_log has kind "draft_outcome"
```

- [ ] **Step 2:** Run them; expected the first and third fail.
- [ ] **Step 3:** A version matches when `sent.startswith(v)` and `len(sent) - len(v) <= max(APPROVE_SLACK_CHARS, APPROVE_SLACK_RATIO * len(v))` (lengths of the `_norm`ed texts). With no pending draft, `on_owner_message` looks up `unmatched_resolved_draft(conversation_id, before=msg.sent_at)` and records the outcome with event `("reply", "draft_outcome", ref, {"draft_id": ...})`.
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `fix(reply): bound the approval prefix match; record the sent outcome after UI approval`.

### Task 5: Untriaged-message retry queue

**Files:**
- Modify: `src/chatmaid/store/db.py`, `src/chatmaid/reply/incoming.py`, `src/chatmaid/daemon.py`, `src/chatmaid/cli/watch.py`
- Test: `tests/test_flow.py`, `tests/test_daemon.py`

**Interfaces:**
- Produces:
  - `Store.untriaged_incoming(*, since: int, max_attempts: int = 3, limit: int = 50) -> list[sqlite3.Row]`: message rows, newest first, with an `event_log` row (`source='evolution'`, `kind='incoming'`, `ref=message.ref_key`), no `triage` row, `is_owner = 0`, `sent_at >= since`, no owner message in the same conversation with a later `sent_at`, and fewer than `max_attempts` `event_log` rows (`source='reply'`, `kind='draft_failed'`, same ref). Messages `init` indexed have no `incoming` event and are never returned.
  - `ReplyFlow.retry_untriaged(*, limit: int = 20) -> int`: for each row, fetch from the connector (a `KeyError` logs a `draft_failed` event with `{"error": "gone from the mail store", "message_id": id}` and continues), then `self.dispatch("incoming", row["conversation_id"], msg)`; returns how many now have a triage row. `since = self.now() - cfg.draft.max_age_days * 86400`.
  - `Watcher.after_scan: Callable[[], None] | None`, called at the end of every `handle_trigger` (exceptions logged, never raised).
  - `watch` (unless `--no-draft`) calls `flow.retry_untriaged()` once before the loop and sets `watcher.after_scan = flow.retry_untriaged`.

- [ ] **Step 1: Write the failing tests**

```python
def test_untriaged_incoming_skips_init_messages_answered_threads_and_spent_attempts(flow_env): ...
def test_retry_untriaged_triages_after_the_model_recovers(flow_env):
    # make the fake LLM raise LLMError for triage, arrive a message -> no triage row,
    # one draft_failed event; restore the LLM; retry_untriaged() == 1; triage row exists
def test_retry_gives_up_after_three_failures(flow_env): ...
def test_after_scan_runs_after_each_trigger_and_its_errors_are_contained(watch_env): ...
```

- [ ] **Step 2:** Run them; expected failures (missing methods/attribute).
- [ ] **Step 3:** Implement as specified above.
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `feat(watch): retry incoming messages left untriaged`.

### Task 6: Draft on demand and the read models

**Files:**
- Modify: `src/chatmaid/reply/incoming.py`
- Create: `src/chatmaid/views.py`
- Test: `tests/test_flow.py`, `tests/test_views.py`

**Interfaces:**
- Produces:
  - `ReplyFlow.draft_for_message(message_id: int) -> int`: build context and draft candidates for any stored incoming message without triage; supersede the conversation's pending draft in the same transaction (as `on_incoming` does); event `("reply", "draft", ref, {"draft_id", "candidates", "on_demand": True})`. `ValueError` for an unknown id or an owner message. Shares the drafting part with `on_incoming` (extract `_create_draft(conversation_id, row, msg) -> int`).
  - `views.py` dataclasses and functions (all take an open `Store`; those that read text also take the connector):
    - `InboxRow(message_id, conversation_id, sent_at, sender, subject, account, category, triage, draft_id)`; `inbox(store, *, filter: str = "default", before: int | None = None, limit: int = 50, account_labels: dict[str, str]) -> list[InboxRow]`. Rows: messages with `is_owner = 0`, newest first, `sent_at < before` when given. Filters: `"default"` excludes conversations categorized `promotional`; `"all"`; `"needs_reply"` (triage category `needs_reply`); `"drafts"` (conversation has a pending draft); any name in `CATEGORIES` (conversation category). `sender` is the identity's display name or address; `account` is the label from `account_labels` (uid → owner address of that account, from `connector.accounts[*].identity_addresses[0]`), falling back to the uid.
    - `MessageView(message_id, conversation_id, sent_at, sender, recipients, subject, own_text, full_text, attachments, available: bool, preview)`; `message_view(store, connector, message_id) -> MessageView`: fetches through the connector; `own_text = strip_reply_chrome(text)`; on `KeyError`/`OSError`/`sqlite3.Error` returns `available=False` with the stored preview. `KeyError` if the message id is unknown.
    - `ConversationView(conversation_id, title, category, summary, participants: list[str], messages: list[InboxRow-like rows], older_before: int | None)`; `conversation_view(store, conversation_id, *, before: int | None = None, limit: int = 50) -> ConversationView` (index rows only, newest first; `older_before` is the oldest shown `sent_at` when more exist).
    - `status_view(store) -> dict` with `counts`, `with_summary`, `without_summary`, `categories`, `recent_events` (last 50 `event_log` rows), `failures` (events of kind `error`, `draft_failed`, `owner_failed`, last 50), `untriaged` (`store.untriaged_incoming(since=now - max_age_days)`). The `status` CLI command prints from `status_view` (same output as today).
    - `account_labels(connector) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests** (`flow_env` / `digested_env`)

```python
def test_draft_for_message_drafts_without_triage(flow_env): ...
    # arrive a message the fake triage marks informational; draft_for_message(id) -> pending draft,
    # no new triage row, event payload on_demand True
def test_draft_for_message_rejects_owner_messages(flow_env): ...
def test_inbox_filters(flow_env): ...  # default hides promotional; needs_reply; drafts; all; paging by before
def test_message_view_falls_back_to_preview_when_gone(digested_env, monkeypatch): ...
def test_conversation_view_pages_newest_first(store, sample_message): ...  # 60 messages -> 50 shown, older_before set
def test_status_cli_output_unchanged(cli_env, capsys): ...  # same lines as before the refactor
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement. SQL stays in the store: add `Store.inbox_rows(*, filter: str, before: int | None, limit: int) -> list[sqlite3.Row]` (one query: message ⨝ identity ⟕ triage ⟕ conversation ⟕ pending draft) and `Store.conversation_messages(conversation_id, *, before: int | None, limit: int) -> list[sqlite3.Row]`; `views.py` never touches `store._conn` and only shapes rows into the dataclasses.
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `feat: draft on demand and read models for the web UI`.

### Task 7: `chatmaid serve`, the app shell and the Inbox

**Files:**
- Modify: `pyproject.toml` (deps `fastapi`, `uvicorn`, `jinja2`, `python-multipart`; dev `httpx`), `src/chatmaid/config.py` (`WebConfig`), `config.example.toml` (`[web]`), `src/chatmaid/cli/__init__.py` (register serve)
- Create: `src/chatmaid/web/__init__.py`, `src/chatmaid/web/app.py`, `src/chatmaid/web/services.py`, `src/chatmaid/web/serve.py`, `src/chatmaid/cli/serve.py`, `src/chatmaid/web/templates/base.html`, `src/chatmaid/web/templates/inbox.html`, `src/chatmaid/web/static/app.css`, `src/chatmaid/web/static/app.js`
- Test: `tests/test_web.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `WebConfig(hosts: tuple[str, ...], port: int, allowed_hosts: tuple[str, ...], base_path: str)` on `Config.web`; validation: `hosts` non-empty list of strings, `port` 1–65535, `base_path` empty or starting with `/` and not ending with `/`.
  - `Services` (dataclass) in `services.py`: `cfg: Config`, `open_store: Callable[[], Store]`, `open_connector: Callable[[], Connector]`, `make_llm: Callable[[], LLM]` (raises `ConfigError` without a token), `make_backend: Callable[[str | None], CloudBackend]`; `Services.from_config(cfg)`; context manager `services.session() -> Iterator[tuple[Store, Connector]]` opening and closing both; `services.flow(store, connector) -> ReplyFlow`.
  - `create_app(services: Services, *, jobs: JobRunner | None = None) -> FastAPI` in `app.py`: `root_path = cfg.web.base_path`; Jinja2 autoescape on; static mounted at `/static`; host-check middleware (400 `"unknown host"`); `app.state.csrf = secrets.token_urlsafe(32)` available to templates as `csrf`; a dependency `check_csrf(csrf: str = Form(...))` raising 403 used by every POST route. Routes in this task: `GET /` (Inbox, query params `filter`, `before`).
  - `bind_sockets(hosts: Iterable[str], port: int) -> list[socket.socket]` and `run(services: Services) -> None` in `serve.py` (`uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=bind_sockets(...))`); `chatmaid serve [--port N]` prints `serving on http://<host>:<port>/` per host.
  - `base.html`: viewport meta, nav (Inbox, Search, Status, Cloud), a flash area, `{% block content %}`; links built with `url_for` so `base_path` applies.

- [ ] **Step 1: Write the failing tests** (`tests/test_web.py`; a `web_env` fixture builds `Services` from `flow_env`'s config with `open_store`/`open_connector` opening the test index and the fake connector, `make_llm` returning the fake LLM, `make_backend` returning `FakeCloud`; `TestClient(create_app(services), base_url="http://127.0.0.1")`)

```python
def test_inbox_lists_incoming_mail_and_escapes_it(web_env): ...
    # arrive subject '<script>alert(1)</script>'; GET / -> 200; '&lt;script&gt;' in body, '<script>alert' not
def test_unknown_host_is_rejected(web_env): ...  # Host: evil.example -> 400
def test_bind_sockets_listens_on_each_host(): ...  # bind_sockets(["127.0.0.1"], 0) -> one listening socket
def test_web_config_defaults_and_validation(tmp_path): ...  # allowed_hosts defaults to hosts; bad port -> ConfigError
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement. Inbox page: filter chips (default, needs reply, drafts, all, each category), rows linking to `/messages/{id}`, a pending-draft marker, "older" link using `before`. CSS: single column, system font stack, max width 48rem, readable at 360 px wide. `app.js` empty except a comment; Task 9 fills it.
- [ ] **Step 4:** Run the suite; expected all pass. Manual: `uv run chatmaid serve --port 8799` against a scratch copy of the index loads `/` in a browser.
- [ ] **Step 5:** Commit `feat(web): chatmaid serve with host check, CSRF and the Inbox`.

### Task 8: Message, Conversation, Related and Search pages

**Files:**
- Modify: `src/chatmaid/web/app.py`
- Create: `src/chatmaid/web/templates/message.html`, `conversation.html`, `search.html`, `_threads.html` (thread-hit list partial)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `views.message_view`, `views.conversation_view`, `lookup.related(store, llm, conversation_id, *, embedding_model, categories, limit)`, `lookup.search(store, llm, *, query, embedding_model, contact, since, until, categories, limit)` (`llm=None` when `make_llm` raises `ConfigError`, with a notice "full-text only: no LM Studio token").
- Produces routes: `GET /messages/{message_id}`, `GET /conversations/{conversation_id}` (param `before`), `GET /search` (params `q`, `category` repeatable, `contact`, `since`, `until` as `YYYY-MM-DD`). Unknown ids → 404 page.

- [ ] **Step 1: Write the failing tests**

```python
def test_message_page_shows_text_thread_related_and_reply_panel(web_env): ...
def test_message_gone_from_source_shows_preview_and_note(web_env, monkeypatch): ...  # Review Focus 2
def test_long_conversation_pages_at_50(web_env): ...  # Review Focus 5
def test_search_page_lists_threads_and_escapes_snippets(web_env): ...  # Review Focus 1
def test_unknown_message_is_404(web_env): ...
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement. Message page order: header (from, to, date, account, category), `own_text` with the full text in a collapsed `<details>`, attachments listed by name, Reply panel (pending draft summary linking to `/drafts/{id}`, or a "Draft a reply" form posting to Task 9's route), Related panel (`_threads.html`, limit 10, link to `/conversations/{id}`), the thread (`conversation_view`, previews linking to each message). Text is rendered in `<pre class="mail">` with `white-space: pre-wrap`.
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `feat(web): message, conversation, related and search pages`.

### Task 9: Background jobs and the Draft page

**Files:**
- Create: `src/chatmaid/web/jobs.py`, `src/chatmaid/web/templates/draft.html`
- Modify: `src/chatmaid/web/app.py`, `src/chatmaid/web/static/app.js`, `src/chatmaid/web/templates/message.html`
- Test: `tests/test_web_jobs.py`, `tests/test_web.py`

**Interfaces:**
- Produces:
  - `Job(id: str, kind: str, label: str, subject_id: int, state: str, result: int | None, error: str | None, created_at: float)` with `state` in `queued|running|done|failed`; `JobRunner(services: Services)` with `submit(kind: str, label: str, subject_id: int, fn: Callable[[ReplyFlow], int | None]) -> Job`, `get(job_id: str) -> Job | None`, `for_subject(kind_prefix: str, subject_id: int) -> list[Job]` (unfinished first), `stop() -> None`. One daemon worker thread; each job opens its own session and flow (`services.session()` + `services.flow(...)`), closes them, and catches every `Exception` into `failed` with `str(e)`. Finished jobs kept in memory (last 200).
  - Routes: `POST /messages/{id}/draft` (submit `draft_for_message`, redirect to the message page), `GET /drafts/{id}`, `POST /drafts/{id}/regenerate` (field `comment`, optional `from_version`), `POST /drafts/{id}/approve` (field `version_id`), `POST /drafts/{id}/edit` (field `text`), `POST /drafts/{id}/dismiss`, `GET /jobs/{id}` (JSON `{state, result, error}`).
  - `app.js`: on pages with `[data-job]` elements, poll `/jobs/{id}` every 3 s and reload when a job finishes; Copy buttons (`[data-copy]`) use `navigator.clipboard.writeText` when `window.isSecureContext`, else select the target text and show "selected — copy with your phone's menu".

- [ ] **Step 1: Write the failing tests**

```python
def test_post_without_csrf_is_forbidden(web_env): ...  # POST /drafts/{id}/dismiss without csrf -> 403, draft still pending
def test_jobs_run_one_at_a_time_and_report_failures(): ...  # two jobs; second queued while first blocks on an Event; a raising job -> failed, next job still runs
def test_draft_button_creates_a_draft_via_the_worker(web_env): ...  # POST then wait for job done; GET /drafts/{id} lists candidates
def test_regenerate_approve_edit_dismiss_from_the_page(web_env): ...
def test_action_on_a_draft_no_longer_pending_shows_the_error(web_env): ...  # Review Focus 3: 200 page with "is dismissed, not pending"
def test_draft_page_escapes_candidate_text(web_env): ...
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement. Draft page: the message's subject and sender (link back), candidates of the latest round with rationale, Approve / Copy per candidate, feedback textarea → Regenerate, Edit textarea prefilled with candidate 1, Dismiss, earlier rounds and cloud versions in `<details>` with their comments, running/queued jobs for this draft with `data-job`. Synchronous actions (approve, edit, dismiss) run in the request; `ValueError` from the flow becomes a flash message on the re-rendered page. The app creates one `JobRunner` at startup and stops it on shutdown (lifespan).
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `feat(web): background model jobs and the draft page`.

### Task 10: Cloud improvement with consent, and the cloud audit page

**Files:**
- Modify: `src/chatmaid/web/app.py`, `src/chatmaid/web/templates/draft.html`
- Create: `src/chatmaid/web/templates/cloud_call.html`, `cloud.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `ReplyFlow.propose_cloud(draft_id, *, backend, comment, from_version) -> CloudProposal`, `send_cloud(call_id) -> int`, `decline_cloud(call_id)`; `Store.cloud_call`, `Store.cloud_calls`.
- Produces routes: `POST /drafts/{id}/cloud` (fields `backend` in `codex|claude`, default `cfg.cloud.default_backend`; `comment`; `from_version`) submits a `propose` job; `GET /cloud/{call_id}` shows the payload verbatim in `<pre>` with backend and model, and Send / Decline forms while `proposed`; `POST /cloud/{call_id}/send` submits a `send` job; `POST /cloud/{call_id}/decline`; `GET /cloud` lists calls (id, draft, backend, status, created, consented, error).

- [ ] **Step 1: Write the failing tests**

```python
def test_cloud_flow_shows_payload_before_send_and_nothing_is_sent_until_send(web_env): ...
    # POST /drafts/{id}/cloud -> job done -> call status 'proposed', FakeCloud not called;
    # GET /cloud/{call} shows the payload text; POST send -> job done -> new cloud version on the draft
def test_decline_marks_the_call_declined(web_env): ...
def test_cloud_audit_lists_calls(web_env): ...
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement. After a propose job finishes, the draft page links to `/cloud/{call_id}` (job `result` is the call id). The Send button's label reads "Send this to <backend>".
- [ ] **Step 4:** Run the suite; expected all pass.
- [ ] **Step 5:** Commit `feat(web): consent-gated cloud improvement and the cloud audit page`.

### Task 11: Status page, docs, and a live smoke run

**Files:**
- Modify: `src/chatmaid/web/app.py`, `README.md`, `config.example.toml`
- Create: `src/chatmaid/web/templates/status.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `views.status_view(store)`, `ReplyFlow.retry_untriaged`, `ReplyFlow.dispatch`.
- Produces routes: `GET /status`; `POST /status/retry/{message_id}` submits a job that fetches the message and calls `flow.dispatch("incoming", cid, msg)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_page_shows_counts_failures_and_untriaged(web_env): ...
def test_retry_button_triages_an_untriaged_message(web_env): ...
```

- [ ] **Step 2:** Run them; expected failures.
- [ ] **Step 3:** Implement the page (counts, categories, summarized/pending, untriaged list with Retry, recent failures, last 20 events). README: `serve` row in the command table, a "Web UI" section (config `[web]`, WireGuard addresses in `hosts`, no auth: the network is the boundary, Copy falls back to text selection over plain HTTP). `config.example.toml` gets the `[web]` section with comments.
- [ ] **Step 4:** Run the suite; expected all pass. Smoke on a scratch copy of the live index (`sqlite3 -readonly data/index.sqlite ".backup <scratch>"`, a scratch config whose `data.dir` points at it): `chatmaid serve --port 8799`; load `/`, a message page with Related, `/search?q=lease`, `/status`; then stop. Do not draft against the live LM Studio in the smoke run unless the owner is present.
- [ ] **Step 5:** Commit `feat(web): status page with retry; docs for serve`.
