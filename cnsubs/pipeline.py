"""生成流程：解析链接 -> 用现成字幕或 Whisper 转写 -> 输出 SRT。"""

import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from . import config, languages, meta, separate, srt
from .translate import translate_cues

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?\S*?v=|shorts/|live/|embed/|playlist\?\S*?list=)|youtu\.be/)"
    r"[\w\-]+(?:[?&]\S*)?"
)

NO_WINDOW = {"creationflags": 0x08000000} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}

# 音乐模式下的分段上限。整首歌通常也就三五分钟，比这个还长的多半是合集，
# 切短一点让时间轴不至于一路偏下去。
MUSIC_CHUNK_SECONDS = 300


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# 运行环境
# ---------------------------------------------------------------------------

def missing_dependencies() -> list[str]:
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:110] or "subtitles"


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **NO_WINDOW,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# 解析粘贴进来的链接
# ---------------------------------------------------------------------------

def expand(url: str) -> list[dict]:
    """返回 [{url, title}]：单个视频就一条，播放列表则展开成每个视频。"""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "ignoreerrors": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("yt-dlp could not read that link")

    if info.get("_type") == "playlist":
        videos = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            link = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if link and not str(link).startswith("http"):
                link = f"https://www.youtube.com/watch?v={link}"
            if link:
                videos.append({"url": link, "title": entry.get("title") or link})
        return videos
    return [{"url": info.get("webpage_url") or url, "title": info.get("title") or url}]


def video_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True,
            "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


# ---------------------------------------------------------------------------
# 路线一：直接用上传者自己的字幕
# ---------------------------------------------------------------------------

def fetch_existing_subs(url: str, info: dict, work: Path, cfg: dict, log) -> list[dict] | None:
    """视频自带字幕就直接下载。免费、即时，而且和原话完全对得上。"""
    codes = cfg.get("sub_codes") or languages.get(cfg.get("language", "zh"))["sub_codes"]
    available = set(info.get("subtitles") or {})
    auto = set(info.get("automatic_captions") or {})
    manual_hit = [c for c in codes if c in available]
    auto_hit = [c for c in codes if c in auto]

    use_auto = False
    if manual_hit:
        log(f"[+] Found uploader subtitles: {', '.join(manual_hit)}")
    elif auto_hit and cfg.get("allow_auto_subs"):
        log(f"[+] Found auto-generated subtitles: {', '.join(auto_hit)}")
        use_auto = True
    else:
        log(f"[+] No {cfg.get('language_name', 'target-language')} subtitles available on this video.")
        return None

    opts = {
        "skip_download": True,
        "writesubtitles": not use_auto,
        "writeautomaticsub": use_auto,
        "subtitleslangs": manual_hit or auto_hit,
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": str(work / "subs"),
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        log(f"[!] Subtitle download failed ({exc}); transcribing the audio instead.")
        return None

    for code in (manual_hit or auto_hit):
        for suffix in (".srt", ".vtt"):
            candidate = work / f"subs.{code}{suffix}"
            if candidate.exists() and candidate.stat().st_size > 0:
                cues = srt.parse(candidate.read_text(encoding="utf-8", errors="ignore"))
                if cues:
                    log(f"[+] Read {len(cues)} cues from the {code} track.")
                    return cues
    log("[!] The downloaded subtitle file was empty; transcribing the audio instead.")
    return None


# ---------------------------------------------------------------------------
# 路线二：下载音频，交给 Whisper
# ---------------------------------------------------------------------------

def download_audio(url: str, work: Path, log, keep_quality: bool = False) -> Path:
    """下载音频。

    平时直接压成 Whisper 要的 16 kHz 单声道，省流量也省上传时间。但这一步
    是有损的，压完再去做人声分离就晚了——Demucs 要的高频细节已经没了。
    所以 keep_quality=True 时原样留着，等分离完再压。
    """
    state = {"pct": -10}

    def hook(status):
        if status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        if not total:
            return
        pct = int(status.get("downloaded_bytes", 0) / total * 100)
        if pct >= state["pct"] + 10:
            state["pct"] = pct
            log(f"    Downloading audio {pct}%")

    opts = {
        "format": "ba/ba*/b",
        "outtmpl": str(work / "audio.%(ext)s"),
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "progress_hooks": [hook],
        "retries": 5, "fragment_retries": 5,
    }
    if not keep_quality:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "48",
        }]
        opts["postprocessor_args"] = ["-ac", "1", "-ar", "16000"]

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    audio = work / "audio.mp3"
    if not audio.exists():
        found = next((p for p in work.glob("audio.*") if p.suffix != ".part"), None)
        if not found:
            raise RuntimeError("The audio download produced no file")
        audio = found
    return audio


