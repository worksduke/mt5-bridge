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


class BarClosed(msgspec.Struct, frozen=True, gc=False):
    """A K-line just finalized. Fires once per closed bar.

    Triggered by:
        - Live ``Bar`` with ``is_closed=True`` (EA pushes once when next bar starts)
        - ``HistoryBar`` from cold-start backfill (always closed; ``is_history=True``)

    Live Bars with ``is_closed=False`` (in-progress updates) do NOT trigger
    this event — subscribe to the raw ``Bar`` class for those.
    """

    symbol:        str
    role:          str         # "execution" / "tactical" / "strategic"
    tf_period:     str         # "M5" / "H1" / "D1" 等
    time:          str         # broker-local "YYYY.MM.DD HH:MM:SS"
    time_msc:      int         # broker-local epoch ms
    open:          float
    high:          float
    low:           float
    close:         float
    volume:        int
    is_history:    bool        # True = from history backfill, False = live close
    event_time_ms: int         # bridge-side detection wall-clock
    type:          int = EventType.BAR_CLOSED


class CubeClosed(msgspec.Struct, frozen=True, gc=False):
    """A Cube directional-block just finalized. Fires once per closed cube.

    Triggered by:
        - Live ``Cube`` with ``is_closed=True`` (EA pushes when a cube transitions
          from FORMING/ACTIVE/AT_RISK to DEAD)
        - ``HistoryCube`` from cold-start backfill (always closed; ``is_history=True``)

    Forming-cube updates (live ``Cube`` with ``is_closed=False``) do NOT trigger
    this event — subscribe to the raw ``Cube`` class for the intra-bar stream.

    ``dir`` ∈ {"UP", "DOWN", "RANGE", "NONE"}; ``state`` is "DEAD" at this layer
    by definition (the EA only emits ``is_closed=True`` when the cube finalizes).
    """

    symbol:        str
    role:          str
    tf_period:     str
    id:            int
    dir:           str
    state:         str
    bar_count:     int
    body_high:     float
    body_low:      float
    wick_high:     float
    wick_low:      float
    first_open:    float
    last_close:    float
    bull_volume:   int
    bear_volume:   int
    obv_score:     float
    efficiency:    float
    total_volume:  int
    time_start:    str
    time_end:      str
    is_history:    bool        # True = from history backfill, False = live close
    event_time_ms: int         # bridge-side detection wall-clock
    type:          int = EventType.CUBE_CLOSED


class MetaCubeClosed(msgspec.Struct, frozen=True, gc=False):
    """A MetaCube (cube-of-cubes) just finalized. Fires once per closed meta-cube.

    Same trigger semantics as ``CubeClosed`` but at the higher composition
    level — a MetaCube groups adjacent cubes whose synthetic open/close
    share a directional bias. ``first_cube_id`` / ``last_cube_id`` reference
    the cube ``id`` field on the same channel.
    """

    symbol:        str
    role:          str
    tf_period:     str
    id:            int
    dir:           str
    state:         str
    cube_count:    int
    bar_count:     int
    body_high:     float
    body_low:      float
    wick_high:     float
    wick_low:      float
    first_open:    float
    last_close:    float
    bull_volume:   int
    bear_volume:   int
    obv_score:     float
    efficiency:    float
    total_volume:  int
    first_cube_id: int
    last_cube_id:  int
    time_start:    str
    time_end:      str
    is_history:    bool
    event_time_ms: int
    type:          int = EventType.META_CUBE_CLOSED


__all__ = [
    "BarClosed",
    "CubeClosed",
    "EmergencyTickStale",
    "MetaCubeClosed",
    "OrderCanceled",
    "OrderModified",
    "OrderPlaced",
    "PositionClosed",
    "PositionModified",
    "PositionOpened",
]
