# WeChat one-shot complete-history extraction (chatmaid)

Research date: **2026-10-21**. Extends `docs/research/wechat-access.md` (2026-09-27); read that first for
the tool landscape, DB/key/media format details, ban-risk analysis, and the DMCA context. This file
answers the *one-time full-history extraction* questions and does **not** repeat the general survey.

Legend: **[V]** verified from a source I read · **[I]** inferred from verified facts · **[U]** uncertain /
not confirmed. Dates in parentheses are the source's own date. Everything here concerns the owner's **own**
account and **own** devices; it is still ToS-contested (see prior doc's legal note).

Owner setup recap (from coordinator probes): primary WeChat on unrooted vivo X Fold 6 (WeChat 8.0.6x+);
Arch Linux dev box with **no QEMU/libvirt and no WeChat-Linux client installed yet**; a MacBook
(**macOS 27.0.1, WeChat.app 4.1.15, already a live secondary login**, reachable over SSH) that the owner
would rather not depend on but which is the current state of the world.

---

## 0. What changed vs the prior doc (corrections + new facts)

1. **`all_keys.json` is NOT a WeChat-produced file — the prior doc was wrong.** [V] It is the *output* of a
   third-party memory-extraction program: `find_all_keys_macos` in the `ylytdeng/wechat-decrypt` project
   writes `all_keys.json` for its `decrypt_db.py` to consume. The coordinator's probe of the owner's clean
   Mac (WeChat not yet poked by any tool) found **no** `all_keys.json` or `*keys*.json` anywhere under
   `~/Library/Containers/com.tencent.xinWeChat` — exactly as expected, because no extraction tool has run.
   macOS 4.x does **not** persist DB keys in cleartext on disk; the key lives only in process memory while
   WeChat runs. Treat "macOS = easy plaintext key file" as **false**. (wechat-decrypt README; mighil.com
   macOS key write-up, both read 2026-10-21.)
2. **macOS key extraction today = live memory read, not a file grab.** [V] On 4.x the key is raw bytes in
   memory; string/salt searches fail. Working method: WeChat **running and logged in** → ad-hoc re-sign a
   copy of `WeChat.app` with `codesign` → `task_for_pid` to read its memory → scan for WCDB key candidates,
   validate each against SQLCipher-4 page-1 HMAC. **SIP does not need to be disabled** (wxkey); Full Disk
   Access for the terminal + one admin prompt are needed. Re-signing disables WeChat auto-update. (See §5.)
3. **The owner's Mac currently holds *recent-only* history, not the full archive.** [I from probe] Its
   `db_storage/message/message_0.db` is only ~47 MB and `Backup/<account>/` is **empty**; `msg/` is 1.4 GB.
   That is the signature of a secondary login that has synced recent messages but never received a
   phone→PC **migration**. A migration from the phone is still required for completeness (same conclusion
   as the prior doc, now confirmed on the actual device).
4. **The probe confirms the 4.x macOS account-dir layout**, including a sticker DB: `db_storage/`
   (`message/message_0.db`, `message_fts.db`, `message_resource.db`, `biz_message_0.db`, `media_0.db`,
   `weclaw.db`), `contact/`, `session/`, **`emoticon/emoticon.db`**, `hardlink/`, `general/`, `MMKV/`,
   `favorite/`, `sns/`, `chatbot/`, plus `msg/` for media and an (empty) `Backup/`. [V-probe]
5. **The official "导出聊天记录 to HTML/Word/TXT/Excel" feature does not exist in native WeChat as of
   Oct 2026.** [V] Native WeChat offers only (a) **迁移/导出到电脑** = merge into another device's live DB,
   and (b) **备份到电脑** = an encrypted restore-only blob. HTML/Word/Excel output comes **only from
   third-party tools** (WeChatMsg/留痕, WeFlow, WechatExporter, WeChatDataAnalysis). Multiple 2026 Chinese
   sources agree the native backup is "加密备份，只能恢复回微信查看". (cloud.tencent 2026-07; juejin; ifanr.)
6. **Phone-side feature is named by version:** older UI = `设置 → 聊天 → 聊天记录迁移与备份 → 迁移 →
   迁移到电脑微信`; 2025+/HarmonyOS UI = `设置 →（通用 →）聊天记录管理 → 导入与导出 → 导出到电脑`. Both do
   the same thing: merge history into the target PC client's live DB. [V] (Huawei support zh-cn01118496;
   sspai 2024-10-05.)

