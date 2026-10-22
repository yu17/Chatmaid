# Using GNOME Evolution's accounts and cache from chatmaid (research, 2026-10-21)

Scope: Evolution / evolution-data-server (EDS) as installed on the owner's Arch box:
**EDS 3.62.1** (`/usr/include/evolution-data-server/libedataserver/eds-version.h`) and
**evolution 3.62.1-1** (`/var/lib/pacman/local/evolution-3.62.1-1/desc`).
Source claims were checked against the GNOME GitLab `master` trees on 2026-10-21 (3.63 development;
functionally identical to 3.62 for everything cited) plus the installed headers, schemas and desktop files.

Tags: **VERIFIED** = read in source/headers/docs this session; **INFERRED** = follows from verified facts
but not read directly; **UNCERTAIN** = not confirmed.

Version note: the cache DB schema changed in **3.58** (CamelStoreDB). Descriptions written for 3.56
("one SQLite table per folder") are obsolete on this machine. VERIFIED: `camel-store-db.c` comment
"3.58.0 adds a new column 'folders.folder_id' and renames all the message info tables into a 'messages_X'
table"; NEWS 3.57.1 "Camel: Add CamelStoreDB and CamelStoreSearch and use them".

## 1. Executive summary

| Requirement | Recommended | Why |
|---|---|---|
| (1) read history + new mail, read-only | Read Evolution's cache: `~/.cache/evolution/mail/<uid>/folders.db` (SQLite, `mode=ro`) + body files under `folders/.../cur/` | No credentials, no second IMAP login, cannot change flags. Bodies exist only for mail Evolution downloaded. |
| (2) new-mail notification | Subscribe to Evolution's D-Bus signal `org.gnome.evolution.mail.dbus.Signal.Newmail`; fallback inotify on `folders.db` | Carries folder, count, `msg_uid`, sender, subject; no polling, no credentials. |
| (3) draft, owner approves, then send | `evolution 'mailto:...?subject=..&body=..&In-Reply-To=..&References=..'` opens a prefilled composer in the running Evolution | Owner reviews/edits/sends in their normal client; drafts go to the identity's Drafts folder. |

Not recommended for phase 1: a Python libcamel (`gi.repository.Camel`) client. It is possible (typelib
installed) but needs a `CamelSession` subclass with six vfunc overrides, its own data/cache dirs, and
re-implements authentication. If direct server access is ever required, plain read-only IMAP reusing the
EDS-stored password/token (section 7) is simpler.

## 2. Storage layout and cache (question 1)

### 2.1 Directories

VERIFIED: Evolution's `EMailSession` sets `user-data-dir = $XDG_DATA_HOME/evolution/mail` and
`user-cache-dir = $XDG_CACHE_HOME/evolution/mail` (`e-mail-session.c`); each `CamelService` appends its
uid (`camel-service.c`).

- `~/.config/evolution/sources/<uid>.source` — one GKeyFile per ESource (mail account, identity,
  transport, collection). Group = extension name; key = CamelCase of the GObject property name
  (VERIFIED `e_source_parameter_to_key`: `remember-password` -> `RememberPassword`).
- `~/.cache/evolution/mail/<uid>/folders.db` — IMAPx summary DB. VERIFIED: `imapx_store_initable_init`
  sets `CAMEL_STORE_USE_CACHE_DIR`; `camel-store.c` builds `<user_cache_dir>/CAMEL_STORE_DB_FILE` and
  calls `camel_store_db_new()`. The macro value `folders.db` is INFERRED (long-standing name; the probe
  in section 8 confirms it).
- `~/.cache/evolution/mail/<uid>/.ev-store-summary` — folder list/flags (VERIFIED).
- `~/.cache/evolution/mail/<uid>/folders/<Name>[/subfolders/<Sub>]/` — per-folder dir. Every `/` in
  the IMAP path becomes `/subfolders/`; no other escaping (VERIFIED `imapx_path_to_physical`).
  Bodies: `cur/<hh>/<imap-uid>` where `hh` = `(g_str_hash(uid) >> 5) & 63` as two hex digits
  (VERIFIED `camel-data-cache.c`); raw RFC 822 as fetched with `UID FETCH <uid> (BODY.PEEK[])` and
  written via `camel_data_cache_add_atomic(cache, "cur", uid)` (VERIFIED `camel-imapx-server.c`).
