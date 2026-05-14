"""Public message contracts for mt5-bridge."""
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
    BarClosed,
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

__all__ = [
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
    "BarClosed",
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
