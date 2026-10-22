"""Reversible pseudonymization: names, addresses, URLs, phone numbers and amounts become placeholders.

Placeholders are [PERSON_n], [EMAIL_n], [URL_n], [PHONE_n] and [AMOUNT_n].
"""

import re
from collections.abc import Callable, Iterable

from chatmaid.config import Config
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM
from chatmaid.llm.jsoncall import complete_json
from chatmaid.store.db import Store

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+"
    r"|(?<![\w@.-])(?:[\w-]+\.)+(?:com|org|net|edu|gov|io|co|cn|me|info|de|uk|jp|app|dev|ai|xyz)"
    r"(?:/[^\s<>\"']*)?(?![\w.-])",
    re.I,
)
PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d \-]{7,}\d(?![\w.])")
AMOUNT_RE = re.compile(
    r"(?:[$¥€£]|RMB|USD|CNY)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:元|美元|块钱|块)"
)

_ISO_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
_PLACEHOLDER_RE = re.compile(r"\[(?:PERSON|EMAIL|URL|PHONE|AMOUNT)_\d+\]")


class Pseudonymizer:
    """Replace sensitive spans with stable placeholders; `rehydrate` reverses them."""

    def __init__(self, deny: Iterable[tuple[str, str]], *, redact_amounts: bool = False) -> None:
        self._redact_amounts = redact_amounts
        self._kinds: dict[str, str] = {}  # lowercase original -> kind
        for original, kind in deny:
            original = original.strip()
            if len(original) >= 2 and kind != "EMAIL":  # EMAIL_RE covers addresses
                self._kinds.setdefault(original.lower(), kind)
                words = original.split()
                if kind == "PERSON" and len(words) >= 2:
                    for word in words:
                        if len(word) >= 3:
                            self._kinds.setdefault(word.lower(), kind)
        originals = sorted(self._kinds, key=len, reverse=True)
        parts = [
            rf"(?<!\w){re.escape(o)}(?!\w)" if o.isascii() else re.escape(o) for o in originals
        ]
        self._deny_re = re.compile("|".join(parts), re.IGNORECASE) if parts else None
        self._placeholders: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}
        self._mapping: dict[str, str] = {}

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._mapping)

    def _placeholder(self, kind: str, original: str) -> str:
        key = (kind, original.lower())
        if key not in self._placeholders:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            placeholder = f"[{kind}_{self._counts[kind]}]"
            self._placeholders[key] = placeholder
            self._mapping[placeholder] = original
        return self._placeholders[key]

    def _sub(self, text: str, pattern: re.Pattern, kind: Callable[[str], str | None]) -> str:
        """Substitute in the stretches between placeholders already inserted."""
        out, last = [], 0
        for hold in [*_PLACEHOLDER_RE.finditer(text), None]:
            end = hold.start() if hold else len(text)
            out.append(pattern.sub(lambda m: self._swap(m, kind), text[last:end]))
            if hold:
                out.append(hold.group())
                last = hold.end()
        return "".join(out)

    def _swap(self, match: re.Match, kind: Callable[[str], str | None]) -> str:
        found = match.group()
        k = kind(found)
        return self._placeholder(k, found) if k else found

    def redact(self, text: str) -> str:
        text = self._sub(text, EMAIL_RE, lambda s: "EMAIL")  # first: names inside addresses
        text = self._sub(text, URL_RE, lambda s: "URL")  # before names: domains can hold them
        if self._deny_re:
            text = self._sub(text, self._deny_re, lambda s: self._kinds[s.lower()])
        text = self._sub(text, PHONE_RE, lambda s: None if _ISO_DATE_RE.match(s) else "PHONE")
        if self._redact_amounts:
            text = self._sub(text, AMOUNT_RE, lambda s: "AMOUNT")
        return text

    @staticmethod
    def rehydrate(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
        unknown: list[str] = []

        def put(m: re.Match) -> str:
            if m.group() in mapping:
                return mapping[m.group()]
            if m.group() not in unknown:
                unknown.append(m.group())
            return m.group()

        return _PLACEHOLDER_RE.sub(put, text), unknown


def tag_persons(llm: LLM, model: str, text: str) -> list[str]:
    """Ask the small local model for person names; keep plausible ones that occur in `text`."""
    reply = complete_json(llm, prompts.load("pii_tag"), text, model, grammar=True, max_tokens=256)
    return [n for n in reply["persons"] if len(n) >= 2 and n in text]


def deny_list_for(store: Store, cfg: Config, conversation_id: int) -> list[tuple[str, str]]:
    """Names and addresses known to be in this conversation, as (original, kind) pairs."""
    deny: list[tuple[str, str]] = [(cfg.owner.name, "PERSON")]
    deny += [(alias, "PERSON") for alias in cfg.owner.aliases]
    contact_ids = [store.owner_contact_id(), *store.participant_contact_ids(conversation_id)]
    for contact_id in contact_ids:
        for identity in store.identities_of(contact_id):
            deny.append((identity["address"], "EMAIL"))
            if identity["display_name"]:
                deny.append((identity["display_name"], "PERSON"))
    for contact_id in store.participant_contact_ids(conversation_id):
        name = store.contact(contact_id)["name"]
        if name:
            deny.append((name, "PERSON"))
    return deny
