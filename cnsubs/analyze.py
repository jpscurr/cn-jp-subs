"""字幕的统计：词频、用字、语速、难度。

给学习者用的：拿到一集字幕，先看看它到底难不难、讲得快不快、
反复出现的是哪些词——够不够格当泛听材料，看这几个数就知道了。

只吃字幕的第一行。注音行和英文行是我们自己加上去的，
统计的时候必须排除，否则拼音会被当成词，英文会污染字频。
"""

import re
from collections import Counter

from . import text as T

# 高频虚词。它们必然排在最前面，可对学习者毫无信息量——
# 与其让「的」占掉榜首，不如把位置让给真正的实词。
STOPWORDS = {
    "zh": {
        "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "不", "这", "那",
        "就", "都", "和", "也", "很", "到", "说", "要", "去", "个", "们", "着", "过",
        "把", "被", "让", "给", "对", "跟", "从", "但", "而", "还", "又", "再", "只",
        "会", "能", "可以", "什么", "怎么", "这个", "那个", "一个", "我们", "你们",
        "他们", "自己", "没有", "因为", "所以", "如果", "然后", "现在", "时候",
    },
    "ja": {
        "は", "を", "が", "に", "の", "で", "と", "も", "か", "ね", "よ", "な", "だ",
        "です", "ます", "した", "する", "して", "いる", "ある", "この", "その", "あの",
        "それ", "これ", "あれ", "そう", "こう", "から", "まで", "けど", "でも", "って",
        "ので", "のに", "たら", "ぱい", "ちゃ", "じゃ", "ん", "て", "た", "い", "う",
        "私", "僕", "俺", "あなた", "もう", "まだ", "ちょっと", "本当", "感じ",
    },
}

# 只留 CJK。纯拉丁、纯数字的 token 对这两门语言的词频没有意义。
_CJK_ONLY = re.compile(rf"^[{T.CJK}ー々]+$")
_HAN_CHAR = re.compile(rf"[{T.HAN}]")


def source_lines(cues: list[dict]) -> list[str]:
    """每条字幕的第一行，也就是原文。"""
    out = []
    for cue in cues:
        head = (cue.get("text") or "").split("\n")[0].strip()
        if head:
            out.append(head)
    return out


def tokenize(line: str, language: str) -> list[str]:
    """按语言切词。切不动就退回整行，至少不会丢内容。"""
    if language == "ja":
        if not T._kakasi:
            return []
        return [(t.get("orig") or "").strip() for t in T._kakasi.convert(line)]
    if T.jieba:
        return [w.strip() for w in T.jieba.cut(line)]
    return [line]


def vocabulary(cues: list[dict], language: str, limit: int = 40) -> list[dict]:
    """出现最多的实词，虚词已剔除。"""
    stop = STOPWORDS.get(language, set())
    counts: Counter[str] = Counter()
    for line in source_lines(cues):
        for word in tokenize(line, language):
            if not word or word in stop or not _CJK_ONLY.match(word):
                continue
            counts[word] += 1

    rows = []
    for word, count in counts.most_common(limit):
        rows.append({
            "word": word,
            "count": count,
            "reading": T.reading_line(word, language) or "",
        })
    return rows


# 这些字谁都认得，排在榜首只会挤掉有用的。统计 unique_chars 时仍然算它们，
# 只是不往榜上放。
TRIVIAL_CHARS = set("的了是我你他她它在有不这那就都和也很到说要去个们着过把被让给对跟从但而还又再只会能一二三四五六七八九十上下大小人中")


def characters(cues: list[dict], limit: int = 24) -> list[dict]:
    """单字频次。中文尤其看这个——生字多少直接决定难不难。"""
    counts: Counter[str] = Counter()
    for line in source_lines(cues):
        for ch in _HAN_CHAR.findall(line):
            if ch not in TRIVIAL_CHARS:
                counts[ch] += 1
    return [{"char": c, "count": n} for c, n in counts.most_common(limit)]


def stats(cues: list[dict], language: str) -> dict:
    """整体数字：时长、语速、用字量。"""
    lines = source_lines(cues)
    body = "".join(lines)
    chars = len(re.sub(r"\s", "", body))

    start = min((c.get("start", 0) for c in cues), default=0)
    end = max((c.get("end", 0) for c in cues), default=0)
    seconds = max(0.0, end - start)

    unique_han = len(set(_HAN_CHAR.findall(body)))
    per_minute = (chars / seconds * 60) if seconds > 0 else 0

    return {
        "lines": len(cues),
        "seconds": round(seconds, 1),
        "chars": chars,
        "unique_chars": unique_han,
        "chars_per_minute": round(per_minute),
        "avg_line_chars": round(chars / len(lines), 1) if lines else 0,
        "difficulty": difficulty(per_minute, unique_han, language),
    }


def difficulty(per_minute: float, unique_han: int, language: str) -> dict:
    """一个很粗的难度档位。

    两个信号：说得多快，以及用到多少不同的汉字。都是相对值，
    只用来在一堆候选视频里挑一个，不必当真。
    """
    # 日文一分钟的假名数天然比中文的汉字数多，阈值得分开定。
    fast = 380 if language == "ja" else 260
    steady = 260 if language == "ja" else 180
    wide = 700 if language == "ja" else 800

    score = 0
    if per_minute >= fast:
        score += 2
    elif per_minute >= steady:
        score += 1
    if unique_han >= wide:
        score += 2
    elif unique_han >= wide * 0.6:
        score += 1

    level, note = [
        ("Gentle", "slow and repetitive — good for early immersion"),
        ("Gentle", "slow and repetitive — good for early immersion"),
        ("Steady", "normal pace, a manageable spread of characters"),
        ("Brisk", "quick delivery or a wide vocabulary"),
        ("Dense", "fast and varied — expect to rewind"),
    ][min(score, 4)]
    return {"level": level, "note": note, "score": score}


def report(cues: list[dict], language: str) -> dict:
    return {
        "stats": stats(cues, language),
        "vocabulary": vocabulary(cues, language),
        "characters": characters(cues),
    }
