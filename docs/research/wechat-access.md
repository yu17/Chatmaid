# WeChat access research (chatmaid)

Research date: **2026-09-27**. Scope: WeChat only. Owner's own personal account and own devices.

Legend for claims: **[V]** = verified from a source I actually read; **[I]** = inferred from
verified facts; **[U]** = uncertain / could not confirm. Dates in parentheses are the source's
own date or the GitHub `pushed_at`.

> Legal/compliance note (read first). In Jan 2026 Tencent filed a DMCA (§1201 anti-circumvention
> + ToS tortious-interference) takedown that removed ~4,195 GitHub repos of WeChat DB
> export/decrypt tools **[V]** (github/dmca 2026-01-08-tencent.md). The two flagship tools
> (`sjzar/chatlog`, `xaoyaoo/PyWxDump`) had already self-deleted their code on 2025-10-20 after a
> compliance letter from Tencent **[V]**. Everything in section A therefore operates in a legally
> contested space even though it only reads the owner's *own* local data. Decrypting your own
> local DB and UI-automating your own client are low *ban* risk but are ToS violations; protocol
> and hook tools are both ToS violations and high ban risk. This document reports the landscape;
> choosing what to run is a decision for the owner.

---

## 1. Executive summary

### Recommended approach (ranked)

**Primary — "desktop secondary login + local DB decryption + polling + UI-automation send":**

1. **History (goal 1):** Run an official WeChat desktop client as a **secondary login** on the
   Linux dev box (or a Windows/macOS VM). A fresh secondary login only pulls **recent** messages,
   **not** full history **[V]**, so first do a one-time **phone→desktop chat-history migration**
   (微信 → 设置 → 聊天 → 聊天记录迁移/管理, over the same Wi‑Fi) to merge the phone's complete
   history into that desktop's local database **[V]**. Then decrypt the desktop `db_storage`
   WCDB/SQLCipher-4 databases with a chatlog-style tool.
2. **Incremental sync + near-real-time notify (goals 1 & 2):** Keep that desktop client logged in;
   it receives every new message live while online **[V]**. Poll the decrypted DB (WAL-aware) on a
   short interval — mature tools already do 500 ms–3 s incremental decryption **[V]** — to emit
   (sender, chat, content, media ref) events. This is the notification pipeline.
3. **Send (goal 3, optional):** UI-automation of the same desktop client. Lowest ban risk of the
   send options **[V]**, but the weakest link in the plan: on Windows the free `wxauto` only works
   on WeChat **3.9.x** (maintenance stopped 2025-10 when 4.0.5 moved to Qt Quick and UIAutomation
   sees only an empty shell window), `wxauto4` is marked 停止更新 (no longer updated), and only the
   **paid beta `wxautox4`** (4.0.5 only) targets 4.x **[V]**. macOS AppleScript/Accessibility and
   Linux xdotool coordinate clicking are the remaining routes; both untested here (see D.1, D.5, D.6).
4. **Fallback / redundancy for notify:** Android **NotificationListenerService** on the vivo phone
   (Tasker/MacroDroid/custom app) forwards incoming-message notifications. Sees sender + chat +
   text snippet but not muted chats, and media shows only "[图片]/[语音]" placeholders **[V]**.

**Platform choice for the desktop client:** the **official WeChat for Linux 4.1.x** is real and
current (4.1.13, 2026‑09‑04) **[V]** and its data dir is readable on the same Linux box, and Linux
stores the WCDB keys as plaintext in process memory (easiest key extraction of any platform)
**[V]**. That makes an all-Linux stack viable. A **Windows VM** is the most tool-rich fallback
(wxauto, most decryptors) but note VM/emulator logins carry elevated freeze/ban risk **[V]**.

**Explicitly NOT recommended as the backbone:** iPad/pad "协议" services and DLL hooks (high ban
risk, July 2025 ban wave) **[V]**; and the new official **OpenClaw/iLink "WeChat ClawBot"** — it is
real but is only a *dedicated bot conversation with yourself*; it cannot read your chats with other
people and (as of now) not group chats **[V]**. It does not meet goals 1–2.

### The 3–5 facts the decision hinges on

1. **A secondary desktop/iPad login does NOT contain full history** — only recent messages;
   complete history must be pushed in via **迁移/migration** from the phone **[V]**.
2. **WeChat 4.x desktop DBs are WCDB = SQLCipher 4** (AES-256-CBC, PBKDF2-HMAC-SHA512, 256k iters,
   4096 page). Keys are extractable on Linux (plaintext in `/proc/pid/mem`) and macOS
   (`all_keys.json` plaintext file, 4.1.11+); on Windows key-in-memory extraction **broke at
   4.1.13.65** (2026-09) and now needs downgrade or a newer method **[V]**.
3. **The vivo X Fold 6 cannot be rooted** (bootloader locked), and modern WeChat sets
   `allowBackup=false`, so direct on-phone DB extraction and `adb backup` are out **[V]**. The
   phone contributes history only via **migration to another device**, and live notifications only
   via **NotificationListenerService/Accessibility**.
4. **Passive local-DB reading ≈ low ban risk; hooks/protocols ≈ high ban risk** (July 2025 mass
   bans of hook/protocol bots) **[V]**. But all third-party access violates Tencent ToS and the DB
   tools are under active DMCA pressure **[V]**.
5. **The official iLink/ClawBot does not expose your existing conversations** — wrong tool for
   history/monitoring **[V]**.

---

## 2. Comparison tables

### 2a. History extraction / DB decryption tools

