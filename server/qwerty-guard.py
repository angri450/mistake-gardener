#!/usr/bin/env python3
"""qwerty 站点暴力破解防护:按 caddy 访问日志自动封禁来源 IP。

背景(2026-09-15 实测环境):
  * 本机没有 fail2ban,dnf 仓库里也没有这个包;
  * Caddy 2.10.2 没有官方 rate_limit 指令(要插件,发行版包换不了);
  * firewalld 在役且支持 rich rule。
所以用"读 caddy JSON 访问日志 -> 统计 401 -> firewalld 临时 drop"这条最短路。

规则:
  窗口 300s 内,单个 IP 在受保护站点(8080 owner / 8505 judge)累计 401 >= 8 次 -> 封。
  首次封 15 分钟,再犯翻倍(1h / 6h),上限 24h;到期自动解封。
  白名单与私网段不封(防止把自己或内网锁死)。
  全局 401 速率也统计,超 200/5min 只告警不自动动作(分布式爆破不能靠单 IP 封禁解决)。

用法:
  python3 /root/qwerty-guard.py              # 正常跑一次(由 systemd timer 每 30s 调)
  python3 /root/qwerty-guard.py --dry-run    # 只报告不动作
  python3 /root/qwerty-guard.py --simulate 203.0.113.9=9   # 自测封禁链路(安全 IP,无副作用)
  python3 /root/qwerty-guard.py --unblock 203.0.113.9      # 手动解封
"""
import datetime
import json
import os
import subprocess
import sys
import time

LOG = '/var/log/caddy/access.log'
STATE = '/root/qwerty-guard-state.json'
REPORT = '/root/qwerty-guard.log'
WINDOW = 300
THRESHOLD = 8
FIRST_BLOCK = 900
MAX_BLOCK = 86400
GLOBAL_ALERT = 200
PORTS = {'8080', '8505'}
WHITELIST = {'127.0.0.1', '::1'}
PRIVATE = ('10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.', '172.2', '172.30.', '172.31.')


def log(msg):
    line = '%s %s' % (datetime.datetime.now().strftime('%F %T'), msg)
    print(line)
    try:
        with open(REPORT, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_state():
    try:
        with open(STATE, encoding='utf-8') as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault('offset', 0)
    st.setdefault('inode', 0)
    st.setdefault('fails', {})
    st.setdefault('blocks', {})
    return st


def save_state(st):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def skip_ip(ip):
    return (not ip) or ip in WHITELIST or ip.startswith(PRIVATE)


def add_drop(ip):
    rule = 'rule family="ipv4" source address="%s" drop' % ip
    subprocess.run(['firewall-cmd', '--add-rich-rule', rule], capture_output=True)
    return rule


def remove_drop(ip):
    rule = 'rule family="ipv4" source address="%s" drop' % ip
    subprocess.run(['firewall-cmd', '--remove-rich-rule', rule], capture_output=True)
    return rule


def read_new_lines(st):
    """增量读日志;inode 变化或文件回退(轮转)时从头读"""
    if not os.path.exists(LOG):
        return []
    stt = os.stat(LOG)
    if stt.st_ino != st['inode'] or stt.st_size < st['offset']:
        log('日志已轮转(inode %s -> %s),重置读取位置' % (st['inode'], stt.st_ino))
        st['offset'] = 0
        st['inode'] = stt.st_ino
    out = []
    with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
        f.seek(st['offset'])
        for line in f:
            out.append(line)
        st['offset'] = f.tell()
    return out


def main():
    dry = '--dry-run' in sys.argv
    now = time.time()
    st = load_state()

    # 自测:往窗口里注入失败记录,验证封禁链路
    for i, a in enumerate(sys.argv):
        if not a.startswith('--simulate'):
            continue
        if '=' in a:
            arg = a.split('=', 1)[1]
        else:
            arg = sys.argv[i + 1] if i + 1 < len(sys.argv) else '203.0.113.9=9'
        ip, _, n = arg.partition('=')
        n = int(n or 9)
        st['fails'].setdefault(ip, [])
        st['fails'][ip].extend([now] * n)
        log('自测注入 %s 失败 %d 次' % (ip, n))
    for a in sys.argv:
        if a == '--unblock':
            ip = sys.argv[sys.argv.index(a) + 1]
            remove_drop(ip)
            st['blocks'].pop(ip, None)
            log('手动解封 %s' % ip)
            save_state(st)
            return

    lines = read_new_lines(st)
    added = 0
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        req = r.get('request') or {}
        host = req.get('host') or ''
        port = host.rsplit(':', 1)[-1]
        ip = r.get('client_ip') or req.get('client_ip') or req.get('remote_ip')
        if r.get('status') == 401 and port in PORTS and not skip_ip(ip):
            st['fails'].setdefault(ip, []).append(r.get('ts') or now)
            added += 1

    # 清理窗口外的记录与已到期的封禁
    for ip in list(st['fails']):
        st['fails'][ip] = [t for t in st['fails'][ip] if now - t <= WINDOW]
        if not st['fails'][ip]:
            st['fails'].pop(ip)
    for ip in list(st['blocks']):
        if st['blocks'][ip].get('until', 0) <= now:
            if not dry:
                remove_drop(ip)
            log('解封 %s(到期)' % ip)
            st['blocks'].pop(ip)

    total = sum(len(v) for v in st['fails'].values())
    if total >= GLOBAL_ALERT:
        log('告警:窗口内全局 401 共 %d 次,疑似分布式爆破(单 IP 封禁无效,人工看一下)' % total)

    for ip, ts_list in sorted(st['fails'].items(), key=lambda x: -len(x[1])):
        if len(ts_list) < THRESHOLD:
            continue
        b = st['blocks'].get(ip, {'strikes': 0})
        b['strikes'] = b.get('strikes', 0) + 1
        dur = min(FIRST_BLOCK * (2 ** (b['strikes'] - 1)), MAX_BLOCK)
        b['until'] = now + dur
        st['blocks'][ip] = b
        st['fails'].pop(ip, None)
        if dry:
            log('[dry-run] 将封禁 %s %d 秒(第 %d 次,窗口内 %d 次失败)' % (ip, dur, b['strikes'], len(ts_list)))
        else:
            add_drop(ip)
            log('封禁 %s %d 秒(第 %d 次违规,窗口内 %d 次 401)' % (ip, dur, b['strikes'], len(ts_list)))

    st['last_run'] = now
    if not dry:
        save_state(st)
    alive = ', '.join('%s(%ds)' % (ip, int(v.get('until', 0) - now)) for ip, v in st['blocks'].items())
    log('本轮:新增失败 %d 条,窗口内失败 IP %d 个,当前封禁 %s' % (
        added, len(st['fails']), alive or '无'))


main()
