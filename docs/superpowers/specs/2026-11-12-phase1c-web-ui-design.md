# chatmaid phase 1c: local web UI and the 1b leftovers

Status: APPROVED 2026-11-12 (owner's answers in section 8). Baseline: section 9 of
`2026-10-21-phase1-design.md` (approved 2026-10-22). This document narrows and
orders that section around what the owner asked for after the full digest, and
adds the leftovers parked by plan 1b. Calls I made are marked *(my call)*.

## 1. Intent and why now

The index is complete (29.4k conversations, 28.4k summarized, 15.4k facts,
style profiles, embeddings) and `lookup`/`related` were rebuilt on 2026-11-12
(thread-level rank fusion; merged at 375574e). The CLI is now the bottleneck:
the owner reads mail on the phone and wants a screen for two moments, in their
words:

1. "when I received an email and would like to check all emails related to
   the subject (beyond that reply thread)";
2. "when I'd like to draft a reply".

Everything else in section 9 (contact merge, init progress, cloud audit) is
secondary. The owner chose to use lookup as it is and tune it later against
sample topics they are collecting.

## 2. Goals, non-goals, anti-goals

Goals

- A phone-first web page where the owner finds a recent email, reads it with
  its thread, sees every related conversation, and drafts a reply: local
  candidates, feedback-and-regenerate, cloud improvement with exact payload
  preview and consent, approve / edit / dismiss, and copy the final text.
- A search page over the whole index (the `lookup` engine).
- The 1b leftovers (section 7), so the daemon side is solid before the UI
  leans on it.

Non-goals (phase 1c)

- Sending mail, writing to Evolution or any server. Approve records approval;
  the owner copies the text into their mail client.
- Authentication: the owner's WireGuard network is the boundary (spec section 9 said proxy; the owner uses WireGuard).
- Push notifications to the phone (owner: just open the page).
- Contact and identity merge (owner: defer to after 1c).
- WeChat, systemd unit, routine D maintenance jobs.

Anti-goals: anything that makes the owner wait on a frozen page during a
model call (Flash-Next takes 30 s to 2 min per call with thinking on); any
cloud transmission without the payload shown and a separate "send" press; any
page that leaks mail content to a third party (no CDN assets, no external
fonts, no analytics).

## 3. Constraints

- Read-only toward mail sources; message text is read from the Evolution cache
  on demand (three-tier rule), never copied into the index.
- Cloud calls only through the existing consent flow (`propose_cloud` →
  show payload → `send_cloud` / `decline_cloud`), audited in `cloud_call`.
- `LMStudio_token.txt` never logged, never rendered.
- One LM Studio model is loaded at a time and serves one session; model calls
  from the UI queue behind each other and behind `watch`.
- Python 3.14 with uv; FastAPI, server-rendered, localhost-only (spec section
  12). The index is SQLite in WAL mode; `init` and `watch` hold the
  single-writer flock, draft commands do not, and neither will the UI.

## 4. How it runs *(my call)*

- New command `chatmaid serve [--port 8765]`, a separate process from `watch`;
  both open the same index. Config section `[web]`: `hosts` (a list; the
  server listens on every address, e.g. 127.0.0.1 plus the owner's WireGuard
  addresses), `port`, `allowed_hosts` (defaults to `hosts`), `base_path`
  (empty by default; kept for a future reverse proxy). The owner reaches it
  from the phone over their own WireGuard setup, so the network is the
  boundary and pages are plain HTTP.
- Dependencies: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`. No front-end
  build; one small vanilla JS file (polling a job, copy to clipboard) and one
  CSS file, served by the app.
- Model work from the UI (draft, regenerate, cloud propose, cloud send) runs on
  one background worker thread with an in-process job table; the page shows the
  job's state and polls it, and a reload shows the job still running. A job
  survives page reloads, not a server restart (the draft tables already record
  every finished result).
- POST forms carry a per-process CSRF token, and requests whose `Host` is not
  one of `web.allowed_hosts` (with or without the port) are rejected: cheap protection against another local page posting a cloud
  send. Links and redirects are built relative to `base_path`. Copy uses the
  clipboard API where the page is HTTPS or localhost and falls back to
  selecting the text otherwise.

## 5. Screens

Mobile-first, one column, readable without zoom; desktop gets the same pages
wider.

1. **Inbox** (home). Recent incoming mail across the five accounts, newest
   first, from the index (hot window). Each row: sender, subject, date,
   account, conversation category, triage result, and a marker when a draft is
   pending. Filters: needs reply / pending drafts / all / one category. A search
   box at the top goes to Search. *(my call: the owner arrives from their mail
   client, so the entry point mirrors an inbox rather than a draft queue.)*
2. **Message**. The email read from the source (quoted history and signature
   collapsed, attachments listed), then its thread (other messages collapsed
   to previews), the conversation summary and category, and two panels:
   - **Related**: `related(conversation_id)` results, each with title, date,
     category, message count and the matching snippet or fact, linking to that
     conversation. Works for mail the digest has not reached (embedded from
     subject and previews).
   - **Reply**: the pending draft if any; otherwise a "Draft a reply" button
     that drafts for this message regardless of the triage result.
3. **Draft** (may be the Reply panel expanded). Candidates of the latest round
   with rationales; earlier rounds and cloud versions collapsed with their
   comments. Actions: feedback box → regenerate locally; "Improve with cloud"
   → choose backend (codex default) and optional comment → the exact payload
   is shown → Send or Decline; Approve (per version), Edit (textarea), Dismiss;
   Copy text. Cloud versions take feedback through the same preview-and-
   consent step.
4. **Conversation**. Any conversation by id (reached from Related or Search):
   summary, category, participants, messages newest first with previews and a
   link to read each one in full.
5. **Search**. Query box with category, contact and date filters; results are
   threads as `lookup` prints them.
6. **Status**. Index counts, digest progress per category and queue group,
   last `watch` activity, recent errors and `draft_failed` events with a retry
   button. Read-only except retry.
7. **Cloud audit**. Every cloud call with status, backend, consent time and
   the payload as sent.

## 6. Daemon-side changes the UI needs

- `ReplyFlow.draft_for_message(message_id)`: draft without triage (the owner
  asked for it), superseding any pending draft of that conversation as today.
- A read model for the Inbox: incoming messages joined with triage, pending
  draft and conversation category; paged by `sent_at`.
- Fetching a full message for display goes through the connector; a message
  gone from the source shows its stored preview and a note.

## 7. The 1b leftovers

1. **Split `cli.py` (709 lines) and `reply/flow.py` (412 lines).** `cli/` becomes
   a package (index/read commands, init, watch, drafts, cloud); `flow.py`
   splits into routine B (triage + draft), routine C (owner mail + summary),
   and draft actions/cloud. Behaviour unchanged; done first so the UI imports
   the same functions the CLI uses.
2. **Untriaged-message retry queue (R18 follow-up).** Only messages `watch`
   itself routed are candidates: those with a `reply`/`incoming` event (or a
   `draft_failed` event) and no triage row, received within
   `draft.max_age_days`, in a conversation where the owner has not written
   since. Messages `init` indexed are never swept *(my call)*. `watch` retries
   them at start and after each trigger, newest first, at most three attempts
   each (counted in the event log); the Status page lists what is left and
   offers retry.
3. **Stop-time pending-counter loss.** When `watch` stops mid-scan, the
   body-wait attempt counters of the refs not yet reached are dropped; carry
   them over.
4. **`drafts` / `draft show` without a `ConfigError` handler** when opening
   the connector: print the error and exit 2 like the other commands.
5. **`PHONE_RE` across newlines.** The separator class includes `\s`, so digits
   on consecutive lines can join into one "phone number"; allow only spaces,
   tabs, dots, dashes, slashes and parentheses inside a number.
6. **Unbounded prefix match in approval detection (R16).** A sent mail that
   merely starts with the draft text counts as "approved" however much the
   owner added; count it as approved only when the addition is short (≤ 20% of
   the draft or ≤ 200 characters, whichever is larger), otherwise "edited".
   Approval in the UI stays what it is today (the draft leaves `pending` with
   the chosen text), but routine C also matches the owner's sent mail against
   the newest resolved draft of the conversation that has no `outcome_ref` yet
   and records `outcome_ref`/`outcome_preview` there without changing its
   status, so the sent-mail signal is kept when the owner approved in the UI
   first *(my call; no schema change: the columns exist)*.
7. **Test gaps.** Cover each item above, plus the UI routes with FastAPI's
   test client against the existing fake connector and fake LLM.

## 8. Owner's answers (2026-11-12)

1. Contact and identity merge: defer to after 1c.
2. Notifications: none; the owner opens the page.
3. Access: a custom WireGuard setup; the UI binds to 127.0.0.1 and a few more
   addresses. No reverse proxy, no TLS in front of it.
4. Design approved; execute subagent-driven, 1b leftovers first.

## 9. Left to the builder

Page layout and styling, template structure, the exact Inbox query and paging,
job-table shape, how collapsed quoted history is rendered, test fixtures, the
order of tasks after the cli/flow split.
