#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OCRia Snap — свой «Lightshot» с OCR (локально, на CPU).

Один поток, без второго окна:
    хоткей -> экран замер и потемнел -> тяните рамку (картинка заморожена, можно не спешить)
    -> отпустили -> под рамкой сразу появляется текстовый слой
    -> тяните мышью по словам, Ctrl+C копирует выделенное. Картинка зоны — отдельной кнопкой.
Сам по себе ничего не копирует: только Ctrl+C или кнопка.

Хоткеи по умолчанию:
    Win+Shift+S / PrintScreen   режим выделения области (после «отключения ножниц винды»)
    Ctrl+Alt+V                  то же для картинки из буфера обмена
    Ctrl+Alt+S                  выделить область -> молча весь текст в буфер, без окна

Движок: LiteParse + модели OCRia-nano-1 (8.1M параметров) на ONNX Runtime, CPU.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CAPTURES_DIR = os.path.join(APP_DIR, "captures")

DEFAULTS = {
    "hotkeys": {
        "snip": ["win+shift+s", "win+shift+x"],
        "snip_image": ["win+ctrl+shift+s"],
        "snip_ai": ["ctrl+alt+a"],
        "clipboard": ["ctrl+alt+v"],
        "silent_copy": ["ctrl+alt+s"],
    },
    "ai_engine": {
        "url": "http://127.0.0.1:11434",
        "model": "llamaindex/ocria-1:q8_0",
        "keep_alive": "2m",       # через сколько освобождать видеопамять после простоя
        "num_ctx": 8192,
        "num_predict": 4096,
        "max_pixels": 4100000,
        "upscale": 1.0,           # растяжение мелких скриншотов (1.0 — замером оказалось лучше и быстрее)
    },
    # решалка простых задач: OCRia-1 только читает текст, считает обычная языковая модель
    "solver": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:1.5b",
        "keep_alive": "2m",
        "num_ctx": 6144,
        "num_predict": 1024,
        "tools": True,            # разрешить калькулятор (точная арифметика)
        "prompt": None,
    },
    "clipboard_action": "off",   # следить за буфером (Win+Shift+S): "overlay" | "copy" | "off"
    "copy_on_select": False,     # True: копировать сразу по отпусканию мыши (без Ctrl+C)
    "save_captures": True,       # складывать снимки в captures/
    "markup_visible": True,      # сразу показывать подчёркивание слов
    "fix_cyrillic": True,        # лечить латинские двойники кириллицы
    "engine": "nano",            # движок по умолчанию: "nano" (быстрый) | "ai" (OCRia-1 4B)
    "checker": {
        "engine": "rules",       # проверка текста: "rules" — LanguageTool, "model" — языковой моделью
    },
    "preprocess": True,          # тёмный фон -> инверсия, серый + автоконтраст перед OCR
    "output_format": "text",     # формат для тихого режима: "text" | "markdown"
    "dpi": 250,                  # DPI рендера страницы для OCR
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        cfg.update(data)
        if isinstance(data.get("hotkeys"), dict):
            hk = dict(DEFAULTS["hotkeys"])
            hk.update(data["hotkeys"])
            cfg["hotkeys"] = hk
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return cfg


CFG = load_config()

ICON_ICO = os.path.join(APP_DIR, "assets", "ocria-snap.ico")
ICON_PNG = os.path.join(APP_DIR, "assets", "ocria-snap.png")
LOG_PATH = os.path.join(APP_DIR, "ocria-snap.log")


def log(msg: str) -> None:
    """Журнал в файл: у pythonw нет консоли, иначе ошибки пропадают молча."""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def log_exc(where: str) -> None:
    log(f"{where}: {traceback.format_exc()}")


def save_config() -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
    except Exception:
        log_exc("save_config")


# --------------------------------------------------------------------------- #
# Windows helpers
# --------------------------------------------------------------------------- #
def set_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def single_instance() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\OCRIASnap_SingleInstance_v1")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32

_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_u32.OpenClipboard.argtypes = [ctypes.c_void_p]
_u32.OpenClipboard.restype = ctypes.c_int
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardSequenceNumber.restype = ctypes.c_ulong

CF_UNICODETEXT = 13
CF_DIB = 8
GMEM_MOVEABLE = 0x0002

MOD_KEYS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
            "win": 0x5B, "super": 0x5B, "meta": 0x5B}
MOD_MASK = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
SPECIAL_KEYS = {"printscreen": 0x2C, "prtsc": 0x2C, "prtscr": 0x2C,
                "insert": 0x2D, "ins": 0x2D, "pause": 0x13, "scrolllock": 0x91}
ALL_MODS = ("ctrl", "alt", "shift", "win")
ACTIONS = ("snip", "snip_image", "snip_ai", "clipboard", "silent_copy")
_CANON_MOD = {"control": "ctrl", "super": "win", "meta": "win"}


def _dib_bytes(image) -> bytes:
    import io

    buf = io.BytesIO()
    image.convert("RGB").save(buf, "BMP")
    return buf.getvalue()[14:]  # BITMAPFILEHEADER не нужен для CF_DIB


def _set_clip_block(fmt: int, payload: bytes, what: str) -> None:
    h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not h:
        raise MemoryError(f"GlobalAlloc for {what} failed")
    p = _k32.GlobalLock(h)
    if not p:
        _k32.GlobalFree(h)
        raise MemoryError(f"GlobalLock for {what} failed")
    ctypes.memmove(p, payload, len(payload))
    _k32.GlobalUnlock(h)
    if not _u32.SetClipboardData(fmt, h):
        _k32.GlobalFree(h)
        raise OSError(f"SetClipboardData({what}) failed")


def set_clipboard(text: str = "", image=None, attempts: int = 6) -> bool:
    """Кладём текст и/или картинку. Текст — первым, чтобы сбой картинки его не снёс."""
    data = (text.encode("utf-16-le") + b"\x00\x00") if text else None
    dib = None
    if image is not None:
        try:
            dib = _dib_bytes(image)
        except Exception:
            dib = None
    last = None
    for i in range(attempts):
        if not _u32.OpenClipboard(None):
            last = OSError("буфер обмена занят другой программой")
            time.sleep(0.04 * (i + 1))
            continue
        try:
            _u32.EmptyClipboard()
            if data is not None:
                _set_clip_block(CF_UNICODETEXT, data, "текст")
            if dib is not None:
                try:
                    _set_clip_block(CF_DIB, dib, "картинка")
                except Exception:
                    pass  # текст уже в буфере, картинка не критична
            return True
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            _u32.CloseClipboard()
        time.sleep(0.04 * (i + 1))
    if last is not None:
        raise last
    return False


def get_clipboard_text(attempts: int = 4) -> str:
    for i in range(attempts):
        if _u32.OpenClipboard(None):
            try:
                h = _u32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""  # буфер открылся, текста в нём просто нет
                ptr = _k32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    _k32.GlobalUnlock(h)
            finally:
                _u32.CloseClipboard()
        time.sleep(0.03 * (i + 1))
    return ""


# --------------------------------------------------------------------------- #
# Постобработка: латинские «двойники» кириллицы
# --------------------------------------------------------------------------- #
_LATIN = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_CYR = set("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_SAFE = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "k": "к", "m": "м", "r": "г",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
}
# модель иногда выдаёт греческие двойники вместо кириллицы: "Отκройте"
_GREEK = {
    "κ": "к", "ο": "о", "α": "а", "ε": "е", "ρ": "р", "τ": "т", "χ": "х",
    "Α": "А", "Β": "В", "Ε": "Е", "Η": "Н", "Κ": "К", "Μ": "М", "Ο": "О",
    "Ρ": "Р", "Τ": "Т", "Χ": "Х", "Υ": "У",
}
# внутри кавычек текст обычно естественный, а не код — там можно смелее
_IN_QUOTES = dict(_SAFE)
_IN_QUOTES.update({"n": "п", "b": "в", "h": "н", "u": "и", "t": "т", "0": "о"})
_WORD = re.compile(r"[A-Za-z0-9\u0370-\u03ff\u0400-\u04ff]+")
_OPEN_Q = {'"', "'", "«", "“", "„"}
_CLOSE_Q = {'"', "'", "»", "”"}


def _has_cyr(s: str) -> bool:
    return any(c in _CYR for c in s)


def _quote_ranges(text: str):
    ranges, opened = [], None
    for i, ch in enumerate(text):
        if opened is None and ch in _OPEN_Q:
            opened = i
        elif opened is not None and ch in _CLOSE_Q:
            ranges.append((opened, i))
            opened = None
    if opened is not None:
        ranges.append((opened, len(text)))
    return ranges


def _fix_token(tok: str, in_quotes: bool, prev_tok: str, next_tok: str) -> str:
    letters = [c for c in tok if c in _LATIN or c in _CYR]
    if not letters:
        return tok

    # 1) кавычки + слово целиком из двойников + заглавных не меньше, чем строчных:
    #    "CTEKno" -> "СТЕКпО" (модель отдала кириллицу латинице, если строка кода латинская)
    up = sum(1 for c in tok if c.isupper())
    low = sum(1 for c in tok if c.islower())
    convertible = all(c in _IN_QUOTES or c in _CYR for c in tok)
    if in_quotes and len(tok) >= 3 and convertible and up >= low:
        tok = "".join(_IN_QUOTES.get(c, c) for c in tok)

    # 2) набор из O/o/0 рядом с кириллицей — это кириллическая О: "O00" -> "ООО"
    elif (set(tok) <= {"O", "o", "0"} and any(c in "Oo" for c in tok)
          and (_has_cyr(prev_tok) or _has_cyr(next_tok))):
        tok = ("О" if "O" in tok else "о") * len(tok)

    # 2b) одиночная буква-двойник рядом с кириллицей: "o работе" -> "о работе"
    elif len(tok) == 1 and tok in _SAFE and (_has_cyr(prev_tok) or _has_cyr(next_tok)):
        tok = _SAFE[tok]

    non_cyr = sum(1 for c in tok if c in _LATIN or c in _GREEK)
    cyr = sum(1 for c in tok if c in _CYR)
    if non_cyr and cyr >= non_cyr:
        tok = "".join(_SAFE.get(c) or _GREEK.get(c) or c for c in tok)

    # 3) заглавная в середине слова — почти всегда ошибка: "портоВ" -> "портов"
    chars = list(tok)
    for i in range(1, len(chars)):
        if chars[i] in _CYR and chars[i].isupper() and chars[i - 1].islower():
            chars[i] = chars[i].lower()
    return "".join(chars)


def _sentence_case(tok: str, prev_tok: str, is_first: bool, in_quotes: bool) -> str:
    """Заглавная сразу после числа: "4821 От" -> "4821 от".

    Только этот узкий случай: любое более широкое правило начинает ломать заголовки
    и имена собственные ("Иванов" -> "иванов").
    """
    if is_first or in_quotes or len(tok) < 2 or tok[0] not in _CYR or not tok[0].isupper():
        return tok
    p = prev_tok.rstrip()
    if not p:
        return tok
    if p.replace(".", "").replace(",", "").replace("\u00a0", "").isdigit():
        return tok[0].lower() + tok[1:]
    return tok


def fix_cyrillic(text: str) -> str:
    # построчно: иначе правила прыгают через переносы строк и портят начало строк
    if "\n" in text:
        return "\n".join(fix_cyrillic(part) for part in text.split("\n"))
    tokens = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(text)]
    if not tokens:
        return text
    qr = _quote_ranges(text)
    out, pos = [], 0
    for i, (s, e, tok) in enumerate(tokens):
        prev_tok = tokens[i - 1][2] if i > 0 else ""
        next_tok = tokens[i + 1][2] if i + 1 < len(tokens) else ""
        in_quotes = any(s >= a and e <= b for a, b in qr)
        fixed = _fix_token(tok, in_quotes, prev_tok, next_tok)
        fixed = _sentence_case(fixed, prev_tok, i == 0, in_quotes)
        out.append(text[pos:s])
        out.append(fixed)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- #
# Подготовка картинки перед OCR (замерено: помогает на тёмных темах)
# --------------------------------------------------------------------------- #
DARK_BG_SHARE = 0.55  # доля тёмных пикселей, выше которой считаем фон тёмным


