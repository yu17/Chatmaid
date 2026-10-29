"""LM Studio client over its OpenAI-compatible API."""

import openai

from chatmaid.llm.base import LLMError

# An empty think block turns a hybrid model's reasoning off. LM Studio rejects this prefill
# together with response_format ("Failed to initialize samplers"), so a thinking-off call
# cannot enforce a schema: callers validate the reply instead. The request-level switches
# (`reasoning`, `chat_template_kwargs.enable_thinking`) are ignored by LM Studio 0.4.13.
_NO_THINK_PREFILL = {"role": "assistant", "content": "<think>\n\n</think>\n\n"}


class LMStudioClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: int = 600,
        think: bool = True,
    ) -> None:
        self._client = openai.OpenAI(
            base_url=base_url, api_key=token, timeout=timeout_s, max_retries=1
        )
        self._think = think

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        json_schema: dict | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # No max_tokens and no sampling parameters: a local call runs to the model's natural
        # end, and temperature, top_p, top_k, min_p and penalties come from the model's
        # per-model defaults in LM Studio, where the owner tunes them for each model and mode.
        kwargs: dict = {"model": model, "messages": messages}
        if self._think:
            if json_schema is not None:  # reasoning lands in reasoning_content; content is the JSON
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "out", "schema": json_schema, "strict": True},
                }
        else:
            messages.append(dict(_NO_THINK_PREFILL))
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            raise LLMError(str(e)) from e
        if not resp.choices:
            raise LLMError("the completion response has no choices")
        return _strip_think(resp.choices[0].message.content or "")

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


def _strip_think(text: str) -> str:
    """Drop a leading <think>...</think> block if LM Studio ever leaves the reasoning inline."""
    body = text.lstrip()
    if body.startswith("<think>"):
        end = body.find("</think>")
        if end != -1:
            return body[end + len("</think>"):].lstrip()
    return text
