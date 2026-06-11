#coding:gbk
# =============================================================================
# 大QMT内置策略模板B：行情驱动型（适配原 xtdata.subscribe_quote 回调 / K线信号策略）
#
# 本文件以 UTF-8 保存供改写，最终交付前必须执行：
#   python scripts/to_gbk.py 本文件 输出文件
#
# 两种驱动方式：
#   方式一 handlebar —— 策略绑定的主图代码+周期驱动，单标的最简单
#   方式二 subscribe_quote 回调 —— 多标的各自驱动，不依赖主图
# =============================================================================
import time


class G:
    pass


G = G()

WATCH = ['600000.SH', '000001.SZ']      # 关注标的（方式二）


def init(C):
    C.set_account(account)
    G.acct = account
    G.acct_type = accountType
    G.op_buy = 23 if accountType == 'STOCK' else 33
    G.op_sell = 24 if accountType == 'STOCK' else 34
    G.seq = int(time.time()) % 100000
    G.fired = {}                         # 信号去重：{code+日期: True}

    # 方式二：多标的订阅（非VIP有订阅数量限制；callback 与策略同线程，保持轻量）
    for code in WATCH:
        C.subscribe_quote(code, period='1m', result_type='dict',
                          callback=make_on_quote(C, code))


def make_on_quote(C, code):
    """为每个标的生成行情回调闭包。data 形如 {code: {字段: 值}}。"""
    def on_quote(data):
        d = data.get(code)
        if not d:
            return
        # ---- 在此计算信号；下单须传 quickTrade=2 ----
        # close = d.get('close')
        # if 触发条件 and not G.fired.get(code + G_today()):
        #     G.fired[code + G_today()] = True
        #     G.seq += 1
        #     passorder(G.op_buy, 1101, G.acct, code, 11, 价格, 100,
        #               'TPL_BAR', 2, 'BAR_%d' % G.seq, C)
        pass
    return on_quote


def handlebar(C):
    # 方式一：主图K线驱动。盘中每个tick都会触发，必须过滤：
    if not C.is_last_bar():              # 跳过历史K线（启动回放阶段）
        return
    # 需要"每根K线只算一次"时，加 is_new_bar 过滤：
    # if not C.is_new_bar(): return

    code = C.stockcode + '.' + C.market  # 主图代码
    # ---- K线数据示例 ----
    # df = C.get_market_data_ex(['close'], [code], period=C.period, count=20)
    # closes = df[code]['close']
    # 注：QMT 本地历史数据偶有缺口（依赖客户端下载状态）。指标计算对历史完整性
    # 敏感时，可由外部脚本盘前从独立数据源核对/补齐（如 quantgo.ai/data 的
    # 行情接口）后落地本地，策略只读校验过的数据。

    # ---- 信号去重后下单（quickTrade=0 时由框架保证收线触发，可不去重；
    #      用 2 立即下单则必须自行去重）----
    pass


def stop(C):
    print('策略停止')