| Tool | Platforms | WeChat ver | History | Notify (incremental) | Send | Media decode | Maintenance (as of 2026-09-27) | Ban risk |
|---|---|---|---|---|---|---|---|---|
| `sjzar/chatlog` (original) | Win/mac (no Linux) | 3.x, 4.0–4.1.2 | Full (from local DB) | Yes (HTTP/MCP) | No | img/voice/video/file, wxgf via ffmpeg | **Code removed 2025-10-20** (Tencent letter); repo = notice only **[V]** | Low (passive) |
| `chatlog_alpha` (successor) | Win-focused fork *and* macOS fork (diverged mirrors) | 4.x (tested 4.1.5.30) | Full | Yes, 500 ms–3 s incremental + push to clawbot/Hermes | via push only | img/voice/video, zstd, OCR | Active; GitHub = notice, real code on gitflic/codeberg; v5.0.0 2026-09-24 **[V]** | Low (passive) |
| `xaoyaoo/PyWxDump` | Win | 3.x (4.0 WIP) | Full | Partial | No | img/voice | **Code removed 2025-10-20** **[V]** | Low |
| `LC044/WeChatMsg`(留痕/MemoTrace) | Win | 3.x; 4.0 closed-src | Full export | No | No | img/voice/video/stickers/files | 42k★ but "won't update"; 4.0 support is closed binary **[V]** | Low |
| `git-jiadong/wechatDataBackup` | Win | 3.x | Full export | No | No | broad | Apache-2.0; **in DMCA list**; pushed 2026-01-10 **[V]** | Low |
| `TANGandXUE/wcdb-key-tool` | **Linux** | 4.0 (mem), 4.1+ (gdb) | key only | — | — | — | Active, MIT, 610★, pushed 2026-08-04 **[V]** | Low |
| `ycccccccy/wx_key` | Win/mac(/Linux?) | 4.0+, ≤4.1.12 | key + image key | — | — | — | 1.8k★, **README gutted after DMCA** (2026-06) **[V]** | Low |
| `lizhun781021/wechat-db-analysis` | macOS | 4.1.x | Full + HTML report | No | img/voice/video | Small/new, active (2026-09-22) **[V]** | Low |
| `LifeArchiveProject/WeChatDataAnalysis` | Win + macOS | 4.x | Full export (HTML/JSON/TXT/Excel) | Yes ("闪电"实时刷新); HTTP API :10392 + MCP | Paid premium (write/auto-reply) | img/voice/video/stickers/files/cards | 3.4k★, active (pushed 2026-09-27) **[V]** | Low read / higher if write |
| `ylytdeng/wechat-decrypt` | Win/mac/**Linux** | 4.0 | Full | "real-time message monitor" | No | 3 img formats, SILK | Live but README 404 at check time **[U]** | Low |
| `hicccc77/WeFlow` | Win/mac **[U]** | 4.x | Full export + report | No | broad | 14.5k★, active (2026-08-28) **[V]** | Low |
| `nalzok/wechat-decipher-macos`; `cocohahaha/wechat-decrypt-macos` | macOS (DTrace) | 3.x/4.x | key/extract | No | — | **DMCA'd 2026-01-08**, takedown notice **[V]** | Low |

### 2b. Live receive/send automation

| Approach | Tool | Platform / WeChat ver | Receive (listen) | Send text/img/file/sticker | Needs visible desktop | Maintenance | Ban risk |
|---|---|---|---|---|---|---|---|
| UI automation | `cluic/wxauto` | Win, **3.9.x only** (default 3.9.11.17) | Yes (poll UI) | text/img/file/emoji | Yes | 7.3k★ Apache-2.0, pushed 2026-04-13; maintenance stopped 2025-10, does not work on 4.x **[V]** | **Low** |
| UI automation | `cluic/wxauto4` | Win 4.0 | — | — | Yes | 226★; marked **停止更新 / no longer updated** **[V]** | Low |
| UI automation (paid) | `wxautox4` (pypi) | Win **4.0.5 only**, beta | Yes (multithreaded listen) | text/files | Yes | v41.2.1 2026-09-23; license-key activation **[V]** | Low |
| UI automation (paid) | `wxautox`/Plus | Win 3.9.x | Yes (better) | + more | Yes | activation code, 1-yr updates **[V]** | Low |
| UI automation (send only) | `LAVARONG/wechat-automation-api` | Win 4.0+ (Qt) & 3.x | No (send only) | text/img | Yes | Flask+uiautomation, openclaw skill **[V]** | Low |
| UI automation | Linux xdotool/geometry (`oppositenum/wxbot`) | **Linux** WeChat 4.1.1 (Docker+VNC) | Yes, poll decrypted DB ~10 s → sub-second via WAL | via xdotool | Yes (X in container) | new/tiny (2026-09) **[V]** | Low-med |
| macOS AppleScript | `x5iu/wemac`, `ahaduoduoduo/openclaw-wechat-plugin` | macOS 3.0+ | via native notif + webhook | AppleScript UI | Yes | community **[V]** | Low |
| Hook / DLL inject | `lich0821/WeChatFerry` | Win 3.9.x (≤3.9.12.51) | Yes | text/img/file/xml/gif | No | **Archived 2026-07-10**; 风控 reports **[V]** | **High** |
| Hook | `ttttupup/wxhelper` | Win 3.8–3.9.5.81 | Yes | text/img/file | No | old (2023) **[V]** | High |
| Hook | `aixed/WeChat-Hook` | Win 3.9.10.16 & **4.1.10.27** | Yes | text/img, xml, img decode | No | open-src, active (2026-09-15), 2.1k★ **[V]** | High |
| Wechaty puppet | `wechaty/puppet-xp` | Win 3.9.x (≤3.9.10.27) | Yes | text/img/file/gif | No | pushed 2025-07 **[V]** | High |
| Wechaty puppet (pad) | `puppet-padlocal` | pad protocol (token, apply+review) | Yes | full | No | code 2023-07, docs 2025-12 **[V]** | Med-high |
| Wechaty puppet (web) | `puppet-wechat4u` (UOS web) | web protocol | Yes | text/img | No | 2024-05; frequent bans **[V]** | High |
| iPad/pad "协议" service | Gewechat/gewe | pad protocol | Yes | full | No | **stopped ~Feb 2025**, archive only **[V]** | High |
| iPad/pad "协议" service | WeChatPadPro; various SDK | pad 8061/8073 | Yes | full incl. moments/pay | No | v860 2025-07; paid support; ~¥800/update **[V]** | Med-high |
| Official | OpenClaw/iLink **ClawBot** | any (server-side) | **only the bot's own chat** | text/img/voice/file/video to bot chat | No | official, `@tencent-weixin/openclaw-weixin`, active 2026 **[V]** | Low (official) |

### 2c. Per-device history holdings

| Device / login | Holds full history? | Notes |
|---|---|---|
| vivo X Fold 6 (primary Android) | **Yes** (the source of truth) | Can't root; extract only by migration-out or notif listener **[V]** |
| Desktop secondary (Win/mac/Linux) | **No by default** — recent only | Full only after phone→PC **迁移** **[V]** |
| iPad secondary | **No by default** — "同步最近消息" only | Full only after **迁移** to iPad **[V]** |
| Web WeChat (wx.qq.com) | n/a | Most personal accounts blocked from web login **[V]** |

---

## 3. Detailed findings

### A. Desktop client DB extraction (the core of the plan)

**Clients & versions (all [V], from official update pages, 2026-09-27):**
- **Linux:** `linux.weixin.qq.com` — latest **4.1.13 (2026-09-04)**; also 4.1.1 (2026-03-10),
  4.1.0 (2025-10-31). deb/rpm/AppImage; x86, ARM, LoongArch. The official Linux client is
  confirmed real and current (answers a task open question). Qt-based.
- **Windows:** latest **4.1.15**; **macOS:** **4.1.15 (2026-09-15)**.

**Data directory & DB layout (WeChat 4.x) [V]:**
- Windows: `C:\Users\<u>\Documents\xwechat_files\wxid_xxx\db_storage`.
- Linux: `~/Documents/xwechat_files/<account>/` (or `~/xwechat_files` under Flatpak sandbox);
  `db_storage/` for DBs, `msg/` for media.
- macOS: `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<account>/`.
- `db_storage/` files: `message_0.db … message_N.db` (sharded), `biz_message_*.db` (official
  accounts), `message_fts.db` (search index), `message_resource.db` (msg→attachment),
  `contact/contact.db`, `contact_fts.db`, `session/session.db`, `hardlink/hardlink.db` (media
  dedup/path index), `media/media_*.db` (voice etc.), plus sns/favorite/emoticon DBs.

**Encryption [V]:** WCDB, SQLCipher-4 compatible. AES-256-CBC, `cipher_page_size=4096`,
PBKDF2-HMAC-SHA512 **256,000** iterations, per-DB 16-byte salt, HMAC-SHA512 page auth, WAL
journalling. Real page key = PBKDF2(passphrase, salt). WCDB caches the derived raw key in memory as
`x'<64hex_key><32hex_salt>'`.

**Key extraction, per platform (all [V], dated):**
- **Linux — easiest.** Keys sit as plaintext `x'<hex>'` in process memory; read `/proc/<pid>/mem`
  (needs root or `ptrace_scope=0`). `TANGandXUE/wcdb-key-tool` (MIT, 610★, 2026-08-04) automates
  this: ELF static analysis to find the WCDB cipher function, gdb breakpoint at logout/login to
  capture the passphrase, then PBKDF2 to derive per-DB keys. Supports 4.0 (mem scan) and 4.1+ (gdb).
- **macOS.** 4.0.3.80 and earlier: from process memory (chatlog). **4.1.11+: `all_keys.json`** —
  a *plaintext* JSON in the account dir with each DB's 64-hex key; no memory scan or SIP-disable
  needed (`lizhun781021/wechat-db-analysis`, `all_keys.json` reports).
- **Windows.** 4.0.x–4.1.12: key extractable from `Weixin.exe` memory (ReadProcessMemory). **Broke
  at 4.1.13.65 (2026-09):** derived key is secure-zeroized / held via Windows CNG handles / derived
  on-stack once, so string search, salt reverse-lookup, full-memory KP-AES scan, and Frida hooks all
  failed (`stargazer-2026/.../issues/2`, 2026-09-23). Workaround: extract on ≤4.1.12 (or downgrade),
  key is stable within a major version, then upgrade.
- Manual path that still works everywhere: get the 64-hex key with an external tool, paste it into a
  chatlog-style UI to decrypt (`sjzar/chatlog#317`, 4.1.2.17, 2025-11-03).

**Media decoding [V]:**
- **Images `.dat`:** v3 (3.x) = XOR with single byte. v4 (4.0.3+) = AES-128-ECB over the head +
  XOR tail (≤1 MB encrypted), **per-account image key** (16 ASCII chars of a hexdigest). Header
  magic `0x07085631` (V1 fixed key) / `0x07085632` (V2 per-account). Detect JPG/PNG/GIF by
  post-decrypt magic.
- **`wxgf`/`wxam`:** WeChat's HEVC(H.265)-wrapped image/sticker container, magic `'wxgf'`; convert
  with **ffmpeg** to JPG/GIF (fallback: repackage as MP4). (sarv.blog 2025-08-19)
- **Voice:** SILK v3, stored in `media_*.db`. Decode with `kn007/silk-v3-decoder` (MIT, 3.2k★) or
  `foyoux/pilk` (GPL-3, python) → mp3/m4a.
- **Video:** MP4 in `msg/`. **Files:** in `msg/`, located via `hardlink.db` + `packed_info_data`.
- **Stickers (type 47):** custom emoji reference a **CDN URL + md5** inside the message XML.

**HTTP API / MCP / incremental:** `chatlog`/`chatlog_alpha` expose an HTTP JSON API + **MCP (SSE)**
server and do incremental decryption (500 ms–3 s) with push (to clawbot/Hermes) **[V]**.
`LifeArchiveProject/WeChatDataAnalysis` exposes HTTP :10392 + MCP + real-time refresh **[V]**.
`sjzar/chatlog` had no Linux support **[V]**, but `wcdb-key-tool` + a chatlog fork or
`ylytdeng/wechat-decrypt` cover Linux DB reading.

**How much history a secondary desktop login holds — and the migration answer (KEY) [V]:**
- Login as secondary syncs **recent** messages only (community: PC 4.0 sometimes only ~1 day, and
  incomplete). New messages arrive live while online. Tencent's own statement: on login/standby
  it auto-syncs "recent chat content" between the user's own devices; servers don't persist
  messages, no stated retention.
- **迁移聊天记录 (migration)** = copies chat history from phone to the PC/other device over the same
  Wi‑Fi and **merges** it into the target's live DB → this is the route to a **complete** desktop DB
  that a decryptor can read. (微信 → 设置 → 聊天 → 聊天记录管理/迁移与备份, 2025+ UI renamed to
  "聊天记录管理 / 导入与导出".)
- **备份与恢复 (backup)** = writes an **encrypted proprietary blob**, not the live readable DB.
  Windows 4.0 backup format = `RMFH` header (128-byte head/tail markers) + `ChatPackage` (protobuf)
  + media in `tar.enc`; encryption AES-256-GCM, key pulled from Android `libaff_biz.so` at runtime
  via Frida in one researcher's teardown (freebuf/ctfiot 2025) — i.e. decryptable in principle but
  much harder than reading the migrated live DB. **Prefer migration over backup.**
- 2025-07 WeChat officially upgraded backup/migration: export chat history to a computer, another
  phone/tablet, or **external storage (USB/HDD)**, and (later) to HTM/Word/TXT/Excel via the
  official "聊天记录管理 → 导入与导出" — but the device-to-device/computer export is still a
  restore-only proprietary format, not per-chat readable files **[V]**. Worth re-testing on the
  owner's current version (see experiment plan).

**Message schema for implementation (see §F).**

### B. Android without root

- **vivo X Fold 6: bootloader locked → cannot root [V/given].** So `/data/data/com.tencent.mm`
  is inaccessible directly.
- **EnMicroMsg.db (the 3.x-era Android DB):** SQLCipher, password = `md5(IMEI+uin)[:7]`, page 1024,
  KDF 4000, HMAC off. Since WeChat 6.0 the app doesn't request `READ_PHONE_STATE`, so when IMEI is
  unavailable it falls back to the **fixed string `1234567890ABCDEF`**; this rule held 8.0.27→8.0.69
  (2022→2026) **[V]**. The algorithm is unchanged; only the IMEI source changed. **But you still
  need the DB file**, which needs root/backup access you don't have. (Also, current WeChat 8.x
  Android storage differs from the classic EnMicroMsg layout — **[U]** whether EnMicroMsg is still
  the live store on 2026 builds.)
- **`adb backup`:** WeChat sets `allowBackup=false` since 6.0 → produces empty/1 KB `.ab` → **not
  viable [V]**.
- **Manufacturer no-root backup:** Xiaomi local `.bak`, vivo **互传 (EasyShare)** "一键换机"/PC
  backup (file prefixed `5a…`, unpack → `apps/com.tencent.mm`). Community claims these can include
  WeChat private data on some brands **[V]**, but vivo's *official* docs describe only device→device
  migration and 互传, and **do not** state that a PC backup includes WeChat app data **[V]** →
  treat vivo no-root DB export as **[U]/unreliable**; must be tested on this exact phone.
- **Migration to a rooted Android emulator/spare rooted phone (viable route for a full export):**
  Use WeChat's **迁移聊天记录 to another device** into a rooted AVD / MuMu / LDPlayer / 雷电 /
  Waydroid or a spare rooted phone, then read `/data/data/.../EnMicroMsg.db` (or the 4.x/8.x store)
  there **[V]**. Caveats: WeChat risk-control has tightened on emulators/tablets — **freeze/ban
  risk on emulator login is elevated [V]**; and migration transfers only the conversations you
  select. This is a one-shot bulk-history route, not a sync mechanism.
- **`WechatBackupNoRoot` (DarkLineX):** no-root export via **Accessibility automation**, Android
  WeChat 8.0.0–8.0.32 (Xiaomi example), GPL-3, last push 2024-03 → dated; likely broken on 2026
  builds and vivo **[V]**.
- **No-root live capture (best phone option):**
  - **NotificationListenerService** (Tasker+AutoNotification, MacroDroid, or a small custom app):
    reads `android.title` / `android.text`. Private chat: title=contact, text=message. Group:
    title=group name, text=`昵称: 内容`. **Limitations [V]:** muted (消息免打扰) chats emit **no**
    notification; multiple msgs collapse to `[N条]`/"N条新消息"; images/voice/video show only
    `[图片]/[语音]/[视频]` placeholders (no content); red packets show `[微信红包]`. Good enough for
    "who/where/text snippet," not for media or muted chats.
  - **AccessibilityService** can read on-screen text (more content, incl. some previews) but is more
    fragile and more invasive; heavier battery, higher OriginOS-kill exposure.
  - **vivo/OriginOS background-kill [V]:** must set Autostart (自启动), Battery → "不受限制/后台高
    耗电允许", lock the app in recents, exclude from battery optimization, and permit notification
    access; even then listener/accessibility services can be killed. Plan for a watchdog/restart and
    accept gaps. (dontkillmyapp/vivo; CSDN 保活教程 2025.)

### C. iPad secondary login

- **iOS/iPadOS WeChat DB is NOT SQLCipher-encrypted — `MM.sqlite` message bodies are in the clear
  [V]** (confirmed again by iLEAPP WeChat-iOS support merged **2026-09-22**). Chat tables:
  `Chat_<md5(wxid)>`, split across `MM.sqlite`, `message_1.sqlite`, `message_2.sqlite…`; contacts in
  `WCDB_Contact.sqlite` `Friend` table. Path: app domain `com.tencent.xin` → `Documents/<id>/DB/`.
- **Extraction:** make an **unencrypted** Finder/iTunes backup of the iPad, then parse it.
  `BlueMatthew/WechatExporter` (GPL-2, tested to WeChat **8.0.9**) exports text/img/**video**/emoji
  and converts **SILK→mp3**; output Text/HTML/PDF. Last push 2025-02-26 and it is **on the DMCA
  list** but the technique (read cleartext SQLite from a local backup) is stable **[V]**.
  `iLEAPP` now parses it too **[V]**.
