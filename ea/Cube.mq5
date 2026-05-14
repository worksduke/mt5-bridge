//+------------------------------------------------------------------+
//|                                              Cube.mq5            |
//|                        Real-time Data Push via Socket            |
//+------------------------------------------------------------------+
#property copyright "CubeDataPushEA"
#property version   "6.06"
#property strict

//--- 输入参数
input string   Host              = "127.0.0.1";  // 服务器地址
input int      Port              = 9991;          // 服务器端口
input int      ReconnectSec      = 5;             // 断线重连间隔(秒)

input group "=== Tick ==="
input bool     EnableTick        = true;          // 推送Tick

input group "=== 执行周期 (Execution) ==="
input string   ExecutionTF       = "M5";          // 执行周期 (M1/M5/M15/M30/H1/H4/D1/W1/MN1, 空=不推送)
input int      ExecutionIntervalSec = 5;          // 执行周期：固定推送间隔(秒)
input bool     ExecutionPushOnTick  = false;      // 执行周期：随Tick推送Bar(忽略间隔)

input group "=== 战术周期 (Tactical) ==="
input string   TacticalTF        = "H1";          // 战术周期 (空=不推送)
input int      TacticalIntervalSec = 30;          // 战术周期：固定推送间隔(秒)
input bool     TacticalPushOnTick   = false;      // 战术周期：随Tick推送Bar(忽略间隔)

input group "=== 战略周期 (Strategic) ==="
input string   StrategicTF       = "D1";          // 战略周期 (空=不推送)
input int      StrategicIntervalSec = 60;         // 战略周期：固定推送间隔(秒)
input bool     StrategicPushOnTick  = false;      // 战略周期：随Tick推送Bar(忽略间隔)

input group "=== 账户/持仓/挂单 ==="
input int      AccountIntervalSec  = 5;           // 账户：固定推送间隔(秒)
input bool     AccountPushOnTick   = false;       // 账户：随Tick推送(忽略间隔)
input int      PositionIntervalSec = 5;           // 持仓：固定推送间隔(秒)
input bool     PositionPushOnTick  = false;       // 持仓：随Tick推送(忽略间隔)
input int      OrderIntervalSec    = 5;           // 挂单：固定推送间隔(秒)
input bool     OrderPushOnTick     = false;       // 挂单：随Tick推送(忽略间隔)

input group "=== 历史K线 ==="
input bool     EnableHistory     = true;          // 连接后推送历史Bar
input int      HistoryCount      = 500;           // 每周期推送历史Bar数量(不含当前Bar)

input group "=== 历史Tick ==="
input bool     EnableHistoryTick = true;          // 连接后推送历史Tick
input int      HistoryTickCount  = 10000;          // 推送历史Tick数量
input string   HistoryTickType   = "ALL";         // Tick类型(ALL/INFO/TRADE)

//+------------------------------------------------------------------+
//| 周期配置结构                                                       |
//+------------------------------------------------------------------+
struct TFConfig
{
   ENUM_TIMEFRAMES tf;
   string          role;         // "execution" | "tactical" | "strategic"
   string          period_str;   // "M5" | "H1" | "D1" 等，保留原始周期信息
   int             intervalSec;
   datetime        lastBarTime;
   datetime        lastPushTime;
   bool            enabled;
   bool            pushOnTick;   // 是否随Tick推送
   //--- 去重用：记录上次推送的未完成Bar的OHLC
   double          lastOpen;
   double          lastHigh;
   double          lastLow;
   double          lastClose;
};

//+------------------------------------------------------------------+
//| 仓位 / 挂单快照（用于检测"消失"事件并补推 close/remove 消息）       |
//+------------------------------------------------------------------+
struct PositionSnap
{
   ulong  ticket;
   string symbol;
   string pos_type;
   double volume;
   double open_price;
   double cur_price;     // 留作 close_price 兜底
   double sl;
   double tp;
   double profit;
   long   magic;
   string comment;
};

struct OrderSnap
{
   ulong  ticket;
   string symbol;
   string order_type;
   double volume;
   double open_price;
   double sl;
   double tp;
   long   magic;
   string comment;
};

//--- 全局变量
int       g_socket       = INVALID_HANDLE;
datetime  g_lastConnect  = 0;
TFConfig  g_tfs[3];
string    g_symbol;

datetime  g_lastAccountPush  = 0;
datetime  g_lastPositionPush = 0;
datetime  g_lastOrderPush    = 0;

datetime  g_startTime = 0;

//--- 去重指纹（仅 PushOnTick 模式下使用）
string g_lastAccountFp  = "";
string g_lastPositionFp = "";
string g_lastOrderFp    = "";

//--- 上次推送时的 ticket→snapshot 表（用于检测消失事件）
PositionSnap g_lastPositionSnaps[];
OrderSnap    g_lastOrderSnaps[];

