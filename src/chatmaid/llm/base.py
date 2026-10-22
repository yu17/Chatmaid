"""LLM protocol shared by the real LM Studio client and the fake."""

from typing import Protocol


class LLMError(Exception):
    """Any failure talking to the language model server."""


class LLM(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        json_schema: dict | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        think: bool = False,
    ) -> str: ...

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...