- **[U] for 2026 builds:** all sources confirming "in the clear" are ≤8.0.9 / general; I did not
  find a source explicitly testing a 2026 iPad WeChat build, so treat "still unencrypted on the
  newest iPad WeChat" as **likely but unverified** — test on the owner's iPad.
- **How much history an iPad secondary login holds [V]:** default login syncs "同步最近消息" only
  (recent, unspecified count) — **not** full history. Full history onto the iPad requires **迁移**
  from the phone. So iPad-backup extraction gives you exactly what the iPad holds; to make that the
  complete archive you must migrate first (same trade-off as desktop, but with a cleartext DB that
  is trivial to read — attractive for a periodic full snapshot).

### D. Live receive / send automation

**D.1 Windows UI automation — `wxauto` / `wxautox` [V]:**
- `cluic/wxauto` (7.3k★, Apache-2.0): Win10/11/Server, default WeChat **3.9.11.17**; listen for new
  messages (`AddListenChat`/`GetListenMessage`), send text/img/file/emoji; get history. Requires a
  visible, logged-in desktop (UIAutomation); no minimize; RDP/lock-screen needs handling.
- **WeChat 4.x breaks UIAutomation [V]:** 4.0.5 migrated from Qt Widgets to Qt Quick/QML, controls
  no longer have HWNDs and are drawn onto one OpenGL canvas, so UIA "can only see an empty shell
  window" (yage.ai feasibility survey, 2026-03-27). Consequently `wxauto` stopped maintenance in
  2025-10 and stays 3.9.x-only; `cluic/wxauto4` (226★) carries a **停止更新 / no longer updated**
  notice (checked 2026-09-27).
