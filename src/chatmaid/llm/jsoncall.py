"""Validated JSON calls: one model call whose reply must match a prompt's schema."""

import json

from chatmaid.llm.base import LLM, LLMError
from chatmaid.llm.prompts import Prompt

RETRY_NOTE = "Your reply was not valid JSON matching the schema; reply with the JSON object only."


def validated(value, schema: dict):
    """Check `value` against the small JSON-schema subset our prompts use.

    Raises ValueError on a wrong type or missing required key; malformed array items are dropped.
    """
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError("expected array")
        items = []
        for item in value:
            try:
                items.append(validated(item, schema["items"]))
            except ValueError:
                continue
        return items
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return {
            key: validated(value[key], sub) if key in value else _missing(key)
            for key, sub in schema["properties"].items()
        }
    return value


def schema_skeleton(schema: dict) -> str:
    """Render the JSON shape of a schema (the subset our prompts use) as an example."""
    kind = schema.get("type")
    if kind == "object":
        inner = ", ".join(f'"{k}": {schema_skeleton(v)}' for k, v in schema["properties"].items())
        return "{" + inner + "}"
    if kind == "array":
        return "[" + schema_skeleton(schema["items"]) + "]"
    return '"<string>"'


def _missing(key: str):
    raise ValueError(f"missing required key {key!r}")


def complete_json(
    llm: LLM,
    prompt: Prompt,
    user: str,
    model: str,
    *,
    grammar: bool,
    max_tokens: int = 2048,
) -> dict:
    """One reasoning-free call whose reply is validated against the prompt schema.

    Invalid JSON or a missing required key gets one retry that shows the model its reply.
    """
    if not grammar:  # no grammar: the model must be told the keys
        user = f"{user}\n\nReply with exactly this JSON shape: {schema_skeleton(prompt.schema)}"
    message = user
    for attempt in range(2):
        raw = llm.complete(
            system=prompt.system,
            user=message,
            model=model,
            json_schema=prompt.schema,
            max_tokens=max_tokens,
            grammar=grammar,
        )
        try:
            return validated(json.loads(raw), prompt.schema)
        except ValueError:  # JSONDecodeError is a ValueError
            message = f"{user}\n\nYour previous reply:\n{raw}\n\n{RETRY_NOTE}"
    raise LLMError(f"{prompt.name}: model did not return valid JSON for the schema")
