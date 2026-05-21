"""External-feed (EA / MT5) message contracts.

Mirrors ``F:\\venv-cube\\V3.0\\contract\\ea_messages.py`` 1:1 so the same
EA bridge can talk to both projects without a wire-format change.

Every message carries a ``type`` discriminator
(``msgspec.Struct(tag=..., tag_field="type")``) so the JSON decoder can
pick the right concrete class:

    {"type": "tick",     "symbol": "...", ...}
    {"type": "bar",      "symbol": "...", ...}
    {"type": "account",  "login": "...",  ...}

``EAMessage`` is a typing.Union over every concrete class; passing it
to ``msgspec.json.Decoder(type=EAMessage)`` builds a single decoder
that returns the right Python class for every line on the wire.

``DECODER`` is built once at import time so each
``LineReceiver.lineReceived`` call only pays the amortised cost of a
method call, not a fresh decoder construction.
"""
from __future__ import annotations

import msgspec


# =============================================================================
# Connection lifecycle
# =============================================================================
class Connected(msgspec.Struct, tag="connected", tag_field="type", frozen=True):
    """The EA has just established a TCP connection to our listener.

    Sent exactly once by the EA right after handshake. Carries the
    symbol the bridge is responsible for and the EA-side wall clock
    (seconds since Unix epoch — note: this is the EA's clock, not
    necessarily UTC).
    """

    symbol: str
    time:   int


# =============================================================================
# Tick
# =============================================================================
class Tick(msgspec.Struct, tag="tick", tag_field="type", frozen=True):
    """MT5 tick quote.

    Fields:
        symbol:   Instrument code (e.g., "XAUUSD").
        time:     Broker time string in "YYYY.MM.DD HH:MM:SS" format.
                  NOTE: This is the broker's local time, NOT UTC.
        time_msc: Broker-local epoch milliseconds.
        bid:      Bid price.
        ask:      Ask price.
        last:     Last traded price (may be 0.0 for FX-only feeds).
        volume:   Tick volume (number of price changes, NOT contract volume).

    Properties:
        spread:   ask - bid
        mid:      (ask + bid) / 2  — informational only.
    """

    symbol: str
    time: str
    time_msc: int
    bid: float
    ask: float
    last: float
    volume: int

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) * 0.5


class HistoryTick(msgspec.Struct, tag="history_tick", tag_field="type", frozen=True):
    """One historical Tick replayed by the EA on bridge start-up.

    Schema is identical to ``Tick`` plus a ``flags`` field that carries
    MT5's tick flag bitmask. We model it as a separate type (rather
    than overloading ``Tick``) so the dispatcher can route the live
    feed and the back-fill to different actors if it so chooses.
    """

    symbol:   str
    time:     str
    time_msc: int
    bid:      float
    ask:      float
    last:     float
    volume:   int
    flags:    int

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) * 0.5


class HistoryTickDone(
    msgspec.Struct, tag="history_tick_done", tag_field="type", frozen=True
):
    """Tail marker the EA emits when its tick back-fill is complete."""

    symbol: str
    count:  int


# =============================================================================
# Bar (OHLCV) snapshots
# =============================================================================
class Bar(msgspec.Struct, tag="bar", tag_field="type", frozen=True):
    symbol:    str
    role:      str
    tf_period: str
    time:      str
    is_closed: bool
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    time_msc:  int = 0


class HistoryBar(msgspec.Struct, tag="history_bar", tag_field="type", frozen=True):
    """Same shape as ``Bar`` but flagged as a back-fill record."""

    symbol:    str
    role:      str
    tf_period: str
    time:      str
    is_closed: bool
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    time_msc:  int = 0


class HistoryBarDone(
    msgspec.Struct, tag="history_bar_done", tag_field="type", frozen=True
):
    """Tail marker the EA emits when its bar back-fill is complete."""

    symbol:    str
    role:      str
    tf_period: str


# =============================================================================
# Cube / MetaCube (CubeAll EA — directional-block engine)
# =============================================================================
# A "cube" is a contiguous group of bars sharing the same directional bias
# (UP / DOWN / RANGE) produced by the EA's in-MQL classification engine. A
# "meta_cube" composes adjacent cubes whose synthetic open/close also share
# a bias — i.e. cube-of-cubes. Both are pushed live (forming + closed) and
# replayed from the EA's in-memory buffer at snapshot time.
#
# `dir`   ∈ {"UP", "DOWN", "RANGE", "NONE"}
# `state` ∈ {"FORMING", "ACTIVE", "AT_RISK", "DEAD"}
#
# `is_closed=True` on live `cube` / `meta_cube` marks the bar at which the
# block was finalized; downstream subscribers wanting a "closed only" stream
# should subscribe to ``output_events.CubeClosed`` / ``MetaCubeClosed``
# instead. History replays always set `is_closed=True`.
class Cube(msgspec.Struct, tag="cube", tag_field="type", frozen=True):
    symbol:       str
    role:         str
    tf_period:    str
    id:           int
    dir:          str
    state:        str
    bar_count:    int
    body_high:    float
    body_low:     float
    wick_high:    float
    wick_low:     float
    first_open:   float
    last_close:   float
    bull_volume:  int
    bear_volume:  int
    obv_score:    float
    efficiency:   float
    total_volume: int
    time_start:   str
    time_end:     str
    is_closed:    bool


