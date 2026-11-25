"""Собираем .ico и .png из сгенерированной картинки: обрезаем поля, делаем набор размеров."""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "icon-source.png")
ICO = os.path.join(HERE, "assets", "ocria-snap.ico")
PNG = os.path.join(HERE, "assets", "ocria-snap.png")

img = Image.open(SRC).convert("RGB")
w, h = img.size

# ищем границы содержимого (фон очень тёмный и почти однородный)
gray = img.convert("L")
px = gray.load()
threshold = 55
minx, miny, maxx, maxy = w, h, 0, 0
step = max(1, min(w, h) // 400)
for y in range(0, h, step):
    for x in range(0, w, step):
        if px[x, y] > threshold:
            minx, miny = min(minx, x), min(miny, y)
            maxx, maxy = max(maxx, x), max(maxy, y)
if maxx <= minx or maxy <= miny:
    minx, miny, maxx, maxy = 0, 0, w - 1, h - 1

# квадрат + небольшой отступ
side = max(maxx - minx, maxy - miny)
pad = int(side * 0.06)
cx, cy = (minx + maxx) // 2, (miny + maxy) // 2
half = side // 2 + pad
box = (cx - half, cy - half, cx + half, cy + half)
box = (max(0, box[0]), max(0, box[1]),
       min(w, box[2]), min(h, box[3]))
cropped = img.crop(box)
print(f"исходник {w}x{h} -> обрезано до {cropped.size}")

# квадрат нужного размера
side = max(cropped.size)
square = Image.new("RGB", (side, side), img.getpixel((2, 2)))
square.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))

square.resize((512, 512), Image.LANCZOS).save(PNG)
square.resize((256, 256), Image.LANCZOS).save(
    ICO, format="ICO",
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
print("сохранено:", ICO)
print("сохранено:", PNG)

# контроль: как выглядит в 16 px
square.resize((16, 16), Image.LANCZOS).resize((64, 64), Image.NEAREST).save(
    os.path.join(HERE, "assets", "icon-16-preview.png"))
