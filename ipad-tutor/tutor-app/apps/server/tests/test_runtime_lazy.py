"""TIER1_LAZY: the tier-1 reader loads on the first canvas ink, not at startup (the screen tutor doesn't need it)."""

import asyncio

from inktutor_server.config import Settings
from inktutor_server.runtime.runtime import Runtime


def test_reader_loads_once_on_first_use_and_retries_after_a_failed_memory_check(tmp_path):
    async def run():
        rt = Runtime(Settings(data_dir=tmp_path, tier1_enabled=True, tier2_provider="none"))
        loads, fail = [], [True]

        async def start_reader():
            loads.append(1)
            await asyncio.sleep(0.01)
            if fail[0]:
                rt.reader_error = "not enough memory for the tier-1 reader"
                return
            rt.reader = object()

        rt._start_reader = start_reader
        assert rt.reader is None  # nothing at startup
        first = await rt.ensure_reader()
        assert first is None and rt.reader_error  # no memory: the canvas falls back, says why
        fail[0] = False
        a, b = await asyncio.gather(rt.ensure_reader(), rt.ensure_reader())  # two lines read at once: one load
        assert a is b is rt.reader is not None and len(loads) == 2 and rt.reader_error == ""
        await rt.ensure_reader()
        assert len(loads) == 2

    asyncio.run(run())


def test_lazy_is_the_default_and_eager_still_loads_at_start(tmp_path):
    assert Settings(data_dir=tmp_path).tier1_lazy is True

    async def run(lazy):
        rt = Runtime(Settings(data_dir=tmp_path, tier1_enabled=True, tier1_lazy=lazy, tier2_provider="none"))
        loads = []

        async def start_reader():
            loads.append(1)

        rt._start_reader = start_reader
        rt._spawn = lambda coro: coro.close()  # no watchdog in a unit test
        await rt.start()
        return len(loads)

    assert asyncio.run(run(True)) == 0 and asyncio.run(run(False)) == 1
