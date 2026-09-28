# Prior art: multi-platform chat assistants, bridges and helpers

Research date: 2026-09-27. Star counts and "last push" come from `api.github.com`
on that date (timestamps are UTC, so some read 2026-09-28). Each claim is tagged
**VERIFIED** (read from a primary source listed in References), **INFERRED**
(follows from verified facts or from several secondary sources) or **UNCERTAIN**
(could not confirm; treat as a lead, not a fact).

## 1. Executive summary

- **OpenClaw** (MIT, ~390k stars) is the reference architecture for "one gateway
  daemon, many channel plugins, sessions, skills, model fallback chains, thin
  remote UIs". Its WhatsApp/iMessage/Signal channels run *as the owner's own
  account*; Telegram/Discord/Slack run as bot identities. It is also the source of
  the year's biggest agent-security lessons (CVE-2026-25253, ClawJacked, ClawHub
  malicious skills). Borrow the gateway/session/plugin/failover shapes; copy none
  of the localhost-trust defaults.
- **WeChat has exactly one sanctioned integration path in 2026: Tencent's iLink
  Bot API** (the "ClawBot" plugin, released 2026-03-22, shipped as the OpenClaw
  plugin `@tencent-weixin/openclaw-weixin`). An iLink bot is a **separate contact
  with its own identity**: it cannot read the owner's chats with other people,
  join groups, or message the owner's contacts. So iLink is a good *control
  channel* for chatmaid, **not** a data source for "suggest my next reply".
  Every unofficial personal-WeChat adapter (itchat/web, Gewechat, WeChatFerry
  hook, wxauto UIA on 3.9, WeChatPadPro iPad protocol) is dead, archived, broken
  by WeChat 4.0, or a ban-risk gray area. Device-side reading is covered in
  `wechat-access.md`.
- **Discord**: automating a user account, even read-only, is expressly forbidden
  and can terminate the account. The only compliant read path is an official bot
  in servers the owner controls; bots can never read user-to-user DMs. Client
  mods (Vencord/BetterDiscord) are also a ToS violation, though not enforced.
- **Email** is the easy platform: IMAP IDLE + a Drafts folder + SMTP on approval
  needs no infrastructure; Gmail push via Pub/Sub only pays off at scale. Inbox
  Zero is the best UX reference ("AI rules in plain English", pre-drafted
  replies), but its AGPL+commercial-terms license should be read before copying.
- **Bridges/unified inboxes**: no maintained Matrix WeChat bridge exists; Beeper
  has no WeChat. Beeper Desktop API (localhost REST/WS/MCP over all connected
  networks) is the cleanest "unified inbox as a local API" pattern seen.
- **Overlays**: web chats get DOM-scraping content scripts with suggestion chips;
  desktop chats get transparent always-on-top windows (Electron/Tauri) fed by
  Accessibility/UIAutomation reads or screen OCR (screenpipe, cheating-daddy).
  WeChat 4.0 on Windows defeats UIAutomation; OCR or DB decryption is left.

## 2. Comparison table

| Project | Kind | WeChat path | Discord | Email | LLM (local / cloud) | Acts as owner's account? | License | Stars | Last push |
|---|---|---|---|---|---|---|---|---|---|
| openclaw/openclaw | personal agent gateway | iLink via Tencent plugin (bot identity) | bot token | no channel (email via skills, UNCERTAIN) | Ollama/LM Studio/vLLM + cloud; fallback chains | WhatsApp/iMessage/Signal yes; Telegram/Discord/Slack no | MIT | 390,672 | 2026-09-28 |
| Tencent/openclaw-weixin | WeChat channel plugin | iLink (official) | - | - | n/a | no (separate bot contact) | MIT | 893 | 2026-09-21 |
| wechaty/wechaty | chatbot SDK, puppet abstraction | web / PadLocal (paid) / XP (Win hook) puppets, all gray-area | no | no | none built in | yes (logs in as you) | Apache-2.0 | 23,334 | 2025-12-21 |
| nonebot/nonebot2 | bot framework (Py) | official-account adapter only; gewechat/ntchat adapters dead | community adapter | no | via plugins | no | MIT | 7,724 | 2026-09-27 |
| koishijs/koishi | bot framework (TS) | official-account adapter; community wechat4u/wechaty adapters | adapter | no | via plugins | community adapters only | MIT | 6,233 | 2026-08-28 |
| AstrBotDevs/AstrBot | "OpenClaw alternative" bot platform | iLink adapter (`weixin_oc`); WeChatPadPro removed | bot | no | Ollama, LM Studio + many cloud/LLMOps | no | AGPL-3.0 | 41,126 | 2026-09-27 |
| langbot-app/LangBot (ex-QChatGPT) | bot platform | iLink ("Personal WeChat (OpenClaw)"); legacy WeChatPadPro docs | bot | yes (email platform) | Ollama, LM Studio, Dify, MCP, many cloud | no | Apache-2.0 | 17,971 | 2026-09-28 |
| zhayujie/CowAgent (ex-chatgpt-on-wechat) | agent harness + channels | "WeChat" QR login (protocol not stated; INFERRED iLink) | yes | no | Ollama + cloud | no | MIT | 47,146 | 2026-09-28 |
| hanfangyuan4396/dify-on-wechat | CoW fork + Dify | itchat / Gewechat (both dead) | - | - | Dify | historically yes | MIT | 2,835 | 2026-04-03 |
| wangrongding/wechat-bot | multi-IM agent | Wechaty (wechat4u/padlocal), ban warnings | no | no | Ollama + many cloud | yes | MIT | 11,401 | 2026-09-04 |
| HenryXiaoYang/XYBotV2 | WeChat bot | iPad protocol | - | - | Dify etc. | yes | GPL-3.0 | 812 | 2025-05-23 (archived) |
| duo/matrix-wechat | Matrix puppeting bridge | Windows-hook agent (UNCERTAIN) | - | - | n/a | yes | AGPL-3.0 | 177 | 2025-02-19 |
| Beeper (Automattic) | unified inbox + Desktop API | none | user-account bridge (ToS risk, UNCERTAIN) | no | on-device model for features | yes | client closed; bridges open | - | - |
| elie222/inbox-zero | email AI assistant | - | - | Gmail API + MS Graph | cloud (local UNCERTAIN) | yes (OAuth) | AGPL-3.0 + terms | 12,360 | 2026-09-28 |
| Mail-0/Zero | email client | - | - | Gmail OAuth | cloud | yes | MIT | 10,832 | 2026-05-26 |
| postalsys/emailengine | headless email REST | - | - | IMAP/SMTP/Gmail/Graph | n/a | yes | commercial, source-available | 2,232 | 2026-09-27 |
| dolfies/discord.py-self | user-account Discord lib | - | self-bot (forbidden) | - | n/a | yes | MIT | 1,271 | 2026-08-07 |
| mediar-ai/screenpipe | screen/audio capture + OCR | any app via OCR | any | any | local-first | n/a | "Other" per GitHub | 21,743 | 2026-09-28 |
| sohzm/cheating-daddy | Electron overlay assistant | any via screen | any | any | Gemini (cloud) | n/a | GPL-3.0 | 5,614 | 2026-07-23 |

