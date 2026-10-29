# Digest scope, categories, style samples and owner-reply examples: design

Date: 2026-10-29. Status: draft for the owner's approval. Builds on the phase 1 design
(`2026-10-21-phase1-design.md`, sections 5 and 6) and on plans 1a and 1b, which are merged.

## 1. Intent

The owner reviews their full mail history for two reasons, in their words:

1. Learn their speech style, which mainly comes from the Sent boxes.
2. Find threads in the inbox, the way a mail client groups replies into one pack, and index
   those threads for future reply references and for queries about a topic ("all chats about
   some subject").

The current `init` pipeline serves both only partly. Measured on the rebuilt index of
2026-10-28 (34,525 messages, 29,054 conversations, five accounts):

- Nine of ten characters in the owner's sent mail are quoted history. 54 of the 60 newest sent
  messages carry a quoted block (mean body 3,207 characters, about 2,900 of them quoted). The
  style pass feeds those bodies in unstripped, so the profile is learned mostly from other
  people's words. The digest chunks repeat every earlier message once per reply.
- The drafter sees only a description of the owner's style, never the owner's actual past
  replies.
- The digest skips the 15,196 conversations the header rule marks bulk, and that rule splits
  "automated versus human-sent", not "worth keeping versus worthless". Seminar announcements,
  HotCRP decisions, Slack mentions, Codeforces round announcements and club events sit on the
  bulk side; college-admissions marketing, vendor newsletters and LinkedIn notices sit on the
  non-bulk side. The owner's personal accounts (yjq17, yu17.fishy) are mostly promotional
  with legitimate events blended in; the university accounts are the reverse.
- The digest extracts "events" and "facts" from everything, including adverts: a Costco mail
  produced two events and two facts.
- `init` prints nothing until a pass ends, and the digest will run for days.

The owner's decision (2026-10-28): summarize every conversation, bulk included, and let the
model judge, classify and sort, because no cheap signal does it reliably. Evidence checked:
read status is useless (the owner reads everything; unread is 0 to 1 percent in every inbox);
answered, flagged and calendar-invite marks cover a few percent; sender identity is a strong
prior (933 senders carry 88 percent of incoming mail) but the senders that matter are the
mixed ones (Codeforces, campusgroups, the Northwestern news address). A long run is
acceptable provided there is a progress bar and the run can be stopped at any time and
resumed without losing more than the conversation in flight.

## 2. Goals

- The style profile is built from the owner's own words only.
- The reply drafter sees a few of the owner's real past replies that resemble the situation.
- Every conversation gets a summary and a category; events and facts are extracted only
  where they mean something.
- Useful summaries (real threads, known contacts) exist within the first day of the run; the
  promotional tail is summarized last and cheaply.
- The digest shows progress, stops cleanly on Ctrl-C or SIGTERM, and resumes where it left
  off.
- Topic queries can be filtered by category and can return threads rather than messages.
- The live triage stops asking the model about senders the digest has already found to be
  purely promotional.

## 3. Non-goals and anti-goals

Non-goals: the web UI (plan 1c), WeChat, full-text search over bodies (that would copy mail
into the index, which the phase 1 design rules out), a second model for the tail (the owner
chose one model and a long run), memory consolidation, per-tier style profiles, changes to
the cloud path.

Anti-goals, things that would make this a failure even if it "works":

- Any write to Evolution, the mail servers or the cache. Everything stays read-only.
- Message bodies copied into the index. Previews stay at 200 characters; owner examples
  shown to the drafter are re-read from the source and stored in the draft's context snapshot
  as previews only (ruling R7).
- A stop that leaves the store inconsistent or re-does more than the conversation in flight.
- Promotional mail excluded from the digest by a rule. Filtering is by stored category at
  query time, never by skipping.
- Facts or events invented from adverts, receipts or newsletters.

## 4. Constraints

- One configurable local model (`lmstudio.model`), thinking on, no sampling parameters and no
  token caps sent by chatmaid (see `chatmaid-lmstudio-thinking` decisions). Thinking stays on
  for every digest call: on a short mail it costs about 6 seconds of a 25-second call, and the
  owner prefers quality.
- Measured cost on Flash-Next: about 72 seconds per call on real thread chunks (12 to 117 s),
  19 to 26 seconds on a short single mail with the current 120-word summary. With a short
  summary for junk categories, the whole corpus is on the order of six days of continuous
  running: about 1,350 real threads at one to two calls each, about 12,000 single non-bulk
  mails at about 20 seconds, about 15,000 bulk-flagged mails at a few seconds each.
- Single writer: `init` and `watch` never run together (`chatmaid.lock`). Each `init` pass
  is resumable on its own, so the owner may stop the digest, run `embed`, and resume.
- No new runtime dependency for the progress display.
- Schema changes go through the migration ladder (`schema_v3.sql`, ruling R20). The live
  index will be rebuilt from scratch anyway (the yjq17 rows ingested bodiless), so no data
  migration of existing summaries is needed, but the ladder must still apply cleanly to an
  index that already has summaries.
- Tests run with `uv run pytest -q -W error`; TDD per task; real-model smoke tests only
  against a scratch index, never `data/index.sqlite`.

## 5. The design, as decided

### 5.1 Quoted history is stripped once, in one place

`strip_reply_chrome` (today in `reply/flow.py`, ruling R16) moves to a shared module and is
used by the style pass, the digest renderer, the owner-example embedding and the drafter's
example display. It keeps the behaviour it has: cut at the first attribution line (English or
Chinese), the first `>` line, or a `-- ` signature separator.

Digest rendering keeps the first message of a conversation whole and strips every later one.
"First" means first by time in the conversation, not first in the list being rendered:
routine C (`ReplyFlow.update_summary`), which renders the owner's single new message into
an existing summary, strips it, because that conversation already has a first message.
Otherwise every live owner reply would re-summarize the quoted history and re-extract facts
the digest already stored. The first message may itself be a forward or a reply whose
original is not in the index, and its quoted part is then the only copy of that context.
(My calls: interleaved replies lose the owner's text after the first quoted line; the full
body stays reachable through lookup.)

### 5.2 The digest covers every conversation and judges each one

`InitOptions.skip_bulk` defaults to `False`; `init --skip-bulk` keeps the old behaviour. The
header rule still marks messages and conversations `bulk`; that flag becomes one signal
among others, not an exclusion.

The `chunk_summary` prompt returns one more field, `category`, one of:

| Category | Meaning |
| --- | --- |
| `conversation` | A person wrote to the owner or a group and an exchange is expected or under way. |
| `event` | Announces or invites to a dated happening: seminar, contest round, meeting, deadline, call for applications. |
| `transactional` | Receipts, invoices, confirmations, shipping, account and security notices about the owner. |
| `newsletter` | Periodic editorial content from an organisation: news digests, weekly bulletins. |
| `promotional` | Marketing, offers, advertising, recruitment and admissions marketing. |
| `system` | Automated machinery: bounces, calendar sync, ticket updates, CI, password resets. |

Output rules in the prompt: summary at most 120 words for `conversation` and `event`, at
most 25 words for the rest; `events` only for `conversation` and `event`; `facts` only for
`conversation`, and only facts about people the owner deals with (roles, relationships,
preferences, commitments), never about organisations or products. The code drops events and
facts that arrive for other categories regardless of what the model returns.

The length rule cannot be enforced by the schema (it depends on `category`) and is not worth
a retry: an over-long summary is accepted as is, counted in the pass stats as `long`, and
logged at debug level with the conversation id. Nothing is truncated and nothing is retried
or left pending for it. For the count, a word is a whitespace-separated token for Latin text
and, for Chinese, 2.5 CJK characters stand for one word (so 25 words is about 60 characters).
(My call: a retry would roughly double the cost of the promotional tail for a cosmetic
violation.)

The prompt also receives one line of sender context from the pre-pass (5.3), for example:
"Sender: 237 messages since 2019, the owner never wrote to them, bulk headers present." That
line is what makes the judgment reliable on the ambiguous middle.

A conversation's category is stored on the `conversation` row. Over several chunks the most
engaged category wins, in the order `conversation` > `event` > `transactional` >
`newsletter` > `promotional` > `system`. When the owner sends a message in a conversation
(routine C in `reply/flow.py`, which already reuses `chunk_summary`), the category becomes
`conversation`.

### 5.3 A pre-pass with no model computes sender statistics and the queue order

Before summarizing, the digest fills a `sender_stat` table from the index alone, per sender
identity: incoming message count, conversation count, first and last seen, whether the owner
has ever written to that contact (participant of a conversation holding an owner message;
large CC lists make this generous, accepted), whether the owner replied in any conversation
with them, the share of their messages carrying bulk headers, and, after the digest, the
count of their conversations per category. It is recomputed at the start of every digest run
and is cheap (one pass over the message table).

"The sender" of a conversation, for the context line and for queue placement, is the earliest
non-owner sender in it. A conversation with no non-owner sender (the owner's outgoing mail
with no reply in the index, a few hundred to low thousands) gets the fixed line "Sender: the
owner; outgoing mail with no reply indexed" and sits in group 3 below. For the per-category
counts in `sender_stat`, a conversation counts once for every non-owner sender in it. (My
calls.)

The queue order, newest first within each group:

1. Conversations with two or more messages and an owner message (real exchanges, 863).
2. Other conversations with two or more messages (about 500).
3. Single messages from senders the owner has written to, and the owner's own outgoing
   singles.
4. Single messages from senders with fewer than five messages in total.
5. Everything else, lowest-volume senders first, so the highest-volume unanswered senders
   (Costco, Codeforces digests, Canvas) come last.

Groups 1 and 2 finish within the first day at the measured rates; the owner can stop there
and already use the index for its two purposes.

### 5.4 Progress, stop and resume

Progress goes to stderr as one line redrawn in place when stderr is a terminal, and as one
log line every 50 conversations otherwise: pass name, done of total, percent, a fixed-width
bar, rate per hour over the last 50 conversations, estimated time remaining, elapsed time,
and the id and truncated title of the conversation in flight. `total` is the number of
conversations pending at pass start; skipped (no cached text) and failed ones count as
processed. The final stats line adds counts per category.

Stop: SIGINT and SIGTERM set a stop flag that the loops check between conversations (digest,
style languages, embed batches), and the signal interrupts the model call in flight. The
digest's writes already happen after the call, per chunk, in one transaction with the
checkpoint, so the store is consistent at any instant and a resumed run restarts the
in-flight conversation at its next chunk; nothing else is lost. The CLI prints the stats so
far and "stopped; run the same command to resume", and exits with status 130. A second
signal exits immediately. The `status` command shows summarized versus pending counts and the
category distribution.

### 5.5 The style pass samples the owner's own words

`run_style` strips every sample with the shared stripper before the length check and the
language grouping; samples shorter than 20 characters after stripping are dropped. The
sampling policy stays: newest first, up to 60 samples per language, 1,500 characters each,
one "default" profile per language. (My call: per-tier profiles wait for plan 1c, when the
tiers exist on contacts.)

### 5.6 The owner's past replies become examples for the drafter

The embed pass also embeds every owner-sent, non-bulk message with a body: stripped text,
truncated to 2,000 characters, under the embedding kind `owner_message` with `ref_id` the
message row id. Only the vector and the text hash are stored, as for every other embedding.

The drafting context gains a section "Your similar past replies": the `draft.examples`
(default 3) nearest `owner_message` vectors to the incoming message, excluding the
conversation being answered, each re-read from the source, stripped, and cut to 800
characters, with its date and recipient. The context snapshot stores them as 200-character
previews plus their source references (ruling R7). The reply prompt tells the model to match
the tone and length of those examples rather than copy their content.

### 5.7 Topic queries by category and by thread

`lookup` gains `--category` (repeatable) and `--threads`: thread mode embeds the query,
ranks conversations by their summary vectors, and prints conversation id, date of the last
message, category, title and the first 160 characters of the summary; message mode is
unchanged apart from the category filter. `conversations` gains `--category`.

### 5.8 Learned sender list for the live triage

After a digest run, `rule_triage` treats a sender as bulk without a model call when
`sender_stat` shows at least ten summarized conversations, every one of them `promotional`,
`newsletter` or `system`. A new `senders` command lists the top senders with their counts and
category distribution so the owner can see what the rule will do. (My call: ten is the
threshold; the owner can change it in config as `triage.learned_bulk_min`.)

### 5.9 Run order after merge

1. Move the live index aside and re-ingest (about four minutes) so the yjq17 bodies and
   Message-IDs are indexed.
2. `init --pass style` (minutes).
3. `init --pass digest` (days, resumable; stop and resume at will).
4. `init --pass embed` (can run between digest sessions; embeds whatever is summarized).

## 6. Left to the builder

- Module names and file layout for the shared stripper, the sender statistics and the
  progress display; exact prompt wording within the rules above; the schema of
  `schema_v3.sql` (at least `conversation.category` and `sender_stat`).
- The exact progress line format and the rate window.
- The nearest-neighbour query for owner examples. The existing `search_embeddings` takes the
  global top `max(10k, 100)` from the single `vec0` table and filters by kind afterwards; with
  29,000 summaries and the memories in that table, the top 100 may hold no owner message at
  all. The query must be partitioned by kind (a `kind` metadata column in `vec0`, or one
  vector table per kind), which the schema v3 step may introduce since the index is being
  rebuilt.
- Test fixtures: a thread with quoted replies, a promotional single mail, an event single
  mail, an interrupted digest that resumes.
- A real-model smoke run of about twenty conversations across the five queue groups on a
  scratch index, reported before the owner launches the full run.
