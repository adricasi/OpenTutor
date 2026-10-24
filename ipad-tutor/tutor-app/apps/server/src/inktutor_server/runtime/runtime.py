"""Runtime: owns the local models.

- Tier 1 (always local): the reader VLM, in-process, loaded at start.
- Tier 2 (local provider): an mlx-vlm server subprocess for `LOCAL_MODEL`, supervised here. One resident
  at a time; a swap stops the old server before starting the new one (non-negotiables 3 and 8).
- Memory guard: a model is only started if it fits in what macOS can hand out without swapping, plus
  headroom. A big MoE model that doesn't fit runs from its expert-offload store instead (experts paged
  from the SSD, a bounded cache in RAM), slower but safe. Measured on the reference Mac (M3 Pro, 36 GB): see docs/MODELS.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

import httpx
from inktutor_protocol import RuntimeStatus, TutorInfo

from ..config import Settings
from . import memory
from .manifest import ModelEntry, load_manifest, snapshot_path

log = logging.getLogger("inktutor.runtime")


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manifest: dict[str, ModelEntry] = {}
        self.reader: Any = None  # recognize.reader.Reader
        self.reader_error = ""
        self.local: Any = None  # tutor.local.LocalMLX once the server answers
        self.local_key = settings.local_model
        self.local_mode = ""  # "full" | "offload (N GB cache)" | "external"
        self.loading: str | None = None
        self.local_error = ""
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.cloud: dict[str, Any] = {}
        self.photo: Any = None  # recognize.photo.PhotoRecognizer, created on first use
        self.voice: Any = None  # voice.engine.VoiceEngine, loaded when a session turns voice on
        self._voice_lock = asyncio.Lock()
        self._reader_lock = asyncio.Lock()
        self._photo_lock = asyncio.Lock()
        # memory watchdog UI (RuntimeStatus): last sample and the last event, numbered so the web shows it once
        self.last_avail: float | None = None
        self._avail_t = 0.0
        self.event = ""
        self.event_id = 0

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        s = self.settings
        self.manifest = load_manifest(s.manifest_path)
        if s.tier1_enabled and not s.tier1_lazy:
            await self._start_reader()
        if s.tier2_provider != "none":  # cloud providers fall back to local on failure
            self._spawn(self.use_local_model(self.local_key))  # don't block startup on a 10-40 s load
        self._init_cloud()
        self._spawn(self._watchdog())

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self._stop_server()
        if self.reader:
            self.reader.close()

    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _init_cloud(self) -> None:
        from ..tutor.cloud import make_cloud_tutors

        self.cloud = make_cloud_tutors(self.settings)

    # ---------------------------------------------------------------- tier 1
    async def ensure_reader(self) -> Any:
        """The tier-1 reader, loaded on first use when TIER1_LAZY (one load at a time; a failed memory check is
        retried on the next call, so the canvas recovers once memory frees up)."""
        if self.reader is not None or not self.settings.tier1_enabled:
            return self.reader
        async with self._reader_lock:
            if self.reader is None:
                self.reader_error = ""
                await self._start_reader()
        return self.reader

    async def _start_reader(self) -> None:
        from ..recognize.reader import Reader

        e = self.manifest.get(self.settings.tier1_text_model)
        path = snapshot_path(e) if e else None
        if not e or not path:
            self.reader_error = f"{self.settings.tier1_text_model} not downloaded (make models)"
            log.warning(self.reader_error)
            return
        ok, why = memory.check(e.memory_gb + 1.0, self.settings.memory_headroom_gb)
        if not ok:
            self.reader_error = f"not enough memory for the tier-1 reader: {why}"
            log.warning(self.reader_error)
            return
        self.reader = Reader(path)
        await self.reader.load()

    # ---------------------------------------------------------------- tier 2 (local)
    def tier2_entries(self) -> list[ModelEntry]:
        return [e for k, e in self.manifest.items() if k.startswith("tier2.")]

    def _plan(self, e: ModelEntry, force_offload: bool = False) -> tuple[Path, float | None, str] | str:
        """(model dir, expert cache GB or None, mode label) or a reason it can't run now."""
        full = None if force_offload else snapshot_path(e)
        avail = memory.available_gb()
        headroom = self.settings.memory_headroom_gb
        if full and avail >= e.memory_gb + self.settings.full_headroom_gb:
            return full, None, "full"
        store = self.settings.spec_dir / "models" / "weights" / e.offload if e.offload else None
        if store and (store / "offload_index.json").exists():
            reserve = 1.0 + self.settings.voice_reserve_gb  # KV cache / activations, and voice if it is turned on
            cache = min(12.0, avail - e.offload_resident_gb - headroom - reserve)
            if cache >= 3.0:
                return store, round(cache, 1), f"offload ({cache:.0f} GB expert cache)"
        if not full:
            return f"{e.short} is not downloaded (make models)"
        need = e.memory_gb + self.settings.full_headroom_gb
        return f"not enough free memory for {e.short}: needs {need:.0f} GiB, {avail:.1f} available"

    async def use_local_model(self, key: str, force_offload: bool = False) -> None:
        """Start (or swap to) the local tier-2 model. Old server is stopped first: one resident at a time."""
        async with self._lock:
            self.local_key = key
            if not self.settings.local_server_managed:
                await self._attach_external()
                return
            e = self.manifest.get(f"tier2.{key}")
            if not e:
                self.local_error = f"unknown local model {key!r} (models/manifest.toml)"
                return
            self.loading = key
            self.local = None
            self.local_error = ""
            try:
                await self._stop_server()
                await asyncio.sleep(1.0)  # let macOS reclaim the old model's memory before measuring
                plan = self._plan(e, force_offload)
                if isinstance(plan, str):
                    self.local_error = plan
                    log.warning(plan)
                    return
                path, cache, mode = plan
                await self._start_server(path, cache)
                served = await self._wait_ready()
                from ..tutor.local import LocalMLX

                self.local = LocalMLX(self.settings.local_openai_base_url, served, key)
                self.local_mode = mode
                log.info("tier 2 ready: %s (%s)", key, mode)
            except Exception as ex:
                self.local_error = f"{key}: {ex}"
                log.exception("tier-2 start failed")
                await self._stop_server()
            finally:
                self.loading = None

    async def _attach_external(self) -> None:
        from ..tutor.local import LocalMLX

        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(self.settings.local_openai_base_url.rstrip("/") + "/models")
                served = r.json()["data"][0]["id"]
            self.local = LocalMLX(self.settings.local_openai_base_url, served, self.local_key)
            self.local_mode = "external"
        except Exception as ex:
            self.local_error = f"no model server at {self.settings.local_openai_base_url}: {ex}"

    async def _start_server(self, model_dir: Path, cache_gb: float | None) -> None:
        port = self.settings.local_openai_base_url.rsplit(":", 1)[-1].split("/")[0]
        args = [
            "-m",
            "mlx_vlm.server",
            "--model",
            str(model_dir),
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--prefill-step-size",
            "512",
            "--max-kv-size",
            "16384",
            "--max-tokens",
            "1024",
            "--log-level",
            "WARNING",
        ]
        if cache_gb is not None:
            args += ["--expert-cache-gb", str(cache_gb)]
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "HF_HOME": str(self.settings.hf_home),
            # automatic prefix caching: the system prompt + rubric + problem are the same on every call of a session,
            # so their KV state is reused (time-to-first-token drops); memory-only, capped, nothing written to disk
            "APC_ENABLED": "1",
            "APC_DISK_ENABLED": "0",
            "APC_MEMORY_MAX_GB": "1.0",
            # sliding-window models (Gemma) can only reuse whole snapshots; the default 2048-token spacing is longer
            # than our prompts, so the shared prefix was never reused. Snapshot every 256 tokens (tens of MB each).
            "APC_CHECKPOINT_INTERVAL_TOKENS": "256",
            "APC_CHECKPOINT_ENTRIES": "8",
            "MLX_VLM_CACHE_HOME": str(self.settings.data_dir / "mlx-vlm"),
        }
        logs = self.settings.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = (logs / "tier2-server.log").open("ab")
        import sys

        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, *args, stdout=out, stderr=out, env=env, start_new_session=True
        )
        log.info(
            "tier-2 server pid %s: %s%s",
            self._proc.pid,
            model_dir.name,
            f" (expert cache {cache_gb} GB)" if cache_gb else "",
        )

    async def _wait_ready(self, timeout: float = 240) -> str:
        base = self.settings.local_openai_base_url.rsplit("/v1", 1)[0]
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=3) as c:
            while time.monotonic() < deadline:
                if self._proc and self._proc.returncode is not None:
                    raise RuntimeError(
                        f"model server exited ({self._proc.returncode}); see .data/logs/tier2-server.log"
                    )
                with contextlib.suppress(httpx.HTTPError, KeyError, ValueError):
                    r = await c.get(base + "/health")
                    if r.status_code == 200 and r.json().get("loaded_model"):
                        return str(r.json()["loaded_model"])
                await asyncio.sleep(1.0)
        raise RuntimeError("model server did not become ready")

    async def _stop_server(self) -> None:
        p, self._proc = self._proc, None
        if not p or p.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(p.wait(), timeout=10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)

    # ---------------------------------------------------------------- photos (Chandra, on demand)
    async def read_photo(self, image: Any, page_id: str) -> tuple[list[Any], str]:
        """Load Chandra, read one photo, unload. One photo at a time; refused when memory is tight."""
        from ..recognize.photo import PhotoRecognizer

        e = self.manifest.get("tier1.photo")
        path = snapshot_path(e) if e else None
        if not e or not path:
            raise RuntimeError("Chandra (tier1.photo) is not downloaded: make models")
        async with self._photo_lock:
            ok, why = memory.check(e.memory_gb + 1.5, self.settings.memory_headroom_gb)  # +1.5: peak while reading
            if not ok:
                raise RuntimeError(f"not enough memory to read a photo now ({why})")
            if self.photo is None:
                self.photo = PhotoRecognizer(path)
            result: tuple[list[Any], str] = await self.photo.read(image, page_id)
            return result

    # ---------------------------------------------------------------- voice (on demand)
    async def voice_engine(self) -> Any:
        from ..voice.engine import VoiceEngine
        from .manifest import named_path

        async with self._voice_lock:
            if self.voice is not None:
                return self.voice
            links = self.settings.hf_home.parent / "links"
            stt, tts = self.manifest.get("voice.stt"), self.manifest.get("voice.tts")
            stt_p = named_path(stt, links) if stt else None
            tts_p = named_path(tts, links) if tts else None
            if not stt_p or not tts_p:
                raise RuntimeError("voice models are not downloaded: make models")
            ok, why = memory.check(3.0, self.settings.memory_headroom_gb)
            if not ok:
                raise RuntimeError(f"not enough memory for voice now ({why})")
            engine = VoiceEngine(stt_p, tts_p)
            await engine.load()
            self.voice = engine
            return engine

    # ---------------------------------------------------------------- watchdog
    async def _watchdog(self) -> None:
        """If memory runs low while a tier-2 model is fully resident, restart it from its offload store."""
        low = 0
        while True:
            await asyncio.sleep(10)
            avail = memory.available_gb()
            self.last_avail, self._avail_t = avail, time.monotonic()
            low = low + 1 if avail < self.settings.memory_critical_gb else 0
            if low:
                log.warning("memory low: %.1f GiB available", avail)
            e = self.manifest.get(f"tier2.{self.local_key}")
            if low >= 2 and self.local_mode == "full" and e and e.offload and not self.loading:
                log.warning("memory pressure: moving %s to its offload store", self.local_key)
                low = 0
                self._event(f"Memory low: tutor switched to slower mode ({avail:.1f} GiB free)")
                self._spawn(self.use_local_model(self.local_key, force_offload=True))

    def _event(self, text: str) -> None:
        self.event = text
        self.event_id += 1

    def status(self) -> RuntimeStatus:
        """What the memory watchdog UI shows: free memory, the local tier-2 model and how it runs."""
        s = self.settings
        if self.last_avail is None or time.monotonic() - self._avail_t > 15:
            self.last_avail, self._avail_t = memory.available_gb(), time.monotonic()
        avail = self.last_avail
        mode: Any
        if self.loading:
            mode, detail = "loading", f"loading {self.loading}…"
        elif self.local is not None:
            mode = self.local_mode if self.local_mode in ("full", "external") else "offload"
            detail = self.local_mode
        elif s.tier2_provider == "none":
            mode, detail = "off", "tier 2 disabled"
        elif "not enough" in self.local_error:
            mode, detail = "refused", self.local_error
        else:
            mode, detail = "unavailable", self.local_error or "not started"
        return RuntimeStatus(
            available_gb=round(avail, 1),
            level=memory.level(avail, s.memory_low_gb, s.memory_critical_gb),
            tier2_model=self.local_key,
            tier2_mode=mode,
            detail=detail,
            event=self.event,
            event_id=self.event_id,
        )

    # ---------------------------------------------------------------- queries
    def tutor(self, provider: str) -> Any:
        if provider == "local":
            return self.local
        return self.cloud.get(provider)

    def tutor_info(self, provider: str) -> TutorInfo:
        if provider in ("anthropic", "openai"):
            t = self.cloud.get(provider)
            return TutorInfo(
                provider=provider,
                model=t.model if t else "",
                ready=bool(t),  # type: ignore[arg-type]
                detail="" if t else "no API key in .env",
            )
        detail = f"loading {self.loading}…" if self.loading else (self.local_error or self.local_mode)
        return TutorInfo(provider="local", model=self.local_key, ready=self.local is not None, detail=detail)

    def settings_view(self, provider: str) -> dict[str, Any]:
        entries = []
        for e in self.tier2_entries():
            store = self.settings.spec_dir / "models" / "weights" / e.offload if e.offload else None
            entries.append(
                {
                    "key": e.short,
                    "name": e.name,
                    "memory_gb": e.memory_gb,
                    "downloaded": snapshot_path(e) is not None
                    or bool(store and (store / "offload_index.json").exists()),
                    "loaded": self.local is not None and self.local_key == e.short,
                }
            )
        return {
            "provider": provider,
            "local_model": self.local_key,
            "local_models": entries,
            "local_mode": self.local_mode,
            "local_error": self.local_error,
            "loading": self.loading,
            "cloud": {"anthropic": "anthropic" in self.cloud, "openai": "openai" in self.cloud},
            # lazy: "ready" means it will load on the first ink (or already has); an error says why it couldn't
            "tier1": {
                "ready": self.reader is not None or (self.settings.tier1_lazy and not self.reader_error),
                "error": self.reader_error,
            },
            "photo": {"loaded": bool(self.photo and self.photo.loaded), "jobs": self.photo.jobs if self.photo else 0},
            "voice": {"loaded": self.voice is not None},
            "memory_available_gb": round(memory.available_gb(), 1),
        }
