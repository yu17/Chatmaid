# chatmaid

A personal chat assistant that watches the owner's main messaging platforms
(WeChat, Discord, email), reads conversation history, and suggests the owner's
next message. It runs a local LLM for anything that touches private chat
content and calls online LLMs only for revision, refinement, or heavier
analysis on request. The user-facing side will be either a unified remote
control interface or an overlay/helper window on top of the chat apps.

## Status

Phase 1a is implemented: a local store and an email connector that read
Evolution's mail cache, build a searchable index of conversations, summarize
them with a local LLM, and route newly arrived mail as events.

Phase 1b adds reply drafting: incoming mail is triaged by a local model, and
conversations that need a reply get candidate drafts written in the owner's
style. A draft can be revised from a comment, and improved by an online model
only with the owner's explicit consent per call, on a pseudonymized payload.
Everything is driven from the CLI. Nothing is sent or written to a mail server.
A UI comes later; research notes are in [`docs/research/`](docs/research/README.md).

## Guiding constraints

- WeChat is the most important platform and the most hostile to third-party
  tooling. The primary login lives on an unrooted Android phone, so all access
  goes through secondary logins (desktop, iPad) or on-phone notification
  capture.
- Chat logs are sensitive. Raw content stays on the local machine; cloud models
  see redacted, summarized, or explicitly approved material only.

## Layout

```
src/chatmaid/
  cli.py config.py model.py identity.py threads.py lookup.py
  daemon.py             watch loop
  lock.py               single-instance lock for the watcher and init
  connectors/           base.py; evolution/ (sources, cache, rfc822, connector, events)
  store/                db.py, schema_v1.sql, schema_v2.sql
  llm/                  base.py, lmstudio.py, fake.py, prompts/
  init/                 pipeline.py (ingest, digest), passes.py (style, embed), chunking.py
  triage/rules.py
  reply/                context.py, drafting.py, flow.py (triage, draft, revise)
  cloud/                base.py, codex.py, claude.py, pseudonym.py, registry.py, fake.py
tests/                  pytest suite (fixture Evolution caches and a fake LLM)
docs/                   research notes that inform the design
data/                   index and config (gitignored)
config.example.toml     template for data/config.toml
```

## Setup

```
uv sync --group dev
mkdir -p data && cp config.example.toml data/config.toml
```

Edit `data/config.toml` (owner name and aliases, Evolution account uids).
Put the LM Studio API token in the file named by `lmstudio.token_file`
(default `LMStudio_token.txt` at the project root; gitignored); `init --pass ingest`
runs without it. LM Studio must be running on `127.0.0.1:40090` with "Require Authentication" enabled.

## Running

Run commands as `uv run chatmaid [--config PATH] <command>` (default config is
`data/config.toml`; `--version` prints the version).

| Command | Flags | Purpose |
| --- | --- | --- |
| `accounts` | | list configured Evolution mail accounts |
| `scan` | `--account UID`, `--dry-run` | count messages per account and folder |
| `llm-check` | | list LM Studio models and run one completion |
| `init` | `--since YYYY-MM-DD`, `--limit N`, `--pass {ingest,digest,style,embed,all}`, `--fake-llm`, `--reset-checkpoints` | build the index (resumable) |
| `status` | | print index counts |
| `conversations` | `--limit N`, `--all` | list indexed conversations (`--all` includes bulk) |
| `show ID` | `--limit N` | show one conversation |
| `lookup QUERY` | `--contact`, `--since`, `--until`, `--limit N`, `--no-vector` | search message history |
| `watch` | `--interval SECONDS`, `--no-dbus`, `--no-draft` | route newly arrived mail until interrupted; triage and draft replies unless `--no-draft` |
| `drafts` | `--status {pending,all}`, `--limit N` | list reply drafts with the text `draft approve` would approve (candidate 1 of the latest round) |
| `draft show ID` | | show a draft with its versions and cloud calls |
| `draft run` | `--message ID`, `--fake-llm` | triage and draft one stored message (the retry for a failed or skipped draft) |
| `draft regenerate ID` | `--comment TEXT`, `--from VERSION`, `--fake-llm` | write a new round of candidates from a comment |
| `draft approve ID` | `--version N` | approve a version |
| `draft edit ID` | `--text TEXT` or `--file PATH` (one required) | replace the text with your own |
| `draft dismiss ID` | | dismiss a draft |
| `draft cloud ID` | `--backend {codex,claude}`, `--comment TEXT`, `--from VERSION`, `--fake-llm` | propose a cloud improvement and print the exact payload |
| `draft send ID` / `draft decline ID` | | send a proposed payload to the backend, or decline it |
| `cloud-calls` | `--limit N` | list cloud proposals and calls |
| `cloud-check` | `--backend {codex,claude}` | send a canned, content-free prompt through a cloud CLI |

