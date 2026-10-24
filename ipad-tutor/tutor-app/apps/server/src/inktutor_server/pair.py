"""`make pair`: open an HTTPS Cloudflare quick tunnel to the app and print a QR code for the iPad.

    uv run python -m inktutor_server.pair                  (server already running: make server / make demo)
    uv run python -m inktutor_server.pair --new-token      (old QR codes stop working)

Safari only allows the microphone over HTTPS, hence the tunnel; the LAN URL (plain http) is printed too
for pen-only use. Also advertises "Ink Tutor" over Bonjour while it runs. Ctrl-C closes the tunnel.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

import httpx

from .config import APP_ROOT, Settings
from .pairing import (
    MIC_NOTE,
    Bonjour,
    QuickTunnel,
    clear_state,
    lan_url,
    load_token,
    mdns_url,
    pairing_dir,
    print_qr,
    with_token,
    write_state,
)


def server_up(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


class Pairing:
    """Tunnel + Bonjour + state file for the server's /api/pairing; reopens the tunnel if it drops."""

    def __init__(self, settings: Settings, port: int, path: str = "/", bonjour: bool = True) -> None:
        self.settings, self.port, self.path = settings, port, path
        self.token = load_token(settings.data_dir)
        self.bonjour = Bonjour(port) if bonjour else None
        self.tunnel: QuickTunnel | None = None
        self.advertised = False

    def show_lan(self) -> None:
        print(f"\nOn the same Wi-Fi: {lan_url(self.port)}{self.path.lstrip('/')}")
        print(f"              or: {mdns_url(self.port)}{self.path.lstrip('/')}")
        print(f"  {MIC_NOTE}", flush=True)

    def open_tunnel(self) -> str:
        self.tunnel = QuickTunnel(self.port, pairing_dir(self.settings.data_dir) / "cloudflared.log")
        print("\nOpening a Cloudflare quick tunnel …", flush=True)
        url = self.tunnel.start()
        write_state(self.settings.data_dir, url=url, reachable=False, bonjour=self.advertised)
        reachable = self.tunnel.wait_reachable()
        if not reachable:
            print("  tunnel registered but not reachable yet; the QR may need a few more seconds", flush=True)
        write_state(self.settings.data_dir, url=url, reachable=reachable, bonjour=self.advertised)
        print_qr(with_token(url + self.path, self.token), "iPad (HTTPS, microphone works)")
        return url

    def start(self, tunnel: bool = True) -> None:
        if self.bonjour:
            self.advertised = self.bonjour.start()
            if self.advertised:
                print('Bonjour: advertising "Ink Tutor" (_http._tcp) on the LAN', flush=True)
        self.show_lan()
        if tunnel:
            self.open_tunnel()
        else:
            write_state(self.settings.data_dir, url=None, bonjour=self.advertised)

    def watch(self, stopping: threading.Event) -> None:
        while not stopping.is_set():
            if self.tunnel and not self.tunnel.alive() and not stopping.is_set():
                print("\n! tunnel dropped; opening a new one (new URL, scan again) …", flush=True)
                time.sleep(2)
                self.open_tunnel()
            stopping.wait(1.0)

    def stop(self) -> None:
        if self.tunnel:
            self.tunnel.stop()
        if self.bonjour:
            self.bonjour.stop()
        clear_state(self.settings.data_dir)


def main(argv: list[str] | None = None) -> int:
    settings = Settings()
    ap = argparse.ArgumentParser(prog="make pair", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--port", type=int, default=settings.port, help="the app server's port (default %(default)s)")
    ap.add_argument("--path", default="/", help="page the QR opens, e.g. '/?demo=rec-math-math-22r_69'")
    ap.add_argument("--new-token", action="store_true", help="rotate the access token (old QR codes stop working)")
    ap.add_argument("--no-tunnel", action="store_true", help="LAN only (no microphone on the iPad)")
    ap.add_argument("--no-bonjour", action="store_true", help="don't advertise on the LAN")
    ap.add_argument("--no-caffeinate", action="store_true", help="let the Mac sleep while pairing")
    a = ap.parse_args(argv)

    if a.new_token:
        load_token(settings.data_dir, new=True)
    if not (APP_ROOT / "apps" / "web" / "dist" / "index.html").exists():
        print("! no web build yet: run `make build` (the server serves apps/web/dist)", flush=True)
    if not server_up(a.port):
        print(f"! nothing answers on :{a.port} yet; start it with `make server` (or `make demo`)", flush=True)

    stopping = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    caff = None if a.no_caffeinate else subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])
    pairing = Pairing(settings, a.port, a.path, bonjour=not a.no_bonjour)
    try:
        pairing.start(tunnel=not a.no_tunnel)
        print("\nScan with the iPad camera. The Pair iPad page on the start screen shows the same QR. Ctrl-C to stop.")
        pairing.watch(stopping)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        pairing.stop()
        if caff:
            caff.terminate()
        print("\nPairing stopped (tunnel closed).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