## 3. OpenClaw (most important entry)

### Identity, repo, license, governance
- **VERIFIED** Current name OpenClaw; repo `openclaw/openclaw`; homepage openclaw.ai;
  LICENSE file is MIT, "Copyright (c) 2026 OpenClaw Foundation" (GitHub's API
  reports `NOASSERTION`, presumably because of the custom header line).
- **VERIFIED** Rename chain (Wikipedia): Warelay (2025-11-24) -> CLAWDIS (2025-12-03)
  -> Clawdbot (2026-01-02) -> Moltbot (2026-01-27, after Anthropic trademark
  contact) -> OpenClaw (2026-01-30). Creator Peter Steinberger (PSPDFKit founder)
  announced joining OpenAI on 2026-02-14 and moved the project to a non-profit
  OpenClaw Foundation.
- **VERIFIED** 390,672 stars, 82,158 forks, 8,798 open issues, created
  2025-11-24, pushed 2026-09-28. Versions are date-stamped (e.g. `2026.5.12`).

### Channels / adapters (docs.openclaw.ai/channels)
- **VERIFIED** Bundled: Telegram (bot token, grammY), WebChat (core), A2A, Reef.
  Official plugins: WhatsApp (own account via Baileys QR pairing), Discord (bot),
  Slack (bot), Signal (signal-cli, user account), iMessage (`imsg` JSON-RPC, user
  account on a Mac), Matrix, IRC, Google Chat, LINE, Mattermost, MS Teams,
  Nextcloud Talk, Nostr, QQ Bot, SMS (Twilio), Synology Chat, Tlon, Twitch, Zalo
  (bot) and Zalo Personal (`zca-js`, QR login), Feishu. External plugins: WeChat
  (`openclaw-weixin`, Tencent), WeCom, Yuanbao, Zalo ClawBot.
- **VERIFIED** No first-party email channel is listed on the channels page.
  Email handling, if any, goes through skills/tools (UNCERTAIN).

### WeChat via Tencent's official plugin (iLink)
- **VERIFIED** Package `@tencent-weixin/openclaw-weixin` (v2.4.8 at research time,
  requires OpenClaw >= 2026.5.12), channel id `openclaw-weixin` (aliases `weixin`,
  `wechat`). Install: `npx -y @tencent-weixin/openclaw-weixin-cli install`. Repo
  `Tencent/openclaw-weixin`: MIT ("Copyright (C) 2026 Tencent"), created
  2026-03-27, 893 stars, pushed 2026-09-21.
- **VERIFIED** OpenClaw's own docs: "Direct chats and media are supported. Group
  chats are not advertised by the plugin capability metadata"; v2.4.8 "does not
  register an OpenClaw pairing adapter" and uses a legacy JSON allowlist that,
  when empty, falls back to the QR-scanning user's id.
- **VERIFIED** Protocol (iLink Bot API, documented by third parties from the
  plugin source): base `https://ilinkai.weixin.qq.com`; `GET
  /ilink/bot/get_bot_qrcode`, `GET /ilink/bot/get_qrcode_status`, `POST
  /ilink/bot/getupdates` (long-poll, up to 35 s), `POST /ilink/bot/sendmessage`;
  `Authorization: Bearer <bot_token>`; bot ids look like `xxx@im.bot`, users like
  `xxx@im.wechat`; each inbound carries a `context_token` that replies must echo;
  a `get_updates_buf` cursor must be persisted or messages repeat. Text, image
  (encrypted), voice (silk, with WeChat-side transcription), file, video.
