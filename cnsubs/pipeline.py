"""生成流程：解析链接 -> 用现成字幕或 Whisper 转写 -> 输出 SRT。"""

import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from . import config, languages, srt
from .translate import translate_cues

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?\S*?v=|shorts/|live/|embed/|playlist\?\S*?list=)|youtu\.be/)"
    r"[\w\-]+(?:[?&]\S*)?"
)

NO_WINDOW = {"creationflags": 0x08000000} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}


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
        raise RuntimeError("yt-dlp 读不出这个链接")

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
        log(f"[+] 找到上传者字幕：{', '.join(manual_hit)}")
    elif auto_hit and cfg.get("allow_auto_subs"):
        log(f"[+] 找到自动生成的字幕：{', '.join(auto_hit)}")
        use_auto = True
    else:
        log(f"[+] 这个视频没有可用的{cfg.get('language_name', '目标语言')}字幕。")
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
        log(f"[!] 字幕下载失败（{exc}），改用语音转写。")
        return None

    for code in (manual_hit or auto_hit):
        for suffix in (".srt", ".vtt"):
            candidate = work / f"subs.{code}{suffix}"
            if candidate.exists() and candidate.stat().st_size > 0:
                cues = srt.parse(candidate.read_text(encoding="utf-8", errors="ignore"))
                if cues:
                    log(f"[+] 从 {code} 轨道读到 {len(cues)} 条字幕。")
                    return cues
    log("[!] 下载到的字幕文件是空的，改用语音转写。")
    return None


# ---------------------------------------------------------------------------
# 路线二：下载音频，交给 Whisper
# ---------------------------------------------------------------------------

def download_audio(url: str, work: Path, log) -> Path:
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
            log(f"    音频下载中 {pct}%")

    opts = {
        "format": "ba/ba*/b",
        "outtmpl": str(work / "audio.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "48",
        }],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "progress_hooks": [hook],
        "retries": 5, "fragment_retries": 5,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    audio = work / "audio.mp3"
    if not audio.exists():
        found = next((p for p in work.glob("audio.*") if p.suffix != ".part"), None)
        if not found:
            raise RuntimeError("音频下载没有生成文件")
        audio = found
    return audio


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
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(audio),
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-c", "copy", "-reset_timestamps", "1",
        str(parts_dir / "part_%03d.mp3"),
    ])

    parts = sorted(parts_dir.glob("part_*.mp3"))
    if not parts:
        log("[!] 切割失败，整个文件一次性发送。")
        return [(audio, 0.0)]

    chunks, offset = [], 0.0
    for part in parts:
        chunks.append((part, offset))
        offset += probe_duration(part)
    log(f"[+] 已切成 {len(chunks)} 段，每段约 {chunk_seconds // 60} 分钟。")
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
            log(f"    [!] {type(exc).__name__}: {exc} —— {wait} 秒后重试（第 {attempt}/{retries - 1} 次）")
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
        log(f"    已转写 {done['n']}/{total}")
        return [{"start": float(s["start"]) + offset,
                 "end": float(s["end"]) + offset,
                 "text": s.get("text", "")} for s in segments]

    workers = max(1, min(int(cfg.get("parallel_chunks", 3)), total))
    if workers == 1:
        results = [worker(item) for item in chunks]
    else:
        log(f"[+] 共 {total} 段，每次同时转写 {workers} 段……")
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
                raise RuntimeError("没有设置 Groq API 密钥。请在设置里填写，或设置环境变量 GROQ_API_KEY。")
            client = Groq(api_key=key)

            log("[+] 正在下载音频……")
            audio = download_audio(url, work, log)
            if should_stop():
                raise Cancelled()

            chunks = split_audio(audio, work, int(cfg["chunk_seconds"]), log)
            segments = transcribe_all(client, chunks, cfg, log, should_stop)
            log(f"[+] Whisper 返回 {len(segments)} 个原始片段。")

        if should_stop():
            raise Cancelled()

        cues = srt.build(segments, cfg)
        log(f"[+] 清洗后剩 {len(cues)} 条字幕。")

        if cfg.get("translate") and cues:
            key = config.api_key(cfg)
            if key:
                log("[+] 正在翻译……")
                client = Groq(api_key=key)
                translations = translate_cues(
                    client, cues, cfg["translate_model"], log, should_stop,
                    language=cfg.get("language_name", "中文"),
                )
                for i, cue in enumerate(cues):
                    english = translations.get(i, "").strip()
                    if english:
                        cue["text"] += "\n" + english
            else:
                log("[!] 没有 API 密钥，跳过翻译。")

        dest = unique_path(out_dir / f"{safe_filename(title)}.srt")
        count = srt.write(cues, dest)
        log(f"[✓] {count} 行 -> {dest}")
        return {"title": title, "path": str(dest), "lines": count, "source": source,
                "language": cfg.get("language", "zh")}

    finally:
        shutil.rmtree(work, ignore_errors=True)
        parent = out_dir / ".work"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
