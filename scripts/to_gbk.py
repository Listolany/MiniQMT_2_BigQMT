# -*- coding: utf-8 -*-
"""把转换后的策略安全转存为 GBK（大QMT内置端要求）。

用法: python to_gbk.py <输入.py> <输出.py>

做四件事：解码(UTF-8优先) → GBK可编码校验(逐行报错) → 编译自检 → GBK落盘+回读验证。
不要用编辑器直接改写 GBK 文件，本脚本是唯一安全路径。
"""
import os
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


def main(src_path, dst_path):
    raw = open(src_path, 'rb').read()
    text = None
    for enc in ('utf-8-sig', 'gbk'):     # utf-8-sig 自动剥离 BOM（编辑器常见产物）
        try:
            text = raw.decode(enc)
            print('源编码: %s' % enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        print('FAIL: 无法以 UTF-8/GBK 解码源文件')
        return 1
    text = text.lstrip('\ufeff')

    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        try:
            line.encode('gbk')
        except UnicodeEncodeError as e:
            bad.append((i, str(e)))
    if bad:
        print('FAIL: %d 行含 GBK 不可编码字符:' % len(bad))
        for ln, msg in bad[:10]:
            print('  L%d: %s' % (ln, msg))
        return 1

    try:
        compile(text, dst_path, 'exec')
    except SyntaxError as e:
        print('FAIL: 编译错误 %s' % e)
        return 1

    with open(dst_path, 'w', encoding='gbk', newline='') as f:
        f.write(text)

    back = open(dst_path, 'rb').read().decode('gbk')
    if back != text:
        print('FAIL: 回读校验不一致')
        return 1
    if '?' * 3 in back and '?' * 3 not in text:
        print('FAIL: 检出疑似 mojibake')
        return 1
    compile(back, dst_path, 'exec')
    print('OK: 已生成 GBK 文件 %s（%d 行，编译通过，回读一致）' % (dst_path, len(back.splitlines())))
    print('下一步: 全文粘贴到大QMT策略编辑器，确认中文注释显示正常后保存编译')
    return 0


if __name__ == '__main__':
    _fix_console()
    if len(sys.argv) != 3:
        print('用法: python to_gbk.py <输入.py> <输出.py>')
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
