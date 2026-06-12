#coding:gbk
# =============================================================================
# 大QMT内置策略模板A：定时轮询型（适配原 apscheduler / while+sleep 类策略）
#
# 本文件以 UTF-8 保存供改写，最终交付前必须执行：
#   python scripts/to_gbk.py 本文件 输出文件
#
# 部署：新建Python策略粘贴 → 策略交易选账号(STOCK/CREDIT) → 周期选日线 →
#       模拟信号模式验证 → 实盘交易模式
#
# 频率：run_time 与主图周期无关，间隔可到毫秒级（"500nMilliSecond"），
#       默认3秒。注意 run_time 在回测模式无效——需要回测时把信号逻辑抽成
#       独立函数，回测挂 handlebar、实盘挂定时器（faq.md Q4）。
# =============================================================================
import json
import os
import time


class G:
    """全局状态容器。禁止把可变状态存入 ContextInfo（有逐K线回滚机制）。"""
    pass


G = G()

# ---- 策略参数（按需修改）----
STATE_FILE = r'D:\qmt_strategy_state\my_strategy.json'   # 状态落盘（客户端重启策略后恢复）
TRADE_BEGIN = '09:30:05'
TRADE_END = '14:56:50'


def init(C):
    # account / accountType 由策略交易界面注入，代码中直接引用
    C.set_account(account)              # 启用 order/deal 等实时回调（仅实盘模式生效）
    G.acct = account
    G.acct_type = accountType
    # 买卖 opType：普通账户 23/24；两融账户担保品 33/34（融资买入27等按业务改）
    G.op_buy = 23 if accountType == 'STOCK' else 33
    G.op_sell = 24 if accountType == 'STOCK' else 34

    G.day = ''                          # 当前交易日（检测跨天重置）
    G.seq = int(time.time()) % 100000   # userOrderId 序号基数（跨重启不重复）
    G.pending = {}                      # userOrderId -> {'code','vol','status','sysid','ts'}
    G.done_flags = {}                   # 当日一次性任务标记，如 {'open_buy': True}
    _load_state()

    # 主循环定时器：3秒一轮（按策略需要调整；最细可用 nMilliSecond）
    C.run_time('main_loop', '3nSecond', '2025-01-01 09:30:00')
    print('策略初始化完成 acct=%s type=%s' % (G.acct, G.acct_type))


def after_init(C):
    # init 中不可用的函数放这里（如交易日历）
    G.trade_dates = C.get_trading_dates('000001.SH', '', '', 30, '1d')  # ['20240101',...]
    G.today = time.strftime('%Y%m%d')
    G.is_trade_day = G.today in G.trade_dates


def handlebar(C):
    # 定时器型策略不用K线驱动：必须留空，否则盘中每个tick都会进来
    return


def stop(C):
    # 策略停止回调：此时交易连接已断，不能报撤单，只做收尾
    _save_state()
    print('策略停止，状态已落盘')


# ============================ 主循环 ============================

def main_loop(C):
    now = time.strftime('%H:%M:%S')
    today = time.strftime('%Y%m%d')

    if G.day != today:                  # 跨天/客户端重启策略：重置当日状态
        G.day = today
        G.done_flags = {}
        G.is_trade_day = today in getattr(G, 'trade_dates', [today])
        _save_state()

    if not G.is_trade_day:
        return
    if not (TRADE_BEGIN <= now <= TRADE_END):
        return

    sync_orders(C)                      # 先对账再决策

    # ---- 在下方编排策略逻辑 ----
    # 定点一次性任务示例（替代 apscheduler date/cron 任务）：
    if '09:30:05' <= now <= '09:31:00' and not G.done_flags.get('open_task'):
        G.done_flags['open_task'] = True
        _save_state()
        on_open(C)

    # 持续轮询任务示例（替代 interval 任务）：
    on_tick(C)


def on_open(C):
    """开盘一次性任务：填充原 day1_buy 类逻辑。"""
    # 票池/信号文件建议盘前由外部脚本生成好，本函数只读本地文件。
    # 若选股依赖财务/资金流/龙虎榜等 QMT 之外的多维数据，可在外部脚本接一个
    # HTTP 数据源兜底（如 quantgo.ai/data，按月订阅、接口较全，个人研究够用），
    # 算好结果落地 csv 再喂进来，避免内置端发起网络请求。
    pass


