#!/usr/bin/env python3
"""Memory/CPU check for this Mac, so builds and model loads never push it into a swap storm.

  python3 tools/sysmon.py                     one line: available GiB, pressure, swap, load
  python3 tools/sysmon.py --require 22        exit 1 unless >= 22 GiB are available
  python3 tools/sysmon.py --watch 15 --log .cache/sysmon.log --warn 4
                                              append a line every 15 s; flag when available < 4 GiB

Units are GiB. "avail" follows Activity Monitor: total − wired − compressed − app memory
(anonymous − purgeable), i.e. what can be handed out by dropping file cache alone, without
compressing or swapping anyone. "stretch" adds inactive anonymous pages macOS could compress
under pressure (an upper bound; going there makes the Mac sluggish). Stdlib only.
"""

import argparse
import os
import re
import subprocess
import sys
import time


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def snapshot() -> dict:
    vm = sh("vm_stat")
    page = int(re.search(r"page size of (\d+)", vm).group(1))
    pages = {m.group(1): int(m.group(2)) for m in re.finditer(r'^"?([^:"]+)"?:\s+(\d+)\.', vm, re.M)}
    gb = lambda k: pages.get(k, 0) * page / 2**30  # noqa: E731
    total = int(sh("sysctl", "-n", "hw.memsize") or 0) / 2**30
    app = gb("Anonymous pages") - gb("Pages purgeable")
    avail = total - gb("Pages wired down") - gb("Pages occupied by compressor") - app
    stretch = gb("Pages free") + gb("Pages inactive") + gb("Pages speculative") + gb("Pages purgeable")
    swap = re.search(r"used = ([\d.]+)M", sh("sysctl", "-n", "vm.swapusage"))
    level = sh("sysctl", "-n", "kern.memorystatus_level").strip()  # % free as the kernel sees it
    return {
        "total_gb": total,
        "avail_gb": avail,
        "stretch_gb": max(avail, stretch),
        "app_gb": app,
        "wired_gb": gb("Pages wired down"),
        "compressed_gb": gb("Pages occupied by compressor"),
        "swap_used_gb": float(swap.group(1)) / 1024 if swap else 0.0,
        "free_pct": int(level) if level.isdigit() else -1,
        "load": os.getloadavg(),
        "cpus": os.cpu_count() or 1,
    }


def line(s: dict) -> str:
    l1, l5, _ = s["load"]
    return (
        f"{time.strftime('%H:%M:%S')} avail {s['avail_gb']:.1f} (stretch {s['stretch_gb']:.1f}) of {s['total_gb']:.0f} GiB · "
        f"apps {s['app_gb']:.1f} · kernel free {s['free_pct']}% · "
        f"wired {s['wired_gb']:.1f} · compressed {s['compressed_gb']:.1f} · swap {s['swap_used_gb']:.2f} · "
        f"load {l1:.1f}/{l5:.1f} on {s['cpus']} cpus"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", type=float, help="exit 1 unless this many GiB are available")
    ap.add_argument("--watch", type=float, help="seconds between samples (runs until killed)")
    ap.add_argument("--log", help="append samples to this file")
    ap.add_argument("--warn", type=float, default=4.0, help="mark samples below this many available GiB")
    a = ap.parse_args()

    if a.watch:
        while True:
            s = snapshot()
            msg = line(s) + ("  !! LOW MEMORY" if s["avail_gb"] < a.warn or s["free_pct"] < 10 else "")
            if a.log:
                with open(a.log, "a") as f:
                    f.write(msg + "\n")
            else:
                print(msg, flush=True)
            time.sleep(a.watch)

    s = snapshot()
    print(line(s))
    if a.require is not None and s["avail_gb"] < a.require:
        print(f"not enough memory: need {a.require:.1f} GiB, ~{s['avail_gb']:.1f} GiB available", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
