"""Проверка: свободна ли комбинация Win+Shift+S (никто её не держит)."""
import ctypes
import sys

u32 = ctypes.windll.user32
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NOREPEAT = 0x4000

VK_S = 0x53
ok = u32.RegisterHotKey(None, 0x9C42, MOD_WIN | MOD_SHIFT | MOD_NOREPEAT, VK_S)
if ok:
    u32.UnregisterHotKey(None, 0x9C42)
    print("Win+Shift+S : FREE  -> комбинация свободна, наш софт её слушает")
else:
    print("Win+Shift+S : BUSY  -> её всё ещё держит винда (нужен перезаход в систему)")
sys.exit(0 if ok else 1)
