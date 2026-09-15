#!/usr/bin/env python3
"""index.html 注入补丁(2026-09-15):在右下角"语速"旁加"空格必打"开关。

为什么要有这个开关:P1 把空格与标点都判为正确之后,用户可以整场练习一次不敲空格,
真实写作场景里空格是必须打的,长期不练会退化。开关写 localStorage.qlStrict=1,
bundle 侧 P1 读到它就恢复"空格必须打"(标点仍然豁免),默认关闭=放宽。
基线: /root/qwerty-backup-202609150810/index.html(基线目录 qwerty-backup-202609150810,08-29 终态含上报与语速两段注入)
"""
import io
import os
import pwd

BASE = '/root/qwerty-backup-202609150810/index.html'
H = '/home/agentxfer/qwerty-site/qwerty-learner/index.html'

src = io.open(BASE, encoding='utf-8').read()
OLD = ("    wrap.appendChild(lab);\n"
       "    wrap.appendChild(sel);\n"
       "    document.body.appendChild(wrap);")
NEW = ("    wrap.appendChild(lab);\n"
       "    wrap.appendChild(sel);\n"
       "    var sl=document.createElement('span');sl.textContent='空格必打';sl.style.marginLeft='6px';\n"
       "    var cb=document.createElement('input');cb.type='checkbox';cb.id='__qlStrict';\n"
       "    try{cb.checked=localStorage.getItem('qlStrict')==='1';}catch(e){}\n"
       "    cb.addEventListener('change',function(){\n"
       "      try{ if(cb.checked){localStorage.setItem('qlStrict','1');}else{localStorage.removeItem('qlStrict');} }catch(e){}\n"
       "      location.reload();\n"
       "    });\n"
       "    wrap.appendChild(sl);\n"
       "    wrap.appendChild(cb);\n"
       "    document.body.appendChild(wrap);")

assert src.count(OLD) == 1, 'anchor hits=%d' % src.count(OLD)
out = src.replace(OLD, NEW, 1)
assert "__qlStrict" in out and out.count('<script') == src.count('<script')

pw = pwd.getpwnam('agentxfer')
with io.open(H, 'w', encoding='utf-8') as f:
    f.write(out)
os.chown(H, pw.pw_uid, pw.pw_gid)
os.chmod(H, 0o664)
print('index.html patched: %d -> %d bytes; toggle id=__qlStrict' % (len(src.encode()), len(out.encode())))
