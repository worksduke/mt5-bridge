from __future__ import annotations

from typing import Any

import MetaTrader5 as mt5
import msgspec


class OpenPosition(msgspec.Struct, frozen=True, gc=False):
    """用户开仓指令

    必填：
        - symbol:     交易对象
        - volume:     交易量
        - type:       仓位类型, 0 表示开多, 1 表示开空

    选填：
        - price:      开仓价格
        - sl:         止损价格
        - tp:         止盈价格

    默认值：
        - action:       默认 mt5.TRADE_ACTION_DEAL
        - deviation:    滑点，默认 20
        - magic:        幻术码，默认 0
        - comment:      备注，默认 ""
        - type_time:    时间类型，默认 mt5.ORDER_TIME_GTC
        - type_filling: 成交类型，默认 mt5.ORDER_FILLING_IOC 。
            可选: mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN

    内建方法：
        - to_mt5_request: 转换为 mt5 请求字典
        - validate:     校验字段是否合法
        - is_valid:     校验字段是否合法
    """
    symbol:       str
    volume:       float
    type:         int

    price:        float
    sl:           float
    tp:           float

    action:       int = mt5.TRADE_ACTION_DEAL
    deviation:    int = 20
    magic:        int = 0
    comment:      str = ""
    type_time:    int = mt5.ORDER_TIME_GTC
    type_filling: int = mt5.ORDER_FILLING_IOC


    def to_mt5_request(self) -> dict[str, Any]:
        """转换为 mt5 请求字典
        """

        self.validate()


        req: dict[str, Any] = {
            "action":       self.action,
            "symbol":       self.symbol,
            "volume":       self.volume,
            "type":         self.type,
            "deviation":    self.deviation,
            "magic":        self.magic,
            "comment":      self.comment,
            "type_time":    self.type_time,
            "type_filling": self.type_filling,
        }

        if self.price > 0:
            req["price"] = self.price

        if self.sl > 0:
            req["sl"] = self.sl

        if self.tp > 0:
            req["tp"] = self.tp

        return req


    def validate(self) -> None:
        """内部检查函数，校验失败时抛出 ValueError

        Raises:
            ValueError: 任何字段不合法时抛出
        """
        # 1. symbol 校验
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError(f"symbol 不能为空，当前值: {self.symbol!r}")

        # 2. volume 校验
        if self.volume <= 0:
            raise ValueError(f"volume 必须大于 0，当前值: {self.volume}")

        # 3. type 校验
        if self.type not in (
            mt5.ORDER_TYPE_BUY,
            mt5.ORDER_TYPE_SELL,
        ):
            raise ValueError(
                f"type 必须是 {mt5.ORDER_TYPE_BUY} 或 {mt5.ORDER_TYPE_SELL} 之一，"
                f"当前值: {self.type!r}"
            )

        # 4. price 校验
        if self.price < 0:
            raise ValueError(f"price 不能为负，当前值: {self.price}")

        # 5. sl/tp 校验
        if self.sl < 0:
            raise ValueError(f"sl 不能为负，当前值: {self.sl}")
        if self.tp < 0:
            raise ValueError(f"tp 不能为负，当前值: {self.tp}")

        # 6. SL/TP 与方向的合理性（仅当 price > 0 时校验）
        if self.price > 0:
            if self.type == mt5.ORDER_TYPE_BUY:
                if self.sl > 0 and self.sl >= self.price:
                    raise ValueError(
                        f"BUY 单的 sl({self.sl}) 必须 < price({self.price})"
                    )
                if self.tp > 0 and self.tp <= self.price:
                    raise ValueError(
                        f"BUY 单的 tp({self.tp}) 必须 > price({self.price})"
                    )
            elif self.type == mt5.ORDER_TYPE_SELL:
                if self.sl > 0 and self.sl <= self.price:
                    raise ValueError(
                        f"SELL 单的 sl({self.sl}) 必须 > price({self.price})"
                    )
                if self.tp > 0 and self.tp >= self.price:
                    raise ValueError(
                        f"SELL 单的 tp({self.tp}) 必须 < price({self.price})"
                    )

        # 7. deviation 校验
        if self.deviation < 0:
            raise ValueError(f"deviation 不能为负，当前值: {self.deviation}")

        # 8. magic 校验
        if self.magic < 0:
            raise ValueError(f"magic 不能为负，当前值: {self.magic}")

        # 9. type_filling / type_time 校验
        if self.type_filling not in (
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_RETURN,
        ):
            raise ValueError(
                f"type_filling 必须是 {sorted((mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN))} 之一，"
                f"当前值: {self.type_filling!r}"
            )
        if self.type_time not in (
            mt5.ORDER_TIME_GTC,
            mt5.ORDER_TIME_DAY,
            mt5.ORDER_TIME_SPECIFIED,
            mt5.ORDER_TIME_SPECIFIED_DAY,
        ):
            raise ValueError(
                f"type_time 必须是 {sorted((mt5.ORDER_TIME_GTC, mt5.ORDER_TIME_DAY, mt5.ORDER_TIME_SPECIFIED, mt5.ORDER_TIME_SPECIFIED_DAY))} 之一，"
                f"当前值: {self.type_time!r}"
            )


    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False


