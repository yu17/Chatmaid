"""In-memory cloud backend for tests."""


class FakeCloud:
    def __init__(self, name: str = "fake", model: str = "fake", response: dict | Exception | None = None) -> None:
        self.name = name
        self.model = model
        self.response = response if response is not None else {"text": "ok", "rationale": "fake"}
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response
