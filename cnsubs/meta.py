"""每份字幕旁边记一笔：它是怎么做出来的。

SRT 文件里只有几行文本，看不出第二行到底是注音还是英文。三行的时候顺序是
固定的（原文／注音／英文），可只有两行时就得靠猜——原来那套猜法认的是拼音
声调符号，日文的假名注音一个都对不上，于是假名被当成翻译：预览里串行，导出
Anki 时更是直接填错字段，卡片背面变成一串假名。

所以生成的时候就把结构记下来，放在输出目录的 .subsmeta.json 里：一个目录一个
文件，不往用户的字幕堆里塞东西，asbplayer 也不会看见它。读的时候直接查表，
不用猜。以前生成的老文件表里没有，那就还是猜，只是猜法比原来准。
"""

import json
import os
import re
import threading
from pathlib import Path

INDEX_NAME = ".subsmeta.json"

# 表大了就把已经删掉的文件清出去。纯粹是别让它无限长，不是什么正确性问题。
MAX_ENTRIES = 500

_LOCK = threading.RLock()

# 假名（含长音符）。整行都是这些就是注音行——注音行里绝不会出现汉字。
_KANA_ONLY = re.compile(r"^[ぁ-ゟ゠-ヿー\s、。！？，,.!?'\"()（）·…]+$")
_CJK = re.compile(r"[一-鿿㐀-䶿ぁ-ゟ゠-ヿ]")
_TONE = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]", re.IGNORECASE)
_MACRON = re.compile(r"[āīūēōâîûêô]", re.IGNORECASE)
# 英文译文里几乎必然出现的功能词。罗马字注音里不会有。
_ENGLISH = re.compile(
    r"\b(the|a|an|and|is|are|was|were|be|been|to|of|in|on|it|its|that|this|"
    r"you|your|i|we|he|she|they|not|but|for|with|have|has|had|what|why|how|"
    r"there|here|about|from|will|would|can|could|do|does|did)\b",
    re.IGNORECASE,
)


def path_for(folder: Path) -> Path:
    return Path(folder) / INDEX_NAME


def read(folder: Path) -> dict:
    """整张表。读不出来就当空表——记录丢了只是回到靠猜，不该让界面挂掉。"""
    try:
        raw = json.loads(path_for(folder).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def get(folder: Path, name: str) -> dict:
    entry = read(folder).get(Path(name).name)
    return entry if isinstance(entry, dict) else {}


def record(folder: Path, name: str, **info) -> None:
    """记下一份字幕的结构。写不进去就算了，功能会退回到猜。"""
    folder = Path(folder)
    name = Path(name).name
    with _LOCK:
        table = read(folder)
        table[name] = info
        if len(table) > MAX_ENTRIES:
            table = _prune(folder, table)
        try:
            tmp = path_for(folder).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path_for(folder))
        except OSError:
            pass


def _prune(folder: Path, table: dict) -> dict:
    """把已经不在目录里的记录扔掉。"""
    try:
        alive = {p.name for p in folder.glob("*.srt")}
    except OSError:
        return table
    return {k: v for k, v in table.items() if k in alive}


# ---------------------------------------------------------------------------
# 拆行
# ---------------------------------------------------------------------------

def split_lines(text: str, info: dict | None = None) -> tuple[str, str, str]:
    """把一条字幕拆成 (原文, 注音, 翻译)。

    有记录就照记录拆。注意记录说的是「这份文件里有没有注音行」，不是「这一条
    有没有」——某一句全是拉丁字母时注音行会是空的，那条就只有两行。所以两行的
    时候还是要看记录里两项各自的开关，两项都开就只能回去猜。
    """
    lines = [line for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return "", "", ""
    head, rest = lines[0], lines[1:]
    if not rest:
        return head, "", ""
    if len(rest) > 1:
        return head, rest[0], " ".join(rest[1:])

    info = info or {}
    has_reading = info.get("reading")
    has_translation = info.get("translation")
    if has_reading and not has_translation:
        return head, rest[0], ""
    if has_translation and not has_reading:
        return head, "", rest[0]
    return (head, rest[0], "") if looks_like_reading(rest[0]) else (head, "", rest[0])


def looks_like_reading(line: str) -> bool:
    """没有记录时的兜底判断：这一行是注音还是英文。

    注音行有一个共同点——里面绝不会有汉字。剩下的分三种：假名、带声调符号的
    拼音、罗马字。前两种一眼能认出来。罗马字和英文都是拉丁字母，分不开时看两点：
    翻译是正常英文句子，首字母大写；罗马字是 kakasi 的输出，从头到尾都是小写。
    单看功能词不够——「と」的罗马字就是 to，正好撞上英文的 to，所以要凑够两个
    不同的功能词才当英文。
    """
    line = (line or "").strip()
    if not line:
        return False
    if _KANA_ONLY.match(line):
        return True
    if _CJK.search(line):
        return False
    if _TONE.search(line):
        return True
    if line != line.lower():          # 有大写字母，是英文句子
        return False
    if len({w.lower() for w in _ENGLISH.findall(line)}) >= 2:
        return False
    return bool(_MACRON.search(line)) or len(line.split()) > 1 or not _ENGLISH.fullmatch(line)