class HistoryCube(msgspec.Struct, tag="history_cube", tag_field="type", frozen=True):
    """Same shape as ``Cube`` but flagged as a back-fill record."""

    symbol:       str
    role:         str
    tf_period:    str
    id:           int
    dir:          str
    state:        str
    bar_count:    int
    body_high:    float
    body_low:     float
    wick_high:    float
    wick_low:     float
    first_open:   float
    last_close:   float
    bull_volume:  int
    bear_volume:  int
    obv_score:    float
    efficiency:   float
    total_volume: int
    time_start:   str
    time_end:     str
    is_closed:    bool


class HistoryCubeDone(
    msgspec.Struct, tag="history_cube_done", tag_field="type", frozen=True
):
    """Tail marker the EA emits when its cube back-fill is complete (per channel)."""

    symbol:    str
    role:      str
    tf_period: str
    count:     int


class MetaCube(msgspec.Struct, tag="meta_cube", tag_field="type", frozen=True):
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
    is_closed:     bool


class HistoryMetaCube(
    msgspec.Struct, tag="history_meta_cube", tag_field="type", frozen=True
):
    """Same shape as ``MetaCube`` but flagged as a back-fill record."""

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
    is_closed:     bool


class HistoryMetaCubeDone(
    msgspec.Struct, tag="history_meta_cube_done", tag_field="type", frozen=True
):
    """Tail marker the EA emits when its meta-cube back-fill is complete (per channel)."""

    symbol:    str
    role:      str
    tf_period: str
    count:     int


# =============================================================================
# Account / Position / Order
# =============================================================================
class Account(msgspec.Struct, tag="account", tag_field="type", frozen=True):
    """A snapshot of the EA-side trading account state."""

    login:    str
    currency: str
    balance:     float
    equity:      float
    margin:      float
    free_margin: float
    profit:      float


class Position(msgspec.Struct, tag="position", tag_field="type", frozen=True):
    """An open position on the EA-side account.

    ``magic`` and ``comment`` default to 0/"" because older EA builds
    didn't include them; current CubeAll.mq5 ``PushPositions`` always
    sends both, but the defaults keep historical recordings parseable.
    """

    ticket:     int
    symbol:     str
    pos_type:   str
    volume:     float
    open_price: float
    cur_price:  float
    sl:         float
    tp:         float
    profit:     float
    magic:      int = 0
    comment:    str = ""


class Order(msgspec.Struct, tag="order", tag_field="type", frozen=True):
    """A pending order on the EA-side account."""

    ticket:     int
    symbol:     str
    order_type: str
    volume:     float
    open_price: float
    sl:         float
    tp:         float
    comment:    str
    magic:      int = 0


class PositionClosed(
    msgspec.Struct, tag="position_closed", tag_field="type", frozen=True
):
    """Internal EA wire signal: a previously-pushed position is gone.

    NOT part of the public API — the dispatcher converts this into
    the user-facing ``output_events.PositionClosed`` OutputEvent.

    ``close_price`` / ``profit`` come from the actual MT5 closing
    deal (looked up via ``HistorySelect`` on the EA side); when no
    matching deal is found, the EA falls back to its last cached
    snapshot's ``cur_price`` / ``profit`` as an estimate.
    ``close_time`` is broker-side seconds (NOT ms).
    """

    ticket:      int
    symbol:      str
    pos_type:    str
    volume:      float
    open_price:  float
    close_price: float
    profit:      float
    magic:       int
    close_time:  int


class OrderRemoved(
    msgspec.Struct, tag="order_removed", tag_field="type", frozen=True
):
    """Internal EA wire signal: a previously-pushed pending order is gone.

    NOT part of the public API — dispatcher converts to
    ``output_events.OrderCanceled``.

    ``reason`` is one of ``"canceled"``, ``"filled"``, ``"expired"``,
    or ``"unknown"`` (from MT5 ``ORDER_STATE``). ``removed_time`` is
    broker-side seconds.
    """

    ticket:       int
    symbol:       str
    order_type:   str
    volume:       float
    open_price:   float
    sl:           float
    tp:           float
    magic:        int
    reason:       str
    removed_time: int


# =============================================================================
# Polymorphic dispatch
# =============================================================================
EAMessage = (
    Tick
    | Connected
    | HistoryTick
    | HistoryTickDone
    | Bar
    | HistoryBar
    | HistoryBarDone
    | Cube
    | HistoryCube
    | HistoryCubeDone
    | MetaCube
    | HistoryMetaCube
    | HistoryMetaCubeDone
    | Account
    | Position
    | Order
    | PositionClosed
    | OrderRemoved
)


DECODER: msgspec.json.Decoder[EAMessage] = msgspec.json.Decoder(type=EAMessage)


__all__ = [
    "DECODER",
    "Account",
    "Bar",
    "Connected",
    "Cube",
    "EAMessage",
    "HistoryBar",
    "HistoryBarDone",
    "HistoryCube",
    "HistoryCubeDone",
    "HistoryMetaCube",
    "HistoryMetaCubeDone",
    "HistoryTick",
    "HistoryTickDone",
    "MetaCube",
    "Order",
    "Position",
    "Tick",
]