- Local "On This Computer": store uid `local` (VERIFIED `E_MAIL_SESSION_LOCAL_UID`), Maildir under
  `~/.local/share/evolution/mail/local/`: Inbox = root `cur/new/tmp`; other folders are dot-dirs
  (`.Sent`; `Work/Mail` -> `.Work.Mail`; `Inbox/Sub` -> `..Sub`) (VERIFIED `camel-maildir-store.c`).
  Its `folders.db` is in that data dir (INFERRED: local store lacks `CAMEL_STORE_USE_CACHE_DIR`).

### 2.2 `.source` keys worth reading (names VERIFIED from headers/`e-source-authentication.c`; spelling INFERRED via the CamelCase rule)

```
[Data Source]       DisplayName= Enabled= Parent=<collection uid or empty>
[Mail Account]      BackendName=imapx|maildir|...  IdentityUid=<identity uid>  ArchiveFolder=
[Authentication]    Host= Port= User= Method=none|plain|...|Google|Outlook|Yahoo  ProxyUid=system-proxy
                    RememberPassword=true CredentialName= CredentialStoreId= IsExternal=false
[Imapx Backend]     StaySynchronized= LimitByAge= LimitUnit= LimitValue= UseIdle=true ConcurrentConnections=3
                    CheckAll=true UseSubscriptions= UseQresync= SingleClientMode= (CamelIMAPxSettings props)
[Mail Composition]  DraftsFolder=  TemplatesFolder=      (identity source)
[Mail Submission]   TransportUid=  SentFolder=            (identity source)
```

### 2.3 `folders.db` schema (VERIFIED `camel-store-db.c`, installed `camel-store-db.h`; 3.58+)

```sql
CREATE TABLE keys (key TEXT PRIMARY KEY, value TEXT);   -- csdb::folders_version=1, csdb::messages_version=1
CREATE TABLE folders (folder_name TEXT PRIMARY KEY, version INTEGER, flags INTEGER, nextuid INTEGER,
  time NUMERIC, saved_count INTEGER, unread_count INTEGER, deleted_count INTEGER, junk_count INTEGER,
  visible_count INTEGER, jnd_count INTEGER, bdata TEXT, folder_id INTEGER);
CREATE TABLE messages_<folder_id> (uid TEXT, flags INTEGER, msg_type INTEGER, dirty INTEGER, size INTEGER,
  dsent NUMERIC, dreceived NUMERIC, subject TEXT, mail_from TEXT, mail_to TEXT, mail_cc TEXT, mlist TEXT,
  part TEXT, labels TEXT, usertags TEXT, cinfo TEXT, bdata TEXT, userheaders TEXT, preview TEXT,
  PRIMARY KEY (uid));
CREATE INDEX messages_<id>_uid_flags ON messages_<id> (uid, flags);
```

- `flags` bits (VERIFIED `camel-message-info.h`): ANSWERED 1, DELETED 2, DRAFT 4, FLAGGED 8, SEEN 16,
  ATTACHMENTS 32, ANSWERED_ALL 64, JUNK 128, SECURE 256, NOTJUNK 512, FORWARDED 1024.
- `dirty` = local flag change not yet uploaded; `part` = references/thread id; `labels`/`usertags` =
  user flags/tags; `preview` = short text preview (header doc). `dsent`/`dreceived` are Unix seconds
  (INFERRED). A migrated DB may still contain `sqlite_sequence` (NEWS 3.59.1).
- `preview` is populated from the server `PREVIEW` FETCH item when supported, otherwise after the body
  is downloaded (UNCERTAIN on the exact rule).

### 2.4 What IMAPx actually caches

- Summary scan fetches `RFC822.SIZE RFC822.HEADER [BODYSTRUCTURE] [PREVIEW] FLAGS` per new UID
  (VERIFIED), so headers/metadata of every message in a scanned folder are in `folders.db`.