---

## 1. Recommended one-shot procedure

**Strategy.** Do a one-time phone→desktop **migration** (not backup) to make a desktop client hold the
*complete* history, then extract the WCDB key on that desktop and decrypt `db_storage` + resolve `msg/`
media locally. Two desktops are viable; pick by a 10-minute key-extraction test, because the only real
unknown is whether today's key tools handle the exact client build:

- **Path A — Mac-first (fastest to attempt; data and login already present).** Recommended to *try first*
  precisely because the Mac already has a live 4.1.15 login and 1.4 GB of media; you only add the migration
  and a key extraction. Risk: macOS key tools may lag 4.1.15 (see §5).
- **Path B — official Linux client 4.1.13 (easiest key extraction; cleanest end state).** Key is plaintext
  in `/proc/<pid>/mem`, trivially grabbed with `wcdb-key-tool`. Risk: (i) uses the single "PC" login seat
  (ii) **unconfirmed** whether the phone can target the Linux client for migration (§3, §6).
- Keep the **Windows VM** (§5/alt-table) as the heavy fallback with the richest decrypt/export tooling.

> **Do not run Path A and a Windows-VM login on the same account at the same time** — both occupy the one
> "电脑/PC" login seat and the second will log the first out (§3). Finish one desktop fully before switching.

### 1a. Owner does — on the phone (vivo X Fold 6)
1. **Pre-stage media so nothing is skipped.** [V, important] Images/videos **not opened or downloaded in
   the last 7 days are dropped by migration** (not even a thumbnail survives). Verbatim from Huawei support
   zh-cn01118496 (re-read 2026-10-21): "微信聊天记录中，超过7天以上未打开查看或未下载的图片/视频迁移后是无法查看
   的(缩略图也不显示)，请在平时注意保存、收藏。" The sentence sits in the phone→phone 迁移 section; the
   导出到电脑 steps on the same page carry no such note, so for phone→PC treat it as **[I] very likely the
   same rule** (the phone can only transfer media it holds locally). Before migrating, open the important
   chats and let media load, or "收藏"/save the must-keep items. Note migration also **excludes** 通话记录
   (call logs) and 公众号 chats: "不包括微信通话记录、公众号的聊天记录等信息". [V]
2. Put phone and the chosen desktop on the **same Wi-Fi/LAN**. [V]
3. Make sure the **same account is logged in on the target desktop** first. [V] (Mac already is; for Linux
   you must install + log in first — see 1b.)
4. Launch migration: `我 → 设置 → 聊天 → 聊天记录迁移与备份 → 迁移 → 迁移到电脑微信` (or, on the 2025+ UI,
   `我 → 设置 →（通用 →）聊天记录管理 → 导入与导出 → 导出到电脑`). **Choose 迁移/导出, not 备份.** [V]
5. Select **全部聊天记录** (or specific chats) → start → **confirm the pop-up on the desktop**. Wait for
   "完成". This *merges* into the desktop's live DB. [V]

### 1b. Owner does — on the desktop
- **Path A (Mac):** nothing to install. After migration, **quit WeChat fully** before copying files (the DB
  is WAL-journalled; a clean quit flushes it). The probe already proves data persists on disk after quit.
- **Path B (Linux):** install official **WeChat for Linux 4.1.13** (deb/rpm/AppImage from
  `linux.weixin.qq.com`; 4.1.13 is 2026-09-04) [V], log in as secondary **before** step 1a/3, then do the
  migration, then quit.
