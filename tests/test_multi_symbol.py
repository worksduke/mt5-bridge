"""Multi-symbol regression tests.

The dispatcher's state machine is keyed by ticket (globally unique
across symbols). The watchdog is keyed by symbol. Make sure both
isolate state correctly when multiple symbols are in flight.
"""
from __future__ import annotations

import time

import pykka
import pytest

from mt5_bridge.actors.dispatcher import Dispatcher
from mt5_bridge.contracts.ea_messages import (
    Position,
    Tick,
)
from mt5_bridge.contracts.ea_messages import (
    PositionClosed as PositionClosedSignal,
)
from mt5_bridge.contracts.output_events import (
    PositionClosed as PositionClosedEvent,
)
from mt5_bridge.contracts.output_events import (
    PositionModified,
    PositionOpened,
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


def _pos(ticket, symbol, sl=0.0, tp=0.0):
    return Position(ticket=ticket, symbol=symbol, pos_type="BUY", volume=0.1,
                    open_price=100.0, cur_price=100.5, sl=sl, tp=tp, profit=0.5)


def test_position_state_per_symbol_independent(dispatcher):
    """Two open positions in different symbols → 2 PositionOpened.
    Modifying one only fires PositionModified for that ticket."""
    opened: list = []
    modified: list = []
    dispatcher.proxy().register_callback(PositionOpened, opened.append).get()
    dispatcher.proxy().register_callback(PositionModified, modified.append).get()

    dispatcher.tell(_pos(ticket=1, symbol="XAUUSD"))
    dispatcher.tell(_pos(ticket=2, symbol="EURUSD"))
    _wait_for(lambda: len(opened) == 2)
    assert {e.symbol for e in opened} == {"XAUUSD", "EURUSD"}

    # Modify only ticket 1
    dispatcher.tell(_pos(ticket=1, symbol="XAUUSD", sl=99.0))
    _wait_for(lambda: len(modified) == 1)
    assert modified[0].ticket == 1
    assert modified[0].symbol == "XAUUSD"


def test_close_one_symbol_other_unaffected(dispatcher):
    """Closing ticket 1 doesn't touch ticket 2's state."""
    opened: list = []
    closed: list = []
    dispatcher.proxy().register_callback(PositionOpened, opened.append).get()
    dispatcher.proxy().register_callback(PositionClosedEvent, closed.append).get()

    dispatcher.tell(_pos(ticket=1, symbol="XAUUSD"))
    dispatcher.tell(_pos(ticket=2, symbol="EURUSD"))
    _wait_for(lambda: len(opened) == 2)

    dispatcher.tell(PositionClosedSignal(
        ticket=1, symbol="XAUUSD", pos_type="BUY", volume=0.1,
        open_price=100.0, close_price=101.0, profit=10.0,
        magic=0, close_time=1747212900,
    ))
    _wait_for(lambda: len(closed) == 1)
    assert closed[0].ticket == 1

    # Re-pushing ticket 2 must NOT fire PositionOpened again
    # (ticket 2 was never closed, dispatcher still has it in _known_positions)
    dispatcher.tell(_pos(ticket=2, symbol="EURUSD"))
    time.sleep(0.05)
    assert len(opened) == 2   # unchanged

    # Re-pushing ticket 1 SHOULD fire PositionOpened again (state was cleared by close)
    dispatcher.tell(_pos(ticket=1, symbol="XAUUSD"))
    _wait_for(lambda: len(opened) == 3)
    assert opened[2].ticket == 1


def test_tick_per_symbol_does_not_pollute_state(dispatcher):
    """Two symbols' Ticks don't interfere — broadcast independently."""
    received: list = []
    dispatcher.proxy().register_callback(Tick, received.append).get()

    for sym in ("XAUUSD", "EURUSD", "USDJPY"):
        dispatcher.tell(Tick(symbol=sym, time="t", time_msc=1,
                             bid=1.0, ask=1.1, last=0.0, volume=1))

    _wait_for(lambda: len(received) == 3)
    assert {t.symbol for t in received} == {"XAUUSD", "EURUSD", "USDJPY"}


# =============================================================================
# Watchdog per-symbol stale isolation
# =============================================================================

def test_watchdog_per_symbol_stale_isolation():
    """Two symbols configured; only the silent one goes stale."""
    from mt5_bridge.actors.tick_watchdog import TickWatchdog
    from mt5_bridge.configs.bridge import WatchdogConfig
    from mt5_bridge.contracts.internal_messages import SymbolStale
    from mt5_bridge.contracts.output_events import EmergencyTickStale

    pykka.ActorRegistry.stop_all()

    class Sink(pykka.ThreadingActor):
        use_daemon_thread = True
        def __init__(self):
            super().__init__()
            self.events: list = []
        def on_receive(self, m):
            self.events.append(m)

    dispatcher_sink = Sink.start()
    executor_sink = Sink.start()

    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=0.2,
                         check_interval_seconds=0.05, symbols=("A", "B"))
    wd = TickWatchdog.start(dispatcher_sink, cfg)
    wd.proxy().set_executor_ref(executor_sink).get()

    # Keep A alive with steady ticks; let B go silent
    deadline = time.time() + 0.5
    while time.time() < deadline:
        wd.tell(Tick(symbol="A", time="t", time_msc=int(time.time()*1000),
                     bid=1.0, ask=1.1, last=0.0, volume=1))
        time.sleep(0.05)

    # B should be flagged stale, A should NOT be
    stale_symbols = [m.symbol for m in executor_sink.proxy().events.get()
                     if isinstance(m, SymbolStale)]
    emergency_symbols = [m.symbol for m in dispatcher_sink.proxy().events.get()
                         if isinstance(m, EmergencyTickStale)]
    assert "B" in stale_symbols
    assert "A" not in stale_symbols
    assert "B" in emergency_symbols
    assert "A" not in emergency_symbols

    wd.stop()
    pykka.ActorRegistry.stop_all()


