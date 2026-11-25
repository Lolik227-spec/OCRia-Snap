"""Юнит-проверки клиента OCRia-1 (без обращения к модели)."""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ocria_snap", os.path.join(HERE, "ocria_snap.pyw"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def check(label, cond, extra=""):
    print(f"   {label:44} {'OK' if cond else 'FAIL'} {extra}")
    if not cond:
        fails.append(label)


raw = (
    "```markdown\n"
    "# Заголовок отчёта\n"
    "**жирный** и ~~зачёркнутый~~ текст\n"
    "<table><tr><th>Товар</th><th>Цена</th></tr>\n"
    "<tr><td>Кабель</td><td>34,50</td></tr></table>\n"
    "<page_footer>стр. 3</page_footer>\n"
    "```"
)
plain = m.strip_markup(raw)
print("--- strip_markup ---")
print(plain)
check("кавычки-ограждение убраны", "```" not in plain and "markdown" not in plain)
check("заголовок без #", "#" not in plain)
check("жирный без **", "**" not in plain)
check("HTML-теги убраны", "<" not in plain)
check("таблица в текст", "Кабель" in plain and "34,50" in plain)
check("подвал страницы виден текстом", "стр. 3" in plain)

looped = "начало " + "| " * 4000
cut = m.cut_loop(looped)
print("--- cut_loop ---")
print(f"   было {len(looped)} -> стало {len(cut)}")
check("цикл обрезан", len(cut) < len(looped) // 2)
check("нормальный текст не тронут",
      m.cut_loop("обычный текст без повторов, вполне себе нормальный") ==
      "обычный текст без повторов, вполне себе нормальный")

ok, msg = m.ai_status()
print("--- ai_status ---")
print(f"   ok={ok}: {msg.splitlines()[0] if msg else ''}")
check("ai_status не падает", isinstance(msg, str) and len(msg) > 0)

# производительность на длинном нормальном тексте
import random
import time

random.seed(0)
words = ["отчёт", "склад", "заявка", "товар", "сумма", "период", "обработка",
         "минута", "номер", "адрес", "клиент", "статус", "кабель", "роутер"]
big = " ".join(random.choice(words) for _ in range(8000))
t0 = time.time()
out = m.cut_loop(big)
dt = time.time() - t0
check(f"нормальный текст не режется ({dt*1000:.0f} мс на {len(big)} симв.)",
      out == big and dt < 1.0)

print("AI-UNIT", "OK" if not fails else "FAIL " + "; ".join(fails))
sys.exit(1 if fails else 0)
