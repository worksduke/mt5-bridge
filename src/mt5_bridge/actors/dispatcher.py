"""Dispatcher actor: state machine + pub/sub.

Owns the per-ticket state (last seen ``Position`` / ``Order`` snapshot)
and decides when to emit each of the six ``OutputEvent`` types.
Routes ``Tick`` to the watchdog and ``Account`` to the kelly actor
(if either is wired up).

User-facing callbacks are invoked synchronously on the dispatcher
thread, wrapped in try/except so a misbehaving subscriber cannot kill
the actor.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import pykka

from mt5_bridge.contracts.ea_messages import (
    Account,
    Bar,
    Connected,
    Cube,
    HistoryBar,
    HistoryCube,
    HistoryMetaCube,
    HistoryTick,
    MetaCube,
    Order,
    Position,
    Tick,
)

# Aliased imports avoid name collision with the public OutputEvents
# of the same name. PositionClosedSignal / OrderRemovedSignal are
# the raw EA wire messages; PositionClosed / OrderCanceled below are
# the user-facing OutputEvents the dispatcher emits in response.
from mt5_bridge.contracts.ea_messages import (
    OrderRemoved as OrderRemovedSignal,
)
from mt5_bridge.contracts.ea_messages import (
    PositionClosed as PositionClosedSignal,
)
from mt5_bridge.contracts.enums import EventType
from mt5_bridge.contracts.internal_messages import TradeStateChanged
from mt5_bridge.contracts.output_events import (
    BarClosed,
    CubeClosed,
    EmergencyTickStale,
    MetaCubeClosed,
    OrderCanceled,
    OrderModified,
    OrderPlaced,
    PositionClosed,
    PositionModified,
    PositionOpened,
)
from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.actors.dispatcher")


def _now_ms() -> int:
    return int(time.time() * 1000)


class Dispatcher(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(self) -> None:
        super().__init__()
        self._callbacks:       dict[Any, list[Callable[[Any], None]]] = {}
        self._callbacks_all:   list[Callable[[Any], None]] = []
        self._known_positions: dict[int, Position] = {}
        self._known_orders:    dict[int, Order] = {}
        self._pending:         dict[str, TradeStateChanged] = {}
        self._watchdog_ref:    pykka.ActorRef | None = None
        self._kelly_ref:       pykka.ActorRef | None = None

    # ── proxy-callable wiring API (used by facade) ────────────
    def register_callback(self, key: Any, cb: Callable[[Any], None]) -> None:
        self._callbacks.setdefault(key, []).append(cb)

    def register_callback_all(self, cb: Callable[[Any], None]) -> None:
        self._callbacks_all.append(cb)

    def set_watchdog_ref(self, ref: pykka.ActorRef | None) -> None:
        self._watchdog_ref = ref

    def set_kelly_ref(self, ref: pykka.ActorRef | None) -> None:
        self._kelly_ref = ref

    # ── pykka entry point ─────────────────────────────────────
    def on_receive(self, message: object) -> None:
        if isinstance(message, Tick):
            self._broadcast(message)
            self._safe_tell(self._watchdog_ref, message)
            return

        if isinstance(message, HistoryTick):
            # Surfacing back-fill ticks to subscribers is useful (cold-start
            # priming) and the watchdog should also see them so a long
            # back-fill doesn't immediately mark the symbol stale.
            self._broadcast(message)
            self._safe_tell(self._watchdog_ref, message)
            return

        if isinstance(message, Account):
            self._broadcast(message)
            self._safe_tell(self._kelly_ref, message)
            return

        if isinstance(message, Position):
            self._handle_position(message)
            return

        if isinstance(message, Order):
            self._handle_order(message)
            return

        if isinstance(message, PositionClosedSignal):
            self._handle_position_closed(message)
            return

        if isinstance(message, OrderRemovedSignal):
            self._handle_order_removed(message)
            return

        if isinstance(message, Connected):
            _log.info("ea_connected_msg", symbol=message.symbol, time=message.time)
            self._broadcast(message)
            return

        if isinstance(message, TradeStateChanged):
            self._pending[message.request_id] = message
            # Only include ticket/retcode/comment when they carry
            # information. In-flight states (POSITION_OPENING etc.)
            # legitimately have ticket=0/retcode=0 because the SDK
            # round-trip hasn't happened yet — surfacing those zeros
            # in every log line makes successful runs look broken.
            log_kwargs: dict[str, Any] = {
                "request_id": message.request_id,
                "state":      message.state.name,
            }
            if message.ticket:
                log_kwargs["ticket"] = message.ticket
            if message.retcode:
                log_kwargs["retcode"] = message.retcode
            if message.comment:
                log_kwargs["comment"] = message.comment
            _log.info("trade_state_changed", **log_kwargs)

            # Broadcast so callers can subscribe to TradeStateChanged
            # directly to learn open/close/cancel results without
            # waiting for the EA push channel (the snapshot-based
            # OutputEvent path may lag, or be absent in EA-less setups).
            self._broadcast(message)
            return

        if isinstance(message, EmergencyTickStale):
            self._broadcast(message)
            return

        if isinstance(message, Bar):
            # Raw broadcast first (preserves subscribe(Bar, cb) semantics for
            # users who want both closed + in-progress). Only emit BarClosed
            # for is_closed=True so subscribers of EventType.BAR_CLOSED get
            # exactly one event per finalized bar.
            self._broadcast(message)
            if message.is_closed:
                self._emit_bar_closed(message, is_history=False)
            return

        if isinstance(message, HistoryBar):
            # History bars are always closed by definition (replayed from
            # the broker's bar archive).
            self._broadcast(message)
            self._emit_bar_closed(message, is_history=True)
            return

        if isinstance(message, Cube):
            # Raw broadcast preserves subscribe(Cube, cb) — both forming and
            # closed pass through. CubeClosed only fires on finalization so
            # EventType.CUBE_CLOSED subscribers see exactly one event per cube.
            self._broadcast(message)
            if message.is_closed:
                self._emit_cube_closed(message, is_history=False)
            return

        if isinstance(message, HistoryCube):
            # History cubes are always closed (replayed from EA buffer).
            self._broadcast(message)
            self._emit_cube_closed(message, is_history=True)
            return

        if isinstance(message, MetaCube):
            self._broadcast(message)
            if message.is_closed:
                self._emit_meta_cube_closed(message, is_history=False)
            return

        if isinstance(message, HistoryMetaCube):
            self._broadcast(message)
            self._emit_meta_cube_closed(message, is_history=True)
            return

        # *Done markers and anything else: pass through unchanged.
        self._broadcast(message)

    # ── position / order state machine ────────────────────────
    def _handle_position(self, pos: Position) -> None:
        prev = self._known_positions.get(pos.ticket)
        self._known_positions[pos.ticket] = pos

        if prev is None:
            self._broadcast(PositionOpened(
                ticket        = pos.ticket,
                symbol        = pos.symbol,
                pos_type      = pos.pos_type,
                volume        = pos.volume,
                open_price    = pos.open_price,
                sl            = pos.sl,
                tp            = pos.tp,
                magic         = pos.magic,
                comment       = pos.comment,
                event_time_ms = _now_ms(),
            ))
            return

        if (prev.sl, prev.tp) != (pos.sl, pos.tp):
            self._broadcast(PositionModified(
                ticket        = pos.ticket,
                symbol        = pos.symbol,
                sl            = pos.sl,
                tp            = pos.tp,
                event_time_ms = _now_ms(),
            ))

    def _handle_order(self, ord_: Order) -> None:
        prev = self._known_orders.get(ord_.ticket)
        self._known_orders[ord_.ticket] = ord_

        if prev is None:
            self._broadcast(OrderPlaced(
                ticket        = ord_.ticket,
                symbol        = ord_.symbol,
                order_type    = ord_.order_type,
                volume        = ord_.volume,
                open_price    = ord_.open_price,
                sl            = ord_.sl,
                tp            = ord_.tp,
                event_time_ms = _now_ms(),
            ))
            return

        if (prev.open_price, prev.sl, prev.tp) != (ord_.open_price, ord_.sl, ord_.tp):
            self._broadcast(OrderModified(
                ticket        = ord_.ticket,
                symbol        = ord_.symbol,
                open_price    = ord_.open_price,
                sl            = ord_.sl,
                tp            = ord_.tp,
                event_time_ms = _now_ms(),
            ))

    def _handle_position_closed(self, m: PositionClosedSignal) -> None:
        # Forget the ticket so a subsequent re-open re-fires PositionOpened
        # rather than being mistaken for a modification of the closed one.
        self._known_positions.pop(m.ticket, None)
        self._broadcast(PositionClosed(
            ticket        = m.ticket,
            symbol        = m.symbol,
            pos_type      = m.pos_type,
            volume        = m.volume,
            open_price    = m.open_price,
            close_price   = m.close_price,
            profit        = m.profit,
            magic         = m.magic,
            close_time_ms = m.close_time * 1000,
            event_time_ms = _now_ms(),
        ))

    def _emit_bar_closed(self, bar: Bar | HistoryBar, *, is_history: bool) -> None:
        self._broadcast(BarClosed(
            symbol        = bar.symbol,
            role          = bar.role,
            tf_period     = bar.tf_period,
            time          = bar.time,
            time_msc      = bar.time_msc,
            open          = bar.open,
            high          = bar.high,
            low           = bar.low,
            close         = bar.close,
            volume        = bar.volume,
            is_history    = is_history,
            event_time_ms = _now_ms(),
        ))

    def _emit_cube_closed(self, cube: Cube | HistoryCube, *, is_history: bool) -> None:
        self._broadcast(CubeClosed(
            symbol        = cube.symbol,
            role          = cube.role,
            tf_period     = cube.tf_period,
            id            = cube.id,
            dir           = cube.dir,
            state         = cube.state,
            bar_count     = cube.bar_count,
            body_high     = cube.body_high,
            body_low      = cube.body_low,
            wick_high     = cube.wick_high,
            wick_low      = cube.wick_low,
            first_open    = cube.first_open,
            last_close    = cube.last_close,
            bull_volume   = cube.bull_volume,
            bear_volume   = cube.bear_volume,
            obv_score     = cube.obv_score,
            efficiency    = cube.efficiency,
            total_volume  = cube.total_volume,
            time_start    = cube.time_start,
            time_end      = cube.time_end,
            is_history    = is_history,
            event_time_ms = _now_ms(),
        ))

    def _emit_meta_cube_closed(
        self, meta: MetaCube | HistoryMetaCube, *, is_history: bool
    ) -> None:
        self._broadcast(MetaCubeClosed(
            symbol        = meta.symbol,
            role          = meta.role,
            tf_period     = meta.tf_period,
            id            = meta.id,
            dir           = meta.dir,
            state         = meta.state,
            cube_count    = meta.cube_count,
            bar_count     = meta.bar_count,
            body_high     = meta.body_high,
            body_low      = meta.body_low,
            wick_high     = meta.wick_high,
            wick_low      = meta.wick_low,
            first_open    = meta.first_open,
            last_close    = meta.last_close,
            bull_volume   = meta.bull_volume,
            bear_volume   = meta.bear_volume,
            obv_score     = meta.obv_score,
            efficiency    = meta.efficiency,
            total_volume  = meta.total_volume,
            first_cube_id = meta.first_cube_id,
            last_cube_id  = meta.last_cube_id,
            time_start    = meta.time_start,
            time_end      = meta.time_end,
            is_history    = is_history,
            event_time_ms = _now_ms(),
        ))

    def _handle_order_removed(self, m: OrderRemovedSignal) -> None:
        self._known_orders.pop(m.ticket, None)
        self._broadcast(OrderCanceled(
            ticket          = m.ticket,
            symbol          = m.symbol,
            order_type      = m.order_type,
            volume          = m.volume,
            open_price      = m.open_price,
            sl              = m.sl,
            tp              = m.tp,
            magic           = m.magic,
            reason          = m.reason,
            removed_time_ms = m.removed_time * 1000,
            event_time_ms   = _now_ms(),
        ))

    # ── broadcast ─────────────────────────────────────────────
    def _broadcast(self, evt: object) -> None:
        # 1) by concrete class
        for cb in self._callbacks.get(type(evt), ()):
            self._safe_call(cb, evt)
        # 2) by EventType enum value (only present on OutputEvent business types)
        evt_type_field = getattr(evt, "type", None)
        if isinstance(evt_type_field, int):
            key: EventType | int
            try:
                key = EventType(evt_type_field)
            except ValueError:
                key = evt_type_field
            for cb in self._callbacks.get(key, ()):
                self._safe_call(cb, evt)
        # 3) catch-all
        for cb in self._callbacks_all:
            self._safe_call(cb, evt)

    @staticmethod
    def _safe_call(cb: Callable[[Any], None], evt: object) -> None:
        try:
            cb(evt)
        except Exception:
            _log.exception("callback_error", evt_type=type(evt).__name__)

    @staticmethod
    def _safe_tell(ref: pykka.ActorRef | None, msg: object) -> None:
        """Forward ``msg`` to ``ref``, swallowing dead-actor races.

        During ``bridge.stop()`` downstream actors (watchdog, kelly)
        may have been stopped while the dispatcher's mailbox still
        holds late-arriving Tick / Account messages. ``ref.tell`` then
        raises ``ActorDeadError``. That race is expected and benign —
        log nothing, just drop.
        """
        if ref is None:
            return
        try:
            ref.tell(msg)
        except pykka.ActorDeadError:
            pass


__all__ = ["Dispatcher"]