- **Bodies are on disk only when fetched**: on open, when a filter/search needs them, or by
  `camel_offline_folder_downsync_sync`, which runs for folders whose `offline-sync` is on or which
  inherit the store's `stay-synchronized` ("Synchronize remote mail locally" / "Download messages for
  offline usage"), optionally limited by `limit-by-age` (VERIFIED `camel-offline-folder.c`).
- Expiry (VERIFIED `camel-imapx-folder.c`, `camel-data-cache.c`): if the folder can downsync, files
  expire after the configured age limit (never if none); otherwise `expire-age` and `expire-access`
  are both 604800 s (7 days). Expired files are unlinked lazily during cache access, at most once per
  bucket per hour. Consequence: without offline download, chatmaid finds bodies only for recently
  viewed mail; with it, everything within the age limit.

### 2.5 Reading `folders.db` while Evolution runs

VERIFIED `camel-db.c`: plain `sqlite3_open()`; **no** `journal_mode` PRAGMA (default rollback journal,
`DELETE`) unless `CAMEL_SQLITE_IN_MEMORY` is set; `ATTACH DATABASE ':memory:' AS mem`;
`sqlite3_busy_timeout(100 ms)` plus a retry loop of 150 x 100 ms (~15 s) on `SQLITE_BUSY/LOCKED`;
custom function `CAMELCOMPAREDATE` and collations registered in-process only.

- A reader using `file:...?mode=ro` coexists safely, but a read transaction held longer than ~15 s can
  make Evolution's write fail. Use autocommit `SELECT`s, set your own busy timeout, and for bulk work
  take a snapshot: `sqlite3 'file:...?mode=ro' '.backup /tmp/snap.db'`.
- Do **not** use `immutable=1` on the live file (disables locking and change detection; torn reads).
- Never open read-write, never change `journal_mode`.
- Plain `SELECT`s need none of Camel's custom SQL functions.

```python
import sqlite3, pathlib
db = pathlib.Path.home() / ".cache/evolution/mail" / UID / "folders.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0, isolation_level=None)
folders = con.execute("SELECT folder_name, folder_id, saved_count, unread_count FROM folders").fetchall()
rows = con.execute(f"SELECT uid, flags, dreceived, mail_from, subject, preview FROM messages_{fid} "
                   "ORDER BY dreceived DESC LIMIT 50").fetchall()
```

## 3. Programmatic access via EDS / libcamel (question 2)

- Typelibs installed by Arch `evolution-data-server 3.62.1-1`: `Camel-1.2`, `EDataServer-1.2`,
  `EBackend-1.2`, `EBook-1.2`, `EBookContacts-1.2`, `ECal-2.0`, `EDataBook-1.2`, `EDataCal-2.0`,
  `EDataServerUI-1.2`. Providers in `/usr/lib/evolution-data-server/camel-providers/`: imapx, local,
  smtp, sendmail, pop3, nntp (VERIFIED archlinux.org file list).
- **There is no mail D-Bus service.** EDS ships `org.gnome.evolution.dataserver.Sources5`
  (`/usr/lib/evolution-source-registry`), `AddressBook10`, `Calendar8`, `UserPrompter0` only
  (VERIFIED `/usr/share/dbus-1/services/`). Mail runs inside the Evolution process (`EMailSession`).
  No `org.gnome.Evolution.service` exists on this machine (VERIFIED absent). A running Evolution
  registers GApplication `org.gnome.Evolution` and accepts CLI URIs through its `handle-uris` action
  (VERIFIED `e-shell.c`), but exposes no folder/message API.
- Enumerating accounts from Python is read-only and easy (VERIFIED names in `e-source-registry.h`,
  `e-source-mail-account.h`): `EDataServer.SourceRegistry.new_sync(None)`,
  `registry.list_sources("Mail Account")`, `source.get_uid()`, `source.get_display_name()`,
  `source.get_parent()`, `source.get_extension("Mail Account").get_backend_name()`,
  `source.get_extension("Authentication").get_host()/get_user()/get_method()`,
  `registry.ref_default_mail_account()`. This talks to `evolution-source-registry`, not Evolution.