- **VERIFIED** (multiple Chinese sources, Mar 2026) The bot appears as a new
  contact named "ClawBot"; it has no access to the owner's contact list or chat
  history, cannot be pulled into groups, and cannot message other contacts:
  "目前只能作为私人的小助手自问自答". Requires WeChat iOS >= 8.0.70 / Android >=
  8.0.69 with the ClawBot plugin (initially gray-released).
- **INFERRED** Because the bot is a distinct identity, "who can message it" is
  governed by whether other users can add the `@im.bot` contact; the OpenClaw
  docs' allowlist logic and the team400 write-up ("new senders need approval")
  imply others *can* reach it, but this is UNCERTAIN in practice.
- **VERIFIED** Standalone iLink clients exist that skip OpenClaw entirely:
  `x1ah/wechat-ilink-demo` (JS, created 2026-03-22), `SiverKing/weixin-ClawBot-API`
  (Python, 128 stars, pushed 2026-09-04), `hao-ji-xing/openclaw-weixin`
  (`protocol.md`, `weixin-bot-api.md`, `wechat-claude-bridge.mjs`; ~302 stars),
  `li1bai0/wechat-channel`, `fooling/wechat-clawbot-gateway`.

### Architecture
- **VERIFIED** Gateway: one long-lived daemon per host, binds `127.0.0.1:18789`,
  owns all provider connections (it is the only process holding WhatsApp
  sessions), exposes a typed WebSocket JSON API with mandatory handshake
  (shared secret or identity), device pairing for non-local clients, idempotency
  keys on side-effecting methods. Control-plane clients: macOS menu-bar app, CLI,
  `openclaw tui`, Control UI (Vite + Lit SPA served on the same port, "admin
  surface, should not be exposed publicly"), automations. "Nodes" (macOS, iOS,
  Android, headless) connect with `role: node` and expose device commands
  (`camera.*`, `screen.record`, `location.get`, `canvas.*`). WebChat is a static
  UI using the WS API. Remote access: keep bind on loopback, use Tailscale Serve
  (tailnet) or Funnel.
- **VERIFIED** Sessions: routed by origin; DMs default to one shared "main"
  session (`session.dmScope: main`, alternatives `per-peer`,
  `per-channel-peer`); groups isolated `per-group` (key
  `agent:<agentId>:whatsapp:group:<jid>`); cron and webhooks get fresh
  sessions. State in SQLite `~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`,
  archived transcripts under `~/.openclaw/agents/<agentId>/sessions/`; context
  compaction and pruning policies; optional cross-session retrieval.
- **VERIFIED** Skills: a directory with `SKILL.md` (YAML frontmatter + markdown
  instructions telling the agent when/how to use tools). Load precedence:
  workspace `skills/` > `.agents/skills` > `~/.agents/skills` > managed
  `<state-dir>/skills` > workshop drafts > bundled > plugin/extra dirs. Registry
  ClawHub: `openclaw skills install @owner/<slug>` (`--global` ->
  `~/.openclaw/skills`). Gating via `metadata.openclaw` (required binaries, env,
  config) and per-agent allowlists. Tools are capabilities; skills are the
  instructions; plugins can ship both.
- **VERIFIED** Models: providers include Anthropic, OpenAI, DeepSeek and local
  stacks (Ollama, vLLM, SGLang, LM Studio, LiteLLM proxies). Failover chains:
  a primary plus ordered `fallbacks` (docs example: primary
  `anthropic/claude-sonnet-4-5`, fallbacks `[lmstudio/minimax-m2.1-gs32,
  anthropic/claude-opus-4-5]`); a "merge" mode keeps hosted models available as
  fallbacks behind local ones. Docs recommend LM Studio with the Responses API as
  the best current local stack.

### Own conversations vs separate bot identity
- **VERIFIED** WhatsApp: a separate number is recommended, but personal-number
  mode is supported; onboarding then writes `dmPolicy: "allowlist"`, `allowFrom`
  containing your own number and `selfChatMode: true` (the "message yourself"
  thread becomes the agent console; read receipts and self-mention triggers are
  suppressed). "The gateway receives all incoming WhatsApp Web messages on the
  linked account"; routing then filters DMs by `dmPolicy` + `allowFrom` or the
  pairing store, and groups by membership allowlist + sender policy + mention
  rules. Under `allowlist`, non-listed senders are silently blocked; under
  `pairing` an unknown sender creates a pairing request that the owner approves
  (whether the stranger receives an automatic reply is UNCERTAIN; sources
  disagree). Unprocessed group messages are buffered and injected as context
  when the bot is triggered (`historyLimit`).
- **VERIFIED** iMessage, Signal, Zalo Personal, Nostr, IRC, Tlon: user-account
  channels. Telegram, Discord, Slack, LINE, Teams, QQ, etc.: bot tokens.
- **INFERRED** OpenClaw's framing is always "you (or approved people) talk *to*
  the agent"; there is no built-in mode that reads a friend's message and drafts
  the owner's reply for approval. That "suggest, don't send" loop is chatmaid's
  differentiator and must be built on top of the same routing primitives.

### Security incidents (why to copy the shape, not the defaults)
- **VERIFIED** CVE-2026-25253 (CVSS 8.8, CWE-669): the Control UI accepted a
  `gatewayUrl` query parameter and auto-connected a WebSocket to it, leaking the
  stored gateway token; attacker gets operator-level API -> config changes ->
  RCE. Fixed in v2026.1.29 (2026-01-30); the UI now asks for confirmation.
