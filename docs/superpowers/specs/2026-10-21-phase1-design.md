# chatmaid phase 1: core store, email connector, WeChat one-shot import

Status: **DRAFT, not approved.** Written 2026-10-21 from the owner's two messages
(2026-09-27 brief, 2026-10-21 follow-up) plus the research in `docs/research/`.
Nothing in this document has been built. Section 9 lists the decisions only the
owner can make; section 10 lists the calls I made so they can be checked.

## 1. Intent

chatmaid watches the owner's messaging platforms (WeChat first, then Discord and
email), reads conversation history, and suggests the owner's next message. A
local LLM handles everything that touches private content; online LLMs are
called per request for revision, refinement, or heavier reasoning, and only see
redacted or explicitly approved material. The user-facing side will be a unified
remote-control interface or an overlay/helper window on the chat apps.

Phase 1 is the foundation: a local message store with a normalized model, the
first read-only connector (email via the owner's Evolution client), and the
one-time import of the complete WeChat history. Reply suggestion, cloud
refinement, Discord, and the UI come in later phases.

## 2. Why now, and what success looks like

- The owner already has the data sources in place: a logged-in secondary WeChat
  client on the MacBook, nine mail accounts cached by Evolution on the
  workstation, and a 24 GB GPU for local inference.
- Success for phase 1: the owner can open one local store and see their complete
  WeChat history (text, images, videos, link cards, files, stickers) and their
  mail history side by side, kept current for mail, with nothing having been
  written to WeChat, Evolution, or any mail server.

## 3. Goals, non-goals, anti-goals

Goals

- One normalized store for messages from every platform, queryable by
  conversation and time, with media stored as files and referenced by the store.
- WeChat: complete one-shot import, including media and stickers, from a
  migrated desktop client database.
- Email: read-only access to history and new mail through Evolution's own
  accounts and cache, with no second sign-in.
- Everything stays on the workstation; cloud models are out of scope for phase 1.

Non-goals (phase 1)

- Sending or drafting on any platform.
- Ongoing WeChat sync and notifications (depends on where the always-on client
  lives; decision deferred, see 9.2).
- Discord. The compliant path is an official bot in servers the owner controls,
  which cannot see DMs; it needs its own design.
- Reply suggestion and the overlay or remote UI.

Anti-goals (would make this a failure even if it "works")

- Any write to WeChat data, Evolution data, or a mail server before the owner
  has explicitly approved that specific test.
- Any protocol-level or hook-based WeChat tooling on the owner's main account
  (high ban risk).
- Chat content or decrypted databases committed to the repository or sent to a
  cloud service.
- A Windows VM that grows into a second workstation. If a VM is used it must stay
  minimal and disposable.

## 4. Constraints

- WeChat primary login is an unrooted vivo X Fold 6. WeChat allows one phone,
  one PC seat (Windows, macOS and Linux share it), and one tablet online at once.
  Logging the same account into a Windows VM will log out the MacBook client.
- A secondary desktop login holds only recent messages. Complete history must be
  pushed from the phone with 迁移/导出到电脑 (migration), which merges into the
  desktop client's live database. The 备份 (backup) blob is encrypted with a key
  held inside the Android app; it is not recoverable on an unrooted phone.
- Migration drops images and videos not opened or downloaded in the last 7 days
  (Huawei support, verbatim in `wechat-one-shot-extraction.md`), and excludes
  call logs and official-account chats.
- Desktop WeChat 4.x databases are SQLCipher 4 (WCDB). Keys live only in process
  memory. Linux: readable from `/proc/<pid>/mem`. macOS: readable with a
  memory-reading tool while WeChat runs, SIP stays on; tool support for 4.1.15
  is unverified. Windows: broke at 4.1.13.65, so a Windows route must pin
  ≤4.1.12.
- The workstation has no QEMU/libvirt installed and no WeChat Linux client.
  `/mnt/Apps` is 91% full (about 179 GB free). The MacBook has 96 GB free and
  its whole WeChat data tree is 1.6 GB today (recent messages only); the
  post-migration size is unknown until the owner reads it off the phone
  (see 9.2).
- Evolution 3.62.1 is running with 9 cached mail accounts (5 IMAP, 3 Exchange
  Web Services, 1 Microsoft 365) and a 17 GB cache. The local "On This
  Computer" store is a 100 MB Maildir holding 307 sent messages and nothing
  else. libcamel and EDataServer are importable from Python through GObject
  introspection.
- Owner's rule: read-only first; no write or edit test begins without telling
  the owner and getting a go.

## 5. Architecture (phase 1 slice)

```
 WeChat desktop client (Mac now; VM or Linux later)   Evolution (workstation)
   db_storage/*.db (SQLCipher)  msg/ media              ~/.cache/evolution/mail/<acct>/
          │ one-shot: key → decrypt to a working copy          folders.db + body cache
          ▼                                                     │ read-only; D-Bus Newmail
   wechat importer ──────────────┐            ┌──────────────── signal or inotify
                                 ▼            ▼
                        normalized message store (SQLite)
                        messages · conversations · contacts · media index
                                 │
                        media files on disk (decoded images, voice→mp3,
                        video, files, stickers), referenced by hash
                                 │
                      (phase 2) local LLM: embeddings, reply drafts
```

Core model (one table set, platform-agnostic): `conversation` (platform,
platform_id, kind: dm/group/mailthread, title), `contact` (platform, platform_id,
display names, aliases), `message` (conversation, sender, sent_at, kind: text,
image, video, voice, file, sticker, link, system, email; body text; quoted or
in-reply-to reference; raw payload kept as JSON for anything lossy), `media`
(sha256, path, mime, width/height/duration, source reference), `message_media`
(link table). Each connector is an importer that maps its source into this
model and is idempotent on re-run.

## 6. WeChat one-shot import

The owner does the migration; the automation does the extraction. Full
procedure and alternatives are in `docs/research/wechat-one-shot-extraction.md`.

Recommended route: **MacBook first.** It already holds a live 4.1.15 login and
needs nothing installed on the workstation. Steps:

1. Owner, on the phone: open the important chats so media is downloaded (7-day
   rule), then `我 → 设置 → 聊天 → 聊天记录迁移与备份 → 迁移 → 迁移到电脑微信`
   (or on the 2025 UI `设置 → 通用 → 聊天记录管理 → 导入与导出 → 导出到电脑`),
   choose all chats, confirm the pop-up on the Mac. Same Wi-Fi, WeChat open and
   logged in on the Mac.
2. Before that, a 10-minute test decides the route: with WeChat running on the
   Mac, run a memory key tool (wxkey / wechat-cli) and check it can open
   `message_0.db`. If it fails on 4.1.15, fall back to the Linux client route
   (install official 4.1.13, log in, which displaces the Mac login, extract the
   key from process memory, then migrate to it). Windows VM is the last resort.
3. Automation: quit WeChat, copy `db_storage/` and `msg/` to the workstation
   over SSH, decrypt into a working copy (never the live directory), parse
   `Msg_<md5>` tables across `message_N.db` shards, resolve media through
   `hardlink.db` and `message_resource.db`, decode `.dat` images with the
   per-account image key, SILK voice to mp3, `wxgf` via ffmpeg, and import.
4. Stickers (type 47): use the local `emoticon.db` and cache first; for the
   rest, download `encrypturl` and decrypt with the message's `aeskey`
   (AES-128-ECB), or fetch `cdnurl`; store-pack stickers that cannot be
   downloaded keep their product id and thumbnail only.
5. Completeness check: message count jumps versus the pre-migration baseline,
   oldest message in the longest chat matches the phone, 10 random media items
   per type open, group senders resolve. Re-run migration once a day later to
   sweep anything missed.

What this touches on the owner's devices: the migration writes into the Mac's
WeChat database (the owner's own action); the key tool runs a re-signed copy
of WeChat.app and reads its memory (no data modified, but third-party code runs
on the Mac). Both need the owner's go.

## 7. Email connector (read-only, through Evolution)

Details and sources are in `docs/research/evolution-email-access.md`. Verified
on the workstation on 2026-10-21: Evolution 3.62.1 is running; nine accounts
are cached under `~/.cache/evolution/mail/<uid>/`, each with a `folders.db`
(SQLite tables `keys`, `folders`, and one `messages_<folder_id>` per folder)
and raw RFC822 body files under `folders/<Name>/cur/<hh>/<imap-uid>`; the
Exchange and Microsoft 365 accounts use the same cache format as IMAP, so no
OAuth work is needed to read them; Evolution's mail-notification plugin has
D-Bus signalling enabled; `org.gnome.Evolution` is on the session bus.

Read path. For each account in scope, open `folders.db` with
`file:...?mode=ro`, short autocommit SELECTs and a busy timeout. Never
read-write, never `immutable=1` on the live file, never a long transaction
(Evolution's writer gives up after about 15 seconds of lock). Enumerate
`folders`, read each `messages_<id>` table (uid, flags, dates, subject, from,
to, cc, thread reference, labels, preview), parse the body file with Python's
`email` package when it exists, and map into the normalized model: a mail
thread is a conversation (grouped by References/In-Reply-To, falling back to
normalized subject), each address is a contact, attachments become media.
Import is idempotent on (account uid, folder, imap uid). Messages without a
cached body are stored header-only and marked so.

Completeness depends on Evolution's "Download messages for offline usage"
setting: with it on, bodies for everything within the age limit are on disk;
with it off, only opened mail is cached and files expire after 7 days. The
probe shows 9 of 14 mail-related sources have it on. Enabling it for the
accounts in scope is a change the owner makes in Evolution's account settings.

Notification path. Subscribe on the session bus to the `Newmail` signal
(interface `org.gnome.evolution.mail.dbus.Signal`, path
`/org/gnome/evolution/mail/newmail`), which carries folder name, count, the
message uid, sender and subject. By default it fires for Inbox folders only.
Fallback: inotify on `folders.db` with debouncing and a diff by max uid. Both
only work while Evolution runs, which matches how the owner uses it.

Later draft and send (phase 2, only after approval). Run
`evolution 'mailto:...?subject=...&In-Reply-To=...&References=...&body=...'`
to open a prefilled, correctly threaded compose window in the running
Evolution; the owner edits and clicks Send, and Evolution keeps its own Sent
and Drafts bookkeeping. No automated send and no draft creation in phase 1.

Not chosen. A libcamel client from Python is possible but needs a
`CamelSession` subclass with six vfunc overrides, its own cache directories,
and its own authentication; plain read-only IMAP would duplicate Evolution's
connections and need OAuth tokens through EDS or GNOME Online Accounts. Both
stay as plan B if a body is ever needed that the cache does not hold.

## 8. Storage, privacy and operations

- Store and media live outside the repository under a path the owner chooses
  (see 9.5); `.gitignore` already excludes `data/` and database files.
- Decrypted WeChat databases and extracted keys are treated as secrets: kept
  only in the data directory, never logged, never committed.
- All connectors open sources read-only (SQLite `mode=ro`, `immutable=1` where
  safe; IMAP would use EXAMINE and BODY.PEEK if ever used).
- Runs as the owner's user on the workstation. Phase 1 ships a re-runnable
  incremental import command plus a foreground `watch` command that subscribes
  to Evolution's new-mail signal (inotify fallback) and imports as mail
  arrives. No systemd unit or background service until phase 2.
- Acceptance is checked with a minimal read CLI: list conversations, show one
  conversation, search text. Without it only someone writing SQL could verify
  the deliverable.

## 9. Decisions the owner must make

Ordered by how much they change what gets built.

1. **WeChat one-shot route.** Mac-first (recommended), Linux client, or Windows
   VM. Mac-first needs: permission to run a key-extraction tool on the Mac with
   WeChat open, and the owner to run the migration from the phone.
2. **Expected WeChat data volume.** On the phone, `设置 → 通用 → 存储空间`
   shows the chat-data size. Under about 20 GB the import is a plain
   copy, decrypt, import sequence; at 50 GB or more it needs staged copying
   with cleanup or a different volume, since the workstation holds the
   encrypted copy, a decrypted working copy, and decoded media (roughly two to
   three times the source).
3. **Where the always-on WeChat client will live later** (affects sending and
   live sync, not phase 1). Windows VM (owner's stated preference; needs QEMU
   install, a Windows 11 IoT LTSC evaluation image or a licensed one, a
   ≤4.1.12 build, and accepts displacing the Mac login) versus the MacBook
   (AppleScript sending, no VM) versus the Linux client (xdotool sending,
   immature). Can be deferred.
4. **Email accounts in scope, and offline download.** All nine cached accounts
   or a subset (the largest single cache is 12 GB). Exchange and Microsoft 365
   accounts are cached in the same format, so the cache route covers them
   without OAuth work. For full bodies, the owner enables "Download messages
   for offline usage" on the in-scope accounts in Evolution; otherwise the
   connector sees bodies only for mail opened in the last 7 days.
5. **Language and stack.** My call is Python 3.14 with uv (see 10); confirm or
   override.
6. **Data directory and encryption at rest.** Where the store and media go
   (the `/mnt/Apps` volume is 91% full). My default for encryption is
   filesystem-level (the owner's LUKS, fscrypt or gocryptfs; the code writes
   plaintext). Application-level encryption (SQLCipher store, encrypted media
   files) changes every database open and all media handling and is cheap to
   retrofit for the database but not for media, so say now if it is required.
7. **Go/no-go for each write-capable step**, which I will request individually:
   phone migration, key tool on the Mac, enabling offline download in
   Evolution, and later anything that drafts or sends.

## 10. Calls I made (check these while reading)

- Python 3.14 + uv for the codebase. Reason: every relevant building block is
  Python-first (SQLCipher bindings, zstd, SILK decoders, python-gobject for
  Evolution, LLM tooling), and uv is already installed.
- SQLite for the store, with sqlite-vec later for embeddings, so one file holds
  messages and vectors.
- Media stored content-addressed by sha256 with the original WeChat path kept as
  metadata, so re-imports deduplicate.
- Raw source payloads kept as JSON beside the normalized fields, so a later
  parser fix never requires re-extracting from WeChat.
- Phase 1 excludes all sending and all cloud calls, matching the owner's
  read-only rule.
- Email scope defaults to all nine cached accounts plus the local Maildir Sent
  folder (307 messages, read with Python's `mailbox` module), because that is
  all the local store contains.
- A foreground `watch` command rather than a service, and a minimal read CLI
  as the acceptance tool (section 8).
- Encryption at rest left to the filesystem unless the owner says otherwise
  (section 9.6).
- WeChat import is planned Mac-first with the Linux client as a branch taken
  only if the key test fails. The Linux branch has an untested gap: research
  confirms the per-account image key (needed to decode `.dat` images) only via
  the macOS tool; whether the Linux key tool yields it is unknown and needs a
  spike before that branch is relied on.

## 11. What is left to the builder

- Exact table DDL and migration tooling.
- Choice of SQLCipher binding and zstd library.
- Parser coverage for the type 49 appmsg subtypes (link, file, quote, forwarded
  records, mini-program) beyond text extraction.
- Import CLI shape and progress reporting.
