"""KellyActor tests.

A fake client returns canned ``symbol_info`` results. We validate
the Kelly formula end-to-end (f* calc, currency-to-lot conversion,
fraction multiplier, clamp) and the error paths.
"""
from __future__ import annotations

from types import SimpleNamespace

import pykka
import pytest

from mt5_bridge.actors.kelly_actor import KellyActor
from mt5_bridge.configs.bridge import KellyConfig
from mt5_bridge.contracts.ea_messages import Account


class FakeClient:
    def __init__(self, info: object | None):
        self._info = info
        self.calls: list[str] = []

    def symbol_info(self, symbol: str):
        self.calls.append(symbol)
        return self._info


@pytest.fixture
def stop_actors():
    pykka.ActorRegistry.stop_all()
    yield
    pykka.ActorRegistry.stop_all()


def _account(equity=10000.0):
    return Account(login="1", currency="USD", balance=equity, equity=equity,
                   margin=0.0, free_margin=equity, profit=0.0)


def _info(tick_value=1.0, tick_size=0.01):
    return SimpleNamespace(trade_tick_value=tick_value, trade_tick_size=tick_size)


def test_no_account_raises(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    with pytest.raises(RuntimeError):
        actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()
    actor.stop()


def test_basic_formula(stop_actors):
    """f* = 0.6 - 0.4/2.0 = 0.4; full Kelly fraction=1.0; equity=10000.

    risk_currency = 10000 * 0.4 = 4000.
    per_lot_loss = stop_loss_pips(100) * (tick_value(1) / tick_size(0.01)) = 10000.
    raw_volume = 4000 / 10000 = 0.4.
    floored to step 0.01 = 0.4. Within [0.01, 10.0] → 0.4.
    """
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info(tick_value=1.0, tick_size=0.01)), cfg)
    actor.tell(_account(equity=10000.0))
    # Give the actor time to absorb the Account
    actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()  # warm
    v = actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()
    assert v == pytest.approx(0.4, rel=1e-6)
    actor.stop()


def test_half_kelly_via_default_fraction(stop_actors):
    """default_fraction=0.5 → halves the volume from the basic case."""
    cfg = KellyConfig(enabled=True, default_fraction=0.5,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account(equity=10000.0))
    v = actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()
    assert v == pytest.approx(0.20, rel=1e-6)
    actor.stop()


def test_fraction_override(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account(equity=10000.0))
    v = actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0, fraction=0.25).get()
    assert v == pytest.approx(0.10, rel=1e-6)
    actor.stop()


def test_clamps_to_max(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=0.05, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account(equity=10000.0))
    v = actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()
    assert v == 0.05
    actor.stop()


def test_clamps_to_min(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=0.001,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account(equity=10000.0))
    v = actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()
    assert v == 0.01
    actor.stop()


def test_unknown_symbol_raises(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(None), cfg)
    actor.tell(_account())
    with pytest.raises(ValueError):
        actor.proxy().suggest_volume("BOGUS", 0.6, 2.0, 100.0).get()
    actor.stop()


def test_invalid_inputs_raise(stop_actors):
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account())
    # Wait for account
    actor.proxy().suggest_volume("X", 0.6, 2.0, 100.0).get()

    with pytest.raises(ValueError):
        actor.proxy().suggest_volume("X", 0.0, 2.0, 100.0).get()
    with pytest.raises(ValueError):
        actor.proxy().suggest_volume("X", 1.0, 2.0, 100.0).get()
    with pytest.raises(ValueError):
        actor.proxy().suggest_volume("X", 0.6, 0.0, 100.0).get()
    with pytest.raises(ValueError):
        actor.proxy().suggest_volume("X", 0.6, 2.0, 0.0).get()
    actor.stop()


def test_negative_kelly_returns_min(stop_actors):
    """When edge is negative (p too low for the payoff), f* = 0 → min_volume."""
    cfg = KellyConfig(enabled=True, default_fraction=1.0,
                      min_volume=0.01, max_volume=10.0, volume_step=0.01)
    actor = KellyActor.start(FakeClient(_info()), cfg)
    actor.tell(_account())
    # p=0.3, b=1.0 → 0.3 - 0.7/1.0 = -0.4 → max(0, -0.4) = 0
    v = actor.proxy().suggest_volume("X", 0.3, 1.0, 100.0).get()
    assert v == 0.01  # clamped to min_volume
    actor.stop()
