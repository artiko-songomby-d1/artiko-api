import asyncio
import io
import os
import stat
import time
from pathlib import Path

import pytest
from PIL import Image

from app.platform.config import Settings
from app.platform.video_frames import (
    INPUT_GUARD,
    SINGLE_THREAD_INPUT,
    SINGLE_THREAD_OUTPUT,
    ProcessRunner,
    ProcessTimedOut,
    UnreadableVideo,
    VideoFrames,
)
from tests.videos import mp4


class SpyRunner(ProcessRunner):
    """The real runner, recording each command and the mode of the directory that holds its input."""

    def __init__(self):
        super().__init__(memory_limit_mb=1024)
        self.commands: list[list[str]] = []
        self.input_dirs: list[tuple[Path, int]] = []

    async def run(self, args: list[str]) -> tuple[int, bytes]:
        self.commands.append(args)
        source = Path(args[args.index("-i") + 1] if "-i" in args else args[-1])
        self.input_dirs.append((source, stat.S_IMODE(source.parent.stat().st_mode)))
        return await super().run(args)


def contains(args: list[str], part: list[str]) -> bool:
    return any(args[i : i + len(part)] == part for i in range(len(args)))


def test_ffmpeg_arguments_environment_and_cleanup(monkeypatch):
    monkeypatch.setenv("SIGHTENGINE_SECRET", "test-secret-9f3a")
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    runner = SpyRunner()
    frames = asyncio.run(VideoFrames(runner).extract(mp4()))

    assert len(frames) == 3 and all(Image.open(io.BytesIO(f)).format == "JPEG" for f in frames)
    assert [c[0] for c in runner.commands] == ["ffprobe", "ffmpeg", "ffmpeg", "ffmpeg"]
    assert all(contains(c, INPUT_GUARD) and contains(c, SINGLE_THREAD_INPUT) for c in runner.commands)
    assert all(c.index("-threads") < c.index("-i") for c in runner.commands if c[0] == "ffmpeg")
    assert all(contains(c, SINGLE_THREAD_OUTPUT) for c in runner.commands if c[0] == "ffmpeg")
    assert SINGLE_THREAD_INPUT == ["-threads", "1"]
    assert SINGLE_THREAD_OUTPUT == ["-filter_threads", "1", "-threads", "1"]
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

    code, environment = asyncio.run(ProcessRunner(1024).run(["env"]))
    assert code == 0 and sorted(environment.decode().splitlines()) == [
        "OMP_NUM_THREADS=1",
        "OPENBLAS_NUM_THREADS=1",
        f"PATH={os.environ['PATH']}",
    ]
    code, data_limit_kb = asyncio.run(ProcessRunner(memory_limit_mb=300).run(["sh", "-c", "ulimit -d"]))
    assert data_limit_kb.decode().strip() == str(300 * 1024)


def test_ffmpeg_timeout_kills_the_process(tmp_path):
    pid_file = tmp_path / "pid"
    slow = tmp_path / "slow-tool"
    slow.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
    slow.chmod(0o700)

    started = time.monotonic()
    with pytest.raises(ProcessTimedOut):
        asyncio.run(ProcessRunner(1024, timeout_seconds=0.5).run([str(slow)]))
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


@pytest.mark.parametrize(
    "video",
    [mp4(seconds=2, size="1920x1080"), mp4(seconds=2, size="3840x2160", rate=5, source="testsrc2", noise=30)],
    ids=["1080p", "noisy-4k"],
)
def test_three_frames_fit_under_the_default_memory_cap(video):
    default_mb = Settings.model_fields["ai_detection_ffmpeg_memory_mb"].default
    assert default_mb == 272 and len(video) <= 20 * 1024 * 1024
    frames = asyncio.run(VideoFrames(ProcessRunner(default_mb)).extract(video))
    assert len(frames) == 3 and all(Image.open(io.BytesIO(f)).width == 1280 for f in frames)
