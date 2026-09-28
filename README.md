# chatmaid

A personal chat assistant that watches the owner's main messaging platforms
(WeChat, Discord, email), reads conversation history, and suggests the owner's
next message. It runs a local LLM for anything that touches private chat
content and calls online LLMs only for revision, refinement, or heavier
analysis on request. The user-facing side will be either a unified remote
control interface or an overlay/helper window on top of the chat apps.

## Status

Pre-design. The repository currently holds research notes only; see
[`docs/research/`](docs/research/README.md). A requirements write-up from the
owner comes next, followed by a design.

## Guiding constraints

- WeChat is the most important platform and the most hostile to third-party
  tooling. The primary login lives on an unrooted Android phone, so all access
  goes through secondary logins (desktop, iPad) or on-phone notification
  capture.
- Chat logs are sensitive. Raw content stays on the local machine; cloud models
  see redacted, summarized, or explicitly approved material only.

## Layout

```
docs/research/   research notes that inform the design
```
