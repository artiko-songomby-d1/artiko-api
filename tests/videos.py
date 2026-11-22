"""Tiny real videos built with the local ffmpeg."""

import asyncio
import subprocess
import tempfile
from pathlib import Path

from app.platform.config import get_settings
from app.platform.video_frames import PROCESS_TIMEOUT_SECONDS, ProcessRunner

PROBED_VIDEO = b"codec_type=video\nDISPOSITION:attached_pic=0\nduration=2.000000\n"


def ffmpeg_runner(timeout_seconds: float = PROCESS_TIMEOUT_SECONDS) -> ProcessRunner:
    """The runner the app builds, with the configured memory cap."""
    return ProcessRunner(get_settings().ai_detection_ffmpeg_memory_mb, timeout_seconds)


def mp4(
    seconds: float = 2, size: str = "1920x1080", rate: int = 10, source: str = "testsrc", noise: int = 0
) -> bytes:
    """An H.264 MP4 of a lavfi test source (`testsrc`, or `color=c=<name>` for a plain colour), with `noise` (0 to
    100) added to every frame, which makes the decoder work hardest."""
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir) / "v.mp4"
        options = f"size={size}:rate={rate}:duration={seconds}"
        lavfi = f"{source}:{options}" if "=" in source else f"{source}={options}"
        # A coarser quality keeps a noisy video under the 20 MB upload limit.
        quality = ["-crf", "30"] if noise else []
        if noise:
            lavfi += f",noise=alls={noise}:allf=t+u"
        args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", lavfi, "-c:v", "libx264", *quality, "-pix_fmt", "yuv420p"]
        args.append(str(out))
        subprocess.run(args, check=True, timeout=60)  # noqa: S603 S607 fixed arguments, ffmpeg found on PATH
        return out.read_bytes()


def audio_only_mp4() -> bytes:
    """An MP4 holding one second of sound and no video stream."""
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir) / "a.mp4"
        args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(out)]
        subprocess.run(args, check=True, timeout=60)  # noqa: S603 S607 fixed arguments, ffmpeg found on PATH
        return out.read_bytes()


class FrameStarvedRunner(ProcessRunner):
    """ffprobe reads a 2 s video, ffmpeg exits with an error (as when it hits the memory cap) except at `framed`."""

    def __init__(self, framed: frozenset[str] = frozenset()):
        super().__init__(memory_limit_mb=1024)
        self.framed = framed

    async def run(self, args: list[str]) -> tuple[int, bytes]:
        if args[0] == "ffprobe":
            return 0, PROBED_VIDEO
        if args[args.index("-ss") + 1] not in self.framed:
            return 1, b""
        await asyncio.to_thread(Path(args[-1]).write_bytes, b"\xff\xd8 a frame " + args[-1].encode())
        return 0, b""
