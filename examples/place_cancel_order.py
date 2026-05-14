"""Demo: place a BUY_LIMIT well below market, then cancel it.

Usage:
    python examples/place_cancel_order.py mt5-bridge/config.toml [SYMBOL] [VOLUME]

Defaults: SYMBOL=XAUUSD, VOLUME=0.01

The limit price is set to ``current_bid * (1 - DROP_RATIO)`` so the
broker won't fill it during the demo (DROP_RATIO defaults to 2 %,
comfortably out of fill range for any normal market). The pending
order is then cancelled.

Same worker-thread + serve_forever pattern as ``open_close_3.py`` —
see that file's docstring for the rationale.

Requires:
    - MT5 terminal running with a (demo) account
    - ``[rpc] enabled = true`` in the config
    - A live quote for SYMBOL (read via the MT5 SDK directly — no EA
      needed for this demo)
"""
from __future__ import annotations

import sys
import threading
import time

import MetaTrader5 as mt5

from mt5_bridge import (
    CancelOrder,
    MT5Bridge,
    OpenOrder,
)
from mt5_bridge.contracts.enums import TradeState
from mt5_bridge.contracts.internal_messages import TradeStateChanged

DROP_RATIO = 0.02


def main(config_path: str, symbol: str = "XAUUSD", volume: float = 0.01) -> int:
    bridge = MT5Bridge(config_path)

    placed: list[TradeStateChanged] = []
    canceled: list[TradeStateChanged] = []
    placed_done = threading.Event()
    canceled_done = threading.Event()

    def on_state(e: TradeStateChanged) -> None:
        if e.state == TradeState.ORDER_PLACED:
            print(f"  [PLACED]   ticket={e.ticket} retcode={e.retcode}")
            placed.append(e)
            placed_done.set()
        elif e.state == TradeState.ORDER_CANCELED:
            print(f"  [CANCELED] ticket={e.ticket} retcode={e.retcode}")
            canceled.append(e)
            canceled_done.set()
        elif e.state.name.endswith("_FAILED"):
            print(f"  [FAIL]     state={e.state.name} retcode={e.retcode} comment={e.comment!r}")
            placed_done.set()
            canceled_done.set()

    bridge.subscribe(TradeStateChanged, on_state)
    bridge.start_io()

    exit_code = {"value": 1}

    def worker() -> None:
        try:
            # Make sure MT5 has the symbol selected (Market Watch). Without
            # this, ``symbol_info_tick`` returns None for symbols the
            # terminal hasn't subscribed to.
            if not mt5.symbol_select(symbol, True):
                print(f"[!] could not select symbol {symbol}: {mt5.last_error()}",
                      file=sys.stderr)
                return

            tick = mt5.symbol_info_tick(symbol)
            if tick is None or tick.bid <= 0:
                print(f"[!] no live quote for {symbol}: {mt5.last_error()}",
                      file=sys.stderr)
                return

            info = mt5.symbol_info(symbol)
            digits = int(getattr(info, "digits", 5)) if info else 5
            limit_price = round(tick.bid * (1 - DROP_RATIO), digits)

            print(f"Current {symbol} bid={tick.bid}, ask={tick.ask}")
            print(f"Placing BUY_LIMIT @ {limit_price} (volume={volume})…")

            bridge.open_order(OpenOrder(
                symbol=symbol, volume=volume,
                type=mt5.ORDER_TYPE_BUY_LIMIT,
                price=limit_price,
                comment="demo place",
            ))

            if not placed_done.wait(timeout=10):
                print("[!] timeout waiting for ORDER_PLACED", file=sys.stderr)
                return
            if not placed:
                print("[!] order placement failed — nothing to cancel", file=sys.stderr)
                return

            ticket = placed[0].ticket
            time.sleep(0.5)

            print(f"Cancelling order {ticket}…")
            bridge.cancel_order(CancelOrder(order=ticket, comment="demo cancel"))

            if not canceled_done.wait(timeout=10):
                print("[!] timeout waiting for ORDER_CANCELED", file=sys.stderr)
                return

            print(f"Done. placed={len(placed)} canceled={len(canceled)}")
            exit_code["value"] = 0
        finally:
            bridge.stop()

    t = threading.Thread(target=worker, daemon=False, name="demo-worker")
    t.start()
    bridge.serve_forever()
    t.join(timeout=2)
    return exit_code["value"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"usage: python {sys.argv[0]} <config.toml> [SYMBOL] [VOLUME]",
              file=sys.stderr)
        sys.exit(2)
    cfg = sys.argv[1]
    sym = sys.argv[2] if len(sys.argv) > 2 else "XAUUSD"
    vol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.01
    sys.exit(main(cfg, sym, vol))
