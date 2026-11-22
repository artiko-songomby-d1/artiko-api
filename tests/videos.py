"""Tiny real videos built with the local ffmpeg."""

import subprocess
import tempfile
from pathlib import Path

from app.platform.config import get_settings
from app.platform.video_frames import PROCESS_TIMEOUT_SECONDS, ProcessRunner


def ffmpeg_runner(timeout_seconds: float = PROCESS_TIMEOUT_SECONDS) -> ProcessRunner:
    """The runner the app builds, with the configured memory cap."""
    return ProcessRunner(get_settings().ai_detection_ffmpeg_memory_mb, timeout_seconds)


def mp4(seconds: float = 2, size: str = "1920x1080", rate: int = 10, source: str = "testsrc") -> bytes:
    """An H.264 MP4 of a lavfi test source (`testsrc`, or `color=c=<name>` for a plain colour)."""
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir) / "v.mp4"
        options = f"size={size}:rate={rate}:duration={seconds}"
        lavfi = f"{source}:{options}" if "=" in source else f"{source}={options}"
        args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", lavfi, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(args, check=True, timeout=60)  # noqa: S603 S607 fixed arguments, ffmpeg found on PATH
        return out.read_bytes()


def audio_only_mp4() -> bytes:
    """An MP4 holding one second of sound and no video stream."""
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir) / "a.mp4"
        args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(out)]
        subprocess.run(args, check=True, timeout=60)  # noqa: S603 S607 fixed arguments, ffmpeg found on PATH
        return out.read_bytes()
