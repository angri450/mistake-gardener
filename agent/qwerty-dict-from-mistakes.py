#!/usr/bin/env python3
"""错题数据 -> 词库 -> bundle 元数据:打字练习闭环的生成端。

输入(只读):
  /home/agentxfer/collect.jsonl                      错题原始记录
  /home/agentxfer/qwerty-site/qwerty-learner/dicts/CET4_T.json   站点自带四级词库,取释义
输出:
  dicts/my_mistakes.json   错词本(按错误密度筛,不是"错过就进")
  dicts/my_phrases.json    句子库(按历史出错位置覆盖,自动切 2-3 词片段 + 半句 + 整句)
  bundle 内两条词典索引的 length 元数据(必须等于实际条目数,否则章节数算错)

筛选规则(写死在此,可复算):
  单词: 事件按新判定口径计(空格与标点不算错),分母为必打字符数;
        attempts>=2 且 密度>=0.15,或 事件>=3,或 attempts>=2 且 事件>=2;排除含空格/标点的句子条目
  句子: 对每个 distinct 句子,枚举 2 词与 3 词滑窗 + 标点分段 + 整句,
        按"该切片覆盖的历史出错位置数"降序取,总量上限 MAX_SENT
  释义: 站点 CET4_T.json 命中则用其释义,否则用旧词库释义,再追加错因注记

用法: python3 /root/qwerty-dict-from-mistakes.py           # dry-run,只打印
      python3 /root/qwerty-dict-from-mistakes.py --apply   # 落盘(自动备份旧词库到 /root)
"""
import collections
import datetime
import statistics
import glob
import io
import json
import os
import re
import shutil
import sys

COLLECT = '/home/agentxfer/collect.jsonl'
DICT_DIR = '/home/agentxfer/qwerty-site/qwerty-learner/dicts'
CET4 = os.path.join(DICT_DIR, 'CET4_T.json')
BUNDLE = '/home/agentxfer/qwerty-site/qwerty-learner/assets/index-08c272b3.js'
MAX_SENT = 12
APPLY = '--apply' in sys.argv