//+------------------------------------------------------------------+
//| 字符串转周期枚举                                                   |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StrToTF(string s)
{
   if(s == "M1")  return PERIOD_M1;
   if(s == "M3")  return PERIOD_M3;
   if(s == "M5")  return PERIOD_M5;
   if(s == "M15") return PERIOD_M15;
   if(s == "M30") return PERIOD_M30;
   if(s == "H1")  return PERIOD_H1;
   if(s == "H4")  return PERIOD_H4;
   if(s == "D1")  return PERIOD_D1;
   if(s == "W1")  return PERIOD_W1;
   if(s == "MN1") return PERIOD_MN1;
   return (ENUM_TIMEFRAMES)-1;
}


//+------------------------------------------------------------------+
//| Socket连接                                                         |
//+------------------------------------------------------------------+
bool TryConnect()
{
   if(g_socket != INVALID_HANDLE) { SocketClose(g_socket); g_socket = INVALID_HANDLE; }
   g_socket = SocketCreate();
   if(g_socket == INVALID_HANDLE) return false;
   if(!SocketConnect(g_socket, Host, Port, 3000))
   {
      SocketClose(g_socket); g_socket = INVALID_HANDLE;
      return false;
   }
   Print("Socket connected → ", Host, ":", Port);
   return true;
}