- Give the automation read access to the account dir:
  - macOS: `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<account>/`
  - Linux: `~/Documents/xwechat_files/<account>/` (or `~/xwechat_files` under Flatpak sandbox) [V]

### 1c. Automation does
1. **Extract the WCDB key** from the running, logged-in client (key is only in memory):
   - **Linux [V, easiest]:** `TANGandXUE/wcdb-key-tool` (MIT, Linux, 4.0 mem-scan / 4.1+ gdb). Needs root
     or `ptrace_scope=0`. Produces the per-DB 64-hex keys.
   - **macOS [V]:** `r266-tech/wxkey` (+ `wechat-cli`; `go install …/wxkey/cmd/wxkey@latest` or the
     wechat-cli release script). `wxkey bootstrap` re-signs a WeChat copy, `task_for_pid`-reads memory,
     validates against page-1 HMAC, writes a per-DB key map to `~/.config/wxcli/config.json`. SIP stays on.
     Alternatives: `ylytdeng/wechat-decrypt`'s `find_all_keys_macos` (→ `all_keys.json`),
     `Thearas/wechat-db-decrypt-macos` (tested only on 4.1.2.241). **Verify these on 4.1.15 (§6-Q1).**
2. **Decrypt** each `db_storage/*.db` with a SQLCipher-4 reader using the key and params
   (AES-256-CBC, page 4096, PBKDF2-HMAC-SHA512 256k, per-DB salt, HMAC-SHA512). Decrypt to a working copy;
   never write the live dir. [V]
3. **Parse messages:** per-contact tables `Msg_<md5(username)>` sharded across `message_N.db`; resolve
   senders via `Name2Id`; `local_type & 0xFFFF` for type; zstd-decompress rows where
   `WCDB_CT_message_content = 4`. [V] (See prior doc §F; welink.click re-confirmed 2026-10-21.)
4. **Resolve + decode media** from `msg/` via `hardlink.db` + `message_resource.db` + `packed_info_data`:
   images `.dat` → AES-128-ECB(head)+XOR(tail) with the per-account **image key** (get it from the same
   tool: `wxkey image-key`); voice SILK (`media_*.db`) → mp3 via `silk-v3-decoder`/`pilk`; video = MP4;
   `wxgf/wxam` → gif/jpg via ffmpeg. [V] Stickers: §4.
5. **Pull everything to Linux** over SSH (`rsync`) if extraction ran on the Mac: copy the decrypted DBs +
   decoded media into chatmaid's store.

### 1d. Expected outputs & completeness checks
- **Outputs:** decrypted `message_*.db`/`contact.db`/`session.db`/`emoticon.db`, plus a media tree
  (images/voice/video/files/stickers) with a message→file index.
- **Verify completeness:** (a) message **count jumps** after migration vs the pre-migration baseline (the
  Mac baseline is ~47 MB / recent-only, so a large jump = success); (b) **spot-check the oldest** message in
  your longest-running chat against the phone; (c) random-sample 10 media items of each type and confirm
  they open; (d) confirm group messages carry the right member via `Name2Id`; (e) check a few type-47
  stickers render (§4). Re-run the migration once more a day later to sweep anything the live sync missed.

---

## 2. Alternatives ranked (one-shot full history)

