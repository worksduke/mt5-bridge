"""Demo: open 3 market BUY positions in a row, then close all 3.

Usage:
    python examples/open_close_3.py mt5-bridge/config.toml [SYMBOL] [VOLUME]

Defaults: SYMBOL=XAUUSD, VOLUME=0.01

Pattern:
    Twisted's reactor must own the main thread (so retry scheduling
    via ``reactor.callLater`` works). The demo's actual logic runs on
    a worker thread; the main thread calls ``serve_forever()``. When
    the worker is done it calls ``bridge.stop()``, which schedules
    ``reactor.stop`` and lets ``serve_forever()`` return cleanly.

Requires:
    - MT5 terminal running with a (demo) account
    - ``[rpc] enabled = true`` in the config (this demo runs the reactor)
    - The bridge subscribes to ``TradeStateChanged`` (executor's own
      acknowledgements) so this demo works whether or not the EA push
      channel is also up.
"""
from __future__ import annotations

import sys
import threading
import time

import MetaTrader5 as mt5

from mt5_bridge import (
    ClosePosition,
    MT5Bridge,
    OpenPosition,
)
from mt5_bridge.contracts.enums import TradeState
from mt5_bridge.contracts.internal_messages import TradeStateChanged


def main(config_path: str, symbol: str = "XAUUSD", volume: float = 0.01) -> int:
    bridge = MT5Bridge(config_path)

    opened: list[TradeStateChanged] = []
    closed: list[TradeStateChanged] = []
    failed: list[TradeStateChanged] = []
    opens_done = threading.Event()
    closes_done = threading.Event()
    N = 3

    def on_state(e: TradeStateChanged) -> None:
        if e.state == TradeState.POSITION_OPENED:
            print(f"  [OPENED] ticket={e.ticket} retcode={e.retcode}")
            opened.append(e)
            if len(opened) >= N:
                opens_done.set()
        elif e.state == TradeState.POSITION_CLOSED:
            print(f"  [CLOSED] ticket={e.ticket} retcode={e.retcode}")
            closed.append(e)
            if len(closed) >= N:
                closes_done.set()
        elif e.state.name.endswith("_FAILED"):
            print(f"  [FAIL]   state={e.state.name} retcode={e.retcode} comment={e.comment!r}")
            failed.append(e)
            if e.state == TradeState.POSITION_OPEN_FAILED:
                if len(opened) + len(failed) >= N:
                    opens_done.set()
            elif e.state == TradeState.POSITION_CLOSE_FAILED:
                closes_done.set()

    bridge.subscribe(TradeStateChanged, on_state)
    bridge.start_io()

    exit_code = {"value": 1}   # closed-over mutable for worker → main

    def worker() -> None:
        try:
            print(f"Opening {N} {symbol} BUY positions @ {volume} lots…")
            for i in range(N):
                bridge.open_position(OpenPosition(
                    symbol=symbol, volume=volume, type=mt5.ORDER_TYPE_BUY,
                    price=0.0, sl=0.0, tp=0.0,
                    comment=f"demo open {i+1}",
                ))

            if not opens_done.wait(timeout=15):
                print("[!] timeout waiting for opens", file=sys.stderr)
                return

            if not opened:
                print("[!] no positions opened — aborting close", file=sys.stderr)
                return

            time.sleep(0.5)  # let the broker settle
            print(f"Closing {len(opened)} positions…")
            for e in opened:
                bridge.close_position(ClosePosition(
                    symbol=symbol, volume=volume, type=mt5.ORDER_TYPE_SELL,
                    position=e.ticket, price=0.0,
                    comment=f"demo close ticket={e.ticket}",
                ))

            if not closes_done.wait(timeout=15):
                print("[!] timeout waiting for closes", file=sys.stderr)
                return

            print(f"Done. opened={len(opened)} closed={len(closed)} failed={len(failed)}")
            exit_code["value"] = 0
        finally:
            bridge.stop()

    t = threading.Thread(target=worker, daemon=False, name="demo-worker")
    t.start()
    bridge.serve_forever()    # blocks until worker calls bridge.stop()
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
