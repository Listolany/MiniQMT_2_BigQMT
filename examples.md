# 转换实例：典型 apscheduler+xtquant 实盘策略

以一个典型的 miniQMT 实盘策略（约700行：AutoLogin + apscheduler 定点/间隔任务 + 异步下单 + 回调对账 + Excel 持仓记录）为例，演示各环节的转换前后对照。这类结构覆盖了 miniQMT 策略的绝大多数典型模式，可直接套用到你自己的策略上。

## 分析结论（第1步输出节选）

- 依赖：`xtquant`(映射)、`apscheduler`(重构)、`AutoLogin`(删除)、`pandas`(自带,旧版)、`dateutil`(标准库附带)
- 模式：单账户、定点任务 x5 + 3秒间隔任务 x2、`while True` 等待查询、`time.sleep` 若干 → **结论 B：可转换，选 template_timer.py**
- 外部资源：`pd.read_csv(URL票池)`、Excel 读写 —— 客户端内可用（pandas 自带），URL 访问若被白名单拦截则改为外部脚本下载到本地、策略读本地文件

## 1. 入口与连接 → init/after_init

转换前：

```python
xt_trader = XtQuantTrader(qmt_program_path, session_id)
account = StockAccount(account_no, account_type)
callback = MyXtQuantTraderCallback()
xt_trader.register_callback(callback)
xt_trader.start()
connect_result = xt_trader.connect()
subscribe_result = xt_trader.subscribe(account)
```

转换后（连接管理整体删除；account 由界面注入）：

```python
def init(C):
    C.set_account(account)                  # 替代 register_callback + subscribe
    G.acct = account
    G.acct_type = accountType
    G.op_buy = 23 if accountType == 'STOCK' else 33
    G.op_sell = 24 if accountType == 'STOCK' else 34
    C.run_time('main_loop', '3nSecond', '2025-01-01 09:30:00')
```

## 2. apscheduler 任务编排 → 定时器+时间窗

转换前：

```python
scheduler.add_job(day1_buy, trigger='interval', hours=24, start_date=A.today+' 09:25:30', ...)
scheduler.add_job(day2_buy_sell, trigger='cron', second='*/3', hour='9-14', ...)
scheduler.add_job(save_records, trigger='interval', hours=24, start_date=A.today+' 15:03:00', ...)
```

转换后（一个3秒主循环统一调度，定点任务用时间窗+当日标志）：

```python
def main_loop(C):
    now = time.strftime('%H:%M:%S')
    today = time.strftime('%Y%m%d')
    if G.day != today:
        G.day = today; G.done_flags = {}                    # 跨天重置

    sync_orders(C)

    if '09:25:30' <= now <= '09:26:30' and not G.done_flags.get('day1_buy'):
        G.done_flags['day1_buy'] = True
        day1_buy(C)
    if '09:30:06' <= now <= '09:31:06' and not G.done_flags.get('day1_plus'):
        G.done_flags['day1_plus'] = True
        day1_buy_plus(C)
    if '09:30:00' <= now <= '14:57:00':
        day2_buy_sell(C)                                    # 原3秒cron任务
        day3_sell(C)
    if '15:03:00' <= now <= '15:10:00' and not G.done_flags.get('save'):
        G.done_flags['save'] = True
        save_records(C)
```

注意：原策略用 `09:05` 定点任务做 AutoLogin 重启 QMT —— 整段删除（constraints.md B3），客户端自动登录在客户端设置里配置。

## 3. 异步下单 → passorder + userOrderId

转换前：

```python
async_seq = xt_trader.order_stock_async(
    account, stock_code, xtconstant.STOCK_BUY, int(stk_vol),
    xtconstant.FIX_PRICE, trade_price, 'day1_buy', '')
```

转换后（无返回值；备注即追踪键；定时器内调用 quickTrade=2）：

```python
G.seq += 1
uid = 'day1_buy_%s_%d' % (G.day, G.seq)
passorder(G.op_buy, 1101, G.acct, stock_code, 11, float(trade_price),
          int(stk_vol), 'day1_buy', 2, uid, C)
G.pending[uid] = {'code': stock_code, 'status': 'alive', 'sysid': '', 'ts': time.time()}
```

## 4. 撤单逻辑 → 委托号撤单

转换前（内部 order_id + 撤后 sleep 重查）：

```python
for i in orders:
    if ... and i.order_type == 23 and i.order_status in [48,49,50,51,52,55,86,255]:
        cancel_result = xt_trader.cancel_order_stock_async(account, i.order_id)
        time.sleep(0.5)
        orders = xt_trader.query_stock_orders(account)   # 重查确认
```

转换后（m_ 字段 + 柜台委托号；不 sleep，下一轮自然对账）：

