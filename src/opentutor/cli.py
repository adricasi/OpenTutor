"""`opentutor start`: check the model, print the hub address and QR code, serve (REQ-012)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
import threading
import time

import segno

from .app import create_app
from .config import Settings
from .model import OllamaModel
from .serve import is_loopback, serve
from .setup import setup
from .tunnel import QuickTunnel, download_cloudflared, find_cloudflared, with_token


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


def start_online_demo(settings: Settings, token: str) -> None:
    """Open the quick tunnel in the background and reopen it if it drops (AC-017.2, AC-017.6)."""
    binary = find_cloudflared()
    if binary is None:
        print("  Online demo link unavailable: run `opentutor setup --online-demo` first.\n")
        return

    def keep_open() -> None:
        while True:
            tunnel = QuickTunnel(settings.tunnel_port, binary)
            try:
                url = tunnel.start()
            except RuntimeError as error:
                print(f"  Online demo link unavailable ({error}); the hub Wi-Fi still works.")
                return
            link = with_token(url + "/", token)
            segno.make(link, error="m").terminal(compact=True, border=2)
            print(
                f"  ONLINE DEMO (internet, via Cloudflare — not for students' daily use): {link}\n"
            )
            while tunnel.alive():
                time.sleep(2)
            print("  ! Online demo link dropped; opening a new one (scan again) …")

    threading.Thread(target=keep_open, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opentutor", description="OpenTutor homework hub")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start the hub")
    start.add_argument("--host", help="address to listen on (the hotspot IP, e.g. 10.42.0.1)")
    start.add_argument("--port", type=int)
    start.add_argument("--dev", action="store_true", help="fake model, localhost only")
    start.add_argument("--online-demo", action="store_true", help="also open a public demo link")
    setup_cmd = sub.add_parser("setup", help="download the configured model and pin its digest")
    setup_cmd.add_argument("--online-demo", action="store_true", help="also fetch cloudflared")
    args = parser.parse_args(argv)
    if args.command == "setup":
        code = setup(Settings().model)
        if code == 0 and args.online_demo and find_cloudflared() is None:
            print(f"✓ cloudflared installed at {download_cloudflared()}")
        return code

    overrides: dict[str, object] = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.online_demo:
        overrides["online_demo"] = True
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
    if settings.online_demo:
        start_online_demo(settings, app.state.demo_token)
    serve(app, settings.host, settings.port, settings.tunnel_port if settings.online_demo else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