//+------------------------------------------------------------------+
//| 发送消息（\n分隔）                                                  |
//+------------------------------------------------------------------+
bool SendMsg(const string &msg)
{
   if(g_socket == INVALID_HANDLE) return false;
   string line = msg + "\n";
   uchar  buf[];
   int    len = StringLen(line);
   ArrayResize(buf, len);
   StringToCharArray(line, buf, 0, len, CP_UTF8);
   if(SocketSend(g_socket, buf, len) < 0)
   {
      Print("Send failed, will reconnect.");
      SocketClose(g_socket); g_socket = INVALID_HANDLE;
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| JSON构建辅助                                                       |
//+------------------------------------------------------------------+
string JQ(string k, string v, bool last=false)
{
   return "\"" + k + "\":\"" + v + "\"" + (last ? "" : ",");
}

string JN(string k, string v, bool last=false)
{
   return "\"" + k + "\":" + v + (last ? "" : ",");
}

string JB(string k, bool v, bool last=false)
{
   return "\"" + k + "\":" + (v ? "true" : "false") + (last ? "" : ",");
}

//+------------------------------------------------------------------+
//| 推送 connected 信号                                                |
//+------------------------------------------------------------------+
void PushConnected()
{
   string msg = "{";
   msg += JQ("type",   "connected");
   msg += JQ("symbol", g_symbol);
   msg += JN("time",   IntegerToString(TimeCurrent()), true);
   msg += "}";
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| 推送单根Bar                                                        |
//| isClosed: true=已完成bar  false=未完成bar(实时更新)                |
//+------------------------------------------------------------------+
void PushBarMsg(const MqlRates &r, string role, string period_str,
                string type, bool isClosed)
{
   string msg = "{";
   msg += JQ("type",      type);
   msg += JQ("symbol",    g_symbol);
   msg += JQ("role",      role);
   msg += JQ("tf_period", period_str);
   msg += JQ("time",      TimeToString(r.time, TIME_DATE|TIME_SECONDS));
   msg += JN("time_msc",  IntegerToString((long)r.time * 1000));
   msg += JB("is_closed",  isClosed);          // ← 语义明确：已完成 or 未完成
   msg += JN("open",      DoubleToString(r.open,  _Digits));
   msg += JN("high",      DoubleToString(r.high,  _Digits));
   msg += JN("low",       DoubleToString(r.low,   _Digits));
   msg += JN("close",     DoubleToString(r.close, _Digits));
   msg += JN("volume",    IntegerToString(r.tick_volume), true);
   msg += "}";
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| 推送已完成Bar（从 shift=1 取前一根确定完成的bar）                  |
//+------------------------------------------------------------------+
void PushClosedBar(int idx)
{
   MqlRates rates[];
   if(CopyRates(g_symbol, g_tfs[idx].tf, 1, 1, rates) < 1) return;
   PushBarMsg(rates[0], g_tfs[idx].role, g_tfs[idx].period_str, "bar", true);
}

//+------------------------------------------------------------------+
//| 推送当前未完成Bar（shift=0）                                       |
//| forcePush: true=强制推送(忽略去重)  false=启用去重               |
//| 返回值: true=实际推送了  false=被去重跳过                         |
//+------------------------------------------------------------------+
bool PushUnclosedBar(int idx, bool forcePush=false)
{
   MqlRates rates[];
   if(CopyRates(g_symbol, g_tfs[idx].tf, 0, 1, rates) < 1) return false;
   
   //--- 去重检查（仅在非强制模式下生效）
   if(!forcePush)
   {
      if(rates[0].open  == g_tfs[idx].lastOpen  &&
         rates[0].high  == g_tfs[idx].lastHigh  &&
         rates[0].low   == g_tfs[idx].lastLow   &&
         rates[0].close == g_tfs[idx].lastClose)
      {
         return false;  // OHLC 未变化，跳过推送
      }
   }
   
   //--- 更新去重缓存
   g_tfs[idx].lastOpen  = rates[0].open;
   g_tfs[idx].lastHigh  = rates[0].high;
   g_tfs[idx].lastLow   = rates[0].low;
   g_tfs[idx].lastClose = rates[0].close;
   
   
   PushBarMsg(rates[0], g_tfs[idx].role, g_tfs[idx].period_str, "bar", false);
   return true;
}

//+------------------------------------------------------------------+
//| 推送历史Bar（type="history_bar"，从旧到新，不含当前Bar）           |
//+------------------------------------------------------------------+
void PushHistoryBars(int idx)
{
    if(!EnableHistory || HistoryCount <= 0) return;

    MqlRates rates[];
    int copied = CopyRates(g_symbol, g_tfs[idx].tf, 1, HistoryCount, rates);
    if(copied < 1) return;

    for(int i = 0; i < copied; i++)
        PushBarMsg(rates[i], g_tfs[idx].role, g_tfs[idx].period_str,
                   "history_bar", true);  // 历史bar一定是closed
}

//+------------------------------------------------------------------+
//| 推送 history_bar_done                                              |
//+------------------------------------------------------------------+
void PushHistoryDone(int idx)
{
    string msg = "{";
    msg += JQ("type",   "history_bar_done");
    msg += JQ("symbol", g_symbol);
    msg += JQ("role",      g_tfs[idx].role);
    msg += JQ("tf_period", g_tfs[idx].period_str, true);
    msg += "}";
    SendMsg(msg);
}

//+------------------------------------------------------------------+
//| 推送历史 Tick                                                    |
//+------------------------------------------------------------------+

uint StrToTickType(string s)
{
   if(s == "INFO")  return COPY_TICKS_INFO;
   if(s == "TRADE") return COPY_TICKS_TRADE;
   return COPY_TICKS_ALL;  // 默认ALL
} 

//+------------------------------------------------------------------+
//| 推送单条历史Tick                                                 |
//+------------------------------------------------------------------+
void PushHistoryTickMsg(const MqlTick &tick)
{
   string msg = "{";
   msg += JQ("type",     "history_tick");
   msg += JQ("symbol",   g_symbol);
   msg += JQ("time",     TimeToString(tick.time, TIME_DATE|TIME_SECONDS));
   msg += JN("time_msc", IntegerToString(tick.time_msc));
   msg += JN("bid",      DoubleToString(tick.bid,  _Digits));
   msg += JN("ask",      DoubleToString(tick.ask,  _Digits));
   msg += JN("last",     DoubleToString(tick.last, _Digits));
   msg += JN("volume",   IntegerToString(tick.volume));
   msg += JN("flags",    IntegerToString(tick.flags), true);
   msg += "}";
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| 推送历史Tick（从旧到新）                                         |
//+------------------------------------------------------------------+
void PushHistoryTicks()
{
   if(!EnableHistoryTick || HistoryTickCount <= 0) return;

   MqlTick ticks[];
   uint tickType = StrToTickType(HistoryTickType);

   //--- CopyTicks 从最新往回取指定数量
   int copied = CopyTicks(g_symbol, ticks, COPY_TICKS_ALL, 0,
                           HistoryTickCount);

   if(copied < 1)
   {
      Print("CopyTicks failed or no data, copied=", copied);
      return;
   }

   Print("Pushing ", copied, " history ticks (type=", HistoryTickType,
         ")..."); 

   //--- 按时间从旧到新推送（CopyTicks返回的数据已按时间升序排列）
   for(int i = 0; i < copied; i++)
   {
      //--- 如果用户只需要特定类型，进行过滤
      if(tickType == COPY_TICKS_INFO)
      {
         if((ticks[i].flags & (TICK_FLAG_BID|TICK_FLAG_ASK)) == 0)
            continue;
      }
      else if(tickType == COPY_TICKS_TRADE)
      {
         if((ticks[i].flags & (TICK_FLAG_LAST|TICK_FLAG_VOLUME)) == 0)
            continue;
      }
      PushHistoryTickMsg(ticks[i]);
   }

   Print("History ticks push completed: ", copied, " ticks sent.");
}

//+------------------------------------------------------------------+
//| 推送 history_tick_done                                           |
//+------------------------------------------------------------------+
void PushHistoryTickDone()
{
   string msg = "{";
   msg += JQ("type",   "history_tick_done");
   msg += JQ("symbol", g_symbol);
   msg += JN("count",  IntegerToString(HistoryTickCount), true);
   msg += "}";
   SendMsg(msg);
}                                        



//+------------------------------------------------------------------+
//| 推送实时 Tick                                                     |
//+------------------------------------------------------------------+
void PushTick(const MqlTick &tick)
{
   string msg = "{";
   msg += JQ("type",     "tick");
   msg += JQ("symbol",   g_symbol);
   msg += JQ("time",     TimeToString(tick.time, TIME_DATE|TIME_SECONDS));
   msg += JN("time_msc", IntegerToString(tick.time_msc));
   msg += JN("bid",      DoubleToString(tick.bid,  _Digits));
   msg += JN("ask",      DoubleToString(tick.ask,  _Digits));
   msg += JN("last",     DoubleToString(tick.last, _Digits));
   msg += JN("volume",   IntegerToString(tick.volume), true);
   msg += "}";
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| 推送 Account（带去重）                                            |
//| forcePush=true 时忽略去重（用于间隔心跳/快照/OnTrade）            |
//+------------------------------------------------------------------+
bool PushAccount(bool forcePush=true)
{
   //--- 构造指纹（关键字段拼接）
   string fp = DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),    2) + "|" +
               DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),     2) + "|" +
               DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),     2) + "|" +
               DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2) + "|" +
               DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),     2);

   if(!forcePush && fp == g_lastAccountFp) return false;
   g_lastAccountFp = fp;

   string msg = "{";
   msg += JQ("type",       "account");
   msg += JQ("login",       IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   msg += JQ("currency",    AccountInfoString(ACCOUNT_CURRENCY));
   msg += JN("balance",     DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),    2));
   msg += JN("equity",      DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),     2));
   msg += JN("margin",      DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),     2));
   msg += JN("free_margin", DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2));
   msg += JN("profit",      DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),     2), true);
   msg += "}";
   SendMsg(msg);
   return true;
}

