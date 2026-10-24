"""`opentutor start`: check the model, print the hub address and QR code, serve (REQ-012)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

import segno

from .app import create_app
from .config import Settings
from .model import OllamaModel
from .serve import is_loopback, serve
from .setup import setup


def ollama_exposed(host: str, port: int = 11434) -> bool:
    """True if the Ollama API answers on the hub's Wi-Fi address (NFR-001 forbids that)."""
    if is_loopback(host):
        return False
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def check_start(settings: Settings) -> list[str]:
    """Problems that must stop the hub from starting; empty when it is safe to start."""
    problems = []
    if settings.host in {"0.0.0.0", "::"}:  # noqa: S104 - this is the check that forbids it
        problems.append(
            "Refusing to listen on all interfaces; set OPENTUTOR_HOST to the hotspot IP."
        )
    if settings.model_backend == "ollama":
        ok, message = asyncio.run(
            OllamaModel(settings.ollama_url, settings.model, settings.model_digest).health()
        )
        if not ok:
            problems.append(message)
        if ollama_exposed(settings.host):
            problems.append(
                f"Ollama answers on {settings.host}:11434. "
                "Set OLLAMA_HOST=127.0.0.1 and restart it."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opentutor", description="OpenTutor homework hub")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start the hub")
    start.add_argument("--host", help="address to listen on (the hotspot IP, e.g. 10.42.0.1)")
    start.add_argument("--port", type=int)
    start.add_argument("--dev", action="store_true", help="fake model, localhost only")
    sub.add_parser("setup", help="download the configured model and pin its digest")
    args = parser.parse_args(argv)
    if args.command == "setup":
        return setup(Settings().model)

    overrides: dict[str, object] = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.dev:
        overrides.update(model_backend="fake", host="127.0.0.1")
    settings = Settings(**overrides)  # type: ignore[arg-type]

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    problems = check_start(settings)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    url = f"http://{settings.host}:{settings.port}/"
    print()
    segno.make(url, error="m").terminal(compact=True, border=2)
    print(f"\n  OpenTutor is ready. Students: join the hub Wi-Fi and open {url}")
    print(f"  Model: {settings.model if settings.model_backend == 'ollama' else 'fake (dev)'}\n")
    app = create_app(settings)
    print(
        f"  Teacher view: http://127.0.0.1:{settings.port}/teacher  PIN {app.state.teacher.pin}\n"
    )
    serve(app, settings.host, settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