| # | Route | Effort | Completeness | Ban/freeze risk | Notes |
|---|---|---|---|---|---|
| 1 | **Mac (already logged in) + migrate + mem-key decrypt** | **Low** | Full after migrate | Low (passive read; no new login) | Fastest *because the login + 1.4 GB media already exist*; only unknown = 4.1.15 key-tool support [U] |
| 2 | **Linux client 4.1.13 + migrate + `wcdb-key-tool`** | Med | Full *if* phone can target Linux [U] | Low–med (new PC login = "new device" check) | Easiest key grab (plaintext in /proc/mem); cleanest all-on-Linux end state; migration-to-Linux unverified |
| 3 | **Windows VM (≤4.1.12) + migrate + chatlog-style decrypt/export** | High | Full | Med (VM login freeze reports) | Richest tooling (WeChatDataAnalysis/WeFlow HTML/Excel export, wxautox4). Needs minimal Win guest (§5). Win mem-key broke at 4.1.13.65 → stay ≤4.1.12 [V] |
| 4 | **iPad secondary + migrate + unencrypted-backup parse** | Med | Full after migrate | Med (tablet login scrutiny) | iOS/iPad `MM.sqlite` is **cleartext** (no key needed) [V]; attractive if an iPad exists, but owner has none in scope. "Still cleartext on 2026 build" is [U] |
| 5 | **Decrypt the 备份到电脑 blob (RMFH/ChatPackage/tar.enc)** | Very high | Full | Low (offline) | **Not recommended.** AES-GCM; **key is NOT on the PC** — a memory dump of the PC client found no key; the key sits in the **Android** app (`libaff_biz.so`), extractable only via **root+Frida** on the phone, which the vivo can't do. No public one-click 4.x backup decryptor. [V] (freebuf 456327 / ctfiot 275982, ~2025-10.) |

**Why migration beats backup for a one-shot:** migration lands a **readable live DB** (key-extractable);
backup lands an **encrypted blob whose key you cannot get** on this phone. [V]

---

## 3. Login concurrency & logout behaviour (affects sequencing)

- **Concurrency [V]:** one phone + one PC + one tablet can be online together. **Two phones cannot**
  (newer kicks older). WeChat customer service, 2025-11-07 (ithome 895620): "电脑和手机可以同时登录…平板
  设备也支持与手机同时登录"; "多部手机无法同时登录".
- **Two PCs / a VM vs the Mac [I]:** Windows, Mac and Linux all count as the single **"电脑/PC"** seat, so
  logging WeChat into a Windows VM **will log out the Mac/Linux client** (a 2021 ithome article states Mac
  and Windows can't be online simultaneously; no 2026 source contradicts it). → Pick one desktop per
  extraction pass. [U] for a definitive 2026 confirmation — worth a quick test (§6-Q4).
- **Logout keeps local data [V/probe]:** quitting or logging out leaves `xwechat_files` on disk (the probe
  read a full `db_storage` with **WeChat not running**). Desktop uninstall shows a "保留聊天记录" style
  prompt. Logging back in re-reads the local store; it does not re-download history. So you can migrate,
  quit, extract offline, and log back in later without loss. [V]

---

## 4. Sticker handling plan

Custom stickers are **type 47 (emoji)** messages; the body is XML. [V] Confirmed field set from a real
sample (itchat docs + prior doc): `<emoji … md5 type len productid androidmd5 androidlen cdnurl thumburl
encrypturl aeskey designerid externurl externmd5 width height …>`. Example values: `aeskey` is a **32-hex
string = 16-byte key → AES-128**; `cdnurl`/`encrypturl` are `emoji.qpic.cn/wx_emoji/…` URLs; `md5` keys the
local cache. [V]

**Plan (in priority order):**
1. **Prefer the local cache over re-downloading.** The owner's Mac already has **`emoticon/emoticon.db`**
   plus the `msg/` tree [V-probe]. Custom stickers the owner has used/sent are cached locally; map the
   message's `md5`/`productid` to the cached file first. (Confirm `emoticon.db` schema + cache dir on the
   device — §6-Q3; welink.click doesn't document it.)
2. **Download + decrypt for anything not cached.** Fetch `encrypturl` (encrypted) and AES-decrypt with
   `hex→bytes(aeskey)` as **AES-128-ECB** (strip PKCS7); or fetch `cdnurl` (often the plain file). Detect
   output by magic bytes. [V for AES-128-ECB + aeskey; the encrypturl↔cdnurl split is [I] — validate on a
   sample.] Note: some **官方表情店 (store) packs cannot be downloaded** (copyright-gated) — itchat warns of
   this; for those, keep the `productid` + thumbnail reference rather than the full asset. [V]
3. **Decode WeChat containers.** Sticker/image payloads may be **`wxgf`/`wxam`** (HEVC-wrapped, magic
   `'wxgf'`) → convert with **ffmpeg** to gif (animated) or jpg/png (static); fallback repackage as mp4.
   Some `wxgf` only decode via WeChat's own decoder (an Android "WXGF decode server" is what `wechat-dump`
   shells out to) — expect a small tail of stickers that won't convert cleanly. [V]