class ClosePosition(msgspec.Struct, frozen=True, gc=False):
    """用户平仓指令

    必填：
        - symbol:     交易对象
        - volume:     交易量
        - type:       反向类型, 0 表示平空, 1 表示平多
        - position:   仓位ID

    选填：
        - price:      平仓价格, 0 表示按当前市价平仓

    默认值：
        - action:       默认 mt5.TRADE_ACTION_DEAL
        - deviation:    滑点，默认 20
        - magic:        幻术码，默认 0
        - comment:      备注，默认 ""
        - type_time:    时间类型，默认 mt5.ORDER_TIME_GTC
        - type_filling: 成交类型，默认 mt5.ORDER_FILLING_IOC 。
            可选: mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN

    内建方法：
        - to_mt5_request: 转换为 mt5 请求字典
        - validate:     校验字段是否合法
        - is_valid:     校验字段是否合法
    """
    symbol:       str
    volume:       float
    type:         int
    position:     int

    price:        float = 0.0   # 0 表示按当前市价平仓

    action:       int = mt5.TRADE_ACTION_DEAL
    deviation:    int = 20
    magic:        int = 0
    comment:      str = ""
    type_time:    int = mt5.ORDER_TIME_GTC
    type_filling: int = mt5.ORDER_FILLING_IOC


    def validate(self) -> None:
        """内部检查函数，校验失败时抛出 ValueError
        """
        if not self.symbol:
            raise ValueError("symbol 不能为空")

        if self.volume <= 0:
            raise ValueError(f"volume 必须大于 0，当前: {self.volume}")

        if self.type not in (
            mt5.ORDER_TYPE_BUY,
            mt5.ORDER_TYPE_SELL,
        ):
            raise ValueError(
                f"平仓 type 只能是 {mt5.ORDER_TYPE_BUY} 或 {mt5.ORDER_TYPE_SELL}，当前: {self.type!r}"
            )

        if self.position <= 0:
            raise ValueError(
                f"position(持仓 ticket) 必须 > 0，当前: {self.position}"
            )

        if self.deviation < 0:
            raise ValueError("deviation 不能为负")

        if self.magic < 0:
            raise ValueError("magic 不能为负")

        if self.type_filling not in (
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_RETURN,
        ):
            raise ValueError(
                f"type_filling 必须是 {sorted((mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN))} 之一"
            )

        if self.type_time not in (
            mt5.ORDER_TIME_GTC,
            mt5.ORDER_TIME_DAY,
            mt5.ORDER_TIME_SPECIFIED,
            mt5.ORDER_TIME_SPECIFIED_DAY,
        ):
            raise ValueError(
                f"type_time 必须是 {sorted((mt5.ORDER_TIME_GTC, mt5.ORDER_TIME_DAY, mt5.ORDER_TIME_SPECIFIED, mt5.ORDER_TIME_SPECIFIED_DAY))} 之一"
            )


    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False


    def to_mt5_request(self) -> dict[str, Any]:
        """转换为 mt5 请求字典
        """
        self.validate()

        req: dict[str, Any] = {
            "action":       self.action,
            "symbol":       self.symbol,
            "volume":       self.volume,
            "type":         self.type,
            "position":     self.position,    # 关键字段
            "deviation":    self.deviation,
            "magic":        self.magic,
            "comment":      self.comment,
            "type_time":    self.type_time,
            "type_filling": self.type_filling,
        }

        if self.price > 0:
            req["price"] = self.price

        return req


