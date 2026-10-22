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
them with a local LLM, and route newly arrived mail as events. Replying and a
UI come later; research notes are in [`docs/research/`](docs/research/README.md).

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
  connectors/           base.py; evolution/ (sources, cache, rfc822, connector, events)
  store/                db.py, schema_v1.sql
  llm/                  base.py, lmstudio.py, fake.py, prompts/
  init/                 pipeline.py (ingest, digest), passes.py (style, embed), chunking.py
  triage/rules.py
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
| `watch` | `--interval SECONDS`, `--no-dbus` | route newly arrived mail until interrupted |

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

## What stays read-only

- Evolution's cache is opened `mode=ro`; nothing is written under `~/.cache` or
  `~/.config`.
- The index holds no message bodies, only 200-character previews.
- Phase 1 sends nothing.

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