# =============================================================================
# Executor stale set per-symbol
# =============================================================================

def test_executor_stale_one_symbol_blocks_only_that_symbol():
    """SymbolStale(A) blocks OpenPosition(A); OpenPosition(B) still goes through."""
    import MetaTrader5 as mt5

    from mt5_bridge.actors.executor import Executor
    from mt5_bridge.configs.bridge import RetryConfig
    from mt5_bridge.contracts.enums import TradeState
    from mt5_bridge.contracts.internal_messages import (
        SymbolStale,
        TradeResult,
        TradeStateChanged,
    )
    from mt5_bridge.contracts.trade_commands import OpenPosition

    pykka.ActorRegistry.stop_all()

    class Sink(pykka.ThreadingActor):
        use_daemon_thread = True
        def __init__(self):
            super().__init__()
            self.events: list = []
        def on_receive(self, m):
            if isinstance(m, TradeStateChanged):
                self.events.append(m)

    class FakeClient:
        def __init__(self):
            self.calls: list = []
        def open_position(self, cmd):
            self.calls.append(cmd)
            return TradeResult(ok=True, ticket=999, retcode=10009)

    class NoopScheduler:
        def schedule(self, delay_seconds, fn): pass

    dispatcher_sink = Sink.start()
    client = FakeClient()
    cfg = RetryConfig(enabled=False)
    executor = Executor.start(client, dispatcher_sink, NoopScheduler(), cfg)

    executor.tell(SymbolStale(symbol="A", last_tick_time_ms=0, silence_seconds=600.0))
    executor.tell(OpenPosition(symbol="A", volume=0.1, type=mt5.ORDER_TYPE_BUY,
                                price=0.0, sl=0.0, tp=0.0))
    executor.tell(OpenPosition(symbol="B", volume=0.1, type=mt5.ORDER_TYPE_BUY,
                                price=0.0, sl=0.0, tp=0.0))

    # Wait for both commands' final states
    def _both_done():
        states = [(e.state, e.comment) for e in dispatcher_sink.proxy().events.get()]
        a_failed = any(s == TradeState.POSITION_OPEN_FAILED and "stale" in c
                       for s, c in states)
        b_opened = any(s == TradeState.POSITION_OPENED for s, c in states)
        return a_failed and b_opened
    _wait_for(_both_done, timeout=2.0)

    # A blocked → client never called for A; B did get through (1 call)
    assert len(client.calls) == 1
    assert client.calls[0].symbol == "B"

    executor.stop()
    pykka.ActorRegistry.stop_all()
