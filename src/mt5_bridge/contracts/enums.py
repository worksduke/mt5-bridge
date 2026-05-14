from __future__ import annotations

from enum import IntEnum


class Side(IntEnum):
    """交易方向"""
    BUY  = 0
    SELL = 1



class EventType(IntEnum):
    """系统输出事件类型"""

    POSITION_OPENED   = 11
    POSITION_CLOSED   = 12
    POSITION_MODIFIED = 13


    ORDER_PLACED      = 21
    ORDER_MODIFIED    = 22
    ORDER_CANCELED    = 23


class TradeState(IntEnum):
    """内部交易订单状态"""

    POSITION_OPENING       = 10
    POSITION_OPENED        = 11
    POSITION_OPEN_FAILED   = 12
    POSITION_CLOSING       = 13
    POSITION_CLOSED        = 14
    POSITION_CLOSE_FAILED  = 15
    POSITION_MODIFYING     = 16
    POSITION_MODIFIED      = 17
    POSITION_MODIFY_FAILED = 18

    ORDER_PLACING          = 20
    ORDER_PLACED           = 21
    ORDER_PLACING_FAILED   = 22
    ORDER_MODIFYING        = 23
    ORDER_MODIFIED         = 24
    ORDER_MODIFY_FAILED    = 25
    ORDER_CANCELLING       = 26
    ORDER_CANCELED         = 27
    ORDER_CANCEL_FAILED    = 28
    ORDER_FILLED           = 29
