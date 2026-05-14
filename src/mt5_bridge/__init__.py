"""mt5-bridge: pykka + msgspec event bridge for MetaTrader 5."""

# Single source of truth for the package version. pyproject.toml reads this
# via ``[tool.setuptools.dynamic] version = { attr = "mt5_bridge.__version__" }``.
__version__ = "0.1.0"

from mt5_bridge.contracts.ea_messages import (
    DECODER,
    Account,
    Bar,
    Connected,
    EAMessage,
    HistoryBar,
    HistoryBarDone,
    HistoryTick,
    HistoryTickDone,
    Order,
    Position,
    Tick,
)
from mt5_bridge.contracts.enums import EventType, Side, TradeState
from mt5_bridge.contracts.output_events import (
    EmergencyTickStale,
    OrderCanceled,
    OrderModified,
    OrderPlaced,
    PositionClosed,
    PositionModified,
    PositionOpened,
)
from mt5_bridge.contracts.trade_commands import (
    CancelOrder,
    ClosePosition,
    ModifyOrder,
    ModifyPosition,
    OpenOrder,
    OpenPosition,
)
from mt5_bridge.facade import MT5Bridge

__all__ = [
    "__version__",
    # Facade
    "MT5Bridge",
    # ea_messages
    "DECODER",
    "Account",
    "Bar",
    "Connected",
    "EAMessage",
    "HistoryBar",
    "HistoryBarDone",
    "HistoryTick",
    "HistoryTickDone",
    "Order",
    "Position",
    "Tick",
    # enums
    "EventType",
    "Side",
    "TradeState",
    # output_events
    "EmergencyTickStale",
    "OrderCanceled",
    "OrderModified",
    "OrderPlaced",
    "PositionClosed",
    "PositionModified",
    "PositionOpened",
    # trade_commands
    "CancelOrder",
    "ClosePosition",
    "ModifyOrder",
    "ModifyPosition",
    "OpenOrder",
    "OpenPosition",
]
