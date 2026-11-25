"""Download OCRia-nano-1 model files into a local staging dir (retry-friendly)."""
import json
import os
import sys
import time
import urllib.request

BASE = "https://huggingface.co/llamaindex/OCRia-nano-1/resolve/main/"
FILES = [
    "config.json",
    "LICENSE",
    "det.onnx",
    "det.meta.json",
    "router.onnx",
    "rec_latin.onnx",
    "rec_latin.charset.json",
    "rec_cjk.onnx",
    "rec_cjk.charset.json",
    "rec_rtl.onnx",
    "rec_rtl.charset.json",
    "rec_rtl.meta.json",
    "rec_world.onnx",
    "rec_world.charset.json",
    "tablenet.onnx",
    "tablenet.bin",
]

out = sys.argv[1]
os.makedirs(out, exist_ok=True)


def fetch(name: str) -> None:
    dst = os.path.join(out, name)
    tmp = dst + ".part"
    for attempt in range(1, 9):
        try:
            req = urllib.request.Request(
                BASE + name, headers={"User-Agent": "model-fetch/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            os.replace(tmp, dst)
            print(f"OK   {name} ({total} bytes)")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"retry {attempt} {name}: {exc}")
            time.sleep(min(2 * attempt, 10))
    raise SystemExit(f"failed: {name}")


for f in FILES:
    p = os.path.join(out, f)
    if os.path.exists(p) and os.path.getsize(p) > 0 and not p.endswith(".part"):
        print(f"skip {f} (already present)")
        continue
    fetch(f)
print("done")
