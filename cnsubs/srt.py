"""SRT 的解析与生成。

一条字幕就是一个普通字典：{"start": float, "end": float, "text": str}。
多行内容（原文 / 注音 / 英文）之间用 "\n" 连接。
"""

import re
from pathlib import Path

from . import text as T

TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hrs, millis = divmod(millis, 3_600_000)
    mins, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse(raw: str) -> list[dict]:
    """把 SRT 或 WebVTT 读成字幕列表。对 yt-dlp 返回的各种脏格式都比较宽容。"""
    raw = raw.replace("\r\n", "\n").replace("﻿", "")
    cues: list[dict] = []
    for block in re.split(r"\n{2,}", raw):
        match = TIME_RE.search(block)
        if not match:
            continue
        start = _to_seconds(*match.group(1, 2, 3, 4))
        end = _to_seconds(*match.group(5, 6, 7, 8))
        body = block[match.end():].strip("\n")
        # 去掉时间轴上方残留的序号行。
        body = re.sub(r"^\s*\d+\s*$", "", body, flags=re.MULTILINE)
        # VTT 的内嵌标签：<c>、<00:00:01.000>、<v 某人>
        body = re.sub(r"<[^>]*>", "", body)
        body = "\n".join(line.strip() for line in body.split("\n") if line.strip())
        if body:
            cues.append({"start": start, "end": end, "text": body})
    return cues


def serialize(cues: list[dict]) -> str:
    rows = []
    for i, cue in enumerate(cues, start=1):
        rows.append(
            f"{i}\n{format_timestamp(cue['start'])} --> {format_timestamp(cue['end'])}\n{cue['text']}\n"
        )
    return "\n".join(rows)


def build(segments: list[dict], cfg: dict, translations: dict[int, str] | None = None) -> list[dict]:
    """把原始片段清洗、过滤、断行、去重叠，整理成最终字幕。

    cfg 是 config.active() 压平后的设置，里面带着当前语言，
    所以注音行是拼音还是假名由它决定，调用方不用操心。
    """
    language = cfg.get("language", "zh")
    variant = cfg.get("script_variant", "")
    max_chars = int(cfg.get("max_chars_per_line", 26))
    min_cue = float(cfg.get("min_cue_seconds", 0.4))
    want_reading = bool(cfg.get("reading"))
    reading_style = cfg.get("reading_style", "")

    cues: list[dict] = []
    previous_end = 0.0
    previous_text = None

    for seg in segments:
        body = T.clean(T.convert_script(seg.get("text", ""), variant), language)
        if T.is_junk(body, language) or T.is_stutter(body):
            continue
        # Whisper 跟丢内容时会把上一句重复一遍。
        if body == previous_text:
            continue
        previous_text = body

        start = float(seg["start"])
        end = float(seg["end"])
        if end <= start:
            end = start + min_cue

        for piece in T.split_long({"start": start, "end": end, "text": body}, max_chars):
            cue_start = max(piece["start"], previous_end)       # 不让字幕相互重叠
            cue_end = max(piece["end"], cue_start + min_cue)    # 也不留零长度的字幕
            lines = [piece["text"]]
            if want_reading:
                reading = T.reading_line(piece["text"], language, reading_style)
                if reading:
                    lines.append(reading)
            cues.append({"start": cue_start, "end": cue_end, "text": "\n".join(lines),
                         "source_text": piece["text"]})
            previous_end = cue_end

    if translations:
        for i, cue in enumerate(cues):
            english = translations.get(i, "").strip()
            if english:
                cue["text"] += "\n" + english
    return cues


def write(cues: list[dict], dest: Path) -> int:
    dest.write_text(serialize(cues), encoding="utf-8")
    return len(cues)
