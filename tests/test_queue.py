import asyncio

import pytest

from opentutor.queue import FairQueue, QueueTimeoutError

pytestmark = pytest.mark.anyio


async def test_ac_009_1_fifo_with_positions() -> None:
    queue = FairQueue(parallel=1, timeout_s=5, update_s=0.01)
    order: list[str] = []
    positions: dict[str, list[int]] = {"b": [], "c": []}

    async def student(name: str) -> None:
        async for position in queue.wait_turn():
            positions[name].append(position)
        order.append(name)
        await asyncio.sleep(0.03)
        queue.release()

    await asyncio.gather(student("a"), student("b"), student("c"))
    assert order == ["a", "b", "c"]
    assert positions["b"][0] == 1
    assert positions["c"][0] == 2
    assert queue.active == 0


async def test_ac_009_4_queue_timeout_removes_request() -> None:
    queue = FairQueue(parallel=1, timeout_s=0.05, update_s=0.01)
    async for _ in queue.wait_turn():
        pass
    with pytest.raises(QueueTimeoutError):
        async for _ in queue.wait_turn():
            pass
    assert len(queue) == 0
    queue.release()
    assert queue.active == 0
