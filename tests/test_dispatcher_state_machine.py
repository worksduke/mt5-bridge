"""Dispatcher state-machine + pub/sub tests.

These tests bring up a real Dispatcher actor (no mocking — Dispatcher
has no MT5 dependency) and feed it ``Position`` / ``Order`` snapshots
via ``ref.tell``. We assert on the OutputEvents that get broadcast
to a captured callback.

A small ``_drain`` helper waits until expected callbacks have fired,
since pykka actors process messages on a separate thread.
"""
from __future__ import annotations

import time

import pykka
import pytest

from mt5_bridge.actors.dispatcher import Dispatcher
from mt5_bridge.contracts.ea_messages import (
    Account,
    Bar,
    HistoryBar,
    Order,
    Position,
    Tick,
)
from mt5_bridge.contracts.ea_messages import (
    OrderRemoved as OrderRemovedSignal,
)
from mt5_bridge.contracts.ea_messages import (
    PositionClosed as PositionClosedSignal,
)
from mt5_bridge.contracts.enums import EventType
from mt5_bridge.contracts.output_events import (
    BarClosed,
    EmergencyTickStale,
    OrderModified,
    OrderPlaced,
    PositionModified,
    PositionOpened,
)
from mt5_bridge.contracts.output_events import (
    OrderCanceled as OrderCanceledEvent,
)
from mt5_bridge.contracts.output_events import (
    PositionClosed as PositionClosedEvent,
)


