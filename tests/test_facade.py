"""End-to-end facade tests.

We never actually connect to MT5 in tests — ``MT5Client.initialize``
is monkeypatched to return True, and ``order_send`` is replaced with
a stub. The rpc-on path is exercised in a separate test that runs the
reactor in a background thread.
"""
from __future__ import annotations

import time

import pykka
import pytest

from mt5_bridge.contracts.ea_messages import Account, Position, Tick
from mt5_bridge.contracts.enums import EventType
from mt5_bridge.contracts.output_events import (
    EmergencyTickStale,
)
from mt5_bridge.facade import MT5Bridge


def _wait_for(predicate, *, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("predicate never became true within timeout")


@pytest.fixture
def no_rpc_config(tmp_path):
    """A config file with rpc disabled (so no Twisted reactor)."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[mt5]\n"
        "[rpc]\nenabled = false\n"
        "[retry]\nenabled = false\n"
        "[watchdog]\nenabled = false\n"
        "[kelly]\nenabled = false\n"
    )
    return cfg


@pytest.fixture
def rpc_off_kelly_on_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[mt5]\n"
        "[rpc]\nenabled = false\n"
        "[retry]\nenabled = false\n"
        "[watchdog]\nenabled = true\n"
        "tick_timeout_seconds = 0.2\n"
        "check_interval_seconds = 0.05\n"
        "symbols = []\n"
        "[kelly]\nenabled = true\n"
        "default_fraction = 1.0\n"
    )
    return cfg


@pytest.fixture(autouse=True)
def patch_mt5_client(monkeypatch):
    """Stub MT5Client so it doesn't try to talk to the broker."""
    from mt5_bridge.infra import mt5_client as mod

    def fake_init(self):
        self._initialized = True
        return True

    def fake_shutdown(self):
        self._initialized = False

    def fake_preflight(self):
        return None  # always passes in tests

    monkeypatch.setattr(mod.MT5Client, "initialize", fake_init)
    monkeypatch.setattr(mod.MT5Client, "shutdown", fake_shutdown)
    monkeypatch.setattr(mod.MT5Client, "assert_trade_ready", fake_preflight)
    yield


@pytest.fixture(autouse=True)
def cleanup_actors():
    yield
    pykka.ActorRegistry.stop_all()


def test_rpc_off_feed_input_routes_to_callback(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    received: list = []
    # Subscribe BEFORE start_io
    bridge.subscribe(Tick, received.append)
    bridge.start_io()

    t = Tick(symbol="X", time="t", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1)
    bridge.feed_input(t)

    _wait_for(lambda: len(received) == 1)
    assert received[0].symbol == "X"
    bridge.stop()


def test_subscribe_after_start(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    bridge.start_io()
    received: list = []
    bridge.subscribe(Tick, received.append)

    t = Tick(symbol="Y", time="t", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1)
    bridge.feed_input(t)

    _wait_for(lambda: len(received) == 1)
    bridge.stop()


def test_subscribe_all_catches_everything(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    everything: list = []
    bridge.subscribe_all(everything.append)
    bridge.start_io()

    bridge.feed_input(Tick(symbol="X", time="t", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1))
    bridge.feed_input(Position(ticket=1, symbol="X", pos_type="BUY", volume=1.0,
                               open_price=1.0, cur_price=1.0, sl=0.0, tp=0.0, profit=0.0))

    _wait_for(lambda: len(everything) >= 2)
    types = {type(e).__name__ for e in everything}
    assert "Tick" in types
    assert "PositionOpened" in types
    bridge.stop()


def test_subscribe_one_and_all_both_fire(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    one: list = []
    everything: list = []
    bridge.subscribe(EventType.POSITION_OPENED, one.append)
    bridge.subscribe_all(everything.append)
    bridge.start_io()

    bridge.feed_input(Position(ticket=2, symbol="X", pos_type="BUY", volume=1.0,
                               open_price=1.0, cur_price=1.0, sl=0.0, tp=0.0, profit=0.0))

    _wait_for(lambda: len(one) == 1 and len(everything) == 1)
    bridge.stop()


def test_watchdog_emergency_event_fires(rpc_off_kelly_on_config):
    bridge = MT5Bridge(rpc_off_kelly_on_config)
    emergencies: list = []
    bridge.subscribe(EmergencyTickStale, emergencies.append)
    bridge.start_io()

    # Send one tick → watchdog tracks "X". Then go silent.
    bridge.feed_input(Tick(symbol="X", time="t", time_msc=1, bid=1.0, ask=1.1, last=0.0, volume=1))

    _wait_for(lambda: len(emergencies) == 1, timeout=2.0)
    assert emergencies[0].symbol == "X"
    bridge.stop()


def test_kelly_property_works(rpc_off_kelly_on_config, monkeypatch):
    """Kelly suggest_volume should work after Account is fed."""
    from types import SimpleNamespace

    from mt5_bridge.infra import mt5_client as mc_mod
    monkeypatch.setattr(mc_mod.MT5Client, "symbol_info",
                        lambda self, symbol: SimpleNamespace(
                            trade_tick_value=1.0, trade_tick_size=0.01))

    bridge = MT5Bridge(rpc_off_kelly_on_config)
    bridge.start_io()
    bridge.feed_input(Account(login="1", currency="USD", balance=10000.0,
                              equity=10000.0, margin=0.0, free_margin=10000.0,
                              profit=0.0))

    # The Account hops dispatcher → kelly asynchronously, so the
    # first suggest_volume call may race the propagation. Retry until
    # kelly has the snapshot, then assert on the actual value.
    deadline = time.time() + 2.0
    v = None
    while time.time() < deadline:
        try:
            v = bridge.kelly.suggest_volume("X", 0.6, 2.0, 100.0).get()
            break
        except RuntimeError:
            time.sleep(0.01)
    assert v is not None, "kelly never absorbed Account snapshot"
    assert v == pytest.approx(0.4, rel=1e-6)
    bridge.stop()


def test_kelly_disabled_raises(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    bridge.start_io()
    with pytest.raises(RuntimeError):
        _ = bridge.kelly
    bridge.stop()


def test_feed_input_before_start_raises(no_rpc_config):
    bridge = MT5Bridge(no_rpc_config)
    with pytest.raises(RuntimeError):
        bridge.feed_input(Tick(symbol="X", time="t", time_msc=1,
                               bid=1.0, ask=1.1, last=0.0, volume=1))


def test_command_before_start_raises(no_rpc_config):
    import MetaTrader5 as mt5

    from mt5_bridge.contracts.trade_commands import OpenPosition
    bridge = MT5Bridge(no_rpc_config)
    with pytest.raises(RuntimeError):
        bridge.open_position(OpenPosition(symbol="X", volume=0.1,
                                          type=mt5.ORDER_TYPE_BUY,
                                          price=0.0, sl=0.0, tp=0.0))


def test_preflight_failure_rolls_back_init(no_rpc_config, monkeypatch):
    """If assert_trade_ready raises, start_io must call shutdown and re-raise."""
    from mt5_bridge.infra import mt5_client as mod

    shutdown_called = {"n": 0}
    orig_shutdown = mod.MT5Client.shutdown

    def counting_shutdown(self):
        shutdown_called["n"] += 1
        orig_shutdown(self)

    monkeypatch.setattr(mod.MT5Client, "shutdown", counting_shutdown)
    monkeypatch.setattr(
        mod.MT5Client, "assert_trade_ready",
        lambda self: (_ for _ in ()).throw(RuntimeError("AutoTrading is DISABLED")),
    )

    bridge = MT5Bridge(no_rpc_config)
    with pytest.raises(RuntimeError, match="AutoTrading"):
        bridge.start_io()
    assert shutdown_called["n"] == 1


def test_clean_stop_no_thread_leak(no_rpc_config):
    """After stop() the bridge's actor threads should be gone."""
    import threading
    bridge = MT5Bridge(no_rpc_config)
    bridge.start_io()

    # Quick exercise so threads are definitely up
    bridge.feed_input(Tick(symbol="X", time="t", time_msc=1,
                           bid=1.0, ask=1.1, last=0.0, volume=1))
    _wait_for(lambda: True, timeout=0.05)

    bridge.stop()

    # Give pykka a moment to tear down.
    time.sleep(0.1)

    # Look for any pykka actor threads still running.
    leaked = [t for t in threading.enumerate()
              if "PykkaActorThread" in t.name and t.is_alive()]
    assert not leaked, f"leaked threads: {[t.name for t in leaked]}"
