#!/usr/bin/env python3
"""qwerty-learner bundle 补丁工具(2026-09-15 版)

基线: /root/index-08c272b3.js.bak.20260915-p123
      = 2026-08-29 终态(官方 gh-pages e70146e6 + 当日三项补丁:fl 常量改 /tts、
        my_mistakes/my_phrases 词典索引注入、字母容器 flex-wrap)
本工具在基线上追加 2026-09-15 的五项改动,一次打完,可重复执行(每次从基线重做):
  P1 判定放宽:目标串里的空格与标点自动视为已打对;用户多敲空格/标点不判错;
     只有字母数字打错才算错。localStorage.qlStrict=="1" 时恢复"空格必须打"(标点仍豁免)。
  P2 按词换行:以可见空格 YT(U+2423) 为界把字母分组,组内 whitespace-nowrap,
     换行只发生在词间,长句不再从单词中间劈开。
  P3 字号自适应:容器按 displayWord 长度设 CSS 变量 --qlk,字母组件 fontSize 乘该系数。
  P4/P5 上一条/下一条预览卡:官方 max-w-xs(320px)+text-2xl 会让整句占三行,
     改为 inline style 放宽到 736px / 18px(Tailwind 预编译产物没有任意值类,只能走 inline)。

纪律: 五个锚点各断言唯一;先 node --check 再落盘;落盘后 chown 回 agentxfer;失败不写任何文件。
"""
import hashlib
import io
import os
import pwd
import subprocess
import sys

BASE = '/root/index-08c272b3.js.bak.20260915-p123'
B = '/home/agentxfer/qwerty-site/qwerty-learner/assets/index-08c272b3.js'
TMP = '/tmp/ql-patched.mjs'

src = io.open(BASE, encoding='utf-8').read()

# ---------------- P1 判定放宽 ----------------
OLD1 = ('v.useEffect(()=>{const I=a.inputWord.length;if(a.hasWrong||I===0||a.displayWord.length===0)return;'
        'const N=a.inputWord[I-1],B=a.displayWord[I-1];let A=!1;'
        'N!=null&&B!=null&&(A=l?N.toLowerCase()===B.toLowerCase():N===B),'
        'A?(i(O=>{O.letterTimeArray.push(Date.now()),O.correctCount+=1}),'
        'I>=a.displayWord.length?(i(O=>{O.letterStates[I-1]="correct",O.isFinished=!0,O.endTime=C4()}),h()):'
        '(i(O=>{O.letterStates[I-1]="correct"}),u()),n({type:Ct.REPORT_CORRECT_WORD})):'
        '(d(),i(O=>{O.letterStates[I-1]="wrong",O.hasWrong=!0,O.hasMadeInputWrong=!0,O.wrongCount+=1,'
        'O.letterTimeArray=[],O.letterMistake[I-1]?O.letterMistake[I-1].push(N):O.letterMistake[I-1]=[N];'
        'const H=JSON.parse(JSON.stringify(O));n({type:Ct.REPORT_WRONG_WORD,payload:{letterMistake:H.letterMistake}})}),'
        '_===0&&r.chapterData.index===0&&a.wrongCount>=3&&S(!0))},[a.inputWord])')

# 标点码点表: , . ; : ! ? ' " ( ) [ ] { } - en-dash em-dash … “ ” ‘ ’ / & @ # * + = < > _
PUNCT_CODES = ('[44,46,59,58,33,63,39,34,40,41,91,93,123,125,45,8211,8212,8230,'
               '8220,8221,8216,8217,47,38,64,35,42,43,61,60,62,95]')

NEW1 = (
    'v.useEffect(()=>{const I=a.inputWord.length;'
    'if(a.hasWrong||I===0||a.displayWord.length===0)return;'
    'const ST=!!(window.localStorage&&localStorage.getItem("qlStrict")==="1");'
    'const PU=' + PUNCT_CODES + '.map(Z=>String.fromCharCode(Z));'
    'const Q=Z=>Z===YT||Z===" "?!ST:PU.some(W=>W===Z);'
    'const N=a.inputWord[I-1];'
    'let J=-1;for(let Z=0;Z<a.letterStates.length;Z++)a.letterStates[Z]==="correct"&&(J=Z);'
    'let K=J+1;const G=[];'
    'while(K<a.displayWord.length&&Q(a.displayWord[K]))G.push(K),K++;'
    'if(N!=null&&Q(N))return;'
    'if(K>=a.displayWord.length){i(O=>{for(let q=0;q<O.displayWord.length;q++)O.letterStates[q]="correct";'
    'O.isFinished=!0,O.endTime=C4()}),h();return}'
    'const B=a.displayWord[K];let A=!1;'
    'N!=null&&B!=null&&(A=l?N.toLowerCase()===B.toLowerCase():N===B),'
    'A?(i(O=>{O.letterTimeArray.push(Date.now()),O.correctCount+=1,'
    'G.forEach(Z=>{O.letterStates[Z]="correct"}),O.letterStates[K]="correct"}),'
    '(()=>{let Z2=K+1;while(Z2<a.displayWord.length&&Q(a.displayWord[Z2]))Z2++;'
    'return Z2>=a.displayWord.length})()?'
    '(i(O=>{for(let q=K+1;q<O.displayWord.length;q++)O.letterStates[q]="correct";'
    'O.isFinished=!0,O.endTime=C4()}),h()):(i(O=>{}),u()),n({type:Ct.REPORT_CORRECT_WORD})):'
    '(d(),i(O=>{O.letterStates[K]="wrong",O.hasWrong=!0,O.hasMadeInputWrong=!0,O.wrongCount+=1,'
    'O.letterTimeArray=[],O.letterMistake[K]?O.letterMistake[K].push(N):O.letterMistake[K]=[N];'
    'const H=JSON.parse(JSON.stringify(O));n({type:Ct.REPORT_WRONG_WORD,payload:{letterMistake:H.letterMistake}})}),'
    '_===0&&r.chapterData.index===0&&a.wrongCount>=3&&S(!0))},[a.inputWord])')

