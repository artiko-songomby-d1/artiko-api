"""JPEG frames cut from an uploaded MP4 by ffprobe and ffmpeg, which run under hard limits: the file is untrusted."""

import asyncio
import math
import os
import tempfile
from pathlib import Path

PROCESS_TIMEOUT_SECONDS = 30
FRAME_SHARES = (0.3, 0.5, 0.7)
FIRST_FRAME = "0.000"
MAX_FRAME_WIDTH = 1280
# Local MP4 and MOV files only: a playlist (HLS, DASH, concat) would make ffmpeg open other files or URLs.
# A tiny MP4 whose sample table declares millions of samples otherwise makes the demuxer allocate gigabytes.
INPUT_GUARD = ["-protocol_whitelist", "file", "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2", "-max_alloc", "268435456"]
# One thread each keeps memory low and steady under the prlimit cap.
SINGLE_THREAD_INPUT = ["-threads", "1"]
SINGLE_THREAD_OUTPUT = ["-filter_threads", "1", "-threads", "1"]
# A BLAS library linked into ffmpeg starts one worker per CPU at start-up, each reserving about 128 MiB: under the
# cap that reservation never succeeds and the process hangs. One worker fits.
SINGLE_THREADED_LIBRARIES = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
PROBE_ENTRIES = "stream=codec_type:stream_disposition=attached_pic:format=duration"
# prlimit reports a command it could not execute with these exit codes.
NOT_EXECUTABLE = {126, 127}


class ToolUnavailable(Exception):
    """prlimit, ffprobe or ffmpeg cannot be started."""


class ProcessTimedOut(Exception):
    """The process ran longer than its limit and was killed."""


class UnreadableVideo(Exception):
    """ffprobe rejects the file, or finds no video stream or no positive duration in it."""


class ProcessRunner:
    """Runs a command without a shell, under prlimit, killed at its time limit, with only PATH and the
    single-thread switches in its environment.

    `memory_limit_mb` caps the total memory of each process; -max_alloc below caps each single allocation.
    """

    def __init__(self, memory_limit_mb: int, timeout_seconds: float = PROCESS_TIMEOUT_SECONDS):
        self._memory_limit_bytes, self._timeout = memory_limit_mb * 1024 * 1024, timeout_seconds

    async def run(self, args: list[str]) -> tuple[int, bytes]:
        """The exit code and standard output of the command."""
        command = ["prlimit", f"--data={self._memory_limit_bytes}", *args]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", os.defpath), **SINGLE_THREADED_LIBRARIES},
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

        Empty when ffmpeg gives no frame at all, which a memory cap hit looks like as much as a broken file does.
        Raises UnreadableVideo when ffprobe finds the file at fault; ToolUnavailable or ProcessTimedOut when a tool is.
        The private directory holding the file is removed in every case.
        """
        with tempfile.TemporaryDirectory(prefix="artiko-video-") as workdir:  # created with mode 0700
            source = Path(workdir) / "input.mp4"
            await asyncio.to_thread(source.write_bytes, video)
            duration = await self._duration(source)
            frames: dict[str, bytes] = {}
            for share in FRAME_SHARES:
                await self._add_frame(source, f"{duration * share:.3f}", frames)
            return list(frames.values())

    async def _duration(self, source: Path) -> float:
        args = [self._ffprobe, "-v", "error", *INPUT_GUARD, *SINGLE_THREAD_INPUT]
        args += ["-select_streams", "v:0", "-show_entries", PROBE_ENTRIES]
        args += ["-of", "default=noprint_wrappers=1"]
        code, stdout = await self._runner.run([*args, str(source)])
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
        args = [self._ffmpeg, "-v", "error", "-y", *INPUT_GUARD, *SINGLE_THREAD_INPUT]
        args += ["-ss", position, "-i", str(source), "-frames:v", "1", "-an"]
        args += ["-vf", f"scale='min({MAX_FRAME_WIDTH},iw)':-2", "-q:v", "3", *SINGLE_THREAD_OUTPUT]
        code, _ = await self._runner.run([*args, "-f", "image2", "-c:v", "mjpeg", str(output)])
        # Past the last frame, ffmpeg 5 exits 0 without writing anything and ffmpeg 7 exits with an error.
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
