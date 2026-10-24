"""Memory guard (vm_stat). Same model as ink-tutor/tools/sysmon.py: what can be handed out by dropping
file cache alone, without compressing or swapping anyone. Models are only loaded if they fit with headroom."""

from __future__ import annotations

import re
import subprocess
from typing import Literal


def _sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def available_gb() -> float:
    vm = _sh("vm_stat")
    m = re.search(r"page size of (\d+)", vm)
    if not m:
        return 0.0
    page = int(m.group(1))
    pages = {k: int(v) for k, v in re.findall(r'^"?([^:"]+)"?:\s+(\d+)\.', vm, re.M)}

    def gb(k: str) -> float:
        return pages.get(k, 0) * page / 2**30

    total = int(_sh("sysctl", "-n", "hw.memsize") or 0) / 2**30
    app = gb("Anonymous pages") - gb("Pages purgeable")
    return total - gb("Pages wired down") - gb("Pages occupied by compressor") - app


def check(need_gb: float, headroom_gb: float) -> tuple[bool, str]:
    have = available_gb()
    ok = have >= need_gb + headroom_gb
    return ok, f"need {need_gb:.1f} + {headroom_gb:.1f} GiB headroom, {have:.1f} GiB available"


def level(avail_gb: float, low_gb: float, critical_gb: float) -> Literal["ok", "low", "critical"]:
    """Traffic light for the memory watchdog UI: ok (green), low (amber), critical (red)."""
    if avail_gb < critical_gb:
        return "critical"
    if avail_gb < low_gb:
        return "low"
    return "ok"
