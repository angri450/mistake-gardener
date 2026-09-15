#!/usr/bin/env python3
"""把 Nong.Image 生成的底图合成成参赛用物料:16:9 封面 + 项目 ICON。

为什么文字用程序压而不是让模型画:文生图对中文与长英文的还原不可靠,
底图只出画面,标题用 Noto Sans CJK 精确排版,保证一个字都不错。

输入(由 Nong.Image 生成):
  cover-raw.png   1920x1080 封面底图(左侧留白)
  icon-keycap.png 1024x1024 键帽花盆 + 嫩芽
输出: ../mistake-gardener-assets/
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SRC = '/root/Nong.Data/生图/mistake-gardener'
OUT = '/root/pi-cwd-20260829/mistake-gardener-assets'
os.makedirs(OUT, exist_ok=True)

CJK_BLACK = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Black.ttc'
CJK_BOLD = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc'
CJK_MED = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Medium.ttc'
CJK_LIGHT = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-DemiLight.ttc'
MONO = '/usr/share/fonts/dejavu/DejaVuSansMono.ttf'
SANS_B = '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'
IDX = 2  # .ttc 里的 SC 字面


def f(path, size):
    return ImageFont.truetype(path, size, index=IDX) if path.endswith('.ttc') else ImageFont.truetype(path, size)


def text_w(d, s, font, spacing=0):
    if not spacing:
        return d.textlength(s, font=font)
    return sum(d.textlength(c, font=font) + spacing for c in s) - spacing


def draw_spaced(img, d, xy, s, font, fill, spacing=0, shadow=None):
    x, y = xy
    if shadow:
        sx, sy, blur, alpha = shadow
        layer = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        cx = x + sx
        for c in s:
            dl.text((cx, y + sy), c, font=font, fill=(0, 0, 0, alpha))
            cx += d.textlength(c, font=font) + spacing
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(layer)
    for c in s:
        d.text((x, y), c, font=font, fill=fill)
        x += d.textlength(c, font=font) + spacing


# ---------------- 封面 16:9 ----------------
cover = Image.open(os.path.join(SRC, 'cover-raw.png')).convert('RGBA')
SIZE = cover.size
d = ImageDraw.Draw(cover)
LEFTPAD = 108

# 左侧加一层极淡的暗色渐变,保证标题在任何底图上都读得清
grad = Image.new('L', cover.size, 0)
gd = ImageDraw.Draw(grad)
for x in range(1100):
    gd.line([(x, 0), (x, cover.height)], fill=int(120 * max(0.0, 1 - x / 1100) ** 1.25))
cover.alpha_composite(Image.merge('RGBA', (
    Image.new('L', cover.size, 3), Image.new('L', cover.size, 8),
    Image.new('L', cover.size, 20), grad)))

title = '错题园丁'
ftitle = f(CJK_BLACK, 168)
draw_spaced(cover, d, (LEFTPAD, 232), title, ftitle, (255, 255, 255, 255), 6, (3, 5, 18, 200))

en = 'MISTAKE GARDENER'
fen = f(SANS_B, 47)
draw_spaced(cover, d, (LEFTPAD + 4, 448), en, fen, (103, 232, 249, 255), 9, (3, 5, 18, 190))

fsub = f(CJK_MED, 52)
d.text((LEFTPAD, 556), '会自己出题的英语打字陪练', font=fsub, fill=(226, 232, 240, 255),
       stroke_width=1, stroke_fill=(3, 5, 20, 160))

fsub2 = f(CJK_LIGHT, 38)
d.text((LEFTPAD, 646), '把每一次敲错，长成下一轮要练的词库', font=fsub2, fill=(148, 197, 190, 255))

# 关键词条:三行小标签,点出闭环
ftag = f(CJK_MED, 30)
tags = ['原始按键流', 'AI 归因', '自动出题', '浏览器自证']
y = 764
for i, t in enumerate(tags):
    w = text_w(d, t, ftag) + 40
    d.rounded_rectangle([LEFTPAD, y, LEFTPAD + w, y + 56], radius=28,
                        fill=(255, 255, 255, 26), outline=(103, 232, 249, 130), width=2)
    d.text((LEFTPAD + 20, y + 12), t, font=ftag, fill=(226, 232, 240, 255))
    LEFTPAD += w + 16

ffoot = f(CJK_LIGHT, 27)
d.text((108, 985), 'github.com/angri450/mistake-gardener', font=f(MONO, 28), fill=(148, 163, 184, 255))
d.text((108, 1028), '基于 Qwerty Learner (GPL-3.0) · 数据驱动判定与词库生成', font=ffoot, fill=(120, 140, 165, 255))

cover.convert('RGB').save(os.path.join(OUT, 'cover-16x9-1920x1080.png'), optimize=True)
cover.convert('RGB').save(os.path.join(OUT, 'cover-16x9-1920x1080.jpg'), quality=92, optimize=True)
print('封面写出: 1920x1080 png + jpg')

# ---------------- ICON ----------------
ic = Image.open(os.path.join(SRC, 'icon-keycap.png')).convert('RGB')
# 找到深色圆角块的范围(亮度阈值扫描),裁掉外圈浅色渐变
g = ic.convert('L')
px = g.load()
W, H = ic.size
xs = [x for x in range(W) if min(px[x, y] for y in range(0, H, 8)) < 70]
ys = [y for y in range(H) if min(px[x, y] for x in range(0, W, 8)) < 70]
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
pad = -16          # 内缩:深色圆角块的圆角比包围盒小,不内缩四角会露出外层浅色渐变
box = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad + 1), min(H, y1 + pad + 1))
core = ic.crop(box)
print('icon 核心区裁剪:', box, '->', core.size)
DARK = (11, 18, 32)


def square(size, name):
    """满幅版:四角按同一圆角半径补深色,避免露出外层背景"""
    img = core.resize((size, size), Image.LANCZOS).convert('RGB')
    m = Image.new('L', (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.215), fill=255)
    img.paste(Image.new('RGB', (size, size), DARK), (0, 0), Image.eval(m, lambda v: 255 - v))
    img.save(os.path.join(OUT, name), optimize=True)


square(1024, 'icon-1024-square.png')
square(512, 'icon-512-square.png')

# 圆角透明版(各平台通用的应用图标形态)
for size, name in ((512, 'icon-512.png'), (256, 'icon-256.png'), (128, 'icon-128.png')):
    img = core.resize((size, size), Image.LANCZOS).convert('RGBA')
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.putalpha(mask)
    img.save(os.path.join(OUT, name))
print('icon 写出: 方形 1024/512 + 圆角透明 512/256/128')

# ---------------- 封面用的方形图标(右上角小尺寸复用) ----------------
square(256, 'icon-256-square.png')
print('完成 ->', OUT)
