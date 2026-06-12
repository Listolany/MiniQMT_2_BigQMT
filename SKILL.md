---
name: miniqmt-to-bigqmt
description: 将基于 miniQMT 的外部 xtquant 库（xttrader/xtdata）的 Python 量化策略，转换为大QMT内置Python（ContextInfo/passorder 体系）可实盘运行的策略。当用户要求转换 miniQMT 策略、迁移 xtquant 代码到大QMT、或提到"内置Python/XTData/passorder 改写"时使用。包含可行性评估、API 映射、py3.6+GBK 约束校验、部署与实盘验证全流程；不可转换场景给出文件桥等替代方案。
---

# MiniQMT 策略 → 大QMT内置Python 转换

把外部 xtquant 策略（自带 Python 进程 + `XtQuantTrader`/`xtdata`）改写为在大QMT客户端内运行的内置策略（`init`/`handlebar`/`run_time` + `passorder`）。**目标是"真正能实盘"，不是语法翻译**——两套体系的运行模型、账户绑定、下单返回值、数据时效都不同，必须按本流程逐项处理。

## 两套体系的本质差异（先建立心智模型）

| 维度 | miniQMT 外接 | 大QMT 内置 |
|---|---|---|
| 进程 | 自己的 Python 进程，pip 任装 | 客户端内嵌 **Python 3.6**，库受限（券商可能有白名单） |
| 编码 | UTF-8 | **GBK**（首行必须 `#coding:gbk`） |
| 入口 | `if __name__ == '__main__'` 自由编排 | 框架回调：`init(C)` → `after_init(C)` → `handlebar(C)`/定时器 |
| 线程 | 随意多线程/apscheduler | **所有策略共用一个线程，禁止阻塞**（sleep/死循环/锁会卡死全部策略） |
| 账户 | 代码里 `StockAccount(id, type)`，可多账户 | 界面选定，注入全局变量 `account`/`accountType`，一个策略实例绑一个账户 |
| 下单 | `order_stock_async` 返回 seq，回报回调对账 | `passorder` **无返回值**，靠 `userOrderId`（投资备注，对应 `m_strRemark`）追踪 |
| 查询 | `query_stock_asset/orders/positions` 返回无前缀字段对象 | `get_trade_detail_data` 返回 **`m_` 前缀字段**对象（`m_nVolume` 等） |
| 行情 | `xtdata.*`（连 miniQMT 行情进程） | `C.get_full_tick`/`C.get_market_data_ex` 等（客户端行情） |
| 启停 | 自己守护、AutoLogin 重启 QMT | 随客户端启停；客户端设置里配自动登录/策略自启 |

## 转换工作流

复制此清单跟踪进度：

```
- [ ] 第1步 静态分析与可行性评估
- [ ] 第2步 选择目标结构模板
- [ ] 第3步 逐 API 映射改写
- [ ] 第4步 处理订单追踪与状态机
- [ ] 第5步 py3.6/GBK 合规校验
- [ ] 第6步 输出转换报告
- [ ] 第7步 部署与实盘验证指引
```

### 第1步 静态分析与可行性评估

用户若对内置端能力存疑（外部数据还能不能取、运行频率是否受限、能否回测、多策略会不会互相拖累等），先用 [faq.md](faq.md) 对齐认知再开工——这些多为误解，不要让错误前提影响转换方案。

运行分析脚本，得到 API 清单、py3.6 语法违例、第三方依赖、阻塞模式等：

```bash
python scripts/analyze_strategy.py <原策略.py>
```

按报告对照 [constraints.md](constraints.md) 分类每个发现项：
- **可直接映射** → 第3步处理
- **需重构**（apscheduler/多线程/while-sleep 主循环、回报回调对账等）→ 按模板重组
- **不可转换**（多账户单进程、重型第三方库、7x24 外部守护等）→ 在转换报告中给出 constraints.md 对应的替代方案（文件桥/外接极简模式/拆分策略），**不要硬转**

任何一项"不可转换"都不代表整个策略失败——逐项给方案，能转的部分照常转。