//+------------------------------------------------------------------+
//| 推送 position_closed（已平仓位）                                  |
//| 优先从 MT5 历史 deal 取真实 close_price/profit/close_time；       |
//| 找不到对应 OUT-deal 时退化为 snap 缓存的 cur_price/profit。       |
//+------------------------------------------------------------------+
void PushPositionClosed(const PositionSnap &snap)
{
   double actualClose  = snap.cur_price;
   double actualProfit = snap.profit;
   long   closeTime    = (long)TimeCurrent();

   datetime fromT = g_startTime - 86400;   // 多回看 1 天容错
   datetime toT   = TimeCurrent() + 1;
   if(HistorySelect(fromT, toT))
   {
      int totalDeals = HistoryDealsTotal();
      // 倒序找最新的 OUT-deal
      for(int i = totalDeals - 1; i >= 0; i--)
      {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(dealTicket == 0) continue;
         if((ulong)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID) != snap.ticket) continue;
         if(HistoryDealGetInteger(dealTicket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
         actualClose  = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
         actualProfit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
         closeTime    = (long)HistoryDealGetInteger(dealTicket, DEAL_TIME);
         break;
      }
   }

   string msg = "{";
   msg += JQ("type",        "position_closed");
   msg += JN("ticket",      IntegerToString(snap.ticket));
   msg += JQ("symbol",      snap.symbol);
   msg += JQ("pos_type",    snap.pos_type);
   msg += JN("volume",      DoubleToString(snap.volume,     2));
   msg += JN("open_price",  DoubleToString(snap.open_price, _Digits));
   msg += JN("close_price", DoubleToString(actualClose,     _Digits));
   msg += JN("profit",      DoubleToString(actualProfit,    2));
   msg += JN("magic",       IntegerToString(snap.magic));
   msg += JN("close_time",  IntegerToString(closeTime), true);
   msg += "}";
   SendMsg(msg);
   PrintFormat("Pushed position_closed: ticket=%I64u close=%s profit=%s",
               snap.ticket, DoubleToString(actualClose, _Digits),
               DoubleToString(actualProfit, 2));
}

//+------------------------------------------------------------------+
//| 推送 Positions（带去重，整体作为一组）                           |
//| 同时检测上次出现但本次消失的 ticket → 触发 PushPositionClosed     |
//+------------------------------------------------------------------+
bool PushPositions(bool forcePush=true)
{
   int total = PositionsTotal();

   //--- (1) 构造 currentSnaps + fingerprint
   PositionSnap currentSnaps[];
   ArrayResize(currentSnaps, total);
   int n = 0;
   string fp = "N=" + IntegerToString(total);
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;

      currentSnaps[n].ticket     = ticket;
      currentSnaps[n].symbol     = PositionGetString(POSITION_SYMBOL);
      currentSnaps[n].pos_type   = (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY ? "BUY":"SELL");
      currentSnaps[n].volume     = PositionGetDouble(POSITION_VOLUME);
      currentSnaps[n].open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      currentSnaps[n].cur_price  = PositionGetDouble(POSITION_PRICE_CURRENT);
      currentSnaps[n].sl         = PositionGetDouble(POSITION_SL);
      currentSnaps[n].tp         = PositionGetDouble(POSITION_TP);
      currentSnaps[n].profit     = PositionGetDouble(POSITION_PROFIT);
      currentSnaps[n].magic      = PositionGetInteger(POSITION_MAGIC);
      currentSnaps[n].comment    = PositionGetString(POSITION_COMMENT);

      fp += ";" + IntegerToString(ticket)
          + "," + DoubleToString(currentSnaps[n].volume,     2)
          + "," + DoubleToString(currentSnaps[n].cur_price,  _Digits)
          + "," + DoubleToString(currentSnaps[n].sl,         _Digits)
          + "," + DoubleToString(currentSnaps[n].tp,         _Digits)
          + "," + DoubleToString(currentSnaps[n].profit,     2);
      n++;
   }
   if(n != total) ArrayResize(currentSnaps, n);

   //--- (2) diff: 上次见过但本次消失的 ticket → 推 position_closed
   int oldCount = ArraySize(g_lastPositionSnaps);
   for(int i = 0; i < oldCount; i++)
   {
      ulong oldTicket = g_lastPositionSnaps[i].ticket;
      bool stillOpen = false;
      for(int j = 0; j < n; j++)
      {
         if(currentSnaps[j].ticket == oldTicket) { stillOpen = true; break; }
      }
      if(!stillOpen)
         PushPositionClosed(g_lastPositionSnaps[i]);
   }

   //--- (3) 更新 g_lastPositionSnaps（必须做，无论 (4) 是否去重）
   ArrayResize(g_lastPositionSnaps, n);
   for(int i = 0; i < n; i++)
      g_lastPositionSnaps[i] = currentSnaps[i];

   //--- (4) fingerprint 去重
   if(!forcePush && fp == g_lastPositionFp) return false;
   g_lastPositionFp = fp;

   //--- (5) 推送当前每个 position
   for(int i = 0; i < n; i++)
   {
      string msg = "{";
      msg += JQ("type",       "position");
      msg += JN("ticket",     IntegerToString(currentSnaps[i].ticket));
      msg += JQ("symbol",     currentSnaps[i].symbol);
      msg += JQ("pos_type",   currentSnaps[i].pos_type);
      msg += JN("volume",     DoubleToString(currentSnaps[i].volume,     2));
      msg += JN("open_price", DoubleToString(currentSnaps[i].open_price, _Digits));
      msg += JN("cur_price",  DoubleToString(currentSnaps[i].cur_price,  _Digits));
      msg += JN("sl",         DoubleToString(currentSnaps[i].sl,         _Digits));
      msg += JN("tp",         DoubleToString(currentSnaps[i].tp,         _Digits));
      msg += JN("profit",     DoubleToString(currentSnaps[i].profit,     2));
      msg += JN("magic",      IntegerToString(currentSnaps[i].magic));
      msg += JQ("comment",    currentSnaps[i].comment, true);
      msg += "}";
      SendMsg(msg);
   }
   return true;
}

