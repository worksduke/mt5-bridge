"""KellyActor: position-sizing helper.

Caches the latest ``Account`` snapshot pushed by the Dispatcher and
the ``symbol_info`` results pulled lazily from MT5. Exposes a
synchronous ``suggest_volume(...)`` method via the Pykka proxy that
the user calls through ``MT5Bridge.kelly.suggest_volume(...)``.

Scope:
    - Pure calculation. The bridge does NOT know your win-rate /
      payoff stats — those are strategy-level inputs the caller
      supplies.
    - The actor never places trades. Sizing only.
"""
from __future__ import annotations

import math
from typing import Any

import pykka

from mt5_bridge.contracts.ea_messages import Account
from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.actors.kelly_actor")


class KellyActor(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(self, client: Any, cfg: Any) -> None:
        super().__init__()
        self._client = client
        self._cfg = cfg
        self._latest_account: Account | None = None
        self._symbol_info_cache: dict[str, Any] = {}

    # ── pykka entry point ─────────────────────────────────────
    def on_receive(self, message: object) -> None:
        if isinstance(message, Account):
            self._latest_account = message

    # ── proxy-callable API ────────────────────────────────────
    def suggest_volume(
        self,
        symbol: str,
        win_prob: float,
        win_loss_ratio: float,
        stop_loss_pips: float,
        fraction: float | None = None,
    ) -> float:
        """Return a suggested lot size given the caller's edge stats.

        Kelly criterion: ``f* = p - (1 - p) / b``, where ``b`` is the
        win/loss ratio. The result is multiplied by ``fraction`` (defaults
        to ``cfg.default_fraction``, typically 0.5 for half-Kelly).

        ``stop_loss_pips`` × symbol's per-tick value gives the per-lot
        currency loss; ``equity * f_used / per_lot_loss`` is the raw
        lot count, which is then floored to ``volume_step`` and clamped
        between ``min_volume`` and ``max_volume``.

        Raises:
            RuntimeError: if no Account snapshot has been received yet.
            ValueError:   on any invalid input or unknown symbol.
        """
        if self._latest_account is None:
            raise RuntimeError("kelly: no account snapshot yet")
        if not (0.0 < win_prob < 1.0):
            raise ValueError(f"win_prob must be in (0, 1), got {win_prob}")
        if win_loss_ratio <= 0:
            raise ValueError(f"win_loss_ratio must be > 0, got {win_loss_ratio}")
        if stop_loss_pips <= 0:
            raise ValueError(f"stop_loss_pips must be > 0, got {stop_loss_pips}")

        info = self._symbol_info_cache.get(symbol)
        if info is None:
            info = self._client.symbol_info(symbol)
            if info is None:
                raise ValueError(f"unknown symbol: {symbol}")
            self._symbol_info_cache[symbol] = info

        f_star = max(0.0, win_prob - (1 - win_prob) / win_loss_ratio)
        f_used = f_star * (
            fraction if fraction is not None else self._cfg.default_fraction
        )
        risk_currency = self._latest_account.equity * f_used

        tick_value = float(getattr(info, "trade_tick_value", 0.0))
        tick_size = float(getattr(info, "trade_tick_size", 0.0))
        if tick_size <= 0 or tick_value <= 0:
            raise ValueError(
                f"symbol {symbol!r} has invalid tick_value/tick_size "
                f"({tick_value}/{tick_size})"
            )

        per_lot_loss = stop_loss_pips * (tick_value / tick_size)
        if per_lot_loss <= 0:
            raise ValueError("computed per_lot_loss <= 0")

        raw_volume = risk_currency / per_lot_loss

        step = self._cfg.volume_step
        # Add a tiny epsilon (1e-9 of a step) before flooring so the
        # result of e.g. 0.4 / 0.01 doesn't end up as 39.999... and
        # round DOWN to 0.39. Real trading lot steps are coarse enough
        # that this epsilon is invisible.
        floored = math.floor(raw_volume / step + 1e-9) * step
        clamped = max(self._cfg.min_volume, min(self._cfg.max_volume, floored))

        _log.info(
            "kelly_suggest_volume",
            symbol=symbol,
            win_prob=win_prob,
            win_loss_ratio=win_loss_ratio,
            stop_loss_pips=stop_loss_pips,
            f_star=f_star,
            f_used=f_used,
            risk_currency=risk_currency,
            raw_volume=raw_volume,
            clamped_volume=clamped,
        )
        return clamped


__all__ = ["KellyActor"]
