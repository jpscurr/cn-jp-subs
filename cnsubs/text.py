"""文本处理：简繁转换、幻觉过滤、断行、注音。"""

import re

try:
    from opencc import OpenCC
    _t2s = OpenCC("t2s")
    _s2t = OpenCC("s2t")
except Exception:                                    # pragma: no cover
    _t2s = _s2t = None

try:
    from pypinyin import lazy_pinyin, Style
except Exception:                                    # pragma: no cover
    lazy_pinyin = None

try:
    import jieba
    jieba.setLogLevel(60)
except Exception:                                    # pragma: no cover
    jieba = None

try:
    import pykakasi
    _kakasi = pykakasi.kakasi()
except Exception:                                    # pragma: no cover
    _kakasi = None

HAS_OPENCC = _t2s is not None
HAS_PINYIN = lazy_pinyin is not None
HAS_SEGMENTER = jieba is not None
HAS_KANA = _kakasi is not None

HAN = r"一-鿿㐀-䶿"
KANA = r"ぁ-ゟ゠-ヿ"
_ALL_KANA = re.compile(rf"^[{KANA}ー]+$")
CJK = HAN + KANA
SENTENCE_END = "。！？!?；;…"
CLAUSE_END = "，,、：:"


def has_reading_support(language: str) -> bool:
    return HAS_KANA if language == "ja" else HAS_PINYIN


# ---------------------------------------------------------------------------
# 简繁转换（仅中文，对日文做转换会把汉字弄坏）
# ---------------------------------------------------------------------------

def to_simplified(text: str) -> str:
    return _t2s.convert(text) if _t2s else text


def convert_script(text: str, variant: str) -> str:
    """variant 取 's' 转简体，取 't' 转繁体，其他值则原样返回。"""
    if not text or not _t2s or variant not in ("s", "t"):
        return text
    return _t2s.convert(text) if variant == "s" else _s2t.convert(text)


# ---------------------------------------------------------------------------
# 注音行
# ---------------------------------------------------------------------------

def reading_line(text: str, language: str, style: str = "") -> str:
    if language == "ja":
        return kana_line(text, style or "kana")
    return pinyin_line(text)


def pinyin_line(text: str) -> str:
    """带声调的拼音，按词而不是按单个音节分组。

    按词分组读起来才顺，同时也能纠正那些只有结合上下文才定得下来的读音：
    「了」读 le 还是 liǎo、「行」读 háng 还是 xíng，以及「不」「一」的变调。
    """
    if not lazy_pinyin or not text:
        return ""

    words = [w for w in jieba.cut(text) if w.strip()] if jieba else [text]
    out: list[str] = []
    pending_open = ""
    for word in words:
        if word in _PUNCT_CLOSE:
            if out:
                out[-1] += _PUNCT_CLOSE[word]
            continue
        if word in _PUNCT_OPEN:
            pending_open += _PUNCT_OPEN[word]
            continue
        syllables = lazy_pinyin(word, style=Style.TONE, errors=lambda chars: [chars])
        joined = "".join(syllables).strip()
        if joined:
            out.append(pending_open + joined)
            pending_open = ""
    return re.sub(r"\s{2,}", " ", " ".join(out)).strip()


def kana_line(text: str, style: str = "kana") -> str:
    """平假名（或罗马字）读音，按 pykakasi 找到的词素边界断开。"""
    if not _kakasi or not text:
        return ""
    romaji = style == "romaji"
    field = "hepburn" if romaji else "hira"
    out: list[str] = []
    for token in _kakasi.convert(text):
        original = (token.get("orig") or "").strip()
        # 本来就写成假名的词，它自己就是读音，不要把片假名外来词
        # （エピソード）压成平假名。
        if not romaji and original and _ALL_KANA.match(original):
            out.append(original)
            continue
        word = (token.get(field) or original).strip()
        if not word:
            continue
        if word in _PUNCT_CLOSE:
            if out:
                out[-1] += _PUNCT_CLOSE[word]
            continue
        if word in _PUNCT_OPEN:
            out.append(_PUNCT_OPEN[word])
            continue
        out.append(word)
    return re.sub(r"\s{2,}", " ", " ".join(out)).strip()


# 中日文标点：转成对应的半角符号，并粘在相邻的词上，
# 免得它自己单独占一个用空格隔开的位置。
_PUNCT_CLOSE = {"，": ",", "、": ",", "。": ".", "！": "!", "？": "?", "：": ":",
                "；": ";", "…": "…", "”": '"', "’": "'", "）": ")", "》": "»",
                "」": "'", "』": '"', ",": ",", ".": ".", "!": "!", "?": "?",
                ":": ":", ";": ";", ")": ")"}
_PUNCT_OPEN = {"“": '"', "‘": "'", "（": "(", "《": "«", "「": "'", "『": '"', "(": "("}


# ---------------------------------------------------------------------------
# 清洗与垃圾判定
# ---------------------------------------------------------------------------