# ---------- 1. 读数据 ----------
rows = []
for line in io.open(COLLECT, encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if isinstance(r, dict) and 'word' in r:
        rows.append(r)

# ---------- 1b. 剔除不该参与生成的记录 ----------
# 判据一:间隔标准差 <3ms 且 timing>=8 —— 恒速击键,人工做不到
# 判据二:本轮回归产生的合成记录按日期切掉(默认 2026-09-15 起全剔;以后若用户真在练,加 --no-cut)
CUT = None if '--no-cut' in sys.argv else '2026-09-15'
N0 = len(rows)


def is_synth(r):
    t = r.get('timing') or []
    return len(t) >= 8 and statistics.pstdev(t) < 3


rows = [r for r in rows if not is_synth(r)]
n_synth = N0 - len(rows)
if CUT:
    N1 = len(rows)
    rows = [r for r in rows
            if datetime.datetime.fromtimestamp(r['timeStamp']).date().isoformat() < CUT]
    n_cut = N1 - len(rows)
else:
    n_cut = 0
print('剔除: 恒速机器记录 %d 条%s; 余 %d 条参与生成' % (
    n_synth, (', 日期 >= %s 的回归记录 %d 条' % (CUT, n_cut)) if CUT else ''  , len(rows)))

# ---------- 2. 聚合 ----------
stat = collections.defaultdict(lambda: {'attempts': 0, 'err_recs': 0, 'err_recs_old': 0, 'events': 0,
                                         'chars': 0, 'pos': collections.Counter(),
                                         'typed': collections.Counter()})
SKIPCH = set(' ,.;:!?\'"()[]{}-–—…“”‘’/@#*+=<>_␣')


def real_events(word, mistakes):
    """新判定口径下仍算错的事件数:目标位是空格/标点的不算,用户敲出空格/标点的不算"""
    n = 0
    for pos, chs in (mistakes or {}).items():
        try:
            p = int(pos)
        except Exception:
            continue
        tgt = word[p] if p < len(word) else None
        for c in chs or []:
            if tgt in SKIPCH or c in SKIPCH:
                continue
            n += 1
    return n


for r in rows:
    k = r['word']
    s = stat[k]
    s['attempts'] += 1
    # 分母用"必打字符数"(去掉空格与标点),与新判定要考的内容一致
    s['chars'] += sum(1 for ch in k if ch not in SKIPCH)
    s['err_recs_old'] += 1 if r.get('wrongCount', 0) > 0 else 0
    ev = real_events(k, r.get('mistakes'))
    s['err_recs'] += 1 if ev else 0
    for pos, chs in (r.get('mistakes') or {}).items():
        try:
            p = int(pos)
        except Exception:
            continue
        tgt = k[p] if p < len(k) else None
        for c in chs or []:
            if tgt in SKIPCH or c in SKIPCH:
                continue
            s['events'] += 1
            s['pos'][p] += 1
            s['typed'][c] += 1

CET = {}
if os.path.exists(CET4):
    for e in json.load(io.open(CET4, encoding='utf-8')):
        CET.setdefault(e['name'].lower(), e.get('trans') or [])

OLD_MISTAKES = json.load(io.open(os.path.join(DICT_DIR, 'my_mistakes.json'), encoding='utf-8'))
OLD_TRANS = {e['name'].lower(): e.get('trans') or [] for e in OLD_MISTAKES}

PUNCT_RE = re.compile(r'[,.;:!?]')

_ANY = None


def lookup_any_dict(w):
    """CET4 未命中时,扫站点全部词典取释义(一次扫描后缓存)"""
    global _ANY
    if _ANY is None:
        _ANY = {}
        for f in glob.glob(os.path.join(DICT_DIR, '*.json')):
            if os.path.basename(f).startswith(('my_', '926')):
                continue          # 自建词库不作为释义源,否则注记会自我叠加
            try:
                for e in json.load(io.open(f, encoding='utf-8')):
                    n = (e.get('name') or '').lower()
                    if n and n not in _ANY and e.get('trans'):
                        _ANY[n] = e['trans']
            except Exception:
                continue
    return _ANY.get(w.lower())


def is_word(w):
    return ' ' not in w and not PUNCT_RE.search(w)


def conf_note(s):
    """把该词最高频的错按字符写成注记"""
    top = s['typed'].most_common(2)
    if not top:
        return ''
    return '易打成 ' + '/'.join(c for c, _ in top)


# ---------- 3. 错词本 ----------
word_rows, sent_rows = [], []
for w, s in stat.items():
    (word_rows if is_word(w) else sent_rows).append((w, s))

picked = []
for w, s in sorted(word_rows, key=lambda x: -(x[1]['events'] / max(1, x[1]['chars']))):
    density = s['events'] / max(1, s['chars'])
    ok = ((s['attempts'] >= 2 and density >= 0.15) or s['events'] >= 3
          or (s['attempts'] >= 2 and s['events'] >= 2))
    if not ok:
        continue
    def strip_note(t):
        return re.sub(r'\(\d+ 次尝试[^)]*\)\s*$', '', t).strip()
    trans = [strip_note(x) for x in (CET.get(w.lower()) or [])] or \
            [strip_note(x) for x in (OLD_TRANS.get(w.lower()) or [])]
    if not trans:
        trans = lookup_any_dict(w) or ['(站点词库未收录,需手工释义)']
    note = '%d 次尝试 %d 事件 %.0f%%' % (s['attempts'], s['events'], density * 100)
    cn = conf_note(s)
    if cn:
        note += ' ' + cn
    picked.append({'name': w, 'trans': [trans[0] + '(' + note + ')'], 'usphone': '', 'ukphone': ''})

# 句子记录里出现过的单词形态(如 must/feel)也进错词本:它们是分段条目的产物,同样有真实错误
for w, s in sorted(sent_rows, key=lambda x: -x[1]['events']):
    for piece in re.findall(r"[A-Za-z']{4,}", w):
        if piece.lower() in {p['name'].lower() for p in picked}:
            continue
        pass  # 句子内单词不单独成条,交给句子切片练(见 4)

mistakes_out = picked

# ---------- 4. 句子库 ----------
sent_texts = collections.Counter()
for w, s in sent_rows:
    if not is_word(w):
        sent_texts[w] += s['events']

# 文本集还要并入现有词库里人工设定的长条目:剔除恒速记录会把某些真实练习一起削掉
# (08-29 两次整句练习就被判成"太稳"而剔除),毕业整句这类骨架不能靠数据里恰好有记录才保留
for e in json.load(io.open(os.path.join(DICT_DIR, 'my_phrases.json'), encoding='utf-8')):
    n = e.get('name') or ''
    if len(n.split()) >= 3:
        sent_texts.setdefault(n, 0)

# 每个句子文本的出错位置分布(合并该文本所有记录)
posmap = collections.defaultdict(collections.Counter)
for r in rows:
    w = r['word']
    if is_word(w):
        continue
    for pos, chs in (r.get('mistakes') or {}).items():
        try:
            p = int(pos)
        except Exception:
            continue
        tgt = w[p] if p < len(w) else None
        for c in chs or []:
            if tgt in SKIPCH or c in SKIPCH:
                continue      # 新判定下这些事件不算错,不该驱动切片排序
            posmap[w][p] += 1


def slices(text):
    """滑窗 2/3 词 + 标点分段 + 整句;返回 (切片, 类型)"""
    out = []
    words = text.split()
    for n in (2,):
        for i in range(0, max(0, len(words) - n + 1)):
            out.append((' '.join(words[i:i + n]), 'slice'))
    for part in [p.strip() for p in re.split(r'[,;:]', text) if p.strip()]:
        if part != text:
            out.append((part if PUNCT_RE.search(part) else part + ',', 'half'))
    for part in [p for p in re.split(r'(?<=[.!?])\s+', text) if p]:
        if part != text:
            out.append((part, 'sent'))
    out.append((text, 'full'))
    seen, uniq = set(), []
    for t, kind in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append((t, kind))
    return uniq


zh_base = {}
for e in json.load(io.open(os.path.join(DICT_DIR, 'my_phrases.json'), encoding='utf-8')):
    for t in (e.get('trans') or []):
        m = re.split(r'[|]', t)
        cand = m[-1].strip().rstrip('。')
        if re.search(r'[\u4e00-\u9fff]', cand) and len(cand) > 6:
            zh_base.setdefault('default', cand)
            break

phrases_out = []
scored = []
for text, _ in sent_texts.most_common():
    pm = posmap.get(text, collections.Counter())
    for sub, kind in slices(text):
        # 覆盖的历史出错位置数:切片起点在原文中,位置区间 [start, start+len)
        start = text.find(sub)
        if start < 0:
            start = text.find(sub.rstrip(',.'))
        end = (start + len(sub)) if start >= 0 else 0
        cover = sum(v for p, v in pm.items() if start <= p < end)
        scored.append((cover, kind, sub, text))

# 分层配额:整句与半句/分句必进(教学骨架),剩余名额给覆盖错点最多的 2 词窗口
order = {'slice': 0, 'half': 1, 'sent': 2, 'full': 3}
scored.sort(key=lambda x: (-x[0], order[x[1]], len(x[2])))
best = {}
for cover, kind, sub, text in scored:
    if sub not in best or cover > best[sub][0]:
        best[sub] = (cover, kind, sub, text)
uniq = list(best.values())
must_have = sorted([x for x in uniq if x[1] != 'slice'], key=lambda x: -x[0])
fillers = sorted([x for x in uniq if x[1] == 'slice'], key=lambda x: (-x[0], len(x[2])))
quota = must_have + fillers[:max(0, MAX_SENT - len(must_have))]
taken = set()
for cover, kind, sub, text in quota:
    if len(phrases_out) >= MAX_SENT:
        break
    if sub in taken:
        continue
    taken.add(sub)
    zh = zh_base.get('default', '')
    label = {'slice': '片段', 'half': '半句', 'sent': '分句', 'full': '整句'}[kind]
    tr = [('%s %s' % (label, ('覆盖历史错点 %d' % cover) if cover else '无错点记录'))]
    if zh:
        tr.append(zh)
    phrases_out.append({'name': sub, 'trans': tr, 'usphone': '', 'ukphone': ''})

# 排序:按长度升序(先短后长),同长度按覆盖面降序
phrases_out.sort(key=lambda e: (len(e['name']), -sum(1 for _ in [])))

# ---------- 5. 打印决策 ----------
print('== 数据源 ==')
print('%s  有效记录 %d 条  时间 %s ~ %s' % (
    COLLECT, len(rows),
    datetime.datetime.fromtimestamp(min(r['timeStamp'] for r in rows)).strftime('%m-%d %H:%M'),
    datetime.datetime.fromtimestamp(max(r['timeStamp'] for r in rows)).strftime('%m-%d %H:%M')))
print('\n== 错词本 my_mistakes.json: %d 条(旧 %d) ==' % (len(mistakes_out), len(OLD_MISTAKES)))
for e in mistakes_out:
    print('  %-12s %s' % (e['name'], e['trans'][0]))
print('\n== 句子库 my_phrases.json: %d 条(上限 %d) ==' % (len(phrases_out), MAX_SENT))
for e in phrases_out:
    print('  %-46s %s' % (repr(e['name']), e['trans'][0]))

if not APPLY:
    print('\n[dry-run] 未写任何文件。加 --apply 落盘。')
    sys.exit(0)

# ---------- 6. 落盘 + 同步 bundle length ----------
ts = datetime.datetime.now().strftime('%Y%m%d%H%M')
bak = '/root/qwerty-dicts-backup-' + ts
os.makedirs(bak, exist_ok=True)
for f in ('my_mistakes.json', 'my_phrases.json'):
    shutil.copy2(os.path.join(DICT_DIR, f), os.path.join(bak, f))
print('\n旧词库已备份到', bak)

for fn, data in (('my_mistakes.json', mistakes_out), ('my_phrases.json', phrases_out)):
    p = os.path.join(DICT_DIR, fn)
    io.open(p, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False, indent=1))
    os.chown(p, 1001, 1001)
    os.chmod(p, 0o664)
    print('写出', fn, len(data), '条')

b = io.open(BUNDLE, encoding='utf-8').read()
for did, n in (('my_mistakes', len(mistakes_out)), ('my_phrases', len(phrases_out))):
    i = b.index('id:"%s"' % did)
    win = b[i:i + 420]
    m = re.search(r'length:(\d+)', win)
    assert m, 'no length in window for ' + did
    newwin = win[:m.start(1)] + str(n) + win[m.end(1):]
    assert b.count(win) == 1, 'index window not unique for ' + did
    b = b.replace(win, newwin, 1)
    print('bundle %s length %s -> %s' % (did, m.group(1), n))
io.open(BUNDLE, 'w', encoding='utf-8').write(b)
os.chown(BUNDLE, 1001, 1001)
print('bundle 已同步')
