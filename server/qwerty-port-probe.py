#!/usr/bin/env python3
"""探测云主机上哪些端口真的能从公网进(区分"安全组挡住"与"本机没服务")。

背景:2026-09-15 评委入口配在 8505 上打不开,本机一切正常(firewalld 已放行、caddy 在听、回环 401),
真正原因是腾讯云安全组只放行了白名单端口。这个方法用 TCP 三态判断,不用等用户反馈:
  连通   -> 安全组已放行(且本机有服务)
  拒绝   -> 安全组已放行,只是本机没服务(可拿来直接用)
  超时   -> 安全组未放行
用法: python3 /root/qwerty-port-probe.py 49.232.197.22 80 443 8080 8505 8504 8517 22716
"""
import socket
import sys
import time

host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
ports = [int(x) for x in sys.argv[2:]] or [80, 443, 8080, 8505, 8504, 8517, 22716]
print('%-7s %-22s %s' % ('端口', '结果', '含义'))
for p in ports:
    s = socket.socket()
    s.settimeout(5)
    t = time.time()
    try:
        s.connect((host, p))
        print('%-7d %-22s %s' % (p, '连通 %.2fs' % (time.time() - t), '安全组已放行'))
    except socket.timeout:
        print('%-7d %-22s %s' % (p, 'timeout', '安全组未放行'))
    except ConnectionRefusedError:
        print('%-7d %-22s %s' % (p, 'refused', '已放行但本机无服务(可征用)'))
    except Exception as e:
        print('%-7d %-22s %s' % (p, type(e).__name__, str(e)[:40]))
    finally:
        s.close()