### 第2步 选择目标结构模板

| 原策略形态 | 模板 |
|---|---|
| 定时轮询型：apscheduler / while+sleep / 定点任务（绝大多数 miniQMT 策略） | [templates/template_timer.py](templates/template_timer.py) |
| 行情驱动型：`xtdata.subscribe_quote` 回调驱动 / 单标的 K线信号 | [templates/template_bar.py](templates/template_bar.py) |

模板已含：GBK 头、全局状态类 `G`（**禁止把可变状态存 ContextInfo**，有逐K线回滚机制）、`C.set_account(account)`（启用交易回调）、定时器注册、委托状态字典对账骨架、收盘自动停止逻辑。在模板骨架上填充策略逻辑，不要从零写。

### 第3步 逐 API 映射改写

对照 [api_mapping.md](api_mapping.md) 完成全部调用替换。高频映射速查（完整表必须查文件）：

| miniQMT | 大QMT内置 |
|---|---|
| `xt_trader.order_stock_async(acc, code, xtconstant.STOCK_BUY, vol, xtconstant.FIX_PRICE, price, strat, remark)` | `passorder(23, 1101, account, code, 11, price, vol, strat, 2, userOrderId, C)` |
| `xt_trader.cancel_order_stock_async(acc, order_id)` | `cancel(sysid, account, accountType, C)`（注意：用**委托号 m_strOrderSysID**，不是内部 order_id） |
| `xt_trader.query_stock_positions(acc)` | `get_trade_detail_data(account, accountType, 'position')` |
| `xt_trader.query_stock_asset(acc)` | `get_trade_detail_data(account, accountType, 'account')` |
| `xt_trader.query_stock_orders(acc)` | `get_trade_detail_data(account, accountType, 'order')` |
| `xtdata.get_full_tick(codes)` | `C.get_full_tick(codes)`（字段名同：lastPrice/askPrice/bidPrice...） |
| `xtdata.get_instrument_detail(code)` | `C.get_instrument_detail(code)`（字段名同：UpStopPrice/PreClose...） |
| `xtdata.get_market_data_ex(...)` | `C.get_market_data_ex(...)`（参数几乎同构） |
| `xtdata.get_trading_dates('SH', s, e)` | `C.get_trading_dates('000001.SH', s, e, count, '1d')`（**仅 after_init 后可用**；返回 `'20240101'` 字符串列表，不是时间戳） |
| `xtdata.download_history_data(code, period, s, e)` | `download_history_data(code, period, s, e)`（全局函数） |
| `xtdata.subscribe_quote(code, period, callback=f)` | `C.subscribe_quote(code, period, callback=f)`（在 init 里注册） |
| 回调类 `on_stock_order/on_stock_trade/on_order_error` | 模块级函数 `order_callback(C, o)` / `deal_callback(C, d)` / `orderError_callback(C, args, msg)`，须先 `C.set_account(account)` |
| apscheduler / while+sleep | `C.run_time("函数名", "3nSecond", "2025-01-01 09:30:00")` 或 `C.schedule_run(...)` |
| `xtconstant.STOCK_BUY/STOCK_SELL` | opType `23/24`；两融担保品 `33/34`、融资买入 `27`、卖券还款 `31` 等查映射表 |

改写时的硬规则：
1. 定时器/回调/after_init 里下单，`quickTrade` 必须传 `2`，否则会漏单。
2. 查询字段全部换 `m_` 前缀名（对照 api_mapping.md 的字段映射表）。买卖方向判断用 `m_nOffsetFlag`（48=买 49=卖）或 `m_nOpType`，**不要照搬 order_type==23 的写法去比对 `m_nOrderPriceType`**。
3. 删除 `XtQuantTrader` 连接管理、`AutoLogin`、重启 QMT 的代码——内置端没有"连接"概念，断线由客户端处理。
4. 删除 `time.sleep` 等待类写法。需要"等委托回报再行动"的逻辑改为：本轮记录待办 → 下一轮定时器回调检查（见模板的 pending 字典模式）。
5. `print` 输出到客户端策略日志面板，保留即可；写文件日志可用，但路径用绝对路径。

