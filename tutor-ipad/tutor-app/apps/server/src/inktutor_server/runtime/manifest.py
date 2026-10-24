"""models/manifest.toml → pinned, local-only model entries (`make models` downloads them)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelEntry:
    key: str  # e.g. "tier2.gemma4-26b-a4b"
    name: str
    repo: str
    revision: str
    memory_gb: float
    default: bool = False
    on_demand: bool = False
    offload: str = ""  # expert-offload store, relative to models/weights/
    offload_resident_gb: float = 3.0

    @property
    def short(self) -> str:
        return self.key.split(".", 1)[1]


def load_manifest(path: Path) -> dict[str, ModelEntry]:
    data = tomllib.loads(path.read_text())
    out: dict[str, ModelEntry] = {}
    for group, items in data.items():
        for key, e in items.items():
            if not e.get("repo"):
                continue
            k = f"{group}.{key}"
            out[k] = ModelEntry(
                key=k,
                name=e.get("name", key),
                repo=e["repo"],
                revision=e.get("revision") or "main",
                memory_gb=float(e.get("memory_gb", 0)),
                default=bool(e.get("default", False)),
                on_demand=bool(e.get("on_demand", False)),
                offload=e.get("offload", ""),
                offload_resident_gb=float(e.get("offload_resident_gb", 3.0)),
            )
    return out


def snapshot_path(e: ModelEntry) -> Path | None:
    """Local folder of the pinned revision, or None if it isn't downloaded. Never touches the network."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return Path(snapshot_download(e.repo, revision=e.revision, local_files_only=True))
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        return None


def named_path(e: ModelEntry, links_dir: Path) -> Path | None:
    """The pinned snapshot under a folder named after the repo (models/weights/links/<repo name>).
    mlx-audio infers the architecture from the folder name, which a commit-hash snapshot folder doesn't carry."""
    snap = snapshot_path(e)
    if snap is None:
        return None
    links_dir.mkdir(parents=True, exist_ok=True)
    link = links_dir / e.repo.split("/")[-1]
    if link.is_symlink() and link.resolve() != snap.resolve():
        link.unlink()
    if not link.exists():
        link.symlink_to(snap, target_is_directory=True)
    return link