class ModifyPosition(msgspec.Struct, frozen=True, gc=False):
    """用户修改仓位指令

    必填：
        - symbol:   交易对象
        - position: 仓位ID

    选填：
        - sl:         新止损价，0 = 取消止损
        - tp:         新止盈价，0 = 取消止盈

    默认值：
        - magic:      幻术码，默认 0
        - comment:    备注，默认 ""
        - action:     操作类型，默认 mt5.TRADE_ACTION_MODIFY

    内建方法：
        - to_mt5_request: 转换为 mt5 请求字典
        - validate:     校验字段是否合法
        - is_valid:     校验字段是否合法
    """
    symbol:   str
    position: int

    sl: float = 0.0        # 新止损价，0 = 取消止损
    tp: float = 0.0        # 新止盈价，0 = 取消止盈


    magic:   int = 0
    comment: str = ""
    action:  int = mt5.TRADE_ACTION_MODIFY


    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("symbol 不能为空")

        if self.position <= 0:
            raise ValueError(
                f"position(持仓 ticket) 必须 > 0，当前: {self.position}"
            )

        if self.sl < 0:
            raise ValueError(f"sl 不能为负，当前: {self.sl}")

        if self.tp < 0:
            raise ValueError(f"tp 不能为负，当前: {self.tp}")

        # 至少要修改一项
        if self.sl == 0 and self.tp == 0:
            raise ValueError("sl 和 tp 不能同时为 0，否则修改无意义")

        if self.magic < 0:
            raise ValueError("magic 不能为负")

    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False


    def to_mt5_request(self) -> dict[str, Any]:
        """转换为 MT5 修改请求字典"""
        self.validate()

        req: dict[str, Any] = {
            "action":   self.action,
            "symbol":   self.symbol,
            "position": self.position,
            "magic":    self.magic,
            "comment":  self.comment,
        }

        # 只发送非 0 的字段（0 在 MT5 中表示取消该止损/止盈）
        # 注意：如果用户明确要"取消"止损/止盈，需要传 0
        # 这里我们总是传，让 MT5 自己处理
        req["sl"] = self.sl
        req["tp"] = self.tp

        return req


