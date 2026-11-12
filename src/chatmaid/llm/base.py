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
    ) -> str: ...

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...


class NoLLM:
    """Stands in when no model is needed (no token): any model call raises LLMError."""

    def complete(self, **_) -> str:
        raise LLMError("this run was started without an LM Studio client")

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMError("this run was started without an LM Studio client")
