"""Изолированный Filmcraft-адаптер. Только stdlib; никаких ключей и команд от LLM."""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import time
from pathlib import Path

MAX_BYTES = 19_000_000
MAX_TOTAL = 90.01


class FilmcraftError(Exception):
    pass


def media_path(directory, name):
    directory = Path(directory).resolve()
    if not isinstance(name, str) or not re.fullmatch(r"(?:source-[0-5]\.mp4|music\.bin|frame-[0-9]+\.jpg|audio-[0-9]+\.wav)", name):
        raise FilmcraftError("invalid_path")
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
        raise FilmcraftError("invalid_path")
    return path


def share_with_job_group(path, mode):
    info = path.stat()
    if os.name == "posix":
        gid = path.parent.stat().st_gid
        if info.st_gid != gid:
            os.chown(path, -1, gid)
    # RestrictSUIDSGID forbids chmod with special bits, even if inherited on mkdir.
    if stat.S_IMODE(info.st_mode) != mode:
        path.chmod(mode)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
    try:
        share_with_job_group(temporary, 0o660)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class FilmcraftEngine:
    def __init__(self, executable):
        self.executable = str(Path(executable).resolve())

    async def tool(self, executable, args, directory, *, timeout=120):
        # Подпроцесс не наследует ключи бота, в том числе при локальном запуске.
        env = {k: v for k, v in os.environ.items()
               if k in {"PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "FONTCONFIG_PATH"}}
        env.update(HOME=str(directory), XDG_CACHE_HOME=str(directory / "cache"),
                   XDG_CONFIG_HOME=str(directory / "config"), RAYON_NUM_THREADS="1",
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        process = await asyncio.create_subprocess_exec(
            str(executable), *map(str, args), cwd=directory, env=env,
            start_new_session=os.name != "nt", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            await process.communicate()
            raise
        if process.returncode or len(out) > 4_000_000 or len(err) > 4_000_000:
            raise FilmcraftError("engine_failed")
        return out

    async def ff(self, binary, args, directory, *, timeout=120):
        executable = shutil.which(binary)
        if not executable:
            raise FilmcraftError("ffmpeg_missing")
        prefix = ["-hide_banner", "-loglevel", "error"]
        if binary == "ffmpeg":
            prefix += ["-nostdin", "-y", "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1"]
        return await self.tool(executable, prefix + args, directory, timeout=timeout)

    @staticmethod
    def input(name):
        return ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi,mp3,wav,flac,ogg", "-i", name]

    async def probe(self, directory, name, *, music=False):
        path = media_path(directory, name)
        if path.stat().st_size > (5_000_000 if music else MAX_BYTES):
            raise FilmcraftError("file_too_large")
        raw = await self.ff("ffprobe", self.input(name) + [
            "-show_entries", "format=duration:stream=codec_type,width,height:stream_side_data=rotation:stream_tags=rotate", "-of", "json"], directory, timeout=30)
        try:
            data = json.loads(raw)
            duration = float(data["format"]["duration"])
            audio = any(s["codec_type"] == "audio" for s in data["streams"])
            if not math.isfinite(duration) or not .5 <= duration <= (600 if music else 90.2):
                raise ValueError
            if music:
                if not audio:
                    raise ValueError
                return {"duration": duration, "audio": True}
            video = next(s for s in data["streams"] if s["codec_type"] == "video")
            width, height = int(video["width"]), int(video["height"])
            rotation = float(next((s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s),
                                  video.get("tags", {}).get("rotate", 0)))
            if round(rotation) % 180 == 90:
                width, height = height, width
            if min(width, height) < 32 or max(width, height) > 4096 or width * height > 9_000_000:
                raise ValueError
            return {"duration": duration, "width": width, "height": height, "audio": audio}
        except (ValueError, KeyError, TypeError, StopIteration):
            raise FilmcraftError("video_limits") from None

    async def sources(self, directory, files):
        if not isinstance(files, list) or not 1 <= len(files) <= 6:
            raise FilmcraftError("video_limits")
        infos = []
        for index, source in enumerate(files):
            if not isinstance(source, dict) or source.get("name") != f"source-{index}.mp4":
                raise FilmcraftError("invalid_path")
            path = media_path(directory, source["name"])
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != source.get("sha256"):
                raise FilmcraftError("source_changed")
            infos.append(await self.probe(directory, source["name"]))
        if sum(s["duration"] for s in infos) > MAX_TOTAL:
            raise FilmcraftError("video_limits")
        return infos

    async def prepare(self, directory, files, speech):
        infos = await self.sources(directory, files)
        segments = []
        for index, info in enumerate(infos):
            count = max(1, math.ceil(info["duration"] / 10))
            for number in range(count):
                segments.append({"source": index, "start": round(info["duration"] * number / count, 3),
                                 "end": round(info["duration"] * (number + 1) / count, 3), "text": ""})
        while len(segments) > 12:
            index = min((i for i in range(len(segments) - 1) if segments[i]["source"] == segments[i + 1]["source"]),
                        key=lambda i: segments[i + 1]["end"] - segments[i]["start"])
            segments[index:index + 2] = [segments[index] | {"end": segments[index + 1]["end"]}]
        for index, segment in enumerate(segments):
            name = files[segment["source"]]["name"]
            start, end = segment["start"], segment["end"]
            frame = f"frame-{index}.jpg"
            await self.ff("ffmpeg", ["-ss", str((start + end) / 2), *self.input(name), "-frames:v", "1",
                                   "-vf", "scale=512:512:force_original_aspect_ratio=decrease", "-q:v", "4", frame], directory)
            segment["frame"] = frame
            if speech and infos[segment["source"]]["audio"]:
                audio = f"audio-{index}.wav"
                await self.ff("ffmpeg", ["-ss", str(start), *self.input(name), "-t", str(end - start),
                                       "-vn", "-ac", "1", "-ar", "16000", audio], directory)
                segment["audio"] = audio
        return {"segments": segments, "infos": infos}

    async def cli(self, directory, args, *, commands=None, save=False):
        base = ["--data-dir", str(directory / "engine-data"), "--compact"]
        if commands is not None:
            script = directory / "commands.jsonl"
            script.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in commands), encoding="utf-8")
            args = ["--project", str(directory / "edit.fcproj"), *( ["--save"] if save else []), "run", str(script)]
        raw = await self.tool(self.executable, base + args, directory, timeout=480)
        (directory / "engine-last-response.jsonl").write_bytes(raw)
        try:
            if commands is None:
                return json.loads(raw)
            rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
            if len(rows) != len(commands) or any(not row.get("ok") for row in rows):
                raise ValueError
            return rows
        except (ValueError, TypeError):
            raise FilmcraftError("filmcraft_command_failed") from None

    async def render(self, directory, files, segments, plan, music, profile="standard"):
        infos = await self.sources(directory, files)
        chosen = plan.get("clips") if isinstance(plan, dict) else None
        if (not isinstance(chosen, list) or not 1 <= len(chosen) <= 8
                or any(type(i) is not int or not 0 <= i < len(segments) for i in chosen)
                or len(set(chosen)) != len(chosen) or plan.get("format") not in {"portrait", "landscape", "square"}
                or plan.get("sound") not in {"original", "mute", "mix"}
                or plan.get("transition") not in {"cut", "fade"} or type(plan.get("subtitles")) is not bool
                or not isinstance(plan.get("title"), str) or not 1 <= len(plan["title"]) <= 80):
            raise FilmcraftError("invalid_plan")
        total = 0
        for index in chosen:
            s = segments[index]
            source, start, end = s.get("source"), s.get("start"), s.get("end")
            if (type(source) is not int or not 0 <= source < len(infos)
                    or type(start) not in {int, float} or type(end) not in {int, float}
                    or not 0 <= start < end <= infos[source]["duration"] + .01):
                raise FilmcraftError("invalid_plan")
            total += end - start
        if not .5 <= total <= 60.01:
            raise FilmcraftError("invalid_plan")
        if plan["sound"] == "mix" and not music:
            raise FilmcraftError("invalid_plan")
        project = directory / "edit.fcproj"
        project.unlink(missing_ok=True)
        imported = await self.cli(directory, ["--save-as", str(project), "import", *[str(media_path(directory, f["name"])) for f in files]])
        items = imported.get("items", [])
        if imported.get("errors") or len(items) != len(files):
            raise FilmcraftError("filmcraft_import_failed")
        items = [item["id"] if isinstance(item, dict) else item for item in items]
        sizes = {
            "standard": {"portrait": (720, 1280), "landscape": (1280, 720), "square": (720, 720)},
            "light": {"portrait": (540, 960), "landscape": (960, 540), "square": (540, 540)},
        }
        if profile not in sizes:
            raise FilmcraftError("invalid_profile")
        width, height = sizes[profile][plan["format"]]
        commands = [{"id": "file.newSequence", "params": {"name": "Образ", "width": width, "height": height,
                     "fps": 30, "video": 2, "audio": 2, "sampleRate": 48000}}]
        cursor = 0
        for index in chosen:
            s = segments[index]
            commands.append({"id": "timeline.place", "params": {"item": items[s["source"]], "track": "V1",
                             "audioTrack": "A1", "seconds": cursor, "sourceIn": {"seconds": s["start"]},
                             "duration": {"seconds": s["end"] - s["start"]}, "matchSequence": False,
                             "video": True, "audio": plan["sound"] != "mute" and infos[s["source"]]["audio"]}})
            cursor += s["end"] - s["start"]
        placed = await self.cli(directory, [], commands=commands, save=True)
        placements = [row["result"]["clips"][0] for row in placed if row["id"] == "timeline.place"]
        commands, cursor = [], 0
        for clip, index in zip(placements, chosen, strict=True):
            s = segments[index]
            info = infos[s["source"]]
            scale = 100 * min(width / info["width"], height / info["height"])
            commands.append({"id": "effects.setParam", "params": {
                "clip": clip, "effect": "motion", "param": "scale", "value": scale}})
            duration = s["end"] - s["start"]
            if plan["transition"] == "fade":
                # Отдельные ключи opacity дают затемнение, а не crossfade соседних роликов.
                edge = min(.12, duration / 4)
                for seconds, opacity in [(cursor, 0), (cursor + edge, 100),
                                         (cursor + duration - edge, 100), (cursor + duration, 0)]:
                    commands.append({"id": "effects.setParam", "params": {"clip": clip, "effect": "opacity",
                                     "param": "opacity", "value": opacity, "time": round(seconds * 254016000000),
                                     "keyframe": True}})
            cursor += duration
        title = re.sub(r"[\x00-\x1f]", " ", plan["title"]).strip()
        commands += [
            {"id": "graphics.newText", "params": {"text": title, "font": "DejaVu Sans", "size": 32,
             "position": [45, 80], "box": [width - 90, 180], "track": 1, "time": 0, "seconds": min(total, 2.5)}},
            {"id": "graphics.setCharStyle", "params": {"style": {"color": "#FFFFFF", "bold": True,
             "stroke": True, "strokeColor": "#172923", "strokeWidth": 2}}},
        ]
        subtitles = []
        cursor = 0
        for index in chosen:
            s = segments[index]
            duration = s["end"] - s["start"]
            text = re.sub(r"[<>\x00-\x1f]", " ", str(s.get("text", "")))[:6000].strip()
            if plan["subtitles"] and text:
                words = text.split()
                lines = [" ".join(words[i:i + 12]) for i in range(0, len(words), 12)]
                for i, line in enumerate(lines):
                    subtitles.append(f"{len(subtitles) + 1}\n{self.srt_time(cursor + duration * i / len(lines))} --> "
                                     f"{self.srt_time(cursor + duration * (i + 1) / len(lines))}\n{line}\n")
            cursor += duration
        if subtitles:
            captions = directory / "captions.srt"
            captions.write_text("\n".join(subtitles), encoding="utf-8")
            commands.append({"id": "captions.import", "params": {"path": str(captions), "language": "ru"}})
        raw = directory / "filmcraft.mp4"
        raw.unlink(missing_ok=True)
        commands.append({"id": "file.exportMedia", "params": {"path": str(raw), "format": "h264", "width": width,
                         "height": height, "fps": 30, "audio": plan["sound"] != "mute", "bitrateKbps": 1800,
                         "hardwareEncoding": "off", "burnCaptions": bool(subtitles), "range": "custom",
                         "startSeconds": 0, "endSeconds": total, "wait": True}})
        rows = await self.cli(directory, [], commands=commands, save=True)
        if "offlineMedia" in json.dumps(rows) and self._offline(rows):
            raise FilmcraftError("offline_media")
        # Filmcraft выполняет весь видеомонтаж. FFmpeg только добавляет свою музыку/faststart.
        args = self.input("filmcraft.mp4")
        if plan["sound"] == "mix":
            await self.probe(directory, "music.bin", music=True)
            sound = await self.ff("ffprobe", self.input("filmcraft.mp4") + ["-show_entries", "stream=codec_type", "-of", "json"], directory)
            has_audio = any(s["codec_type"] == "audio" for s in json.loads(sound)["streams"])
            args += ["-stream_loop", "-1", *self.input("music.bin")]
            if has_audio:
                args += ["-filter_complex", "[1:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=longest:normalize=0[a]",
                         "-map", "0:v:0", "-map", "[a]"]
            else:
                args += ["-map", "0:v:0", "-map", "1:a:0", "-af", "volume=0.12"]
            args += ["-c:a", "aac", "-b:a", "96k"]
        else:
            args += ["-c:a", "copy"]
        args += ["-t", str(total), "-c:v", "copy", "-movflags", "+faststart", "rendering.mp4"]
        await self.ff("ffmpeg", args, directory)
        output = directory / "rendering.mp4"
        raw_probe = await self.ff("ffprobe", self.input(output.name) + [
            "-show_entries", "format=duration:stream=codec_type,width,height,codec_name", "-of", "json"], directory)
        result = json.loads(raw_probe)
        video = next(s for s in result["streams"] if s["codec_type"] == "video")
        if (video.get("codec_name") != "h264" or (video["width"], video["height"]) != (width, height)
                or abs(float(result["format"]["duration"]) - total) > .3 or not 1024 < output.stat().st_size < 49_000_000):
            raise FilmcraftError("invalid_output")
        await self.ff("ffmpeg", [*self.input(output.name), "-f", "null", "-"], directory, timeout=120)
        output.replace(directory / "result.mp4")
        return {"output": "result.mp4", "duration": round(total, 3), "width": width, "height": height,
                "bytes": (directory / "result.mp4").stat().st_size, "renderer": "Filmcraft 0.20.1", "verified": True}

    @staticmethod
    def _offline(value):
        if isinstance(value, dict):
            return bool(value.get("offlineMedia")) or any(FilmcraftEngine._offline(v) for v in value.values())
        return isinstance(value, list) and any(FilmcraftEngine._offline(v) for v in value)

    @staticmethod
    def srt_time(seconds):
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def resources_available(spool):
    if shutil.disk_usage(spool).free < 1_000_000_000:
        return False
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        match = re.search(r"MemAvailable:\s+(\d+)", meminfo.read_text())
        if not match or int(match[1]) < 650_000:
            return False
    return True


async def worker(spool, executable):
    spool = Path(spool).resolve()
    engine = FilmcraftEngine(executable)
    os.umask(0o007)
    spool.mkdir(mode=0o770, parents=True, exist_ok=True)
    # Рестарт локального процесса не повторяет ни команду, ни тем более платный API.
    for running in spool.glob("*/request-*.working"):
        atomic_json(running.with_suffix(".result"), {"ok": False, "error": "worker_interrupted"})
        running.unlink()
    while True:
        atomic_json(spool / "worker-health.json", {"time": time.time(), "version": "0.20.1"})
        requests = sorted(spool.glob("*/request-*.json"))
        for request in requests:
            if (not re.fullmatch(r"[a-f0-9]{32}", request.parent.name)
                    or not re.fullmatch(r"request-[a-f0-9]{32}\.json", request.name)
                    or request.parent.is_symlink() or request.is_symlink()
                    or request.resolve().parent.parent != spool):
                continue
            working = request.with_suffix(".working")
            request.replace(working)
            began = time.monotonic()
            operation = None
            try:
                if working.stat().st_size > 1_000_000:
                    raise FilmcraftError("invalid_request")
                value = json.loads(working.read_text())
                operation = value["operation"]
                if abs(time.time() - value["created"]) > 90:
                    raise FilmcraftError("worker_interrupted")
                if not resources_available(spool):
                    raise FilmcraftError("server_busy")
                async def execute():
                    if value["operation"] == "probe":
                        result = await engine.probe(working.parent, value["name"], music=value.get("music", False))
                    elif value["operation"] == "prepare":
                        result = await engine.prepare(working.parent, value["files"], value["speech"])
                    elif value["operation"] == "render":
                        result = await engine.render(working.parent, value["files"], value["segments"], value["plan"],
                                                     value["music"], value.get("profile", "standard"))
                    else:
                        raise FilmcraftError("invalid_request")
                    return result
                task = asyncio.create_task(execute())
                try:
                    async with asyncio.timeout(600):
                        while not task.done():
                            if working.with_suffix(".cancel").exists():
                                raise FilmcraftError("worker_canceled")
                            await asyncio.sleep(.3)
                        result = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    working.with_suffix(".cancel").unlink(missing_ok=True)
                response = {"ok": True, "result": result, "seconds": round(time.monotonic() - began, 3)}
            except Exception as error:
                code = ("render_timeout" if isinstance(error, TimeoutError) and operation == "render"
                        else str(error) if isinstance(error, FilmcraftError) else "worker_failed")
                response = {"ok": False, "error": code}
            atomic_json(working.with_suffix(".result"), response)
            working.unlink(missing_ok=True)
        await asyncio.sleep(.5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", required=True)
    parser.add_argument("--engine", required=True)
    args = parser.parse_args()
    asyncio.run(worker(args.spool, args.engine))