class OpenOrder(msgspec.Struct, frozen=True, gc=False):
    """用户挂单指令

    挂单类型说明：
        - BUY_LIMIT:       code=2, 价格下方挂买单（期望反弹买入）
        - SELL_LIMIT:      code=3, 价格上方挂卖单（期望回落卖出）
        - BUY_STOP:        code=4, 价格上方挂买单（突破买入）
        - SELL_STOP:       code=5, 价格下方挂卖单（破位卖出）
        - BUY_STOP_LIMIT:  code=6, 突破触发后挂买入限价单
        - SELL_STOP_LIMIT: code=7, 破位触发后挂卖出限价单

    必填：
        - symbol:     交易对象
        - volume:     交易量
        - type:       挂单类型
        - price:      挂单价格（必填）

    选填：
        - sl:         止损价，0 = 不设置
        - tp:         止盈价，0 = 不设置
        - stoplimit:  仅 STOP_LIMIT 类型需要：触发后挂的限价
        - expiration: 过期时间戳（秒），0 = 不过期，仅 type_time 为 SPECIFIED 时生效

    默认值：
        - action:       默认 mt5.TRADE_ACTION_PENDING
        - deviation:    默认 20
        - magic:        默认 0
        - comment:      默认 ""
        - type_time:    默认 mt5.ORDER_TIME_GTC
        - type_filling: 默认 mt5.ORDER_FILLING_IOC

    内建方法：
        - to_mt5_request: 转换为 mt5 请求字典
        - validate:     校验字段是否合法
        - is_valid:     校验字段是否合法
    """
    # === 必填字段 ===
    symbol: str            # 交易品种
    volume: float          # 手数
    type:   int            # 挂单类型（mt5.ORDER_TYPE_*_LIMIT/STOP/STOP_LIMIT）
    price:  float          # 挂单触发价（必填）

    # === 可选字段 ===
    sl:         float = 0.0    # 止损价，0 = 不设置
    tp:         float = 0.0    # 止盈价，0 = 不设置
    stoplimit:  float = 0.0    # 仅 STOP_LIMIT 类型需要：触发后挂的限价
    expiration: int   = 0      # 过期时间戳（秒），0 = 不过期，仅 type_time 为 SPECIFIED 时生效


    # === 默认值 ===
    action:       int = mt5.TRADE_ACTION_PENDING
    magic:        int = 0
    comment:      str = ""
    deviation:    int = 20
    type_time:    int = mt5.ORDER_TIME_GTC          # GTC = 撤单前一直有效
    type_filling: int = mt5.ORDER_FILLING_RETURN    # 挂单官方推荐 RETURN



    def validate(self) -> None:
        # 1. symbol
        if not self.symbol:
            raise ValueError("symbol 不能为空")

        # 2. volume
        if self.volume <= 0:
            raise ValueError(f"volume 必须大于 0，当前: {self.volume}")

        # 3. type
        if self.type not in (
            mt5.ORDER_TYPE_BUY_LIMIT,
            mt5.ORDER_TYPE_SELL_LIMIT,
            mt5.ORDER_TYPE_BUY_STOP,
            mt5.ORDER_TYPE_SELL_STOP,
            mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            mt5.ORDER_TYPE_SELL_STOP_LIMIT,
        ):
            raise ValueError(
                f"挂单 type 必须是 {sorted((mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT, mt5.ORDER_TYPE_SELL_STOP_LIMIT))} 之一，"
                f"当前: {self.type!r}"
            )

        # 4. price 挂单必填
        if self.price <= 0:
            raise ValueError(f"挂单 price 必须 > 0，当前: {self.price}")

        # 5. STOP_LIMIT 类型必须填 stoplimit
        if self.type in (mt5.ORDER_TYPE_BUY_STOP_LIMIT, mt5.ORDER_TYPE_SELL_STOP_LIMIT):
            if self.stoplimit <= 0:
                raise ValueError(
                    f"{self.type} 必须指定 stoplimit > 0，当前: {self.stoplimit}"
                )

        # 6. sl/tp 不能为负
        if self.sl < 0:
            raise ValueError(f"sl 不能为负，当前: {self.sl}")
        if self.tp < 0:
            raise ValueError(f"tp 不能为负，当前: {self.tp}")

        # 7. SL/TP 与方向的合理性
        if self.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT):
            if self.sl > 0 and self.sl >= self.price:
                raise ValueError(
                    f"BUY 挂单的 sl({self.sl}) 必须 < price({self.price})"
                )
            if self.tp > 0 and self.tp <= self.price:
                raise ValueError(
                    f"BUY 挂单的 tp({self.tp}) 必须 > price({self.price})"
                )
        elif self.type in (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_SELL_STOP_LIMIT):
            if self.sl > 0 and self.sl <= self.price:
                raise ValueError(
                    f"SELL 挂单的 sl({self.sl}) 必须 > price({self.price})"
                )
            if self.tp > 0 and self.tp >= self.price:
                raise ValueError(
                    f"SELL 挂单的 tp({self.tp}) 必须 < price({self.price})"
                )

        # 8. deviation
        if self.deviation < 0:
            raise ValueError("deviation 不能为负")

        # 9. magic
        if self.magic < 0:
            raise ValueError("magic 不能为负")

        # 10. type_filling / type_time
        if self.type_filling not in (
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
        ):
            raise ValueError(
                f"type_filling 必须是 {sorted((mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN))} 之一"
            )
        if self.type_time not in (
            mt5.ORDER_TIME_GTC,
            mt5.ORDER_TIME_DAY,
            mt5.ORDER_TIME_SPECIFIED,
            mt5.ORDER_TIME_SPECIFIED_DAY,
        ):
            raise ValueError(
                f"type_time 必须是 {sorted((mt5.ORDER_TIME_GTC, mt5.ORDER_TIME_DAY, mt5.ORDER_TIME_SPECIFIED, mt5.ORDER_TIME_SPECIFIED_DAY))} 之一"
            )

        # 11. expiration
        if self.expiration < 0:
            raise ValueError(f"expiration 不能为负，当前: {self.expiration}")
        # type_time is an int constant (mt5.ORDER_TIME_*), not a string —
        # the original {"SPECIFIED", "SPECIFIED_DAY"} string set could
        # never match an int and silently disabled this check.
        if (self.type_time in (mt5.ORDER_TIME_SPECIFIED, mt5.ORDER_TIME_SPECIFIED_DAY)
                and self.expiration <= 0):
            raise ValueError(
                f"type_time={self.type_time} 时必须指定 expiration > 0"
            )


    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False


    def to_mt5_request(self) -> dict[str, Any]:
        """转换为 MT5 挂单请求字典"""
        self.validate()

        req: dict[str, Any] = {
            "action":       self.action,
            "symbol":       self.symbol,
            "volume":       self.volume,
            "type":         self.type,
            "price":        self.price,
            "magic":        self.magic,
            "comment":      self.comment,
            "deviation":    self.deviation,
            "type_time":    self.type_time,
            "type_filling": self.type_filling,
        }

        # 可选字段，0 时不发送
        if self.sl > 0:
            req["sl"] = self.sl
        if self.tp > 0:
            req["tp"] = self.tp
        if self.stoplimit > 0:
            req["stoplimit"] = self.stoplimit
        if self.expiration > 0:
            req["expiration"] = self.expiration

        return req


