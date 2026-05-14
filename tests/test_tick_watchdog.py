"""TickWatchdog tests.

We use a tiny ``check_interval_seconds`` and ``tick_timeout_seconds``
so the periodic scan fires quickly without freezing time. Captured
sinks act as the dispatcher and executor for assertion.
"""
from __future__ import annotations

import time

import pykka
import pytest

from mt5_bridge.actors.tick_watchdog import TickWatchdog
from mt5_bridge.configs.bridge import WatchdogConfig
from mt5_bridge.contracts.ea_messages import Tick
from mt5_bridge.contracts.internal_messages import SymbolAlive, SymbolStale
from mt5_bridge.contracts.output_events import EmergencyTickStale


def _wait_for(predicate, *, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("predicate never became true within timeout")


class Sink(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(self):
        super().__init__()
        self.events: list = []

    def on_receive(self, msg):
        self.events.append(msg)


@pytest.fixture
def env():
    pykka.ActorRegistry.stop_all()
    dispatcher = Sink.start()
    executor = Sink.start()
    yield dispatcher, executor
    pykka.ActorRegistry.stop_all()


def _tick(symbol="X", ms=None):
    if ms is None:
        ms = int(time.time() * 1000)
    return Tick(symbol=symbol, time="t", time_msc=ms,
                bid=1.0, ask=1.1, last=0.0, volume=1)


def test_no_event_when_ticks_flow(env):
    dispatcher, executor = env
    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=1.0,
                         check_interval_seconds=0.05, symbols=("X",))
    wd = TickWatchdog.start(dispatcher, cfg)
    wd.proxy().set_executor_ref(executor).get()

    # Send a tick every 50ms for 300ms — well below the 1s timeout
    deadline = time.time() + 0.3
    while time.time() < deadline:
        wd.tell(_tick("X"))
        time.sleep(0.05)

    time.sleep(0.1)
    assert not any(isinstance(m, SymbolStale) for m in executor.proxy().events.get())
    assert not any(isinstance(m, EmergencyTickStale) for m in dispatcher.proxy().events.get())
    wd.stop()


def test_stale_emitted_when_silent(env):
    dispatcher, executor = env
    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=0.2,
                         check_interval_seconds=0.05, symbols=("X",))
    wd = TickWatchdog.start(dispatcher, cfg)
    wd.proxy().set_executor_ref(executor).get()

    # Prime with one tick, then go silent
    wd.tell(_tick("X"))

    _wait_for(lambda: any(isinstance(m, SymbolStale)
                          for m in executor.proxy().events.get()),
              timeout=2.0)
    _wait_for(lambda: any(isinstance(m, EmergencyTickStale)
                          for m in dispatcher.proxy().events.get()),
              timeout=2.0)
    wd.stop()


def test_recovery_emits_alive(env):
    dispatcher, executor = env
    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=0.2,
                         check_interval_seconds=0.05, symbols=("X",))
    wd = TickWatchdog.start(dispatcher, cfg)
    wd.proxy().set_executor_ref(executor).get()

    wd.tell(_tick("X"))
    _wait_for(lambda: any(isinstance(m, SymbolStale)
                          for m in executor.proxy().events.get()),
              timeout=2.0)

    # Now send a tick → should emit SymbolAlive
    wd.tell(_tick("X"))
    _wait_for(lambda: any(isinstance(m, SymbolAlive)
                          for m in executor.proxy().events.get()),
              timeout=1.0)
    wd.stop()


def test_stale_only_emitted_once(env):
    """A symbol that stays stale should not flood with repeat events."""
    dispatcher, executor = env
    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=0.15,
                         check_interval_seconds=0.05, symbols=("X",))
    wd = TickWatchdog.start(dispatcher, cfg)
    wd.proxy().set_executor_ref(executor).get()

    wd.tell(_tick("X"))
    _wait_for(lambda: any(isinstance(m, SymbolStale)
                          for m in executor.proxy().events.get()),
              timeout=2.0)

    # Wait for several scan cycles to confirm no repeats
    time.sleep(0.4)
    stale_msgs = [m for m in executor.proxy().events.get()
                  if isinstance(m, SymbolStale)]
    assert len(stale_msgs) == 1
    wd.stop()


def test_filter_only_configured_symbols(env):
    dispatcher, executor = env
    cfg = WatchdogConfig(enabled=True, tick_timeout_seconds=0.15,
                         check_interval_seconds=0.05, symbols=("ONLY",))
    wd = TickWatchdog.start(dispatcher, cfg)
    wd.proxy().set_executor_ref(executor).get()

    # An unconfigured symbol — should be ignored entirely (no last_ms entry)
    wd.tell(_tick("OTHER"))
    time.sleep(0.5)
    # The "ONLY" symbol was pre-seeded at start time — it WILL go stale.
    # OTHER should never trigger.
    stale_symbols = [m.symbol for m in executor.proxy().events.get()
                     if isinstance(m, SymbolStale)]
    assert "OTHER" not in stale_symbols
    wd.stop()
