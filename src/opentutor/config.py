"""Hub configuration: defaults here, overridden by OPENTUTOR_* environment variables."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

PACKAGE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    # Precedence: OPENTUTOR_* environment variables > opentutor.toml (written by setup) > defaults.
    model_config = SettingsConfigDict(env_prefix="OPENTUTOR_", toml_file="opentutor.toml")

    # Network (NFR-001): never 0.0.0.0. On a NetworkManager hotspot the hub is usually 10.42.0.1.
    host: str = "127.0.0.1"
    port: int = 8000

    # Model runtime (ADR-0004). The tag and digest are settled by the T-001 spike.
    model_backend: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:e4b"
    model_digest: str = ""
    parallel_requests: int = 2
    # Keep the model loaded between questions (Ollama unloads idle models after 5 min).
    model_keep_alive: str = "8h"

    # Limits (requirements.md)
    max_sessions: int = 15
    max_sessions_per_address: int = 2
    join_attempts_per_minute: int = 10
    session_idle_minutes: int = 30
    max_message_chars: int = 2000
    max_upload_bytes: int = 10 * 1024 * 1024
    image_max_side: int = 1024
    turns_kept: int = 20
    queue_timeout_s: float = 120.0
    first_output_timeout_s: float = 60.0
    call_timeout_s: float = 120.0
    queue_update_s: float = 5.0

    # Teacher view (REQ-013): on the hub laptop only unless allowed (ASSUMPTION A-6).
    teacher_from_network: bool = False

    # Optional online demo link (REQ-017): off by default; the tunnel gets its own listener.
    online_demo: bool = False
    tunnel_port: int = 8001

    # iPad handwriting tutor (REQ-021, ADR-0008): where GET /ipad sends students. Empty = not
    # connected; /ipad then shows how to connect it. Only absolute http(s) URLs are accepted.
    ipad_url: str = ""

    tutors_dir: Path = PACKAGE_DIR / "tutors"
    prompts_dir: Path = PACKAGE_DIR / "prompts"

    @field_validator("ipad_url", mode="before")
    @classmethod
    def _absolute_http_url(cls, value: object) -> str:
        """Blank anything that isn't an absolute http(s) URL, so /ipad never redirects there."""
        text = str(value or "").strip()
        try:
            parts = urlsplit(text)
        except ValueError:
            return ""
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        if any(ch.isspace() or ord(ch) < 32 for ch in text):
            return ""
        return text

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, TomlConfigSettingsSource(settings_cls)
