import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from image_studio.video_montage import OpenAIPlanner, VideoEngine, VideoError, checked_plan
from image_studio.video_store import VideoRepository


def plan(**overrides):
    return {"title": "Наш ролик", "format": "portrait", "transition": "fade", "sound": "original",
            "subtitles": True, "clips": [0, 1], **overrides}


def test_owner_bound_claim_recovery_and_usage_survive_restart(tmp_path):
    repo = VideoRepository(tmp_path)
    job = repo.create(7)
    repo.save(7, job["id"], state="ready", prompt="test")
    assert not repo.get(8, job["id"])
    assert repo.claim(7, job["id"], "ready", "analyzing")
    assert not repo.claim(7, job["id"], "ready", "analyzing")
    call = repo.call_start(7, job["id"], "plan", "gpt-6.1-sol")
    repo.call_finish(call, "completed", usage={"input_tokens": 42, "output_tokens": 10,
                                             "output_tokens_details": {"reasoning_tokens": 6}}, request_id="req-test")
    repo.call_start(7, job["id"], "transcribe", "gpt-transcribe", seconds=3)
    restored = VideoRepository(tmp_path)
    restored.recover()
    assert restored.get(7, job["id"])["state"] == "review"
    calls = restored.calls(7, job["id"])
    assert calls[0]["usage"]["input_tokens"] == 42 and calls[1]["status"] == "unknown"
    assert restored.calls(8, job["id"]) == []


def test_retention_removes_content_but_keeps_usage(tmp_path, monkeypatch):
    repo = VideoRepository(tmp_path)
    job = repo.create(7)
    repo.save(7, job["id"], prompt="private", segments=[{"text": "private"}])
    (repo.directory(job["id"]) / "source.mp4").write_bytes(b"private-video")
    call = repo.call_start(7, job["id"], "plan", "gpt-6.1-sol")
    repo.call_finish(call, "completed", usage={"input_tokens": 3, "output_tokens": 2})
    monkeypatch.setattr("image_studio.video_store.time.time", lambda: job["created"] + 86461)
    repo.cleanup()
    assert repo.get(7, job["id"])["data"] == {} and not repo.directory(job["id"]).exists()
    assert repo.calls(7, job["id"])[0]["usage"] == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.parametrize("bad", [plan(clips=[-1]), plan(clips=[0, 0]), plan(clips=[10]),
                                 plan(clips=[True]), plan(format="file:/etc/passwd"),
                                 plan(sound="mix"), plan(title="x" * 81)])
def test_plan_rejects_untrusted_ranges_and_commands(bad):
    with pytest.raises(VideoError):
        checked_plan(bad, [{"start": 0, "end": 4}, {"start": 4, "end": 8}], music=False)


async def test_usage_persisted_even_if_plan_is_invalid_and_no_automatic_retry(tmp_path):
    repo = VideoRepository(tmp_path)
    job = repo.create(7)
    repo.save(7, job["id"], state="analyzing", prompt="сделай клип")
    response = SimpleNamespace(status="completed", output_text=json.dumps(plan(clips=[99])),
                               usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 321, "output_tokens": 123}),
                               _request_id="req-accounted")
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)),
                             close=AsyncMock())
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"test")
    planner = OpenAIPlanner("offline", repo, client=client)
    with pytest.raises(VideoError):
        await planner.plan(7, job["id"], [{"start": 0, "end": 3, "frame": str(frame), "text": ""}], False)
    assert client.responses.create.await_count == 1
    assert repo.calls(7, job["id"])[0]["usage"]["input_tokens"] == 321
    request = client.responses.create.call_args.kwargs
    assert request["model"] == "gpt-6.1-sol" and request["store"] is False
    assert request["text"]["format"]["strict"] is True and "tools" not in request


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg system dependency")
@pytest.mark.parametrize("sound,audio", [("original", True), ("mix", True), ("mute", False)])
async def test_real_local_render_with_audio_subtitles_and_injection_like_title(tmp_path, sound, audio):
    engine = VideoEngine()
    await engine.tool("ffmpeg", ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
                                  "-f", "lavfi", "-i", "sine=frequency=240:sample_rate=16000",
                                  "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                  "-c:a", "aac", "source.mp4"], tmp_path, timeout=30)
    info = await engine.probe(tmp_path / "source.mp4")
    prepared = await engine.prepare(tmp_path, info, speech=audio)
    assert prepared and all(Path(s["frame"]).is_file() for s in prepared)
    if sound == "mix":
        await engine.tool("ffmpeg", [*engine.input("source.mp4"), "-vn", "-f", "wav", "music.bin"], tmp_path)
    segments = [{"start": 0, "end": 1.2, "text": "Привет, это тест."},
                {"start": 1.5, "end": 3, "text": "Монтаж из исходных кадров."}]
    result = await engine.render(tmp_path, info | {"audio": audio}, segments, plan(title="Тест {\\p1} <>&;", sound=sound))
    output = await engine.probe(result)
    assert 2.4 < output["duration"] < 3.1
    assert (output["width"], output["height"]) == (720, 1280) and output["audio"]
    assert result.stat().st_size < 20_000_000
    assert not any(p.name.startswith("<") for p in tmp_path.iterdir())
    assert await asyncio.wait_for(engine.probe(result), timeout=20)
