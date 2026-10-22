# chatmaid phase 1: headless daemon, email connector, local web UI

Status: **APPROVED 2026-10-22** (v2 plus the owner's answers recorded in
section 11). Section 12 lists the calls I made so they can be checked.
Research behind this document: `docs/research/`. Implementation plans:
`docs/superpowers/plans/`.

## 1. Intent

chatmaid is a headless daemon that watches the owner's messaging platforms
(email now, WeChat once its history is extracted, Discord later), keeps an
index of who the owner talks to and what has been said, and for each incoming
message decides whether a reply is needed and drafts one in the owner's own
style. A local model handles everything that touches private content. Cloud
models are used per request for refinement and heavier reasoning and only see
pseudonymized material the owner has agreed to send (Pattern A in
`hybrid-llm-architecture.md`). A small local web UI, reachable from the
owner's phone through their private proxy, shows drafts and lets the owner
approve, edit or dismiss them.

Phase 1 delivers the daemon's two core routines, the email connector as the
first data source, and the web UI. It does not send anything anywhere.

## 2. Owner's decisions so far (theirs, verbatim in spirit)

- The MacBook is not reachable now; WeChat extraction waits. Nothing in phase 1
  may block on it.
- WeChat history is 20.7 GB over about ten years. It may reside fully on
  `/mnt/Apps`, and the owner keeps their own backup of it. Only the last one to
  two years need to sit next to the running daemon; older history is reached
  only on demand. Full history serves two purposes: learning and summarizing
  the owner's talk style, and a persistent memory that stays coherent across
  chat sessions. Reply generation uses recent context only.
- Email accounts in scope: b5a7v1, jiaqi.yu1, jxy618.case, yjq17, yu17.fishy.
  The owner uses "jiaqi" as the main alias on b5a7v1 and "fishy" occasionally;
  one account may carry several identities.
- Python 3.14 with uv.
- All data lives in one folder inside this project on `/mnt/Apps`, laid out
  for easy backup. No duplication: no copy of the mail. No encryption for now.
- Cloud models through the `claude` and `codex` command-line tools; local
  models through LM Studio, with the owner's `ccx` launcher as a reference.
  Pattern A (local core, pseudonymized consent-gated cloud refine) is accepted.
- Start with the daemon's initialization and reply-draft flow, then the email
  connector, then a simple local web frontend.

## 3. How the owner's suggestions were evaluated

Adopted as stated: the daemon with two routines; Pattern A; LM Studio as the
local server; a web UI loadable from the phone; no full-history database.

Adopted with a precise shape. "No database of full history" becomes a
three-tier layout (section 5): the **archive** is the raw sources, kept once
and never copied into chatmaid; the **index** is small and derived; the **hot
window** holds only references and derived fields for recent messages, and
message text is read from the source on demand. Nothing in the mail cache is
duplicated. The WeChat archive is the one decrypted copy chatmaid produces
later, which is also what the owner backs up.

Adopted with an adjustment to order. The daemon's initialization has nothing
to digest without a connector, and email is ready today while WeChat is not.
So the email connector is built alongside the daemon core as its first source,
and the reply-draft flow and the UI follow. The owner's order is kept in
spirit: daemon first, UI last.

Adopted with caveats. `claude -p` and `codex exec` work as cloud backends and
use the owner's existing subscriptions, so no API keys are needed. Both must be
run so they cannot touch local files: `claude -p --tools "" --no-session-persistence --setting-sources "" --disable-slash-commands --strict-mcp-config --output-format json --json-schema <schema> [--model <m>] <prompt>` and
`codex exec --ephemeral --ignore-user-config --ignore-rules --disable shell_tool --disable unified_exec --disable code_mode_host --disable apps --disable multi_agent --disable plugins --disable browser_use --disable computer_use -s read-only -C <empty dir> --skip-git-repo-check --color never --output-schema <file> -o <file> [-m <m>] <prompt>`
(`-s read-only` alone still lets the model's shell read the whole filesystem, so
every codex feature that runs or reaches anything is disabled; the sandbox stays
as defence in depth). Both add a few
seconds of process start-up per call, which is fine for on-demand refinement
and not for the per-message hot path. Both sit behind one provider interface so
API-key backends can be added later. Note that in codex, `-p` selects a
profile; the non-interactive mode is `codex exec`.

`ccx` is the owner's launcher for Claude Code on alternate backends, including
LM Studio on port 40090 with the Anthropic Messages API. Two things carry over:
the LM Studio facts it documents (authenticated server, just-in-time model
loading with a cold start of about a minute, prompt-cache stability), and the
option of running Claude Code's agent loop on a local model for heavy offline
jobs such as the initial digest. The daemon's per-message path calls LM Studio
directly for latency; `ccx lmstudio -p` is an optional runner for batch
analyses, not a dependency.