- **VERIFIED** "ClawJacked" (Oasis Security, 2026-02-26): localhost WebSocket
  had no origin check, loopback was exempt from rate limiting, and device
  pairing from localhost was auto-approved, so any website could brute-force the
  gateway password from the browser. Fixed in 2026.2.25.
- **VERIFIED** ClawHub supply chain: 341 malicious skills (335 in one
  "ClawHavoc" campaign); Koi Security later counted 820+ of ~10,700 skills.
  Cisco found third-party skills doing exfiltration and prompt injection.
- **VERIFIED** (secondary sources; numbers vary) Scans found >135,000 gateways
  exposed with no auth, ~50,000 exploitable. Wikipedia: Chinese authorities
  barred state agencies and banks from OpenClaw (Mar 2026); Feb 2026 "MoltMatch"
  incident (agent created dating profiles unprompted).

## 4. Wechaty

- **VERIFIED** `wechaty/wechaty`: Apache-2.0, 23,334 stars, not archived, last
  push 2025-12-21; npm latest `wechaty@1.20.2`; last GitHub *Release* entry is
  v0.56 from 2021 (releases are cut on npm, not GitHub). Puppet providers page
  (updated 2025-12-21): WeChat web (beta), WeChat4U web (alpha), PadLocal pad
  (beta, paid token from pad-local.com, 7-day free trial), XP Windows hook
  (alpha), Official Account, WhatsApp web (alpha), Gitter, Lark, Service (gRPC),
  Mock. `puppet-padlocal` repo last push 2023-07-04 (but issues still being filed
  Sep 2026 because the hosted pad service continues), `puppet-xp` 2025-07-29,
  `puppet-wechat4u` 2024-05-31.
- **INFERRED** Wechaty is in maintenance mode: framework alive, WeChat puppets
  stagnant, every one a reverse-engineered protocol (web is widely reported
  blocked for accounts registered after 2017; XP needs a pinned Windows 3.9
  client). `wechat-bot` warns the default protocol "can trigger ... account bans".
- **Borrow**: the Puppet abstraction (uniform Contact/Room/Message/login events
  across protocols) and `puppet-service` (run the adapter out-of-process over
  gRPC, e.g. inside a Windows VM) are exactly the seam chatmaid needs between
  "reader" and "brain".

## 5. Chinese multi-platform bot frameworks

Adapter health first, because it decides everything:
- **VERIFIED** Gewechat (`Devo919/Gewechat`, Apache-2.0, 3,494 stars): README says
  the runtime service, images and support are discontinued; repo kept as archive.
- **VERIFIED** WeChatFerry (`lich0821/WeChatFerry`, MIT, 6,797 stars): archived;
  Windows hook, version-pinned.
- **VERIFIED** wxauto (`cluic/wxauto`, Apache-2.0, 7,334 stars, pushed
  2026-04-13): UIAutomation on Windows WeChat 3.9.x only; WeChat 4.0.5 moved to
  Qt Quick/QML so controls no longer have HWNDs and UIA "sees an empty shell
  window"; `wxauto4` is marked no longer updated; `wxautox4` is a paid fork;
  `pywechat` (pywinauto) claims 4.0 support (UNCERTAIN how well).
