"""Thin wrapper around the MetaTrader5 Python SDK.

Owns:
    - Terminal connection lifecycle (initialize / shutdown / is_connected)
    - All six trade calls with a unified ``TradeResult`` return shape
    - ``symbol_info`` passthrough for KellyActor sizing

NOT owned here:
    - Command validation — done in ``trade_commands.*.validate()``
    - Async / actor concerns — Executor wraps these calls
"""
from __future__ import annotations

from typing import Any

import MetaTrader5 as mt5

from mt5_bridge.configs.mt5 import MT5Config
from mt5_bridge.contracts.internal_messages import TradeResult
from mt5_bridge.contracts.trade_commands import (
    CancelOrder,
    ClosePosition,
    ModifyOrder,
    ModifyPosition,
    OpenOrder,
    OpenPosition,
)
from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.infra.mt5_client")


class MT5Client:
    def __init__(self, mt5_config: MT5Config):
        self.cfg = mt5_config
        self._initialized = False

    # ====================================
    # Lifecycle
    # ====================================
    def initialize(self) -> bool:
        path = self.cfg.mt5_local_path
        path_str = str(path) if path is not None else None

        _log.info(
            "mt5_initializing",
            path=path_str or "(auto-detect)",
            account=self.cfg.mt5_account,
        )

        # Build kwargs lazily — leaving login/password/server out when
        # they're empty lets the SDK fall back to the terminal's own
        # last-used account, which is what users expect when they boot
        # the bridge with a blank config.
        kwargs: dict[str, Any] = {}
        if path_str:
            kwargs["path"] = path_str
        if self.cfg.mt5_account:
            kwargs["login"] = self.cfg.mt5_account
        if self.cfg.mt5_password:
            kwargs["password"] = self.cfg.mt5_password
        if self.cfg.mt5_server:
            kwargs["server"] = self.cfg.mt5_server

        ok = mt5.initialize(**kwargs)
        self._initialized = bool(ok)

        if not ok:
            _log.error("mt5_initialize_failed", error=mt5.last_error())
            return False

        _log.info(
            "mt5_initialized",
            path=path_str or "(auto-detect)",
            account=self.cfg.mt5_account,
        )
        return True

    def shutdown(self) -> None:
        if self._initialized:
            mt5.shutdown()
            self._initialized = False
        _log.info("mt5_shutdown")

    def is_connected(self) -> bool:
        info = mt5.terminal_info()
        return info is not None and info.connected

    # ====================================
    # Pre-flight (call after initialize, before sending orders)
    # ====================================
    def assert_trade_ready(self) -> None:
        """Validate the terminal + account can actually accept trades.

        This catches the kind of failures that would otherwise only
        surface on the first ``order_send`` and waste retry budget:

        - AutoTrading button disabled in MT5 terminal (Ctrl+E)
        - Account locked / read-only
        - Account doesn't allow EAs

        Raises ``RuntimeError`` with a human-readable message on the
        first failure found. Bridge.start_io calls this so a misconfig
        fails at boot, not at the first command.
        """
        terminal = mt5.terminal_info()
        if terminal is None:
            raise RuntimeError(
                f"MT5 terminal_info() unavailable: {mt5.last_error()}"
            )
        if not getattr(terminal, "connected", False):
            raise RuntimeError("MT5 terminal is not connected to the broker")
        if not getattr(terminal, "trade_allowed", True):
            raise RuntimeError(
                "AutoTrading is DISABLED in the MT5 terminal. "
                "Enable it (toolbar AutoTrading button, or Ctrl+E) and retry."
            )

        account = mt5.account_info()
        if account is None:
            raise RuntimeError(
                f"MT5 account_info() unavailable: {mt5.last_error()} "
                "(no account logged in?)"
            )
        if not getattr(account, "trade_allowed", True):
            raise RuntimeError(
                "Account is read-only (trade_allowed=False) — broker may have "
                "disabled trading on this login."
            )
        if not getattr(account, "trade_expert", True):
            raise RuntimeError(
                "Expert Advisors are NOT permitted on this account "
                "(trade_expert=False). Switch to an EA-enabled account."
            )

        _log.info(
            "trade_ready",
            login=getattr(account, "login", "?"),
            mode=getattr(account, "trade_mode", "?"),
            company=getattr(account, "company", "?"),
        )

    # ====================================
    # Symbol info (Kelly sizing)
    # ====================================
    def symbol_info(self, symbol: str) -> Any:
        """Return the raw ``SymbolInfo`` namedtuple from MT5.

        Caller (KellyActor) reads ``trade_tick_value`` and
        ``trade_tick_size`` for currency-per-lot conversion. Returns
        ``None`` if the symbol is unknown — callers must handle.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            _log.warning("mt5_symbol_info_unknown", symbol=symbol, error=mt5.last_error())
        return info

    # ====================================
    # Trade calls — unified TradeResult shape
    # ====================================
    def open_position(self, command: OpenPosition) -> TradeResult:
        return self._send(command.to_mt5_request(), action="open_position")

    def close_position(self, command: ClosePosition) -> TradeResult:
        return self._send(command.to_mt5_request(), action="close_position")

    def modify_position(self, command: ModifyPosition) -> TradeResult:
        return self._send(command.to_mt5_request(), action="modify_position")

    def open_order(self, command: OpenOrder) -> TradeResult:
        return self._send(command.to_mt5_request(), action="open_order")

    def modify_order(self, command: ModifyOrder) -> TradeResult:
        return self._send(command.to_mt5_request(), action="modify_order")

    def cancel_order(self, command: CancelOrder) -> TradeResult:
        return self._send(command.to_mt5_request(), action="cancel_order")

    # ====================================
    # Internal
    # ====================================
    def _send(self, request: dict[str, Any], *, action: str) -> TradeResult:
        result = mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            _log.error("mt5_order_send_none", action=action, error=err)
            return TradeResult(ok=False, retcode=-1, comment=str(err))

        # MT5 returns a TRADE_RETCODE_DONE on success; some brokers
        # also return DONE_PARTIAL. Treat both as success.
        ok = result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL)

        # ``order`` is the ticket for new positions / pending orders;
        # for modify / cancel calls MT5 echoes the same ticket back.
        ticket = int(getattr(result, "order", 0) or 0)
        if not ticket:
            ticket = int(getattr(result, "deal", 0) or 0)

        comment = str(getattr(result, "comment", "") or "")

        log = _log.info if ok else _log.warning
        log(
            "mt5_order_send",
            action=action,
            ok=ok,
            retcode=result.retcode,
            ticket=ticket,
            comment=comment,
        )
        return TradeResult(ok=ok, ticket=ticket, retcode=int(result.retcode), comment=comment)
