"""Deterministic in-memory LLM used by tests and --fake-llm."""

import hashlib

from chatmaid.llm.base import LLMError


class FakeLLM:
    def __init__(self, responses: dict[str, str] | None = None, *, dim: int = 4) -> None:
        self.responses = responses or {}
        self.dim = dim
        self.calls: list[dict] = []
        self._failures = 0

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
    ) -> str:
        if self._failures > 0:
            self._failures -= 1
            raise LLMError("timeout")
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        key = system.split("\n", 1)[0].removeprefix("# ")
        return self.responses.get(key, "{}")

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([digest[i % len(digest)] / 256 for i in range(self.dim)])
        return out