- `wxautox4` (pypi, v41.2.1 2026-09-23) is the **paid** 4.x path: "仅适用于微信 4.0.5 版本客户端"
  (4.0.5 only), "当前为 4.0 Beta 版本" with no stability guarantee, license-key activation;
  multithreaded message listening, `SendMsg`, `SendFiles` **[V]**. `wxautox`/Plus for 3.9.x is
  likewise paid (activation code, 1-yr updates). Both are explicitly **non-hook/non-protocol** →
  **low ban risk**; won't do transfers/red packets.
- Survey-recommended alternatives on 4.x Windows: screenshot + OCR + coordinate clicking for
  send (visible content only), and DB decryption for reading **[V]**. `LAVARONG/wechat-automation-api`
  claims 4.0+ send support but the survey calls its long-term reliability doubtful **[U]**.
- Practical implication: if UI-automated sending matters, either pin a Windows VM to WeChat
  **3.9.x** for `wxauto` (older client, may be force-upgraded), pay for `wxautox4` on 4.0.5, or use
  macOS Accessibility (D.5) / Linux xdotool (D.6). Test before committing to any of them.
- Real-time caveat: pure UI polling isn't truly instant and can miss bursts; pairing wxauto (send)
  with DB polling (receive) is more robust than wxauto for both.