//+------------------------------------------------------------------+
//| 推送 order_removed（已撤/已成交/已过期挂单）                       |
//| 通过 HistoryOrderSelect 取真实 ORDER_STATE → reason，             |
//| ORDER_TIME_DONE → removed_time。                                  |
//+------------------------------------------------------------------+
void PushOrderRemoved(const OrderSnap &snap)
{
   string reason   = "unknown";
   long   doneTime = (long)TimeCurrent();

   if(HistorySelect(g_startTime - 86400, TimeCurrent() + 1))
   {
      if(HistoryOrderSelect(snap.ticket))
      {
         long state = HistoryOrderGetInteger(snap.ticket, ORDER_STATE);
         if(state == ORDER_STATE_CANCELED)      reason = "canceled";
         else if(state == ORDER_STATE_FILLED)   reason = "filled";
         else if(state == ORDER_STATE_EXPIRED)  reason = "expired";
         doneTime = (long)HistoryOrderGetInteger(snap.ticket, ORDER_TIME_DONE);
      }
   }

   string msg = "{";
   msg += JQ("type",         "order_removed");
   msg += JN("ticket",       IntegerToString(snap.ticket));
   msg += JQ("symbol",       snap.symbol);
   msg += JQ("order_type",   snap.order_type);
   msg += JN("volume",       DoubleToString(snap.volume,     2));
   msg += JN("open_price",   DoubleToString(snap.open_price, _Digits));
   msg += JN("sl",           DoubleToString(snap.sl,         _Digits));
   msg += JN("tp",           DoubleToString(snap.tp,         _Digits));
   msg += JN("magic",        IntegerToString(snap.magic));
   msg += JQ("reason",       reason);
   msg += JN("removed_time", IntegerToString(doneTime), true);
   msg += "}";
   SendMsg(msg);
   PrintFormat("Pushed order_removed: ticket=%I64u reason=%s", snap.ticket, reason);
}

