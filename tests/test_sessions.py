import pytest

from opentutor.sessions import JoinError, SessionStore


def store(clock: list[float] | None = None) -> SessionStore:
    times = clock if clock is not None else [0.0]
    return SessionStore(15, 2, 10, 30 * 60, clock=lambda: times[0])


def test_ac_001_2_valid_nickname_starts_session() -> None:
    session = store().join("Ana_2 b-c", "10.42.0.5")
    assert session.nickname == "Ana_2 b-c"
    assert len(session.id) >= 43  # 32 random bytes, URL-safe: >= 256 bits


@pytest.mark.parametrize("nickname", ["", "   ", "x" * 21, "Ana<script>", "Zoë", "__"])
def test_ac_001_3_invalid_nickname_rejected(nickname: str) -> None:
    with pytest.raises(JoinError) as error:
        store().join(nickname, "10.42.0.5")
    assert error.value.code == "invalid_nickname"


def test_ac_001_4_duplicate_nickname_case_insensitive() -> None:
    hub = store()
    hub.join("Ana", "10.42.0.5")
    with pytest.raises(JoinError) as error:
        hub.join("ana", "10.42.0.6")
    assert error.value.status == 409


def test_ac_001_5_hub_full() -> None:
    hub = store()
    for i in range(15):
        hub.join(f"s{i}", f"10.42.0.{i}")
    with pytest.raises(JoinError) as error:
        hub.join("late", "10.42.0.99")
    assert error.value.code == "hub_full"


def test_ac_001_7_per_address_limits() -> None:
    hub = store()
    hub.join("a", "10.42.0.5")
    hub.join("b", "10.42.0.5")
    with pytest.raises(JoinError) as error:
        hub.join("c", "10.42.0.5")
    assert error.value.code == "too_many_sessions"
    for _ in range(10):
        with pytest.raises(JoinError):
            hub.join("", "10.42.0.7")
    with pytest.raises(JoinError) as error:
        hub.join("", "10.42.0.7")
    assert error.value.code == "too_many_attempts"


def test_ac_011_2_idle_session_expires() -> None:
    clock = [0.0]
    hub = store(clock)
    session = hub.join("Ana", "10.42.0.5")
    clock[0] = 29 * 60
    assert hub.get(session.id) is session
    clock[0] = 29 * 60 + 30 * 60 + 1
    assert hub.get(session.id) is None
    assert len(hub) == 0


def test_ac_009_5_finish_and_expiry_mark_session_ended() -> None:
    clock = [0.0]
    hub = store(clock)
    finished = hub.join("Ana", "10.42.0.5")
    idle = hub.join("Ben", "10.42.0.6")
    hub.end(finished.id)
    assert finished.ended
    clock[0] = 31 * 60
    hub.expire()
    assert idle.ended
