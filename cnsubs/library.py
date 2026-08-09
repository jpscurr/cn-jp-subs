"""在已经生成的全部字幕里翻找。

学到一个词，想知道以前在哪几集里见过——这个模块干的就是这件事。
它会把整个输出目录扫一遍，逐条字幕匹配，返回文件名和时间点。

解析出来的结果按「路径 + 修改时间」缓存。文件没变就不重新解析，
所以连着搜几次只有第一次是慢的。
"""

import re
import threading
from pathlib import Path

from . import srt

# {路径: (修改时间, 字幕列表)}
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_LOCK = threading.Lock()

# 目录再大也别一次全嚼完——本地小工具，不值得为此卡住界面。
MAX_FILES = 400
MAX_HITS = 300


def cues_for(path: Path) -> list[dict]:
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []

    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

    try:
        cues = srt.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return []

    with _LOCK:
        _CACHE[key] = (mtime, cues)
        # 缓存无限长会把内存吃掉，超了就整个丢掉重来——本来也就重新解析一次。
        if len(_CACHE) > MAX_FILES * 2:
            _CACHE.clear()
            _CACHE[key] = (mtime, cues)
    return cues


def search(folder: Path, term: str, limit: int = MAX_HITS) -> dict:
    """在 folder 下的所有 .srt 里找 term。大小写不敏感。"""
    term = (term or "").strip()
    if not term or not folder.exists():
        return {"hits": [], "files_searched": 0, "truncated": False}

    needle = term.lower()
    paths = sorted(folder.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_FILES]

    hits: list[dict] = []
    truncated = False
    for path in paths:
        for cue in cues_for(path):
            # 只搜原文那一行。注音行里 "shi" 到处都是，翻译行是英文，
            # 都会把结果冲得没法看。
            head = (cue.get("text") or "").split("\n")[0]
            if needle not in head.lower():
                continue
            hits.append({
                "file": path.name,
                "start": cue.get("start", 0),
                "text": head,
            })
            if len(hits) >= limit:
                truncated = True
                break
        if truncated:
            break

    return {"hits": hits, "files_searched": len(paths), "truncated": truncated}


def phrase_pool(folder: Path, language: str, want: int = 60) -> list[dict]:
    """从最近几个文件里挑一些够短、够完整的句子。

    熊猫／锦鲤的气泡本来只会说写死的几句话，接上这个之后，
    它开口讲的就是你自己素材里的句子了。
    """
    if not folder.exists():
        return []

    paths = sorted(folder.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    pool: list[dict] = []
    seen: set[str] = set()

    for path in paths:
        for cue in cues_for(path):
            parts = [p.strip() for p in (cue.get("text") or "").split("\n") if p.strip()]
            if not parts:
                continue
            head = parts[0]
            # 太短没意思，太长气泡塞不下。
            if not (4 <= len(head) <= 18) or head in seen:
                continue
            # 至少得有一个汉字或假名，纯符号的不要。
            if not re.search(r"[一-鿿぀-ヿ]", head):
                continue
            seen.add(head)
            pool.append({
                "text": head,
                "reading": parts[1] if len(parts) > 1 else "",
                "translation": parts[2] if len(parts) > 2 else "",
                "file": path.name,
                "start": cue.get("start", 0),
            })
            if len(pool) >= want:
                return pool
    return pool
