# API 映射表：xtquant（miniQMT外接） → 大QMT内置Python

逐条对照改写。"——"表示无直接等价物，处理方式见备注或 constraints.md。
权威细节：`docs/qmt_api/innerApi/trading_function.md`、`data_function.md`、`enum_constants.md`、`data_structure.md`。

## 1. 连接与生命周期

| miniQMT | 大QMT内置 | 备注 |
|---|---|---|
| `XtQuantTrader(path, session_id)` | ——（删除） | 内置端无连接概念，策略在客户端内运行 |
| `xt_trader.start()` / `.connect()` / `.stop()` | ——（删除） | 同上 |
| `xt_trader.subscribe(account)` | `ContextInfo.set_account(account)`（init 中调用） | 启用 order/deal/position/account 回调的前提 |
| `StockAccount(acc_id, 'STOCK'/'CREDIT')` | 全局变量 `account`、`accountType`（界面选定后注入） | 代码中直接引用，不要自己定义同名变量覆盖 |
| `if __name__ == '__main__':` 主程序 | `init(C)` + `after_init(C)` + 定时器/`handlebar` | 初始化进 init/after_init；注意 `get_trading_dates` 等在 init 中不可用，放 after_init |
| 脚本退出/信号处理 | `stop(C)`（策略停止时被调用） | stop 中交易连接已断，不能报撤单 |

## 2. 下单与撤单

| miniQMT | 大QMT内置 | 备注 |
|---|---|---|
| `order_stock(acc, code, order_type, vol, price_type, price, strategy, remark)` | `passorder(opType, 1101, account, code, prType, price, vol, strategy, 2, userOrderId, C)` | 同步/异步在内置端无区别，passorder 本身异步无返回值 |
| `order_stock_async(...)` 返回 seq | 无返回值；用 `userOrderId`（→ 回报对象的 `m_strRemark`）追踪 | 见 SKILL.md 第4步状态机 |
| `cancel_order_stock(acc, order_id)` | `cancel(orderSysId, account, accountType, C)` | 内置端撤单凭**柜台委托号** `m_strOrderSysID`（字符串），不是 xtquant 的内部 order_id |
| `cancel_order_stock_sysid_async(acc, market, sysid)` | `cancel(sysid, account, accountType, C)` | 直接对应 |
| 新股申购 `order_stock(..., xtconstant.STOCK_BUY, ...)` 对申购代码 | `passorder` + 专用 opType（申购相关枚举见 enum_constants.md） | `get_ipo_data()` 可取当日新股新债信息 |
| ——（外接无算法单） | `algo_passorder(...)` / `smart_algo_passorder(...)` | 转换时可顺带升级：自研拆单可改用券商 VWAP/TWAP 算法（需权限） |

### opType（股票/两融常用）

| xtconstant | mini值 | 内置 opType | 说明 |
|---|---|---|---|
| `STOCK_BUY` | 23 | `23` | 股票/ETF/可转债买入 |
| `STOCK_SELL` | 24 | `24` | 股票/ETF/可转债卖出 |
| `CREDIT_BUY`（担保品买入） | **23** | `33` | **数值不同源！** mini 的 CREDIT_BUY 实际值=23（与 STOCK_BUY 相同，靠 StockAccount 类型区分）；内置端信用账户必须显式用 33 |
| `CREDIT_SELL`（担保品卖出） | **24** | `34` | 同上，内置端必须显式用 34 |
| `CREDIT_FIN_BUY` | 27 | `27` | 融资买入（两端数值一致） |
| `CREDIT_SLO_SELL` | 28 | `28` | 融券卖出 |
| `CREDIT_BUY_SECU_REPAY` | 29 | `29` | 买券还券 |
| `CREDIT_DIRECT_SECU_REPAY` | 30 | `30` | 直接还券 |
| `CREDIT_SELL_SECU_REPAY` | 31 | `31` | 卖券还款 |
| `CREDIT_DIRECT_CASH_REPAY` | 32 | `32` | 直接还款 |

**两融账户转换陷阱**：mini 策略里写 `xtconstant.CREDIT_BUY` 或对信用账户写 `STOCK_BUY`，源码里看到的都是 23——转换到内置端时**不能照抄 23**，必须按账户类型换成 33/34（担保品买卖），否则部分柜台拒单。融资融券专项操作 27~32 两端数值一致可直抄。