class CancelOrder(msgspec.Struct, frozen=True, gc=False):
    """撤销挂单指令

    必填：
        - order: 挂单 ticket

    选填：
        - comment: 撤单备注
        - magic:   业务记录用，撤单本身不强制要求

    内建方法：
        - to_mt5_request: 转换为 mt5 请求字典
        - validate:     校验字段是否合法
        - is_valid:     校验字段是否合法

    便捷构造方法：
        - from_order:   从已有挂单对象构造撤单指令
        - from_ticket:  从 ticket 直接构造撤单指令
    """

    # === 必填字段 ===
    order: int

    # === 可选字段 ===
    comment: str = ""
    magic:   int = 0

    # === 写死字段 ===
    action: int = mt5.TRADE_ACTION_REMOVE

    # ---------- 校验 ----------
    def validate(self) -> None:
        if self.order <= 0:
            raise ValueError(
                f"order(挂单 ticket) 必须 > 0，当前: {self.order}"
            )

        if self.magic < 0:
            raise ValueError("magic 不能为负")

    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False


    def to_mt5_request(self) -> dict[str, Any]:
        """转换为 MT5 撤单请求字典"""
        self.validate()

        return {
            "action":  self.action,
            "order":   self.order,
            "comment": self.comment,
            "magic":   self.magic,
        }

    # ---------- 便捷构造 ----------
    @classmethod
    def from_order(
        cls,
        order,                       # mt5.TradeOrder 对象
        comment: str = "python script cancel",
    ) -> "CancelOrder":
        """从已有挂单对象构造撤单指令"""
        return cls(
            order=order.ticket,
            magic=order.magic,
            comment=comment,
        )

    @classmethod
    def from_ticket(
        cls,
        ticket: int,
        comment: str = "python script cancel",
    ) -> "CancelOrder":
        """从 ticket 直接构造撤单指令"""
        return cls(order=ticket, comment=comment)