- A full libcamel client is possible but heavy (VERIFIED constraints):
  - `Camel.init(certdb_dir, nss_init)`, `Camel.provider_init()`, then
    `camel_session_add_service(session, uid, "imapx", Camel.ProviderType.STORE)`.
  - `CamelSession` is a plain (non-abstract) GType that PyGObject can subclass, but `get_password`,
    `forget_password`, `trust_prompt`, `lookup_addressbook`, `get_filter_driver`,
    `get_oauth2_access_token_sync` have **no default**, and the default `authenticate_sync` is
    documented "not intended for production use" (retries forever on REJECTED with the password set
    via `camel_service_set_password`). XOAUTH2 calls `camel_session_get_oauth2_access_token_sync`.
  - EDS's own test session (`src/camel/tests/lib/session.c`) overrides nothing and sets only
    `user-data-dir` — enough for local folders, not for IMAP auth.
  - `e_source_camel_configure_service(source, service)` copies `.source` settings into the service.
  - 3.62 folder API: `camel_store_get_folder_info_sync`, `camel_store_get_folder_sync`,
    `camel_folder_refresh_info_sync`, `camel_folder_dup_uids` (renamed from `get_uids` in 3.57.1),
    `camel_folder_get_message_info`, `camel_folder_get_message_sync`, `camel_folder_get_message_cached`,
    `camel_folder_dup_headers_sync` (VERIFIED `camel-folder.h`, NEWS). `CamelStoreDB` methods
    (`read_messages`, `dup_uids_with_flags`, `get_folder_id`) are "Since 3.58" (VERIFIED docs).
  - A second libcamel process **must** use its own `user-data-dir`/`user-cache-dir`; pointing it at
    `~/.cache/evolution/mail` would make two writers of the same `folders.db` (INFERRED).
- No public Python example using `gi.repository.Camel` was found (UNCERTAIN that none exists);
  Debian/Ubuntu ship `gir1.2-camel-1.2`, so the binding is supported.
- Server side, a second IMAP login is normal. Evolution opens up to `concurrent-connections` (default 3)
  sessions per account and keeps one IDLE on INBOX or the selected mailbox (VERIFIED
  `camel-imapx-conn-manager.c`); Gmail allows up to 15 clients per account (VERIFIED Google support).

## 4. Credentials (question 3)

- **Passwords.** `e_source_store_password_sync` / `e_source_lookup_password_sync` call
  `e_secret_store_*` with **the source's own uid** (VERIFIED `e-source.c`). libsecret schema
  `org.gnome.Evolution.Data.Source`, attributes `e-source-uid` and `eds-origin`; lookup matches on
  `e-source-uid` only; `permanently=TRUE` -> default collection, else session collection
  (VERIFIED `e-secret-store.c`). Label: `Evolution Data Source "<name>" (...)` (VERIFIED).
- **Which uid holds the password.** For collection accounts (GOA, "Collection account") the child mail
  source has none; `e_source_credentials_provider_ref_credentials_source()` walks up to the parent that
  has the `Collection` extension (VERIFIED `e-source-credentials-provider.c`). So look up the account uid
  or its `[Data Source] Parent=` uid.
- **Third-party read.** `secret-tool lookup e-source-uid <uid>` returns exactly what EDS reads;
  `secret-tool search --all e-source-uid <uid>` shows attributes/label without the secret. libsecret
  enforces nothing beyond the unlocked keyring (VERIFIED schema; standard libsecret CLI).
  API route: `EDataServer.SourceCredentialsProvider.new(registry)` ->
  `provider.lookup_sync(source, None)` -> `ENamedParameters` (VERIFIED installed header; parameter key
  names such as `username`/`password` INFERRED, macros not read this session).
- **Built-in OAuth2** (`Method=Google|Outlook|Yahoo` or a Dynamic service). The default `can_process`
  compares `[Authentication] Method` **exactly** with the service name and skips `IsExternal=true`
  (VERIFIED `eos_default_can_process`). Tokens are stored in the same libsecret schema under uid
  **`OAuth2::<service>[<user>]`** or `OAuth2::<service>[<user>][<CredentialStoreId>]` (not the
  source uid), label `Evolution Data Source - <service>[...]`, value = JSON with `refresh_token`,
  `access_token`, `expires_after` (absolute Unix time) (VERIFIED `eos_generate_secret_uid`,
  `eos_store_token_sync`, `e-oauth2-service.h`). `e_oauth2_service_get_access_token_sync()` refreshes
  when <= 10 s remain and serialises refreshes across processes with `$TMPDIR/.evo-oauth2-<uid>.lock`
  (VERIFIED).