### prType（价格类型）

| xtconstant | 值 | 内置 prType | 说明 |
|---|---|---|---|
| `FIX_PRICE` | 11 | `11` | 指定价（最常用），price 参数生效 |
| `LATEST_PRICE` | 5 | `5` | 最新价，price 填任意占位数 |
| 卖5~卖1价 | — | `0`~`4` | 对手方向盘口价 |
| 买1~买5价 | — | `6`~`10` | 本方向盘口价 |
| `MARKET_SH_CONVERT_5_CANCEL` | 42 | `42` | 沪最优五档即成剩撤（仿真柜台不支持市价类） |
| `MARKET_SH_CONVERT_5_LIMIT` | 43 | `43` | 沪五档剩转限价 |
| `MARKET_PEER_PRICE_FIRST` | 44 | `44` | 对手方最优 |
| `MARKET_MINE_PRICE_FIRST` | 45 | `45` | 本方最优 |
| `MARKET_SZ_INSTBUSI_RESTCANCEL` | 46 | `46` | 深即成剩撤 |
| `MARKET_SZ_CONVERT_5_CANCEL` | 47 | `47` | 深五档即成剩撤 |
| `MARKET_SZ_FULL_OR_CANCEL` | 48 | `48` | 深全额成交或撤 |
| 盘后定价 | — | `49` | 科创/创业盘后固定价 |
| 对手价 | — | `14` | 对方一档 |
| 挂单价 | — | `13` | 本方一档 |

### orderType 第二参数（单股）

固定用 `1101`（单股单账号按股数）。按金额下单用 `1102`（volume 单位变为元）、按比例 `1103`（%）。账号组 `1201/1202/1203`（极少用，多账户场景见 constraints.md）。

### quickTrade 第九参数

定时器回调/行情回调/after_init 中调用 → **必须 `2`**。仅 handlebar 中希望模拟K线收线信号 → `0`。`1` = 仅最新K线触发。

## 3. 查询

| miniQMT | 大QMT内置 | 备注 |
|---|---|---|
| `query_stock_asset(acc)` | `get_trade_detail_data(account, accountType, 'account')` | 返回 list（取 `[0]`） |
| `query_stock_positions(acc)` | `get_trade_detail_data(account, accountType, 'position')` | |
| `query_stock_orders(acc, cancelable_only)` | `get_trade_detail_data(account, accountType, 'order')` | 无 cancelable_only 参数，自行按状态过滤 |
| `query_stock_trades(acc)` | `get_trade_detail_data(account, accountType, 'deal')` | |
| 按策略过滤 | `get_trade_detail_data(account, accountType, 'order', strategyName)` | 第4参数过滤 passorder 的 strategyName |
| ——（无） | `get_last_order_id(account, accountType, 'order'[, strategyName])` | 最新委托号，找不到返回 `'-1'` |
| ——（无） | `get_value_by_order_id(sysid, account, accountType, 'order'/'deal')` | 按委托号取单笔对象 |
| `query_credit_detail(acc)` | `get_trade_detail_data(account, 'CREDIT', 'account')` | 信用账号对象，字段见 data_structure.md |
| `query_stock_orders` 历史 | `get_history_trade_detail_data(account, type, 'ORDER', '20240101', '20240131')` | 内置端可查历史明细（外接查不到隔日） |
| ——（无） | `query_credit_account(seq, C)` + `credit_account_callback` | 查柜台两融明细（异步回调） |
| 两融标的 | `get_assure_contract(accid)` / `get_enable_short_contract(accid)` | 担保品/可融券明细 |

### 查询对象字段映射（高频）