class ModifyOrder(msgspec.Struct, frozen=True, gc=False):
    """修改挂单（价格、SL/TP、过期时间）

    对应 MT5 官方请求:
    request = {
        "action":       mt5.TRADE_ACTION_MODIFY,
        "order":        order_ticket,
        "price":        new_price,
        "sl":           new_sl,
        "tp":           new_tp,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    """

    # === 必填字段 ===
    order: int             # 要修改的挂单 ticket
    price: float           # 新的挂单价格（修改挂单时必填）

    # === 可选字段 ===
    sl: float = 0.0
    tp: float = 0.0

    # === 业务字段 ===
    magic:   int = 0
    comment: str = ""

    # === 写死字段 ===
    action:       int = mt5.TRADE_ACTION_MODIFY
    type_time:    int = mt5.ORDER_TIME_GTC
    type_filling: int = mt5.ORDER_FILLING_RETURN


    # ---------- 校验 ----------
    def validate(self) -> None:
        if self.order <= 0:
            raise ValueError(
                f"order(挂单 ticket) 必须 > 0，当前: {self.order}"
            )

        if self.price <= 0:
            raise ValueError(
                f"修改挂单时 price 必须 > 0，当前: {self.price}"
            )

        if self.sl < 0:
            raise ValueError("sl 不能为负")

        if self.tp < 0:
            raise ValueError("tp 不能为负")

        if self.type_filling not in (
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
        ):
            raise ValueError(
                f"type_filling 必须是 {sorted((mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN))} 之一"
            )

        if self.type_time not in (
            mt5.ORDER_TIME_GTC,
            mt5.ORDER_TIME_DAY,
            mt5.ORDER_TIME_SPECIFIED,
            mt5.ORDER_TIME_SPECIFIED_DAY,
        ):
            raise ValueError(
                f"type_time 必须是 {sorted((mt5.ORDER_TIME_GTC, mt5.ORDER_TIME_DAY, mt5.ORDER_TIME_SPECIFIED, mt5.ORDER_TIME_SPECIFIED_DAY))} 之一"
            )

    def is_valid(self) -> bool:
        try:
            self.validate()
            return True
        except ValueError:
            return False

    # ---------- 转换 ----------
    def to_dict(self) -> dict[str, Any]:
        return msgspec.to_builtins(self)

    def to_mt5_request(self) -> dict[str, Any]:
        self.validate()

        req: dict[str, Any] = {
            "action":       self.action,
            "order":        self.order,
            "price":        self.price,
            "sl":           self.sl,
            "tp":           self.tp,
            "magic":        self.magic,
            "comment":      self.comment,
            "type_time":    self.type_time,
            "type_filling": self.type_filling,
        }
        return req

    def to_json(self) -> bytes:
        return msgspec.json.encode(self)

    @classmethod
    def from_order(
        cls,
        order,                       # mt5.TradeOrder
        price: float | None = None,
        sl:    float | None = None,
        tp:    float | None = None,
        comment: str = "python script modify order",
    ) -> "ModifyOrder":
        """从已有挂单构造修改指令"""
        return cls(
            order=order.ticket,
            price=price if price is not None else order.price_open,
            sl=sl if sl is not None else order.sl,
            tp=tp if tp is not None else order.tp,
            magic=order.magic,
            comment=comment,
        )
