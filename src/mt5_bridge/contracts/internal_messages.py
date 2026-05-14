"""Actor-to-actor internal messages.

Not part of the public API — these flow only between Executor /
Dispatcher / Watchdog and never reach user-registered callbacks
directly. See ``output_events`` for things users should subscribe to.
"""
from __future__ import annotations

from typing import Any

import msgspec

from mt5_bridge.contracts.enums import TradeState


class TradeResult(msgspec.Struct, frozen=True, gc=False):
    """Unified return value for every ``MT5Client`` trade method.

    Fields:
        ok:      Whether MT5 accepted the request (retcode == DONE).
        ticket:  Position / order ticket assigned by MT5 (0 on failure).
        retcode: MT5 retcode from ``order_send`` result.
        comment: Broker / MT5 comment (often the rejection reason).
    """

    ok:      bool
    ticket:  int = 0
    retcode: int = 0
    comment: str = ""


class TradeStateChanged(msgspec.Struct, frozen=True, gc=False):
    """Executor → Dispatcher: a request transitioned state."""

    request_id: str
    state:      TradeState
    ticket:     int = 0
    retcode:    int = 0
    comment:    str = ""


class SymbolStale(msgspec.Struct, frozen=True, gc=False):
    """Watchdog → Executor: this symbol's tick stream went silent."""

    symbol:            str
    last_tick_time_ms: int
    silence_seconds:   float


class SymbolAlive(msgspec.Struct, frozen=True, gc=False):
    """Watchdog → Executor: this symbol's tick stream recovered."""

    symbol:       str
    tick_time_ms: int


class _RetryAttempt(msgspec.Struct, frozen=True, gc=False):
    """Executor → Executor (via scheduler): re-deliver a command after backoff.

    Carries the original command so the Executor can re-enter ``_exec``
    with an incremented attempt counter without reconstructing state.
    Underscore-prefixed because it's strictly internal.
    """

    request_id: str
    cmd_kind:   str
    cmd:        Any
    attempt:    int


# Kept for backwards-compatibility with the existing MT5Client stub
# (``open_position`` was typed to return ``PositionOpenResult``). New
# code should use ``TradeResult``.
class PositionOpenResult(msgspec.Struct, frozen=True, gc=False):
    """DEPRECATED: use ``TradeResult`` instead."""

    success: bool
    ticket:  int = 0
    retcode: int = 0
    comment: str = ""


__all__ = [
    "PositionOpenResult",
    "SymbolAlive",
    "SymbolStale",
    "TradeResult",
    "TradeStateChanged",
    "_RetryAttempt",
]
