"""Render a Russian test page image (screenshot-like)."""
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1500, 940
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)

FR = "C:\\Windows\\Fonts\\arial.ttf"
FRB = "C:\\Windows\\Fonts\\arialbd.ttf"
f_h1 = ImageFont.truetype(FRB, 40)
f_h2 = ImageFont.truetype(FRB, 27)
f_b = ImageFont.truetype(FR, 23)
f_s = ImageFont.truetype(FR, 21)

d.text((60, 50), "Отчёт о работе склада за ноябрь 2026 года", font=f_h1, fill="black")

d.text((60, 125), "Общие показатели", font=f_h2, fill="black")
lines = [
    "За отчётный период через склад прошло 48 219 единиц товара.",
    "Среднее время обработки одной заявки составило 12,4 минуты,",
    "что на 8% быстрее, чем в октябре.",
    "",
    "Ответственный за приёмку — Иванов Пётр Сергеевич.",
    "Контактный телефон: +7 (495) 123-45-67, доб. 14.",
]
y = 170
for ln in lines:
    d.text((60, y), ln, font=f_b, fill="black")
    y += 34

# Simple table
tx, ty = 60, y + 30
rows = [
    ["№", "Товар", "Кол-во", "Цена, ₽"],
    ["1", "Кабель UTP-5e", "1 250", "34,50"],
    ["2", "Роутер AC1200", "48", "3 190,00"],
    ["3", "Коммутатор 8 портов", "17", "2 745,90"],
]
colw = [70, 400, 180, 200]
rh = 46
for r, row in enumerate(rows):
    d.rectangle([tx, ty + r * rh, tx + sum(colw), ty + (r + 1) * rh], outline="black", width=1)
    cx = tx
    for c, cell in enumerate(row):
        fnt = f_s
        d.text((cx + 12, ty + r * rh + 11), cell, font=fnt, fill="black")
        if c < len(colw) - 1:
            cx += colw[c]
            d.line([cx, ty, cx, ty + len(rows) * rh], fill="black", width=1)

d.text((60, ty + len(rows) * rh + 30),
       "Итог: план выполнен на 104%. Документ подготовлен автоматически.",
       font=f_b, fill="black")

os.makedirs(r"C:\Users\Administrator\ocria-snap\samples", exist_ok=True)
img.save(r"C:\Users\Administrator\ocria-snap\samples\ru_test.png")
print("saved", img.size)