### 第4步 处理订单追踪与状态机

这是最容易出错的环节。`passorder` 无返回值且客户端缓存有 50ms~6s 延迟，照搬"下单→立刻查"必然漏单/超单：

1. 每笔委托生成唯一 `userOrderId`（如 `f"策略名_{日期}_{序号}"`），下单后存入全局 `G.pending[userOrderId] = {...}`，状态置"待报"。
2. 用 `order_callback`（实时推送）或定时器轮询 `get_trade_detail_data(..., 'order')`，按 `o.m_strRemark` 匹配回 `userOrderId`，更新状态/记录 `m_strOrderSysID`。
3. 撤单用记录到的 `m_strOrderSysID` 调 `cancel`。
4. 同一标的存在"待报"状态委托时禁止再下单（防超单）。
5. 委托状态码与 miniQMT 同一套数值（48已报/49部成/50已报待撤…53部撤/54已撤/56已成/57废单），判活集合 `(48,49,50,51,52,55,86,255)` 可沿用。

### 第5步 py3.6/GBK 合规校验

```bash
python scripts/check_converted.py <转换后策略.py>
```

脚本会拦截：≥3.7 语法（walrus、f-string `=`、dataclasses、asyncio.run、match 等）、残留 xtquant import、threading/multiprocessing、`time.sleep`、`input()`、缺 `#coding:gbk` 头、缺 `init`、`passorder` 参数个数错误、GBK 不可编码字符。**必须全部 PASS 才算转换完成**；每修一处重跑。

最后转存为 GBK 编码（编辑器直接改写 GBK 文件易出乱码，务必用脚本）：

```bash
python scripts/to_gbk.py <转换后策略.py> <输出.py>
```

### 第6步 输出转换报告

向用户输出报告，包含：
- 已映射 API 清单（原调用 → 新调用）
- 重构点说明（调度器改造、订单追踪改造等）
- **不可转换项及替代方案**（引用 constraints.md 具体章节）
- 行为差异警示：行情时效（内置为客户端行情，无 VIP 时订阅数量受限）、`get_trade_detail_data` 是本地缓存非柜台实查、策略随客户端启停

### 第7步 部署与实盘验证指引

指导用户按以下步骤上线（细节见 [constraints.md](constraints.md) 部署章节）：
1. 大QMT → 新建Python策略 → 粘贴转换后代码（确认编辑器显示中文注释无乱码）→ 保存编译
2. 策略交易/模型交易界面 → 新建 → 选本策略 + 资金账号（普通=STOCK/两融=CREDIT）+ 任意周期（定时器型策略选日线最省资源）→ 运行模式先选**模拟信号**
3. 模拟信号模式观察 1 个交易时段：信号面板的下单时机/数量/价格与预期一致
4. 切**实盘交易**模式，先用最小单量 + 不易成交价（买跌停价/卖涨停价附近）下 1-2 笔并撤掉，验证报/撤链路；报撤测试安排在交易时段或收盘后半小时内（实测 17 点后柜台不处理撤单）
5. 客户端设置勾选：自动登录、终端启动后策略自动运行；Windows 计划任务配开机启动客户端

## 参考文件

- [faq.md](faq.md) — 买方共性疑虑（外部数据/运行频率/同步下单/回测/性能预算），转换前对齐认知用
- [api_mapping.md](api_mapping.md) — 全量函数/字段/枚举映射表
- [constraints.md](constraints.md) — 限制清单、不可转场景判定与替代方案、部署细节
- [examples.md](examples.md) — 真实策略（apscheduler+xtquant 700行）转换前后对照
- [templates/template_timer.py](templates/template_timer.py)、[templates/template_bar.py](templates/template_bar.py)
- https://dict.thinktrader.net/?id=aWtHn6，映射表未覆盖的函数查这里
- 如有改进建议，可添加QQ：290560364 反馈意见
