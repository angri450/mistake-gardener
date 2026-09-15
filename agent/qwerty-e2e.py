#!/usr/bin/env python3
"""qwerty-learner 端到端回归/探针脚本(本机 8081,免认证免 TLS)。

用法:
  python3 /root/qwerty-e2e.py --book phrases --mode probe
  python3 /root/qwerty-e2e.py --book phrases --mode both --shot /root/s.png
mode: full(原文) / nopunct(不打标点) / nospace(不打空格) / both(标点空格都不打) /
      wrong(末字母打错) / probe(只进页面 dump 结构不 typing)
输出:截图 + 字母 DOM 结构 + 页面状态摘要;读 IndexedDB 最新记录。
"""
import argparse, json, os, re, subprocess, sys, time
from playwright.sync_api import sync_playwright

EXE = '/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
SITE = 'http://127.0.0.1:8081/qwerty-learner/'
DICT_DIR = '/home/agentxfer/qwerty-site/qwerty-learner/dicts'
BOOKS = {  # 文件 -> (tag 名, 词书名)
    'phrases': ('词组', '我的词组句库', 'my_phrases.json'),
    'mistakes': ('错词', '我的错词本', 'my_mistakes.json'),
    'cet4': ('大学英语', 'CET-4', 'CET4_T.json'),
}
PUNCT = set(',.;:!?\'"()[]-_…“”‘’')


def transform(text, mode):
    if mode == 'full':
        return text
    if mode == 'nopunct':
        return ''.join(c for c in text if c not in PUNCT)
    if mode == 'nospace':
        return text.replace(' ', '')
    if mode == 'both':
        return ''.join(c for c in text if c not in PUNCT and c != ' ')
    if mode == 'lead':          # 开头多敲空格 + 词间多敲逗号(习惯性乱敲标点)
        return ' ' + ', '.join(text.split())
    if mode == 'wrong':
        # 把最后一个字母换错(标点与空格已不算考核项,改它们无意义)
        ch = list(text)
        for i in range(len(ch) - 1, -1, -1):
            if ch[i].isalpha():
                ch[i] = 'q' if ch[i].lower() != 'q' else 'w'
                break
        return ''.join(ch)
    raise SystemExit('bad mode ' + mode)


def nav(p, tag, name):
    p.goto(SITE, wait_until='domcontentloaded')
    time.sleep(3)
    p.locator("a[href*='gallery']").click(force=True)
    time.sleep(2.5)
    p.get_by_text(tag, exact=True).first.click()
    time.sleep(2)
    p.get_by_text(name, exact=True).first.click(force=True)
    time.sleep(2.5)
    ch = p.get_by_text(re.compile(r'^第\s*\d+\s*章'), exact=False)
    if ch.count() == 0:
        raise SystemExit('no chapter card found; url=' + p.url)
    ch.first.click(force=True)
    time.sleep(3)


def dump_letters(p):
    js = """() => {
      const box = document.querySelector('div.flex.flex-wrap.items-center');
      if (!box) return {err: 'letter box not found',
                        boxes: [...document.querySelectorAll('div')].length};
      return {cls: box.className,
              kids: box.children.length,
              boxRect: (r => ({x: Math.round(r.x), y: Math.round(r.y),
                               w: Math.round(r.width), h: Math.round(r.height)}))(box.getBoundingClientRect()),
              rows: [...new Set([...box.children].map(e => Math.round(e.getBoundingClientRect().y)))].sort((a,b)=>a-b),
              sample: [...box.children].slice(0, 6).map(e => ({t: e.textContent, c: e.className}))};
    }"""
    return p.evaluate(js)


def current_text(p):
    """从 DOM 反演当前条目原文(␣ 还原为空格)"""
    js = """() => {const b=document.querySelector('div.flex.flex-wrap.items-center');
      return b ? [...b.children].map(e=>e.textContent).join('') : '';}
"""
    return (p.evaluate(js) or '').replace('\u2423', ' ').replace('\u2038', '').replace('<', '')


def wait_advance(p, prev, timeout=12):
    """等当前条目推进(轮询 DOM),返回新文本;超时返回最后读到的值"""
    t0 = time.time()
    cur = prev
    while time.time() - t0 < timeout:
        time.sleep(0.8)
        cur = current_text(p)
        if cur != prev:
            return cur, True
    return cur, False


def shot(p, path):
    try:
        p.screenshot(path=path, full_page=False, timeout=12000)
    except Exception as e:
        print('   [screenshot failed: %s]' % str(e)[:80])


def has_card(p):
    try:
        return p.evaluate("() => document.body.innerText.includes('按任意键开始')")
    except Exception:
        return None


