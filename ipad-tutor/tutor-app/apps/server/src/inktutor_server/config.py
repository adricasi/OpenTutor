"""Settings from the environment / `tutor-app/.env` (keys as in `.env.example`). Secrets stay server-side."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[4]  # tutor-app/
SPEC_DIR = APP_ROOT.parent / "ink-tutor"  # docs, fixtures, model manifest + weights


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env", extra="ignore", case_sensitive=False, populate_by_name=True
    )

    data_dir: Path = APP_ROOT / ".data"
    spec_dir: Path = SPEC_DIR
    host: str = "0.0.0.0"
    port: int = 8000

    # tier 1 (always local)
    tier1_enabled: bool = True
    # load the reader on the first canvas ink instead of at startup: the screen tutor doesn't need it, and without
    # its ~3.6 GB the tier-2 model fits fully resident (first step ~4x sooner than expert-offloaded)
    tier1_lazy: bool = True
    tier1_text_model: str = "tier1.text"  # manifest key
    debounce_ms: int = 700  # pen-up debounce the client applies; replay uses the same

    # tier 2
    tier2_provider: Literal["local", "anthropic", "openai", "none"] = Field("local", alias="TUTOR_PROVIDER")
    local_model: str = Field("gemma4-26b-a4b", alias="LOCAL_MODEL")
    local_openai_base_url: str = Field("http://127.0.0.1:8080/v1", alias="LOCAL_OPENAI_BASE_URL")
    local_server_managed: bool = Field(True, alias="LOCAL_SERVER_MANAGED")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-sonnet-5-5", alias="ANTHROPIC_MODEL")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5", alias="OPENAI_MODEL")

    # memory guard (GiB): a model loads only if this much stays free after it. A fully resident tier-2 model
    # gets a bigger margin: loading Qwen3.6 (21 GB) with 2 GiB to spare pushed the reference Mac into 7.7 GiB swap.
    memory_headroom_gb: float = 2.0
    full_headroom_gb: float = 6.0
    voice_reserve_gb: float = 3.0  # an expert cache fills lazily: keep room for voice turning on later
    # memory watchdog UI: below `memory_low_gb` the toolbar light turns amber, below `memory_critical_gb` red
    # (the watchdog also moves a fully resident tier-2 model to its offload store there). Pushed to every open
    # session every `runtime_status_interval_s` seconds.
    memory_low_gb: float = 4.0
    memory_critical_gb: float = 2.0
    runtime_status_interval_s: float = 5.0
    # "keep page history after wipe": how much of the wiped work the tutor is given as prior work
    history_max_lines: int = 12
    history_max_chars: int = 1500
    # screen tutor (docs/SCREEN-TUTOR.md): sent to the overlay helper in `screen.config`; it points, it never acts
    screen_blocklist: str = Field("1Password,Bitwarden,Keychain Access", alias="SCREEN_BLOCKLIST")  # comma-separated
    allow_automation: bool = Field(False, alias="ALLOW_AUTOMATION")  # not built; keep false

    @property
    def fixtures_dir(self) -> Path:
        return self.spec_dir / "fixtures" / "recordings"

    @property
    def annotations_dir(self) -> Path:
        return self.spec_dir / "fixtures" / "annotations"

    @property
    def manifest_path(self) -> Path:
        return self.spec_dir / "models" / "manifest.toml"

    @property
    def hf_home(self) -> Path:
        return self.spec_dir / "models" / "weights" / "hf"


def configure_hf(settings: Settings) -> None:
    """Weights live in the repo; never reach the network for a pinned snapshot."""
    os.environ.setdefault("HF_HOME", str(settings.hf_home))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