- Google: scope `https://mail.google.com/` (+calendar/contacts/tasks); auth
  `accounts.google.com/o/oauth2/v2/auth`, token `oauth2.googleapis.com/token`; client id/secret from
  GSettings `org.gnome.evolution-data-server oauth2-google-client-id/-secret` or build-time values
  (VERIFIED). "Outlook" is the consumer `login.live.com` flow (`wl.offline_access wl.emails wl.imap`)
  matching hosts `outlook.com`/`office365.com` for `imapx`/`smtp` (VERIFIED); Microsoft 365 work
  accounts normally go through evolution-ews/Graph instead of IMAP (UNCERTAIN for the owner's setup).
- **Getting a token legitimately from another process.** Prefer
  `EDataServer.Source.get_oauth2_access_token_sync()` on the account or collection source: it uses the
  registry's D-Bus `org.gnome.evolution.dataserver.Source.OAuth2Support.GetAccessToken
  (out s access_token, out i expires_in)` when present (GOA-backed sources), else the local
  `EOAuth2Services` + keyring path (VERIFIED `e-source.c`, `Source.xml`). Reading the `OAuth2::...`
  secret and refreshing it yourself works but races Evolution unless you honour the same lock file,
  and reuses a client id you do not own.
- GNOME Online Accounts: goa-daemon `org.gnome.OnlineAccounts.OAuth2Based.GetAccessToken() ->
  (s access_token, i expires_in)` (VERIFIED GOA docs); EDS proxies it via `OAuth2Support`.

## 5. Notifications (question 4)

- **mail-notification plugin D-Bus signal** (VERIFIED `src/plugins/mail-notification/mail-notification.c`):
  signal `Newmail`, interface `org.gnome.evolution.mail.dbus.Signal`, path
  `/org/gnome/evolution/mail/newmail`. Body: `(s)` folder display name; if count > 0 also `(s)` display
  name again and `(u)` count; then optional `(s)` strings `msg_uid:<uid>`, `msg_sender:<addr>`,
  `msg_subject:<subj>`. `MessageReading` is emitted when a message is read. Honours
  `org.gnome.evolution.plugin.mail-notification notify-only-inbox` (default true) and
  `notify-not-accounts`. The installed schema still has `notify-dbus-enabled` (default true) but master
  no longer reads it; emission is unconditional while the plugin is enabled (UNCERTAIN whether 3.62.1
  still checks the key — on by default either way).

```python
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def on_newmail(conn, sender, path, iface, signal, params):
    print(params.unpack())          # e.g. ('Inbox', 'Inbox', 1, 'msg_uid:1234', 'msg_sender:..', 'msg_subject:..')
bus.signal_subscribe(None, "org.gnome.evolution.mail.dbus.Signal", "Newmail",
                     "/org/gnome/evolution/mail/newmail", None, Gio.DBusSignalFlags.NONE, on_newmail)
GLib.MainLoop().run()
```

- **Desktop notifications**: the same plugin uses libnotify (`notify_init("evolution-mail-notification")`,
  summary "New email in Evolution", hint `desktop-entry=org.gnome.Evolution`) (VERIFIED). Capturing
  them needs eavesdropping `org.freedesktop.Notifications.Notify` (`dbus-monitor`/`BecomeMonitor`) and
  loses the UID — use the signal instead.
- **File watching**: inotify on `~/.cache/evolution/mail/<uid>/folders.db` (+ `-journal`) fires on every
  summary write (flags, counts), so debounce and diff `messages_<id>` by max `uid`/`dreceived`
  (INFERRED). Works even with the plugin disabled, but not when Evolution is closed (nothing updates the
  cache then anyway).
- **Own IMAP IDLE**: Evolution already keeps IDLE on INBOX (`use-idle` default TRUE) (VERIFIED); a
  chatmaid IDLE would duplicate it and need credentials — only for the section 7 architecture.
- EDS/registry D-Bus has no mail-change signals; `ESourceRegistry` only signals source
  added/changed/removed/enabled/disabled/credentials-required (VERIFIED header).

## 6. Drafting and sending through Evolution (question 5)

- Desktop file (VERIFIED): `Exec=evolution %U`; `MimeType` includes `x-scheme-handler/mailto` and
  `x-scheme-handler/mid`; the "compose" desktop action runs `evolution mailto:`.
- Delivery to the running instance (VERIFIED `e-shell.c`, `e-mail-shell-backend.c`): a second
  `evolution` process detects `g_application_get_is_remote`, activates the primary and forwards URIs via
  the `handle-uris` action; the mail backend handles `mailto:` (compose), `folder:` (select a folder)
  and `mid:` (search by Message-ID).
- mailto parsing (`handle_mailto` in `e-msg-composer.c`, VERIFIED): `to`, `cc`, `bcc`, `subject`,
  `body` (plain text -> editor), `attach`/`attachment` (path or `file:` URI; hidden dirs, `etc`, `..`
  are refused; a review warning is shown), `from` (selects the identity by address), `reply-to`,
  `mail-followup-to`, `mail-reply-to`. **Any other header is URL-decoded and added verbatim as a custom
  header** via `e_msg_composer_add_header`, so `In-Reply-To=%3Cid%3E&References=%3Cid%3E` produces a
  properly threaded reply. The window is presented; nothing is sent automatically.
- Example: `evolution 'mailto:alice@example.org?subject=Re%3A%20Plan&In-Reply-To=%3Cabc%40example.org%3E&References=%3Cabc%40example.org%3E&body=Hi%20Alice%2C%0A...'`
- Drafts: no CLI/D-Bus creates a draft; the composer's Save Draft appends to the identity's
  `[Mail Composition] DraftsFolder` or the local Drafts folder (VERIFIED `em-composer-utils.c`). A
  chatmaid-written draft would be an IMAP `APPEND` — a write, out of scope until approved.
- Sending without the GUI (later, after approval): libcamel `camel_session_add_service(..., "smtp",
  TRANSPORT)` + `camel_transport_send_to_sync` with the transport `.source` settings and the same keyring
  password (INFERRED from provider list), or Python `smtplib` with PLAIN/XOAUTH2. Both skip Evolution's
  Sent-folder copy and `X-Evolution-*` bookkeeping; the mailto hand-off matches "owner approves in their
  normal client" better.
- Other CLI options (VERIFIED `e-shell.c`): `--component=mail|calendar|contacts|tasks|memos`,
  `--offline/--online/--force-online`, `--quit`, `--disable-eplugin`, `--import`, `--view`,
  `--force-shutdown` (also kills the EDS factories — never use from automation).

## 7. Lower-coupling alternative: plain IMAP (question 6)

- Config from `.source` (`Host`, `Port`, `User`, `[Security] Method`), password via
  `secret-tool lookup e-source-uid <uid>` or the credentials provider (section 4).
- Read-only discipline (VERIFIED RFC 9051): open with `EXAMINE` (6.3.3: "the selected mailbox is
  identified as read-only ... No changes to the permanent state of the mailbox, including per-user state,
  are permitted"); fetch with `BODY.PEEK[...]` (6.4.5: "does not implicitly set the \Seen flag"); IDLE is
  6.3.13. `imaplib.select(mailbox, readonly=True)` issues EXAMINE; `imap-tools` has `mark_seen=False`.
- OAuth2 accounts: Gmail requires SASL `XOAUTH2` = `base64("user=" + user + "^Aauth=Bearer " + token +
  "^A^A")` with scope `https://mail.google.com/`; Microsoft 365 uses the same mechanism with scope
  `https://outlook.office.com/IMAP.AccessAsUser.All` (VERIFIED vendor docs). Basic auth is effectively
  gone on both, so the token must come from EDS/GOA (section 4) or chatmaid's own OAuth app — the former
  keeps "one sign-in", the latter duplicates it.
- Trade-off: no cache knowledge needed and every body is reachable, but it duplicates connections/IDLE,
  must handle reconnects/throttling, and the credential reuse couples it to EDS anyway. For read-only
  history the cache route (section 2) is simpler and safer; keep this as the plan B.

## 8. Probe commands (read-only; run as the owner)

```sh
pacman -Q evolution evolution-data-server libsecret gnome-online-accounts python-gobject sqlite
ls ~/.config/evolution/sources/
grep -H -E '^(DisplayName|Parent|Enabled|BackendName|Host|Port|User|Method|ProxyUid|IdentityUid|StaySynchronized|LimitByAge|UseIdle|ConcurrentConnections|TransportUid|DraftsFolder|AccountId)=' ~/.config/evolution/sources/*.source
ls -la ~/.cache/evolution/mail/ ~/.local/share/evolution/mail/
for db in ~/.cache/evolution/mail/*/folders.db ~/.local/share/evolution/mail/local/folders.db; do
  echo "== $db"; sqlite3 "file:$db?mode=ro" '.tables' 'SELECT key, value FROM keys;' \
    'SELECT folder_name, folder_id, saved_count, unread_count FROM folders;'; done
sqlite3 "file:$HOME/.cache/evolution/mail/<uid>/folders.db?mode=ro" \
  'SELECT uid, flags, datetime(dreceived,"unixepoch"), mail_from, subject FROM messages_<id> ORDER BY dreceived DESC LIMIT 10;'
find ~/.cache/evolution/mail/<uid>/folders -path '*/cur/*' -type f | wc -l     # cached bodies vs saved_count
find ~/.cache/evolution/mail/<uid>/folders -path '*/cur/*' -type f | head -3
secret-tool search --all e-source-uid <uid>                                   # attributes + label, no secret
secret-tool search --all eds-origin evolution-data-server 2>/dev/null | grep -E 'e-source-uid|label'  # incl. OAuth2::...
python3 -c 'import gi; gi.require_version("EDataServer","1.2"); gi.require_version("Camel","1.2")
from gi.repository import EDataServer, Camel
r = EDataServer.SourceRegistry.new_sync(None)
for s in r.list_sources("Mail Account"):
    print(s.get_uid(), s.get_display_name(), s.get_extension("Mail Account").get_backend_name(), s.get_parent())
print([m for m in dir(Camel.Folder) if m.startswith("dup_")])'
gsettings list-recursively org.gnome.evolution.plugin.mail-notification
dbus-monitor --session "type='signal',interface='org.gnome.evolution.mail.dbus.Signal'"   # then mail yourself
busctl --user list | grep -i evolution     # expect ...dataserver.Sources5 and org.gnome.Evolution while running
```

## 9. Risks

- **Locking**: rollback-journal mode plus Camel's ~15 s busy budget — a long reader transaction (or an
  interactive `sqlite3` left inside `BEGIN`) can make Evolution's write fail. Use autocommit `SELECT`s,
  `mode=ro`, your own busy timeout, or `.backup` snapshots. Never `immutable=1`, never change
  `journal_mode`, never open read-write.