Not adopted. gpt-oss-120b as a default: at 63 GB it only runs with heavy CPU
offload. The existing nomic embedding model as the only embedder: it is
English-centric and the corpus is largely Chinese (section 7).

## 4. Goals, non-goals, anti-goals

Goals

- A daemon with an initialization routine (digest the sources into the index)
  and two live routines: handle an incoming message, and record a message or
  thread the owner initiated.
- Email connector reading Evolution's cache read-only for the five accounts,
  with new-mail events from Evolution's D-Bus signal.
- Drafts for incoming mail that needs a reply, in the owner's style, from a
  local model, with an optional pseudonymized cloud refine the owner triggers.
- A local web UI listing pending drafts and conversations, usable from a phone.
- Everything resumable and idempotent so initialization can be re-run.

Non-goals (phase 1)

- Sending, saving drafts into Evolution, or any write to a source.
- WeChat import (ready to slot in once the archive exists; the one-shot plan is
  in `wechat-one-shot-extraction.md`).
- Discord.
- Automatic cloud calls. Every cloud call is owner-triggered in phase 1.

Anti-goals

- Writing to WeChat data, Evolution data, or mail servers before the owner has
  approved that specific test.
- Copying mail bodies or the Evolution cache into chatmaid's data.
- Sending raw message text to a cloud model, or letting a cloud CLI read local
  files.
- Chat content, keys or tokens in the repository.

## 5. Data layout and the three tiers

One folder, `data/` at the project root, gitignored, backed up by copying it.

```
data/
  config.toml              accounts in scope, owner identities and aliases,
                           policies, LM Studio endpoint; token in a separate
                           file with mode 600
  index.sqlite             the index + hot window + drafts + audit log
                           (sqlite-vec for embeddings)
  archive/wechat/<acct>/   the one decrypted copy of WeChat DBs and decoded
                           media, produced later by the one-shot import
  logs/
```