//+------------------------------------------------------------------+
//| 推送 Orders（带去重，整体作为一组）                                |
//| 同时检测上次出现但本次消失的 ticket → 触发 PushOrderRemoved       |
//+------------------------------------------------------------------+
bool PushOrders(bool forcePush=true)
{
   int total = OrdersTotal();

   //--- (1) 构造 currentSnaps + fingerprint
   OrderSnap currentSnaps[];
   ArrayResize(currentSnaps, total);
   int n = 0;
   string fp = "N=" + IntegerToString(total);
   for(int i = 0; i < total; i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;

      currentSnaps[n].ticket     = ticket;
      currentSnaps[n].symbol     = OrderGetString(ORDER_SYMBOL);
      currentSnaps[n].order_type = EnumToString((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE));
      currentSnaps[n].volume     = OrderGetDouble(ORDER_VOLUME_INITIAL);
      currentSnaps[n].open_price = OrderGetDouble(ORDER_PRICE_OPEN);
      currentSnaps[n].sl         = OrderGetDouble(ORDER_SL);
      currentSnaps[n].tp         = OrderGetDouble(ORDER_TP);
      currentSnaps[n].magic      = OrderGetInteger(ORDER_MAGIC);
      currentSnaps[n].comment    = OrderGetString(ORDER_COMMENT);

      fp += ";" + IntegerToString(ticket)
          + "," + IntegerToString(OrderGetInteger(ORDER_TYPE))
          + "," + DoubleToString(currentSnaps[n].volume,     2)
          + "," + DoubleToString(currentSnaps[n].open_price, _Digits)
          + "," + DoubleToString(currentSnaps[n].sl,         _Digits)
          + "," + DoubleToString(currentSnaps[n].tp,         _Digits);
      n++;
   }
   if(n != total) ArrayResize(currentSnaps, n);

   //--- (2) diff: 上次见过但本次消失的 ticket → 推 order_removed
   int oldCount = ArraySize(g_lastOrderSnaps);
   for(int i = 0; i < oldCount; i++)
   {
      ulong oldTicket = g_lastOrderSnaps[i].ticket;
      bool stillExists = false;
      for(int j = 0; j < n; j++)
      {
         if(currentSnaps[j].ticket == oldTicket) { stillExists = true; break; }
      }
      if(!stillExists)
         PushOrderRemoved(g_lastOrderSnaps[i]);
   }

   //--- (3) 更新 g_lastOrderSnaps（必须做，无论 (4) 是否去重）
   ArrayResize(g_lastOrderSnaps, n);
   for(int i = 0; i < n; i++)
      g_lastOrderSnaps[i] = currentSnaps[i];

   //--- (4) fingerprint 去重
   if(!forcePush && fp == g_lastOrderFp) return false;
   g_lastOrderFp = fp;

   //--- (5) 推送当前每个 order（注意：现在多带一个 magic 字段）
   for(int i = 0; i < n; i++)
   {
      string msg = "{";
      msg += JQ("type",       "order");
      msg += JN("ticket",     IntegerToString(currentSnaps[i].ticket));
      msg += JQ("symbol",     currentSnaps[i].symbol);
      msg += JQ("order_type", currentSnaps[i].order_type);
      msg += JN("volume",     DoubleToString(currentSnaps[i].volume,     2));
      msg += JN("open_price", DoubleToString(currentSnaps[i].open_price, _Digits));
      msg += JN("sl",         DoubleToString(currentSnaps[i].sl,         _Digits));
      msg += JN("tp",         DoubleToString(currentSnaps[i].tp,         _Digits));
      msg += JQ("comment",    currentSnaps[i].comment);
      msg += JN("magic",      IntegerToString(currentSnaps[i].magic), true);
      msg += "}";
      SendMsg(msg);
   }
   return true;
}

//+------------------------------------------------------------------+
//| 初始化单个TFConfig                                                 |
//+------------------------------------------------------------------+
void InitTFConfig(TFConfig &cfg, string tfStr, string role, int intervalSec, bool pushOnTick)
{
   if(tfStr == "") { cfg.enabled = false; return; }
   ENUM_TIMEFRAMES tf = StrToTF(tfStr);
   if(tf == (ENUM_TIMEFRAMES)-1)
   {
      cfg.enabled = false;
      Print("Invalid TF string for ", role, ": '", tfStr, "', disabled.");
      return;
   }
   cfg.tf           = tf;
   cfg.role         = role;
   cfg.period_str   = tfStr;
   cfg.intervalSec  = intervalSec;
   cfg.lastBarTime  = iTime(g_symbol, tf, 0);
   cfg.lastPushTime = 0;
   cfg.enabled      = true;
   cfg.pushOnTick   = pushOnTick;
   cfg.lastOpen     = 0.0;
   cfg.lastHigh     = 0.0;
   cfg.lastLow      = 0.0;
   cfg.lastClose    = 0.0;
   Print("TF configured: role=", role, " period=", tfStr, " interval=", intervalSec, "s pushOnTick=", pushOnTick);
}


//+----------------------------------------------------------------------------------+
//| 连接后推送全量快照                                                               |
//| 顺序：connected → history_tick → history_bar×N → account → position → order → bar|
//+----------------------------------------------------------------------------------+
void PushSnapshot()
{
   // 1. 连接信号
   PushConnected();
   
   // 2. 历史Tick（从旧到新）
   PushHistoryTicks();
   PushHistoryTickDone();

   // 3. 历史Bar（各启用周期，从旧到新，全部isClosed=true）
   for(int i = 0; i < 3; i++)
   {
      if(!g_tfs[i].enabled) continue;
      PushHistoryBars(i);
      PushHistoryDone(i);
   }

   // 4. 账户/持仓/挂单 快照
   PushAccount();
   PushPositions();
   PushOrders();

   // 5. 各周期当前Bar快照（未完成bar）
   for(int i = 0; i < 3; i++)
      if(g_tfs[i].enabled) PushUnclosedBar(i, true);
}

