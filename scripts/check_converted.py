# -*- coding: utf-8 -*-
"""转换后策略校验：py3.6/GBK/内置框架合规。全部 PASS 才可交付。

用法: python check_converted.py <转换后策略.py>
退出码: 0=PASS  1=FAIL
"""
import ast
import os
import re
import sys


def _fix_console():
    if os.name != 'nt':
        return
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        sys.stdout.reconfigure(encoding='utf-8' if cp == 65001 else 'gbk',
                               errors='replace')
    except Exception:
        pass

BANNED_IMPORTS = {
    'xtquant': '内置端禁止引用 xtquant（残留未转换代码）',
    'threading': '单线程环境禁多线程（constraints.md A4）',
    'multiprocessing': '禁多进程（A4）',
    'asyncio': '禁协程（A4）',
    'apscheduler': '调度器须改 C.run_time（api_mapping.md 第6节）',
    'AutoLogin': '删除 AutoLogin（constraints.md B3）',
}
SYS_FUNCS = ('init', 'after_init', 'handlebar', 'stop', 'account_callback',
             'order_callback', 'deal_callback', 'position_callback',
             'orderError_callback', 'task_callback')


def read_source(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'gbk'):     # utf-8-sig 自动剥离 BOM
        try:
            return raw.decode(enc).lstrip('\ufeff'), enc
        except UnicodeDecodeError:
            continue
    return None, None