- **Cache incompleteness**: bodies only for downloaded messages, expired after 7 days unless offline
  sync is on; `preview` may be empty; unscanned folders have no `messages_<id>` table. Mitigation: ask
  the owner to enable "Download messages for offline usage" on the account (also stops expiry).
- **Schema drift**: `messages_<id>` layout is "version 1" since 3.58; older docs describe per-folder
  tables. Check `keys` (`csdb::*_version`) and `folders.folder_id` at startup.
- **Accidental flag changes**: impossible via the cache route. With libcamel/IMAP any `SELECT` +
  `FETCH BODY[]`, or `camel_folder_set_message_flags`, writes — use EXAMINE/PEEK and never call set-flag
  APIs.
- **Credential exposure**: `secret-tool lookup` prints secrets in clear; keep them out of logs/transcripts.
  Remember collection accounts key the password by the collection uid.
- **OAuth2 refresh races**: refreshing the `OAuth2::...` secret yourself can invalidate Evolution's
  token and ignores EDS's `.evo-oauth2-*.lock`; go through `get_oauth2_access_token_sync` / GOA.
- **Server limits**: Gmail 15 simultaneous clients; Evolution already uses up to 3 connections + IDLE.
- **Process coupling**: the `Newmail` signal exists only while Evolution runs with the plugin enabled;
  inotify covers the plugin-disabled case; nothing covers Evolution-closed (the cache is stale then).
