"""Download the CLIP ViT-B/32 image encoder (ONNX, quantized, ~88 MB) into MODELS_DIR.

Resumes from the partial file after a dropped connection (HTTP Range), retrying until done.
"""

import time
from pathlib import Path

import httpx

from app.platform.config import get_settings

URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx"
ATTEMPTS = 20

target = Path(get_settings().models_dir) / "clip-vision.onnx"
target.parent.mkdir(parents=True, exist_ok=True)
tmp = target.with_suffix(".part")

for attempt in range(1, ATTEMPTS + 1):
    if target.exists():
        break
    have = tmp.stat().st_size if tmp.exists() else 0
    try:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with httpx.stream("GET", URL, headers=headers, follow_redirects=True, timeout=60) as r:
            if r.status_code == 416:  # nothing left to fetch: the partial file is complete
                tmp.rename(target)
                break
            r.raise_for_status()
            # 206 = the server honoured the range; 200 = it restarts from byte 0.
            with tmp.open("ab" if r.status_code == 206 else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.rename(target)
    except httpx.HTTPError as e:
        print(f"attempt {attempt}: {e!r}, resuming at {tmp.stat().st_size if tmp.exists() else 0} bytes")
        time.sleep(min(2 * attempt, 15))

if not target.exists():
    raise SystemExit(f"download failed after {ATTEMPTS} attempts; rerun to resume")
print(f"{target} ready ({target.stat().st_size // 2**20} MB)")
