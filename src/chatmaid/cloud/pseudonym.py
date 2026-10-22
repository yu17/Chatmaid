"""Reversible pseudonymization: names, addresses, URLs, phone numbers and amounts become placeholders.

Placeholders are [PERSON_n], [EMAIL_n], [URL_n], [PHONE_n] and [AMOUNT_n].
"""

import re
import string
from collections.abc import Callable, Iterable

from chatmaid.config import Config
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM
from chatmaid.llm.jsoncall import complete_json
from chatmaid.store.db import Store

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+"
    r"|(?<![A-Za-z0-9@._-])(?:[A-Za-z0-9-]+\.)+"
    r"(?:com|org|net|edu|gov|io|co|cn|me|info|de|uk|jp|app|dev|ai|xyz"
    r"|ca|fr|us|au|nl|it|es|se|ch|at|be|dk|fi|no|pl|ru|br|in|kr|hk|tw|sg|nz|ie|pt|cz"
    r"|hu|gr|tr|mx|ar|za)"
    r"(?:/[^\s<>\"']*)?(?![A-Za-z0-9_]|[.-][A-Za-z0-9_])",
    re.I,
)
# Candidates only: `_looks_like_phone` drops dates and short numbers. A trailing full stop
# ends a sentence, not the match (`(?!\w|\.\w)`), so "call 312-555-0142." is caught too.
PHONE_RE = re.compile(r"(?<![\w.])\+?(?:\(\d{1,4}\)|\d)[\d\s().\-/]{5,}\d(?!\w|\.\w)")
AMOUNT_RE = re.compile(
    r"(?:[$¥€£]|RMB|USD|CNY)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:元|美元|块钱|块)"
)

_LEADING_DATE_RE = re.compile(
    r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\s*"
)
_NAME_PART_STOPWORDS = frozenset(
    "the and from team support office group dept department inc ltd llc co gmbh via of for"
    .split()
)
_PLACEHOLDER_RE = re.compile(r"\[(?:PERSON|EMAIL|URL|PHONE|AMOUNT)_\d+\]")


def _looks_like_phone(candidate: str) -> bool:
    """A `PHONE_RE` match is a phone when, after one leading date, 7 or more digits remain."""
    rest = _LEADING_DATE_RE.sub("", candidate, count=1)
    return sum(ch.isdigit() for ch in rest) >= 7


def _name_parts(full_name: str) -> list[str]:
    """Parts of a multi-word name worth redacting alone: capitalised, not a stopword."""
    parts = []
    for word in full_name.split():
        word = word.strip(string.punctuation)
        if (
            len(word) >= 3
            and "A" <= word[0] <= "Z"
            and word.lower() not in _NAME_PART_STOPWORDS
        ):
            parts.append(word)
    return parts


class Pseudonymizer:
    """Replace sensitive spans with stable placeholders; `rehydrate` reverses them."""

    def __init__(self, deny: Iterable[tuple[str, str]], *, redact_amounts: bool = False) -> None:
        self._redact_amounts = redact_amounts
        self._kinds: dict[str, str] = {}  # lowercase original -> kind
        self._part_kinds: dict[str, str] = {}  # name part, exact case -> kind
        for original, kind in deny:
            original = original.strip()
            if len(original) >= 2 and kind != "EMAIL":  # EMAIL_RE covers addresses
                self._kinds.setdefault(original.lower(), kind)
                if kind == "PERSON" and len(original.split()) >= 2:
                    for part in _name_parts(original):
                        self._part_kinds.setdefault(part, kind)
        originals = sorted(self._kinds, key=len, reverse=True)
        alts = [
            rf"(?<!\w){re.escape(o)}(?!\w)" if o.isascii() else re.escape(o) for o in originals
        ]
        self._deny_re = re.compile("|".join(alts), re.IGNORECASE) if alts else None
        # Parts match case-sensitively, whole word: "Will Smith" must not redact "I will".
        parts = sorted(
            (w for w in self._part_kinds if w.lower() not in self._kinds), key=len, reverse=True
        )
        self._part_re = (
            re.compile("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in parts)) if parts else None
        )
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
        if self._part_re:
            text = self._sub(text, self._part_re, lambda s: self._part_kinds[s])
        text = self._sub(text, PHONE_RE, lambda s: "PHONE" if _looks_like_phone(s) else None)
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