# ---------------- P2 按词换行 + P3 字号变量 ----------------
OLD2 = ('className:`flex flex-wrap items-center ${s&&"select-all"} justify-center ${a.hasWrong?E6e.wrong:""}`,'
        'children:a.displayWord.split("").map((I,N)=>x(YK,{letter:I,visible:C(N),state:a.letterStates[N]},`${N}-${I}`))')
NEW2 = ('className:`flex flex-wrap items-center justify-center ${s&&"select-all"} ${a.hasWrong?E6e.wrong:""}`,'
        'style:{"--qlk":a.displayWord.length>80?"0.46":a.displayWord.length>56?"0.55":a.displayWord.length>40?"0.68":'
        'a.displayWord.length>24?"0.82":"1"},'
        'children:(()=>{const W=a.displayWord.split(""),GP=[];let CU=[];'
        'W.forEach((I,N)=>{CU.push(x(YK,{letter:I,visible:C(N),state:a.letterStates[N]},`${N}-${I}`));'
        'if(I===YT){GP.push(CU);CU=[]}});'
        'CU.length&&GP.push(CU);'
        'return GP.map((g,gi)=>x("span",{className:"inline-block whitespace-nowrap",children:g},gi))})()')

# ---------------- P3 字母组件按 --qlk 缩放 ----------------
OLD3 = 'style:{fontSize:n.foreignFont.toString()+"px"}'
NEW3 = 'style:{fontSize:"calc("+n.foreignFont.toString()+"px * var(--qlk,1))"}'

# ---------------- P4 上一条/下一条预览卡宽度 ----------------
OLD4 = ('className:"flex max-w-xs cursor-pointer select-none items-center text-gray-700 '
        'opacity-60 duration-200 ease-in-out hover:opacity-100 dark:text-gray-400",children:')
NEW4 = ('className:"flex cursor-pointer select-none items-center text-gray-700 '
        'opacity-60 duration-200 ease-in-out hover:opacity-100 dark:text-gray-400",'
        'style:{maxWidth:"736px"},children:')

# ---------------- P5 预览卡字号 ----------------
OLD5 = ('x("p",{className:`font-mono text-2xl font-normal text-gray-700 dark:text-gray-400 '
        '${n.isOpen?"tracking-wider":"tracking-normal"}`,children:c})')
NEW5 = ('x("p",{className:`font-mono font-normal text-gray-700 dark:text-gray-400 '
        '${n.isOpen?"tracking-wider":"tracking-normal"}`,style:{fontSize:"18px",lineHeight:1.35},children:c})')

PAIRS = [('P1 判定放宽+strict开关', OLD1, NEW1),
         ('P2 按词换行+P3 字号变量', OLD2, NEW2),
         ('P3 字母 fontSize 走 --qlk', OLD3, NEW3),
         ('P4 预览卡放宽', OLD4, NEW4),
         ('P5 预览卡字号', OLD5, NEW5)]

for tag, old, new in PAIRS:
    n = src.count(old)
    assert n == 1, '%s: anchor hits=%d, abort with nothing written' % (tag, n)
    print('%-28s anchor unique (%d chars)' % (tag, len(old)))

out = src
for tag, old, new in PAIRS:
    out = out.replace(old, new, 1)

io.open(TMP, 'w', encoding='utf-8').write(out)
r = subprocess.run(['node', '--check', TMP], capture_output=True, text=True)
if r.returncode != 0:
    print('node --check FAILED, nothing written to site:\n' + (r.stderr or '')[:2500])
    sys.exit(1)
print('node --check OK')

# 不在站点目录留 .bak(对端会往 home 推文件,且会污染内嵌 .git 的 status);回滚源是 BASE
pw = pwd.getpwnam('agentxfer')
with io.open(B, 'w', encoding='utf-8') as f:
    f.write(out)
os.chown(B, pw.pw_uid, pw.pw_gid)
os.chmod(B, 0o664)
h = lambda b: hashlib.sha256(b).hexdigest()[:12]
print('base %s -> patched %s (%d -> %d bytes)' % (h(src.encode()), h(out.encode()),
                                                  len(src.encode()), len(out.encode())))
