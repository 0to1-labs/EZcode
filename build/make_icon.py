# -*- coding: utf-8 -*-
"""生成 EZcode 品牌图标 (松鼠头像)。

输入: build/squirrel-source.png —— AI 生成的松鼠头像原图(右下角带生成水印)。
处理:
  1. 用「水平镜像的同位置背景块」+ 羽化 + 亮度对齐, 无痕抹掉右下角水印
  2. 圆角矩形遮罩, 输出透明底 RGBA
  3. 多尺寸 ICO (16~256), 512 PNG, 前端 logo PNG, 文档用 PNG

可重复执行: python build/make_icon.py
"""
import glob
import os
import shutil

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEBUI = os.path.join(ROOT, "app", "webui")
ASSETS = os.path.join(ROOT, "assets")
os.makedirs(ASSETS, exist_ok=True)

SOURCE = os.path.join(HERE, "squirrel-source.png")
if not os.path.exists(SOURCE):
    candidates = glob.glob(os.path.join(HERE, "*松鼠*.png"))
    if not candidates:
        raise SystemExit("缺少松鼠原图: build/*松鼠*.png")
    shutil.copyfile(sorted(candidates)[-1], SOURCE)

im = Image.open(SOURCE).convert("RGB")
S = im.size[0]
if im.size != (S, S):
    raise SystemExit("原图必须是正方形, 当前: %s" % (im.size,))
if S != 1024:
    im = im.resize((1024, 1024), Image.LANCZOS)
    S = 1024

# ---------- 1. 去水印 (右下角) ----------
# 水印区域保守外扩; 源块取水平镜像同位置(左下角), 那里同样是纯背景且渐变对称
BOX = (850, 900, S, S)
mirror = im.transpose(Image.FLIP_LEFT_RIGHT)
patch = mirror.crop(BOX)


def avg(img, box):
    crop = img.crop(box)
    small = crop.resize((1, 1), Image.BOX)
    return small.getpixel((0, 0))[:3]


# 亮度对齐: 让补丁顶部颜色与原图紧邻上方背景一致, 避免拼接色差
above = avg(im, (BOX[0], BOX[1] - 12, BOX[2], BOX[1] - 2))
ptop = avg(patch, (0, 0, patch.width, 10))
delta = tuple(int(a) - int(b) for a, b in zip(above, ptop))
if any(delta):
    chans = []
    for ch, d in zip(patch.split(), delta):
        chans.append(ch.point(lambda v, d=d: max(0, min(255, v + d))))
    patch = Image.merge("RGB", chans)

mask = Image.new("L", patch.size, 0)
ImageDraw.Draw(mask).rectangle((12, 12, patch.width, patch.height), fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(9))
im.paste(patch, (BOX[0], BOX[1]), mask)

# ---------- 2. 圆角遮罩 ----------
shape = Image.new("L", (S, S), 0)
ImageDraw.Draw(shape).rounded_rectangle((0, 0, S - 1, S - 1), radius=196, fill=255)
canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
canvas.paste(im.convert("RGBA"), (0, 0), shape)

big = canvas.resize((512, 512), Image.LANCZOS)

# ---------- 3. 输出 ----------
out_ico = os.path.join(HERE, "EZcode.ico")
out_png = os.path.join(HERE, "EZcode-icon.png")
out_web = os.path.join(WEBUI, "EZcode.png")
out_doc = os.path.join(ASSETS, "EZcode-icon.png")

big.save(out_png)
big.save(out_ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
canvas.resize((160, 160), Image.LANCZOS).save(out_web)
canvas.resize((256, 256), Image.LANCZOS).save(out_doc)
# 供人工检查 64px 实际观感
canvas.resize((64, 64), Image.LANCZOS).save(os.path.join(HERE, "_icon-64-preview.png"))

for p in (out_ico, out_png, out_web, out_doc):
    print("已生成 %-52s %7.1f KB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1024))