def main(path):
    errors, warns = [], []
    src, enc = read_source(path)
    if src is None:
        print('[FAIL] 文件无法以 UTF-8/GBK 解码')
        return 1

    # 1. GBK 头与可编码性
    head = '\n'.join(src.splitlines()[:2])
    if not re.search(r'coding[:=]\s*gbk', head, re.I):
        errors.append('缺少 #coding:gbk 文件头（必须在前两行）')
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        try:
            line.encode('gbk')
        except UnicodeEncodeError:
            bad.append(i)
    if bad:
        errors.append('存在 GBK 不可编码字符，行号: %s（替换 emoji/特殊符号）' % bad[:10])
    if enc != 'gbk':
        warns.append('当前为 UTF-8 编码：交付前运行 to_gbk.py 转存')

    # 2. 语法解析
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        errors.append('语法错误: %s' % e)
        return report(errors, warns)

    # 3. py3.6 上限
    for node in ast.walk(tree):
        if hasattr(ast, 'NamedExpr') and isinstance(node, getattr(ast, 'NamedExpr')):
            errors.append('L%d 海象运算符 :=（py3.8）' % node.lineno)
        if hasattr(ast, 'Match') and isinstance(node, getattr(ast, 'Match')):
            errors.append('L%d match 语句（py3.10）' % node.lineno)
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            errors.append('L%d async/await 不可用' % node.lineno)
        if isinstance(node, (ast.FunctionDef,)) and getattr(node.args, 'posonlyargs', None):
            errors.append('L%d 位置仅参数 /（py3.8）' % node.lineno)
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r'f["\'][^"\']*\{[^{}]*=\}', line):
            errors.append("L%d f-string {x=}（py3.8）" % i)
        if re.search(r'^\s*(from\s+dataclasses|import\s+dataclasses)', line):
            errors.append('L%d dataclasses（py3.7）' % i)

    # 4. 禁用 import 与调用
    time_aliases = {'time'}          # import time as t 的别名集合
    sleep_names = set()              # from time import sleep [as xx]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mod = a.name.split('.')[0]
                if mod in BANNED_IMPORTS:
                    errors.append('L%d import %s —— %s' % (node.lineno, mod, BANNED_IMPORTS[mod]))
                if a.name == 'time':
                    time_aliases.add(a.asname or 'time')
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or '').split('.')[0]
            if mod in BANNED_IMPORTS:
                errors.append('L%d from %s import —— %s' % (node.lineno, mod, BANNED_IMPORTS[mod]))
            if node.module == 'time':
                for a in node.names:
                    if a.name == 'sleep':
                        sleep_names.add(a.asname or 'sleep')
                        errors.append('L%d from time import sleep —— 阻塞全部策略，改状态机（A4）'
                                      % node.lineno)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        full = ''
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            full = '%s.%s' % (fn.value.id, fn.attr)
            if fn.attr == 'sleep' and fn.value.id in time_aliases:
                errors.append('L%d %s —— 阻塞全部策略，改状态机（A4）' % (node.lineno, full))
        elif isinstance(fn, ast.Name):
            full = fn.id
            if full in sleep_names:
                errors.append('L%d sleep() —— 阻塞全部策略，改状态机（A4）' % node.lineno)
        if full == 'input':
            errors.append('L%d input() 不可用' % node.lineno)
        if full in ('os.startfile',):
            warns.append('L%d os.startfile —— 确认确需在策略内拉起外部程序' % node.lineno)

    # 5. 框架结构
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if 'init' not in funcs:
        errors.append('缺少 init(ContextInfo) 入口函数')
    elif len(funcs['init'].args.args) != 1:
        errors.append('init 必须只有一个参数（ContextInfo）')
    if '__main__' in src:
        warns.append("检出 if __name__ == '__main__'：内置端不会执行，确认仅用于外部自测")

    # 6. passorder / cancel 参数个数
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            n = len(node.args)
            if node.func.id == 'passorder' and n != 11:
                errors.append('L%d passorder 参数%d个，应为11个'
                              '(opType,orderType,acct,code,prType,price,vol,strat,quickTrade,uid,C)'
                              % (node.lineno, n))
            if node.func.id == 'cancel' and n != 4:
                errors.append('L%d cancel 参数%d个，应为4个(sysid,acct,acctType,C)' % (node.lineno, n))
            if node.func.id == 'get_trade_detail_data' and n not in (3, 4):
                errors.append('L%d get_trade_detail_data 参数%d个，应为3或4个' % (node.lineno, n))

    # 7. quickTrade 检查：定时器/回调中 passorder 第9参须为2（静态近似：检查所有调用）
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == 'passorder' and len(node.args) == 11:
            qt = node.args[8]
            if isinstance(qt, ast.Constant) and qt.value not in (2,):
                warns.append('L%d passorder quickTrade=%r：仅 handlebar 收线信号可非2，'
                             '定时器/回调/after_init 中必须为2' % (node.lineno, qt.value))

    # 8. ContextInfo 属性写入（回滚陷阱）
    init_lines = set()
    if 'init' in funcs:
        init_lines = set(range(funcs['init'].lineno, funcs['init'].end_lineno + 1))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id in ('C', 'ContextInfo') \
                        and t.attr not in ('start', 'end', 'capital'):
                    if node.lineno not in init_lines:
                        warns.append('L%d 对 ContextInfo 属性赋值（%s）：盘中会被逐K线回滚，'
                                     '可变状态改存全局 G（constraints.md A5）' % (node.lineno, t.attr))

    # 9. init 中调用受限函数
    if 'init' in funcs:
        for node in ast.walk(funcs['init']):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else \
                    (node.func.id if isinstance(node.func, ast.Name) else '')
                if name == 'get_trading_dates':
                    errors.append('L%d get_trading_dates 在 init 中不可用，移到 after_init' % node.lineno)
                if name == 'get_market_data_ex':
                    warns.append('L%d get_market_data_ex 在 init 中仅能取本地数据' % node.lineno)

    return report(errors, warns)


def report(errors, warns):
    for e in errors:
        print('[FAIL] %s' % e)
    for x in warns:
        print('[WARN] %s' % x)
    if errors:
        print('\n结果: FAIL（%d项错误，%d项警告）—— 修复后重跑' % (len(errors), len(warns)))
        return 1
    print('\n结果: PASS（%d项警告）' % len(warns))
    return 0


if __name__ == '__main__':
    _fix_console()
    if len(sys.argv) != 2:
        print('用法: python check_converted.py <策略.py>')
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