4. **GIF vs static:** animated stickers → `.gif`; static → `.png/.jpg`; decide by decoded magic/frame count,
   not by the XML. [I]
5. **Reuse existing tooling instead of writing from scratch:** `chatlog_alpha` (img/voice/video + wxgf via
   ffmpeg), `LifeArchiveProject/WeChatDataAnalysis` (3.8k★, Win+macOS, packages chat exports as a ZIP with
   "必要资源"; sticker specifics unconfirmed), `WeFlow` (broad media). None *documents* a complete sticker
   decryptor, so budget time to verify sticker output on real data. [V/U]

---

## 5. Minimal Windows VM notes (fallback route 3)

Only needed if Paths A/B fail or you want the Windows-only export tools / `wxautox4`.

- **Guest OS, stated plainly:**
  - **Windows 11 IoT Enterprise LTSC 2024** — smallest *official* image. No TPM 2.0 required; MS-listed
    minimums 2 GB RAM / 16 GB disk (preferred 4 GB / 64 GB). The Evaluation Center ISO is a **90-day trial,
    no product key needed**, but that eval is **not a perpetual/production licence** — legitimate for a
    short one-shot, not for keeping. [V] (microsoft.com evalcenter; invgate; osnews 2024.)
  - **Tiny11** — a debloated Windows 11 Pro. You must supply your **own genuine ISO**; it **still needs a
    valid Windows licence/key to activate** (the author states no activation bypass), and **redistributing**
    a custom image breaches Microsoft's licence terms. Unofficial/unsupported. [V] (techspot; theregister;
    windowsnews.ai.) → For a legal short-lived VM, **IoT LTSC eval is the cleaner choice.**
- **Footprint [V/I]:** plan ~**4 GB RAM / 40–64 GB disk** for comfortable WeChat 4.x operation (2 GB/16 GB
  is the floor, tight for a Qt-Quick app + migration media).
- **Graphics / black-window risk [I]:** WeChat 4.x is **Qt Quick** and wants OpenGL; Qt-Quick apps are a
  known source of **black/blank windows** in VMs without working GPU/GL. Mitigations: enable **virtio-gpu
  with 3D accel (virgl)** in libvirt/QEMU; if it still blacks out, force **software rendering**
  (`QT_QUICK_BACKEND=software` / `LIBGL_ALWAYS_SOFTWARE=1`) or toggle 3D accel off→on. No WeChat-specific
  confirmed KVM recipe was found — treat as a tuning exercise. [U]
- **VM detection / ban [V]:** VM/emulator logins carry **elevated freeze/ban risk**; new-device login
  triggers SMS/identity verification; one report = KVM Windows VM banned after ~5 months. For a *one-shot
  read* this is acceptable (no automation, no sends), but don't run bots from it on the main account.
- **Getting data out [I]:** QEMU **spice-vdagent** for clipboard; **virtiofs/9p** or a Samba share to copy
  the decrypted DBs + media to the Linux host. Simplest: export with WeChatDataAnalysis/WeFlow inside the
  VM, then copy the output folder out.
- **Build recipes [V/U]:** **quickemu** auto-installs VirtIO drivers + swtpm and can fetch standard Win10/11
  automatically (Win11 **IoT LTSC** needs a manual ISO); **virt-manager** for hand setup. The Linux box has
  **no QEMU/libvirt yet**, so this route starts from a full virtualization install — factor that effort in.