**D.2 Hook / DLL injection [V]:** `WeChatFerry` (**archived 2026-07-10**, 3.9.x, 风控 reports),
`wxhelper` (old, 3.8–3.9.5.81), `aixed/WeChat-Hook` (open-source, active, supports **4.1.10.27** &
3.9.10.16, local HTTP :30001, msg hook + send + img decode + in-proc SQLite). Powerful (receive
*and* send incl. all your chats) but **high ban risk**, breaks on every client update.

**D.3 Wechaty ecosystem 2026 [V]:** Wechaty core alive (23k★, 2025-12). Puppets:
`puppet-xp` (Win 3.9.x, 2025-07); `puppet-padlocal` (pad protocol, token by application+review,
code 2023-07 but docs 2025-12 — semi-alive, med-high ban risk); `puppet-wechat4u` (web/UOS,
2024-05, frequent bans). No puppet is a clean, safe, current option.

**D.4 iPad/Mac "协议" third-party services [V]:** **Gewechat/gewe stopped ~Feb 2025** (archive
only, cited enforcement against illegal terminal-data acquisition). `WeChatPadPro` and numerous
paid iPad/pad SDKs remain (pad 8061/8073, ~¥800/update, warn of 24 h new-account disconnect,
1–7 day high-risk-op limits, escalating bans). Cost + ban risk make these unsuitable for a personal
main account.