Notes:

- Gmail and IMAP folders are tracked by integer uid. Exchange and Microsoft 365
  folders have opaque uids and are tracked by received time.
- Ingest checkpoints record the run's `--since`. `init --reset-checkpoints`
  clears them so a complete-history ingest can follow an earlier `--since` run.
- `watch` baselines every folder on first start (no backlog is routed), then
  routes new mail as `incoming` or `owner` events. Folders that appear later are
  baselined too. A message whose body is not yet cached is deferred for up to
  three triggers; locked databases are retried on the next trigger. It listens
  for Evolution's `Newmail` D-Bus signal, watches `folders.db` for changes, and
  polls every `--interval` seconds; `--no-dbus` disables the signal.
- `watch` prints one line per routed message, `<incoming|owner>  conv <conversation
  id>  msg <message id>  <sender>  <subject>`. On Ctrl-C or SIGTERM during a scan
  it finishes the message in hand (including its triage and draft), keeps the
  rest for the next start, and exits.

## Draft flow

- Triage runs on each newly routed incoming message (`watch`, or `draft run
  --message ID` by hand). Categories: `needs_reply`, `informational`, `bulk`,
  `system`, `unknown`. Only `needs_reply` produces a draft; a message whose body
  is not cached yields no draft. Mail sent more than `draft.max_age_days`
  (default 14) days ago is `informational` by rule, without a model call, in
  both `watch` and `draft run`.
- When a draft cannot be written (for example LM Studio is down), the message
  stays indexed and a `draft_failed` event records the error and the message id;
  `draft run --message ID` retries it.
- A draft stores the generated candidate texts in full, the language, and a
  context record (contact card, summary, recent previews, memories, style
  profile). The owner's own sent text is referenced, never copied.
- One pending draft exists per conversation; a newer incoming message marks the
  old one `superseded` once the new draft is written (a failed draft leaves the
  old one pending). When a reply joins two conversations that both have a pending
  draft, the older draft is superseded. Statuses: `pending`, `approved`, `edited`,
  `dismissed`, `superseded`.
- When your own reply in the conversation shows up in a Sent folder, the pending
  draft is resolved: `approved` if what you wrote, without the quoted history
  (`On ... wrote:`, `-----Original Message-----`, `在...写道`, `>` lines) and the
  `-- ` signature, equals a candidate or starts with one; `edited` otherwise.
- `draft regenerate --comment` writes a new round from your feedback.
- `approve` records that you accept a version. It sends nothing and writes
  nothing to Evolution or a mail server; you still send the reply yourself.
  `edit` replaces the text with your own, `dismiss` drops the draft.

## Cloud backends

Cloud improvement is optional and always owner-triggered. `draft cloud ID`
stores a proposal and always prints the exact pseudonymized payload first
(names, addresses, and phone numbers become `[PERSON_n]`, `[EMAIL_n]`,
`[PHONE_n]`, URLs and bare domains `[URL_n]`; amounts too if
`cloud.redact_amounts = true`). On a terminal it then asks `Send this to
<backend>? [y/N]`: only `y` or `yes` sends, and any other answer declines the
proposal. When stdin is not a terminal it sends nothing and prints
`run: chatmaid draft send <id>  (or: chatmaid draft decline <id>)`; `draft send`
and `draft decline` then act on that open proposal. A send transmits the stored
payload byte for byte. The watcher never calls a cloud backend. The payload holds the
previous draft, your comment, the conversation summary, and at most 1,500
characters of the message being answered. The placeholder map stays local.
Review the payload yourself: free text such as signatures can still carry
details the pseudonymizer does not recognise.

