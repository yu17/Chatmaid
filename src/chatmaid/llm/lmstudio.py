"""LM Studio client over its OpenAI-compatible API."""

import openai

from chatmaid.llm.base import LLMError


class LMStudioClient:
    def __init__(self, base_url: str, token: str, *, timeout_s: int = 600) -> None:
        self._client = openai.OpenAI(
            base_url=base_url, api_key=token, timeout=timeout_s, max_retries=1
        )

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
    ) -> str:
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not think:
            # An empty think block disables reasoning; LM Studio rejects it together with
            # response_format, so the schema is not enforced here (callers validate).
            kwargs["messages"].append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
        elif json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": json_schema, "strict": True},
            }
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            raise LLMError(str(e)) from e
        if not resp.choices:
            raise LLMError("the completion response has no choices")
        return resp.choices[0].message.content or ""

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(model=model, input=texts)
        except openai.OpenAIError as e:
            raise LLMError(str(e)) from e
        return [list(d.embedding) for d in resp.data]

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client.models.list())
        except openai.OpenAIError as e:
            raise LLMError(str(e)) from e