- **VERIFIED** WeChatPadPro (`WeChatPadPro/WeChatPadPro`, no license, 2,624
  stars, pushed 2026-08-20): iPad-protocol server, gray-area; AstrBot removed it
  from core (issue #4865) and LangBot keeps it only as legacy docs.
- **VERIFIED** iLink/ClawBot is now the documented personal-WeChat path in
  AstrBot (`platform/weixin_oc`: "openclaw-weixin" interface, QR login + long
  polling; text/image/video/file; voice receive-only) and LangBot
  (`platforms/wechat/weixin`: "Personal WeChat (OpenClaw)", WeChat >= 8.0.69,
  "single chats, streaming output, whitelist").

Per project:
- **NoneBot2** (MIT, 7,724 stars, pushed 2026-09-27). Python async, adapter
  model (OneBot v11/12, Telegram, Discord, Feishu, QQ, ...). WeChat: only
  `nonebot-adapter-wxmp` (official accounts / mini-program customer service) is
  viable; `nonebot-adapter-gewechat` and `nonebot-adapter-ntchat` died with their
  backends. LLMs are plugin-level. Borrow: adapter/event/matcher design, nothing
  WeChat-specific.
- **Koishi** (MIT, 6,233 stars, pushed 2026-08-28). TypeScript, plugin-first,
  Satori protocol. WeChat: `@koishijs/plugin-adapter-wechat-official` (official
  accounts); community `@xiongcj/koishi-plugin-adapter-wechat4u` and
  `CaoMeiYouRen/koishi-plugin-adapter-wechaty` (personal, gray-area). Borrow: the
  Satori "universal message element" model for cross-platform rich messages.
- **AstrBot** (AGPL-3.0, 41,126 stars, pushed 2026-09-27; bills itself "your
  openclaw alternative"). Platforms: QQ (OneBot v11), Telegram, WeCom + WeCom AI
  bot, WeChat official accounts, WeChat 客服, personal WeChat via iLink, Feishu,
  DingTalk, Slack, Discord, LINE, Satori, KOOK, Misskey, Mattermost; community
  Matrix/Rocket.Chat/VoceChat. LLMs: OpenAI, Anthropic, Gemini, DeepSeek,
  Moonshot, Zhipu, Ollama, LM Studio, Dify, Coze, Bailian, many routers; Whisper/
  SenseVoice STT. WebUI, Docker, desktop app, 1000+ plugins. AGPL matters if
  code is copied.
- **LangBot** (Apache-2.0, 17,971 stars, pushed 2026-09-28; renamed from
  QChatGPT by RockChinQ, VERIFIED via the project's own discussions). Platforms:
  Discord, Telegram, Slack, LINE, QQ, WeCom (incl. 客服 and AI bot), WeChat
  personal (iLink) and official, Lark, DingTalk, KOOK, Matrix (and anything
  bridged into it), **Email**, Satori. LLMs: OpenAI, Anthropic, DeepSeek, Gemini,
  xAI, Moonshot, Zhipu, Ollama, LM Studio, Dify, MCP, SiliconFlow, Bailian, Volc
  Ark, ModelScope, GiteeAI. "Multi-pipeline" architecture (per-bot pipeline of
  pre-processors, model, post-processors), event-driven plugins, web dashboard.
  Borrow: pipeline-per-channel idea and the fact that email is just another
  platform adapter.
- **CowAgent** (`zhayujie/CowAgent`, formerly chatgpt-on-wechat; MIT, 47,146
  stars, pushed 2026-09-28). Now an "agent harness": task planning, tools (file
  I/O, terminal, web search, browser automation, scheduling), MCP, skills,
  memory, multi-agent; Ollama plus cloud. Channels: WeChat (QR login in the web
  console; README does not name the protocol; the old itchat web channel is
  reported non-functional and issue #2717 "2.0.4: 支持个人微信接入" coincides
  with the iLink release, so INFERRED iLink), WeCom bot/app, WeChat 客服,
  official accounts, Telegram, Discord, Slack, Feishu, DingTalk, QQ, web
  console. `dify-on-wechat` is a stale downstream fork (itchat + Gewechat).
- **xxxbot-pad / XYBot**. `NanSsye/xxxbot-pad` returns 404 (moved or removed;
  forks like `Lwjivd/xxxbot-pad` remain, UNCERTAIN); it used 849/855/iPad
  protocols. `HenryXiaoYang/XYBot` (hook, WeChat 3.9.10.27, GPL-3.0) and
  `XYBotV2` (iPad protocol, GPL-3.0, 812 stars) are both archived (2025-05-23).
- **wechat-bot** (`wangrongding/wechat-bot`, MIT, 11,401 stars, pushed
  2026-09-04). Wechaty (wechat4u/padlocal) with explicit ban warnings; also
  Telegram Bot API, WhatsApp Cloud API, Lark; Ollama plus Chinese cloud LLMs.

## 6. Matrix / Beeper bridges

- **VERIFIED** mautrix (Go) bridges on the org page: whatsapp (1,896 stars),
  meta (Messenger/Instagram), discord, twitter, signal, slack, gvoice, all pushed
  Sep 2026, built on the `mautrix/go` framework. No WeChat bridge in the org.
  (mautrix-telegram, gmessages, bluesky and others exist but were not on the
  fetched page: INFERRED.)
- **VERIFIED** matrix.org lists two WeChat bridges, both alpha: `duo/matrix-wechat`
  (AGPL-3.0, 177 stars, last push 2025-02-19, mautrix-go based; it drives a
  Windows WeChat client through a separate agent, UNCERTAIN details) and
  `huan/matrix-appservice-wechaty` (= `wechaty/matrix-appservice`, Apache-2.0,
  last push 2022-09-09). `LeiShi1313/mautrix-wechat` (1 star, 2022) wraps
  `wechat-box` (dockerised Windows WeChat). Conclusion: **no maintained WeChat
  bridge exists**, and each one depends on a hooked Windows client.
- **VERIFIED** Beeper: Automattic bought Texts.com (2023, $50M) and Beeper (Apr
  2024, $125M); merged in 2025 into an on-device Beeper. Networks: WhatsApp,
  Instagram, Messenger, X, Telegram, Signal, Matrix, Slack, Google Chat, Discord,
  LinkedIn, Google Messages, iMessage (Mac only). WeChat absent. Bridges are open
  source; the client is not stated to be. Texts.com's old app "can still be
  used" per its founder; no shutdown date found (UNCERTAIN).
- **VERIFIED** Beeper Desktop API: local server `http://localhost:23373`, REST +
  `ws://localhost:23373/v1/ws` + MCP, bearer token created under Settings ->
  Developers; read/search/send/edit/react/stream across every connected network;
  TypeScript/Python/Go SDKs and a `beeper` CLI.
- **Pattern value**: a puppeting bridge logs in *as you*, backfills history and
  mirrors your outgoing messages ("double puppeting"), exactly chatmaid's read
  model. A homeserver is heavy for one user, but the shape (network login ->
  portal rooms -> ghost users -> backfill) is a clean local data model. Beeper's
  Discord bridge uses the user's own token, so it inherits the self-bot ToS
  problem (INFERRED).

## 7. Email assistants

- **Inbox Zero** (`elie222/inbox-zero`, 12,360 stars, pushed 2026-09-28).
  **VERIFIED** license: AGPL-3.0 plus additional terms; personal, educational,
  research and organisations under five business users are exempt, larger
  organisations need an enterprise license. Connects via Gmail API and Microsoft
  Graph; features: pre-drafted replies "in your tone", "AI Rules" written in
  plain English, Reply Zero tracking, cold-email blocking, meeting briefs;
  Next.js + Postgres + Redis + pub/sub webhooks, Turborepo; Slack/Telegram
  integrations; `npx @inbox-zero/cli setup`. Local-model support UNCERTAIN.
- **Zero / Mail-0** (`Mail-0/Zero`, MIT, 10,832 stars, last push 2026-05-26, four
  months idle). Gmail via Google OAuth; Next.js, Drizzle + Postgres, Cloudflare
  Workers with Durable Objects/R2 for mail. More a client than an assistant.
- **EmailEngine** (`postalsys/emailengine`, 2,232 stars, pushed 2026-09-27).
  **VERIFIED** source-available under a commercial "EmailEngine License": 14-day
  trial, then a flat annual fee. Unified REST over IMAP/SMTP, Gmail API and
  Graph, webhooks, OAuth2, IMAP/SMTP proxy. Its predecessor "IMAP API" was
  open source (mirror `kokizzu/imapapi`).
- **Nylas**: hosted commercial email/calendar API; not open source; pricing not
  checked (UNCERTAIN).
- **Plain approach** (VERIFIED from Google docs and 2025-2026 reports): Google
  disabled plain-password IMAP/SMTP in 2025; app passwords still work but are
  called transitional; OAuth2 (SASL XOAUTH2) is preferred. Gmail push =
  `users.watch` -> Cloud Pub/Sub topic -> your webhook -> `history.list`; the
  watch expires after 7 days (renew daily). For one mailbox the simplest
  "read, draft, send on approval" is: IMAP IDLE on INBOX (imapflow / aioimaplib
  / go-imap), write the draft with IMAP `APPEND` to the Drafts folder (or
  `drafts.create` on Gmail API), send with SMTP once approved. Pub/Sub only pays
  off when polling many accounts or when a serverless deployment cannot hold an
  IDLE connection (INFERRED).

## 8. Discord for a personal account

- **VERIFIED** Policy text (docs.discord.com, OAuth2 page): "Automating normal
  user accounts (generally called 'self-bots') outside of the OAuth2/bot API is
  forbidden, and can result in an account termination if found." The support
  article "Automated User Accounts (Self-Bots)" says the same; there is no
  read-only exemption. Support-forum threads show accounts disabled for it.
- **VERIFIED** `dolfies/discord.py-self` (MIT, 1,271 stars, pushed 2026-08-07)
  and `discum` implement user-account automation and therefore fall under that
  prohibition even if only used to read.
- **VERIFIED** Client mods: Vencord's README states "Client modifications are
  against Discord's Terms of Service" (ToS section 11: no modifying, reverse
  engineering or derivative works). No documented bans solely for Vencord or
  BetterDiscord as of 2026, and Discord staff have called it a low enforcement
  priority, but it remains a violation; a plugin that reads chat inside the
  client is a client mod.
- **VERIFIED** Local cache: `%AppData%\discord\Cache` (Chromium HTTP cache),
  `Local Storage\leveldb` (token, recent channels), IndexedDB. Forensic tools
  (`mdawsonuk/DiscordExplorer`, `SecurityRonin/discord-desktop-forensic`) recover
  only fragments of "cached channels + messages". **INFERRED** Reading your own
  disk is not API automation, but the cache is incomplete and unstable.
- **VERIFIED** (community reports; the official article returned 403) The Data
  Package ("Request all of my Data") contains only messages *you sent*, grouped by
  channel id, and takes days. Useless for reading incoming DMs.
- **VERIFIED** Official bot: allowed. In servers the owner controls, add a bot
  with the Message Content privileged intent; unverified bots in fewer than 100
  servers can toggle privileged intents in the Developer Portal without review
  (newly created apps have it off by default). Bots receive guild messages where
  they have read permission plus DMs sent *to the bot*; they cannot read
  user-to-user DMs, and `/users/@me/channels` cannot open a DM on a user's behalf.
- **Net**: Discord servers you control -> official bot (this is what OpenClaw,
  LangBot and AstrBot do). Discord DMs -> no compliant read path; treat as out of
  scope or accept ToS risk explicitly.

## 9. Overlay / helper-window UIs and unified inboxes

- **Browser content scripts for web chats** (VERIFIED repos): WhatsApp Web
  suggestion chips (`imlolman/Whatsapp-Suggestion-Chips`,
  `hansvdam/ChatGPTWhatAppWebAnswerSuggestions`,
  `silham/WhatsApp-Web-AI-Assistant`, which also exports conversations); general
  sidebars `dantnan/reply-better-ai` (MIT, local models) and `page-assist`.
  Pattern: scrape the message DOM, inject chips next to the composer,
  click-to-insert. Works for Discord/WhatsApp/Telegram web; WeChat web is dead.
- **Desktop transparent overlays** (VERIFIED repos): `sohzm/cheating-daddy`
  (GPL-3.0, 5,614 stars, pushed 2026-07-23; Electron transparent always-on-top,
  screen + audio capture to Gemini), `pickle-com/glass` (GPL-3.0, 7,604 stars,
  pushed 2025-10-26), `Vectorindia1/wisp` (Electron + FastAPI, Anthropic/OpenAI/
  Ollama), Cluely (commercial). `mediar-ai/screenpipe` (21,743 stars, pushed
  2026-09-28) records screen + audio continuously, OCRs locally and exposes it to
  agents: the "no per-app adapter, read the pixels" fallback.
- **Reading the chat under the overlay**: Windows UIAutomation works for WeChat
  3.9 (`wxauto`, `pywinauto`) and breaks on 4.0 (OCR fallback, ~50 ms with
  OpenCV per the yage.ai survey); macOS Accessibility (`AXUIElement`,
  `kAXValueAttribute`, batched `AXUIElementCopyMultipleAttributeValues`) exposes
  usable WeChat objects for sending and text for many native apps, but Electron/
  canvas surfaces can be empty (set `AXManualAccessibility`, fall back to OCR);
  Android AccessibilityService is strongest but WeChat obfuscates node ids since
  8.0.52 (`worktool` tracks it). The same survey rates DB decryption as the
  lowest-detection way to read WeChat history on Windows/macOS.
- **Overlay window mechanics**: Tauri v2 supports transparent + alwaysOnTop but
  has no per-region hit-testing; overlays poll the cursor and toggle
  `setIgnoreCursorEvents` (issue #13070). Electron has the same pattern plus
  `electron-overlay-window` for attaching to a target window.
- **Unified inbox / remote control**: Beeper (Desktop API above), OpenClaw's
  WebChat / Control UI / TUI / macOS app over the Gateway WS with Tailscale,
  AstrBot and LangBot web dashboards, CowAgent's web console. All are "thin
  client over a local daemon"; none is "suggest my reply".

## 10. Borrowable ideas and patterns

1. **Gateway daemon + typed WS API + thin clients** (OpenClaw). Loopback bind,
   explicit device pairing, idempotency keys, Tailscale for remote. Lessons from
   CVE-2026-25253 and ClawJacked: never auto-connect to a URL from a query
   string, check WebSocket `Origin`, rate-limit loopback too, never auto-approve
   localhost pairing.
2. **Channel plugin contract** (OpenClaw weixin docs): normalized inbound
   message -> session router -> agent -> plugin outbound; plugins declare
   capability metadata (direct/group/media). Chatmaid adapters should be
   read-capability-aware: "can read own history", "can observe live", "can send".
3. **Session keying** `agent:<id>:<channel>:<peer|group>` with SQLite state,
   on-disk transcripts, compaction, and a separate history buffer per thread.
   For chatmaid a session is "my thread with contact X on platform Y".
4. **Model failover chains** with a local primary and cloud fallbacks, or
   "merge" mode; maps directly onto local-draft-then-cloud-refine.
5. **Skills as `SKILL.md` with gating and per-agent allowlists**; reuse for
   per-contact style/persona files and platform etiquette rules.
6. **Wechaty Puppet + puppet-service**: one interface over many protocols and the
   ability to run the fragile adapter out of process (Windows VM for WeChat 3.9
   UIA, or the Android device per `wechat-access.md`).
7. **Bridge data model** (mautrix): login-as-user, portal per conversation, ghost
   identity per remote user, backfill, double puppeting; adopt the schema, skip
   the homeserver. Consider consuming Beeper Desktop API as a ready adapter for
   WhatsApp/Telegram/Signal/Slack instead of writing those.
8. **iLink as control channel only**: use ClawBot to *talk to* chatmaid from the
   phone and to push approved drafts; source WeChat history from the device.
9. **Email**: IMAP IDLE + Drafts APPEND + SMTP on approval; copy Inbox Zero's
   plain-English rules and tone-matched pre-drafts as UX, not its code (AGPL).
10. **Discord**: bot in own servers via Message Content intent; do not build on
    discord.py-self; do not read the client cache.
11. **Overlay**: content-script chips for web chats; Tauri/Electron transparent
    window plus AX/UIA reads for native apps; screenpipe-style OCR as the
    universal last resort (and the only one for WeChat 4.0 on Windows).

## 11. References (accessed 2026-09-27)

- OpenClaw repo and LICENSE: https://github.com/openclaw/openclaw ; https://github.com/openclaw/openclaw/blob/main/LICENSE ; API https://api.github.com/repos/openclaw/openclaw
- OpenClaw Wikipedia: https://en.wikipedia.org/wiki/OpenClaw
- OpenClaw docs: channels https://docs.openclaw.ai/channels ; WeChat https://docs.openclaw.ai/channels/wechat ; WhatsApp https://docs.openclaw.ai/channels/whatsapp ; architecture https://docs.openclaw.ai/concepts/architecture ; sessions https://docs.openclaw.ai/concepts/session ; skills https://docs.openclaw.ai/tools/skills ; local models https://docs.openclaw.ai/gateway/local-models ; model providers https://docs.openclaw.ai/concepts/model-providers ; remote access https://docs.openclaw.ai/gateway/remote ; Tailscale https://docs.openclaw.ai/gateway/tailscale
- Tencent plugin: https://github.com/Tencent/openclaw-weixin ; https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin ; iLink API notes https://github.com/hao-ji-xing/openclaw-weixin/blob/main/weixin-bot-api.md ; https://allclaw.org/blog/what-is-ilink ; https://team400.ai/blog/2026-04-openclaw-wechat-channel-plugin-setup ; https://github.com/x1ah/wechat-ilink-demo ; https://github.com/SiverKing/weixin-ClawBot-API ; Chinese coverage https://www.zhihu.com/question/2019397044381376559 ; https://blog.csdn.net/weixin_42125125/article/details/159414119 ; https://zeeklog.com/wei-xin-zhong-yu-kai-fang-guan-fang-bot-api-clawbot-cha-jian-shen-du-jie-xi-ai-kai-fa-zhe-de-xin-ji-yu-7
- OpenClaw security: CVE-2026-25253 https://github.com/advisories/GHSA-g8p2-7wf7-98mq ; https://socradar.io/blog/cve-2026-25253-rce-openclaw-auth-token/ ; ClawJacked https://www.oasis.security/blog/openclaw-vulnerability ; ClawHub https://www.esecurityplanet.com/threats/hundreds-of-malicious-skills-found-in-openclaws-clawhub/ ; https://www.adminbyrequest.com/en/blogs/openclaw-went-from-viral-ai-agent-to-security-crisis-in-just-three-weeks
- Wechaty: https://github.com/wechaty/wechaty ; https://wechaty.js.org/docs/puppet-providers/ ; https://registry.npmjs.org/wechaty/latest ; puppet repos https://github.com/wechaty/puppet-padlocal , https://github.com/wechaty/puppet-xp , https://github.com/wechaty/puppet-wechat4u , https://github.com/wechaty/matrix-appservice
- Chinese frameworks: https://github.com/nonebot/nonebot2 ; https://github.com/YangRucheng/nonebot-adapter-wxmp ; https://github.com/koishijs/koishi ; https://koishi.chat/en-US/plugins/adapter/wechat-official ; https://github.com/AstrBotDevs/AstrBot ; https://docs.astrbot.app/platform/weixin_oc.html ; https://github.com/AstrBotDevs/AstrBot/issues/4865 ; https://github.com/langbot-app/LangBot ; https://docs.langbot.app/zh/usage/platforms/wechat/weixin ; https://github.com/langbot-app/LangBot/discussions/690 ; https://github.com/zhayujie/CowAgent ; https://github.com/zhayujie/chatgpt-on-wechat/issues/2717 ; https://github.com/hanfangyuan4396/dify-on-wechat ; https://github.com/wangrongding/wechat-bot ; https://github.com/HenryXiaoYang/XYBot ; https://github.com/HenryXiaoYang/XYBotV2
- WeChat adapters: https://github.com/Devo919/Gewechat ; https://github.com/lich0821/WeChatFerry ; https://github.com/cluic/wxauto ; https://github.com/cluic/wxauto4 ; https://pypi.org/project/wxautox4/ ; https://github.com/Hello-Mr-Crab/pywechat ; https://github.com/WeChatPadPro/WeChatPadPro ; feasibility survey https://yage.ai/share/wechat-uia-platform-feasibility-survey-en-20260327.html
- Matrix/Beeper: https://github.com/mautrix ; https://matrix.org/ecosystem/bridges/wechat/ ; https://github.com/duo/matrix-wechat ; https://github.com/LeiShi1313/mautrix-wechat ; https://en.wikipedia.org/wiki/Beeper_(software) ; https://www.beeper.com/faq ; https://developers.beeper.com/desktop-api/ ; https://techcrunch.com/2025/07/16/beepers-all-in-one-messaging-app-relaunches-with-an-on-device-model-and-premium-upgrades/
- Email: https://github.com/elie222/inbox-zero ; https://github.com/elie222/inbox-zero/blob/main/LICENSE ; https://github.com/Mail-0/Zero ; https://github.com/postalsys/emailengine ; https://learn.emailengine.app/docs/licensing ; Gmail push https://developers.google.com/workspace/gmail/api/guides/push ; XOAUTH2 https://developers.google.com/workspace/gmail/imap/xoauth2-protocol ; LSA transition https://support.google.com/a/answer/14114704
- Discord: https://docs.discord.com/developers/topics/oauth2 ; https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots ; https://github.com/dolfies/discord.py-self ; https://github.com/Vendicated/Vencord ; https://vencord.cc/docs/security/ ; intents https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review ; https://github.com/discord/discord-api-docs/discussions/5412 ; data package https://support.discord.com/hc/en-us/articles/360004957991-Your-Discord-Data-Package ; https://support.discord.com/hc/en-us/community/posts/6943310374167 ; cache https://github.com/mdawsonuk/DiscordExplorer ; https://github.com/SecurityRonin/discord-desktop-forensic
- Overlays: https://github.com/sohzm/cheating-daddy ; https://github.com/pickle-com/glass ; https://github.com/Vectorindia1/wisp ; https://github.com/mediar-ai/screenpipe ; https://github.com/imlolman/Whatsapp-Suggestion-Chips ; https://github.com/hansvdam/ChatGPTWhatAppWebAnswerSuggestions ; https://github.com/silham/WhatsApp-Web-AI-Assistant ; https://github.com/dantnan/reply-better-ai ; Tauri click-through https://github.com/tauri-apps/tauri/issues/13070 ; https://blog.manasight.gg/why-i-chose-tauri-v2-for-a-desktop-overlay/ ; https://github.com/SnosMe/electron-overlay-window ; macOS AX https://crowecawcaw.github.io/general/2026/05/30/accessibility-for-computer-use.html
