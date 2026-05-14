"""``MT5Client.assert_trade_ready`` startup pre-flight tests.

We monkeypatch the ``mt5`` module attributes that ``assert_trade_ready``
reads, so these tests don't actually talk to a broker.
"""
from __future__ import annotations

from types import SimpleNamespace

import MetaTrader5 as mt5
import pytest

from mt5_bridge.configs.mt5 import MT5Config
from mt5_bridge.infra.mt5_client import MT5Client


def _client() -> MT5Client:
    return MT5Client(MT5Config())


def _terminal(connected=True, trade_allowed=True):
    return SimpleNamespace(connected=connected, trade_allowed=trade_allowed)


def _account(trade_allowed=True, trade_expert=True, login=42, trade_mode=0,
             company="DemoBroker"):
    return SimpleNamespace(
        trade_allowed=trade_allowed,
        trade_expert=trade_expert,
        login=login,
        trade_mode=trade_mode,
        company=company,
    )


def test_passes_when_everything_ok(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: _terminal())
    monkeypatch.setattr(mt5, "account_info",  lambda: _account())
    _client().assert_trade_ready()  # no exception


def test_fails_when_no_terminal(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: None)
    monkeypatch.setattr(mt5, "last_error",    lambda: (-1, "no terminal"))
    with pytest.raises(RuntimeError, match="terminal_info"):
        _client().assert_trade_ready()


def test_fails_when_terminal_disconnected(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: _terminal(connected=False))
    with pytest.raises(RuntimeError, match="not connected"):
        _client().assert_trade_ready()


def test_fails_when_autotrading_disabled(monkeypatch):
    """The exact failure mode that motivated this preflight."""
    monkeypatch.setattr(mt5, "terminal_info",
                        lambda: _terminal(trade_allowed=False))
    with pytest.raises(RuntimeError, match="AutoTrading is DISABLED"):
        _client().assert_trade_ready()


def test_fails_when_no_account(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: _terminal())
    monkeypatch.setattr(mt5, "account_info",  lambda: None)
    monkeypatch.setattr(mt5, "last_error",    lambda: (-1, "no login"))
    with pytest.raises(RuntimeError, match="account_info"):
        _client().assert_trade_ready()


def test_fails_when_account_readonly(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: _terminal())
    monkeypatch.setattr(mt5, "account_info",
                        lambda: _account(trade_allowed=False))
    with pytest.raises(RuntimeError, match="read-only"):
        _client().assert_trade_ready()


def test_fails_when_eas_disallowed(monkeypatch):
    monkeypatch.setattr(mt5, "terminal_info", lambda: _terminal())
    monkeypatch.setattr(mt5, "account_info",
                        lambda: _account(trade_expert=False))
    with pytest.raises(RuntimeError, match="Expert Advisors are NOT permitted"):
        _client().assert_trade_ready()
