"""Рекламный баннер OCRia Snap — рисуем локально, без внешних сервисов.

Запуск:  py tools-make-promo.py
Результат: assets\\promo.png  (1200x630, под соцсети и GitHub)
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(APP_DIR, "assets", "promo.png")

W, H = 1200, 630          # итоговый размер
S = 2                     # рисуем вдвое крупнее и уменьшаем — так края гладкие
CW, CH = W * S, H * S

# ---- палитра --------------------------------------------------------------- #
BG_TOP = (10, 16, 28)
BG_BOT = (18, 26, 43)
CYAN = (34, 211, 238)
CYAN_DARK = (8, 145, 178)
EMERALD = (52, 211, 153)
WHITE = (241, 245, 249)
MUTED = (148, 163, 184)
DIM = (100, 116, 139)
CARD = (15, 23, 42)
CARD_2 = (17, 24, 39)
LINE = (30, 41, 59)
SCREEN = (248, 250, 252)
INK = (51, 65, 85)
INK_SOFT = (71, 85, 105)
TINT = (223, 247, 251)     # голубая подсветка выделения на светлом
CHIP_BORDER = (22, 78, 99)
PANEL_BORDER = (22, 78, 99)

FONT_DIR = r"C:\Windows\Fonts"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size * S)


def px(v: float) -> int:
    return int(round(v * S))


def gradient() -> Image.Image:
    strip = Image.new("RGB", (1, CH))
    for y in range(CH):
        t = y / (CH - 1)
        strip.putpixel((0, y), tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    return strip.resize((CW, CH)).convert("RGBA")


def add_glow(img: Image.Image, cx: float, cy: float, r: float, color, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(px(70)))
    return Image.alpha_composite(img, layer)


def dashed_rect(d, box, color, width=2, dash=8, gap=6):
    x1, y1, x2, y2 = box
    for edge in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        ax, ay, bx, by = edge
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        if not length:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            d.line([px(ax + ux * pos), px(ay + uy * pos),
                    px(ax + ux * end), px(ay + uy * end)],
                   fill=color, width=px(width))
            pos += dash + gap


def main() -> None:
    canvas = gradient()
    canvas = add_glow(canvas, 300, 340, 380, CYAN, 46)
    canvas = add_glow(canvas, 980, 520, 300, EMERALD, 22)

    d = ImageDraw.Draw(canvas)

    # сетка (сплошной чуть более светлый тон — на RGBA прозрачность не смешивается)
    for x in range(0, W, 40):
        d.line([px(x), 0, px(x), CH], fill=(22, 33, 51), width=1)
    for y in range(0, H, 40):
        d.line([0, px(y), CW, px(y)], fill=(22, 33, 51), width=1)

    # ---------------- шапка ---------------- #
    # логотип
    d.rounded_rectangle([px(64), px(52), px(108), px(96)], radius=px(12),
                        fill=CYAN, outline=None)
    d.text((px(86), px(74)), "O", font=font("segoeuib.ttf", 26), fill=(8, 16, 28), anchor="mm")

    d.text((px(124), px(56)), "OCRia Snap", font=font("segoeuib.ttf", 40), fill=WHITE)
    d.text((px(126), px(106)),
           "Скриншот → текст. Выделяйте мышью, копируйте одним Ctrl+C.",
           font=font("segoeui.ttf", 17), fill=MUTED)

    # хоткей справа
    d.rounded_rectangle([px(884), px(56), px(1136), px(98)], radius=px(12),
                        fill=CARD, outline=CHIP_BORDER, width=2)
    d.text((px(1010), px(77)), "Win + Shift + S", font=font("seguisb.ttf", 16),
           fill=(103, 232, 249), anchor="mm")

    # ---------------- экран со сканом ---------------- #
    d.rounded_rectangle([px(64), px(176), px(684), px(528)], radius=px(18),
                        fill=CARD, outline=LINE, width=2)
    d.rounded_rectangle([px(76), px(188), px(672), px(516)], radius=px(10), fill=SCREEN)
    # верхняя панель окна
    d.line([px(76), px(222), px(672), px(222)], fill=(226, 232, 240), width=px(2))
    for i, c in enumerate(((248, 113, 113), (251, 191, 36), (52, 211, 153))):
        d.ellipse([px(92 + i * 16), px(199), px(102 + i * 16), px(209)], fill=c)

    # документ
    d.text((px(100), px(240)), "ДОГОВОР № 17-Б", font=font("segoeuib.ttf", 15), fill=INK)
    d.text((px(100), px(264)), "от 3 марта 2026 года", font=font("segoeui.ttf", 11), fill=INK_SOFT)

    # выделение области — подложка ДО текста, чтобы текст оставался читаемым
    sel = (96, 286, 616, 382)
    d.rounded_rectangle([px(sel[0]), px(sel[1]), px(sel[2]), px(sel[3])], radius=px(4),
                        fill=TINT)

    lines = [
        "Исполнитель обязуется оказать услуги по ремонту",
        "помещения, а Заказчик обязуется принять и оплатить",
        "их. Работы выполняются в течение десяти дней.",
        "Стоимость работ составляет 128 400,00 рублей.",
    ]
    for i, ln in enumerate(lines):
        d.text((px(100), px(292 + i * 20)), ln, font=font("segoeui.ttf", 11), fill=INK_SOFT)

    # небольшая таблица в документе — намёк, что таблицы тоже распознаются
    tx1, tx2 = 100, 620
    for ty in (410, 440, 470):
        d.line([px(tx1), px(ty), px(tx2), px(ty)], fill=(203, 213, 225), width=px(1))
    d.line([px(420), px(410), px(420), px(470)], fill=(203, 213, 225), width=px(1))
    d.text((px(tx1 + 8), px(422)), "Наименование", font=font("seguisb.ttf", 10), fill=INK_SOFT)
    d.text((px(428), px(422)), "Сумма", font=font("seguisb.ttf", 10), fill=INK_SOFT)
    d.text((px(tx1 + 8), px(452)), "Ремонт помещения", font=font("segoeui.ttf", 10), fill=INK_SOFT)
    d.text((px(428), px(452)), "128 400,00", font=font("segoeui.ttf", 10), fill=INK_SOFT)

    dashed_rect(d, sel, CYAN_DARK, width=2, dash=7, gap=5)
    for cx, cy in ((sel[0], sel[1]), (sel[2], sel[1]), (sel[0], sel[3]), (sel[2], sel[3])):
        d.rectangle([px(cx - 3), px(cy - 3), px(cx + 3), px(cy + 3)], fill=CYAN_DARK)

    # курсор мыши
    cur = [(604, 372), (604, 392), (609, 387), (613, 396), (616, 395), (612, 386), (618, 386)]
    d.polygon([(px(x), px(y)) for x, y in cur], fill=(15, 23, 42), outline=WHITE)

    # ---------------- панель с текстом ---------------- #
    d.rounded_rectangle([px(716), px(176), px(1136), px(528)], radius=px(18),
                        fill=CARD, outline=PANEL_BORDER, width=2)
    d.ellipse([px(740), px(200), px(750), px(210)], fill=EMERALD)
    d.text((px(760), px(205)), "Распознанный текст", font=font("seguisb.ttf", 14),
           fill=WHITE, anchor="lm")
    d.rounded_rectangle([px(1016), px(194), px(1112), px(216)], radius=px(8),
                        fill=CARD_2, outline=CHIP_BORDER, width=1)
    d.text((px(1064), px(205)), "OCRia-1 4B", font=font("seguisb.ttf", 10),
           fill=(103, 232, 249), anchor="mm")

    # подсветка «выделенного» куска текста (за строкой, текст рисуется поверх)
    d.rectangle([px(740), px(314), px(1108), px(334)], fill=(14, 56, 74))

    panel_lines = [
        "Договор № 17-Б от 3 марта 2026 года.",
        "Исполнитель обязуется оказать услуги по",
        "ремонту помещения, а Заказчик обязуется",
        "принять и оплатить их. Работы выполняются",
        "в течение десяти дней. Стоимость работ",
        "составляет 128 400,00 рублей.",
    ]
    for i, ln in enumerate(panel_lines):
        d.text((px(744), px(252 + i * 22)), ln, font=font("segoeui.ttf", 11), fill=(203, 213, 225))

    # кнопки
    d.rounded_rectangle([px(740), px(462), px(922), px(500)], radius=px(10), fill=CYAN)
    d.text((px(831), px(481)), "Копировать текст", font=font("seguisb.ttf", 12),
           fill=(8, 16, 28), anchor="mm")
    d.rounded_rectangle([px(932), px(462), px(1112), px(500)], radius=px(10),
                        fill=CARD_2, outline=CHIP_BORDER, width=2)
    d.text((px(1022), px(481)), "Копировать скриншот", font=font("seguisb.ttf", 12),
           fill=(103, 232, 249), anchor="mm")

    # ---------------- чипы функций ---------------- #
    chips = [
        "Быстрый OCR",
        "4B-модель",
        "Формулы",
        "Проверка текста",
        "Скриншот в буфер",
        "Win+Shift+S",
    ]
    x, y, w, h, gap = 64, 560, 168, 40, 10
    for i, label in enumerate(chips):
        bx = x + i * (w + gap)
        d.rounded_rectangle([px(bx), px(y), px(bx + w), px(y + h)], radius=px(11),
                            fill=CARD, outline=LINE, width=2)
        d.ellipse([px(bx + 14), px(y + 17), px(bx + 22), px(y + 25)], fill=CYAN)
        d.text((px(bx + 30), px(y + h / 2)), label, font=font("seguisb.ttf", 12),
               fill=(203, 213, 225), anchor="lm")

    d.text((px(1136), px(614)), "работает в трее · автозапуск · offline",
           font=font("segoeui.ttf", 10), fill=DIM, anchor="ra")

    canvas = canvas.resize((W, H), Image.LANCZOS).convert("RGB")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    canvas.save(OUT, "PNG", optimize=True)
    print("готово:", OUT)


if __name__ == "__main__":
    main()
