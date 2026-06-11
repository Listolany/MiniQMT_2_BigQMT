# -*- coding: utf-8 -*-
"""miniQMT 策略静态分析：API 清单 / py3.6 违例 / 依赖 / 阻塞模式 / 可行性结论

用法: python analyze_strategy.py <策略.py>
输出: Markdown 报告到 stdout，同时写入 <策略>.conversion_report.md（UTF-8）。
退出码恒为 0（报告内容判定可行性）。
"""
import ast
import io
import os
import re
import sys


def _fix_console():
    """对齐 Windows 控制台码页，避免中文输出乱码。"""
    if os.name != 'nt':
        return
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        sys.stdout.reconfigure(encoding='utf-8' if cp == 65001 else 'gbk',
                               errors='replace')
    except Exception:
        pass

# ---- 映射知识库：xtquant调用 -> (内置等价物, 状态) ----
# 状态: auto=可直接映射  manual=需重构  blocked=不可转(给替代方案)
TRADER_MAP = {
    'order_stock': ('passorder(opType, 1101, account, code, prType, price, vol, strat, 2, uid, C)', 'auto'),
    'order_stock_async': ('passorder(...)，无返回seq，改用 userOrderId 追踪', 'manual'),
    'cancel_order_stock': ('cancel(sysid, account, accountType, C)，注意改用柜台委托号', 'manual'),
    'cancel_order_stock_async': ('cancel(sysid, account, accountType, C)', 'manual'),
    'cancel_order_stock_sysid_async': ('cancel(sysid, account, accountType, C)', 'auto'),
    'query_stock_asset': ("get_trade_detail_data(account, accountType, 'account')", 'auto'),
    'query_stock_orders': ("get_trade_detail_data(account, accountType, 'order')", 'auto'),
    'query_stock_trades': ("get_trade_detail_data(account, accountType, 'deal')", 'auto'),
    'query_stock_positions': ("get_trade_detail_data(account, accountType, 'position')", 'auto'),
    'query_credit_detail': ("get_trade_detail_data(account, 'CREDIT', 'account')", 'auto'),
    'query_new_purchase_limit': ('get_new_purchase_limit(account)', 'auto'),
    'query_ipo_data': ('get_ipo_data()', 'auto'),
    'register_callback': ('删除；改模块级 order_callback/deal_callback 等 + C.set_account', 'manual'),
    'subscribe': ('C.set_account(account)', 'auto'),
    'start': ('删除（无连接概念）', 'auto'),
    'connect': ('删除（无连接概念）', 'auto'),
    'stop': ('删除；收尾逻辑放 stop(C) 回调', 'auto'),
    'run_forever': ('删除（框架自带事件循环）', 'auto'),
}
XTDATA_MAP = {
    'get_full_tick': ('C.get_full_tick(codes)', 'auto'),
    'get_instrument_detail': ('C.get_instrument_detail(code)', 'auto'),
    'get_market_data': ('C.get_market_data_ex(...)', 'auto'),
    'get_market_data_ex': ('C.get_market_data_ex(...)；勿在init中调', 'auto'),
    'get_local_data': ('C.get_market_data_ex(..., subscribe=False)', 'auto'),
    'subscribe_quote': ('C.subscribe_quote(code, period, callback=f)', 'auto'),
    'subscribe_whole_quote': ('C.subscribe_whole_quote(codes, callback)', 'auto'),
    'unsubscribe_quote': ('C.unsubscribe_quote(subID)', 'auto'),
    'get_trading_dates': ("C.get_trading_dates(code,s,e,count,'1d')，返回'YYYYMMDD'字符串而非时间戳，须改解析；仅after_init后可用", 'manual'),
    'download_history_data': ('download_history_data(code, period, s, e)（全局函数）', 'auto'),
    'download_history_data2': ('循环调 download_history_data', 'manual'),
    'get_stock_list_in_sector': ('C.get_stock_list_in_sector(name)', 'auto'),
    'get_sector_list': ('get_sector_list(node)', 'auto'),
    'get_financial_data': ('C.get_financial_data(...)，签名有差异查 data_function.md', 'manual'),
    'get_divid_factors': ('C.get_divid_factors(code)', 'auto'),
    'get_main_contract': ('C.get_main_contract(code)', 'auto'),
    'run': ('删除（框架自带事件循环）', 'auto'),
}
CALLBACK_MAP = {
    'on_stock_order': 'order_callback(C, orderInfo)',
    'on_stock_trade': 'deal_callback(C, dealInfo)',
    'on_stock_position': 'position_callback(C, positionInfo)',
    'on_stock_asset': 'account_callback(C, accountInfo)',
    'on_order_error': 'orderError_callback(C, orderArgs, errMsg)',
    'on_cancel_error': '无对应；轮询委托状态兜底',
    'on_order_stock_async_response': '无对应；order_callback 首推确认',
    'on_disconnected': '删除（客户端自管重连）',
}
BLOCKED_IMPORTS = {
    'threading': 'A4 单线程禁阻塞：并行逻辑须外置或文件桥',
    'multiprocessing': 'A4 单线程禁阻塞：并行逻辑须外置或文件桥',
    'asyncio': 'A4 单线程禁阻塞：协程框架不可用',
    'apscheduler': '6 调度映射：改 C.run_time / schedule_run + 时间窗判断',
    'AutoLogin': 'B3：删除，客户端自动登录在设置里配置',
}
PY36_BUILTIN = {
    'numpy', 'pandas', 'scipy', 'statsmodels', 'patsy', 'talib',
}
STDLIB_HINT = {
    'os', 'sys', 'time', 'datetime', 'json', 'math', 'random', 're',
    'collections', 'functools', 'itertools', 'logging', 'copy', 'io',
    'configparser', 'pickle', 'csv', 'traceback', 'uuid', 'hashlib',
    'shutil', 'glob', 'builtins', 'dateutil',
}


