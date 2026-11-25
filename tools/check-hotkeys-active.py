"""Какие хоткеи реально активны у приложения прямо сейчас."""
import importlib.util
import os
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("ocria_snap", os.path.join(ROOT, "ocria_snap.pyw"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

root = tk.Tk()
root.withdraw()
app = m.App(root)
for name, combos in app.active.items():
    specs = []
    for spec_str, combo in zip(app._combos_spec(name), app._combos(name)):
        if combo in combos:
            specs.append(spec_str)
    print(f"{name:12} -> {specs if specs else 'НЕТ АКТИВНЫХ'}")
root.destroy()
