import subprocess
from pathlib import Path

import pytest

from opentutor.config import Settings
from opentutor.serve import listen_addresses
from opentutor.setup import model_digest, setup, write_config

LISTING = (
    "NAME          ID              SIZE    MODIFIED\n"
    "gemma4:e4b    abc123def456    9.6 GB  1 minute ago\n"
)


def fake_runner(pull_code: int = 0):  # type: ignore[no-untyped-def]
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        out = LISTING if command[1] == "list" else ""
        code = pull_code if command[1] == "pull" else 0
        return subprocess.CompletedProcess(command, code, out, "network down" if code else "")

    return runner


def test_ac_012_1_setup_pulls_and_pins_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert setup("gemma4:e4b", fake_runner(), which=lambda _: "/usr/bin/ollama") == 0
    assert 'model_digest = "abc123def456"' in (tmp_path / "opentutor.toml").read_text()
    assert Settings().model_digest == "abc123def456"  # picked up from opentutor.toml


def test_ac_012_1_setup_reports_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert setup("gemma4:e4b", fake_runner(), which=lambda _: None) == 1
    assert setup("gemma4:e4b", fake_runner(pull_code=1), which=lambda _: "/x") == 1


def test_model_digest_lookup() -> None:
    assert model_digest("gemma4:e4b", fake_runner()) == "abc123def456"
    assert model_digest("gemma4:e2b", fake_runner()) is None


def test_write_config_keeps_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "opentutor.toml"
    path.write_text('host = "10.42.0.1"\nmodel = "old"\n')
    write_config("gemma4:e2b", "fff", path)
    text = path.read_text()
    assert 'host = "10.42.0.1"' in text
    assert 'model = "gemma4:e2b"' in text
    assert "old" not in text


def test_nfr_001_listens_on_hotspot_and_loopback_only() -> None:
    assert listen_addresses("10.42.0.1") == ["10.42.0.1", "127.0.0.1"]
    assert listen_addresses("127.0.0.1") == ["127.0.0.1"]


def test_ac_012_5_missing_hotspot_address_detected() -> None:
    from opentutor.serve import address_available

    assert address_available("127.0.0.1")
    assert not address_available("203.0.113.7")  # TEST-NET-3: never on this machine
