"""Fair FIFO request queue across all students (REQ-009)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable


class QueueTimeoutError(Exception):
    pass


class QueueCancelledError(Exception):
    """The student left (Finish or expiry) while waiting (AC-009.5)."""


class FairQueue:
    def __init__(self, parallel: int, timeout_s: float, update_s: float) -> None:
        self.parallel, self.timeout_s, self.update_s = parallel, timeout_s, update_s
        self.active = 0
        self._waiting: deque[asyncio.Future[None]] = deque()

    def __len__(self) -> int:
        return len(self._waiting)

    async def wait_turn(self, cancelled: Callable[[], bool] = lambda: False) -> AsyncIterator[int]:
        """Yield the queue position while waiting; return once a slot is held.

        The caller must call `release()` after a successful wait.
        """
        if self.active < self.parallel and not self._waiting:
            self.active += 1
            return
        ticket: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting.append(ticket)
        started = time.monotonic()
        try:
            while True:
                if ticket.done():  # handed a slot between two position updates
                    return
                yield self._waiting.index(ticket) + 1
                if cancelled():
                    raise QueueCancelledError
                remaining = self.timeout_s - (time.monotonic() - started)
                if remaining <= 0:
                    raise QueueTimeoutError
                try:
                    await asyncio.wait_for(
                        asyncio.shield(ticket), timeout=min(self.update_s, remaining)
                    )
                    return
                except TimeoutError:
                    continue
        except BaseException:
            if ticket in self._waiting:
                self._waiting.remove(ticket)
            elif ticket.done():  # the slot was handed over just as we gave up
                self.release()
            raise

    def release(self) -> None:
        while self._waiting:
            ticket = self._waiting.popleft()
            if not ticket.done():
                ticket.set_result(None)  # the slot passes to the next student
                return
        self.active -= 1
