"""Ищем обращения self.X, для которых X нигде не присваивается, — забытые поля класса."""
import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "ocria_snap.pyw")

tree = ast.parse(io.open(PATH, encoding="utf-8").read())

# имена, приходящие извне объекта и потому безопасные
KNOWN = {"tk", "root", "master", "canvas", "win", "after"}  # 'win' у Snip/App объявлен

bad_total = 0
for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
    methods = {m.name for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # атрибуты, объявленные прямо в теле класса (KEEP_NAMES = {...}) — это законно
    class_attrs = {t.id for n in cls.body if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
    assigned, loaded = set(class_attrs), {}
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                assigned.add(node.attr)
            else:
                loaded.setdefault(node.attr, node.lineno)
    missing = [(ln, a) for a, ln in loaded.items() if a not in assigned and a not in methods]
    print(f"=== класс {cls.name} ===")
    if not missing:
        print("   всё присваивается, обращений к забытым полям нет")
    for ln, a in sorted(missing):
        print(f"   строка {ln:5}: self.{a}  — НИГДЕ НЕ ПРИСВАИВАЕТСЯ")
        bad_total += 1

print()
print("ИТОГО подозрительных:", bad_total)
sys.exit(1 if bad_total else 0)
