"""`opentutor setup`: download the configured Gemma 4 model and pin its digest (REQ-012)."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

CONFIG_FILE = Path("opentutor.toml")
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


def model_digest(model: str, runner: Runner = run) -> str | None:
    """The digest `ollama list` shows for `model` (Ollama prints a 12-char prefix)."""
    listing = runner(["ollama", "list"])
    for line in listing.stdout.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] in {model, f"{model}:latest"}:
            return fields[1]
    return None


def write_config(model: str, digest: str, path: Path = CONFIG_FILE) -> None:
    """Record the model tag and digest, keeping any other settings already in the file."""
    text = path.read_text() if path.exists() else ""
    for key, value in (("model", model), ("model_digest", digest)):
        line = f'{key} = "{value}"'
        pattern = re.compile(rf"^{key}\s*=.*$", re.M)
        text = pattern.sub(line, text) if pattern.search(text) else f"{text}{line}\n"
    path.write_text(text)


def setup(
    model: str, runner: Runner = run, which: Callable[[str], str | None] = shutil.which
) -> int:
    if which("ollama") is None:
        print(
            "error: Ollama is not installed. "
            "Install it from https://ollama.com/download, then rerun."
        )
        return 1
    print(f"Downloading {model} (several GB; this needs internet once) …", flush=True)
    pulled = runner(["ollama", "pull", model])
    if pulled.returncode != 0:
        print(f"error: ollama pull {model} failed:\n{pulled.stderr.strip()}")
        return 1
    digest = model_digest(model, runner)
    if digest is None:
        print(f"error: {model} was pulled but `ollama list` doesn't show it.")
        return 1
    write_config(model, digest)
    print(f"✓ {model} ready, digest {digest} pinned in {CONFIG_FILE}")
    return 0
