"""`opentutor start`: check the model, print the hub address and QR code, serve (REQ-012)."""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import socket
import sys
import threading
import time

import segno

from .app import create_app
from .checker.maths import check_attempt
from .config import Settings
from .evaluation import eval_command
from .model import ModelError, OllamaModel
from .serve import address_available, is_loopback, serve
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
            print_qr(link)
            print(
                f"  ONLINE DEMO (internet, via Cloudflare — not for students' daily use): {link}\n"
            )
            while tunnel.alive():
                time.sleep(2)
            print("  ! Online demo link dropped; opening a new one (scan again) …")

    threading.Thread(target=keep_open, daemon=True).start()


def warm_up(settings: Settings) -> None:
    """Load the model and start the maths checker before students arrive."""
    asyncio.run(check_attempt("x = 1", True, "x = 1"))
    if settings.model_backend != "ollama":
        return
    print("  Loading the model (the first time can take a minute) …", flush=True)
    model = OllamaModel(
        settings.ollama_url, settings.model, settings.model_digest, settings.model_keep_alive
    )
    try:
        asyncio.run(model.warm_up())
    except ModelError as error:
        print(f"  warning: model warm-up failed ({error}); the first question may be slow.")


# Black on bright white, whatever the terminal's theme: segno's compact output uses the
# terminal's own colours, which inverts the code on a light theme, and phone cameras
# don't read inverted QR codes.
QR_COLORS = "\x1b[30;107m"
QR_RESET = "\x1b[0m"
HALF_BLOCKS = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}


def enable_ansi_colors() -> None:
    """Windows consoles show ANSI colours only once virtual terminal processing is on."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        stdout = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(stdout, ctypes.byref(mode)):
            kernel32.SetConsoleMode(stdout, mode.value | 0x0004)


def print_qr(link: str) -> None:
    """Print a QR code that phone cameras can scan, two modules per character (AC-012.2)."""
    rows = [list(row) for row in segno.make(link, error="m").matrix_iter(border=4)]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))  # one more light quiet-zone row
    for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
        line = "".join(HALF_BLOCKS[pair] for pair in zip(top, bottom, strict=True))
        print(QR_COLORS + line + QR_RESET)


def utf8_output() -> None:
    """Print UTF-8 so the QR code's block characters survive a Windows cp1252 console (AC-012.2)."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    enable_ansi_colors()
    parser = argparse.ArgumentParser(prog="opentutor", description="OpenTutor homework hub")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start the hub")
    start.add_argument("--host", help="address to listen on (the hotspot IP, e.g. 10.42.0.1)")
    start.add_argument("--port", type=int)
    start.add_argument("--dev", action="store_true", help="fake model, localhost only")
    start.add_argument("--online-demo", action="store_true", help="also open a public demo link")
    setup_cmd = sub.add_parser("setup", help="download the configured model and pin its digest")
    setup_cmd.add_argument("--online-demo", action="store_true", help="also fetch cloudflared")
    evaluate = sub.add_parser("eval", help="score the tutor and the safety set (REQ-014)")
    evaluate.add_argument("--tutor", help="only this subject tutor id, e.g. maths")
    evaluate.add_argument(
        "--judge-model", help="Ollama tag for the judge (default: the tutor model)"
    )
    evaluate.add_argument("--safety-only", action="store_true", help="skip the exercise cases")
    args = parser.parse_args(argv)
    if args.command == "setup":
        code = setup(Settings().model)
        if code == 0 and args.online_demo and find_cloudflared() is None:
            print(f"✓ cloudflared installed at {download_cloudflared()}")
        return code

    if args.command == "eval":
        return eval_command(
            Settings(),
            tutor_id=args.tutor,
            judge_model=args.judge_model,
            safety_only=args.safety_only,
        )

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
    if not is_loopback(settings.host) and not address_available(settings.host):
        # AC-012.5: no hub network yet; serve on this laptop only and say how to fix it.
        print(
            f"  {settings.host} isn't on this laptop yet, so the hub Wi-Fi is probably off.\n"
            "  Starting for this laptop only. To let phones in, create the hotspot (README:\n"
            "  nmcli device wifi hotspot …) and start again with --host <hotspot IP>.\n"
        )
        settings = settings.model_copy(update={"host": "127.0.0.1"})
    warm_up(settings)
    url = f"http://{settings.host}:{settings.port}/"
    print()
    if is_loopback(settings.host):
        # A phone that scans a loopback address opens itself, so a QR code can't work here.
        print(f"  OpenTutor is ready on this laptop only: {url}")
        print("  Phones can't open this address. To let them in, start with --host <hotspot IP>.")
    else:
        print_qr(url)
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
