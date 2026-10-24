"""Serve the hub on the hotspot address and on loopback from one process (design.md)."""

from __future__ import annotations

import ipaddress
import socket

import uvicorn
from fastapi import FastAPI


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def listen_addresses(host: str) -> list[str]:
    """The hotspot address plus loopback, so the operator can also open the hub on the laptop."""
    return [host] if is_loopback(host) else [host, "127.0.0.1"]


def bind(address: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((address, port))
    return sock


def serve(app: FastAPI, host: str, port: int) -> None:
    sockets = [bind(address, port) for address in listen_addresses(host)]
    config = uvicorn.Config(app, workers=1, log_level="warning")
    uvicorn.Server(config).run(sockets=sockets)
