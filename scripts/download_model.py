"""Download the CLIP ViT-B/32 image encoder (ONNX, quantized, ~88 MB) into MODELS_DIR."""

from pathlib import Path

import httpx

from app.platform.config import get_settings

URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx"

target = Path(get_settings().models_dir) / "clip-vision.onnx"
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    print(f"{target} already there")
else:
    tmp = target.with_suffix(".part")
    with httpx.stream("GET", URL, follow_redirects=True, timeout=None) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(target)
    print(f"saved {target} ({target.stat().st_size // 2**20} MB)")
