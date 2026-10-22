"""Cloud backend that runs `claude -p` and reads the structured output from its JSON envelope."""

import json
import subprocess
import tempfile
from pathlib import Path

from chatmaid.cloud.base import CloudError, run_cli


class ClaudeBackend:
    name = "claude"

    def __init__(self, model: str, *, timeout_s: int, runner=subprocess.run) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._runner = runner

    def complete(self, prompt: str, schema: dict) -> dict:
        argv = [
            "claude", "-p", "--tools", "", "--no-session-persistence", "--setting-sources", "",
            "--disable-slash-commands", "--strict-mcp-config", "--output-format", "json",
            "--json-schema", json.dumps(schema),
            *(["--model", self.model] if self.model else []),
            prompt,
        ]
        with tempfile.TemporaryDirectory(prefix="chatmaid-claude-") as tmp:
            proc = run_cli(argv, cwd=Path(tmp), timeout_s=self._timeout_s, runner=self._runner)
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            if proc.returncode != 0:
                raise CloudError(f"claude exited {proc.returncode}: {(proc.stderr or '')[-500:]}") from e
            raise CloudError(f"claude output is not valid JSON: {e}") from e
        if not isinstance(envelope, dict):
            raise CloudError("claude returned no structured output")
        if (
            envelope.get("is_error")
            or envelope.get("subtype") != "success"
            or not isinstance(envelope.get("structured_output"), dict)
        ):
            raise CloudError(envelope.get("result") or "claude returned no structured output")
        return envelope["structured_output"]