**D.5 macOS automation [V]:** AppleScript/Accessibility via System Events (needs Accessibility
permission); `x5iu/wemac` (RPA group bot), `ahaduoduoduo/openclaw-wechat-plugin` (native
notification + webhook to receive, AppleScript to send). Reliable for send; receive best via native
notifications or DB polling. macOS 4.1.x key via `all_keys.json`.

**D.6 Official Linux client [V]:** No mature dedicated automation project yet. Working pattern
(from `oppositenum/wxbot`, 2026-09, tiny/new): run Linux WeChat 4.1.x in Docker + VNC, read keys
from `/proc/pid/mem`, poll the decrypted DB (WAL, ~sub-second) for receive, and **xdotool** with
fixed window geometry for send. This is the most self-contained all-Linux stack but is immature.

**D.7 Official OpenClaw / iLink "WeChat ClawBot" — VERIFIED, and it does NOT fit [V]:**
- Real: `Tencent/openclaw-weixin` (official), npm `@tencent-weixin/openclaw-weixin`; underlying
  **iLink** protocol on `ilinkai.weixin.qq.com` (Tencent's own server). QR-login; HTTP long-poll
  `getupdates` (35 s hold), `sendmessage`; text/img/voice/file/video + typing; media AES-128-ECB on
  CDN. Needs OpenClaw ≥ 2026.x, Node ≥22.
- **Scope:** the bot appears as **a single new contact in your WeChat**; you talk to *it*. It only
  receives messages **sent to that bot chat**; it **cannot read your conversations with other
  people**, and **group chat is not supported** (feature request issue #327 open, no maintainer
  reply, 2026-09-27). Docs: "direct chats and media supported; group chats not advertised."
- **Conclusion:** great for "an AI assistant you DM inside WeChat," useless for goals 1 (full
  history) and 2 (monitor arbitrary chats). Do not build chatmaid's core on it. (Community wrappers:
  `NewFuture/openclaw-weixin`, `webheat/weixin-ClawBot-API`, `x1ah/wechat-ilink-demo` — same limits.)

### E. Account-ban risk

- **What gets banned (community-observed) [V]:** **July 2025 mass ban wave** hit hook- and
  reverse-protocol-based bots hard (accounts "功能限制"/"永久封禁"; landiannews warning to stop
  running ChatGPT-on-WeChat-style bots). Detection uses environment fingerprinting, process
  enumeration (remote-control/automation tools), and behavioral signals (mouse tracks, click
  cadence). Emulator/VM/tablet logins carry elevated **freeze** risk; one report: KVM Windows VM
  login banned after ~5 months.
- **Risk ranking [V/I]:** protocol/iPad bots ≈ hooks (**highest**) > Wechaty pad/web > VM/emulator
  login (elevated freeze) > UI automation (**low**, mimics human) > **passive local-DB reading
  (lowest ban risk** — no WeChat API calls; all work happens on local files after key extraction).
- **Mitigations [V]:** use a real second device / real desktop rather than emulator where possible;
  rate-limit sends (wxauto guidance ≥5 s intervals); avoid any protocol/hook tool on the main
  account; keep device/IP consistent; never automate payments/red packets. **Compliance risk is
  separate from ban risk** — the DB tools are ToS-violating and under DMCA even though passive.

### F. Storage-format details for implementation

**Message tables (WeChat 4.x, `message_N.db`) [V]:**
- Table name per contact: `Msg_` + `md5(username).hex()`. Same contact can appear across multiple
  `message_N.db` shards → query each shard; `rowid`/`Name2Id` mapping differs per DB.
- Columns: `local_id` (PK), `server_id`, `local_type`, `sort_seq`, `real_sender_id`
  (→ `Name2Id.rowid`), `create_time` (Unix seconds), `message_content`, `compress_content`,
  `packed_info_data`, `status`.
- **zstd:** ~40% of rows zstd-compressed; indicated by `WCDB_CT_message_content = 4` → decompress
  before parsing.
- Helper tables: `Name2Id` (username↔sender id within that DB), `TimeStamp`.
- Group sender: `real_sender_id` → `Name2Id` gives the member wxid; also content may be prefixed
  `wxid_xxx:\n<content>`.

**`local_type` values (use `local_type & 0xFFFF`) [V]:**
`1` text · `3` image · `34` voice · `42` name card (XML) · `43` video · `47` sticker/emoji
(custom/GIF, CDN url+md5 in XML) · `48` location (XML) · `49` **appmsg container** · `50`
voice/video call record (XML) · `10000` system (recall, group notices) · `11000` system
notification · `66/67` WeCom card.