| 外接字段（XtAsset/XtOrder/XtPosition/XtTrade） | 内置字段（m_ 前缀） |
|---|---|
| `asset.cash` | `acc.m_dAvailable` |
| `asset.total_asset` | `acc.m_dBalance` |
| `asset.market_value` | `acc.m_dInstrumentValue`（或 `m_dStockValue`） |
| `asset.frozen_cash` | `acc.m_dFrozenCash` |
| `order.stock_code`（'600000.SH'） | 拼接：`o.m_strInstrumentID + '.' + o.m_strExchangeID` |
| `order.order_id`（int，本地） | 无对应；以 `o.m_strOrderSysID`（柜台委托号）为准 |
| `order.order_sysid` | `o.m_strOrderSysID` |
| `order.order_status` | `o.m_nOrderStatus`（状态码数值同一套） |
| `order.order_volume` | `o.m_nVolumeTotalOriginal` |
| `order.traded_volume` | `o.m_nVolumeTraded` |
| `order.traded_price` | `o.m_dTradedPrice` |
| `order.price` | `o.m_dLimitPrice` |
| `order.order_type`（23买/24卖） | `o.m_nOpType`（23/24/33/34...）；方向也可用 `m_nOffsetFlag`（48买/49卖） |
| `order.order_remark` | `o.m_strRemark`（= passorder 的 userOrderId） |
| `order.strategy_name` | `o.m_strSource` 或按 strategyName 过滤查询 |
| `order.order_time`（时间戳） | `o.m_strInsertTime`（'091259' 字符串）+ `m_strInsertDate` |
| `position.stock_code` | 拼接：`p.m_strInstrumentID + '.' + p.m_strExchangeID` |
| `position.volume` | `p.m_nVolume` |
| `position.can_use_volume` | `p.m_nCanUseVolume` |
| `position.market_value` | `p.m_dInstrumentValue`（或 `m_dMarketValue`） |
| `position.avg_price` | `p.m_dOpenPrice`（或 `m_dAvgOpenPrice`/`m_dPositionCost` 成本额） |
| `position.on_road_volume` | `p.m_nOnRoadVolume` |
| `trade.traded_price` | `d.m_dPrice` |
| `trade.traded_volume` | `d.m_nVolume` |
| `trade.traded_amount` | `d.m_dTradeAmount` |
| `trade.traded_time` | `d.m_strTradeTime`（'172341'）+ `m_strTradeDate` |
| `trade.order_sysid` | `d.m_strOrderSysID`（与委托表同号，用于关联） |

### 委托状态码（两边同一套数值，已实测核对 xtconstant 与 inner 枚举一致）

48未报 / 49待报 / 50已报 / 51已报待撤 / 52部成待撤 / 53部撤 / 54已撤 / 55部成 / 56已成 / 57废单 / 255未知（86 为 mini 侧扩展值，inner 文档未列，判活集合保留无害）。
在途判活集合：`(48, 49, 50, 51, 52, 55, 86, 255)`；终态：`(53, 54, 56, 57)`。

**跨客户端可见性（实测结论，重要）**：委托本身是柜台级共享——A 客户端下的单，B 客户端（或 miniQMT）能查到同一 `sysid` 和状态；但 `m_strRemark`（投资备注/userOrderId）、`strategyName`、mini 的 `order_id` 都**只在下单客户端本地可见**（他端查询 remark 为空、order_id 为 0）。因此：同客户端对账用 userOrderId，跨客户端对账只能凭柜台委托号 sysid。

## 4. 回调

| miniQMT（XtQuantTraderCallback 方法） | 大QMT内置（模块级函数，需先 `C.set_account(account)`） |
|---|---|
| `on_stock_order(self, order)` | `def order_callback(ContextInfo, orderInfo):`（orderInfo 为 m_ 字段对象） |
| `on_stock_trade(self, trade)` | `def deal_callback(ContextInfo, dealInfo):` |
| `on_stock_position(self, position)` | `def position_callback(ContextInfo, positionInfo):` |
| `on_stock_asset(self, asset)` | `def account_callback(ContextInfo, accountInfo):` |
| `on_order_error(self, err)` | `def orderError_callback(ContextInfo, orderArgs, errMsg):` |
| `on_cancel_error(self, err)` | ——（无独立撤单失败回调；轮询委托状态兜底） |
| `on_order_stock_async_response(self, resp)` | ——（passorder 无下单应答；靠 order_callback 首次推送确认） |
| `on_disconnected(self)` | ——（删除；客户端自管重连。交易日切换时策略会被自动重启，属正常） |

注意：内置回调**仅实盘运行模式生效**（模拟信号模式不触发），且与策略同线程——回调里不要做耗时操作。

## 5. 行情

