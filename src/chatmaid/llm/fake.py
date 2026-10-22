"""Deterministic in-memory LLM used by tests and --fake-llm."""

import hashlib

from chatmaid.llm.base import LLMError


CANNED: dict[str, str] = {
    "triage": '{"category":"needs_reply","tier":"work","reason":"asks a question"}',
    "reply_draft": (
        '{"candidates":[{"text":"Sure, Friday works.","rationale":"short confirmation"},'
        '{"text":"Friday is fine, thanks.","rationale":"warmer"}],"lookup":""}'
    ),
    "reply_revise": '{"text":"Sure, Friday works. Thanks for the reminder.","rationale":"added thanks"}',
    "chunk_summary": '{"summary":"S1","events":[],"facts":[{"about":"Ann","text":"likes tea"}]}',
    "rolling_summary": '{"summary":"S"}',
    "pii_tag": '{"persons":[]}',
}


class FakeLLM:
    def __init__(self, responses: dict[str, str | list[str]] | None = None, *, dim: int = 4) -> None:
        self.responses = responses or {}
        self.dim = dim
        self.calls: list[dict] = []
        self._failures = 0
        self._served: dict[str, int] = {}

    def fail_next(self, n: int) -> None:
        self._failures = n

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        json_schema: dict | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        grammar: bool = False,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "grammar": grammar,
            }
        )
        if self._failures > 0:
            self._failures -= 1
            raise LLMError("timeout")
        key = system.split("\n", 1)[0].removeprefix("# ")
        reply = self.responses.get(key, "{}")
        if isinstance(reply, list):  # one element per call, the last one repeats
            n = self._served.get(key, 0)
            self._served[key] = n + 1
            return reply[min(n, len(reply) - 1)]
        return reply

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([digest[i % len(digest)] / 256 for i in range(self.dim)])
        return out