def prepare_for_ocr(image):
    """Серый + автоконтраст; если фон тёмный (тёмная тема) — предварительно инвертируем.

    Размер картинки не меняется, поэтому координаты слов остаются валидными.
    Апскейл намеренно НЕ делаем: замеры показали, что он только ухудшает (CER 5% -> 10%).
    """
    if not CFG.get("preprocess", True):
        return image
    from PIL import ImageOps

    g = image.convert("L")
    hist = g.histogram()
    total = sum(hist) or 1
    if sum(hist[:100]) / total > DARK_BG_SHARE:
        g = ImageOps.invert(g)
    return ImageOps.autocontrast(g).convert("RGB")


# --------------------------------------------------------------------------- #
# Разбиение строки OCR на слова (модель отдаёт боксы только по строкам)
# --------------------------------------------------------------------------- #
_NARROW = set("iljtfr.,:;!|'`()[]{}/\\-")
_WIDE = set("MWЖШЩЮФМmшщюw")


def _char_weight(ch: str) -> float:
    if ch == " ":
        return 0.30
    if ch in _NARROW:
        return 0.36
    if ch in _WIDE:
        return 1.35
    if ch.isdigit():
        return 0.62
    if ch.isupper():
        return 1.12
    return 0.98


def split_line_words(text: str, bx: float, by: float, bw: float, bh: float):
    tokens = text.split()
    if not tokens:
        return []
    if len(tokens) == 1:
        return [{"text": tokens[0], "x": bx, "y": by, "w": bw, "h": bh}]
    total = sum(_char_weight(c) for c in text) or 1.0
    scale = bw / total
    out, pos, adv = [], 0, 0.0
    for tok in tokens:
        start = text.find(tok, pos)
        if start < 0:
            start = pos
        adv += sum(_char_weight(c) for c in text[pos:start])
        wt = sum(_char_weight(c) for c in tok)
        out.append({"text": tok, "x": bx + adv * scale, "y": by, "w": wt * scale, "h": bh})
        adv += wt
        pos = start + len(tok)
    return out


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
_parser_lock = threading.Lock()
_parsers: dict = {}


def get_parser(kind: str):
    with _parser_lock:
        if kind not in _parsers:
            from liteparse import LiteParse

            if kind == "json":
                kwargs = dict(ocr_backend="ocria-nano", output_format="json", quiet=True)
            else:
                kwargs = dict(ocr_backend="ocria-nano", quiet=True,
                              output_format=CFG.get("output_format") or "text")
            if CFG.get("dpi"):
                kwargs["dpi"] = float(CFG["dpi"])
            _parsers[kind] = LiteParse(**kwargs)
    return _parsers[kind]


def _tmp_png(image) -> str:
    path = os.path.join(tempfile.gettempdir(), f"ocria_snap_{int(time.time()*1000)}.png")
    image.save(path)
    return path


