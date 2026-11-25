"""
Красивый вывод результата OCRia-1: формулы LaTeX, таблицы, заголовки -> картинка.

Рисуем через matplotlib: у него встроенный движок математики (mathtext), поэтому
ни LaTeX, ни интернета не нужно. Понимает \\sqrt, \\frac, степени, индексы,
греческие буквы, интегралы, \\left(...\\right).

    import ocria_render
    img = ocria_render.render(markdown_text, width=1000)   # -> PIL.Image
"""
from __future__ import annotations

import re

TEXT_C = "#e5e7eb"
DIM_C = "#9ca3af"
ACCENT = "#22d3ee"
RULE_C = "#374151"

# тёмная тема — для показа в окне, светлая — для копирования картинкой в документ
THEMES = {
    "dark": {"bg": None, "text": "#e5e7eb", "dim": "#9ca3af",
             "accent": "#22d3ee", "rule": "#374151"},
    "light": {"bg": "#ffffff", "text": "#111827", "dim": "#4b5563",
              "accent": "#0b6e7f", "rule": "#9ca3af"},
}

TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t([dh])([^>]*)>(.*?)</t[dh]>", re.S | re.I)
COLSPAN_RE = re.compile(r'colspan\s*=\s*"?(\d+)', re.I)
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*```")
PART_RE = re.compile(r"(\$[^$]*\$)")

# конструкции mathtext не понимает — такие формулы показываем как есть
UNSUPPORTED = ("\\begin", "\\end", "\\\\", "\\over", "\\substack")


def clean_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def parse_blocks(text: str):
    """-> ('h', ур., текст) | ('p', текст) | ('math', формула) | ('table', строки) | ('code', текст)"""
    m = re.search(r"```[a-zA-Z]*\n(.*?)(?:\n```|$)", text, re.S)
    if m:
        text = m.group(1)
    blocks, para = [], []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0

    def flush():
        if para:
            joined = " ".join(x.strip() for x in para if x.strip())
            if joined:
                blocks.append(("p", joined))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()

        if FENCE_RE.match(line):
            flush()
            i += 1
            code = []
            while i < len(lines) and not FENCE_RE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            if code:
                blocks.append(("code", "\n".join(code)))
            continue

        if "<table" in line.lower():
            flush()
            html = line                      # таблица у модели занимает несколько строк
            while "</table>" not in html.lower() and i + 1 < len(lines):
                i += 1
                html += "\n" + lines[i]
            rows = []
            for row in ROW_RE.finditer(html):
                cells = []
                for _kind, attrs, body in CELL_RE.findall(row.group(1)):
                    span = COLSPAN_RE.search(attrs or "")
                    cells.append((clean_html(body), int(span.group(1)) if span else 1))
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append(("table", rows))
            i += 1
            continue

        if not s:
            flush()
            i += 1
            continue

        mh = HEAD_RE.match(s)
        if mh:
            flush()
            blocks.append(("h", len(mh.group(1)), clean_html(mh.group(2))))
            i += 1
            continue

        if (s.startswith("$$") and s.endswith("$$") and len(s) > 4) or \
           (s.startswith("\\[") and s.endswith("\\]")):
            flush()
            blocks.append(("math", s[2:-2].strip()))
            i += 1
            continue

        para.append(line)
        i += 1
    flush()
    return blocks


def prepare_math(src: str):
    """LaTeX -> mathtext. None, если не берёмся."""
    s = src.strip()
    for bad in UNSUPPORTED:
        if bad in s:
            return None
    s = re.sub(r"\\(d|t)frac", r"\\frac", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\\operatorname\*?\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = s.replace("{,}", ",").replace("{.}", ".").replace("~", " ")
    s = re.sub(r"\\displaystyle|\\limits|\\!|\\;|\\,|\\quad|\\qquad|\\label\{[^{}]*\}|\\tag\{[^{}]*\}",
               " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def has_cyrillic(s: str) -> bool:
    return any("\u0400" <= c <= "\u04ff" for c in s)


def looks_rich(text: str) -> bool:
    if "$" in text or "<table" in text.lower():
        return True
    return bool(re.search(r"^\s*#{1,6}\s", text, re.M))


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("$", r"\$").replace("`", "")


class _Size:
    """Измерение текста в пикселях на пробной фигуре (mathtext парсится здесь же)."""

    def __init__(self, dpi: int):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        self.fig = Figure(figsize=(10, 10), dpi=dpi)
        self.canvas = FigureCanvasAgg(self.fig)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.axis("off")

    def __call__(self, s, fs, fam="cm", weight="normal"):
        try:
            t = self.ax.text(0, 0, s, fontsize=fs, math_fontfamily=fam, fontweight=weight)
            bb = t.get_window_extent(self.canvas.get_renderer())
            t.remove()
            return bb.width, bb.height
        except Exception:
            return None

    def close(self):
        import matplotlib.pyplot as plt

        plt.close(self.fig)


def render(text: str, width: int = 1000, font_size: int = 13, dpi: int = 140,
           theme: str = "dark"):
    """Текст от модели -> (картинка, список блоков с их координатами).

    theme="dark"  — прозрачный фон, светлый текст: для показа в окне;
    theme="light" — белый фон, тёмный текст: чтобы вставлять картинкой в документы.
    """
    import matplotlib
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
    from PIL import Image

    pal = THEMES.get(theme, THEMES["dark"])
    TEXT_C, DIM_C, ACCENT, RULE_C = pal["text"], pal["dim"], pal["accent"], pal["rule"]

    blocks = parse_blocks(text)
    if not blocks:
        return None, []

    margin, gap = 22, 6
    body_w = width - margin * 2
    size = _Size(dpi)
    draws = []          # примитивы с абсолютными координатами
    found = []          # границы блоков, чтобы можно было копировать их по отдельности

    def push_text(x, y, s, fs, color=TEXT_C, weight="normal", family=None, va="top"):
        draws.append(("text", x, y, s, fs, color, weight, family, va))

    def push_line(x1, y1, x2, y2, color=RULE_C, lw=1.0):
        draws.append(("line", x1, y1, x2, y2, color, lw))

    def push_rect(x, y, w, h, edge=RULE_C, lw=1.0):
        draws.append(("rect", x, y, w, h, edge, lw))

    def piece(kind, val, fs, weight="normal"):
        """Кусок строки -> (что рисовать, ширина, math_fontfamily)."""
        if kind == "math":
            prep = prepare_math(val)
            if prep is not None:
                s = "$" + prep + "$"
                fam = "dejavusans" if has_cyrillic(prep) else "cm"
                got = size(s, fs, fam, weight)
                if got:
                    return s, got[0], fam
            return _esc(val), size(_esc(val), fs, "cm", weight)[0], "cm"
        return _esc(val), size(_esc(val), fs, "cm", weight)[0], "cm"

    def wrap(items, fs):
        """Жадная укладка пар (слово, жирность); формулы не разрываются."""
        space_w = size(" ", fs)[0]
        lines, cur, cur_w = [], [], 0.0
        for word, bold in items:
            weight = "bold" if bold else "normal"
            ww = sum(piece(k, v, fs, weight)[1] for k, v in _split_pieces(word))
            if cur and cur_w + space_w + ww > body_w:
                lines.append(cur)
                cur, cur_w = [(word, bold)], ww
            else:
                cur_w += (space_w + ww) if cur else ww
                cur.append((word, bold))
        if cur:
            lines.append(cur)
        return lines, space_w

    y = float(margin)

    def plain_of(b):
        if b[0] == "h":
            return b[2]
        if b[0] == "table":
            return "\n".join(" | ".join(c[0] for c in row) for row in b[1])
        return str(b[1])

    for b in blocks:
        kind = b[0]
        y0 = y
        src = plain_of(b)

        if kind == "h":
            lvl, txt = b[1], b[2]
            fs = {1: 22, 2: 19, 3: 17}.get(lvl, 15)
            _, h = size(_esc(txt), fs, "cm", "bold")
            push_text(margin, y, _esc(txt), fs, TEXT_C, "bold")
            y += h + gap
        elif kind == "p":
            fs = font_size
            items = tokenize_rich(b[1])
            lines, space_w = wrap(items, fs) if items else ([], 0.0)
            base_h = size("Ай", fs)[1]              # высота обычной строки текста
            for ln in lines:
                # сначала измерим строку целиком, чтобы высокие формулы не наезжали на соседей
                parts, line_h = [], base_h
                for wi, (word, bold) in enumerate(ln):
                    weight = "bold" if bold else "normal"
                    if wi:
                        parts.append((" ", space_w, "cm", "normal"))
                    for k, v in _split_pieces(word):
                        s, w, fam = piece(k, v, fs, weight)
                        line_h = max(line_h, size(s, fs, fam, weight)[1])
                        parts.append((s, w, fam, weight))
                if line_h > base_h * 1.6:
                    y += (line_h - base_h) * 0.35     # воздух под высокую формулу
                x = float(margin)
                for s, w, fam, weight in parts:
                    if s.startswith("$"):
                        push_text(x, y + base_h / 2, s, fs, TEXT_C, weight, fam, va="center")
                    else:
                        push_text(x, y, s, fs, TEXT_C, weight, fam)
                    x += w
                y += line_h + gap
        elif kind == "math":
            prep = prepare_math(b[1])
            if prep is None:
                h = size(_esc(b[1]), font_size)[1]
                push_text(margin, y, "$" + _esc(b[1]) + "$", font_size, DIM_C)
                y += h + gap
            else:
                fam = "dejavusans" if has_cyrillic(prep) else "cm"
                s = "$" + prep + "$"
                got = size(s, font_size + 3, fam)
                draws.append(("text_c", width / 2, y, s, font_size + 3, ACCENT, "normal",
                              fam, "top"))
                y += got[1] + gap
        elif kind == "code":
            for ln in b[1].split("\n"):
                h = size(_esc(ln or " "), font_size - 1)[1]
                push_text(margin + 8, y, _esc(ln), font_size - 1, DIM_C, "normal", "mono")
                y += h + 2
            y += gap
        elif kind == "table":
            rows = b[1]
            cell_fs = font_size - 1
            ncol = max(sum(c[1] for c in row) for row in rows)
            col_w = [0.0] * ncol
            row_h = []
            for row in rows:
                ci, rh = 0, 0.0
                for text_, span in row:
                    got = size(_esc(text_), cell_fs)
                    for k in range(span):
                        if ci + k < ncol:
                            col_w[ci + k] = max(col_w[ci + k], got[0] / span)
                    rh = max(rh, got[1])
                    ci += span
                row_h.append(rh + 14)
            col_w = [w + 14 for w in col_w]      # запас под отступы ячеек
            total = sum(col_w)
            if total > body_w:
                k = body_w / total
                col_w = [w * k for w in col_w]
            tw, tbl_h = sum(col_w), sum(row_h)
            push_rect(margin, y, tw, tbl_h)
            yy = y
            for row, rh in zip(rows, row_h):
                ci, xx = 0, float(margin)
                for text_, span in row:
                    span_w = sum(col_w[ci:ci + span])
                    push_text(xx + 6, yy + 7, _esc(text_), cell_fs)
                    xx += span_w
                    push_line(xx, yy, xx, yy + rh)
                    ci += span
                yy += rh
                push_line(margin, yy, margin + tw, yy)
            y = yy + gap
        elif kind == "rule":
            push_line(margin, y, width - margin, y)
            y += gap

        found.append({"kind": kind, "y0": y0, "y1": max(y0 + 4.0, y - gap),
                      "text": src})

    total_h = int(max(y + margin, 60))
    size.close()

    fig = Figure(figsize=(width / dpi, total_h / dpi), dpi=dpi)
    fig.patch.set_alpha(0.0)
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(total_h, 0)
    ax.axis("off")
    ax.patch.set_alpha(0.0)

    def paint(items):
        ax.clear()
        ax.set_xlim(0, width)
        ax.set_ylim(total_h, 0)
        ax.axis("off")
        ax.patch.set_alpha(0.0)
        if pal["bg"]:                     # светлая тема — плотный фон для вставки в документ
            ax.add_patch(Rectangle((0, 0), width, total_h, facecolor=pal["bg"],
                                   edgecolor="none", zorder=-1000))
        for it in items:
            k = it[0]
            if k in ("text", "text_c"):
                _, x, yy, s, fs, color, weight, fam, va = it
                kw = {"ha": "center"} if k == "text_c" else {}
                extra = {}
                if fam == "mono":
                    extra["family"] = "monospace"
                elif fam:
                    extra["math_fontfamily"] = fam
                ax.text(x, yy, s, fontsize=fs, color=color, va=va,
                        fontweight=weight, **kw, **extra)
            elif k == "line":
                _, x1, y1, x2, y2, color, lw = it
                ax.plot([x1, x2], [y1, y2], color=color, linewidth=lw)
            elif k == "rect":
                _, x, yy, w, h, edge, lw = it
                ax.add_patch(Rectangle((x, yy), w, h, fill=False, edgecolor=edge,
                                       linewidth=lw))

    try:
        paint(draws)
        canvas.draw()
    except Exception:
        # формула не по зубам движку — рисуем всё как обычный текст
        safe = []
        for it in draws:
            if it[0] in ("text", "text_c") and "$" in it[3]:
                item = list(it)
                item[3] = it[3].replace("$", "")
                item[7] = None
                safe.append(tuple(item))
            else:
                safe.append(it)
        paint(safe)
        canvas.draw()

    return Image.fromarray(np.asarray(canvas.buffer_rgba())), found


def _split_pieces(s: str):
    """Строка -> куски ('text'|'math', значение)."""
    for part in PART_RE.split(s):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            yield "math", part[1:-1]
        else:
            yield "text", part


MATH_RE = re.compile(r"\$[^$]*\$")


def tokenize_rich(text: str):
    """Слова с флагом жирности; формулы $...$ остаются одним куском."""
    items, pos, bold = [], 0, False
    for m in re.finditer(r"\*\*|\$[^$]*\$", text):
        for w in text[pos:m.start()].split():
            items.append((w, bold))
        if m.group(0) == "**":
            bold = not bold
        else:
            items.append((m.group(0), bold))
        pos = m.end()
    for w in text[pos:].split():
        items.append((w, bold))
    return items