def _wait_for(predicate, *, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("predicate never became true within timeout")


@pytest.fixture
def dispatcher():
    pykka.ActorRegistry.stop_all()
    ref = Dispatcher.start()
    yield ref
    ref.stop()
    pykka.ActorRegistry.stop_all()


def test_first_position_emits_opened(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(PositionOpened, received.append).get()

    pos = Position(ticket=1, symbol="XAUUSD", pos_type="BUY", volume=0.1,
                   open_price=2050.0, cur_price=2051.0, sl=0.0, tp=0.0, profit=10.0)
    dispatcher.tell(pos)

    _wait_for(lambda: len(received) == 1)
    assert received[0].ticket == 1
    assert received[0].symbol == "XAUUSD"
    assert received[0].pos_type == "BUY"


def test_same_position_twice_no_event(dispatcher):
    opened: list = []
    modified: list = []
    dispatcher.proxy().register_callback(PositionOpened, opened.append).get()
    dispatcher.proxy().register_callback(PositionModified, modified.append).get()

    pos = Position(ticket=2, symbol="EURUSD", pos_type="SELL", volume=0.5,
                   open_price=1.10, cur_price=1.099, sl=1.11, tp=1.09, profit=5.0)
    dispatcher.tell(pos)
    dispatcher.tell(pos)
    dispatcher.tell(pos)

    _wait_for(lambda: len(opened) == 1)
    # Wait a moment to confirm no extra events
    time.sleep(0.1)
    assert len(opened) == 1
    assert len(modified) == 0


def test_position_sl_change_emits_modified(dispatcher):
    opened: list = []
    modified: list = []
    dispatcher.proxy().register_callback(PositionOpened, opened.append).get()
    dispatcher.proxy().register_callback(PositionModified, modified.append).get()

    p1 = Position(ticket=3, symbol="X", pos_type="BUY", volume=1.0,
                  open_price=100.0, cur_price=100.5, sl=99.0, tp=0.0, profit=0.0)
    p2 = Position(ticket=3, symbol="X", pos_type="BUY", volume=1.0,
                  open_price=100.0, cur_price=100.5, sl=99.5, tp=101.0, profit=0.0)
    dispatcher.tell(p1)
    dispatcher.tell(p2)

    _wait_for(lambda: len(opened) == 1 and len(modified) == 1)
    assert modified[0].sl == 99.5
    assert modified[0].tp == 101.0


def test_first_order_emits_placed(dispatcher):
    placed: list = []
    dispatcher.proxy().register_callback(OrderPlaced, placed.append).get()

    o = Order(ticket=10, symbol="X", order_type="BUY_LIMIT", volume=0.2,
              open_price=99.0, sl=0.0, tp=0.0, comment="")
    dispatcher.tell(o)

    _wait_for(lambda: len(placed) == 1)
    assert placed[0].ticket == 10
    assert placed[0].order_type == "BUY_LIMIT"


def test_order_price_change_emits_modified(dispatcher):
    placed: list = []
    modified: list = []
    dispatcher.proxy().register_callback(OrderPlaced, placed.append).get()
    dispatcher.proxy().register_callback(OrderModified, modified.append).get()

    o1 = Order(ticket=11, symbol="X", order_type="BUY_LIMIT", volume=0.2,
               open_price=99.0, sl=0.0, tp=0.0, comment="")
    o2 = Order(ticket=11, symbol="X", order_type="BUY_LIMIT", volume=0.2,
               open_price=98.5, sl=98.0, tp=0.0, comment="")
    dispatcher.tell(o1)
    dispatcher.tell(o2)

    _wait_for(lambda: len(placed) == 1 and len(modified) == 1)
    assert modified[0].open_price == 98.5
    assert modified[0].sl == 98.0


def test_subscribe_by_eventtype_int(dispatcher):
    """Users can subscribe with the EventType enum value, not just the class."""
    received: list = []
    dispatcher.proxy().register_callback(EventType.POSITION_OPENED, received.append).get()

    pos = Position(ticket=20, symbol="Y", pos_type="BUY", volume=1.0,
                   open_price=10.0, cur_price=10.0, sl=0.0, tp=0.0, profit=0.0)
    dispatcher.tell(pos)

    _wait_for(lambda: len(received) == 1)
    assert received[0].ticket == 20


def test_tick_broadcast_and_account_broadcast(dispatcher):
    ticks: list = []
    accounts: list = []
    dispatcher.proxy().register_callback(Tick, ticks.append).get()
    dispatcher.proxy().register_callback(Account, accounts.append).get()

    t = Tick(symbol="X", time="2024.01.01 12:00:00", time_msc=1, bid=1.0,
             ask=1.1, last=0.0, volume=1)
    a = Account(login="42", currency="USD", balance=1000.0, equity=1000.0,
                margin=0.0, free_margin=1000.0, profit=0.0)
    dispatcher.tell(t)
    dispatcher.tell(a)

    _wait_for(lambda: len(ticks) == 1 and len(accounts) == 1)


def test_subscribe_all_catches_everything(dispatcher):
    everything: list = []
    dispatcher.proxy().register_callback_all(everything.append).get()

    t = Tick(symbol="X", time="x", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1)
    pos = Position(ticket=99, symbol="X", pos_type="BUY", volume=1.0,
                   open_price=1.0, cur_price=1.0, sl=0.0, tp=0.0, profit=0.0)
    dispatcher.tell(t)
    dispatcher.tell(pos)

    # Expect: Tick (broadcast) + PositionOpened (broadcast)
    _wait_for(lambda: len(everything) >= 2)
    types = {type(e).__name__ for e in everything}
    assert "Tick" in types
    assert "PositionOpened" in types


def test_subscribe_one_and_all_both_fire(dispatcher):
    pos_only: list = []
    everything: list = []
    dispatcher.proxy().register_callback(PositionOpened, pos_only.append).get()
    dispatcher.proxy().register_callback_all(everything.append).get()

    pos = Position(ticket=100, symbol="X", pos_type="BUY", volume=1.0,
                   open_price=1.0, cur_price=1.0, sl=0.0, tp=0.0, profit=0.0)
    dispatcher.tell(pos)

    _wait_for(lambda: len(pos_only) == 1 and len(everything) == 1)


def test_callback_exception_does_not_kill_others(dispatcher):
    good: list = []

    def bad(_): raise RuntimeError("boom")

    dispatcher.proxy().register_callback(PositionOpened, bad).get()
    dispatcher.proxy().register_callback(PositionOpened, good.append).get()

    pos = Position(ticket=200, symbol="X", pos_type="BUY", volume=1.0,
                   open_price=1.0, cur_price=1.0, sl=0.0, tp=0.0, profit=0.0)
    dispatcher.tell(pos)

    _wait_for(lambda: len(good) == 1)
    # Dispatcher is still alive
    assert dispatcher.is_alive()


def test_emergency_tick_stale_broadcast(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(EmergencyTickStale, received.append).get()

    msg = EmergencyTickStale(symbol="X", last_tick_time_ms=0,
                             silence_seconds=999.0, detected_at_ms=1)
    dispatcher.tell(msg)

    _wait_for(lambda: len(received) == 1)
    assert received[0].symbol == "X"


def test_trade_state_changed_is_broadcast(dispatcher):
    """Demos rely on subscribing to TradeStateChanged for confirmations."""
    from mt5_bridge.contracts.enums import TradeState
    from mt5_bridge.contracts.internal_messages import TradeStateChanged

    received: list = []
    dispatcher.proxy().register_callback(TradeStateChanged, received.append).get()

    msg = TradeStateChanged(request_id="r1", state=TradeState.POSITION_OPENED,
                            ticket=42, retcode=10009, comment="ok")
    dispatcher.tell(msg)

    _wait_for(lambda: len(received) == 1)
    assert received[0].ticket == 42
    assert received[0].state == TradeState.POSITION_OPENED


# =============================================================================
# Close / removal detection (position_closed / order_removed wire signals)
# =============================================================================


def _pos(ticket=1, symbol="X", sl=0.0, tp=0.0, magic=0, comment=""):
    return Position(ticket=ticket, symbol=symbol, pos_type="BUY", volume=0.1,
                    open_price=100.0, cur_price=100.5, sl=sl, tp=tp, profit=0.5,
                    magic=magic, comment=comment)


def _ord(ticket=10, symbol="X", price=99.0, magic=0):
    return Order(ticket=ticket, symbol=symbol, order_type="BUY_LIMIT", volume=0.1,
                 open_price=price, sl=0.0, tp=0.0, comment="", magic=magic)


def test_position_closed_signal_emits_output_event(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(PositionClosedEvent, received.append).get()

    sig = PositionClosedSignal(
        ticket=1, symbol="XAUUSD", pos_type="BUY", volume=0.5,
        open_price=2050.0, close_price=2055.5, profit=275.0,
        magic=1234, close_time=1747180900,
    )
    dispatcher.tell(sig)

    _wait_for(lambda: len(received) == 1)
    evt = received[0]
    assert evt.ticket == 1
    assert evt.symbol == "XAUUSD"
    assert evt.pos_type == "BUY"
    assert evt.volume == 0.5
    assert evt.open_price == 2050.0
    assert evt.close_price == 2055.5
    assert evt.profit == 275.0
    assert evt.magic == 1234
    assert evt.close_time_ms == 1747180900 * 1000


def test_position_closed_clears_known_state_so_reopen_re_fires(dispatcher):
    opened: list = []
    closed: list = []
    dispatcher.proxy().register_callback(PositionOpened, opened.append).get()
    dispatcher.proxy().register_callback(PositionClosedEvent, closed.append).get()

    dispatcher.tell(_pos(ticket=99))
    _wait_for(lambda: len(opened) == 1)

    dispatcher.tell(PositionClosedSignal(
        ticket=99, symbol="X", pos_type="BUY", volume=0.1,
        open_price=100.0, close_price=101.0, profit=0.1,
        magic=0, close_time=1747180900,
    ))
    _wait_for(lambda: len(closed) == 1)

    # Same ticket reappears (e.g. broker re-uses, or new position with same id) —
    # MUST fire Opened again, not be silently treated as a modify.
    dispatcher.tell(_pos(ticket=99))
    _wait_for(lambda: len(opened) == 2)


def test_position_closed_for_unknown_ticket_still_emits(dispatcher):
    """If the EA pushes position_closed before we ever saw the open
    (cold-start race after reconnect), still emit — better than dropping."""
    received: list = []
    dispatcher.proxy().register_callback(PositionClosedEvent, received.append).get()

    dispatcher.tell(PositionClosedSignal(
        ticket=999, symbol="X", pos_type="SELL", volume=1.0,
        open_price=10.0, close_price=11.0, profit=-100.0,
        magic=0, close_time=1747180900,
    ))
    _wait_for(lambda: len(received) == 1)


def test_order_removed_signal_emits_output_event(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(OrderCanceledEvent, received.append).get()

    sig = OrderRemovedSignal(
        ticket=42, symbol="X", order_type="BUY_LIMIT", volume=0.2,
        open_price=99.5, sl=98.0, tp=101.0, magic=7,
        reason="canceled", removed_time=1747180900,
    )
    dispatcher.tell(sig)

    _wait_for(lambda: len(received) == 1)
    evt = received[0]
    assert evt.ticket == 42
    assert evt.reason == "canceled"
    assert evt.order_type == "BUY_LIMIT"
    assert evt.removed_time_ms == 1747180900 * 1000
    assert evt.magic == 7


def test_order_removed_clears_known_state_so_replace_re_fires(dispatcher):
    placed: list = []
    canceled: list = []
    dispatcher.proxy().register_callback(OrderPlaced, placed.append).get()
    dispatcher.proxy().register_callback(OrderCanceledEvent, canceled.append).get()

    dispatcher.tell(_ord(ticket=50))
    _wait_for(lambda: len(placed) == 1)

    dispatcher.tell(OrderRemovedSignal(
        ticket=50, symbol="X", order_type="BUY_LIMIT", volume=0.1,
        open_price=99.0, sl=0.0, tp=0.0, magic=0,
        reason="filled", removed_time=1747180900,
    ))
    _wait_for(lambda: len(canceled) == 1)

    dispatcher.tell(_ord(ticket=50))
    _wait_for(lambda: len(placed) == 2)


def test_position_open_now_carries_magic_and_comment(dispatcher):
    """Regression: dispatcher used to hardcode magic=0/comment=''.
    Position now carries them; PositionOpened should pass them through."""
    received: list = []
    dispatcher.proxy().register_callback(PositionOpened, received.append).get()

    dispatcher.tell(_pos(ticket=7, magic=4242, comment="strategy A"))
    _wait_for(lambda: len(received) == 1)
    assert received[0].magic == 4242
    assert received[0].comment == "strategy A"


def test_dead_watchdog_does_not_crash_dispatcher(dispatcher):
    """Regression: during bridge.stop, watchdog can be reaped before
    dispatcher finishes its mailbox. Dispatcher must NOT crash on the
    forward — silently drop instead."""
    # Spin up a watchdog-like sink and immediately stop it
    class Sink(pykka.ThreadingActor):
        use_daemon_thread = True
        def on_receive(self, m): pass
    dead_ref = Sink.start()
    dead_ref.stop()  # kill it BEFORE we wire dispatcher

    dispatcher.proxy().set_watchdog_ref(dead_ref).get()

    received: list = []
    dispatcher.proxy().register_callback(Tick, received.append).get()

    # Send a Tick — dispatcher will try to forward to dead_ref. Must not crash.
    t = Tick(symbol="X", time="t", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1)
    dispatcher.tell(t)

    # Local subscribers still get the broadcast; dispatcher still alive.
    _wait_for(lambda: len(received) == 1)
    assert dispatcher.is_alive()


def test_dead_kelly_does_not_crash_dispatcher(dispatcher):
    """Same race for Account → Kelly forward."""
    class Sink(pykka.ThreadingActor):
        use_daemon_thread = True
        def on_receive(self, m): pass
    dead_ref = Sink.start()
    dead_ref.stop()

    dispatcher.proxy().set_kelly_ref(dead_ref).get()

    received: list = []
    dispatcher.proxy().register_callback(Account, received.append).get()

    a = Account(login="1", currency="USD", balance=1.0, equity=1.0,
                margin=0.0, free_margin=1.0, profit=0.0)
    dispatcher.tell(a)

    _wait_for(lambda: len(received) == 1)
    assert dispatcher.is_alive()


# =============================================================================
# Bar / HistoryBar → BarClosed (v0.2)
# =============================================================================

def _bar(symbol="X", role="execution", tf="M5", time="2026.05.14 09:00:00",
         time_msc=1747213200000, is_closed=True,
         open_=100.0, high=101.0, low=99.0, close=100.5, vol=42):
    return Bar(symbol=symbol, role=role, tf_period=tf, time=time, time_msc=time_msc,
               is_closed=is_closed, open=open_, high=high, low=low, close=close, volume=vol)


def _hbar(symbol="X", role="execution", tf="M5", time="2026.05.14 08:55:00",
          time_msc=1747212900000,
          open_=99.0, high=100.0, low=98.5, close=99.5, vol=30):
    return HistoryBar(symbol=symbol, role=role, tf_period=tf, time=time, time_msc=time_msc,
                      is_closed=True, open=open_, high=high, low=low, close=close, volume=vol)


def test_closed_bar_emits_bar_closed_event(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(BarClosed, received.append).get()

    dispatcher.tell(_bar(is_closed=True, open_=100.0, close=100.5))

    _wait_for(lambda: len(received) == 1)
    evt = received[0]
    assert evt.symbol == "X"
    assert evt.role == "execution"
    assert evt.tf_period == "M5"
    assert evt.open == 100.0
    assert evt.close == 100.5
    assert evt.is_history is False
    assert evt.type == EventType.BAR_CLOSED


def test_unclosed_bar_does_not_emit_bar_closed(dispatcher):
    received: list = []
    raw_bars: list = []
    dispatcher.proxy().register_callback(BarClosed, received.append).get()
    dispatcher.proxy().register_callback(Bar, raw_bars.append).get()

    dispatcher.tell(_bar(is_closed=False))

    # Raw Bar still passes through; BarClosed must not fire
    _wait_for(lambda: len(raw_bars) == 1)
    time.sleep(0.05)
    assert len(received) == 0


def test_history_bar_emits_bar_closed_with_is_history_true(dispatcher):
    received: list = []
    dispatcher.proxy().register_callback(BarClosed, received.append).get()

    dispatcher.tell(_hbar())

    _wait_for(lambda: len(received) == 1)
    assert received[0].is_history is True
    assert received[0].type == EventType.BAR_CLOSED


def test_bar_closed_subscribe_by_event_type(dispatcher):
    """Users can subscribe with the EventType enum value, not just the class."""
    received: list = []
    dispatcher.proxy().register_callback(EventType.BAR_CLOSED, received.append).get()

    dispatcher.tell(_bar(is_closed=True))
    dispatcher.tell(_hbar())

    _wait_for(lambda: len(received) == 2)


def test_closed_bar_pushed_twice_emits_twice(dispatcher):
    """Dispatcher does NOT dedupe BarClosed — EA already emits closed bars
    only once per finalization, so any duplicates the dispatcher sees are
    intentional (tests, manual replay) and should pass through."""
    received: list = []
    dispatcher.proxy().register_callback(BarClosed, received.append).get()

    b = _bar(is_closed=True)
    dispatcher.tell(b)
    dispatcher.tell(b)

    _wait_for(lambda: len(received) == 2)


def test_raw_bar_subscribe_still_works(dispatcher):
    """Backward compat: subscribe(Bar, cb) still gets both closed AND unclosed."""
    raw: list = []
    dispatcher.proxy().register_callback(Bar, raw.append).get()

    dispatcher.tell(_bar(is_closed=True))
    dispatcher.tell(_bar(is_closed=False))

    _wait_for(lambda: len(raw) == 2)
    assert {b.is_closed for b in raw} == {True, False}