Tier 1, archive (cold, large): the raw sources. WeChat: `data/archive/wechat`
(about 20 GB, the owner's backup target). Email: Evolution's own cache under
`~/.cache/evolution/mail/<uid>/`, read in place and never copied.

Tier 2, index (warm, small): derived data only.

- `identity` and `contact`: one contact may own several identities (email
  addresses, later WeChat ids). The owner is a contact with all five account
  addresses and the aliases "jiaqi" and "fishy". Merging is by rule plus owner
  confirmation in the UI.
- `conversation`: per thread or chat: participants, platform refs, a running
  summary, a timeline of notable events, tier (family, work, finance, casual,
  bulk), and whether it is tracked for replies.
- `memory`: facts and events extracted from history with provenance (source,
  message ref, date), consolidated over time. This is the persistent memory
  that keeps sessions coherent.
- `style_profile`: the owner's writing style per language and per
  conversation tier, built from the owner's own messages, refreshed on a
  schedule, with example snippets referenced, not copied.
- `embedding`: vectors over conversation summaries, memories, and hot-window
  messages, for retrieval.
- `draft`: generated drafts, their context snapshot, the owner's action
  (approved, edited, dismissed, ignored), and the edited text when given.
- `cloud_call`: audit log of every cloud request: backend, pseudonymized
  payload, consent decision, response hash, time.

Tier 3, hot window: `message` rows hold, for every message seen, only the
sender, time, kind, subject, a short preview (at most 200 characters),
derived labels, and a source reference; the full text is read from the
source on demand through the connector, so nothing is duplicated. The hot
window is the date-bounded view of those rows (owner: one year) that the
drafting path uses for context; older rows stay only so that the digest is
resumable and the history lookup tool can find them.

History lookup on demand: a `search_history(query, contact, date range)` tool
that queries the archive directly (Evolution `folders.db` plus body files;
WeChat tables and FTS later) and returns snippets with references. The model
calls it only when the context header is not enough.

## 6. The daemon

Single process, `chatmaid` CLI with subcommands, asyncio, running as the
owner's user. Phase 1 runs it in the foreground; a systemd user unit comes
later.

Routine A, `init` (bootstrap, resumable, batch): for each connector, walk the
source from oldest to newest in chunks; create identities and conversations;
summarize each conversation in passes (chunk summaries, then a rolling
summary); extract memory facts with provenance; fill the hot window; build
the style profile from the owner's own messages; compute embeddings. Progress
is checkpointed per conversation so interruption and re-runs are safe. All
model calls go to LM Studio. Expect this to run for hours on the mail
corpus; it is a background job with a progress view in the UI.

Routine B, `on_incoming(message)`:

1. Triage without a model where possible: bulk headers (List-Unsubscribe,
   Precedence), known-bulk senders, the conversation's tier. Then a small
   local model classifies into needs-reply, informational, bulk/ad, system.
   Everything is recorded; only needs-reply continues.
2. Build the context header: contact card and relationship summary,
   conversation summary and recent hot-window messages, relevant memories
   (vector search), style profile for the language and tier, and the message
   itself read from the source.
3. Draft with the local reply model: one to three candidates, with a one-line
   rationale each. The model may call `search_history` if the header is thin.
4. Store the draft, notify the UI. If the owner asks for a cloud refine, run
   the Pattern A hop: pseudonymize (names, addresses, phone numbers, amounts if
   flagged) with a reversible map, show the exact payload, send on consent,
   re-hydrate, store both versions and the audit row.

Routine C, `on_owner_message(message)`: fired when the owner sends or starts a
thread (email: Sent folders of the five accounts; WeChat later: own messages).
Update the conversation summary and tier, extract memories, add a style
sample, and if a draft existed for that thread, record what the owner actually
sent as the accept/edit/reject signal.

Routine D, `watch`: subscribes to Evolution's `Newmail` D-Bus signal, with an
inotify fallback on `folders.db`, and polls Sent folders for routine C;
debounces, resolves the message by (account, folder, uid), and dispatches B or
C. Maintenance jobs: nightly hot-window rollover, memory consolidation, style
refresh.

## 7. Models and the cloud hop

Local, through LM Studio at `127.0.0.1:40090` (authenticated; the owner
provides the token). Models already downloaded: Qwen3.5-35B-A3B and 9B,
Gemma 4 26B-A4B, GLM-4.7-Flash 30B-A3B, GLM-4.6V-Flash 9B, Qwen3-4B,
gpt-oss-120b. Roles:

- Triage, PII tagging, chunk summaries: Qwen3-4B (fast, always loadable).
- Hot path (reply drafting, rolling summaries): Qwen3.5-35B-A3B at the
  6-bit quantization already on disk (29 GB; attention on the GPU, some
  experts on the CPU). Gemma 4 26B-A4B and GLM-4.7-Flash are compared on a
  private eval set built from the owner's own threads.
- Heavy path (initial digest, style profile, deep summaries), the "most
  capable model that fits with CPU layers": Qwen3.5-122B-A10B, unsloth
  UD-Q4_K_XL, 77 GB (verified from the Hugging Face file listing), loaded
  with LM Studio's MoE expert-offload-to-CPU setting; 10B active parameters
  keep it usable with experts in system RAM. The 6-bit build of this model
  would be over 100 GB and does not fit beside everything else in 124 GB of
  RAM, so 4-bit dynamic is the sensible quantization here. For models of 35B
  and below, 6-bit stays the rule. The already-downloaded gpt-oss-120b is
  compared in the same eval but is English-leaning.
- Embeddings: a multilingual model is needed for Chinese; the owner downloads
  Qwen3-Embedding-0.6B (GGUF, Q8_0, about 0.6 GB; 1024 dimensions) in LM
  Studio, or bge-m3 (about 1.2 GB, 1024 dimensions) if the first is not
  offered. The present nomic model stays for English-only experiments.
- LM Studio loads models just in time with a cold start of about a minute and
  an idle unload; the daemon tolerates that and can pin models with `lms load`.
  The server requires an API token; it is read from `LMStudio_token.txt` at
  the project root (gitignored, owner-provided).

Cloud, owner-triggered only, through a provider interface with two initial
backends: `codex exec --ephemeral --ignore-user-config --ignore-rules --disable shell_tool
--disable unified_exec --disable code_mode_host --disable apps --disable multi_agent
--disable plugins --disable browser_use --disable computer_use -s read-only -C <empty dir>
--skip-git-repo-check --color never --output-schema <file> -o <file> [-m <m>]` (the default
cloud writer) and `claude -p --tools ""
--no-session-persistence --setting-sources "" --disable-slash-commands
--strict-mcp-config --output-format json --json-schema <schema> [--model <m>]`
(`--bare` is not used: it restricts auth to `ANTHROPIC_API_KEY`, and the owner
logs in through OAuth), selectable per request in the UI. Both
see only the pseudonymized payload. API-key backends can be added later
behind the same interface.

## 8. Email connector

Read-only reader over Evolution's cache, verified on the workstation on
2026-10-21 (details in `evolution-email-access.md`):

- Source of truth per account: `~/.cache/evolution/mail/<uid>/folders.db`
  (tables `folders` and `messages_<folder_id>`) and raw RFC822 bodies under
  `folders/<Name>/cur/<hh>/<uid>`. Opened with `file:...?mode=ro`, short
  autocommit SELECTs, a busy timeout, never `immutable=1`, never a long
  transaction, never read-write.
- Coverage today is complete: every indexed message in all five accounts has a
  cached body (counts checked per account). Three IMAP accounts have offline
  download on with no age limit; the Microsoft 365 and Exchange accounts do
  not, so their cached bodies could expire later. Enabling the setting on
  those two in Evolution is an owner action and is recommended, not required.
- Identity: the five accounts' addresses and the aliases map to the owner
  contact; a message is "owner-initiated" if its From matches any of them or
  it sits in a Sent folder.
- Threads become conversations via References and In-Reply-To with a subject
  fallback. Bulk mail is tagged from headers at import time.
- The local Maildir Sent folder (307 messages) is included through Python's
  `mailbox` module since it is all the local store contains.
- Events: `Newmail` signal on `org.gnome.evolution.mail.dbus.Signal`, which
  by default fires for Inbox folders only; inotify fallback; Sent-folder
  polling for routine C.
- Later, draft hand-off: `evolution 'mailto:...'` with In-Reply-To and
  References opens a prefilled compose window in the running Evolution; the
  owner sends. Out of phase 1.

## 9. Web UI

A small FastAPI app bound to localhost, server-rendered pages with minimal
JavaScript, mobile-first layout, exposed to the phone only through the owner's
private proxy (no auth of its own in phase 1; the proxy is the boundary).
Views: pending drafts, conversation view with summary and recent messages,
contact and identity merge, init progress, cloud audit log.

A draft defaults to the local model and offers two actions besides approve,
edit and dismiss:

- **Feedback and regenerate (local):** a comment box; the comment and the
  previous draft go back to the local model, which produces a new version.
  Every version is kept with its comment.
- **Cloud improvement:** starts the Pattern A flow: build the pseudonymized
  payload (previous draft, the owner's comment if any, redacted excerpt or
  summary), show it exactly, let the owner pick the backend (codex by
  default, claude or others as configured), send on consent, re-hydrate, and
  show the result as a new version. The cloud version also takes feedback:
  a comment sends a revise request through the same payload-preview-and-
  consent step, never silently.

Approve in phase 1 means "record approval"; nothing is sent.

## 10. Build order

1. Core: package skeleton, config, index schema, connector interface, LM
   Studio client, history lookup tool, `chatmaid init` over email.
2. Email connector: reader, Sent detection, Newmail subscription, `watch`.
3. Reply-draft flow: triage, context header, drafting, draft store, cloud
   provider interface with `claude` and `codex` backends and the pseudonymizer.
4. Web UI.
5. WeChat importer, when the archive exists.

## 11. Owner's decisions (2026-10-22)

1. **Hot window:** one year.
2. **LM Studio access:** token saved by the owner in `LMStudio_token.txt`
   at the project root.
3. **Embedding model:** the owner downloads what section 7 names, in LM
   Studio.
4. **Local model:** the most capable that fits with some CPU layers, at a
   reasonable quantization (section 7: Qwen3.5-122B-A10B UD-Q4_K_XL for the
   heavy path, Qwen3.5-35B-A3B 6-bit for the hot path).
5. **Cloud default:** codex, with other backends selectable in the UI. The
   consent rule is always show the payload and ask.
6. **Draft actions:** local by default; feedback box with local regenerate;
   cloud improvement with payload preview and consent, which also accepts
   feedback and revises (section 9).
7. **Evolution offline download** on yjq17 and jiaqi.yu1 remains optional.
8. **Design approved.** Write-capable tests still get their own go/no-go.

## 12. Calls I made (check these)

- Three-tier layout with no copied mail: the index holds references and
  derived fields; text is read from the source on demand.
- Email built together with the daemon core as its first source; UI last.
- Cloud CLIs run tool-less and stateless and sit behind a provider interface.
- `ccx lmstudio` kept as an optional batch runner, not part of the hot path.
- Qwen3.5-35B-A3B as the default drafting model, Qwen3-4B for triage, pending
  a private eval.
- Initialization is a resumable pipeline with checkpoints, not an agent loop.
- Triage uses header rules before any model call.
- Sent folders define "owner-initiated" for email.
- FastAPI, server-rendered, localhost-only, proxy as the security boundary.
- Foreground process in phase 1, systemd user unit later.

## 13. Left to the builder

- Exact DDL, migration tooling, chunking and summarization prompts.
- Pseudonymizer implementation (Presidio with a Chinese model plus a
  contact-book deny-list and a small-model second pass) and the placeholder
  format.
- Eval set construction from the owner's threads and the model comparison.
- UI framework details and styling.
