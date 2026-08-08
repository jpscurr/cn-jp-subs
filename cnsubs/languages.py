"""各语言的定义。

中文和日文之间所有不一样的地方都集中放在这里，其余代码因此与具体语言无关，
以后要再加一门语言，只需在下面添加一条记录。
"""

from pathlib import Path

DOCS = Path.home() / "Documents"

# 顺序即界面上语言按钮的顺序：日文排在前面，也是默认语言。
LANGUAGES = {
    "ja": {
        "name": "Japanese",
        "native": "日本語",
        "whisper_language": "ja",
        "sub_codes": ["ja", "ja-JP", "ja-Hira"],
        "reading_label": "Kana",
        "reading_styles": {"kana": "Hiragana", "romaji": "Rōmaji"},
        "default_reading_style": "kana",
        "has_script_variant": False,
        "defaults": {
            "output_dir": str(DOCS / "JapaneseSubs"),
            "max_chars_per_line": 24,    # 日文字幕的常见上限
            "reading_style": "kana",
            "prompt": "以下は日本語の音声です。標準的な句読点を使って書き起こしてください。",
        },
    },
    "zh": {
        "name": "Chinese",
        "native": "中文",
        "whisper_language": "zh",
        # 按优先级排列，视频上第一个存在的字幕轨道胜出。
        "sub_codes": ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK", "zh-SG", "yue"],
        "reading_label": "Pinyin",
        "reading_styles": {"pinyin": "Pinyin (tone marks)"},
        "default_reading_style": "pinyin",
        "has_script_variant": True,      # 简体 <-> 繁体
        "defaults": {
            "output_dir": str(DOCS / "ChineseSubs"),
            "max_chars_per_line": 26,    # 中文字幕一行超过约 28 字就很难读了
            "reading_style": "pinyin",
            # 引导 Whisper 输出普通话，并使用正常标点。
            "prompt": "以下是普通话的句子，请使用简体中文和标准标点符号转写。",
        },
    },
}

DEFAULT_LANGUAGE = "ja"


def get(code: str) -> dict:
    return LANGUAGES.get(code) or LANGUAGES[DEFAULT_LANGUAGE]


def codes() -> list[str]:
    return list(LANGUAGES)


def summary() -> list[dict]:
    """网页界面渲染语言切换按钮和相关设置项所需要的信息。"""
    return [{
        "code": code,
        "name": spec["name"],
        "native": spec["native"],
        "reading_label": spec["reading_label"],
        "reading_styles": spec["reading_styles"],
        "has_script_variant": spec["has_script_variant"],
    } for code, spec in LANGUAGES.items()]