- **Version pin [V]:** for Windows memory-key extraction stay **≤ 4.1.12** (it broke at 4.1.13.65, 2026-09);
  for `wxautox4` UI automation use exactly **4.0.5**. Current latest Windows/Mac = **4.1.15** (2026-09-15),
  Linux = **4.1.13** (2026-09-04). [V] Archived Windows installers: **winget-pkgs** manifests
  (`Tencent.WeChat.Universal` lists `4.1.12.26` → `WeChatWin_4.1.12.exe`), Tencent CDN pattern
  `dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin_<ver>.exe` (some old links pulled — **not verified
  live**, [U]), GitHub mirrors (e.g. `Rodert/wechat-win-versions`), and web.archive.org. Verify any download
  by hash.

**MacBook as the fallback detail (it is actually route 1, not a fallback):** given the Mac already holds a
live 4.1.15 login + media and needs no virtualization, it is the *lowest-setup* path — the only open item is
confirming a key tool works on **4.1.15** (wechat-cli's notes currently pin macOS support to **≤ 4.1.8.100**
and say Tencent changes key derivation between minor builds, with fixes lagging **1–4 weeks** [V]). If the
key tool works on 4.1.15, the Mac is the fastest complete extraction; if not, fall to the Linux client
(route 2), whose key extraction is build-independent (plain memory scan).

---

## 6. Open questions needing a test on the owner's devices

1. **Does a key tool work on macOS WeChat 4.1.15?** wxkey/`wechat-cli` currently advertise macOS ≤4.1.8.100;
   `Thearas` tested only 4.1.2.241. **Test:** with WeChat logged in, run `wxkey bootstrap` / `doctor`;
   success = a populated `~/.config/wxcli/config.json` that opens `message_0.db`. [U]
2. **Can the phone migrate directly to the official Linux client 4.1.13?** Community reports say the Linux
   client lacks a 备份/迁移/导入导出 UI, but migration is **phone-initiated** (the PC only confirms a pop-up).
   **Test:** install+log in Linux client, start `迁移到电脑微信` on the phone, see whether the Linux client
   shows the confirm dialog and merges. [U]
3. **`emoticon.db` schema + sticker cache dir, and whether cached stickers are already decrypted.** Inspect
   `emoticon/emoticon.db` and the `msg/`/`emoji` tree on the Mac. [U]
4. **Does a Windows-VM login log out the Mac client (shared PC seat)?** Test before relying on running both.
   [U]
5. **Is Windows memory-key extraction still broken on 4.1.13+ / re-established on 4.1.15?** Could not pull
   the latest `stargazer-2026/wechat-4.1.12-decrypt` issues this session (tooling blocked); assume **stay
   ≤4.1.12 on Windows** until confirmed. [U]
6. **Do old desktop builds (4.0.5 / ≤4.1.12) still log in in Oct 2026, or is there a force-upgrade wall?**
   3.9.12 is reportedly already blocked; 4.0.x/4.1.x status unconfirmed. **Test:** install an archived build
   and attempt login before committing to the wxautox4/Windows-mem-key plan. [U]
7. **Is the 7-day media-expiry limit avoidable** by pre-loading all chats, or do very old media get dropped
   regardless? Spot-check oldest media after migration. [U]

---

## 7. References (accessed 2026-10-21)

Official / procedure:
- Huawei support — WeChat 迁移/导出到电脑 steps, 7-day media limit, exclusions — https://consumer.huawei.com/cn/support/content/zh-cn01118496 ; https://consumer.huawei.com/cn/support/content/zh-cn16032762/
- sspai — phone→PC 迁移 then PC-side export (2024-10-05) — https://sspai.com/post/92315
- WeChat update pages — Win/Mac **4.1.15** (2026-09-15), Linux **4.1.13** (2026-09-04) — https://weixin.qq.com/updates?platform=win ; https://weixin.qq.com/updates?platform=mac ; https://weixin.qq.com/updates?platform=linux
- Native backup is encrypted/restore-only (no HTML/Word) — https://cloud.tencent.com/developer/article/2714144 (2026-07, via search snippet) ; https://juejin.cn/post/7661754132202569763 ; https://www.ifanr.com/515943

