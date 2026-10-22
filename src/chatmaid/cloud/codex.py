"""Cloud backend that runs `codex exec` and reads its schema-shaped final message from a file."""

import json
import subprocess
import tempfile
from pathlib import Path

from chatmaid.cloud.base import CloudError, run_cli


# Every feature that lets the model run or reach anything (ruling R14, checked on codex-cli
# 0.163.0: the tool router then reports "code-mode host is disabled" and the model answers in
# text). `-s read-only` stays as defence in depth.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "code_mode_host", "apps", "multi_agent", "plugins",
    "browser_use", "computer_use",
)
_DISABLED_ARGS = [arg for feature in DISABLED_FEATURES for arg in ("--disable", feature)]


class CodexBackend:
    name = "codex"

    def __init__(self, model: str, *, timeout_s: int, runner=subprocess.run) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._runner = runner

    def complete(self, prompt: str, schema: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="chatmaid-codex-") as tmp:
            root = Path(tmp)
            empty = root / "empty"
            empty.mkdir()
            schema_path = root / "schema.json"
            schema_path.write_text(json.dumps(schema))
            out_path = root / "out.json"
            argv = [
                "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                *_DISABLED_ARGS,
                "-s", "read-only", "-C", str(empty), "--skip-git-repo-check", "--color", "never",
                "--output-schema", str(schema_path), "-o", str(out_path),
                *(["-m", self.model] if self.model else []),
                prompt,
            ]
            proc = run_cli(argv, cwd=empty, timeout_s=self._timeout_s, runner=self._runner)
            if proc.returncode != 0:
                raise CloudError(f"codex exited {proc.returncode}: {(proc.stderr or '')[-500:]}")
            try:
                return json.loads(out_path.read_text())
            except FileNotFoundError as e:
                raise CloudError("codex wrote no output file") from e
            except json.JSONDecodeError as e:
                raise CloudError(f"codex output is not valid JSON: {e}") from e
