"""Header-based rules for spotting bulk / automated mail."""

from __future__ import annotations

import re

from chatmaid.connectors.evolution.rfc822 import ParsedMail

NOREPLY_RE = re.compile(r"^(no-?reply|do-?not-?reply|notifications?|newsletter|mailer-daemon|postmaster|bounce)", re.I)

_BULK_PRECEDENCE = {"bulk", "list", "junk"}


def is_bulk(parsed: ParsedMail) -> bool:
    h = parsed.list_headers
    if "list-unsubscribe" in h or "list-id" in h:
        return True
    if h.get("precedence", "").strip().lower() in _BULK_PRECEDENCE:
        return True
    auto = h.get("auto-submitted")
    if auto is not None and auto.strip().lower() != "no":
        return True
    return bool(NOREPLY_RE.match(parsed.from_.email.split("@", 1)[0]))