A cloud call is `proposed` until you answer. Consent is recorded (`sending`,
with its time) before anything is transmitted; the call then ends `sent`,
`failed` or, if you decline, `declined`. A send interrupted by Ctrl-C or a
crash is marked `failed` with `interrupted: <reason>`, and a call that is not
`proposed` can never be sent again: propose a new one. EOF or Ctrl-C at the
consent prompt declines.

Both CLIs run stateless in an empty temporary directory. claude gets no tools
(`--tools ""`). codex runs with its shell, unified exec, code-mode host, apps,
multi-agent, plugin, browser-use and computer-use features disabled, so the
model can only answer in text, and with its read-only sandbox kept as a second
line of defence:

- codex: `codex exec --ephemeral --ignore-user-config --ignore-rules --disable shell_tool --disable unified_exec --disable code_mode_host --disable apps --disable multi_agent --disable plugins --disable browser_use --disable computer_use -s read-only -C <empty temp dir> --skip-git-repo-check --color never --output-schema <schema file> -o <out file> [-m <model>] <prompt>`
- claude: `claude -p --tools "" --no-session-persistence --setting-sources "" --disable-slash-commands --strict-mcp-config --output-format json --json-schema <schema json> [--model <m>] <prompt>`

`--bare` cannot be used with claude: it restricts auth to `ANTHROPIC_API_KEY`,
and the owner is logged in through OAuth. `cloud-check` exercises each backend
with a canned prompt that carries no mail content.

## What stays read-only

- Evolution's cache is opened `mode=ro`; nothing is written under `~/.cache` or
  `~/.config`.
- The index holds no message bodies, only 200-character previews.
- Phase 1 sends no mail. The only outbound traffic beyond LM Studio is a
  consented cloud call (see Cloud backends) and `cloud-check`'s canned,
  content-free call.

## Models

- `lmstudio.model` (`qwen3.5-122b-a10b`): the one local model for triage, chunk
  and rolling summaries, drafts and revisions, the style profile and person
  tagging. One model for every call keeps LM Studio from unloading and reloading
  between calls (its "Only Keep Last JIT Loaded Model" setting is on by
  default). Change the value to switch models.
- `lmstudio.think` (`true`): reasoning on. LM Studio returns the reasoning in a
  separate field and enforces each prompt's JSON schema on the answer. With
  `false` the client prefills an empty think block, which LM Studio cannot
  combine with a schema, so the reply is validated afterwards and retried once.
  LM Studio 0.4.13 ignores the request-level reasoning switches, so the prefill
  is the only off switch.
- `lmstudio.temperature` (`0.6`, Qwen's thinking-mode value): sent with every
  call, overriding the model's LM Studio preset.
- No `max_tokens` is sent: a local call runs to the model's natural end,
  reasoning included.
- `model_embedding` (`text-embedding-qwen3-embedding-0.6b`): 1024 dimensions.

## Known limitations

- A message written to the cache after a folder's baseline with a received time
  at or below the boundary is not routed live by `watch`; a later `init` still
  indexes it.
- An index built with `--since` before checkpoints were recorded needs one
  `init --reset-checkpoints` before a complete-history ingest.
- There is no automatic queue of untriaged messages yet (planned for phase 1c).
  A message whose draft failed, or that was routed just before `watch` was
  killed hard, is indexed but not drafted until you run `draft run --message
  ID` (the id is in the `watch` line and the `draft_failed` event).
- `watch` stops between messages, not inside a model call: a stop request waits
  for the triage and draft of the message in hand.