def encode_for_whisper(audio: Path, work: Path, log) -> Path:
    """压成 Whisper 要的 16 kHz 单声道 MP3。

    只有音乐模式会走到这里：那条路上下载的是原始音质，分离出来的人声还是
    WAV，直接上传又大又慢。分离已经把伴奏去掉了，这时候再压掉高频不影响
    识别。压不出来就把手里这个原样送走——大不了慢一点，别把任务弄挂。
    """
    dest = work / "whisper.mp3"
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(audio),
        "-ac", "1", "-ar", "16000", "-b:a", "64k", str(dest),
    ])
    if not dest.exists() or dest.stat().st_size == 0:
        log("[!] Could not re-encode the audio; uploading it as-is.")
        return audio
    return dest


def apply_music_mode(cfg: dict, log) -> dict:
    """音乐模式下要改的几项设置。

    分段调短，是因为歌里长段的纯伴奏会让 Whisper 把时间轴越推越偏；
    段子短，偏了也只偏一段。自动字幕在这里一并关掉——音乐视频的自动
    字幕基本没法看，反倒会把真正该转写的活儿顶掉。
    """
    cfg = dict(cfg)
    cfg["chunk_seconds"] = min(int(cfg.get("chunk_seconds") or 600), MUSIC_CHUNK_SECONDS)
    cfg["allow_auto_subs"] = False
    if cfg.get("music_prompt"):
        cfg["prompt"] = cfg["music_prompt"]
    log(f"[+] Music mode: {cfg['chunk_seconds'] // 60}-minute chunks, lyrics prompt.")
    return cfg


def split_audio(audio: Path, work: Path, chunk_seconds: int, log) -> list[tuple[Path, float]]:
    """切成 (文件, 真实起始偏移) 的列表。

    切割点落在帧边界上，而不是精确落在要求的那一秒，所以每一段都要实测时长，
    偏移量按实测值累加。如果假定每段都刚好等长，视频越长，字幕就会一段比一段
    偏得更多。
    """
    duration = probe_duration(audio)
    if duration <= chunk_seconds:
        return [(audio, 0.0)]

    parts_dir = work / "parts"
    parts_dir.mkdir(exist_ok=True)
    # 分段用的是 -c copy，容器得跟原文件一致：把 webm 的音频原样倒进 .mp3
    # 里 ffmpeg 会直接拒绝，一段都切不出来。正常路线拿到的就是 mp3，
    # 音乐模式下重编码失败退回原始文件时，这里才派上用场。
    suffix = audio.suffix.lower() or ".mp3"
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(audio),
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-c", "copy", "-reset_timestamps", "1",
        str(parts_dir / f"part_%03d{suffix}"),
    ])

    parts = sorted(parts_dir.glob(f"part_*{suffix}"))
    if not parts:
        log("[!] Splitting failed; sending the whole file in one go.")
        return [(audio, 0.0)]

    chunks, offset = [], 0.0
    for part in parts:
        chunks.append((part, offset))
        offset += probe_duration(part)
    log(f"[+] Split into {len(chunks)} chunks of about {chunk_seconds // 60} minutes each.")
    return chunks