Login concurrency / logout:
- WeChat CS on multi-device login (2025-11-07) — https://www.ithome.com/0/895/620.htm
- Mac vs Windows can't be online together (2021) — https://www.ithome.com/0/834/785.htm
- HarmonyOS phone+tablet not simultaneous (2025-03-02) — https://www.thepaper.cn/newsDetail_forward_29602374
- Linux client has no backup/export UI; phone→Linux migration unconfirmed (2026-01) — https://v2ex.com/t/1182710

Key extraction / decryption:
- `all_keys.json` is tool-generated (`find_all_keys_macos`) — https://gitea.com/josephdavis/wechat-decrypt (mirror of ylytdeng/wechat-decrypt)
- macOS key via memory + ad-hoc codesign, SIP stays on — `r266-tech/wxkey` v1.4.7 (2026-05-31) https://pkg.go.dev/github.com/r266-tech/wxkey@v1.4.7 ; `wechat-cli` (macOS ≤4.1.8.100 noted; task_for_pid; `~/.config/wxcli`) https://x-cmd.com/install/wechat-cli ; mighil.com macOS write-up https://mighil.com/on-extracting-wechat-database-encryption-keys-in-macos
- `Thearas/wechat-db-decrypt-macos` (tested 4.1.2.241 only) — https://github.com/Thearas/wechat-db-decrypt-macos
- Linux key tool — `TANGandXUE/wcdb-key-tool` — https://github.com/TANGandXUE/wcdb-key-tool
- Windows key break at 4.1.13.65; wx_key notes function change since 4.1.4 — https://github.com/stargazer-2026/wechat-4.1.12-decrypt/issues/2 ; https://github.com/ycccccccy/wx_key
- Backup blob (RMFH/ChatPackage/tar.enc), AES-GCM, key only on Android `libaff_biz.so` via Frida, none in PC memory — https://www.freebuf.com/articles/456327.html ; https://www.ctfiot.com/275982.html

Stickers / media:
- type 47 emoji XML sample (md5/cdnurl/encrypturl/aeskey…), store packs un-downloadable — https://itchat.readthedocs.io/zh/latest/intro/messages/
- 4.x schema / message types (local_type, zstd, type-49 sub-8 store GIF) — https://welink.click/message-types.html ; https://welink.click/database.html
- image `.dat` AES-128-ECB+XOR / wxgf via ffmpeg (prior doc) — https://upxuu.com/posts/wechat-dat-image-decrypt/ ; https://sarv.blog/posts/wxam/
- SILK voice — https://github.com/kn007/silk-v3-decoder ; https://github.com/foyoux/pilk
- export tools (HTML/Excel/stickers) — https://github.com/LifeArchiveProject/WeChatDataAnalysis (3.8k★) ; https://github.com/hicccc77/WeFlow ; WeChatMsg/留痕 (via search)

Windows VM:
- Win11 IoT Enterprise LTSC eval (90-day, no key, no TPM) — https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-iot-enterprise-ltsc ; https://invgate.com/itdb/windows-iot-enterprise-ltsc-2024
- Tiny11 needs own ISO + genuine licence; no activation bypass — https://techspot.com/downloads/7578-tiny11.html ; https://www.theregister.com/a/5303034
- quickemu Windows guests / VirtIO / swtpm — https://github.com/quickemu-project/quickemu
- archived Windows installer — winget `Tencent.WeChat.Universal` (4.1.12.26) https://github.com/microsoft/winget-pkgs ; mirror `Rodert/wechat-win-versions`
- VM-login ban/freeze reports (prior doc) — https://v2ex.com/t/811646

Chinese terms: 聊天记录迁移与备份 / 迁移到电脑微信 · 聊天记录管理 → 导入与导出 → 导出到电脑 (merge into PC
live DB) · 备份到电脑 (encrypted restore-only blob) · 同步最近消息 (recent-only) · 密钥 (key) · 解密
(decrypt) · 表情 (sticker) · 风控/封号 (risk control/ban) · 保活 (keep-alive).
