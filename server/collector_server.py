#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwerty 站点服务 + 错题收集器
GET  静态文件（qwerty-site 目录）
POST /collect  错题记录追加写入 collect.jsonl
"""
import hashlib
import base64
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = '/home/agentxfer/qwerty-site'
COLLECT = '/home/agentxfer/collect.jsonl'          # owner(本人)的错题流水
COLLECT_JUDGE = '/home/agentxfer/collect-judge.jsonl'  # 评委/访客的错题流水,与 owner 物理隔离


def _tag(handler):
    """请求来源:owner(本人)还是 judge(评委/访客)。

    2026-09-15 起两个账号共用 8080 站点,basic_auth 已由 caddy 校验,所以这里读
    caddy 转发下来的 Authorization 头,解出用户名即可分辨来源——不必把明文凭据写进 Caddyfile,
    也不必为评委单开端口。安全前提:8081 只绑 127.0.0.1,外部进不来,这个头只可能来自 caddy。
    客户端若自带 X-Collector-Tag,caddy 侧已用 header_up -X-Collector-Tag 删掉;
    万一没删(例如日后改配置),这里也只在值恰为 judge/owner 时才采信,并优先按用户名判断。
    """
    a = handler.headers.get('Authorization') or ''
    if a[:6].lower() == 'basic ':
        try:
            val = a.split(None, 1)[1].strip()
            val += '=' * (-len(val) % 4)
            raw = base64.b64decode(val).decode('utf-8', 'replace')
            if raw.split(':', 1)[0].strip().lower() == 'judge':
                return 'judge'
        except Exception:
            pass
    t = (handler.headers.get('X-Collector-Tag') or '').strip().lower()
    if t == 'judge':
        return 'judge'
    return 'owner'
os.makedirs(ROOT, exist_ok=True)

# --- TTS:短文本(<=YOUDAO_MAX 字符)302 代理 youdao,与单词朗读同音色;长文本走本地 Piper,磁盘缓存 ---
# 2026-09-02 中文音色接入:voice=zh → 中文 Piper(zh_CN-huayan-medium),voice=en(缺省)保持原行为
TTS_BIN = '/home/agentxfer/tts/venv/bin/piper'
TTS_VOICE = '/home/agentxfer/tts/voices/en_US-lessac-medium.onnx'
TTS_VOICE_ZH = '/home/agentxfer/tts/voices/zh_CN-huayan-medium.onnx'
TTS_CACHE = '/home/agentxfer/tts/cache'
YOUDAO_MAX = 12
YOUDAO_PREFIX = 'https://dict.youdao.com/dictvoice?audio='
TTS_LOCK = threading.Lock()
os.makedirs(TTS_CACHE, exist_ok=True)

# --- 中英混合 TTS(voice=mix):按 CJK 边界切段,中文段华妍/英文段 lessac 交替合成,wave 拼接 ---
# 两个 Piper 音色同为 22050Hz 16bit mono PCM,直接按帧拼接无需重采样
_CJK_RUN = r'[\u4e00-\u9fff\u3400-\u4dbf]+'


def _mix_segments(text):
    """返回 [(语言, 合成文本), ...];相邻同语言段已合并;纯标点并入前段尾部保留停顿"""
    segs = []
    for raw in re.findall(_CJK_RUN + r'|[^\u4e00-\u9fff\u3400-\u4dbf]+', text):
        if not raw:
            continue
        if re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', raw):
            lang = 'zh'
        elif re.search(r'[A-Za-z0-9]', raw):
            lang = 'en'
        else:
            # 纯标点/空白:贴到前一段尾部,保留原始符号(中文停顿节奏不丢)
            if segs:
                segs[-1][1] += raw
            continue
        if segs and segs[-1][0] == lang:
            segs[-1][1] += raw
        else:
            segs.append([lang, raw])
    return segs


def _piper_synth(model, text, out_path):
    subprocess.run([TTS_BIN, '--model', model, '--output_file', out_path],
                   input=text.encode(), check=True, timeout=120)


def _concat_wavs(src_list, dst):
    """同参数 WAV 顺序拼接:参数取第一个文件,其余只读帧体"""
    with wave.open(dst, 'wb') as out:
        first = True
        for src in src_list:
            with wave.open(src, 'rb') as w:
                if first:
                    out.setnchannels(w.getnchannels())
                    out.setsampwidth(w.getsampwidth())
                    out.setframerate(w.getframerate())
                    first = False
                out.writeframes(w.readframes(w.getnframes()))


class H(SimpleHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/collect', '/collect.jsonl'):
            # 只读查看当前收集内容;评委来源一律拒绝(那是 owner 的练习隐私)
            if _tag(self) == 'judge':
                self.send_response(403)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":false,"err":"forbidden"}')
                return
            try:
                data = open(COLLECT, 'rb').read()
            except FileNotFoundError:
                data = b''
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/tts':
            # 手工解析 query:unquote 不把 '+' 当空格,避免文本含 '+' 时被破坏
            q = urllib.parse.urlparse(self.path).query
            parts = {}
            for kv in q.split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    parts.setdefault(k, urllib.parse.unquote(v))
            text = parts.get('audio', '').strip()
            ttype = parts.get('type', '2')
            voice = parts.get('voice', 'en')  # en(缺省) | zh(中文华妍)
            if not text:
                self.send_response(400)
                self._cors()
                self.end_headers()
                self.wfile.write(b'{"ok":false,"err":"empty audio"}')
                return
            if voice == 'zh':
                # 中文:短文本也不走有道(有道对中文词不保证发音),一律本地中文 Piper
                model = TTS_VOICE_ZH
                key = hashlib.md5(f'zh|{text}'.encode()).hexdigest()
                path = os.path.join(TTS_CACHE, key + '.wav')
                if not os.path.exists(path):
                    with TTS_LOCK:
                        if not os.path.exists(path):
                            tmp = path + f'.{os.getpid()}.tmp'
                            subprocess.run([TTS_BIN, '--model', model, '--output_file', tmp],
                                           input=text.encode(), check=True, timeout=120)
                            os.replace(tmp, path)
                ctype = 'audio/wav'
            elif voice in ('mix', 'mixed'):
                # 中英混合:按 CJK 边界切段,中文段华妍/英文段 lessac 交替合成后拼接
                key = hashlib.md5(f'mix|{text}'.encode()).hexdigest()
                path = os.path.join(TTS_CACHE, key + '.wav')
                if not os.path.exists(path):
                    with TTS_LOCK:
                        if not os.path.exists(path):
                            segs = _mix_segments(text)
                            if not segs:
                                segs = [('en', text)]  # 全符号文本兜底走英文
                            tmp_dir = path + f'.{os.getpid()}.tmp'
                            os.makedirs(tmp_dir, exist_ok=True)
                            try:
                                parts = []
                                for i, (lang, seg_text) in enumerate(segs):
                                    p = os.path.join(tmp_dir, f'{i}.wav')
                                    _piper_synth(TTS_VOICE_ZH if lang == 'zh' else TTS_VOICE,
                                                 seg_text, p)
                                    parts.append(p)
                                _concat_wavs(parts, path)
                            finally:
                                shutil.rmtree(tmp_dir, ignore_errors=True)
                ctype = 'audio/wav'
            elif len(text) <= YOUDAO_MAX and ttype in ('1', '2'):
                # 短文本:服务端代理 youdao(同源返回字节流,避免 302 跨域被浏览器 CORS 拦截)
                key = hashlib.md5(f'y{ttype}|{text}'.encode()).hexdigest()
                cache_file = os.path.join(TTS_CACHE, key + '.mp3')
                if not os.path.exists(cache_file):
                    with TTS_LOCK:
                        if not os.path.exists(cache_file):
                            req = urllib.request.Request(
                                f'{YOUDAO_PREFIX}{urllib.parse.quote(text)}&type={ttype}',
                                headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=20) as resp:
                                audio = resp.read()
                            tmp = cache_file + '.tmp'
                            with open(tmp, 'wb') as f:
                                f.write(audio)
                            os.replace(tmp, cache_file)
                ctype, path = 'audio/mpeg', cache_file
            else:
                key = hashlib.md5(f'{ttype}|{text}'.encode()).hexdigest()
                path = os.path.join(TTS_CACHE, key + '.wav')
                if not os.path.exists(path):
                    with TTS_LOCK:
                        if not os.path.exists(path):
                            tmp = path + f'.{os.getpid()}.tmp'
                            subprocess.run([TTS_BIN, '--model', TTS_VOICE, '--output_file', tmp],
                                           input=text.encode(), check=True, timeout=120)
                            os.replace(tmp, path)
                ctype = 'audio/wav'
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(os.path.getsize(path)))
            self.end_headers()
            with open(path, 'rb') as f:
                self.wfile.write(f.read())
            return
        super().do_GET()

    def do_POST(self):
        if not self.path.startswith('/collect'):
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get('Content-Length', 0) or 0)
        body = self.rfile.read(n).decode('utf-8', 'replace') if n else ''
        try:
            data = json.loads(body)
            rows = data if isinstance(data, list) else [data]
            tag = _tag(self)
            target = COLLECT_JUDGE if tag == 'judge' else COLLECT
            with open(target, 'a', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'stored': len(rows), 'bucket': tag}).encode())
        except Exception as e:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'err': str(e)}).encode())

    def end_headers(self):
        # 2026-09-15:静态代码必须每次校验,否则改了 bundle 用户仍吃旧缓存(启发式缓存按 Last-Modified 龄期算)
        # 只对 html/js/css 生效;音频与词典 JSON 保持可缓存(词典改动罕见,改后硬刷新即可)
        try:
            _p = self.path.split('?')[0]
            if _p.endswith(('.js', '.css', '.html')) or _p in ('', '/'):
                self.send_header('Cache-Control', 'no-cache')
        except Exception:
            pass
        SimpleHTTPRequestHandler.end_headers(self)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer.allow_reuse_address = True
    with ThreadingHTTPServer(('127.0.0.1', 8081), H) as httpd:
        httpd.serve_forever()
