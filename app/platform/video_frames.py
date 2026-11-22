"""JPEG frames cut from an uploaded MP4 by ffprobe and ffmpeg, which run under hard limits: the file is untrusted."""

import asyncio
import math
import os
import tempfile
from pathlib import Path

PROCESS_TIMEOUT_SECONDS = 30
# Caps the total memory of each process; -max_alloc below caps each single allocation.
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
FRAME_SHARES = (0.3, 0.5, 0.7)
FIRST_FRAME = "0.000"
MAX_FRAME_WIDTH = 1280
# Local MP4 and MOV files only: a playlist (HLS, DASH, concat) would make ffmpeg open other files or URLs.
# A tiny MP4 whose sample table declares millions of samples otherwise makes the demuxer allocate gigabytes.
INPUT_GUARD = ["-protocol_whitelist", "file", "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2", "-max_alloc", "268435456"]
PROBE_ENTRIES = "stream=codec_type:stream_disposition=attached_pic:format=duration"
# prlimit reports a command it could not execute with these exit codes.
NOT_EXECUTABLE = {126, 127}


class ToolUnavailable(Exception):
    """prlimit, ffprobe or ffmpeg cannot be started."""


class ProcessTimedOut(Exception):
    """The process ran longer than its limit and was killed."""


class UnreadableVideo(Exception):
    """The file has no readable video stream, no positive duration, or no frame."""


class ProcessRunner:
    """Runs a command without a shell, under prlimit, with only PATH in its environment, killed at its time limit."""

    def __init__(self, timeout_seconds: float = PROCESS_TIMEOUT_SECONDS):
        self._timeout = timeout_seconds

    async def run(self, args: list[str]) -> tuple[int, bytes]:
        """The exit code and standard output of the command."""
        command = ["prlimit", f"--data={MEMORY_LIMIT_BYTES}", *args]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", os.defpath)},
            )
        except OSError as e:
            raise ToolUnavailable(command[0]) from e
        try:
            async with asyncio.timeout(self._timeout):
                stdout, _ = await process.communicate()
        except TimeoutError as e:
            raise ProcessTimedOut(args[0]) from e
        finally:
            if process.returncode is None:  # timed out, or the request was cancelled
                process.kill()
                await process.wait()
        if process.returncode in NOT_EXECUTABLE:
            raise ToolUnavailable(args[0])
        return process.returncode, stdout


class VideoFrames:
    def __init__(self, runner: ProcessRunner, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg"):
        self._runner, self._ffprobe, self._ffmpeg = runner, ffprobe, ffmpeg

    async def extract(self, video: bytes) -> list[bytes]:
        """One JPEG at 30, 50 and 70 % of the duration, the first frame for a position that has none, each once.

        Raises UnreadableVideo or ToolUnavailable. The private directory holding the file is removed in every case.
        """
        with tempfile.TemporaryDirectory(prefix="artiko-video-") as workdir:  # created with mode 0700
            source = Path(workdir) / "input.mp4"
            await asyncio.to_thread(source.write_bytes, video)
            duration = await self._duration(source)
            frames: dict[str, bytes] = {}
            for share in FRAME_SHARES:
                await self._add_frame(source, f"{duration * share:.3f}", frames)
            if not frames:
                raise UnreadableVideo("no frame")
            return list(frames.values())

    async def _duration(self, source: Path) -> float:
        args = [self._ffprobe, "-v", "error", *INPUT_GUARD, "-select_streams", "v:0", "-show_entries", PROBE_ENTRIES]
        args += ["-of", "default=noprint_wrappers=1"]
        try:
            code, stdout = await self._runner.run([*args, str(source)])
        except ProcessTimedOut as e:
            raise UnreadableVideo("probe timed out") from e
        if code != 0:
            raise UnreadableVideo("probe failed")
        return _duration_of(stdout.decode(errors="replace"))

    async def _add_frame(self, source: Path, position: str, frames: dict[str, bytes]) -> None:
        """Adds the frame at `position`, or the first frame when there is none there, unless it is already in."""
        for candidate in (position, FIRST_FRAME):
            if candidate in frames:
                return
            frame = await self._frame(source, candidate)
            if frame:
                frames[candidate] = frame
                return

    async def _frame(self, source: Path, position: str) -> bytes | None:
        output = source.with_name(f"frame-{position}.jpg")
        args = [self._ffmpeg, "-v", "error", "-y", *INPUT_GUARD, "-ss", position, "-i", str(source)]
        args += ["-frames:v", "1", "-an", "-vf", f"scale='min({MAX_FRAME_WIDTH},iw)':-2", "-q:v", "3"]
        try:
            code, _ = await self._runner.run([*args, "-f", "image2", "-c:v", "mjpeg", str(output)])
        except ProcessTimedOut:
            return None
        # ffmpeg exits 0 without writing anything when the position is past the last frame.
        if code != 0 or not output.is_file():
            return None
        return await asyncio.to_thread(output.read_bytes) or None


def _duration_of(probe_output: str) -> float:
    entries = dict(line.split("=", 1) for line in probe_output.splitlines() if "=" in line)
    # The cover picture of an audio file is reported as a video stream too.
    if entries.get("codec_type") != "video" or entries.get("DISPOSITION:attached_pic") == "1":
        raise UnreadableVideo("no video stream")
    try:
        duration = float(entries.get("duration", ""))
    except ValueError as e:
        raise UnreadableVideo("no duration") from e
    if not math.isfinite(duration) or duration <= 0:
        raise UnreadableVideo("no positive duration")
    return duration
