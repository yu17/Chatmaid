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
| `drafts` | `--status {pending,all}`, `--limit N` | list reply drafts |
| `draft show ID` | | show a draft with its versions and cloud calls |
| `draft run` | `--message ID`, `--fake-llm` | triage and draft one stored message |
| `draft regenerate ID` | `--comment TEXT`, `--from VERSION` | write a new round of candidates from a comment |
| `draft approve ID` / `draft edit ID` / `draft dismiss ID` | | approve a version, replace the text with your own, or dismiss |
| `draft cloud ID` | `--backend {codex,claude}`, `--comment TEXT`, `--from VERSION` | propose a cloud improvement and print the exact payload |
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

## Draft flow

- Triage runs on each newly routed incoming message (`watch`, or `draft run
  --message ID` by hand). Categories: `needs_reply`, `informational`, `bulk`,
  `system`, `unknown`. Only `needs_reply` produces a draft; a message whose body
  is not cached yields no draft.
- A draft stores the generated candidate texts in full, the language, and a
  context record (contact card, summary, recent previews, memories, style
  profile). The owner's own sent text is referenced, never copied.
- One pending draft exists per conversation; a newer incoming message marks the
  old one `superseded`. Statuses: `pending`, `approved`, `edited`, `dismissed`,
  `superseded`.
- `draft regenerate --comment` writes a new round from your feedback.
- `approve` records that you accept a version. It sends nothing and writes
  nothing to Evolution or a mail server; you still send the reply yourself.
  `edit` replaces the text with your own, `dismiss` drops the draft.

## Cloud backends

Cloud improvement is optional and always owner-triggered. `draft cloud ID`
stores a proposal, prints the exact pseudonymized payload (names, addresses,
and phone numbers become `[PERSON_n]`, `[EMAIL_n]`, `[PHONE_n]`, and URLs and bare domains `[URL_n]`; amounts too if
`cloud.redact_amounts = true`), and sends nothing. Only `draft send ID`
transmits that stored payload, byte for byte, after you confirm; `draft decline
ID` discards it. The watcher never calls a cloud backend. The payload holds the
previous draft, your comment, the conversation summary, and at most 1,500
characters of the message being answered. The placeholder map stays local.
Review the payload yourself: free text such as signatures can still carry
details the pseudonymizer does not recognise.

Both CLIs run tool-less and stateless in an empty temporary directory:

- codex: `codex exec --ephemeral --ignore-user-config --ignore-rules -s read-only -C <empty temp dir> --skip-git-repo-check --color never --output-schema <schema file> -o <out file> -m <model> <prompt>`
- claude: `claude -p --tools "" --no-session-persistence --setting-sources "" --disable-slash-commands --strict-mcp-config --output-format json --json-schema <schema json> [--model <m>] <prompt>`

`--bare` cannot be used with claude: it restricts auth to `ANTHROPIC_API_KEY`,
and the owner is logged in through OAuth. `cloud-check` exercises each backend
with a canned prompt that carries no mail content.

## What stays read-only

- Evolution's cache is opened `mode=ro`; nothing is written under `~/.cache` or
  `~/.config`.
- The index holds no message bodies, only 200-character previews.
- Phase 1 sends no mail. The only outbound traffic beyond LM Studio is a
  consented cloud call (see Cloud backends).

## Models

- `model_small` (`qwen/qwen3-4b-2507`): triage and chunk summaries.
- `model_main` (`qwen/qwen3.5-35b-a3b`): rolling summaries.
- `model_heavy` (`qwen3.5-122b-a10b`): style profile. Optional; set it to the
  `model_main` value to fall back.
- `model_embedding` (`text-embedding-qwen3-embedding-0.6b`): 1024 dimensions.

## Known limitations

- A message written to the cache after a folder's baseline with a received time
  at or below the boundary is not routed live by `watch`; a later `init` still
  indexes it.
- An index built with `--since` before checkpoints were recorded needs one
  `init --reset-checkpoints` before a complete-history ingest.