- **Collection/GOA accounts**: child sources have `Parent=`; uid-based paths and secrets then differ
  from a standalone account — resolve the parent before looking anything up.

## 10. References (accessed 2026-10-21)

- EDS source (master), base URL `https://gitlab.gnome.org/GNOME/evolution-data-server/-/raw/master/`:
  `src/camel/camel-store-db.c`, `camel-db.c`, `camel-store.c`, `camel-service.c`, `camel-session.c`,
  `camel-data-cache.c`, `camel-offline-folder.c`, `camel-sasl-xoauth2.c`,
  `src/camel/providers/imapx/camel-imapx-store.c`, `camel-imapx-folder.c`, `camel-imapx-server.c`,
  `camel-imapx-conn-manager.c`, `camel-imapx-settings.c`, `camel-imapx-utils.c`,
  `src/camel/providers/local/camel-maildir-store.c`, `src/camel/tests/lib/session.c`,
  `src/libedataserver/e-secret-store.c`, `e-source.c`, `e-source-camel.c`, `e-source-authentication.c`,
  `e-source-credentials-provider.c`, `e-source-credentials-provider-impl-oauth2.c`,
  `e-oauth2-service.c`, `e-oauth2-services.c`, `e-oauth2-service-google.c`, `e-oauth2-service-outlook.c`,
  `src/private/org.gnome.evolution.dataserver.Source.xml`, `NEWS`.