def _parse_temp(image, kind: str):
    """Разбираем картинку через временный png и сразу его удаляем."""
    path = _tmp_png(image)
    try:
        return get_parser(kind).parse(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def clean_temp_dir() -> int:
    """Убираем осиротевшие временные png от прошлых запусков."""
    import glob

    n = 0
    for p in glob.glob(os.path.join(tempfile.gettempdir(), "ocria_snap_*.png")):
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def save_capture(image):
    if not CFG.get("save_captures", True):
        return None
    try:
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        path = os.path.join(CAPTURES_DIR, time.strftime("snip-%Y%m%d-%H%M%S") + ".png")
        image.save(path)
        return path
    except Exception:
        return None


def ocr_image(image) -> str:
    result = _parse_temp(prepare_for_ocr(image), "text")
    text = clean_text(getattr(result, "text", "") or "")
    return fix_cyrillic(text) if CFG.get("fix_cyrillic", True) else text


def ocr_image_words(image):
    """Слова с координатами в пикселях картинки, в порядке чтения."""
    result = _parse_temp(prepare_for_ocr(image), "json")
    pages = getattr(result, "pages", None) or []
    if not pages:
        return []
    pg = pages[0]
    sx = (image.width / pg.width) if pg.width else 1.0
    sy = (image.height / pg.height) if pg.height else 1.0
    words = []
    for line_no, item in enumerate(pg.text_items):
        text = clean_text(item.text or "")
        if CFG.get("fix_cyrillic", True):
            text = fix_cyrillic(text)
        if not text:
            continue
        for w in split_line_words(text, item.x * sx, item.y * sy,
                                  item.width * sx, item.height * sy):
            w["line"] = line_no
            words.append(w)
    return words


# --------------------------------------------------------------------------- #
# OCRia-1 (4B) через Ollama — точный движок для сложных случаев
# --------------------------------------------------------------------------- #
def ai_cfg() -> dict:
    cfg = dict(DEFAULTS["ai_engine"])
    cfg.update(CFG.get("ai_engine") or {})
    return cfg


def _http_json(url: str, payload=None, timeout: float = 5.0):
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ai_status():
    """(готова ли модель, текст статуса)."""
    cfg = ai_cfg()
    try:
        tags = _http_json(cfg["url"].rstrip("/") + "/api/tags", timeout=3)
    except Exception as exc:
        return False, f"Ollama не отвечает ({exc}).\nЗапустите Ollama и повторите."
    names = [m.get("name", "") for m in tags.get("models", [])]
    want = cfg["model"]
    base = want.split(":")[0]
    if want in names or any(n.startswith(base + ":") for n in names):
        return True, want
    return False, (f"Модель {want} не найдена.\nСкачайте:  ollama pull {want}")


_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)(?:\n```|$)", re.S)


def cut_loop(text: str, min_repeat: int = 2000, max_period: int = 256,
             min_periods: int = 3) -> str:
    """Обрезаем зацикливание: модель иногда повторяет один блок до лимита токенов.

    Ищем наименьший период, при котором хвост строго периодичен, и оставляем один повтор.
    """
    n = len(text)
    if n < min_repeat:
        return text
    for p in range(1, max_period + 1):
        need = max(min_repeat, min_periods * p)
        if n < need:
            break
        i0 = n - need + p
        ok = True
        for i in range(i0, n):
            if text[i] != text[i - p]:
                ok = False
                break
        if not ok:
            continue
        while i0 - p - 1 >= 0 and text[i0 - 1] == text[i0 - 1 - p]:
            i0 -= 1
        return text[: i0]
    return text


def strip_markup(text: str) -> str:
    """Markdown/HTML от модели -> обычный текст, удобный для копирования."""
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    text = re.sub(r"</t[dh]>", "\t", text)
    text = re.sub(r"</tr>", "\n", text)
    text = re.sub(r"<(?:br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ollama_ocr(image) -> str:
    """Отправляем картинку в OCRia-1 через Ollama и получаем разметку страницы."""
    import base64
    import io

    from PIL import Image

    cfg = ai_cfg()
    img = image.convert("RGB")
    up = float(cfg.get("upscale") or 1.0)
    if up > 1.0 and max(img.size) < 1000:
        img = img.resize((int(img.width * up), int(img.height * up)), Image.LANCZOS)
    max_px = int(cfg.get("max_pixels") or 4100000)
    if img.width * img.height > max_px:
        k = (max_px / float(img.width * img.height)) ** 0.5
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    body = {
        "model": cfg["model"],
        "think": False,
        "stream": False,
        "keep_alive": cfg.get("keep_alive", "2m"),
        "options": {
            "temperature": 0,
            "num_ctx": int(cfg.get("num_ctx", 8192)),
            "num_predict": int(cfg.get("num_predict", 4096)),
        },
        "messages": [{"role": "user", "content": "", "images": [
            base64.b64encode(buf.getvalue()).decode("ascii")]}],
    }
    reply = None
    last = None
    for attempt in range(4):
        try:
            reply = _http_json(cfg["url"].rstrip("/") + "/api/chat", body, timeout=900)
            break
        except Exception as exc:  # обрыв соединения, модель выгрузилась, 500 и т.п.
            last = exc
            log(f"OCRia-1, попытка {attempt + 1} не удалась: {exc}")
            time.sleep(0.7 * (2 ** attempt))
    if reply is None:
        raise last if last else RuntimeError("Ollama не ответил")
    text = (reply.get("message") or {}).get("content", "") or ""
    return cut_loop(text)


def ollama_load_model() -> bool:
    """Прогреть модель: загрузить веса в видеопамять заранее (несколько минут)."""
    cfg = ai_cfg()
    body = {
        "model": cfg["model"],
        "prompt": "1",
        "stream": False,
        "keep_alive": cfg.get("keep_alive", "2m"),
        "options": {"num_predict": 1, "num_ctx": int(cfg.get("num_ctx", 8192))},
    }
    try:
        _http_json(cfg["url"].rstrip("/") + "/api/generate", body, timeout=900)
        return True
    except Exception:
        return False


def ollama_unload_model() -> bool:
    """Выгрузить модель и сразу освободить видеопамять."""
    cfg = ai_cfg()
    try:
        _http_json(cfg["url"].rstrip("/") + "/api/generate",
                   {"model": cfg["model"], "keep_alive": 0}, timeout=30)
        return True
    except Exception:
        return False


def ollama_loaded() -> bool:
    """Загружена ли модель в память прямо сейчас."""
    cfg = ai_cfg()
    try:
        ps = _http_json(cfg["url"].rstrip("/") + "/api/ps", timeout=4)
    except Exception:
        return False
    return any(m.get("name") == cfg["model"] for m in (ps.get("models") or []))


def solver_cfg() -> dict:
    cfg = dict(DEFAULTS["solver"])
    cfg.update(CFG.get("solver") or {})
    return cfg


SOLVE_PROMPT = (
    "Тебе дают текст, распознанный со скриншота. Реши то, что в нём требуется: уравнение, пример, "
    "вычисление, заполни пустые ячейки таблицы. Если задачи нет — коротко скажи, о чём текст.\n"
    "ВАЖНО про числа: в русском тексте запятая — десятичный разделитель. Значит 34,50 — это 34,5, "
    "а 1 250 — это 1250. Не принимай запятую за разделитель тысяч.\n"
    "Для любых вычислений вызывай инструмент calc: он считает точно, а в уме ошибаться нельзя.\n"
    "В конце обязательно проверь ответ подстановкой или обратным действием.\n"
    "Отвечай коротко, не больше шести строк: сначала ответ, потом ключевые шаги.\n"
    "Формулы записывай в LaTeX с обратным слешем: $\\sqrt{2x-3}$, $\\frac{a}{b}$."
)


CALC_TOOL = {
    "type": "function",
    "function": {
        "name": "calc",
        "description": "Точно вычислить арифметическое выражение. Обязательно используй для "
                       "любых вычислений, особенно умножения, деления и процентов: в уме не считай.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string",
                               "description": "арифметика с числами, например 1250 * 34.5"},
            },
            "required": ["expression"],
        },
    },
}

_CALC_OPS = None


def safe_calc(expression: str) -> str:
    """Считаем выражение сами: только арифметика через разбор AST, без eval."""
    import ast as _ast
    import operator

    ops = {_ast.Add: operator.add, _ast.Sub: operator.sub, _ast.Mult: operator.mul,
           _ast.Div: operator.truediv, _ast.FloorDiv: operator.floordiv,
           _ast.Mod: operator.mod, _ast.Pow: operator.pow,
           _ast.USub: operator.neg, _ast.UAdd: operator.pos}

    def ev(node):
        if isinstance(node, _ast.Expression):
            return ev(node.body)
        if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, _ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError("разрешена только арифметика")

    text = (expression or "").replace("×", "*").replace("÷", "/").replace("^", "**")
    text = text.replace(",", ".").replace("=", " ").replace(" ", "")
    value = ev(_ast.parse(text, mode="eval"))
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.6g}"
    return str(value)


_JSON_CALL = re.compile(r'\{\s*"name"\s*:\s*"calc"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}', re.S)


def _calc_from_content(text: str):
    """Некоторые модели пишут вызов инструмента текстом, а не структурой — вытащим и это."""
    m = _JSON_CALL.search(text or "")
    if not m:
        return None
    try:
        args = json.loads(m.group(1))
        return str(args.get("expression", ""))
    except Exception:
        return None


def ollama_solve(text: str) -> str:
    """Отправляем распознанный текст модели; арифметику она считает калькулятором."""
    cfg = solver_cfg()
    messages = [
        {"role": "system", "content": cfg.get("prompt") or SOLVE_PROMPT},
        {"role": "user", "content": text},
    ]
    use_tools = bool(cfg.get("tools", True))
    last_content = ""
    last = None

    for _round in range(5):
        body = {
            "model": cfg["model"],
            "stream": False,
            "think": False,
            "keep_alive": cfg.get("keep_alive", "2m"),
            "messages": messages,
            "options": {
                "temperature": 0,
                "num_ctx": int(cfg.get("num_ctx", 6144)),
                "num_predict": int(cfg.get("num_predict", 1024)),
            },
        }
        if use_tools:
            body["tools"] = [CALC_TOOL]

        reply = None
        for attempt in range(3):
            try:
                reply = _http_json(cfg["url"].rstrip("/") + "/api/chat", body, timeout=600)
                break
            except Exception as exc:
                last = exc
                log(f"решалка, попытка {attempt + 1}: {exc}")
                time.sleep(0.7 * (2 ** attempt))
        if reply is None:
            raise last if last else RuntimeError("модель не ответила")

        msg = reply.get("message") or {}
        content = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        if not calls:
            guess = _calc_from_content(content)
            if guess:
                calls = [{"function": {"name": "calc", "arguments": {"expression": guess}}}]
            else:
                return cut_loop(content)

        if content:
            last_content = content
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"expression": args}
            expr = (args or {}).get("expression", "")
            try:
                result = safe_calc(str(expr))
            except Exception as exc:
                result = f"не смог вычислить: {exc}"
            log(f"калькулятор: {expr} = {result}")
            messages.append({"role": "tool", "content": str(result),
                             "tool_name": fn.get("name") or "calc"})

    return cut_loop(last_content) or "модель слишком долго вызывает калькулятор"


CHECKER_PROMPT = (
    "Ты корректор. Тебе дают текст, распознанный со скриншота. Исправь в нём ТОЛЬКО ошибки:\n"
    "— орфография: русские слова по правилам русского языка, английские — по правилам английского;\n"
    "— пунктуация: запятые, тире, двоеточия, кавычки, точки в конце предложений;\n"
    "— грамматика: падежи, согласование, число, время глаголов;\n"
    "— заглавные буквы в начале предложений и в именах собственных.\n"
    "СТРОГО ЗАПРЕЩЕНО: переписывать фразы, менять порядок слов, заменять слова синонимами, "
    "сокращать или дополнять текст, менять числа, даты, имена и адреса, склеивать абзацы. "
    "Абзацы и пустые строки сохраняй точно так же, как в исходном тексте. Если сомневаешься — "
    "оставь как есть.\n"
    "Верни ТОЛЬКО исправленный текст целиком, без пояснений, без списков правок и без "
    "markdown-разметки."
)


def checker_cfg() -> dict:
    cfg = dict(DEFAULTS["solver"])
    cfg.update(CFG.get("solver") or {})
    cfg.update(CFG.get("checker") or {})
    return cfg


def _chunks(text: str, limit: int = 1500):
    """Режем большой текст на куски по абзацам: иначе модель не доскажет и текст потеряется."""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 0 else limit
            parts.append(para[:cut].strip())
            para = para[cut:]
        if len(cur) + len(para) + 2 <= limit:
            cur = f"{cur}\n\n{para}" if cur else para
        else:
            if cur.strip():
                parts.append(cur.strip())
            cur = para
    if cur.strip():
        parts.append(cur.strip())
    return parts or [text]


def ollama_check(text: str) -> str:
    """Проверка орфографии и пунктуации: возвращает исправленный текст.

    Большие тексты проверяются по частям — так ничего не обрывается по лимиту токенов.
    """
    chunks = _chunks(text)
    if len(chunks) > 1:
        log(f"проверка текста: {len(text)} симв. разбито на {len(chunks)} частей")

        def safe(part: str) -> str:
            try:
                fixed = _check_once(part)
            except Exception as exc:
                log(f"часть текста не проверена, оставляю как было: {exc}")
                return part       # лучше непроверенный кусок, чем потерянный
            if not fixed.strip():
                log("часть текста вернулась пустой, оставляю как было")
                return part
            ratio = len(fixed) / max(1, len(part))
            if 0.85 <= ratio <= 1.35:
                return fixed
            # модель слишком сильно переписала кусок — не рискуем вашим текстом
            log(f"кусок подозрительно изменился ({len(part)} -> {len(fixed)} симв., "
                f"{ratio * 100:.0f}%), оставляю исходный")
            return part

        return "\n\n".join(safe(c) for c in chunks)
    return _check_once(text)


def _check_once(text: str) -> str:
    cfg = checker_cfg()
    body = {
        "model": cfg["model"],
        "stream": False,
        "think": False,
        "keep_alive": cfg.get("keep_alive", "2m"),
        "messages": [
            {"role": "system", "content": cfg.get("check_prompt") or CHECKER_PROMPT},
            {"role": "user", "content": text},
        ],
        "options": {
            "temperature": 0,
            "num_ctx": int(cfg.get("num_ctx", 6144)),
            "num_predict": int(cfg.get("num_predict", 2048)),
        },
    }
    last = None
    for attempt in range(3):
        try:
            reply = _http_json(cfg["url"].rstrip("/") + "/api/chat", body, timeout=600)
            if reply.get("done_reason") == "length":
                log("проверка текста: ответ упёрся в лимит токенов, часть могла обрезаться")
            return cut_loop((reply.get("message") or {}).get("content", "") or "")
        except Exception as exc:
            last = exc
            log(f"проверка текста, попытка {attempt + 1}: {exc}")
            time.sleep(0.7 * (2 ** attempt))
    raise last if last else RuntimeError("модель не ответила")


def diff_marks(original: str, fixed: str):
    """Где в исправленном тексте слова отличаются от исходных — чтобы подсветить правки."""
    import difflib

    nw = fixed.split()
    offsets, pos = [], 0
    for w in nw:
        i = fixed.find(w, pos)
        if i < 0:
            i = pos
        offsets.append((i, i + len(w)))
        pos = i + len(w)
    marks = []
    sm = difflib.SequenceMatcher(None, original.split(), nw)
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag != "equal" and j1 < j2:
            marks.append((offsets[j1][0], offsets[j2 - 1][1]))
    return marks, len(marks)


# --------------------------------------------------------------------------- #
# LanguageTool — проверка настоящими правилами (нужна Java)
# --------------------------------------------------------------------------- #
_lt_lock = threading.Lock()
_lt_tool = None


def lt_available() -> bool:
    try:
        import language_tool_python  # noqa: F401
        return True
    except Exception:
        return False


def _lt_language(text: str) -> str:
    """LanguageTool требует конкретный язык, поэтому определяем по письменности."""
    cyr = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    return "ru-RU" if cyr >= lat else "en-US"


def _find_java() -> str | None:
    """Ищем Java: приложение не должно зависеть от того, попал ли он в PATH."""
    import glob
    import shutil

    found = shutil.which("java")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 r"C:\Program Files\Java",
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Eclipse Adoptium")):
        for p in glob.glob(os.path.join(base, "**", "bin", "java.exe"), recursive=True):
            return p
    return None


def _lt_start(lang: str = "en-US"):
    """Поднимаем сервер LanguageTool один раз.

    Сервер сам по себе язык не знает — язык передаётся в каждом запросе. Поэтому при
    переходе с русского на английский НЕ надо перезапускать Java: это экономит секунды.
    """
    global _lt_tool
    with _lt_lock:
        if _lt_tool is None:
            java = _find_java()
            if java:
                home = os.path.dirname(os.path.dirname(java))
                os.environ.setdefault("JAVA_HOME", home)
                if os.path.dirname(java) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = os.path.dirname(java) + os.pathsep + os.environ.get("PATH", "")
                log(f"Java: {java}")
            import language_tool_python

            log(f"запускаю LanguageTool ({lang})… первый раз он скачает движок")
            _lt_tool = language_tool_python.LanguageTool(lang)
            log("LanguageTool готов")
        return _lt_tool


def lt_close():
    """Останавливаем Java-сервер при выходе, чтобы не оставался висеть в памяти."""
    global _lt_tool
    with _lt_lock:
        if _lt_tool is not None:
            try:
                _lt_tool.close()
            except Exception:
                pass
            _lt_tool = None


def lt_warmup():
    """Прогрев в фоне: первый запрос на каждом языке грузит словари (~10 с).

    Без прогрева первый «Проверить» после запуска висит эти секунды; с ним — мгновенный.
    """
    def work():
        try:
            samples = {"ru-RU": "проверка текста", "en-US": "checking the text"}
            for lang, sample in samples.items():
                _lt_raw(_lt_start(lang), sample, lang)
            log("LanguageTool прогрет: ru-RU и en-US")
        except Exception as exc:
            log(f"прогрев LanguageTool не удался: {exc}")
    threading.Thread(target=work, daemon=True).start()


def _lt_apply(chunk: str, notes):
    """Применяем правки с конца и НЕ трогаем перекрывающиеся — иначе текст портится.

    LanguageTool иногда выдаёт два замечания на один и тот же кусок (например «teh best»),
    и наивное применение обоих даёт «teh the best».
    """
    accepted, last_end = [], -1
    for n in sorted(notes, key=lambda x: x["start"]):
        if not n["reps"] or n["end"] <= n["start"]:
            continue
        if n["start"] < last_end:
            continue
        accepted.append((n["start"], n["end"], n["reps"][0]))
        last_end = n["end"]
    fixed = chunk
    for start, end, rep in reversed(accepted):
        fixed = fixed[:start] + rep + fixed[end:]
    return fixed


def _lt_server_url(tool) -> str:
    """Базовый адрес движка. У прослойки это уже .../v2/, поэтому /check цепляем к нему."""
    for attr in ("_url", "url", "_server_url", "server_url"):
        value = getattr(tool, attr, None)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return "http://localhost:8081/v2/"


def _lt_raw(tool, text: str, lang: str, disabled=None):
    """Свой запрос к LanguageTool: берём абсолютные смещения.

    Прослойка language_tool_python отдаёт смещения относительно окна контекста, и применять
    их как абсолютные нельзя — текст превращается в кашу. Поэтому спрашиваем движок сами.
    """
    import urllib.parse
    import urllib.request

    base = _lt_server_url(tool)
    if not base.endswith("/"):
        base += "/"
    url = urllib.parse.urljoin(base, "check")
    params = {"text": text, "language": lang}
    if disabled:
        params["disabledRules"] = ",".join(sorted(disabled))
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=180) as r:
        payload = json.loads(r.read().decode("utf-8"))
    out = []
    for m in payload.get("matches", []):
        off = int(m.get("offset", 0) or 0)
        ln = int(m.get("length", 0) or 0)
        out.append({
            "start": off,
            "end": off + ln,
            "reps": [x.get("value", "") for x in (m.get("replacements") or [])],
            "rule": (m.get("rule") or {}).get("id", "") or "",
            "message": m.get("message", "") or "",
            "before": text[off:off + ln],
        })
    return out


def _lt_words(text: str):
    """Только слова, без знаков препинания — иначе запятая считается «вставкой»."""
    return re.findall(r"[^\W_]+", text, re.UNICODE)


def _lt_insertions(parts):
    """Правки, которые лишь ВСТАВЛЯЮТ слово, оставляя ошибочное на месте.

    Пример: «teh best» -> «teh the best». Такая правка маскирует настоящую ошибку —
    спелчекер уже не срабатывает. Такие правила надо отключить и спросить заново.

    Сравниваем по словам: исходные слова должны идти подряд внутри новой фразы, а слов
    должно стать больше. Иначе « but» -> «, but» (обычная замена) считалось бы вставкой.
    """
    out = []
    for n in parts:
        before = n["before"].strip()
        after = (n["reps"][0] if n["reps"] else "").strip()
        if not before or not after:
            continue
        bw = _lt_words(before)
        aw = _lt_words(after)
        if len(aw) <= len(bw):
            continue
        i = 0
        for w in aw:
            if i < len(bw) and w == bw[i]:
                i += 1
        if i == len(bw):
            out.append(n)
    return out


def lt_check(text: str):
    """Проверка правилами: -> (исправленный текст, список замечаний с названием правила)."""
    fixed_parts, notes = [], []
    for chunk in _chunks(text, limit=3000):
        lang = _lt_language(chunk)
        disabled = set()
        last, parts = None, None
        for _ in range(3):
            for attempt in range(3):
                try:
                    tool = _lt_start(lang)
                    parts = _lt_raw(tool, chunk, lang, disabled)
                    break
                except Exception as exc:
                    # первый запуск распаковывает движок: файл может быть ещё занят
                    last = exc
                    log(f"LanguageTool, попытка {attempt + 1}: {exc}")
                    time.sleep(2 + attempt * 2)
            if parts is None:
                raise last if last else RuntimeError("LanguageTool не ответил")
            bad = _lt_insertions(parts)
            new_rules = {n["rule"] for n in bad if n["rule"]} - disabled
            if not new_rules:
                break
            disabled |= new_rules
            log(f"LanguageTool: отключаю правила-вставки {sorted(new_rules)} и спрашиваю снова")
            parts = None
        notes.extend({"rule": n["rule"], "message": n["message"],
                      "before": n["before"], "after": n["reps"][0] if n["reps"] else ""}
                     for n in parts)
        fixed_parts.append(_lt_apply(chunk, parts))
    return "\n\n".join(fixed_parts), notes


def run_check(text: str):
    """Единая точка входа: проверяем либо правилами, либо моделью.

    -> (исправленный текст, подсветка правок, замечания, название движка)
    """
    engine = str((CFG.get("checker") or {}).get("engine", "rules")).lower()
    if engine == "rules" and lt_available():
        try:
            fixed, notes = lt_check(text)
            marks, _ = diff_marks(text, fixed)
            return fixed, marks, notes, "LanguageTool (правила)"
        except Exception as exc:
            # Java нет, движок не поднялся — не оставляем пользователя без проверки
            log(f"LanguageTool не сработал ({exc}) — проверяю моделью")
    if engine == "rules":
        log("LanguageTool запрошен, но недоступен — проверяю моделью")
    fixed = ollama_check(text)
    marks, _ = diff_marks(text, fixed)
    return fixed, marks, [], f"модель {checker_cfg()['model']}"


def count_words(text: str):
    """-> (слов, символов, символов без пробелов, строк)."""
    words = len(re.findall(r"[\w\u0400-\u04ff\u0370-\u03ff]+", text or "", re.UNICODE))
    if not isinstance(words, int):        # на всякий случай
        words = 0
    chars = len(text or "")
    nospace = len(re.sub(r"\s", "", text or ""))
    lines = len([ln for ln in (text or "").split("\n") if ln.strip()])
    return words, chars, nospace, lines


def clip_image(images_only: bool = False):
    from PIL import ImageGrab

    img = ImageGrab.grabclipboard()
    if img is None:
        return None
    if isinstance(img, list):
        if images_only or not img:
            return None
        from PIL import Image

        img = Image.open(img[0])
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


# --------------------------------------------------------------------------- #
# Экран выделения (Lightshot-стиль): замороженный снимок + текстовый слой
# --------------------------------------------------------------------------- #
class Snip:
    def __init__(self, app, mode: str = "text", image=None):
        self.app = app
        self.tk = app.tk
        self.root = app.root
        self.mode = mode          # "text" | "all" | "image" | "ai"
        self.state = "select"     # select -> ocr -> text
        self.words = []
        self.rects = []
        self.sel = []
        self.anchor = None
        self.band = None
        self.marks = []
        self.tool_item = None
        self.region = None        # (x, y, w, h) в логических координатах
        self.sx = self.sy = 1.0
        self._photo = None
        self.win = None
        self.canvas = None

    # ---------------- открытие ---------------- #
    def open(self):
        from PIL import ImageGrab, ImageTk

        tk = self.tk
        self.root.update_idletasks()
        vx = self.root.winfo_vrootx()
        vy = self.root.winfo_vrooty()
        vw = self.root.winfo_vrootwidth()
        vh = self.root.winfo_vrootheight()
        self.vx, self.vy, self.vw, self.vh = vx, vy, vw, vh

        if self.app.pending_image is not None:
            self.base = self.app.pending_image
            self.app.pending_image = None
            self.freeze = self.base
            self.sx = self.sy = 1.0
            self.vx = self.vy = 0
            self.vw, self.vh = self.base.size
            self._canvas_image = self.base
        else:
            self.base = ImageGrab.grab(bbox=(vx, vy, vx + vw, vy + vh), all_screens=True)
            self.sx = self.base.width / vw
            self.sy = self.base.height / vh
            self._canvas_image = (self.base if self.base.size == (vw, vh)
                                  else self.base.resize((vw, vh)))

        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.geometry(f"{vw}x{vh}+{vx}+{vy}")
        win.attributes("-topmost", True)
        self.app.busy = True

        canvas = tk.Canvas(win, width=vw, height=vh, highlightthickness=0, bd=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        self.canvas = canvas
        self._photo = ImageTk.PhotoImage(self._canvas_image)
        canvas.create_image(0, 0, anchor="nw", image=self._photo)

        self.shades = [
            canvas.create_rectangle(0, 0, vw, vh, fill="#000000", stipple="gray50", outline=""),
            canvas.create_rectangle(0, 0, vw, 0, fill="#000000", stipple="gray50", outline=""),
            canvas.create_rectangle(0, 0, 0, vh, fill="#000000", stipple="gray50", outline=""),
            canvas.create_rectangle(0, 0, 0, 0, fill="#000000", stipple="gray50", outline=""),
        ]
        self.sel_rect = canvas.create_rectangle(0, 0, 0, 0, outline="#22d3ee", width=2)
        if self.mode == "image":
            self.hint_text = "Тяните рамку — снимок сразу уйдёт в буфер  ·  Esc — отмена"
        elif self.mode == "all":
            self.hint_text = "Тяните рамку — весь текст сразу уйдёт в буфер  ·  Esc — отмена"
        elif self.mode == "ai":
            self.hint_text = "Тяните рамку — распознаю через OCRia-1 (4B)  ·  Esc — отмена"
        else:
            self.hint_text = "Тяните рамку  ·  Esc — отмена"
        self.hint = canvas.create_text(
            vw // 2, 44, text=self.hint_text,
            fill="#ffffff", font=("Segoe UI", 15, "bold"))
        self.size_label = canvas.create_text(0, 0, text="", fill="#22d3ee",
                                             font=("Segoe UI", 11, "bold"), anchor="nw")
        self.status = canvas.create_text(0, 0, text="", fill="#fbbf24",
                                         font=("Segoe UI", 11, "bold"), anchor="nw")

        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._motion)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<ButtonPress-3>", lambda e: self.close())
        canvas.bind("<Motion>", self._hover)
        win.bind("<Escape>", lambda e: self.close())
        for w in (win, canvas):
            w.bind("<Control-a>", self._select_all)
            w.bind("<Control-A>", self._select_all)
            w.bind("<Control-c>", lambda e: self._on_ctrl_c())
            w.bind("<Control-C>", lambda e: self._on_ctrl_c())
            w.bind("<Control-Insert>", lambda e: self._on_ctrl_c())
        self._keys = {vk: bool(_u32.GetAsyncKeyState(vk) & 0x8000) for vk in (0x43, 0x41)}
        self._poll_keys()
        win.focus_force()
        canvas.focus_set()

    def close(self):
        self.app.busy = False
        self.app.snip = None
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    # ---------------- выбор области ---------------- #
    def _shade(self, x1, y1, x2, y2):
        vw, vh = self.vw, self.vh
        x1, y1 = max(0, min(x1, vw)), max(0, min(y1, vh))
        x2, y2 = max(0, min(x2, vw)), max(0, min(y2, vh))
        c = self.canvas
        c.coords(self.shades[0], 0, 0, vw, y1)
        c.coords(self.shades[1], 0, y2, vw, vh)
        c.coords(self.shades[2], 0, y1, x1, y2)
        c.coords(self.shades[3], x2, y1, vw, y2)
        c.coords(self.sel_rect, x1, y1, x2, y2)
        if x2 - x1 > 1 and y2 - y1 > 1:
            c.coords(self.size_label, x1 + 4, max(0, y1 - 20))
            c.itemconfig(self.size_label, text=f"{int(x2 - x1)} × {int(y2 - y1)}")
        else:
            c.itemconfig(self.size_label, text="")

    def _press(self, e):
        if self.state == "select":
            self.anchor = (e.x, e.y)
            self._shade(e.x, e.y, e.x, e.y)
        elif self.state == "text":
            self.anchor = (e.x, e.y)
            self.moved = False
            self._drop_band()

    def _motion(self, e):
        if self.anchor is None:
            return
        x0, y0 = self.anchor
        if abs(e.x - x0) > 3 or abs(e.y - y0) > 3:
            self.moved = True
        if self.state == "select":
            x1, x2 = sorted((x0, e.x))
            y1, y2 = sorted((y0, e.y))
            self._shade(x1, y1, x2, y2)
        elif self.state == "text":
            x1, x2 = sorted((x0, e.x))
            y1, y2 = sorted((y0, e.y))
            if self.band is None:
                self.band = self.canvas.create_rectangle(x1, y1, x2, y2, outline="#22d3ee",
                                                         width=1, dash=(3, 2))
            else:
                self.canvas.coords(self.band, x1, y1, x2, y2)
            sel = [i for i, (a, b, c, d) in enumerate(self.rects)
                   if c >= x1 and a <= x2 and d >= y1 and b <= y2]
            if sel != self.sel:
                self.sel = sel
                self._paint()

    def _release(self, e):
        if self.anchor is None:
            return
        x0, y0 = self.anchor
        self.anchor = None
        if self.state == "select":
            x1, x2 = sorted((x0, e.x))
            y1, y2 = sorted((y0, e.y))
            if x2 - x1 < 5 or y2 - y1 < 5:
                self.close()
                return
            self.commit_region((x1, y1, x2 - x1, y2 - y1))
        elif self.state == "text":
            if not self.moved:
                i = self._hit(e.x, e.y)
                self.sel = [] if i is None else [i]
                self._paint()
            self._drop_band()

    def _drop_band(self):
        if self.band is not None:
            self.canvas.delete(self.band)
            self.band = None

    def _hover(self, e):
        if self.state != "text":
            return
        i = self._hit(e.x, e.y)
        self.canvas.itemconfig(self.sel_rect, outline="#22d3ee" if i is None else "#a3e635")

    def _hit(self, mx, my):
        for i, (x1, y1, x2, y2) in enumerate(self.rects):
            if x1 <= mx <= x2 and y1 <= my <= y2:
                return i
        return None

    def commit_region(self, rect):
        self.region = rect
        rx, ry, rw, rh = rect
        self._shade(rx, ry, rx + rw, ry + rh)
        self.canvas.itemconfig(self.hint, text="")
        self.canvas.coords(self.status, rx + 6, ry + rh + 8)
        self.canvas.itemconfig(self.status, text="распознаю…")
        self.state = "ocr"

        box = (int(rx * self.sx), int(ry * self.sy),
               int((rx + rw) * self.sx), int((ry + rh) * self.sy))
        crop = self.base.crop(box)
        self.crop = crop

        if self.mode == "image":
            # картинка зоны сразу в буфер: OCR не нужен вообще
            save_capture(crop)
            try:
                set_clipboard("", crop)
                self.app.mark_clipboard_written()
                self.app.show_toast("✔ Скриншот выделенной зоны скопирован", 1800)
            except Exception:
                self.app.show_toast("буфер обмена занят, попробуйте ещё раз", 2000)
            self.close()
            return

        if self.mode == "ai":
            self.app.busy = True
            threading.Thread(target=self._ai_thread, args=(crop,), daemon=True).start()
            return

        self.app.busy = True
        threading.Thread(target=self._ocr_thread, args=(crop,), daemon=True).start()

    # ---------------- OCRia-1 (4B) ---------------- #
    def _ai_thread(self, crop):
        t0 = time.time()
        try:
            ok, info = ai_status()
            if not ok:
                raise RuntimeError(info)
            text = ollama_ocr(crop)
        except Exception as exc:
            msg = str(exc)
            self.root.after(0, lambda: self._ai_fallback(msg, crop))
            return
        dt = time.time() - t0
        save_capture(crop)
        self.root.after(0, lambda: self._ai_done(text, crop, dt))

    def _ai_fallback(self, message: str, crop):
        """OCRia-1 не ответила — не оставляем без текста, распознаём быстрой моделью."""
        log(f"OCRia-1 не ответила ({message}); откат на nano")
        if self.mode != "ai" or self.win is None:
            self._ai_failed(message)
            return
        self.app.show_toast("OCRia-1 не ответила — распознаю быстрой моделью", 3000)
        try:
            words = ocr_image_words(crop)
        except Exception:
            log_exc("откат на nano")
            self._ai_failed(message)
            return
        dt = 0.0
        self.root.after(0, lambda: self._ocr_done(words, dt))

    def _ai_failed(self, message: str):
        self.app.busy = False
        log(f"OCRia-1 ошибка: {message}")
        self.app.show_toast(message, 6000)
        if self.state == "ocr":
            self.canvas.itemconfig(self.status, text="OCRia-1 не ответил")
            self.state = "text"

    def _ai_done(self, text: str, crop, dt: float):
        self.app.busy = False
        log(f"OCRia-1: {len(text)} симв. за {dt:.1f} c")
        try:
            if self.win is not None:
                self.close()
            if not text.strip():
                self.app.show_toast("OCRia-1 вернул пустой ответ", 3000)
                return
            log("открываю окно результата")
            self.app.show_text_result(text, crop, dt)
            log("окно результата открыто")
        except Exception:
            log_exc("_ai_done (окно результата)")

    def _ask_ai(self, _e=None):
        if self.crop is None:
            return
        self.canvas.itemconfig(self.status, text="OCRia-1 (4B) думает…")
        self.app.busy = True
        threading.Thread(target=self._ai_thread, args=(self.crop,), daemon=True).start()

    def reselect(self):
        self.state = "select"
        self.words, self.rects, self.sel = [], [], []
        self.canvas.delete("textmark")
        self.canvas.delete("selmark")
        self._remove_toolbar()
        self.canvas.itemconfig(self.status, text="")
        self.canvas.itemconfig(self.hint, text=self.hint_text)
        self.canvas.itemconfig(self.sel_rect, outline="#22d3ee")
        self._shade(0, 0, 0, 0)

    # ---------------- OCR ---------------- #
    def _ocr_thread(self, crop):
        t0 = time.time()
        try:
            words = ocr_image_words(crop)
        except Exception:
            err = traceback.format_exc(limit=3)
            self.root.after(0, lambda: self._ocr_failed(err))
            return
        dt = time.time() - t0
        save_capture(crop)
        self.root.after(0, lambda: self._ocr_done(words, dt))

    def _ocr_failed(self, err):
        if self.win is None:
            return
        self.app.busy = False
        self.canvas.itemconfig(self.status, text="ошибка распознавания")
        print(err, file=sys.stderr)

    def _ocr_done(self, words, dt):
        if self.win is None:
            return
        self.words = words
        rx, ry = self.region[0], self.region[1]
        self.rects = [((w["x"] / self.sx) + rx, (w["y"] / self.sy) + ry,
                       ((w["x"] + w["w"]) / self.sx) + rx,
                       ((w["y"] + w["h"]) / self.sy) + ry) for w in words]

        if self.mode == "all":
            self.app.busy = False
            if not words:
                self.app.show_toast("Текст не распознан", 2500)
                self.close()
                return
            text = self._text(list(range(len(words))))
            self._copy(text)
            self.app.show_toast(f"✔ Скопировано {len(text)} симв.", 2000)
            self.close()
            return

        c = self.canvas
        for (x1, y1, x2, y2) in self.rects:
            self.marks.append(c.create_line(x1, y2, x2, y2, fill="#4ade80", dash=(2, 2),
                                            tags="textmark"))
        if not CFG.get("markup_visible", True):
            c.itemconfigure("textmark", state="hidden")
        self.state = "text"
        self.canvas.itemconfig(self.status,
                               text=f"{len(words)} слов · {dt:.1f} с  ·  тяните мышью по тексту, "
                                    f"Ctrl+C — копировать (без выделения — скриншот)")
        self.canvas.itemconfig(self.sel_rect, outline="#22d3ee")
        self._show_toolbar()
        self.app.busy = False

    # ---------------- выделение и копирование ---------------- #
    def _paint(self):
        c = self.canvas
        c.delete("selmark")
        for i in self.sel:
            x1, y1, x2, y2 = self.rects[i]
            c.create_rectangle(x1, y1, x2, y2, fill="#22d3ee", stipple="gray25",
                               outline="", tags="selmark")
        if self.sel:
            if CFG.get("copy_on_select", False):
                self._copy_selected()
            else:
                n = len(self._text(self.sel))
                c.itemconfig(self.status,
                             text=f"выделено {len(self.sel)} слов / {n} симв.  ·  Ctrl+C — скопировать")
        elif self.state == "text":
            c.itemconfig(self.status, text=f"{len(self.words)} слов · тяните мышью по тексту")

    def _copy(self, text: str) -> bool:
        """Копируем ТОЛЬКО текст. Картинка — отдельной кнопкой."""
        for _ in range(3):
            try:
                set_clipboard(text)
                self.app.mark_clipboard_written()
                return True
            except Exception:
                time.sleep(0.05)
        self.app.show_toast("буфер обмена занят, попробуйте ещё раз", 2000)
        return False

    def _copy_selected(self, _e=None):
        """Ctrl+C: копируем ТОЛЬКО выделенное. Ничего не выделено — ничего не копируем."""
        if self.state != "text" or not self.words:
            return "break"
        if not self.sel:
            self.canvas.itemconfig(self.status, text="ничего не выделено  ·  Ctrl+A — всё, "
                                                     "Ctrl+C — скопировать скриншот")
            return "break"
        text = self._text(self.sel)
        if text and self._copy(text):
            self.canvas.itemconfig(self.status,
                                   text=f"✔ скопирован выделенный текст: {len(text)} симв.")
        return "break"

    def _copy_text_all(self, _e=None):
        """Кнопка «Копировать весь текст»."""
        if self.state != "text" or not self.words:
            return "break"
        text = self._text(list(range(len(self.words))))
        if text and self._copy(text):
            self.canvas.itemconfig(self.status,
                                   text=f"✔ скопирован весь текст: {len(text)} симв.")
        return "break"

    def _copy_image(self, _e=None):
        """Кнопка «Копировать скриншот»: только картинка зоны, без текста."""
        try:
            set_clipboard("", self.crop)
            self.app.mark_clipboard_written()
            self.canvas.itemconfig(self.status, text="✔ скриншот зоны в буфере обмена")
        except Exception:
            self.app.show_toast("буфер обмена занят, попробуйте ещё раз", 2000)
        return "break"

    def _paint_marks_only(self):
        c = self.canvas
        c.delete("selmark")
        for i in self.sel:
            x1, y1, x2, y2 = self.rects[i]
            c.create_rectangle(x1, y1, x2, y2, fill="#22d3ee", stipple="gray25",
                               outline="", tags="selmark")

    def _select_all(self, _e=None):
        if self.state != "text" or not self.words:
            return "break"
        self.sel = list(range(len(self.words)))
        self._paint_marks_only()
        n = len(self._text(self.sel))
        self.canvas.itemconfig(self.status,
                               text=f"выделено {len(self.sel)} слов / {n} симв.  ·  Ctrl+C — скопировать")
        return "break"

    # ---------------- Ctrl+C через физическое состояние клавиш ---------------- #
    # Tk-биндинги на клавиши бывают капризны (особенно когда окно перехватывает фокус),
    # а GetAsyncKeyState видит нажатие всегда — тем же способом ловятся и хоткеи.
    def _poll_keys(self):
        if self.win is None:
            return
        try:
            ctrl = bool(_u32.GetAsyncKeyState(0x11) & 0x8000)
            for vk, action in ((0x43, self._on_ctrl_c), (0x41, self._on_ctrl_a)):
                now = bool(_u32.GetAsyncKeyState(vk) & 0x8000)
                was = self._keys.get(vk, False)
                self._keys[vk] = now
                if now and not was and ctrl:
                    action()
        except Exception:
            log_exc("Ctrl/Ctrl+A в окне выделения")
        if self.win is not None:
            try:
                self.win.after(40, self._poll_keys)
            except Exception:
                pass

    def _on_ctrl_c(self):
        """Ctrl+C: есть выделение — копируем текст, нет выделения — копируем скриншот."""
        if self.state != "text":
            return
        if self.sel:
            self._copy_selected()
        else:
            self._copy_image()

    def _on_ctrl_a(self):
        if self.state == "text":
            self._select_all()

    def _text(self, sel) -> str:
        if not sel:
            return ""
        lines, cur, cur_line = [], [], None
        for i in sorted(sel):
            w = self.words[i]
            if cur_line is None or w["line"] == cur_line:
                cur.append(w["text"])
            else:
                lines.append(" ".join(cur))
                cur = [w["text"]]
            cur_line = w["line"]
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)

    # ---------------- панель кнопок ---------------- #
    def _remove_toolbar(self):
        if self.tool_item is not None:
            self.canvas.delete(self.tool_item)
            self.tool_item = None

    def _show_toolbar(self):
        tk = self.tk
        self._remove_toolbar()
        bar = tk.Frame(self.canvas, bg="#111827", bd=0)

        def mk(text_, cmd, primary=False):
            bg = "#22d3ee" if primary else "#1f2937"
            fg = "#0b1220" if primary else "#e5e7eb"
            tk.Button(bar, text=text_, command=cmd, bg=bg, fg=fg, relief="flat",
                      activebackground="#38bdf8" if primary else "#374151",
                      activeforeground=fg, font=("Segoe UI", 9, "bold"),
                      padx=10, pady=5, cursor="hand2").pack(side="left", padx=3, pady=3)

        mk("Копировать выделенное", self._copy_selected, primary=True)
        mk("Копировать весь текст", self._copy_text_all)
        mk("Копировать скриншот", self._copy_image)
        mk("Точнее: OCRia-1 (4B)", self._ask_ai)
        mk("Выделить всё", self._select_all)
        mk("Сохранить .txt", self._save_text)
        mk("Сохранить картинку", self._save_image)
        mk("Другая область", self.reselect)
        mk("Разметка", self._toggle_markup)
        mk("Закрыть", self.close)

        bar.update_idletasks()
        bw = bar.winfo_reqwidth()
        bh = bar.winfo_reqheight()
        rx, ry, rw, rh = self.region
        x = min(max(4, rx), self.vw - bw - 4)
        y = ry + rh + 26
        if y + bh > self.vh - 4:
            y = ry - bh - 6
        if y < 4:
            y = 4
        self.tool_item = self.canvas.create_window(x, y, anchor="nw", window=bar)
        self.toolbar = bar

    def _toggle_markup(self):
        hidden = self.canvas.itemcget("textmark", "state") == "hidden"
        self.canvas.itemconfigure("textmark", state="normal" if hidden else "hidden")

    def _save_text(self):
        from tkinter import filedialog

        sel = self.sel or list(range(len(self.words)))
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="ocr.txt",
                                            filetypes=[("Текст", "*.txt"), ("Все файлы", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._text(sel))

    def _save_image(self):
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(defaultextension=".png", initialfile="snip.png",
                                            filetypes=[("Картинка", "*.png"), ("Все файлы", "*.*")])
        if path:
            self.crop.save(path)


# --------------------------------------------------------------------------- #
# Значок в системном трее с выбором модели
# --------------------------------------------------------------------------- #
def tray_image():
    from PIL import Image, ImageDraw

    for p in (ICON_PNG, ICON_ICO):
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except Exception:
                pass
    img = Image.new("RGBA", (64, 64), (17, 24, 39, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([5, 5, 59, 59], radius=11, outline=(34, 211, 238, 255), width=4)
    d.rectangle([15, 21, 49, 27], fill=(255, 255, 255, 255))
    d.rectangle([15, 29, 49, 35], fill=(34, 211, 238, 255))
    d.rectangle([15, 37, 49, 43], fill=(255, 255, 255, 255))
    return img


def start_tray(app):
    """Значок в трее; из его меню переключается модель распознавания."""
    try:
        import pystray
    except Exception as exc:
        print(f"[ocr snap] трей недоступен ({exc}); нужен pystray", file=sys.stderr)
        return None

    def later(fn, *args):
        """Tk трогаем только из главного потока."""
        return lambda icon=None, item=None: app.root.after(0, lambda: fn(*args))

    def engine_item(kind, label):
        return pystray.MenuItem(
            label, later(app.set_engine, kind),
            checked=lambda item, k=kind: CFG.get("engine", "nano") == k, radio=True)

    def keep_item(spec, label):
        return pystray.MenuItem(
            label, later(app.set_keep_alive, spec),
            checked=lambda item, s=spec: str(ai_cfg().get("keep_alive")) == s, radio=True)

    def check_engine_item(kind, label):
        return pystray.MenuItem(
            label, later(app.set_check_engine, kind),
            checked=lambda item, k=kind: str((CFG.get("checker") or {}).get("engine",
                                                                           "rules")) == k,
            radio=True)

    solver_state = {"models": []}

    def solver_item(name):
        return pystray.MenuItem(
            name, later(app.set_solver_model, name),
            checked=lambda item, n=name: solver_cfg().get("model") == n, radio=True)

    def solver_menu(item=None):
        names = solver_state["models"] or [solver_cfg().get("model")]
        return pystray.Menu(*[solver_item(n) for n in names])

    def fetch_solver_models():
        """Список установленных моделей для меню — по возрастанию размера."""
        try:
            tags = _http_json(ai_cfg()["url"].rstrip("/") + "/api/tags", timeout=6)
            ms = sorted(tags.get("models", []), key=lambda m: m.get("size", 0))
            solver_state["models"] = [m.get("name", "") for m in ms if m.get("name")]
        except Exception:
            pass

    threading.Thread(target=fetch_solver_models, daemon=True).start()

    def open_captures(icon=None, item=None):
        try:
            os.makedirs(CAPTURES_DIR, exist_ok=True)
            os.startfile(CAPTURES_DIR)
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"OCRia Snap · {app.engine_name()}",
                         later(app.show_status), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Модель распознавания", pystray.Menu(
            engine_item("nano", "nano — быстрая (8.1M, CPU)"),
            engine_item("ai", "OCRia-1 — точная (4B, GPU)"),
        )),
        pystray.MenuItem("Держать модель в памяти", pystray.Menu(
            keep_item("2m", "2 минуты после снимка"),
            keep_item("30m", "30 минут"),
            keep_item("-1", "до выхода из программы"),
        )),
        pystray.MenuItem("Прогреть модель сейчас", later(app.warm_model)),
        pystray.MenuItem("Выгрузить, освободить VRAM", later(app.unload_model)),
        pystray.MenuItem("Решать моделью", solver_menu),
        pystray.MenuItem("Проверять текст", pystray.Menu(
            check_engine_item("model", "моделью (гибко)"),
            check_engine_item("rules", "правилами (LanguageTool)"),
        )),
        pystray.MenuItem("Подсчёт слов", later(app.count_last),
                         enabled=lambda item: bool(app.last_text)),
        pystray.MenuItem("Проверить последний текст", later(app.check_last),
                         enabled=lambda item: bool(app.last_text)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Снять область\tWin+Shift+S", later(app.start_snip)),
        pystray.MenuItem("Скопировать снимок\tWin+Ctrl+Shift+S",
                         later(app.start_snip, "image")),
        pystray.MenuItem("Точная модель (4B)\tCtrl+Alt+A", later(app.start_snip, "ai")),
        pystray.MenuItem("Папка со снимками", open_captures),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", lambda icon, item: (lt_close(), icon.stop(),
                                                      app.root.after(0, app.root.quit))),
    )
    icon = pystray.Icon("ocria-snap", tray_image(), app.engine_name(), menu)
    app.tray = icon
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


# --------------------------------------------------------------------------- #
# Приложение
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root):
        import tkinter as tk

        self.tk = tk
        self.root = root
        root.withdraw()
        self.busy = False
        self.snip = None
        self.win = None          # окно с текстом от OCRia-1 (4B)
        self.toast = None
        self._photo_refs = []    # держим ссылки на картинки, иначе Tk их съест
        self.pending_image = None
        self.tray = None
        self.last_text = ""      # последний распознанный текст — его проверяет трей
        self._prev = {}
        self._last_seq = _u32.GetClipboardSequenceNumber()
        self._own_hashes = set()
        self.active = self._resolve_hotkeys()

    def _resolve_hotkeys(self) -> dict:
        """Оставляем только те комбинации, которые не заняты другими программами."""
        active = {}
        for name in ACTIONS:
            combos = []
            for spec, combo in zip(self._combos_spec(name), self._combos(name)):
                if combo is not None and self._combo_available(combo):
                    combos.append(combo)
            active[name] = combos
        return active

    def _combos_spec(self, name):
        spec = (CFG.get("hotkeys") or {}).get(name)
        if isinstance(spec, str):
            spec = [spec]
        return list(spec or [])

    @staticmethod
    def _combo_available(combo) -> bool:
        """RegisterHotKey проходит -> комбинацию никто не держит, значит слушать её безопасно."""
        mods, vk = combo
        mask = 0
        for m in mods:
            mask |= MOD_MASK[m]
        ok = _u32.RegisterHotKey(None, 0x9C41, mask | 0x4000, vk)
        if ok:
            _u32.UnregisterHotKey(None, 0x9C41)
        return bool(ok)

    def snip_keys_text(self) -> str:
        out = []
        for spec, combo in zip(self._combos_spec("snip"), self._combos("snip")):
            if combo is not None and combo in self.active.get("snip", []):
                out.append(str(spec))
        return ", ".join(out)

    def engine_name(self) -> str:
        return "OCRia-1 (4B, точная)" if CFG.get("engine", "nano") == "ai" \
            else "OCRia-nano (быстрая)"

    def show_status(self):
        self.show_toast(f"Модель: {self.engine_name()}\n"
                        f"Win+Shift+S — рамка · Ctrl+Alt+A — точная модель", 3000)

    def set_engine(self, kind: str):
        """Переключение модели из меню трея; выбор сохраняется в config.json."""
        CFG["engine"] = "ai" if kind == "ai" else "nano"
        save_config()
        log(f"движок переключён: {CFG['engine']}")
        self.show_toast(f"Модель распознавания: {self.engine_name()}", 2500)
        if self.tray is not None:
            try:
                self.tray.title = self.engine_name()
            except Exception:
                pass
        if CFG["engine"] == "ai" and not ollama_loaded():
            # прогреваем заранее, чтобы первый снимок не ждал загрузки
            threading.Thread(target=ollama_load_model, daemon=True).start()

    KEEP_NAMES = {"2m": "2 минуты после снимка", "30m": "30 минут", "-1": "до выхода"}

    def set_keep_alive(self, spec: str):
        CFG.setdefault("ai_engine", {})["keep_alive"] = spec
        save_config()
        self.show_toast(f"Модель в памяти: {self.KEEP_NAMES.get(spec, spec)}", 2500)

    def warm_model(self):
        self.show_toast("Загружаю OCRia-1 в видеопамять…\nРазовая пауза, примерно 2 минуты", 4000)

        def work():
            ok = ollama_load_model()
            self.root.after(0, lambda: self.show_toast(
                "✔ OCRia-1 готова — дальше снимки будут за секунду" if ok
                else "Не удалось загрузить модель (Ollama запущена?)", 3500))

        threading.Thread(target=work, daemon=True).start()

    def set_solver_model(self, name: str):
        CFG.setdefault("solver", {})["model"] = name
        save_config()
        log(f"модель для решения: {name}")
        self.show_toast(f"Решать будет: {name}", 2500)

    def unload_model(self):
        def work():
            ok = ollama_unload_model()
            self.root.after(0, lambda: self.show_toast(
                "✔ Модель выгружена, видеопамять свободна" if ok
                else "Не удалось выгрузить модель", 2800))

        threading.Thread(target=work, daemon=True).start()

    def mark_clipboard_written(self):
        """Наш собственный write в буфер не должен триггерить слежение."""
        seq = _u32.GetClipboardSequenceNumber()
        self._last_seq = seq
        self._last_own_seq = seq

    # ---------------- горячие клавиши ---------------- #
    def _down(self, vk: int) -> bool:
        return bool(_u32.GetAsyncKeyState(vk) & 0x8000)

    def _combos(self, name: str):
        spec = (CFG.get("hotkeys") or {}).get(name)
        if isinstance(spec, str):
            spec = [spec]
        return [self._parse(c) for c in (spec or [])]

    @staticmethod
    def _parse(spec: str):
        parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
        mods = {_CANON_MOD.get(p, p) for p in parts if p in MOD_KEYS}
        rest = [p for p in parts if p not in MOD_KEYS]
        if len(rest) != 1:
            return None
        key = rest[0]
        if key in SPECIAL_KEYS:
            vk = SPECIAL_KEYS[key]
        elif len(key) == 1 and key.isalnum():
            vk = ord(key.upper())
        else:
            return None
        return frozenset(mods), vk

    def _combo_fired(self, combo) -> bool:
        if combo is None:
            return False
        mods, vk = combo
        for m in ALL_MODS:
            if self._down(MOD_KEYS[m]) != (m in mods):
                return False
        key = (combo, vk)
        now = self._down(vk)
        was = self._prev.get(key, False)
        self._prev[key] = now
        return now and not was

    def poll_hotkeys(self):
        try:
            if not self.busy:
                fired = None
                for name in ACTIONS:
                    for combo in self.active.get(name, []):
                        if self._combo_fired(combo):
                            fired = name
                            break
                    if fired:
                        break
                if fired == "snip":
                    self.start_snip()
                elif fired == "snip_image":
                    self.start_snip(mode="image")
                elif fired == "snip_ai":
                    self.start_snip(mode="ai")
                elif fired == "clipboard":
                    self.start_clipboard()
                elif fired == "silent_copy":
                    self.start_snip(mode="all")
            else:
                for name in ACTIONS:
                    for combo in self.active.get(name, []):
                        self._combo_fired(combo)  # сбрасываем состояние клавиш
        except Exception:
            log_exc("poll_hotkeys")
        finally:
            self.root.after(45, self.poll_hotkeys)

    def poll_clipboard(self):
        """Опциональное слежение за буфером (если clipboard_action != off)."""
        try:
            seq = _u32.GetClipboardSequenceNumber()
            if seq != self._last_seq:
                self._last_seq = seq
                action = CFG.get("clipboard_action", "off")
                if action != "off" and not self.busy:
                    img = clip_image(images_only=True)
                    if img is not None:
                        h = hashlib.sha1(img.tobytes()).hexdigest()
                        if h in self._own_hashes:
                            return
                        self._own_hashes.add(h)
                        if action == "overlay":
                            self.pending_image = img
                            self.start_snip(image=img)
                        else:
                            threading.Thread(target=self._copy_clipboard_image,
                                             args=(img,), daemon=True).start()
        except Exception:
            log_exc("poll_clipboard")
        finally:
            self.root.after(250, self.poll_clipboard)

    def _copy_clipboard_image(self, img):
        try:
            text = ocr_image(img)
        except Exception as exc:
            msg = str(exc)
            self.root.after(0, lambda: self.show_toast(msg, 3000))
            return
        save_capture(img)
        self.root.after(0, lambda: (set_clipboard(text), self.mark_clipboard_written(),
                                    self.show_toast(f"✔ Скопировано {len(text)} симв.", 2000)))

    # ---------------- запуск режима выделения ---------------- #
    def start_snip(self, mode: str | None = None, image=None):
        if self.busy:
            return
        if mode is None:
            mode = "ai" if CFG.get("engine", "nano") == "ai" else "text"
        if self.snip is not None:
            self.snip.close()
        self.pending_image = image
        self.snip = Snip(self, mode=mode, image=image)
        try:
            self.snip.open()
        except Exception:
            self.busy = False
            self.snip = None
            log_exc("start_snip")
            self.show_toast("Не удалось открыть режим выделения", 3000)

    def start_clipboard(self):
        if self.busy:
            return
        img = clip_image()
        if img is None:
            self.show_toast("В буфере обмена нет картинки", 2500)
            return
        self.start_snip(image=img)

    # ---------------- окно с текстом от OCRia-1 (4B) ---------------- #
    def show_text_result(self, raw: str, image, dt: float,
                         title: str = "OCRia-1 (4B) — распознанный текст",
                         label: str = "OCRia-1 (4B)",
                         destroy_current: bool = True, track: bool = True):
        from PIL import ImageTk

        tk = self.tk
        if destroy_current and self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        if track:
            self.win = win
        self.last_text = raw
        win.title(title)
        win.configure(bg="#111827")
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(1240, int(sw * 0.78)), min(780, int(sh * 0.82))
        win.geometry(f"{w}x{h}+{max(0, sw - w - 40)}+70")
        win.attributes("-topmost", True)

        state = {"raw": raw, "plain": strip_markup(raw), "md": False, "pretty": False,
                 "light": None}

        top = tk.Frame(win, bg="#111827")
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text=label, bg="#111827", fg="#f9fafb",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        status = tk.Label(top, text=f"{count_words(state['plain'])[0]} слов · "
                                    f"{len(state['plain'])} симв. · {dt:.1f} с",
                          bg="#111827", fg="#34d399", font=("Segoe UI", 10))
        status.pack(side="left", padx=12)
        selinfo = tk.Label(top, text="", bg="#111827", fg="#38bdf8", font=("Segoe UI", 10))
        selinfo.pack(side="left", padx=6)
        tk.Label(top, text="выделите мышью и Ctrl+C", bg="#111827", fg="#6b7280",
                 font=("Segoe UI", 9)).pack(side="right")

        body = tk.Frame(win, bg="#111827")
        body.pack(fill="both", expand=True, padx=14, pady=6)

        # левая колонка: два вида — обычный текст (для копирования) и «красивый» рендер
        left = tk.Frame(body, bg="#0b1220")
        left.pack(side="left", fill="both", expand=True)
        text_view = tk.Frame(left, bg="#0b1220")
        pretty_view = tk.Frame(left, bg="#0b1220")

        text_widget = tk.Text(text_view, wrap="word", bg="#0b1220", fg="#e5e7eb",
                              insertbackground="#e5e7eb", relief="flat",
                              font=("Consolas", 11), padx=10, pady=8)
        sb = tk.Scrollbar(text_view, command=text_widget.yview)
        text_widget.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        text_widget.pack(side="left", fill="both", expand=True)
        text_widget.insert("1.0", state["plain"])
        text_view.pack(fill="both", expand=True)

        def on_select(_e=None):
            """Считаем слова и в выделенном куске — часто нужно именно фрагмент."""
            try:
                sel = text_widget.get("sel.first", "sel.last")
            except Exception:
                sel = ""
            if sel.strip():
                w_, c_ = count_words(sel)[:2]
                selinfo.config(text=f"выделено: {w_} слов · {c_} симв.")
            else:
                selinfo.config(text="")

        text_widget.bind("<<Selection>>", on_select)

        pretty = {"built": False}

        def show_pretty():
            if pretty["built"]:
                return
            pretty["built"] = True
            note = tk.Label(pretty_view, text="рисую формулы и таблицы…", bg="#0b1220",
                            fg="#9ca3af", font=("Segoe UI", 11))
            note.pack(pady=30)

            def work():
                img, why, blocks = None, "", []
                try:
                    if APP_DIR not in sys.path:
                        sys.path.insert(0, APP_DIR)
                    import ocria_render
                    img, blocks = ocria_render.render(state["raw"], width=980, dpi=140)
                except ImportError:
                    why = "нужен рендерер: py -m pip install matplotlib"
                    log("красивый вид недоступен: нет matplotlib")
                except Exception:
                    why = "не удалось отрисовать (подробности в журнале)"
                    log_exc("красивый рендер")
                win.after(0, lambda: build(img, why, blocks))

            def build(img, why="", blocks=()):
                note.destroy()
                if img is None:
                    tk.Label(pretty_view, text=why or "не удалось отрисовать", bg="#0b1220",
                             fg="#fbbf24", font=("Segoe UI", 10)).pack(pady=30)
                    return
                from PIL import Image

                cv = tk.Canvas(pretty_view, bg="#0b1220", highlightthickness=0)
                vs = tk.Scrollbar(pretty_view, command=cv.yview)
                cv.configure(yscrollcommand=vs.set)
                hint = tk.Label(pretty_view, text="", bg="#0b1220", fg="#6b7280",
                                font=("Segoe UI", 9))
                hint.pack(side="bottom", fill="x")     # подсказку пакуем первой
                vs.pack(side="right", fill="y")
                cv.pack(side="left", fill="both", expand=True)
                win.update_idletasks()
                target = max(320, cv.winfo_width() - 10)
                scale = 1.0
                if img.width > target:
                    scale = target / img.width
                    img = img.resize((target, max(1, int(img.height * scale))), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._photo_refs.append(photo)
                cv.create_image(4, 4, anchor="nw", image=photo)
                cv.configure(scrollregion=(0, 0, img.width + 8, img.height + 8))
                cv.bind("<MouseWheel>",
                        lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))
                # клик по блоку — скопировать его картинкой

                def block_at(cy):
                    for bb in blocks:
                        if bb["y0"] * scale <= cy <= bb["y1"] * scale:
                            return bb
                    return None

                state["blocks"] = blocks

                def on_click(e):
                    cy = e.y - 4 + cv.canvasy(0)
                    bb = block_at(cy)
                    if bb is None:
                        return
                    copy_block_image(bb)

                def on_move(e):
                    cy = e.y - 4 + cv.canvasy(0)
                    bb = block_at(cy)
                    if bb is None:
                        cv.configure(cursor="")
                        hint.config(text="")
                    else:
                        cv.configure(cursor="hand2")
                        hint.config(text=f"нажмите — скопировать картинкой ({block_name(bb)})",
                                    fg="#34d399")

                cv.bind("<Button-1>", on_click)
                cv.bind("<Motion>", on_move)

                def block_name(bb):
                    return {"math": "формула", "table": "таблица", "h": "заголовок",
                            "code": "код"}.get(bb["kind"], "абзац")

                def copy_block_image(bb):
                    def work2():
                        try:
                            from PIL import ImageChops, ImageOps

                            full = render_light()
                            pad = 10
                            y0 = max(0, int(bb["y0"]) - pad)
                            y1 = min(full.height, int(bb["y1"]) + pad)
                            crop = full.crop((0, y0, full.width, y1)).convert("RGB")
                            # обрезаем белые поля: оставляем только сам блок
                            ink = ImageOps.invert(crop.convert("L")).getbbox()
                            if ink:
                                x0, yy0, x1, yy1 = ink
                                crop = crop.crop((max(0, x0 - 8), max(0, yy0 - 8),
                                                  min(crop.width, x1 + 8),
                                                  min(crop.height, yy1 + 8)))
                            set_clipboard("", crop)
                            self.mark_clipboard_written()
                            win.after(0, lambda: put_image(
                                f"✔ {block_name(bb)} скопирована картинкой"))
                        except Exception as exc:
                            log(f"копирование картинкой не удалось: {exc}")
                            win.after(0, lambda: status.config(text="не удалось скопировать",
                                                               fg="#fbbf24"))

                    threading.Thread(target=work2, daemon=True).start()

                def put_image(msg):
                    status.config(text=msg, fg="#34d399")

            threading.Thread(target=work, daemon=True).start()

        def set_view(is_pretty: bool):
            state["pretty"] = is_pretty
            if is_pretty:
                text_view.pack_forget()
                pretty_view.pack(fill="both", expand=True)
                show_pretty()
                btn_view.config(text="Текст")
            else:
                pretty_view.pack_forget()
                text_view.pack(fill="both", expand=True)
                btn_view.config(text="Красиво")

        if image is not None:
            prev = image.copy()
            ratio = min(300 / prev.width, (h - 170) / prev.height, 1.0)
            if ratio < 1.0:
                prev = prev.resize((max(1, int(prev.width * ratio)),
                                    max(1, int(prev.height * ratio))))
            photo = ImageTk.PhotoImage(prev)
            self._photo_refs.append(photo)
            holder = tk.Frame(body, bg="#111827")
            holder.pack(side="right", fill="y", padx=(12, 0))
            tk.Label(holder, text="Выделенный фрагмент", bg="#111827", fg="#9ca3af",
                     font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(holder, image=photo, bg="#111827", bd=1, relief="solid").pack(anchor="n")

        bottom = tk.Frame(win, bg="#111827")
        bottom.pack(fill="x", padx=14, pady=(4, 12))

        def put(text_: str, what: str):
            try:
                set_clipboard(text_)
                self.mark_clipboard_written()
                status.config(text=f"✔ скопировано {what}: {len(text_)} симв.", fg="#34d399")
            except Exception:
                status.config(text="буфер обмена занят", fg="#fbbf24")

        def copy_sel(_e=None):
            try:
                sel = text_widget.get("sel.first", "sel.last")
            except Exception:
                return "break"
            if sel:
                put(sel, "выделенное")
            return "break"

        def current() -> str:
            return text_widget.get("1.0", "end-1c")

        def mk(text_, cmd, primary=False):
            bg = "#22d3ee" if primary else "#1f2937"
            fg = "#0b1220" if primary else "#e5e7eb"
            b = tk.Button(bottom, text=text_, command=cmd, bg=bg, fg=fg, relief="flat",
                          activebackground="#38bdf8" if primary else "#374151",
                          activeforeground=fg, font=("Segoe UI", 10, "bold"),
                          padx=14, pady=6, cursor="hand2")
            b.pack(side="left", padx=4)
            return b

        def do_save():
            from tkinter import filedialog

            path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="ocr.txt",
                                                filetypes=[("Текст", "*.txt"), ("Все файлы", "*.*")])
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(current())

        def toggle_md():
            state["md"] = not state["md"]
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", state["raw"] if state["md"] else state["plain"])
            btn_md.config(text="Обычный текст" if state["md"] else "Markdown")

        def render_light():
            if state.get("light") is None:
                if APP_DIR not in sys.path:
                    sys.path.insert(0, APP_DIR)
                import ocria_render
                state["light"] = ocria_render.render(state["raw"], width=980, dpi=140,
                                                     theme="light")[0]
            return state["light"]

        def whole_image():
            """Вся распознанная страница картинкой (светлый фон — для вставки в документ)."""

            def work():
                try:
                    full = render_light()
                    set_clipboard("", full)
                    self.mark_clipboard_written()
                    win.after(0, lambda: status.config(
                        text="✔ вся страница скопирована картинкой", fg="#34d399"))
                except Exception:
                    log_exc("копирование всей страницы картинкой")

            threading.Thread(target=work, daemon=True).start()

        def save_png():
            from tkinter import filedialog

            path = filedialog.asksaveasfilename(defaultextension=".png", initialfile="ocr.png",
                                                filetypes=[("Картинка", "*.png"), ("Все файлы", "*.*")])
            if path:
                try:
                    render_light().save(path)
                    status.config(text="✔ картинка сохранена", fg="#34d399")
                except Exception:
                    log_exc("сохранение картинки")

        def solve():
            """Отдаём распознанный текст обычной языковой модели: она считает, OCR только читает."""
            text = state["plain"].strip()
            if not text:
                status.config(text="нечего решать — текста нет", fg="#fbbf24")
                return
            model = solver_cfg()["model"]
            status.config(text=f"решаю моделью {model}…", fg="#fbbf24")

            def work():
                try:
                    ans = ollama_solve(text)
                except Exception as exc:
                    msg = str(exc)[:70]
                    log(f"решение не удалось: {msg}")
                    win.after(0, lambda: status.config(text=f"не получилось: {msg}",
                                                       fg="#fbbf24"))
                    return
                if not ans.strip():
                    win.after(0, lambda: status.config(text="модель вернула пустой ответ",
                                                       fg="#fbbf24"))
                    return
                log(f"решение получено: {len(ans)} симв.")

                def show():
                    status.config(text="✔ решение — в отдельном окне", fg="#34d399")
                    self.show_text_result(ans, None, 0.0,
                                          title=f"Решение · {model}", label="Решение",
                                          destroy_current=False, track=False)

                win.after(0, show)

            threading.Thread(target=work, daemon=True).start()

        def check():
            """Проверка текста: движок выбирается в трее — модель или правила LanguageTool."""
            text = state["plain"].strip()
            if not text:
                status.config(text="нечего проверять — текста нет", fg="#fbbf24")
                return
            status.config(text="проверяю текст…", fg="#fbbf24")

            def work():
                try:
                    fixed, marks, notes, engine = run_check(text)
                except Exception as exc:
                    msg = str(exc)[:80]
                    log(f"проверка текста не удалась: {msg}")
                    win.after(0, lambda: status.config(text=f"не получилось: {msg}",
                                                       fg="#fbbf24"))
                    return
                if not fixed.strip():
                    win.after(0, lambda: status.config(text="пустой результат проверки",
                                                       fg="#fbbf24"))
                    return
                log(f"проверка ({engine}): правок {len(marks)}, замечаний {len(notes)}")

                def show():
                    status.config(text=f"✔ {engine}: правок {len(marks)}", fg="#34d399")
                    self.show_check_result(text, fixed, marks, notes, engine)

                win.after(0, show)

            threading.Thread(target=work, daemon=True).start()

        mk("Копировать всё", lambda: put(current(), "всё"), primary=True)
        mk("Копировать выделенное", copy_sel)
        mk("Решить", solve)
        mk("Проверить", check)
        btn_view = mk("Красиво", lambda: set_view(not state.get("pretty", False)))
        mk("Картинкой", whole_image)
        mk("Markdown", toggle_md)
        mk("Сохранить .txt", do_save)
        mk("Сохранить .png", save_png)
        mk("Закрыть", win.destroy)

        win.bind("<Escape>", lambda e: win.destroy())
        for wdg in (win, text_widget):
            wdg.bind("<Control-c>", copy_sel)
            wdg.bind("<Control-C>", copy_sel)
        text_widget.focus_set()

        # если в ответе есть формулы или таблицы — сразу показываем красивый вид
        try:
            if APP_DIR not in sys.path:
                sys.path.insert(0, APP_DIR)
            import ocria_render
            if ocria_render.looks_rich(state["raw"]):
                set_view(True)
        except Exception:
            pass

    # ---------------- окно проверки орфографии и пунктуации ---------------- #
    def show_check_result(self, original: str, fixed: str, marks, notes=(), engine=""):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title(f"Проверка текста · {engine}" if engine else "Проверка текста")
        win.configure(bg="#111827")
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(1100, int(sw * 0.72)), min(700, int(sh * 0.8))
        win.geometry(f"{w}x{h}+{max(0, sw - w - 40)}+70")
        win.attributes("-topmost", True)

        top = tk.Frame(win, bg="#111827")
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="Проверка орфографии и пунктуации", bg="#111827", fg="#f9fafb",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        wc = count_words(fixed)
        summary = f"{wc[0]} слов · {len(fixed)} симв.  ·  правок: {len(marks)}"
        if notes:
            summary += f"  ·  замечаний: {len(notes)}"
        status = tk.Label(top, text=summary, bg="#111827", fg="#34d399",
                          font=("Segoe UI", 10))
        status.pack(side="left", padx=12)
        selinfo = tk.Label(top, text="", bg="#111827", fg="#38bdf8", font=("Segoe UI", 10))
        selinfo.pack(side="left", padx=6)
        tk.Label(top, text="зелёным — что исправлено", bg="#111827", fg="#6b7280",
                 font=("Segoe UI", 9)).pack(side="right")

        body = tk.Frame(win, bg="#0b1220")
        body.pack(fill="both", expand=True, padx=14, pady=6)
        tw = tk.Text(body, wrap="word", bg="#0b1220", fg="#e5e7eb",
                     insertbackground="#e5e7eb", relief="flat",
                     font=("Consolas", 11), padx=10, pady=8)
        sb = tk.Scrollbar(body, command=tw.yview)
        tw.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tw.pack(side="left", fill="both", expand=True)
        tw.tag_config("fix", background="#065f46", foreground="#d1fae5")
        view = {"mode": "fixed"}

        def paint():
            tw.delete("1.0", "end")
            if view["mode"] == "fixed":
                tw.insert("1.0", fixed)
                for a, b in marks:
                    tw.tag_add("fix", f"1.0+{a}c", f"1.0+{b}c")
            elif view["mode"] == "original":
                tw.insert("1.0", original)
            else:
                if not notes:
                    tw.insert("1.0", "Ничего не найдено — текст чистый.")
                for i, n in enumerate(notes, 1):
                    tw.insert("end", f"{i}. [{n['rule']}] {n['message']}\n")
                    if n["before"] or n["after"]:
                        tw.insert("end", f"    «{n['before']}» → «{n['after']}»\n\n")

        paint()
        bottom = tk.Frame(win, bg="#111827")
        bottom.pack(fill="x", padx=14, pady=(4, 12))

        def put(text_, what):
            try:
                set_clipboard(text_)
                self.mark_clipboard_written()
                status.config(text=f"✔ скопировано {what}: {len(text_)} симв.", fg="#34d399")
            except Exception:
                status.config(text="буфер обмена занят", fg="#fbbf24")

        def copy_sel(_e=None):
            try:
                sel = tw.get("sel.first", "sel.last")
            except Exception:
                return "break"
            if sel:
                put(sel, "выделенное")
            return "break"

        def mk(text_, cmd, primary=False):
            bg = "#22d3ee" if primary else "#1f2937"
            fg = "#0b1220" if primary else "#e5e7eb"
            b = tk.Button(bottom, text=text_, command=cmd, bg=bg, fg=fg, relief="flat",
                          activebackground="#38bdf8" if primary else "#374151",
                          activeforeground=fg, font=("Segoe UI", 10, "bold"),
                          padx=14, pady=6, cursor="hand2")
            b.pack(side="left", padx=4)
            return b

        def toggle():
            view["mode"] = "fixed" if view["mode"] == "original" else "original"
            paint()
            btn.config(text="Что было" if view["mode"] == "fixed" else "Исправленный")

        def toggle_rules():
            view["mode"] = "fixed" if view["mode"] == "rules" else "rules"
            paint()
            btn_rules.config(text="Правила" if view["mode"] != "rules" else "Текст")

        mk("Копировать всё", lambda: put(tw.get("1.0", "end-1c"), "всё"), primary=True)
        mk("Копировать выделенное", copy_sel)
        btn = mk("Что было", toggle)
        btn_rules = mk("Правила", toggle_rules) if notes else None
        mk("Закрыть", win.destroy)
        tk.Label(bottom, text="Esc — закрыть", bg="#111827", fg="#6b7280",
                 font=("Segoe UI", 9)).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        for wdg in (win, tw):
            wdg.bind("<Control-c>", copy_sel)

        def on_select(_e=None):
            try:
                sel = tw.get("sel.first", "sel.last")
            except Exception:
                sel = ""
            if sel.strip():
                w_, c_ = count_words(sel)[:2]
                selinfo.config(text=f"выделено: {w_} слов · {c_} симв.")
            else:
                selinfo.config(text="")

        tw.bind("<<Selection>>", on_select)
        tw.focus_set()

    def check_last(self):
        """Проверка последнего распознанного текста — вызывается из трея."""
        text = (self.last_text or "").strip()
        if not text:
            self.show_toast("Сначала распознайте текст — потом проверю", 2500)
            return
        engine = str((CFG.get("checker") or {}).get("engine", "rules")).lower()
        what = "правилами LanguageTool" if engine == "rules" else f"моделью {checker_cfg()['model']}"
        self.show_toast(f"Проверяю текст {what}…", 2500)

        def work():
            try:
                fixed, marks, notes, engine = run_check(text)
            except Exception as exc:
                msg = str(exc)[:70]
                log(f"проверка из трея не удалась: {msg}")
                self.root.after(0, lambda: self.show_toast(f"Не получилось: {msg}", 4000))
                return
            if not fixed.strip():
                self.root.after(0, lambda: self.show_toast("Пустой результат проверки", 3000))
                return
            log(f"проверка из трея ({engine}): правок {len(marks)}, замечаний {len(notes)}")
            self.root.after(0, lambda: self.show_check_result(text, fixed, marks, notes, engine))

        threading.Thread(target=work, daemon=True).start()

    def count_last(self):
        """Подсчёт слов в последнем распознанном тексте."""
        text = self.last_text or ""
        if not text.strip():
            self.show_toast("Пока нечего считать — сначала распознайте текст", 2500)
            return
        words, chars, nospace, lines = count_words(text)
        self.show_toast(f"Слов: {words}  ·  символов: {chars} (без пробелов {nospace})  ·  "
                        f"строк: {lines}", 5000)

    def set_check_engine(self, kind: str):
        """Чем проверять текст: языковой моделью или правилами LanguageTool."""
        kind = "rules" if kind == "rules" else "model"
        if kind == "rules" and not lt_available():
            self.show_toast("LanguageTool не установлен (py -m pip install language-tool-python)",
                            4000)
            return
        if kind == "rules" and _find_java() is None:
            self.show_toast("LanguageTool установлен, но не найдена Java — "
                            "поставьте Eclipse Temurin JRE 17", 4500)
            return
        CFG.setdefault("checker", {})["engine"] = kind
        save_config()
        log(f"движок проверки: {kind}")
        if kind == "rules":
            lt_warmup()
        what = "правилами LanguageTool" if kind == "rules" else f"моделью {checker_cfg()['model']}"
        self.show_toast(f"Текст проверяю {what}", 3000)

    # ---------------- toast ---------------- #
    def show_toast(self, message: str, ms: int = 2200):
        tk = self.tk
        if self.toast is not None:
            try:
                self.toast.destroy()
            except Exception:
                pass
        t = tk.Toplevel(self.root)
        self.toast = t
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg="#0f172a")
        tk.Label(t, text=message, bg="#0f172a", fg="#e5e7eb",
                 font=("Segoe UI", 10), padx=18, pady=12).pack()
        t.update_idletasks()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        w, h = t.winfo_reqwidth(), t.winfo_reqheight()
        t.geometry(f"{w}x{h}+{sw - w - 30}+{sh - h - 60}")
        t.bind("<Button-1>", lambda e: t.destroy())

        def hide():
            if self.toast is t:
                self.toast = None
            try:
                t.destroy()
            except Exception:
                pass

        t.after(ms, hide)


# --------------------------------------------------------------------------- #
def selftest(path: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from PIL import Image

    img = Image.open(path)
    t0 = time.time()
    text = ocr_image(img)
    dt = time.time() - t0
    print(f"[selftest] {path}  {img.size}  {dt:.2f}s  ({len(text)} chars)")
    print("--- текст ---")
    print(text)
    set_clipboard(text, img)
    print("--- буфер: текст OK" if get_clipboard_text() == text else "--- буфер: MISMATCH")
    t0 = time.time()
    words = ocr_image_words(img)
    print(f"--- слов: {len(words)} за {time.time()-t0:.2f}s ---")
    lines = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    for ln in list(lines)[:4]:
        ws = sorted(lines[ln], key=lambda w: w["x"])
        print(f"  строка {ln}: x={ws[0]['x']:.0f}..{ws[-1]['x']+ws[-1]['w']:.0f} :: "
              + " ".join(w["text"] for w in ws))
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])

    set_dpi_awareness()
    if not single_instance():
        return 0

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    # любое исключение в обработчике Tk иначе пропадает молча (у pythonw нет консоли)
    root.report_callback_exception = lambda exc, val, tb: log(
        "Tk-обработчик упал: " + "".join(traceback.format_exception(exc, val, tb)))
    if os.path.exists(ICON_ICO):
        try:
            root.iconbitmap(default=ICON_ICO)   # иконка для всех окон приложения
        except Exception:
            pass
    app = App(root)
    log(f"запуск: движок={CFG.get('engine')}, python={sys.version.split()[0]}, "
        f"хоткеи={app.snip_keys_text()}")
    removed = clean_temp_dir()
    if removed:
        print(f"[ocr snap] убрано временных файлов: {removed}", file=sys.stderr)

    keys = app.snip_keys_text() or "—"
    msg = (f"OCRia Snap запущен · {app.engine_name()}\n"
           f"{keys} — тяните рамку\n"
           f"Смена модели — правой кнопкой по значку в трее")
    win_s_free = any(combo == App._parse("win+shift+s") for combo in app.active.get("snip", []))
    if not win_s_free:
        msg += "\nWin+Shift+S ещё держит винда — запустите take-over-winshifts.cmd"
    root.after(300, lambda: app.show_toast(msg, 5000))

    root.after(50, app.poll_hotkeys)
    if CFG.get("clipboard_action", "off") != "off":
        root.after(300, app.poll_clipboard)
    start_tray(app)
    if str((CFG.get("checker") or {}).get("engine", "rules")).lower() == "rules":
        # проверка правилами выбрана — заранее поднимаем LanguageTool, чтобы первый раз не ждал
        lt_warmup()
    if CFG.get("engine", "nano") == "ai":
        # движок 4B выбран — прогреваем модель в фоне, чтобы первый снимок не ждал
        threading.Thread(target=ollama_load_model, daemon=True).start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    if app.tray is not None:
        try:
            app.tray.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
