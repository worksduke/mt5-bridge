"""Regression tests for trade_commands validation.

Mostly there to lock down defaults that have bitten us before:
    - OpenOrder.type_filling default must be ORDER_FILLING_RETURN
      (was ORDER_TYPE_SELL_STOP_LIMIT, which is a different constant
      family entirely; validate() rejected it as 7 ∉ {0,1,2}).
    - OpenOrder.validate() must actually enforce expiration > 0 when
      type_time is SPECIFIED — the original int-vs-string comparison
      was silent dead code.
"""
from __future__ import annotations

import MetaTrader5 as mt5
import pytest

from mt5_bridge.contracts.trade_commands import (
    CancelOrder,
    ModifyOrder,
    OpenOrder,
)


def test_open_order_minimal_defaults_validate():
    """The minimal OpenOrder (only required fields) must pass validate()."""
    cmd = OpenOrder(
        symbol="XAUUSD",
        volume=0.1,
        type=mt5.ORDER_TYPE_BUY_LIMIT,
        price=2000.0,
    )
    cmd.validate()  # no exception
    req = cmd.to_mt5_request()
    assert req["type_filling"] == mt5.ORDER_FILLING_RETURN
    assert req["type_time"] == mt5.ORDER_TIME_GTC
    assert req["action"] == mt5.TRADE_ACTION_PENDING


def test_open_order_default_type_filling_is_return():
    """Lock the default — anything else means we re-broke the bug."""
    cmd = OpenOrder(symbol="X", volume=0.1, type=mt5.ORDER_TYPE_BUY_LIMIT, price=1.0)
    assert cmd.type_filling == mt5.ORDER_FILLING_RETURN
    assert cmd.type_filling in (
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_RETURN,
    )


def test_open_order_specified_time_requires_expiration():
    """The previously-dead expiration-vs-type_time check must now bite."""
    cmd = OpenOrder(
        symbol="X", volume=0.1, type=mt5.ORDER_TYPE_BUY_LIMIT, price=1.0,
        type_time=mt5.ORDER_TIME_SPECIFIED,
        expiration=0,
    )
    with pytest.raises(ValueError, match="expiration"):
        cmd.validate()


def test_open_order_specified_time_with_expiration_ok():
    cmd = OpenOrder(
        symbol="X", volume=0.1, type=mt5.ORDER_TYPE_BUY_LIMIT, price=1.0,
        type_time=mt5.ORDER_TIME_SPECIFIED,
        expiration=2_000_000_000,   # far-future epoch
    )
    cmd.validate()


def test_open_order_invalid_type_filling_raises():
    cmd = OpenOrder(
        symbol="X", volume=0.1, type=mt5.ORDER_TYPE_BUY_LIMIT, price=1.0,
        type_filling=mt5.ORDER_TYPE_SELL_STOP_LIMIT,   # the bad default we used to have
    )
    with pytest.raises(ValueError, match="type_filling"):
        cmd.validate()


def test_modify_order_defaults():
    cmd = ModifyOrder(order=12345, price=2000.0)
    cmd.validate()
    assert cmd.type_filling == mt5.ORDER_FILLING_RETURN
    assert cmd.action == mt5.TRADE_ACTION_MODIFY


def test_cancel_order_defaults():
    cmd = CancelOrder(order=12345)
    cmd.validate()
    assert cmd.action == mt5.TRADE_ACTION_REMOVE
    req = cmd.to_mt5_request()
    assert req["action"] == mt5.TRADE_ACTION_REMOVE
    assert req["order"] == 12345