- Installed headers (EDS 3.62.1): `/usr/include/evolution-data-server/camel/{camel.h,camel-session.h,
  camel-folder.h,camel-store.h,camel-store-db.h,camel-provider.h,camel-offline-settings.h,
  camel-message-info.h}`, `libedataserver/{eds-version.h,e-secret-store.h,e-oauth2-service.h,
  e-source-credentials-provider.h,e-source-registry.h,e-source-mail-account.h,
  e-source-authentication.h,e-source-mail-composition.h,e-source-camel.h}`.
- EDS API docs: https://gnome.pages.gitlab.gnome.org/evolution-data-server/camel/class.StoreDB.html
- Evolution source (master), base URL `https://gitlab.gnome.org/GNOME/evolution/-/raw/master/`:
  `src/shell/main.c`, `src/shell/e-shell.c`, `src/modules/mail/e-mail-shell-backend.c`,
  `src/composer/e-msg-composer.c`, `src/mail/em-composer-utils.c`,
  `src/libemail-engine/e-mail-session.c`, `e-mail-session.h`,
  `src/plugins/mail-notification/mail-notification.c`.
- Local Evolution files: `/usr/share/applications/org.gnome.Evolution.desktop`,
  `/usr/share/glib-2.0/schemas/org.gnome.evolution.plugin.mail-notification.gschema.xml`,
  `/usr/share/glib-2.0/schemas/org.gnome.evolution-data-server.gschema.xml`,
  `/usr/share/dbus-1/services/org.gnome.evolution.dataserver.Sources5.service`.
- Arch package file list 3.62.1-1: https://archlinux.org/packages/extra/x86_64/evolution-data-server/files/
- GNOME Online Accounts D-Bus: https://gnome.pages.gitlab.gnome.org/gnome-online-accounts/dbus-org.gnome.OnlineAccounts.OAuth2Based.html
- RFC 9051 (IMAP4rev2): https://www.rfc-editor.org/rfc/rfc9051.html (6.3.3 EXAMINE, 6.3.13 IDLE, 6.4.5 FETCH)
- Gmail XOAUTH2: https://developers.google.com/workspace/gmail/imap/xoauth2-protocol ;
  Gmail client limit: https://support.google.com/mail/answer/7126229
- Microsoft IMAP/SMTP OAuth: https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth
- Not reachable this session: wiki.archlinux.org/title/Evolution (bot protection);
  wiki.gnome.org/Apps/Evolution/CamelDBSummary is an outdated design page and was not relied on.
