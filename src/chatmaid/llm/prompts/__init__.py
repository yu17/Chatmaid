"""Prompt files: line 1 `# <name>`, system text, then one fenced json block holding the schema."""

import json
import re
from dataclasses import dataclass
from importlib import resources

_FENCE = re.compile(r"```json\n(.*?)\n```", re.S)


@dataclass(frozen=True)
class Prompt:
    name: str
    system: str
    schema: dict


def load(name: str) -> Prompt:
    text = (resources.files(__package__) / f"{name}.md").read_text(encoding="utf-8")
    match = _FENCE.search(text)
    if match is None:
        raise ValueError(f"prompt {name!r} has no json schema block")
    system = (text[: match.start()] + text[match.end() :]).strip()
    return Prompt(name, system, json.loads(match.group(1)))