//+------------------------------------------------------------------+
//| OnInit                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   g_startTime = TimeCurrent();
   g_symbol = Symbol();
   
   //=== 启动横幅 ===
   Print("================================================================");
   Print("  CubeEA v5.50 启动");
   Print("================================================================");
   Print("时间: ", TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
         "  (本地: ", TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS), ")");
   Print("品种: ", g_symbol, "  Digits=", _Digits, "  Point=", _Point);
   Print("图表周期: ", EnumToString(_Period));

   //--- 服务器配置
   Print("---------------- 服务器配置 ----------------");
   Print("Host:Port      = ", Host, ":", Port);
   Print("断线重连间隔   = ", ReconnectSec, " 秒");

   //--- Tick配置
   Print("---------------- Tick 配置 -----------------");
   Print("EnableTick     = ", EnableTick);

   //--- 历史数据配置
   Print("---------------- 历史数据 ------------------");
   Print("EnableHistory     = ", EnableHistory, "  HistoryCount=", HistoryCount);
   Print("EnableHistoryTick = ", EnableHistoryTick,
         "  HistoryTickCount=", HistoryTickCount,
         "  Type=", HistoryTickType);

   //--- 账户/持仓/挂单
   Print("---------------- 账户/持仓/挂单 ------------");
   Print("AccountInterval  = ", AccountIntervalSec,  " 秒  PushOnTick=", AccountPushOnTick);
   Print("PositionInterval = ", PositionIntervalSec, " 秒  PushOnTick=", PositionPushOnTick);
   Print("OrderInterval    = ", OrderIntervalSec,    " 秒  PushOnTick=", OrderPushOnTick);

   //--- 周期配置
   Print("---------------- 周期配置 ------------------");
   InitTFConfig(g_tfs[0], ExecutionTF,  "execution",  ExecutionIntervalSec, ExecutionPushOnTick);
   InitTFConfig(g_tfs[1], TacticalTF,   "tactical",   TacticalIntervalSec, TacticalPushOnTick);
   InitTFConfig(g_tfs[2], StrategicTF,  "strategic",  StrategicIntervalSec, StrategicPushOnTick);
   
   //--- 账户信息（即时打印一次，便于核对）
   Print("---------------- 账户信息 ------------------");
   Print("Login    = ", AccountInfoInteger(ACCOUNT_LOGIN));
   Print("Server   = ", AccountInfoString(ACCOUNT_SERVER));
   Print("Company  = ", AccountInfoString(ACCOUNT_COMPANY));
   Print("Currency = ", AccountInfoString(ACCOUNT_CURRENCY));
   Print("Balance  = ", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));
   Print("Equity   = ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),  2));
   Print("Leverage = 1:", AccountInfoInteger(ACCOUNT_LEVERAGE));
   
   //--- 当前持仓/挂单数量
   Print("当前持仓数 = ", PositionsTotal(), "  当前挂单数 = ", OrdersTotal());

   //--- 尝试连接
   Print("---------------- 连接服务器 ----------------");
   if(TryConnect())
   {
      Print("✓ 初始连接成功，开始推送快照...");
      PushSnapshot();
      Print("✓ 快照推送完成");
   }
   else
   {
      Print("✗ 初始连接失败，将在 ", ReconnectSec, " 秒后重试");
   }

   Print("================================================================");
   Print("  EA 初始化完成，进入运行状态");
   Print("================================================================");

   return INIT_SUCCEEDED;
}


//+------------------------------------------------------------------+
//| OnDeinit                                                           |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| 将 OnDeinit 的 reason 代码翻译为可读文本                           |
//+------------------------------------------------------------------+
string DeinitReasonText(const int reason)
{
   switch(reason)
   {
      case REASON_PROGRAM:     return "EA 自行调用 ExpertRemove() 退出";
      case REASON_REMOVE:      return "EA 从图表移除";
      case REASON_RECOMPILE:   return "EA 被重新编译";
      case REASON_CHARTCHANGE: return "图表品种或周期被更改";
      case REASON_CHARTCLOSE:  return "图表被关闭";
      case REASON_PARAMETERS:  return "输入参数被修改";
      case REASON_ACCOUNT:     return "切换了交易账户";
      case REASON_TEMPLATE:    return "应用了新的模板";
      case REASON_INITFAILED:  return "OnInit 返回非零值，初始化失败";
      case REASON_CLOSE:       return "MT5 终端关闭";
      default:                 return "未知原因 (" + IntegerToString(reason) + ")";
   }
}