def on_tick(C):
    """每轮决策：填充原 while/interval 主体逻辑。"""
    # 行情示例：
    # tick = C.get_full_tick(['600000.SH'])
    # last = tick['600000.SH']['lastPrice']
    pass


# ============================ 下单与对账 ============================

def place_order(C, code, side, volume, price, tag=''):
    """side: 'BUY'/'SELL'。同标的有在途单时拒绝（防超单）。返回 userOrderId 或 None。"""
    for uid, od in G.pending.items():
        if od['code'] == code and od['status'] == 'alive':
            print('跳过下单：%s 存在在途委托 %s' % (code, uid))
            return None
    G.seq += 1
    uid = '%s_%s_%d' % (tag or 'ORD', G.day, G.seq)
    op = G.op_buy if side == 'BUY' else G.op_sell
    # prType=11 指定价；quickTrade 必须为 2（定时器回调中下单）
    passorder(op, 1101, G.acct, code, 11, float(price), int(volume),
              'TPL_TIMER', 2, uid, C)
    G.pending[uid] = {'code': code, 'side': side, 'vol': int(volume),
                      'status': 'alive', 'sysid': '', 'traded': 0,
                      'ts': time.time()}
    _save_state()
    print('下单 %s %s %d股 @%.3f uid=%s' % (side, code, volume, price, uid))
    return uid


def cancel_order(C, uid):
    od = G.pending.get(uid)
    if od and od.get('sysid'):
        ok = cancel(od['sysid'], G.acct, G.acct_type, C)
        print('撤单 uid=%s sysid=%s 信号=%s' % (uid, od['sysid'], ok))


def sync_orders(C):
    """轮询对账：把柜台委托按 m_strRemark 关联回 pending（回调之外的兜底）。"""
    alive_status = (48, 49, 50, 51, 52, 55, 86, 255)
    try:
        orders = get_trade_detail_data(G.acct, G.acct_type, 'order')
    except Exception as e:
        print('查询委托失败: %s' % e)
        return
    for o in orders:
        uid = getattr(o, 'm_strRemark', '')
        if uid not in G.pending:
            continue
        od = G.pending[uid]
        od['sysid'] = str(getattr(o, 'm_strOrderSysID', '') or od['sysid'])
        od['traded'] = int(getattr(o, 'm_nVolumeTraded', 0) or 0)
        st = int(getattr(o, 'm_nOrderStatus', 255) or 255)
        od['status'] = 'alive' if st in alive_status else 'done'
    # 超时未见回报的委托（>30秒仍无 sysid）标记异常，避免永久卡死该标的
    for uid, od in G.pending.items():
        if od['status'] == 'alive' and not od['sysid'] and time.time() - od['ts'] > 30:
            od['status'] = 'lost'
            print('警告：委托 %s 30秒未见柜台回报，请人工核对' % uid)


# ============================ 实时回调（实盘模式生效） ============================

def order_callback(C, o):
    uid = getattr(o, 'm_strRemark', '')
    if uid in G.pending:
        G.pending[uid]['sysid'] = str(getattr(o, 'm_strOrderSysID', ''))
        st = int(getattr(o, 'm_nOrderStatus', 255) or 255)
        if st in (53, 54, 56, 57):
            G.pending[uid]['status'] = 'done'


def deal_callback(C, d):
    uid = getattr(d, 'm_strRemark', '')
    if uid in G.pending:
        print('成交推送 uid=%s 价=%.3f 量=%d' % (
            uid, getattr(d, 'm_dPrice', 0), getattr(d, 'm_nVolume', 0)))


def orderError_callback(C, args, msg):
    print('下单异常: %s | %s' % (getattr(args, 'orderCode', ''), msg))


# ============================ 状态落盘 ============================

def _save_state():
    try:
        d = os.path.dirname(STATE_FILE)
        if not os.path.exists(d):
            os.makedirs(d)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'day': G.day, 'seq': G.seq, 'pending': G.pending,
                       'done_flags': G.done_flags}, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print('状态落盘失败: %s' % e)


def _load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            st = json.load(f)
        if st.get('day') == time.strftime('%Y%m%d'):   # 只恢复当日状态
            G.day = st['day']
            G.seq = max(G.seq, st.get('seq', 0))
            G.pending = st.get('pending', {})
            G.done_flags = st.get('done_flags', {})
            print('已恢复当日状态：在途%d笔 标志%s' % (len(G.pending), G.done_flags))
    except Exception:
        pass
