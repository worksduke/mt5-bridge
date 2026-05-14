"""Executor retry / stale-check tests.

Uses a fake MT5Client (just a class with the trade methods we care
about) and a synchronous fake scheduler that runs callbacks
immediately so retries fire deterministically without sleeping.
"""
from __future__ import annotations

import time
from typing import Any

import MetaTrader5 as mt5
import pykka
import pytest

from mt5_bridge.actors.executor import Executor
from mt5_bridge.configs.bridge import RetryConfig
from mt5_bridge.contracts.enums import TradeState
from mt5_bridge.contracts.internal_messages import (
    SymbolAlive,
    SymbolStale,
    TradeResult,
    TradeStateChanged,
)
from mt5_bridge.contracts.trade_commands import OpenPosition


def _wait_for(predicate, *, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("predicate never became true within timeout")


class FakeClient:
    """Minimal stand-in for MT5Client. Configure ``responses`` (list)
    to script per-call return values for ``open_position``."""

    def __init__(self, responses: list[TradeResult]):
        self.responses = list(responses)
        self.calls: list[Any] = []

    def open_position(self, cmd):
        self.calls.append(cmd)
        if not self.responses:
            return TradeResult(ok=True, ticket=1)
        return self.responses.pop(0)


class ImmediateScheduler:
    """Schedules callbacks to fire immediately on a worker thread.

    Using a worker thread (not the actor thread) better mirrors
    real-world behaviour where the scheduler hops onto a different
    thread before tell-ing the actor.
    """
    def __init__(self):
        self.scheduled: list[float] = []

    def schedule(self, delay_seconds, fn):
        self.scheduled.append(delay_seconds)
        # fire on a fresh thread to mirror reactor.callFromThread
        import threading
        threading.Thread(target=fn, daemon=True).start()


class CapturingDispatcher(pykka.ThreadingActor):
    """Records every TradeStateChanged the executor emits."""
    use_daemon_thread = True

    def __init__(self):
        super().__init__()
        self.events: list[TradeStateChanged] = []

    def on_receive(self, message):
        if isinstance(message, TradeStateChanged):
            self.events.append(message)


@pytest.fixture
def env():
    pykka.ActorRegistry.stop_all()
    dispatcher = CapturingDispatcher.start()
    yield dispatcher
    pykka.ActorRegistry.stop_all()


def _make_open_cmd():
    return OpenPosition(
        symbol="XAUUSD", volume=0.1, type=mt5.ORDER_TYPE_BUY,
        price=0.0, sl=0.0, tp=0.0,
    )


def test_success_emits_opening_then_opened(env):
    dispatcher = env
    client = FakeClient([TradeResult(ok=True, ticket=42, retcode=10009)])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=True, max_attempts=3, retryable_retcodes=(10004,))
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(_make_open_cmd())

    _wait_for(lambda: len(dispatcher.proxy().events.get()) == 2)
    states = [e.state for e in dispatcher.proxy().events.get()]
    assert states == [TradeState.POSITION_OPENING, TradeState.POSITION_OPENED]
    assert dispatcher.proxy().events.get()[1].ticket == 42
    assert len(sched.scheduled) == 0  # no retry
    executor.stop()


def test_non_retryable_retcode_fails_immediately(env):
    dispatcher = env
    # 10019 = NO_MONEY (not in our retryable set)
    client = FakeClient([TradeResult(ok=False, retcode=10019, comment="no money")])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=True, max_attempts=3, retryable_retcodes=(10004, 10021))
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(_make_open_cmd())

    _wait_for(lambda: any(e.state == TradeState.POSITION_OPEN_FAILED
                          for e in dispatcher.proxy().events.get()))
    final = [e for e in dispatcher.proxy().events.get()
             if e.state == TradeState.POSITION_OPEN_FAILED][0]
    assert final.retcode == 10019
    assert final.comment == "no money"
    assert len(sched.scheduled) == 0
    assert len(client.calls) == 1
    executor.stop()