//+------------------------------------------------------------------+
//| 格式化运行时长（秒 → "Xd Xh Xm Xs"）                              |
//+------------------------------------------------------------------+
string FormatDuration(long seconds)
{
   if(seconds < 0) seconds = 0;
   long days  = seconds / 86400;  seconds %= 86400;
   long hours = seconds / 3600;   seconds %= 3600;
   long mins  = seconds / 60;     seconds %= 60;
   string s = "";
   if(days  > 0) s += IntegerToString(days)  + "d ";
   if(hours > 0) s += IntegerToString(hours) + "h ";
   if(mins  > 0) s += IntegerToString(mins)  + "m ";
   s += IntegerToString(seconds) + "s";
   return s;
}

void OnDeinit(const int reason)
{
   datetime now = TimeCurrent();
   long uptime = (long)(now - g_startTime);

   Print("================================================================");
   Print("  CubeEA 退出");
   Print("================================================================");
   Print("退出时间   = ", TimeToString(now, TIME_DATE|TIME_SECONDS));
   Print("启动时间   = ", TimeToString(g_startTime, TIME_DATE|TIME_SECONDS));
   Print("运行时长   = ", FormatDuration(uptime));
   Print("退出原因   = [", reason, "] ", DeinitReasonText(reason));
   Print("品种       = ", g_symbol);
   Print("Socket状态 = ", (g_socket != INVALID_HANDLE ? "已连接，准备关闭" : "未连接"));

   //--- 关闭 Socket
   if(g_socket != INVALID_HANDLE)
   {
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
      Print("✓ Socket 已关闭");
   }

   Print("================================================================");
   Print("  祝您发财");
   Print("================================================================");
}

//+------------------------------------------------------------------+
//| OnTick                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();

   //--- 断线重连
   if(g_socket == INVALID_HANDLE)
   {
      if(now - g_lastConnect >= ReconnectSec)
      {
         g_lastConnect = now;
         if(TryConnect()) PushSnapshot();
      }
      return;
   }

   //--- Tick推送
   if(EnableTick)
   {
      MqlTick tick;
      if(SymbolInfoTick(g_symbol, tick)) PushTick(tick);
   }

   //--- K线周期推送
   for(int i = 0; i < 3; i++)
   {
      if(!g_tfs[i].enabled) continue;

      datetime barTime = iTime(g_symbol, g_tfs[i].tf, 0);
      bool newBarArrived = (barTime != g_tfs[i].lastBarTime);

      //--- 新bar出现 → 前一根bar已确定完成（无论是否到间隔，立即推送）
      if(newBarArrived)
      {
         g_tfs[i].lastBarTime = barTime;
         PushClosedBar(i);       // shift=1, isClosed=true
         
         g_tfs[i].lastOpen  = 0.0;
         g_tfs[i].lastHigh  = 0.0;
         g_tfs[i].lastLow   = 0.0;
         g_tfs[i].lastClose = 0.0;
      }
      
      //--- 未完成bar推送：Tick驱动 或 时间间隔到达
      bool intervalReached = (now - g_tfs[i].lastPushTime >= g_tfs[i].intervalSec);
      bool shouldPushUnclosed = g_tfs[i].pushOnTick || intervalReached;

      if(shouldPushUnclosed)
      {
         //--- 间隔到达时强制推送（保证心跳）；Tick驱动时启用去重
         bool forcePush = intervalReached && !g_tfs[i].pushOnTick;
         if(PushUnclosedBar(i, forcePush))
            g_tfs[i].lastPushTime = now;
      }
   }

   //--- 账户：Tick驱动 或 时间间隔到达
   {
      bool intervalReached = (now - g_lastAccountPush >= AccountIntervalSec);
      if(AccountPushOnTick || intervalReached)
      {
         //--- 间隔到达时强制推送（保证心跳）；Tick驱动时启用去重
         bool forcePush = intervalReached && !AccountPushOnTick;
         if(PushAccount(forcePush))
            g_lastAccountPush = now;
      }
   }
   
   //--- 持仓
   {
      bool intervalReached = (now - g_lastPositionPush >= PositionIntervalSec);
      if(PositionPushOnTick || intervalReached)
      {
         bool forcePush = intervalReached && !PositionPushOnTick;
         if(PushPositions(forcePush))
            g_lastPositionPush = now;
      }
   }
   
   //--- 挂单
   {
      bool intervalReached = (now - g_lastOrderPush >= OrderIntervalSec);
      if(OrderPushOnTick || intervalReached)
      {
         bool forcePush = intervalReached && !OrderPushOnTick;
         if(PushOrders(forcePush))
            g_lastOrderPush = now;
      }
   }
}

//+------------------------------------------------------------------+
//| OnTrade（交易事件立即推送）                                        |
//+------------------------------------------------------------------+
void OnTrade()
{
   if(g_socket == INVALID_HANDLE) return;
   datetime now = TimeCurrent();
   PushAccount();   g_lastAccountPush  = now;
   PushPositions(); g_lastPositionPush = now;
   PushOrders();    g_lastOrderPush    = now;
}