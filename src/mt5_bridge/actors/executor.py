"""Executor actor: serial command execution + retry + stale check.

Owns no MT5 mutable state — every call goes straight through to
``MT5Client``. The actor's mailbox enforces serial ordering of trade
requests (MT5 SDK is not thread-safe).

Retry behaviour:
    - Only retcodes in ``RetryConfig.retryable_retcodes`` are retried.
    - Backoff is exponential with jitter, capped by ``max_delay_ms``.
    - Retries are scheduled via the injected ``Scheduler``, NOT via
      ``time.sleep`` — the mailbox stays unblocked so SymbolStale /
      SymbolAlive updates can land between attempts.

Stale handling:
    - ``SymbolStale`` / ``SymbolAlive`` come in from TickWatchdog.
    - When a symbol is stale, ``OpenPosition`` / ``OpenOrder`` for
      that symbol are rejected immediately (no client call).
    - Close / Modify / Cancel are NOT blocked by stale — exiting
      a position when ticks stop is exactly when you'd want them to
      go through.
"""
from __future__ import annotations

import random
from typing import Any
from uuid import uuid4

import pykka

from mt5_bridge.contracts.enums import TradeState
from mt5_bridge.contracts.internal_messages import (
    SymbolAlive,
    SymbolStale,
    TradeResult,
    TradeStateChanged,
    _RetryAttempt,
)
from mt5_bridge.contracts.trade_commands import (
    CancelOrder,
    ClosePosition,
    ModifyOrder,
    ModifyPosition,
    OpenOrder,
    OpenPosition,
)
from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.actors.executor")


# (in-progress, success, failed, kind name)
_STATE_TABLE: dict[type, tuple[TradeState, TradeState, TradeState, str]] = {
    OpenPosition:   (TradeState.POSITION_OPENING,   TradeState.POSITION_OPENED,    TradeState.POSITION_OPEN_FAILED,    "open_position"),
    ClosePosition:  (TradeState.POSITION_CLOSING,   TradeState.POSITION_CLOSED,    TradeState.POSITION_CLOSE_FAILED,   "close_position"),
    ModifyPosition: (TradeState.POSITION_MODIFYING, TradeState.POSITION_MODIFIED,  TradeState.POSITION_MODIFY_FAILED,  "modify_position"),
    OpenOrder:      (TradeState.ORDER_PLACING,      TradeState.ORDER_PLACED,       TradeState.ORDER_PLACING_FAILED,    "open_order"),
    ModifyOrder:    (TradeState.ORDER_MODIFYING,    TradeState.ORDER_MODIFIED,     TradeState.ORDER_MODIFY_FAILED,     "modify_order"),
    CancelOrder:    (TradeState.ORDER_CANCELLING,   TradeState.ORDER_CANCELED,     TradeState.ORDER_CANCEL_FAILED,     "cancel_order"),
}

_OPENING_TYPES = (OpenPosition, OpenOrder)


class Executor(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(
        self,
        client: Any,                          # MT5Client (avoid hard import for testability)
        dispatcher_ref: pykka.ActorRef,
        scheduler: Any,                       # Scheduler protocol
        retry_cfg: Any,                       # RetryConfig
    ) -> None:
        super().__init__()
        self._client = client
        self._dispatcher = dispatcher_ref
        self._scheduler = scheduler
        self._retry_cfg = retry_cfg
        self._stale: set[str] = set()

    # ── pykka entry point ─────────────────────────────────────
    def on_receive(self, message: object) -> None:
        if isinstance(message, _RetryAttempt):
            self._exec(message.cmd, attempt=message.attempt, request_id=message.request_id)
            return
        if isinstance(message, SymbolStale):
            self._stale.add(message.symbol)
            _log.warning(
                "symbol_marked_stale",
                symbol=message.symbol,
                silence_seconds=message.silence_seconds,
            )
            return
        if isinstance(message, SymbolAlive):
            self._stale.discard(message.symbol)
            _log.info("symbol_marked_alive", symbol=message.symbol)
            return
        if type(message) in _STATE_TABLE:
            self._exec(message, attempt=1, request_id=uuid4().hex)
            return

        _log.warning("executor_unsupported_message", msg_type=type(message).__name__)

    # ── core ──────────────────────────────────────────────────
    def _exec(self, cmd: Any, *, attempt: int, request_id: str) -> None:
        cmd_type = type(cmd)
        opening_state, success_state, failed_state, kind = _STATE_TABLE[cmd_type]

        # 1) Stale rejection (only for entry orders)
        symbol = getattr(cmd, "symbol", None)
        if isinstance(cmd, _OPENING_TYPES) and symbol in self._stale:
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=failed_state,
                retcode=-1,
                comment="symbol_tick_stale",
            ))
            return

        # 2) Validation guard (commands have their own .validate()).
        try:
            cmd.validate()
        except ValueError as e:
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=failed_state,
                retcode=-2,
                comment=f"validation_failed: {e}",
            ))
            return

        # 3) "Now in flight" notice on the first attempt
        if attempt == 1:
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=opening_state,
            ))

        # 4) Call MT5
        try:
            result: TradeResult = getattr(self._client, kind)(cmd)
        except Exception as e:
            _log.exception("client_call_failed", kind=kind, request_id=request_id)
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=failed_state,
                retcode=-3,
                comment=f"client_exception: {e}",
            ))
            return

        if result.ok:
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=success_state,
                ticket=result.ticket,
                retcode=result.retcode,
                comment=result.comment,
            ))
            return

        # 5) Failure path: maybe retry
        if (not self._retry_cfg.enabled
                or result.retcode not in self._retry_cfg.retryable_retcodes
                or attempt >= self._retry_cfg.max_attempts):
            self._emit(TradeStateChanged(
                request_id=request_id,
                state=failed_state,
                ticket=result.ticket,
                retcode=result.retcode,
                comment=result.comment,
            ))
            return

        # 6) Schedule the retry on the scheduler — DON'T sleep here,
        # the mailbox needs to keep accepting SymbolStale / SymbolAlive
        # and other commands.
        delay_ms = min(
            self._retry_cfg.base_delay_ms * (2 ** (attempt - 1)),
            self._retry_cfg.max_delay_ms,
        )
        jitter = (random.random() * 2 - 1) * self._retry_cfg.jitter_ratio
        delay_seconds = max(0.0, delay_ms * (1 + jitter)) / 1000.0

        actor_ref = self.actor_ref
        next_attempt = attempt + 1
        retry_msg = _RetryAttempt(
            request_id=request_id,
            cmd_kind=kind,
            cmd=cmd,
            attempt=next_attempt,
        )

        def _fire() -> None:
            actor_ref.tell(retry_msg)

        _log.info(
            "trade_retry_scheduled",
            request_id=request_id,
            kind=kind,
            attempt=attempt,
            next_attempt=next_attempt,
            delay_ms=delay_ms,
            retcode=result.retcode,
        )
        self._scheduler.schedule(delay_seconds, _fire)

    def _emit(self, msg: TradeStateChanged) -> None:
        # Defensive: dispatcher may have been stopped before the executor
        # finished its mailbox during shutdown — that race is benign.
        try:
            self._dispatcher.tell(msg)
        except pykka.ActorDeadError:
            pass


__all__ = ["Executor"]
