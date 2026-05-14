# mt5-bridge

> 事件驱动的 **MetaTrader 5** 桥接包 —— 把 MT5 终端的实时行情 / 账户 / 持仓 / 挂单事件
> 通过 pykka actor 模型派发给 Python 订阅者，命令侧 fire-and-forget。

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-v0.1.0-orange)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-82%20passed-brightgreen)](#测试)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](#)

## 特点

- **Pykka ThreadingActor** 解耦 IO / 命令 / 派发，关停期 defensive `tell` 防 dead-actor race
- **Twisted + msgspec** 内置 EA TCP 接收（**可选**，配置开关；不启用时不导入 Twisted）
- **Fire-and-forget** 交易命令，结果通过 `OutputEvent` 同步回调返回
- **7 类 OutputEvent**：仓位 / 挂单的开仓 / 修改 / 平仓 / 撤单 + 紧急 Tick 心跳
- 内置 **Retry + 指数退避**（可配 retcode 白名单）、**Tick Watchdog**（per-symbol 心跳超时拒新仓 + 广播紧急事件）、**Kelly 仓位计算**（half-Kelly 默认）
- **启动 preflight**：AutoTrading 关 / 账号锁定 / EA 不允许 → 启动直接 fail-fast，不等到第一笔单才报错

## 目录

```
mt5-bridge/
├── LICENSE
├── CHANGELOG.md
├── README.md
├── pyproject.toml
├── config.example.toml            # 配置模板（账号留空，入仓）
├── config.toml                    # 你的运行时配置（gitignored，需手动 cp + 编辑）
├── ea/                            # MT5 EA 源码 + 编译产物
│   ├── Cube.mq5
│   ├── Cube.ex5
│   └── README.md
├── examples/
│   ├── run_bridge.py              # 长跑订阅 demo（订阅所有事件类型）
│   ├── open_close_3.py            # 连开 3 仓 → 反向平 3 仓
│   └── place_cancel_order.py      # 挂 BUY_LIMIT → 撤单
├── src/mt5_bridge/
│   ├── facade.py                  # MT5Bridge
│   ├── actors/                    # dispatcher / executor / tick_watchdog / kelly_actor / ea_receiver
│   ├── configs/                   # mt5 / bridge / logging
│   ├── contracts/                 # ea_messages / output_events / trade_commands / enums
│   └── infra/                     # mt5_client / scheduler / logging / rpc(可选)
└── tests/                         # 82 个 pytest 用例
```

## 安装

```bash
# 最小核心（不含 Twisted；用户自己解析后通过 bridge.feed_input 喂入）
pip install -e .

# 含内置 Twisted RPC 接收（推荐，与 EA 直接对接）
pip install -e .[rpc]

# 开发 / 跑测试（[rpc] + pytest）
pip install -e .[dev]
```

依赖：Python 3.12+、`MetaTrader5` 5.0+（Windows-only wheel）、`pykka` 4+、`msgspec` 0.21+、`structlog` 25+、可选 `twisted` 24+。

## 快速开始

### 1. 配置

```bash
cp config.example.toml config.toml
# 编辑 config.toml：填入 [mt5] account/password/server，
# 确认 [[rpc.endpoints]].port 与 EA 输入参数 Port 一致
```

### 2. 启 EA

详见 [`ea/README.md`](ea/README.md)。要点：把 `Cube.ex5` 拷到 `MQL5/Experts/`，挂图表，`Port` 与 config 对齐，按下 `AutoTrading` 按钮（绿灯）。

### 3. 跑 bridge

```python
from mt5_bridge import MT5Bridge, EventType, Tick, OpenPosition
import MetaTrader5 as mt5

bridge = MT5Bridge("config.toml")
bridge.subscribe(EventType.POSITION_OPENED, lambda e: print(f"opened {e.ticket}"))
bridge.subscribe(EventType.POSITION_CLOSED, lambda e: print(f"closed {e.ticket} profit={e.profit}"))
bridge.subscribe(Tick, lambda t: ...)

bridge.start_io()

bridge.open_position(OpenPosition(
    symbol="XAUUSD", volume=0.01, type=mt5.ORDER_TYPE_BUY,
    price=0.0, sl=0.0, tp=0.0,
))

bridge.serve_forever()    # 阻塞主线程；Ctrl+C 触发 bridge.stop()
```

## 两种部署模式

### A) 内置 RPC（reactor 跑主线程）

`config.toml` 设 `[rpc] enabled = true`。bridge 启 Twisted listener，EA 推过来。**`serve_forever()` 必须在主线程**调用（reactor 要求）。

### B) 外部喂数据，不启用 RPC

`config.toml` 设 `[rpc] enabled = false`。bridge 不导入 twisted，用户自己接 EA / 文件 / 别的源，把解析好的 `EAMessage` 实例通过 `feed_input(msg)` 推进来。

```python
bridge = MT5Bridge("config.toml")
bridge.subscribe(EventType.POSITION_OPENED, on_opened)
bridge.start_io()

while True:
    msg = my_external_source.recv()    # any EAMessage subclass
    bridge.feed_input(msg)
```

## 订阅 API

| 调用 | 触发条件 |
|---|---|
| `subscribe(EventType.POSITION_OPENED, cb)` | 按 `EventType` 枚举值订阅 OutputEvent |
| `subscribe(Tick, cb)`                       | 按消息 / 事件**类**订阅（`Tick` / `Account` / `Bar` / `Connected` / `EmergencyTickStale` / `OutputEvent` 子类等） |
| `subscribe(TradeStateChanged, cb)`          | 订阅执行器的内部状态流（`POSITION_OPENING` / `OPENED` / `CLOSING` / 等等），demo 里用它来拿 ticket（不依赖 EA push） |
| `subscribe_all(cb)`                          | 接收所有 broadcast 事件（一个回调吃天下，调试 / 录制用） |

订阅可在 `start_io()` 前后调用。`start_io()` 之前注册的会被缓冲，启动时一次性 flush。
回调**同步**在 dispatcher 线程里执行 —— 保持快、不抛异常（异常会被 try/except 包住但单回调失败不影响其他）。

## OutputEvent 速查

| 事件 | EventType | 关键字段 |
|---|---|---|
| `PositionOpened`  | `11` POSITION_OPENED   | `ticket, symbol, pos_type, volume, open_price, sl, tp, magic, comment, event_time_ms` |
| `PositionClosed`  | `12` POSITION_CLOSED   | `ticket, symbol, pos_type, volume, open_price, close_price, profit, magic, close_time_ms, event_time_ms` |
| `PositionModified`| `13` POSITION_MODIFIED | `ticket, symbol, sl, tp, event_time_ms` |
| `OrderPlaced`     | `21` ORDER_PLACED      | `ticket, symbol, order_type, volume, open_price, sl, tp, event_time_ms` |
| `OrderModified`   | `22` ORDER_MODIFIED    | `ticket, symbol, open_price, sl, tp, event_time_ms` |
| `OrderCanceled`   | `23` ORDER_CANCELED    | `ticket, symbol, order_type, volume, open_price, sl, tp, magic, reason, removed_time_ms, event_time_ms` |
| `EmergencyTickStale` | —                   | `symbol, last_tick_time_ms, silence_seconds, detected_at_ms` |

`event_time_ms` 是 bridge 检测时间；`close_time_ms` / `removed_time_ms` 是 broker 端真实成交 / 撤单时间。`reason` ∈ `{canceled, filled, expired, unknown}`。

## 命令 API

每个都是 fire-and-forget，结果通过 `TradeStateChanged`（内部）+ EA 推回的 OutputEvent（外部）观察：

| 方法 | 入参 |
|---|---|
| `bridge.open_position(OpenPosition(...))` | `symbol, volume, type, price=0, sl=0, tp=0, ...` |
| `bridge.close_position(ClosePosition(...))` | `symbol, volume, type, position, price=0` |
| `bridge.modify_position(ModifyPosition(...))` | `symbol, position, sl=0, tp=0` |
| `bridge.open_order(OpenOrder(...))` | `symbol, volume, type, price, sl=0, tp=0, stoplimit=0, expiration=0` |
| `bridge.modify_order(ModifyOrder(...))` | `order, price, sl=0, tp=0` |
| `bridge.cancel_order(CancelOrder(...))` | `order` |

Trade command 内置 `validate()`，bridge 会在执行前调用，校验失败 → 直接 emit 对应的 `*_FAILED` `TradeStateChanged`，不打 broker。

## Kelly 仓位建议

```python
v = bridge.kelly.suggest_volume(
    symbol="XAUUSD",
    win_prob=0.55,
    win_loss_ratio=1.5,
    stop_loss_pips=200,
    fraction=0.5,        # 可选；缺省取 [kelly] default_fraction
).get()                  # pykka proxy → Future.get()
```

公式 `f* = max(0, p - (1-p)/b)`；结果再乘 `fraction`，转金额 → 手数：
`手数 = floor(equity * f_used / (stop_loss_pips * tick_value / tick_size) / volume_step) * volume_step`，再 clamp 到 `[min_volume, max_volume]`。

要求：bridge 必须收到过 `Account` 推送（EA 模式自动来；`feed_input` 模式下需手动注入）。

## 配置参考

完整字段在 [`config.example.toml`](config.example.toml)。关键开关：

| 段 | 字段 | 含义 |
|---|---|---|
| `[mt5]` | `account` / `password` / `server` / `mt5_local_path` | 留空 = 复用终端当前账号 / 自动探测路径 |
| `[rpc]` | `enabled` | `false` 时不导入 twisted；用户走 `feed_input` |
| `[rpc.endpoints]` | `host` / `port` | **必须与 EA 输入参数 `Port` 对齐** |
| `[retry]` | `retryable_retcodes` | 严格白名单（默认仅 REQUOTE / REJECT / TIMEOUT / PRICE_CHANGED / PRICE_OFF / TOO_MANY_REQUESTS / CONNECTION） |
| `[watchdog]` | `tick_timeout_seconds` | 超时则 EmergencyTickStale + 拒绝该 symbol 新仓 |
| `[kelly]` | `default_fraction` | 0.5 = half-Kelly（行业常规） |

## Examples

| 文件 | 干嘛 |
|---|---|
| `examples/run_bridge.py` | 长跑订阅 demo —— 订阅所有 OutputEvent + Tick + Account + Connected。开 bridge → 你在 MT5 手动开/平仓，看输出 |
| `examples/open_close_3.py` | 连开 3 个 BUY 市价仓 → 等收齐 OPENED → 反向平掉 |
| `examples/place_cancel_order.py` | 挂一个远离市价的 BUY_LIMIT → 等 PLACED → 撤掉 |

跑法：
```bash
python examples/run_bridge.py config.toml
python examples/open_close_3.py config.toml XAUUSD 0.01
python examples/place_cancel_order.py config.toml XAUUSD 0.01
```

## 测试

```bash
pip install -e .[dev]
pytest
```

82 个测试覆盖：dispatcher 状态机（含 close detection + dead-actor race）、executor 重试 + stale 拒单、watchdog 心跳、Kelly 公式 + 边界、RPC 协议解码、facade 端到端（rpc on/off）、ea_messages DECODER round-trip、MT5Client preflight 失败模式、trade_commands 校验回归。

## 故障排查

| 症状 | 多半原因 |
|---|---|
| 启动报 `AutoTrading is DISABLED` | MT5 工具栏 AutoTrading 按钮没按下；按下（变绿）再启 |
| retcode `10027 CLIENT_DISABLES_AT` | 同上 —— 启动 preflight 应该提前堵住，但运行中关掉 AutoTrading 也会触发 |
| retcode `10014 Invalid volume` | volume 不是 broker 接受的步进 / 超出 min/max；`mt5.symbol_info(...).volume_min/_step/_max` 查 |
| retcode `10019 No money` | 自由保证金不够，硬错误，不重试 |
| retcode `10021 PRICE_OFF` | 没行情（周末 / 假期），或 symbol 没在 Market Watch；`mt5.symbol_select(symbol, True)` |
| EA 接不上：bridge 没 `[EA] connected` | EA `Port` 与 `[[rpc.endpoints]].port` 没对齐；改一边即可 |
| `ActorDeadError` 关停时 | v0.1.0 已加 defensive `tell`；如果还出现，提 issue |

## License

[MIT](LICENSE) © 2026 Tonny.Bao