| miniQMT (xtdata) | 大QMT内置 | 备注 |
|---|---|---|
| `get_full_tick(codes)` | `C.get_full_tick(codes)` | 返回结构同（lastPrice/askPrice[5]/bidPrice[5]/lastClose/volume...） |
| `get_instrument_detail(code)` | `C.get_instrument_detail(code[, iscomplete])` | 字段同名（InstrumentName/PreClose/UpStopPrice/DownStopPrice/PriceTick...） |
| `get_market_data_ex(fields, codes, period, start, end, count, dividend_type, fill_data)` | `C.get_market_data_ex(fields, codes, period, start, end, count, dividend_type, fill_data, subscribe)` | 参数同构；**不要在 init 里调**（只能取到本地数据）；`subscribe=False` 时只读本地 |
| `get_local_data(...)` | `C.get_market_data_ex(..., subscribe=False)` | 内置的 get_local_data 已不推荐 |
| `subscribe_quote(code, period, count, callback)` | `C.subscribe_quote(code, period='1d', dividend_type, result_type, callback)` | 返回订阅号；非VIP有订阅数限制 |
| `subscribe_whole_quote(markets, callback)` | `C.subscribe_whole_quote(codes, callback)` | 全推快照 |
| `unsubscribe_quote(seq)` | `C.unsubscribe_quote(subID)` | |
| `get_trading_dates('SH', start, end)` 返回**毫秒时间戳列表** | `C.get_trading_dates('000001.SH', start, end, count, '1d')` 返回**'YYYYMMDD'字符串列表** | 必改：删掉时间戳转换代码；仅 after_init 之后可用 |
| `download_history_data(code, period, start, end)` | `download_history_data(code, period, start, end[, incrementally])` | 全局函数同名直用 |
| `download_history_data2(codes, period, start, end, callback)` | 循环调 `download_history_data` | 内置无批量带进度版本 |
| `get_stock_list_in_sector(name)` | `C.get_stock_list_in_sector(name)` | |
| `get_financial_data(...)` | `C.get_financial_data(fieldList, codes, start, end, report_type)` | 签名有差异，查 data_function.md |
| `get_divid_factors(code)` | `C.get_divid_factors(code)` | |
| `get_main_contract(code)` | `C.get_main_contract(code)` | 期货 |
| `xtdata.run()` | ——（删除） | 内置框架自带事件循环 |

## 6. 调度/定时

定时器与周期无关：主图周期选日线，`run_time` 照样按设定间隔跑（最短毫秒级）。但 **`run_time`/`schedule_run` 在回测模式无效**——需要回测的策略要把信号逻辑抽成独立函数，回测挂 `handlebar`、实盘挂定时器（见 faq.md Q4）。

| miniQMT 模式 | 大QMT内置 | 备注 |
|---|---|---|
| `while True: ... time.sleep(n)` | `C.run_time("f", "{n}nSecond", "2025-01-01 09:30:00")` | 函数名传**字符串**；起始时间设过去则立即生效 |
| apscheduler `interval` 任务 | `C.run_time("f", "3nSecond", ...)` 或 `C.schedule_run(f, '20250101093000', -1, dt.timedelta(seconds=3), 'grp')` | schedule_run 传函数对象，可取消（`C.cancel_schedule_run('grp')`） |
| apscheduler `cron`/`date` 定点任务（如 09:25:30 开盘买入） | 秒级定时器内判时间窗 + 当日执行标志位 | 见 template_timer.py 的 `_in_window`/`G.done_flags` 模式 |
| 毫秒级轮询 | `"500nMilliSecond"` | 留意性能，所有策略共线程 |
| 每日重置状态 | 定时器回调里检测日期变化后重置 G | 交易日切换时策略也会被客户端重启（init 重跑），状态需可重建（见第4步状态机+可选落盘） |

## 7. 删除/禁用清单

转换时直接删除，不要带入：
- `from xtquant import ...` 全部 import
- `XtQuantTrader`/`XtQuantTraderCallback` 类与连接管理
- `AutoLogin`、`os.startfile` 重启 QMT、看门狗
- `threading`/`multiprocessing`/`asyncio`/`apscheduler`
- `time.sleep`（任何等待逻辑改状态机）
- `input()`、GUI、命令行参数解析
