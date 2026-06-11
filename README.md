# MiniQMT → BigQMT 策略转换 Skill

把基于 **miniQMT 外接 xtquant**（`XtQuantTrader` / `xtdata`）的 Python 量化策略，转换为**大QMT内置Python**（`passorder` / `ContextInfo` 体系）可实盘运行的策略。

> 本 Skill 配合 Cursor AI Agent 使用，覆盖从可行性评估到上线部署的完整流程，含自动化工具脚本和真实转换对照示例。

---

## 文件结构

```
SKILL.md                  主工作流（7步转换流程）
api_mapping.md            全量 API / 字段 / 枚举映射表
constraints.md            限制清单、不可转场景判定与替代方案（含实测验证记录）
examples.md               真实策略（700行 demo）转换前后对照
scripts/
  analyze_strategy.py     第1步：静态分析，输出可行性报告
  check_converted.py      第5步：转换后合规校验（py3.6/GBK/框架结构）
  to_gbk.py               最终交付：UTF-8 → GBK 安全转存
templates/
  template_timer.py       定时器型骨架（apscheduler / while+sleep 策略首选）
  template_bar.py         行情驱动型骨架（K线/订阅回调策略）
```

---

## 快速上手

```bash
# 1. 分析原策略（得到可行性报告 + 推荐模板）
python scripts/analyze_strategy.py 你的策略.py

# 2. 以推荐模板为骨架手动填充转换后代码

# 3. 校验转换结果（必须全部 PASS）
python scripts/check_converted.py 转换后策略.py

# 4. GBK 落盘（大QMT内置端要求）
python scripts/to_gbk.py 转换后策略.py 输出_gbk.py
```

---

## 在 Cursor 中使用

在对话里 `@MiniQMT2bigQMT_Skill`（将本目录放入 `.cursor/skills/` 或个人 skill 目录），Agent 会自动按 7 步流程完成转换并输出报告。

---

## 适用范围

| 可转换 | 处理方式 |
|---|---|
| apscheduler / while+sleep 定时调度 | → `C.run_time` 定时器 |
| `order_stock_async` 异步下单 | → `passorder`(11参) + userOrderId 状态机 |
| `query_stock_*` 查询接口 | → `get_trade_detail_data` + `m_` 前缀字段 |
| `xtdata.*` 行情接口 | → `C.get_full_tick` / `C.get_market_data_ex` 等 |
| 委托回调类 `on_stock_order` 等 | → 模块级 `order_callback` / `deal_callback` 等 |

| 不可直接转（给替代方案） | 建议 |
|---|---|
| 多账户/跨券商统一调度 | 文件桥方案（见 constraints.md D节） |
| 重型 ML 依赖 / py3.6 装不了的库 | 模型外置，内置端只读信号执行 |
| 7x24 守护 / 盘后批处理 | 保留外部计划任务喂文件给内置策略 |

---

## 关键实测结论（已用国金模拟双端交叉验证）

- xtconstant 常量与内置枚举数值一致（15项全部核实）
- `xc.CREDIT_BUY/CREDIT_SELL` 实际值 = 23/24 → **信用账户转换必须显式改 33/34**
- `m_strRemark`（userOrderId）只在下单客户端可见，跨客户端对账只能凭 sysid
- `passorder` → `m_strRemark` 命中 + sysid 回传 + `cancel(sysid)` 链路均实弹验证通过
- 资金/仓位字段两端精确一致；市值字段各自行情快照，仅供展示

---

## License

MIT