def clean(text: str, language: str = "zh") -> str:
    text = (text or "").strip().replace("　", " ")
    if language == "zh":
        # Whisper 会在汉字之间乱塞空格，中文本来没有空格。
        # 日文不动：那里出现的空格更可能是有意为之。
        text = re.sub(rf"(?<=[{HAN}])\s+(?=[{HAN}])", "", text)
    text = re.sub(rf"\s+(?=[{SENTENCE_END}{CLAUSE_END}）」』》])", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# Whisper 的固定幻觉——它从字幕训练数据里学来的频道结束语，
# 一遇到静音或纯音乐就会冒出来。
HALLUCINATIONS = {
    "zh": [
        r"^字幕由.{0,20}(提供|制作|組|组).{0,10}$",
        r"^字幕制作[:：]?.{0,20}$",
        r"^(请)?不吝点赞.{0,20}$",
        r"^(请)?订阅.{0,20}$",
        r"^谢谢(大家的)?(观看|收看|聆听).{0,10}$",
        r"^感谢(大家的)?(观看|收看|聆听).{0,10}$",
        r"^明镜与点点栏目$",
        r"^(欢迎)?(收看|收听|关注).{0,6}(频道|节目)$",
    ],
    "ja": [
        r"^ご視聴(いただき)?ありがとう(ございました|ございます).{0,10}$",
        r"^最後まで.{0,15}ありがとう.{0,15}$",
        r"^チャンネル登録.{0,15}$",
        r"^(高)?評価.{0,4}(と|、)?チャンネル登録.{0,15}$",
        r"^字幕(制作|提供|協力)[:：]?.{0,20}$",
        r"^おやすみなさい。?$",
        r"^エンディング$",
    ],
}
_HALLU = {lang: [re.compile(p) for p in patterns] for lang, patterns in HALLUCINATIONS.items()}

_SHARED_HALLU = [re.compile(p) for p in [r"^由\s*Amara\.org.{0,30}$", r"^\s*$"]]

# 整条只有标点、点号，或者音乐、掌声标记的字幕。
_NOISE = re.compile(r"^[\s。，、．.·…－—\-~～!！?？'\"“”‘’()（）\[\]【】「」♪♫*]+$")


def is_junk(text: str, language: str = "zh") -> bool:
    if not text:
        return True
    if _NOISE.match(text):
        return True
    # 统一转成简体再比对，这样繁体写法的结束语也能命中同一条规则。
    candidate = to_simplified(text) if language == "zh" else text
    patterns = _HALLU.get(language, []) + _SHARED_HALLU
    return any(p.match(candidate) for p in patterns)


def is_stutter(text: str) -> bool:
    """识别 Whisper 的死循环：同一个短语一直重复到底。

    单个字要重复很多次才算数，这样「哈哈哈哈」这种真的笑声能留下来，
    而卡住的解码循环会被剔除。
    """
    body = re.sub(rf"[^{CJK}]", "", text)
    if len(body) < 8:
        return False
    for size in range(1, len(body) // 4 + 1):
        repeats = len(body) // size
        if repeats < (10 if size == 1 else 4):
            continue
        unit = body[:size]
        if (unit * (repeats + 1))[:len(body)] == body:
            return True
    return False


# ---------------------------------------------------------------------------
# 断行
# ---------------------------------------------------------------------------

def split_long(seg: dict, max_chars: int) -> list[dict]:
    """把过长的字幕在标点处断开，并按字数比例分配时间轴。

    依次尝试句末标点 -> 句中标点 -> 硬性按字数切，
    所以只要能切，最后每条都会落在字数上限以内。
    """
    text = seg["text"]
    if len(text) <= max_chars:
        return [seg]

    parts = _split_keeping(text, SENTENCE_END)
    if max(len(p) for p in parts) > max_chars:
        parts = [q for p in parts for q in _split_keeping(p, CLAUSE_END)]
    if max(len(p) for p in parts) > max_chars:
        parts = [q for p in parts for q in _hard_wrap(p, max_chars)]

    parts = _merge_runts(parts, max_chars)
    if len(parts) <= 1:
        return [seg]

    total = sum(len(p) for p in parts) or 1
    span = seg["end"] - seg["start"]
    out, cursor = [], seg["start"]
    for part in parts:
        share = span * (len(part) / total)
        out.append({"start": cursor, "end": cursor + share, "text": part.strip()})
        cursor += share
    out[-1]["end"] = seg["end"]
    return out


def _split_keeping(text: str, punctuation: str) -> list[str]:
    parts = [p for p in re.split(rf"(?<=[{punctuation}])", text) if p.strip()]
    return parts or [text]


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)] or [text]


def _merge_runts(parts: list[str], max_chars: int, floor: int = 6) -> list[str]:
    """把过短的碎片并到旁边一条上，免得生成只有两个字的字幕。"""
    out: list[str] = []
    for part in parts:
        if out and (len(part) < floor or len(out[-1]) < floor) and len(out[-1]) + len(part) <= max_chars:
            out[-1] += part
        else:
            out.append(part)
    return out
