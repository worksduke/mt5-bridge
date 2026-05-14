"""Output events broadcast by Dispatcher to user-registered callbacks.

Six business events map 1:1 to ``EventType``; one operational
event (``EmergencyTickStale``) carries watchdog alerts. All are
``msgspec.Struct`` frozen for cheap equality / serialization.

The ``type`` field on each business event mirrors the matching
``EventType`` int so users can subscribe by either the class or the
enum value.
"""
from __future__ import annotations

import msgspec

from mt5_bridge.contracts.enums import EventType


class PositionOpened(msgspec.Struct, frozen=True, gc=False):
    ticket:        int
    symbol:        str
    pos_type:      str
    volume:        float
    open_price:    float
    sl:            float
    tp:            float
    magic:         int
    comment:       str
    event_time_ms: int
    type:          int = EventType.POSITION_OPENED


class PositionClosed(msgspec.Struct, frozen=True, gc=False):
    ticket:        int
    symbol:        str
    pos_type:      str
    volume:        float
    open_price:    float
    close_price:   float
    profit:        float
    magic:         int
    close_time_ms: int          # broker-side close time × 1000
    event_time_ms: int          # bridge-side detection wall clock
    type:          int = EventType.POSITION_CLOSED


class PositionModified(msgspec.Struct, frozen=True, gc=False):
    ticket:        int
    symbol:        str
    sl:            float
    tp:            float
    event_time_ms: int
    type:          int = EventType.POSITION_MODIFIED


class OrderPlaced(msgspec.Struct, frozen=True, gc=False):
    ticket:        int
    symbol:        str
    order_type:    str
    volume:        float
    open_price:    float
    sl:            float
    tp:            float
    event_time_ms: int
    type:          int = EventType.ORDER_PLACED


class OrderModified(msgspec.Struct, frozen=True, gc=False):
    ticket:        int
    symbol:        str
    open_price:    float
    sl:            float
    tp:            float
    event_time_ms: int
    type:          int = EventType.ORDER_MODIFIED


class OrderCanceled(msgspec.Struct, frozen=True, gc=False):
    """Pending order disappeared — canceled, filled, or expired.

    ``reason`` is one of ``"canceled"``, ``"filled"``, ``"expired"``,
    or ``"unknown"`` (the EA reads MT5's ``ORDER_STATE`` history to
    figure this out; "unknown" only when history lookup fails).
    """

    ticket:          int
    symbol:          str
    order_type:      str
    volume:          float
    open_price:      float
    sl:              float
    tp:              float
    magic:           int
    reason:          str
    removed_time_ms: int        # broker-side ORDER_TIME_DONE × 1000
    event_time_ms:   int
    type:            int = EventType.ORDER_CANCELED


class EmergencyTickStale(msgspec.Struct, frozen=True, gc=False):
    """Tick heartbeat timeout — user should manually inspect open positions."""

    symbol:            str
    last_tick_time_ms: int
    silence_seconds:   float
    detected_at_ms:    int


__all__ = [
    "EmergencyTickStale",
    "OrderCanceled",
    "OrderModified",
    "OrderPlaced",
    "PositionClosed",
    "PositionModified",
    "PositionOpened",
]