def classify(p):
    """逐位输出字符与状态类别(按 class 聚类),用于定位卡在哪一个字符"""
    js = """() => {const b=document.querySelector('div.flex.flex-wrap.items-center');
      if(!b) return [];
      return [...b.children].map(e=>({t:e.textContent, c:e.className.replace(/m-0 p-0 font-mono font-normal /,'')
        .replace(/ duration-0 dark:text-opacity-80/,'').replace(/ pr-0\.8/,'')}));}
"""
    arr = p.evaluate(js)
    seen = {}
    for i, e in enumerate(arr):
        seen.setdefault(e['c'], []).append((i, e['t']))
    out = []
    for cls, items in seen.items():
        out.append('%-44s x%-3d %s' % (cls[:44], len(items), items[:16]))
    return '\n'.join(out)


def read_idb(p):
    js = """async () => {
      const db = await new Promise((res, rej) => {const r = indexedDB.open('RecordDB'); r.onsuccess=()=>res(r.result); r.onerror=()=>rej(r.error);});
      if (!db.objectStoreNames.contains('wordRecords')) return {err: 'no store', names: [...db.objectStoreNames]};
      const all = await new Promise((res, rej) => {const q = db.transaction('wordRecords').objectStore('wordRecords').getAll(); q.onsuccess=()=>res(q.result); q.onerror=()=>rej(q.error);});
      return {count: all.length, last: all.length ? all[all.length-1] : null};
    }"""
    try:
        return p.evaluate(js)
    except Exception as e:
        return {'err': str(e)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', default='phrases', choices=list(BOOKS))
    ap.add_argument('--mode', default='probe', choices=['full', 'nopunct', 'nospace', 'both', 'lead', 'wrong', 'probe'])
    ap.add_argument('--item', type=int, default=0, help='词库第几条(0 基)')
    ap.add_argument('--shot', default='/root/qwerty-e2e.png')
    ap.add_argument('--advance', type=int, default=1, help='连打几条(用于推进到长句)')
    ap.add_argument('--fromdom', action='store_true', help='从 DOM 反演当前条目原文(配合 --advance 推进到长句)')
    ap.add_argument('--gate', default='Enter', help='关门键(默认 Enter)')
    ap.add_argument('--strict', action='store_true', help='预设 localStorage.qlStrict=1(空格必须打)')
    ap.add_argument('--preshot', default='/root/qwerty-e2e-pre.png', help='每步打字前的截图(最后一张即目标条目排版)')
    a = ap.parse_args()

    tag, name, fn = BOOKS[a.book]
    entries = json.load(open(os.path.join(DICT_DIR, fn), encoding='utf-8'))
    target = entries[min(a.item, len(entries) - 1)]['name']

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, executable_path=EXE, args=['--no-sandbox'])
        ctx = b.new_context(viewport={'width': 1280, 'height': 900})
        if a.strict:
            ctx.add_init_script("try{localStorage.setItem('qlStrict','1')}catch(e){}")
        p = ctx.new_page()
        errs = []
        p.on('console', lambda m: errs.append(m.text[:120]) if m.type == 'error' else None)
        p.on('pageerror', lambda e: errs.append('PAGEERROR ' + str(e)[:200]))
        nav(p, tag, name)
        before = dump_letters(p)
        p.screenshot(path=a.shot.replace('.png', '-0-before.png'), full_page=True)
        print('== target entry ==')
        print(repr(target))
        print('== letter box before typing ==')
        print(json.dumps(before, ensure_ascii=False, indent=1))

        if a.mode != 'probe':
            cur = current_text(p)
            for step in range(a.advance):
                src = cur if a.fromdom else target
                typed = transform(src, a.mode)
                print('-- step %d: display=%r  typed(%s)=%r  len=%d  card_before=%s' % (
                    step, src[:70], a.mode, typed[:70], len(typed), has_card(p)))
                if has_card(p):            # 只有起始卡片在时才吃首键;无卡片时按 Enter 会暂停/重置输入
                    p.keyboard.press(a.gate)
                    time.sleep(1)
                    print('   card_after_gate=%s' % has_card(p))
                shot(p, a.preshot)          # 打字前的排版(看长句占几行)
                time.sleep(1)
                p.keyboard.type(typed, delay=70)
                time.sleep(2.5)
                st = dump_letters(p)
                print('   box w=%s h=%s rows=%s kids=%s' % (
                    st.get('boxRect', {}).get('w'), st.get('boxRect', {}).get('h'),
                    st.get('rows'), st.get('kids')))
                shot(p, a.shot)
                print('   classes:\n' + classify(p))
                nxt, moved = wait_advance(p, src)
                print('   advanced=%s -> %r' % (moved, nxt[:70]))
                cur = nxt
            time.sleep(9)                  # 等 12 秒上报水位
            print('== IndexedDB ==')
            print(json.dumps(read_idb(p), ensure_ascii=False)[:700])
        print('== console errors ==')
        print([e for e in errs if 'insights' not in e and 'favicon' not in e][:6])
        b.close()


main()
