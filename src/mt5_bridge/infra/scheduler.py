"""Delayed-callback scheduler abstraction.

Two implementations:

- ``ReactorScheduler``  — used when RPC is enabled (Twisted reactor running
                          on the main thread). Hops onto the reactor thread
                          via ``callFromThread`` and uses ``callLater``.
- ``TimerScheduler``    — used when RPC is disabled (no reactor running).
                          Backs onto ``threading.Timer`` so the bridge stays
                          functional without Twisted.

Both let the Executor schedule a retry without blocking its mailbox.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Protocol


class Scheduler(Protocol):
    """Schedule ``fn`` to run after ``delay_seconds``.

    Must be callable from any thread (the Executor's actor thread in
    practice). The implementation hops onto the right thread itself.
    """

    def schedule(self, delay_seconds: float, fn: Callable[[], None]) -> None: ...


class ReactorScheduler:
    """Twisted-backed scheduler.

    ``callFromThread`` is required because ``callLater`` itself is NOT
    thread-safe — it must be invoked on the reactor thread.

    The reactor parameter is typed ``Any`` because Twisted is a lazy
    import (only imported when the bridge's [rpc] section is enabled);
    we can't reference its concrete protocol type without forcing the
    import.
    """

    def __init__(self, reactor: Any) -> None:
        self._reactor: Any = reactor

    def schedule(self, delay_seconds: float, fn: Callable[[], None]) -> None:
        self._reactor.callFromThread(self._reactor.callLater, delay_seconds, fn)


class TimerScheduler:
    """``threading.Timer`` fallback for the no-RPC mode.

    Each call spawns a daemon timer thread that fires once and exits.
    Cheap enough for retry frequencies (sub-second to seconds).
    """

    def schedule(self, delay_seconds: float, fn: Callable[[], None]) -> None:
        t = threading.Timer(delay_seconds, fn)
        t.daemon = True
        t.start()


__all__ = ["ReactorScheduler", "Scheduler", "TimerScheduler"]
