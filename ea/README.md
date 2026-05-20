# CubeAll.mq5 — MT5 Expert Advisor

实时 TCP 推送 EA。把 MT5 终端里的 Tick / Bar / Cube / MetaCube / Account /
Position / Order 事件流推给 mt5-bridge。

当前版本：**v1.10**（在 v6.06 Cube.mq5 基础上合并了 Cube/MetaCube 计算 + 推送）。

> **注意**：仓里只随包发布编译产物 `CubeAll.ex5`，源码 `CubeAll.mq5` 由 EA 作者
> 单独保管。当 bridge 升级到 v0.3.0 之后，`CubeAll.ex5` 由作者另行提供放入本目录。

## 安装

1. 把 `CubeAll.ex5` 拷到 MT5 数据目录的 `MQL5/Experts/`（建议放子目录，
   例如 `MQL5/Experts/TonnyEA/CubeAll.ex5`）
2. 在 MT5 终端 Navigator 里右键 `Refresh`，找到 `CubeAll` → 拖到目标品种图表
3. 在弹出的属性对话框 **Inputs** 标签里检查：
   - `Host` —— bridge 地址（默认 `127.0.0.1`）
   - `Port` —— **必须与 `config.toml` 里 `[[rpc.endpoints]].port` 完全一致**
   - `ExecutionTF` / `TacticalTF` / `StrategicTF` —— 三周期 K 线（默认 `M5`/`H1`/`D1`）
   - `InpEnableCubeCompute` / `InpEnableMetaCompute` —— 是否启用 Cube / MetaCube 计算
4. 工具栏 `AutoTrading` 按钮要按下（图标变绿），否则 EA 推送照常但 bridge 端
   `assert_trade_ready` 会在启动时报错拒绝下单

> **常见坑**：bridge 没收到任何 `[TICK]` / `[EA] connected` 一般是 Port 没对齐。
> EA 默认 Port=9991，bridge `config.example.toml` 默认 5500——挑一边改成另一边的值。

## 协议（与 Python 侧 `contracts/ea_messages.py` 对齐）

每条消息一行 JSON，以 `\n` 结尾。每条都带 `"type"` 判别字段。

### 连接 / Tick / Bar

| type                | 说明 |
|---------------------|------|
| `connected`         | 建连握手（symbol + EA 时间） |
| `tick`              | 实时 Tick |
| `history_tick`      | 启动时回放的历史 Tick |
| `history_tick_done` | 历史 Tick 回放完成尾标 |
| `bar`               | 实时 Bar 快照（含未完成 / 已完成） |
| `history_bar`       | 启动时回放的历史 Bar |
| `history_bar_done`  | 历史 Bar 回放完成尾标 |

### 账户 / 持仓 / 挂单

| type                | 说明 |
|---------------------|------|
| `account`           | 账户快照 |
| `position`          | 单仓位快照（每仓位一条；含 `magic` / `comment`） |
| `order`             | 单挂单快照（每挂单一条；含 `magic`） |
| `position_closed`   | 仓位消失——`HistorySelect` 找 OUT-deal 拿真实 `close_price` / `profit` / `close_time` |
| `order_removed`     | 挂单消失——`HistoryOrderSelect` 拿 `reason` ∈ {canceled, filled, expired, unknown} 与 `removed_time` |

### Cube / MetaCube（v1.00+，CubeAll 新增）

| type                      | 说明 |
|---------------------------|------|
| **`cube`**                | 实时 Cube（含 forming / 已收线；`is_closed` 字段区分） |
| **`history_cube`**        | 启动时回放的历史 Cube（`is_closed=true` 恒为真） |
| **`history_cube_done`**   | 历史 Cube 回放完成尾标（按通道一条） |
| **`meta_cube`**           | 实时 MetaCube（cube 的更高层组合；同样含 forming / 已收线） |
| **`history_meta_cube`**   | 启动时回放的历史 MetaCube |
| **`history_meta_cube_done`** | 历史 MetaCube 回放完成尾标（按通道一条） |

`dir` 取值：`"UP"` / `"DOWN"` / `"RANGE"` / `"NONE"`
`state` 取值：`"FORMING"` / `"ACTIVE"` / `"AT_RISK"` / `"DEAD"`

Python 侧 v0.3.0 起，Cube / MetaCube 在 `is_closed=true` 时还会派发
`CubeClosed` / `MetaCubeClosed` OutputEvent（`EventType.CUBE_CLOSED = 41` /
`META_CUBE_CLOSED = 42`），便于只关心"收线时刻"的下游订阅。

`position_closed` / `order_removed` 由 EA 在自己每轮 `PushPositions` /
`PushOrders` 跑差异检测时主动发出（上一次发过的 ticket 本次集合里没了 → 推消息）。
即使 bridge 在断网期间漏掉过中间状态，EA 重连后第一次 `PushSnapshot` 也会
补上对应的关闭事件。

## 配置参数（节选）

| 参数 | 默认 | 用途 |
|---|---|---|
| `Host` / `Port` | `127.0.0.1` / `9991` | bridge 监听地址 |
| `ReconnectSec` | `5` | 断线重连间隔 |
| `EnableTick` | `true` | 推送实时 Tick |
| `ExecutionTF` / `TacticalTF` / `StrategicTF` | `M5` / `H1` / `D1` | 三周期 K 线推送（空字符串 = 关该周期） |
| `PositionIntervalSec` / `OrderIntervalSec` | `5` / `5` | 持仓 / 挂单的固定推送间隔。**这也是 close detection 的最大延迟** |
| `EnableHistory` / `HistoryCount` | `true` / `500` | 重连时回放的历史 Bar 数量 |
| `EnableHistoryTick` / `HistoryTickCount` | `true` / `10000` | 重连时回放的历史 Tick 数量 |
| `InpEnableCubeCompute` | `true` | 启用 Cube 引擎并推送 cube / history_cube |
| `InpEnableMetaCompute` | `true` | 启用 MetaCube 引擎并推送 meta_cube / history_meta_cube |
| `InpMaxHistoryBars` | `2000` | Cube 引擎回看 Bar 数（启动时初始化用） |
| `InpMaxCubes` / `InpMaxMetaCubes` | `500` / `200` | 每通道保留的 Cube / MetaCube 上限（超出丢弃最旧） |
