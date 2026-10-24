#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["huggingface_hub>=0.34"]
# ///
"""Download / verify the MLX weights pinned in models/manifest.toml into models/weights/hf/ (HF_HOME).

  uv run models/fetch.py                       LOCAL_MODEL (from .env, else the manifest default) + tier1 + voice
  uv run models/fetch.py tier2.gemma4-26b-a4b  specific entries
  uv run models/fetch.py --all                 everything with a repo
  uv run models/fetch.py --verify              check what is on disk against the pinned revisions (no download)
  uv run models/fetch.py --list                show entries, sizes, and whether they are present
  uv run models/fetch.py --offload tier2.qwen36-35b-a3b
                                               build the expert-offload store (mlx_vlm.moe_offload repack) the
                                               runtime uses when the full model doesn't fit in free memory
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models" / "manifest.toml"
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "weights" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# hf_xet buffers chunks in RAM (measured 5.2 GB RSS with 8 workers); plain HTTP streams to disk.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import HfApi, snapshot_download  # noqa: E402
from huggingface_hub.errors import LocalEntryNotFoundError  # noqa: E402


def entries() -> dict[str, dict]:
    m = tomllib.loads(MANIFEST.read_text())
    out = {}
    for group, items in m.items():
        for key, e in items.items():
            out[f"{group}.{key}"] = e
    return out


def local_model() -> str:
    for f in (ROOT / ".env", ROOT / ".env.example"):
        if f.exists():
            for line in f.read_text().splitlines():
                if line.strip().startswith("LOCAL_MODEL="):
                    return line.split("=", 1)[1].split("#")[0].strip()
    return next(k.split(".", 1)[1] for k, e in entries().items() if k.startswith("tier2.") and e.get("default"))


def default_keys(all_: dict[str, dict]) -> list[str]:
    keys = [f"tier2.{local_model()}"]
    keys += [k for k, e in all_.items() if k.startswith(("tier1.", "voice.")) and e.get("repo") and not e.get("on_demand")]
    return keys


def present(e: dict) -> bool:
    try:
        snapshot_download(e["repo"], revision=e["revision"], local_files_only=True)
        return True
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        return False


def verify(key: str, e: dict) -> bool:
    """Every file of the pinned revision exists locally with the expected size."""
    try:
        path = Path(snapshot_download(e["repo"], revision=e["revision"], local_files_only=True))
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        print(f"  ✗ {key}: not downloaded")
        return False
    info = HfApi().model_info(e["repo"], revision=e["revision"], files_metadata=True)
    bad = [s.rfilename for s in info.siblings or [] if not (path / s.rfilename).exists()
           or (s.size is not None and (path / s.rfilename).stat().st_size != s.size)]
    if bad:
        print(f"  ✗ {key}: {len(bad)} missing/short files, e.g. {bad[:3]}")
        return False
    print(f"  ✓ {key}: {len(info.siblings or [])} files @ {e['revision'][:10]}")
    return True


def repack(keys: list[str], entries: dict[str, dict]) -> int:
    """Needs mlx-vlm (run from tutor-app: `uv run --project ../tutor-app python ../ink-tutor/models/fetch.py --offload …`)."""
    import subprocess

    for k in keys:
        e = entries[k]
        if not e.get("offload"):
            print(f"  {k}: no `offload` path in the manifest, skipped")
            continue
        src = snapshot_download(e["repo"], revision=e["revision"], local_files_only=True)
        out = ROOT / "models" / "weights" / e["offload"]
        if (out / "offload_index.json").exists():
            print(f"  ✓ {k}: offload store exists ({out.relative_to(ROOT)})")
            continue
        print(f"→ {k}: repacking into {out.relative_to(ROOT)}", flush=True)
        r = subprocess.run([sys.executable, "-m", "mlx_vlm.moe_offload", "--build", src, "--out", str(out),
                            "--resident-shard-gb", "3"], check=False)
        if r.returncode:
            return r.returncode
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="manifest keys like tier2.gemma4-26b-a4b")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--offload", action="store_true", help="repack the given tier-2 keys for expert offloading")
    a = ap.parse_args()
    all_ = entries()
    with_repo = {k: e for k, e in all_.items() if e.get("repo")}

    if a.list:
        for k, e in with_repo.items():
            print(f"  {'●' if present(e) else '○'} {k:28} {e.get('disk_gb', '?'):>5} GB  {e['repo']}")
        return 0

    keys = list(with_repo) if a.all else (a.keys or default_keys(all_))
    unknown = [k for k in keys if k not in with_repo]
    if unknown:
        print(f"unknown or repo-less keys: {unknown}; known: {list(with_repo)}", file=sys.stderr)
        return 2
    if a.offload:
        return repack(keys, with_repo)
    if a.verify:
        return 0 if all([verify(k, with_repo[k]) for k in keys]) else 1

    total = sum(float(with_repo[k].get("disk_gb", 0)) for k in keys)
    print(f"Fetching {len(keys)} entries (~{total:.1f} GB) into {os.environ['HF_HOME']}")
    for k in keys:
        e = with_repo[k]
        print(f"→ {k}: {e['repo']}@{e['revision'][:10]} (~{e.get('disk_gb', '?')} GB)", flush=True)
        snapshot_download(e["repo"], revision=e["revision"], max_workers=4)
        verify(k, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
