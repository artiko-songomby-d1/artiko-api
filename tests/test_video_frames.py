import asyncio
import io
import os
import stat
import time
from pathlib import Path

import pytest
from PIL import Image

from app.platform.video_frames import INPUT_GUARD, ProcessRunner, ProcessTimedOut, UnreadableVideo, VideoFrames
from tests.videos import mp4


class SpyRunner(ProcessRunner):
    """The real runner, recording each command and the mode of the directory that holds its input."""

    def __init__(self, timeout_seconds: float = 30):
        super().__init__(timeout_seconds)
        self.commands: list[list[str]] = []
        self.input_dirs: list[tuple[Path, int]] = []

    async def run(self, args: list[str]) -> tuple[int, bytes]:
        self.commands.append(args)
        source = Path(args[args.index("-i") + 1] if "-i" in args else args[-1])
        self.input_dirs.append((source, stat.S_IMODE(source.parent.stat().st_mode)))
        return await super().run(args)


def contains(args: list[str], part: list[str]) -> bool:
    return any(args[i : i + len(part)] == part for i in range(len(args)))


def test_ffmpeg_arguments_environment_and_cleanup():
    runner = SpyRunner()
    frames = asyncio.run(VideoFrames(runner).extract(mp4()))

    assert len(frames) == 3 and all(Image.open(io.BytesIO(f)).format == "JPEG" for f in frames)
    assert [c[0] for c in runner.commands] == ["ffprobe", "ffmpeg", "ffmpeg", "ffmpeg"]
    assert all(contains(c, INPUT_GUARD) for c in runner.commands)
    assert contains(INPUT_GUARD, ["-protocol_whitelist", "file"])
    assert contains(INPUT_GUARD, ["-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2"])
    assert contains(INPUT_GUARD, ["-max_alloc", "268435456"])
    assert {source.name for source, _ in runner.input_dirs} == {"input.mp4"}
    assert {mode for _, mode in runner.input_dirs} == {0o700}
    assert not any(source.parent.exists() for source, _ in runner.input_dirs)

    garbage = SpyRunner()
    with pytest.raises(UnreadableVideo):
        asyncio.run(VideoFrames(garbage).extract(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64))
    assert not garbage.input_dirs[0][0].parent.exists()

    code, environment = asyncio.run(ProcessRunner().run(["env"]))
    assert code == 0 and environment.decode().splitlines() == [f"PATH={os.environ['PATH']}"]
    code, data_limit_kb = asyncio.run(ProcessRunner().run(["sh", "-c", "ulimit -d"]))
    assert data_limit_kb.decode().strip() == str(1024 * 1024)


def test_ffmpeg_timeout_kills_the_process(tmp_path):
    pid_file = tmp_path / "pid"
    slow = tmp_path / "slow-tool"
    slow.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
    slow.chmod(0o700)

    started = time.monotonic()
    with pytest.raises(ProcessTimedOut):
        asyncio.run(ProcessRunner(timeout_seconds=0.5).run([str(slow)]))
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_short_video_falls_back_to_the_first_frame():
    runner = SpyRunner()
    one_frame = mp4(seconds=1, size="64x48", rate=1, source="color=c=red")
    frames = asyncio.run(VideoFrames(runner).extract(one_frame))

    assert len(frames) == 1
    positions = [c[c.index("-ss") + 1] for c in runner.commands if "-ss" in c]
    assert positions == ["0.300", "0.000", "0.500", "0.700"]
