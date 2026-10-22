"""Shared pieces of the cloud backends: error type, protocol, schema and the subprocess helper."""

import os
import subprocess
from pathlib import Path
from typing import Protocol


class CloudError(Exception):
    """A cloud CLI could not be run or did not return a usable answer."""


class CloudBackend(Protocol):
    name: str
    model: str

    def complete(self, prompt: str, schema: dict) -> dict: ...


CLOUD_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["text", "rationale"],
    "additionalProperties": False,
}


def scrubbed_env() -> dict[str, str]:
    """A copy of the environment without the variables that make `claude` refuse to nest."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def run_cli(argv: list[str], *, cwd: Path, timeout_s: int, runner=subprocess.run) -> subprocess.CompletedProcess:
    """Run a cloud CLI with no stdin, a scrubbed environment and a timeout."""
    try:
        return runner(
            argv,
            cwd=cwd,
            env=scrubbed_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise CloudError(f"{argv[0]} timed out after {timeout_s}s") from e
    except FileNotFoundError as e:
        raise CloudError(f"{argv[0]} is not installed or not on PATH") from e