def test_retryable_retcode_then_success(env):
    dispatcher = env
    # First attempt: PRICE_OFF (retryable). Second: success.
    client = FakeClient([
        TradeResult(ok=False, retcode=10021, comment="price off"),
        TradeResult(ok=True, ticket=99, retcode=10009),
    ])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=True, max_attempts=3, base_delay_ms=10,
                      max_delay_ms=20, jitter_ratio=0.0,
                      retryable_retcodes=(10021,))
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(_make_open_cmd())

    # Expect: OPENING (attempt 1) → eventually OPENED on attempt 2
    _wait_for(lambda: any(e.state == TradeState.POSITION_OPENED
                          for e in dispatcher.proxy().events.get()))
    states = [e.state for e in dispatcher.proxy().events.get()]
    assert TradeState.POSITION_OPENING in states
    assert TradeState.POSITION_OPENED in states
    # NO failed event between them
    assert TradeState.POSITION_OPEN_FAILED not in states
    assert len(sched.scheduled) == 1
    assert len(client.calls) == 2
    executor.stop()


def test_max_attempts_emits_final_failure(env):
    dispatcher = env
    client = FakeClient([
        TradeResult(ok=False, retcode=10021, comment="off1"),
        TradeResult(ok=False, retcode=10021, comment="off2"),
        TradeResult(ok=False, retcode=10021, comment="off3"),
    ])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=True, max_attempts=3, base_delay_ms=5,
                      max_delay_ms=10, jitter_ratio=0.0,
                      retryable_retcodes=(10021,))
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(_make_open_cmd())

    _wait_for(lambda: any(e.state == TradeState.POSITION_OPEN_FAILED
                          for e in dispatcher.proxy().events.get()))
    final = [e for e in dispatcher.proxy().events.get()
             if e.state == TradeState.POSITION_OPEN_FAILED][-1]
    assert final.retcode == 10021
    assert final.comment == "off3"   # last attempt's comment carried through
    assert len(client.calls) == 3
    # 2 retries scheduled (after attempt 1 and 2; attempt 3 doesn't retry)
    assert len(sched.scheduled) == 2
    executor.stop()


def test_stale_symbol_blocks_open(env):
    dispatcher = env
    client = FakeClient([])  # should never be called
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=False)
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(SymbolStale(symbol="XAUUSD", last_tick_time_ms=0, silence_seconds=600.0))
    executor.tell(_make_open_cmd())

    _wait_for(lambda: any(e.state == TradeState.POSITION_OPEN_FAILED
                          for e in dispatcher.proxy().events.get()))
    final = [e for e in dispatcher.proxy().events.get()
             if e.state == TradeState.POSITION_OPEN_FAILED][0]
    assert "stale" in final.comment
    assert len(client.calls) == 0
    executor.stop()


def test_symbol_alive_re_enables_open(env):
    dispatcher = env
    client = FakeClient([TradeResult(ok=True, ticket=7, retcode=10009)])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=False)
    executor = Executor.start(client, dispatcher, sched, cfg)

    executor.tell(SymbolStale(symbol="XAUUSD", last_tick_time_ms=0, silence_seconds=600.0))
    executor.tell(SymbolAlive(symbol="XAUUSD", tick_time_ms=1))
    executor.tell(_make_open_cmd())

    _wait_for(lambda: any(e.state == TradeState.POSITION_OPENED
                          for e in dispatcher.proxy().events.get()))
    assert len(client.calls) == 1
    executor.stop()


def test_validation_failure_short_circuits(env):
    dispatcher = env
    client = FakeClient([])
    sched = ImmediateScheduler()
    cfg = RetryConfig(enabled=True, retryable_retcodes=(10021,))
    executor = Executor.start(client, dispatcher, sched, cfg)

    # volume=0 fails OpenPosition.validate()
    bad = OpenPosition(symbol="X", volume=0.0, type=mt5.ORDER_TYPE_BUY,
                       price=0.0, sl=0.0, tp=0.0)
    executor.tell(bad)

    _wait_for(lambda: any(e.state == TradeState.POSITION_OPEN_FAILED
                          for e in dispatcher.proxy().events.get()))
    final = [e for e in dispatcher.proxy().events.get()
             if e.state == TradeState.POSITION_OPEN_FAILED][0]
    assert "validation_failed" in final.comment
    assert len(client.calls) == 0
    executor.stop()
