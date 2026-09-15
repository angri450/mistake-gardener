#!/usr/bin/env python3
"""用新判定规则重放历史错题记录,交叉验证归因报告第 4 条的口径。

新规则(2026-09-15 实装的 P1):
  1) 目标串里落在空格/标点上的位置由程序自动判对 -> 记在该位置的错误事件不再算错
  2) 用户敲出的空格/标点被直接忽略 -> 按错字符是空格(U+2423)或标点的事件不再算错
  3) 其余(字母/数字打错)仍算错
输入: /home/agentxfer/collect.jsonl(只读)
"""
import io
import json
import statistics
import datetime as dt

YT = '␣'
PUNCT = set(',.;:!?\'"()[]{}-–—…“”‘’/@#*+=<>_ ')


def is_skip_target(ch):
    return ch is None or ch in PUNCT or ch == YT


def is_skip_typed(ch):
    return ch is None or ch in PUNCT or ch == YT


rows = []
for line in io.open('/home/agentxfer/collect.jsonl', encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if isinstance(r, dict) and 'word' in r:
        rows.append(r)

# 剔除本轮回归产生的合成记录(恒速 + 09-15 之后),与生成端口径一致
def synth(r):
    t = r.get('timing') or []
    return len(t) >= 8 and statistics.pstdev(t) < 3


real = [r for r in rows if not synth(r)
        and dt.datetime.fromtimestamp(r['timeStamp']).date() < dt.date(2026, 9, 15)]

print('总行数 %d | 含 word %d | 剔恒速与 09-15 后参与重放 %d' % (len(rows), len(rows), len(real)))


def replay(r):
    """返回 (旧事件数, 新事件数, 被规则1豁免, 被规则2豁免, 仍算错的明细)"""
    word = r['word']
    old_ev = sum(len(v or []) for v in (r.get('mistakes') or {}).values())
    keep = 0
    ex_target = ex_typed = 0
    detail = []
    for pos, chs in (r.get('mistakes') or {}).items():
        try:
            p = int(pos)
        except Exception:
            continue
        for c in chs or []:
            tgt = word[p] if p < len(word) else None
            if is_skip_target(tgt):
                ex_target += 1
            elif is_skip_typed(c):
                ex_typed += 1
            else:
                keep += 1
                detail.append('%d:%s->%s' % (p, tgt, c))
    return old_ev, keep, ex_target, ex_typed, detail


old_err = [r for r in real if r.get('wrongCount', 0) > 0]
still = []
turned_clean = []
tot_old = tot_new = tot_ex1 = tot_ex2 = 0
for r in old_err:
    oe, ke, e1, e2, det = replay(r)
    tot_old += oe
    tot_new += ke
    tot_ex1 += e1
    tot_ex2 += e2
    (still if ke else turned_clean).append((r, ke, det))

print('\n== 记录口径 ==')
print('旧判定有错 %d 条 -> 新判定仍错 %d 条,转为无错 %d 条' % (len(old_err), len(still), len(turned_clean)))
print('  转无错的:')
for r, _, _ in turned_clean:
    print('    %-46r %s wc=%d' % (r['word'][:44], r['dict'], r['wrongCount']))
print('  仍判错的:')
for r, ke, det in sorted(still, key=lambda x: -x[1]):
    print('    %-46r %s wc=%d -> 新事件 %d  %s' % (r['word'][:44], r['dict'], r['wrongCount'], ke, ' '.join(det[:6])))

print('\n== 事件口径 ==')
print('旧事件 %d -> 新事件 %d | 被规则1(目标为空格/标点)豁免 %d | 被规则2(按出空格/标点)豁免 %d' % (
    tot_old, tot_new, tot_ex1, tot_ex2))
print('豁免合计 %d / %d = %.2f%%' % (tot_ex1 + tot_ex2, tot_old, 100.0 * (tot_ex1 + tot_ex2) / max(1, tot_old)))

sent = [r for r in old_err if ' ' in r['word']]
sc = [r for r in sent if not replay(r)[1]]
print('\n== 句子记录单看(对齐报告第 4 条) ==')
print('句子类有错 %d 条 -> 转无错 %d 条,仍错 %d 条' % (len(sent), len(sc), len(sent) - len(sc)))