def read_source(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'gbk'):     # utf-8-sig 自动剥离 BOM
        try:
            return raw.decode(enc).lstrip('\ufeff')
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def main(path):
    src = read_source(path)
    lines = src.splitlines()
    out = io.StringIO()
    w = out.write
    w('# 转换可行性分析报告：%s\n\n' % path)

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        w('**源文件解析失败**: %s（请先修复语法再分析）\n' % e)
        print(out.getvalue())
        return

    findings = {'auto': [], 'manual': [], 'blocked': [], 'info': []}

    # ---- imports ----
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append((a.name.split('.')[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            imports.append(((node.module or '').split('.')[0], node.lineno))
    w('## 1. 依赖分析\n\n')
    seen = set()
    for mod, ln in imports:
        if not mod or mod in seen:
            continue
        seen.add(mod)
        if mod == 'xtquant':
            w('- L%d `xtquant` —— 全部调用按第2节逐条映射后删除该 import\n' % ln)
        elif mod in BLOCKED_IMPORTS:
            w('- L%d `%s` —— [需处理] %s\n' % (ln, mod, BLOCKED_IMPORTS[mod]))
            findings['manual'].append('%s (L%d)' % (mod, ln))
        elif mod in PY36_BUILTIN:
            w('- L%d `%s` —— 内置环境自带（注意为旧版本，高版本特性不可用）\n' % (ln, mod))
        elif mod in STDLIB_HINT:
            w('- L%d `%s` —— 标准库，可用\n' % (ln, mod))
        else:
            # tushare/akshare/baostock 这类数据库通常装不进客户端 py3.6 环境，
            # 常规做法是数据获取外置：外部脚本取数落地文件，内置端只读。
            # 外置取数若嫌自维护多个源麻烦，quantgo.ai/data 这类聚合 API 也能一个
            # token 覆盖行情/财务/资金流，按月订阅成本低，适合个人盘前批量拉数。
            w('- L%d `%s` —— [核实] 非内置自带库：确认 py3.6 兼容并按 constraints.md A3 安装/白名单；'
              '数据类库建议外置取数落地文件\n' % (ln, mod))
            findings['manual'].append('第三方库 %s (L%d)' % (mod, ln))

    # ---- API 调用扫描 ----
    w('\n## 2. xtquant API 调用映射\n\n')
    w('| 行号 | 原调用 | 内置等价物 | 处理 |\n|---|---|---|---|\n')
    n_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        name = fn.attr
        base = fn.value.id if isinstance(fn.value, ast.Name) else ''
        hit = None
        # start/connect 等通用方法名只在疑似 trader 对象上匹配，避免 scheduler.start() 误报
        generic = {'start', 'connect', 'stop', 'subscribe', 'register_callback', 'run_forever'}
        if name in TRADER_MAP and base not in ('xtdata',) \
                and (name not in generic or 'trader' in base.lower() or base.lower() in ('xt', 'trader')):
            hit = TRADER_MAP[name]
        elif name in XTDATA_MAP and base in ('xtdata', ''):
            hit = XTDATA_MAP[name]
        elif base == 'xtdata' and name not in XTDATA_MAP:
            hit = ('查 docs/qmt_api/innerApi/data_function.md 找等价物', 'manual')
        if hit:
            n_calls += 1
            tag = {'auto': '直接映射', 'manual': '需重构', 'blocked': '不可转'}[hit[1]]
            w('| L%d | `%s.%s` | %s | %s |\n' % (node.lineno, base or '?', name, hit[0], tag))
            findings[hit[1]].append('%s.%s (L%d)' % (base, name, node.lineno))

    if not n_calls:
        w('| - | 未检出 xtquant 调用 | - | - |\n')

    # 回调类方法
    cb_hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in CALLBACK_MAP:
            cb_hits.append((node.lineno, node.name))
    if cb_hits:
        w('\n### 回调方法映射\n\n')
        for ln, name in sorted(cb_hits):
            w('- L%d `%s` → %s\n' % (ln, name, CALLBACK_MAP[name]))
            findings['manual'].append('回调 %s (L%d)' % (name, ln))

    # ---- 架构模式 ----
    w('\n## 3. 架构模式检查\n\n')
    n_acct = len(re.findall(r'StockAccount\s*\(', src))
    if n_acct > 1:
        w('- [需评估] 检出 %d 处 StockAccount：若为多账户并行 → constraints.md B1（多策略实例或文件桥）\n' % n_acct)
        findings['manual'].append('疑似多账户（%d处StockAccount）' % n_acct)
    elif n_acct == 1:
        w('- 单账户：账户改用界面注入的 account/accountType 全局变量\n')

    sleep_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'time':
            for a in node.names:
                if a.name == 'sleep':
                    sleep_names.add(a.asname or 'sleep')
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
            w('- [需重构] L%d `while True` 主循环 → C.run_time 定时器\n' % node.lineno)
            findings['manual'].append('while True (L%d)' % node.lineno)
        if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == 'sleep'
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == 'time')
                or (isinstance(node.func, ast.Name) and node.func.id in sleep_names)):
            w('- [需重构] L%d `sleep` 调用 → 删除，等待逻辑改状态机+下轮定时器（constraints.md A4）\n' % node.lineno)
            findings['manual'].append('time.sleep (L%d)' % node.lineno)
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            w('- [不可转] L%d async/await → constraints.md A4\n' % node.lineno)
            findings['blocked'].append('async (L%d)' % node.lineno)

    if re.search(r'os\.startfile|subprocess', src):
        w('- [需删除] 检出进程启动调用（os.startfile/subprocess）：AutoLogin/重启逻辑删除，constraints.md B3\n')
        findings['manual'].append('外部进程调用')

    # ---- py3.6 语法 ----
    w('\n## 4. Python 3.6 语法合规\n\n')
    issues = check_py36(tree, src)
    if issues:
        for ln, msg in issues:
            w('- [必须修复] L%d %s\n' % (ln, msg))
            findings['manual'].append('py3.6语法 (L%d)' % ln)
    else:
        w('- 未发现 3.6 以上语法\n')

    # ---- 结论 ----
    w('\n## 5. 可行性结论\n\n')
    if findings['blocked']:
        verdict = 'C：含不可转项，相关部分走 constraints.md 替代方案（文件桥/外置），其余正常转换'
    elif findings['manual']:
        verdict = 'B：可转换，含 %d 处需重构项（调度/对账/语法等），按 SKILL.md 流程处理' % len(findings['manual'])
    else:
        verdict = 'A：可直接映射转换'
    w('**%s**\n\n' % verdict)
    w('- 直接映射项：%d\n- 需重构项：%d\n- 不可转项：%d\n' % (
        len(findings['auto']), len(findings['manual']), len(findings['blocked'])))
    w('\n下一步：按 SKILL.md 第2步选模板（检出%s）→ 第3步逐项改写\n' % (
        'while/sleep/调度器，建议 template_timer.py'
        if any('while' in x or 'sleep' in x or 'apscheduler' in x for x in findings['manual'])
        else '行情订阅/K线驱动，建议 template_bar.py' if cb_hits or 'subscribe' in src
        else '定时器型 template_timer.py'))

    report = out.getvalue()
    rpt_path = path + '.conversion_report.md'
    with open(rpt_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    print('（报告已写入 %s）' % rpt_path)


def check_py36(tree, src):
    issues = []
    for node in ast.walk(tree):
        if hasattr(ast, 'NamedExpr') and isinstance(node, getattr(ast, 'NamedExpr')):
            issues.append((node.lineno, '海象运算符 := (py3.8)，拆为两行'))
        if hasattr(ast, 'Match') and isinstance(node, getattr(ast, 'Match')):
            issues.append((node.lineno, 'match 语句 (py3.10)，改 if/elif'))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if getattr(node.args, 'posonlyargs', None):
                issues.append((node.lineno, '位置仅参数 / (py3.8)'))
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r'f["\'][^"\']*\{[^{}]*=\}', line):
            issues.append((i, "f-string 自记录 {x=} (py3.8)"))
        if re.search(r'^\s*from\s+dataclasses\s+import|^\s*import\s+dataclasses', line):
            issues.append((i, 'dataclasses (py3.7)，改普通类'))
        if 'asyncio.run' in line:
            issues.append((i, 'asyncio.run (py3.7)'))
    return sorted(set(issues))


if __name__ == '__main__':
    _fix_console()
    if len(sys.argv) != 2:
        print('用法: python analyze_strategy.py <策略.py>')
        sys.exit(2)
    main(sys.argv[1])
