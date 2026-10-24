"""`make demo`: a scripted session. The browser replays a real recording into the canvas, live.

    uv run python -m inktutor_server.demo                          (rec-math-math-22r_69, real timing)
    uv run python -m inktutor_server.demo --speed 2 --pair         (twice as fast; + HTTPS tunnel QR for the iPad)

Starts the server (serving the built web app) unless one already answers on the port, then opens
`/?demo=<stem>`. That page fetches `GET /api/demo/<stem>` (the recording's pen and eraser events, with its
annotation's mode and problem), starts a session and plays the events into the canvas as pen pointer
events with their recorded timing, so the client batches and sends ink exactly as a hand would and the
tutor reacts live. Ctrl-C stops the server it started.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from typing import Any

from fastapi import FastAPI, HTTPException

from .config import APP_ROOT, Settings
from .fixtures import list_fixtures, load

DEFAULT_STEM = "rec-math-math-22r_69"
REPLAYED = {"page.new", "stroke.start", "stroke.points", "stroke.end", "erase", "erase.end", "tool", "session.end"}


def demo_script(stem: str, settings: Settings) -> dict[str, Any]:
    path = next((p for p in list_fixtures(settings) if p.stem == stem), None)
    if path is None:
        raise KeyError(stem)
    rec = load(path, settings)
    page = rec.pages[0] if rec.pages else None
    return {
        "stem": stem,
        "mode": rec.mode,
        "problem": rec.session.problem,
        "duration_ms": rec.session.duration_ms,
        "page": {"width": page.width, "height": page.height} if page else None,
        "events": [e for e in rec.events if e.get("type") in REPLAYED],
    }


def install(app: FastAPI, settings: Settings) -> None:
    """`GET /api/demo/{stem}`. Call before the SPA catch-all route is added."""

    @app.get("/api/demo/{stem}")
    def get_demo(stem: str) -> dict[str, Any]:
        try:
            return demo_script(stem, settings)
        except KeyError:
            raise HTTPException(404, f"no fixture recording {stem}") from None


def start_server(port: int, no_models: bool) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    if no_models:  # plumbing check without loading anything: no marks will appear
        env.update({"TIER1_ENABLED": "false", "TUTOR_PROVIDER": "none"})
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "inktutor_server.app:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        env=env,
        cwd=str(APP_ROOT),
        start_new_session=True,  # Ctrl-C goes to us; we stop it cleanly
    )


def main(argv: list[str] | None = None) -> int:
    from .pair import Pairing, server_up

    settings = Settings()
    ap = argparse.ArgumentParser(prog="make demo", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--stem", default=DEFAULT_STEM, help="fixture recording (default %(default)s)")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed (1 = real timing)")
    ap.add_argument("--port", type=int, default=settings.port)
    ap.add_argument("--pair", action="store_true", help="also open the HTTPS tunnel and print a QR of the demo")
    ap.add_argument("--no-open", action="store_true", help="print the URL, don't open a browser")
    ap.add_argument("--no-models", action="store_true", help="start the server with tier 1/2 off (plumbing only)")
    a = ap.parse_args(argv)

    try:
        demo_script(a.stem, settings)
    except KeyError:
        names = ", ".join(p.stem for p in list_fixtures(settings))
        raise SystemExit(f"no fixture recording {a.stem!r}; have: {names}") from None
    if not (APP_ROOT / "apps" / "web" / "dist" / "index.html").exists():
        raise SystemExit("no web build: run `make build` first (make demo does)")

    stopping = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    proc = None
    if server_up(a.port):
        print(f"Using the server already running on :{a.port}", flush=True)
    else:
        print(f"Starting the server on :{a.port} (models load first; this can take a minute) …", flush=True)
        proc = start_server(a.port, a.no_models)
        while not server_up(a.port):
            if proc.poll() is not None:
                print(f"error: the server exited ({proc.returncode})", file=sys.stderr)
                return 1
            if stopping.wait(1.0):
                break

    path = f"/?demo={a.stem}" + (f"&speed={a.speed:g}" if a.speed != 1 else "")
    url = f"http://localhost:{a.port}{path}"
    pairing = Pairing(settings, a.port, path) if a.pair else None
    try:
        if not stopping.is_set():
            print(f"\nDemo: {url}", flush=True)
            if not a.no_open:
                webbrowser.open(url)
            if pairing:
                pairing.start()
            print("\nEach open of the link starts a new session and replays the recording. Ctrl-C to stop.", flush=True)
            if pairing:
                pairing.watch(stopping)
            else:
                while not stopping.wait(1.0):
                    pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if pairing:
            pairing.stop()
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        print("\nDemo stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
