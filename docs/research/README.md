# Research notes

Preliminary research done before requirements were written. Each file states
what is verified from a source, what is inferred, and what is uncertain, and
ends with a dated reference list.

| File | Question it answers |
|------|---------------------|
| [wechat-access.md](wechat-access.md) | How to get complete WeChat history (incl. media), live notifications, and optionally send messages, given an unrooted Android primary device |
| [hybrid-llm-architecture.md](hybrid-llm-architecture.md) | Existing local-plus-cloud LLM architectures and building blocks; candidate patterns for this project |
| [prior-art.md](prior-art.md) | Existing multi-platform chat assistants and bridges (OpenClaw, Wechaty, Chinese bot frameworks, Matrix bridges, email and Discord options) worth borrowing from |
| [wechat-one-shot-extraction.md](wechat-one-shot-extraction.md) | (2026-10-21) Step-by-step one-time full-history extraction: migration vs backup, key extraction per desktop OS, stickers, minimal Windows VM, login-seat limits |
| [evolution-email-access.md](evolution-email-access.md) | (2026-10-21) Reusing the owner's Evolution accounts and cache for read-only mail access, notifications, and approved drafting/sending |

## Conclusions across the three notes (2026-09-27)

WeChat

- A secondary login (desktop or iPad) holds only recent messages. Full history
  has to be pushed in once with the phone's "迁移聊天记录" (chat history
  migration); the "备份" (backup) feature produces a restore-only encrypted blob
  and is not useful to us.
- The lowest-risk way to read history and detect new messages is passive
  decryption of the desktop client's local SQLCipher database, then polling it.
  The official WeChat for Linux 4.1.x client exists, and on Linux the database
  keys are readable from process memory, so an all-Linux stack is viable.
- Tencent's official iLink "ClawBot" (the OpenClaw WeChat plugin) is a separate
  bot contact. It cannot read the owner's chats with other people or groups, so
  it is at most a control channel, never the data source.
- All third-party access violates Tencent's terms. Passive DB reading and UI
  automation have low ban risk; hook and protocol bots have high ban risk.
  Tencent also filed a large DMCA takedown of DB-decryption repos in January
  2026, so tooling moves between mirrors.
- Sending programmatically is the weakest link. Free UI automation (wxauto) only
  works on WeChat 3.9.x on Windows, the 4.x port is marked no longer updated,
  and only a paid beta (wxautox4, 4.0.5 only) targets 4.x. macOS
  AppleScript/Accessibility and Linux xdotool coordinate clicking are the
  remaining options, both untested for us.

Hybrid LLM

- Start with a local core (ingest to SQLite plus a vector index, local model
  drafts the reply) and a consent-gated cloud "refine" hop that sees only a
  reversibly pseudonymized draft and excerpt. The Minions/PAPILLON style of
  abstraction delegation is the phase-2 target for heavy analysis; learned
  cascades are a later cost optimizer, not a privacy mechanism.
- Reversible pseudonymization beats irreversible redaction on quality. Chinese
  nicknames and kinship terms defeat off-the-shelf NER, so a contact-book
  deny-list plus a local LLM second pass is needed.
- Qwen3.5/3.6/3.8 are the default local models for Chinese plus English;
  the document has a VRAM-tiered table with verified quantized sizes.
- No surveyed open-source assistant does content-aware privacy routing; they
  route by static config, user choice, or error failover. That is the gap.

Prior art

- OpenClaw is the reference for the gateway daemon, channel plugin, session,
  skill, and model-failover shapes. Copy the shapes, not its localhost-trust
  defaults, which caused several 2026 security incidents.
- Discord forbids automating a user account even read-only. The compliant path
  is an official bot in servers the owner controls; bots can never read DMs.
- Email is easy: IMAP IDLE plus a Drafts folder plus SMTP on approval.

The verified cloud CLI invocations (codex and claude, tool-less and stateless) are in [`docs/superpowers/plans/2026-10-22-phase1b-reply-drafts.md`](../superpowers/plans/2026-10-22-phase1b-reply-drafts.md), Global Constraints.
