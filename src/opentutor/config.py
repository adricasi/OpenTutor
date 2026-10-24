"""Hub configuration: defaults here, overridden by OPENTUTOR_* environment variables."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENTUTOR_", env_file=None)

    # Network (NFR-001): never 0.0.0.0. On a NetworkManager hotspot the hub is usually 10.42.0.1.
    host: str = "127.0.0.1"
    port: int = 8000

    # Model runtime (ADR-0004). The tag and digest are settled by the T-001 spike.
    model_backend: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:e4b"
    model_digest: str = ""
    parallel_requests: int = 2

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

    tutors_dir: Path = PACKAGE_DIR / "tutors"
    prompts_dir: Path = PACKAGE_DIR / "prompts"