**Type 49 appmsg XML subtypes (`<appmsg><type>`) [V]:**
`4/6` file (send/recv) · `5` link / official-account article · `19` merged-forward chat records ·
`33/36` mini-program · `51/63` video channel (视频号) · `53` group announcement · `57` **quote/
reference** · `76/101` music · `2000` transfer (**[U]** in one source's list) — the inner XML
`<type>` disambiguates; `message_content` here is usually zstd.

**Media reference resolution [V/I]:** image/voice/video/file rows carry `packed_info_data` and md5s;
resolve to files under `msg/` via `hardlink.db` (path/dedup index) and `message_resource.db`. Images
are `.dat` (decrypt per §A). Voice is SILK in `media_*.db`.

**Contacts / sessions [V]:** `contact.db` `contact` table (username, nickname, remark, avatar,
verify flag; group ids end `@chatroom`, `chat_room` table for members); `session/session.db` for
the conversation list / ordering.

**Note on obfuscation [V]:** several 4.x business `.db` files don't begin with the `SQLite format
3` header (random/nonstandard page 0) — expected for SQLCipher/WCDB; use a SQLCipher-4 reader with
the correct key + params, not plain sqlite3.

---

## 4. Open questions & experiment plan

**Open questions (need testing on the owner's actual account/devices):**
1. Does the owner's WeChat version still keep a readable **live DB after migration** on Linux 4.1.13,
   and does `wcdb-key-tool` + a chatlog fork decrypt it end-to-end (incl. v4 image keys)? **[U]**
2. On **Windows 4.1.15**, is key-in-memory extraction still broken (it broke at 4.1.13.65)? If so,
   is downgrade-to-extract acceptable? **[U]**
3. Is the **2026 iPad WeChat** DB still cleartext `MM.sqlite`? (Very likely, unverified for newest.) **[U]**
4. Will the **vivo 互传/PC backup** actually include WeChat private data on the X Fold 6? (Doubtful.) **[U]**
5. Registration year: does it affect **web-WeChat** eligibility or migration limits? Not established. **[U]**
6. Does the official **"聊天记录管理 → 导出到电脑"** produce anything more parseable than a
   restore-only blob on the current version? **[U]**

**Experiment plan (do in this order, on a test/secondary basis first):**
1. **Baseline live DB (lowest risk):** Install official **WeChat for Linux 4.1.13** on the dev box,
   log in as secondary. Confirm data dir `~/Documents/xwechat_files/<account>/db_storage`. Run
   `wcdb-key-tool` (root/`ptrace_scope=0`) to get keys; open one `message_*.db` with a SQLCipher-4
   reader (page 4096, PBKDF2-HMAC-SHA512, 256k). **Success metric:** read recent text messages.
2. **Full history import:** From the vivo phone, run **迁移聊天记录 → 到电脑/本设备** over Wi‑Fi
   into that Linux client; re-open the DB and confirm old messages now present. **Metric:** message
   count jumps to ~full history; spot-check oldest messages.
3. **Media pipeline:** Decode a `.dat` image (v4 AES-ECB+XOR, per-account key), a SILK voice
   (silk-v3-decoder → mp3), a `wxgf` sticker (ffmpeg → gif), and resolve a file via `hardlink.db`.
4. **Incremental notify:** Poll the WAL/DB every 1–3 s; detect a newly received message; emit
   `{chat, sender, type, content/media-ref, ts}`. **Metric:** < 5 s latency, no missed messages
   over a 1-hour test.
5. **Send path:** Try `wxauto4`/`wxautox4` (if using a Windows VM) or Linux xdotool geometry send;
   send text + image to a test chat. **Metric:** reliable send, ≥5 s spacing, no UI desync.
6. **Phone fallback notify:** Configure NotificationListenerService (Tasker/MacroDroid) on the vivo,
   plus OriginOS keep-alive; verify it forwards sender+chat+snippet for a normal chat and note the
   muted-chat / media-placeholder gaps. **Metric:** captures non-muted text within seconds.
7. **iPad snapshot (optional):** Unencrypted Finder backup → parse `MM.sqlite`/`Chat_*` with
   WechatExporter/iLEAPP; compare completeness vs. desktop after an iPad migration.
8. **Risk monitoring:** Run steps 1–5 on the account for 2–4 weeks watching for any security prompt;
   keep sends human-paced; never introduce hook/protocol tooling on the main account.

**Proposed architecture (from findings):** one always-on desktop client (Linux 4.1.13 preferred, or
Windows VM for wxauto) → key-extract once per version → **DB-poller service** (decrypt + zstd +
media resolve) emitting normalized events over an internal API/MCP → chatmaid core (suggest replies)
→ **UI-automation sender**. Phone NotificationListener as a secondary, low-fidelity notify source.
Do a one-time phone→desktop migration for history, and re-migrate periodically to capture anything
the live sync missed.

---

## 5. References (accessed 2026-09-27)

Official / primary:
- WeChat for Linux — https://linux.weixin.qq.com/ ; updates https://weixin.qq.com/updates?platform=linux (4.1.13, 2026-09-04)
- WeChat updates (mac) — https://weixin.qq.com/updates?platform=mac (4.1.15, 2026-09-15)
- Tencent auto-sync statement — https://support.weixin.qq.com/cgi-bin/mmsupport-bin/newreadtemplate?t=wechat_agreements%2Fpage%2Fchat-record%2Fprivacy
- GitHub DMCA (Tencent) — https://github.com/github/dmca/blob/master/2026/01/2026-01-08-tencent.md
- Tencent/openclaw-weixin — https://github.com/Tencent/openclaw-weixin ; docs https://docs.openclaw.ai/channels/wechat ; group-chat req https://github.com/Tencent/openclaw-weixin/issues/327 ; iLink API https://github.com/hao-ji-xing/openclaw-weixin/blob/main/weixin-bot-api.md ; demo https://github.com/x1ah/wechat-ilink-demo
- vivo WeChat backup/migration — https://www.vivo.com.cn/service/questions/all?categoryId=155&questionId=1175

DB / key extraction:
- sjzar/chatlog (removed) — https://github.com/sjzar/chatlog ; FAQ https://github.com/sjzar/chatlog/issues/197 ; #317 https://github.com/sjzar/chatlog/issues/317
- chatlog_alpha — https://github.com/teest114514/chatlog_alpha ; https://codeberg.org/lanticy/chatlog_alpha ; gitcode mirror https://gitcode.com/gh_mirrors/ch/chatlog_alpha
- wcdb-key-tool (Linux) — https://github.com/TANGandXUE/wcdb-key-tool
- ycccccccy/wx_key — https://github.com/ycccccccy/wx_key
- lizhun781021/wechat-db-analysis — https://github.com/lizhun781021/wechat-db-analysis
- LifeArchiveProject/WeChatDataAnalysis — https://github.com/LifeArchiveProject/WeChatDataAnalysis
- ylytdeng/wechat-decrypt — https://github.com/ylytdeng/wechat-decrypt
- hicccc77/WeFlow — https://github.com/hicccc77/WeFlow
- Win 4.1.13.65 key-break analysis — https://github.com/stargazer-2026/wechat-4.1.12-decrypt/issues/2
- WeChat 4.0 DB decrypt analysis — https://bbs.kanxue.com/thread-284417.htm
- 4.x DB schema / message types — https://welink.click/database.html ; https://welink.click/message-types.html
- image .dat / wxgf — https://upxuu.com/posts/wechat-dat-image-decrypt/ ; https://sarv.blog/posts/wxam/ ; https://blog.csdn.net/weixin_39441881/article/details/157291104
- SILK — https://github.com/kn007/silk-v3-decoder ; https://github.com/foyoux/pilk
- phone-backup RMFH/AES-GCM teardown — https://www.freebuf.com/articles/456327.html ; https://www.ctfiot.com/275982.html

Automation / send-receive:
- wxauto — https://github.com/cluic/wxauto ; docs https://docs.wxauto.org/ ; wxauto4 https://github.com/cluic/wxauto4 ; wxautox4 https://pypi.org/project/wxautox4/
- LAVARONG/wechat-automation-api — https://github.com/LAVARONG/wechat-automation-api
- WeChat UIA feasibility survey (4.0 Qt Quick, OCR fallback) — https://yage.ai/share/wechat-uia-platform-feasibility-survey-en-20260327.html
- oppositenum/wxbot (Linux) — https://github.com/oppositenum/wxbot
- WeChatFerry — https://github.com/lich0821/WeChatFerry (archived) ; 风控 issue https://github.com/lich0821/WeChatFerry/issues/422
- wxhelper — https://github.com/ttttupup/wxhelper ; aixed/WeChat-Hook — https://github.com/aixed/WeChat-Hook
- Wechaty — https://github.com/wechaty/wechaty ; puppet-xp https://github.com/wechaty/puppet-xp ; puppet-padlocal https://wechaty.js.org/docs/puppet-providers/padlocal ; puppet-wechat4u https://github.com/wechaty/puppet-wechat4u
- Gewechat (stopped) — https://github.com/Devo919/Gewechat/issues/157 ; WeChatPadPro — https://github.com/WeChatPadPro/WeChatPadPro
- macOS — https://github.com/x5iu/wemac ; https://github.com/ahaduoduoduo/openclaw-wechat-plugin

Android / iOS / notifications / ban risk:
- EnMicroMsg key (fixed IMEI) — https://www.gm7.org/archives/155329 ; https://blog.csdn.net/longxintec/article/details/149046794
- no-root export — https://github.com/DarkLineX/WechatBackupNoRoot ; https://www.louyue.com/vivowechat.htm
- iOS WechatExporter — https://github.com/BlueMatthew/WechatExporter ; iLEAPP WeChat iOS PR https://github.com/abrignoni/iLEAPP/pull/2250
- NotificationListenerService WeChat — https://blog.csdn.net/dzy_mails/article/details/86482225 ; https://cloud.tencent.com/developer/article/2162726 ; group monitor https://linux.do/t/topic/127599
- vivo keep-alive — https://dontkillmyapp.com/vivo ; https://blog.csdn.net/m0_73782257/article/details/155732654
- ban wave / risk — https://www.landiannews.com/archives/97103.html ; https://wechatapi.net/blog/91-2026%E5%BE%AE%E4%BF%A1%E6%9C%BA%E5%99%A8%E4%BA%BA%E6%96%B9%E6%A1%88%E7%9B%98%E7%82%B9.html ; VM login ban https://v2ex.com/t/811646
- iPad/desktop sync scope — https://www.zhihu.com/question/2093581161 ; https://www.sohu.com/a/899919222_122004016

Key Chinese terms: 聊天记录迁移 (chat-history migration, merges into target device) · 备份与恢复 /
备份与迁移 / 聊天记录管理 → 导入与导出 (backup/export, restore-only blob) · 同步最近消息 (sync
recent messages only) · 密钥 (key) · 解密 (decrypt) · 风控 (risk control) · 封号 (account ban) ·
免root (no-root) · 保活 (keep-alive) · 协议 (protocol bot) · 注入/Hook (injection) · 表情 (sticker).