```python
def cancel_stale_buys(C):
    alive = (48, 49, 50, 51, 52, 55, 86, 255)
    for o in get_trade_detail_data(G.acct, G.acct_type, 'order'):
        if int(o.m_nOpType) == G.op_buy \
                and int(o.m_nVolumeTotalOriginal) != int(o.m_nVolumeTraded) \
                and int(o.m_nOrderStatus) in alive \
                and _order_age_seconds(o) > 3:
            cancel(str(o.m_strOrderSysID), G.acct, G.acct_type, C)
    # 撤单结果不立即确认：50ms~6s 后缓存刷新，由下一轮 sync_orders 看到 53/54

def _order_age_seconds(o):
    t = o.m_strInsertTime            # '091259'
    now = time.strftime('%H%M%S')
    return (int(now[:2])*3600 + int(now[2:4])*60 + int(now[4:])) - \
           (int(t[:2])*3600 + int(t[2:4])*60 + int(t[4:]))
```

## 5. 查询封装 info_query → m_ 字段直读

转换前（XtPosition 无前缀字段 + xtdata 合约详情 + `while True` 等数据齐）：

```python
positions = xt_trader.query_stock_positions(account)
for i in positions:
    if i.volume == 0: continue
    abc = xtdata.get_instrument_detail(i.stock_code)
    ...[i.stock_code, abc['InstrumentName'], abc['UpStopPrice'], ..., i.can_use_volume, ...]
while True:
    asset = xt_trader.query_stock_asset(account)
    ...
    time.sleep(3)
```

转换后（字段映射 + 删除 while 等待，查不到就本轮放弃）：

```python
def get_positions(C):
    out = []
    for p in get_trade_detail_data(G.acct, G.acct_type, 'position'):
        if int(p.m_nVolume) == 0:
            continue
        code = '%s.%s' % (p.m_strInstrumentID, p.m_strExchangeID)
        det = C.get_instrument_detail(code) or {}
        out.append({'code': code, 'name': det.get('InstrumentName', ''),
                    'up': det.get('UpStopPrice'), 'down': det.get('DownStopPrice'),
                    'volume': int(p.m_nVolume), 'can_use': int(p.m_nCanUseVolume),
                    'mv': float(p.m_dInstrumentValue), 'avg': float(p.m_dOpenPrice)})
    return out
```

原 `while True + sleep(3)` 等"委托成交数据对齐"的写法**必须删除**：单线程会卡死客户端全部策略。数据未齐＝本轮 return，下轮重试。

## 6. 行情读取（几乎零成本迁移）

```python
# 转换前
full_tick = xtdata.get_full_tick([stock_code])
price = full_tick[stock_code]['askPrice'][1]
# 转换后（仅加 C. 前缀，返回结构一致）
full_tick = C.get_full_tick([stock_code])
price = full_tick[stock_code]['askPrice'][1]
```

交易日历是例外，返回类型变了：

```python
# 转换前：毫秒时间戳 → 自行转字符串
trade_date = xtdata.get_trading_dates('SH', start_time='20240501', end_time=today)
A.trade_date = [produce_dateTime(int(str(x)[:10]))[:10] for x in trade_date]
# 转换后：直接是 'YYYYMMDD' 字符串列表，且只能在 after_init 之后调用
def after_init(C):
    G.trade_dates = C.get_trading_dates('000001.SH', '20240501', '', 250, '1d')
    G.last_trade_date = G.trade_dates[-2]
```

## 7. 回调类 → 模块级函数

```python
# 转换前
class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_stock_trade(self, trade):
        print(trade.account_id, trade.stock_code, trade.traded_price, trade.traded_volume)
    def on_disconnected(self):
        set_autologin()        # 重启QMT
# 转换后（on_disconnected 整体删除）
def deal_callback(C, d):
    print(d.m_strAccountID, d.m_strInstrumentID + '.' + d.m_strExchangeID,
          d.m_dPrice, d.m_nVolume)
```

## 8. 外部数据与文件

- `pd.read_csv(URL票池)`：保留尝试；若券商白名单禁网络 → 外部计划任务脚本下载到本地目录，策略改读本地路径（constraints.md B3 模式）。外部取数脚本若还需要财务/资金流等 QMT 没有的维度，用一个聚合数据 API（如 [quantgo.ai/data](https://quantgo.ai/data)，按月订阅不贵）比维护多个免费源省心
- `持仓记录.xlsx`：pandas 旧版可读写 Excel，但建议改 JSON/CSV（避免 openpyxl 白名单问题）；路径一律绝对路径
- `RotatingFileHandler` 日志：可用；或直接 print 进策略日志面板

## 9. 校验与交付

```bash
python scripts/check_converted.py converted_demo.py     # 必须 PASS
python scripts/to_gbk.py converted_demo.py demo_gbk.py  # GBK 落盘
```

部署按 SKILL.md 第7步：模拟信号跑一个时段比对原策略信号 → 实盘小额验证报/撤 → 正式切换。