def transcribe_chunk(client, path: Path, cfg: dict, log, retries: int = 4) -> list[dict]:
    for attempt in range(1, retries + 1):
        try:
            with open(path, "rb") as fh:
                resp = client.audio.transcriptions.create(
                    file=(path.name, fh.read()),
                    model=cfg["model"],
                    # 指定语言，免得 Whisper 猜成邻近语言
                    # （把中文听成日文，或者反过来）。
                    language=cfg.get("whisper_language", "zh"),
                    prompt=cfg.get("prompt") or None,
                    temperature=0.0,              # 结果稳定，也更少胡编
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            segments = resp.segments or []
            return [s if isinstance(s, dict) else s.__dict__ for s in segments]
        except Exception as exc:
            if attempt == retries:
                raise
            wait = min(2 ** attempt, 30)
            log(f"    [!] {type(exc).__name__}: {exc} - retrying in {wait}s ({attempt}/{retries - 1})")
            time.sleep(wait)
    return []


def transcribe_all(client, chunks, cfg, log, should_stop) -> list[dict]:
    done = {"n": 0}
    total = len(chunks)

    def worker(item):
        chunk, offset = item
        if should_stop():
            raise Cancelled()
        segments = transcribe_chunk(client, chunk, cfg, log)
        done["n"] += 1
        log(f"    Transcribed {done['n']}/{total}")
        return [{"start": float(s["start"]) + offset,
                 "end": float(s["end"]) + offset,
                 "text": s.get("text", "")} for s in segments]

    workers = max(1, min(int(cfg.get("parallel_chunks", 3)), total))
    if workers == 1:
        results = [worker(item) for item in chunks]
    else:
        log(f"[+] {total} chunks, transcribing {workers} at a time...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(worker, chunks))

    return [seg for group in results for seg in group]


# ---------------------------------------------------------------------------
# 总流程
# ---------------------------------------------------------------------------

def process(url: str, cfg: dict, log=print, should_stop=lambda: False) -> dict:
    """生成一个 SRT。返回 {title, path, lines, source, language}。

    完整设置和压平后的设置都能接受；无论哪种，用的都是当前语言对应的
    输出目录、提示词和每行字数。
    """
    from groq import Groq

    cfg = config.active(cfg) if "languages" in cfg else cfg
    music = bool(cfg.get("music_mode"))
    separated = False
    translated = False
    if music:
        cfg = apply_music_mode(cfg, log)
    out_dir = config.output_dir(cfg)
    work = out_dir / ".work" / uuid.uuid4().hex[:8]
    work.mkdir(parents=True, exist_ok=True)

    try:
        log(f"[+] {url}")
        info = video_info(url)
        title = info.get("title") or "subtitles"
        log(f"[+] {title}")

        cues_in = None
        source = "whisper"
        if cfg.get("prefer_existing_subs"):
            cues_in = fetch_existing_subs(url, info, work, cfg, log)

        if cues_in:
            source = "youtube"
            segments = [{"start": c["start"], "end": c["end"],
                         "text": c["text"].replace("\n", " ")} for c in cues_in]
        else:
            key = config.api_key(cfg)
            if not key:
                raise RuntimeError("No Groq API key set. Add one in Settings, or set the GROQ_API_KEY environment variable.")
            client = Groq(api_key=key)

            log("[+] Downloading audio...")
            audio = download_audio(url, work, log, keep_quality=music)
            if should_stop():
                raise Cancelled()

            if music:
                vocals = separate.isolate_vocals(audio, work, log, should_stop)
                if should_stop():
                    raise Cancelled()
                # 分离没成功也照样往下走，只是准头回到从前。
                separated = vocals is not None
                audio = encode_for_whisper(vocals or audio, work, log)

            chunks = split_audio(audio, work, int(cfg["chunk_seconds"]), log)
            segments = transcribe_all(client, chunks, cfg, log, should_stop)
            log(f"[+] Whisper returned {len(segments)} raw segments.")

        if should_stop():
            raise Cancelled()

        cues = srt.build(segments, cfg)
        log(f"[+] {len(cues)} cues left after cleanup.")
        # 同上：勾了注音，也得 pypinyin／pykakasi 真的装了才会有那一行。
        has_reading = bool(cfg.get("reading")) and any("\n" in c["text"] for c in cues)

        if cfg.get("translate") and cues:
            key = config.api_key(cfg)
            if key:
                log("[+] Translating...")
                client = Groq(api_key=key)
                translations = translate_cues(
                    client, cues, cfg["translate_model"], log, should_stop,
                    language=cfg.get("language_name", "Chinese"),
                )
                for i, cue in enumerate(cues):
                    english = translations.get(i, "").strip()
                    if english:
                        cue["text"] += "\n" + english
                        translated = True
            else:
                log("[!] No API key; skipping translation.")

        dest = unique_path(out_dir / f"{safe_filename(title)}.srt")
        count = srt.write(cues, dest)
        log(f"[✓] {count} lines -> {dest}")

        # 记一笔这份字幕是怎么做出来的。第二行是注音还是英文，只有这里知道；
        # 之后预览、导出 Anki、气泡取句子都靠它，不必再去猜。
        info = {
            "language": cfg.get("language", "zh"),
            "reading": has_reading,
            # 记的是这份文件里实际有没有那一行，不是设置里勾没勾：翻译可能
            # 因为没有密钥而整段跳过，那样记成「有翻译」，读回来就会串行。
            "translation": translated,
            "reading_style": cfg.get("reading_style", ""),
            "source": source,
            "music": music,
            "separated": separated,
            "created": time.time(),
        }
        meta.record(out_dir, dest.name, **info)
        return {"title": title, "path": str(dest), "lines": count, "source": source,
                "language": info["language"], "music": music, "separated": separated}

    finally:
        shutil.rmtree(work, ignore_errors=True)
        parent = out_dir / ".work"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def sweep_work_dirs(out_dir: Path, older_than_hours: float = 6.0) -> int:
    """清掉 .work 下面没人要的临时目录。

    正常跑完会自己删干净，但进程被强杀（关掉那个黑窗口、机器重启）时来不及删，
    半个下载好的音频就一直躺在输出目录里。开机时扫一遍。

    只动够旧的：万一同时开着第二个实例，它手里那个是新的，不能碰。
    """
    parent = out_dir / ".work"
    if not parent.exists():
        return 0
    cutoff = time.time() - older_than_hours * 3600
    removed = 0
    for stale in parent.iterdir():
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                shutil.rmtree(stale, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    try:
        if not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return removed


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
