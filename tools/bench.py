"""Бенчмарк качества OCR: светлые «документы» + тёмная тема с кодом + ложные срабатывания.

Считаем CER против эталона. Чем меньше — тем лучше.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import time

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ARIAL = r"C:\Windows\Fonts\arial.ttf"
SEGOE = "C:\\Windows\\Fonts\\segoeui.ttf"
CONSOLAS = "C:\\Windows\\Fonts\\consola.ttf"

BLOCKS = [
    ["Заявка № 4821 от 14.11.2026",
     "Клиент: ООО Ромашка",
     "Сумма: 128 400,00 руб.",
     "Статус: оплачено полностью"],
    ["Отчёт о работе склада за ноябрь 2026 года",
     "Среднее время обработки заявки — 12,4 минуты.",
     "Ответственный: Иванов Пётр Сергеевич.",
     "Телефон: +7 (495) 123-45-67, доб. 14"],
    ["Настройки соединения",
     "Роутер AC1200, IP 192.168.0.1",
     "Кабель UTP-5e, длина 305 метров",
     "Коммутатор на 8 портов, VLAN 20"],
]

CODE = [
    "# from itertools import *",
    "# k = 1",
    '# for i in product(sorted("СТЕКЛО"), repeat=6):',
    '#     word = "".join(i)',
    '#     if k % 2 == 0 and word[0] not in "СЕЛ"',
    "#     k += 1",
]

# проверка ложных срабатываний: русский текст с английскими словами в кавычках
ADV = [
    ['Откройте меню "Tools" и выберите "Open файл"'],
    ['Файл сохранён в папке "Documents", раздел "Setup"'],
    ['Нажмите "OK" или "Cancel" для отмены'],
]
SYNTAX_COMMENT = "#6a9955"


def render(lines, size, font_path=ARIAL, pad=14, gap=6, bg="white", fg="black"):
    font = ImageFont.truetype(font_path, size)
    d0 = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lh = d0.textbbox((0, 0), "Ай", font=font)[3]
    w = max(d0.textbbox((0, 0), ln, font=font)[2] for ln in lines) + pad * 2
    h = (lh + gap) * len(lines) + pad * 2
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    y = pad
    for ln in lines:
        d.text((pad, y), ln, font=font, fill=fg)
        y += lh + gap
    return img


def render_code(size, pad=12, gap=4):
    font = ImageFont.truetype(CONSOLAS, size)
    d0 = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lh = d0.textbbox((0, 0), "Ag", font=font)[3]
    w = max(d0.textbbox((0, 0), ln, font=font)[2] for ln in CODE) + pad * 2
    h = (lh + gap) * len(CODE) + pad * 2
    img = Image.new("RGB", (w, h), "#1e1e1e")
    d = ImageDraw.Draw(img)
    y = pad
    for ln in CODE:
        d.text((pad, y), ln, font=font, fill=SYNTAX_COMMENT)
        y += lh + gap
    return img


def make_samples():
    out = []
    for bi, block in enumerate(BLOCKS):
        for size in (12, 14, 18):
            fp = SEGOE if bi == 2 else ARIAL
            out.append((f"док{bi + 1}@{size}", render(block, size, fp), "\n".join(block)))
    for size in (11, 12, 14):
        out.append((f"код@{size}", render_code(size), "\n".join(CODE)))
    for i, block in enumerate(ADV):
        out.append((f"кавычки{i + 1}", render(block, 15), "\n".join(block)))
    return out


def norm(s: str) -> str:
    s = s.replace("«", '"').replace("»", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(truth: str, got: str) -> float:
    t, g = norm(truth), norm(got)
    return distance(t, g) / max(1, len(t))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    spec = importlib.util.spec_from_file_location("ocria_snap",
                                                  os.path.join(ROOT, "ocria_snap.pyw"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    detail = "--detail" in sys.argv
    with_ai = "--ai" in sys.argv

    variants = [
        {"name": "nano: было", "engine": "nano", "dpi": 250, "pre": False, "fix": False},
        {"name": "nano: стало", "engine": "nano", "dpi": 250, "pre": True, "fix": True},
    ]
    if with_ai:
        variants += [
            {"name": "OCRia-1 4B (x1)", "engine": "ai", "up": 1.0},
            {"name": "OCRia-1 4B (x2)", "engine": "ai", "up": 2.0},
        ]

    data = make_samples()
    print(f"Сэмплов: {len(data)} (документы, тёмный код, кавычки)"
          f"{'  + замер OCRia-1 (4B)' if with_ai else ''}\n")
    results = {}
    for v in variants:
        name = v["name"]
        if v["engine"] == "ai":
            ok, info = m.ai_status()
            if not ok:
                print(f"{name:26} пропущен: {info.splitlines()[0]}")
                continue
            m.CFG.setdefault("ai_engine", {})["upscale"] = v["up"]
        else:
            m.CFG["dpi"] = v["dpi"]
            m._parsers.pop("json", None)
            parser = m.get_parser("json")

        cers, per = [], []
        t0 = time.time()
        for label, img, truth in data:
            if v["engine"] == "ai":
                got = m.strip_markup(m.ollama_ocr(img))
            else:
                src = m.prepare_for_ocr(img) if v["pre"] else img
                res = parser.parse(m._tmp_png(src))
                pg = res.pages[0] if res.pages else None
                lines = [it.text for it in pg.text_items if it.text] if pg else []
                if v["fix"]:
                    lines = [m.fix_cyrillic(x) for x in lines]
                got = "\n".join(lines)
            c = cer(truth, got)
            cers.append(c)
            per.append((label, c, got))
        mean = sum(cers) / len(cers)
        results[name] = mean

        def grp(pfx):
            v2 = [c for (l, c, _) in per if l.startswith(pfx)]
            return sum(v2) / len(v2) * 100 if v2 else 0.0

        print(f"{name:26} CER {mean*100:5.1f}%   "
              f"(док {grp('док'):4.1f}  код {grp('код'):4.1f}  кавычки {grp('кавычки'):4.1f})  "
              f"{time.time()-t0:.1f}s")
        if detail:
            for label, c, got in per:
                if c > 0:
                    print(f"   {label:12} {c*100:5.1f}%")
                    print(f"      эталон: {truth_of(data, label)!r}")
                    print(f"      вышло:  {got!r}")
    if results:
        best = min(results, key=results.get)
        print(f"\nЛучший по CER: {best} ({results[best]*100:.1f}%)")


def truth_of(data, label):
    for lb, _, truth in data:
        if lb == label:
            return truth
    return ""


if __name__ == "__main__":
    main()
