"""设置项，保存在项目目录下的 config.json 里。

设置分成两类。通用项（API 密钥、模型、分段长度等）放在顶层；
凡是应该随语言而变的——输出目录、提示词、每行字数、注音方式——放在
"languages" 下面，这样从中文切到日文时，文件的存放位置也跟着一起切换。

API 密钥绝不写进源代码。读取顺序：
    1. 环境变量 GROQ_API_KEY
    2. config.json 里的 "api_key"
"""

import json
import os
import threading
from pathlib import Path

from . import languages

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_DIR / "config.json"

# 网页服务是多线程的：切换语言、勾选开关、后台任务都会同时读写这个文件。
# 读和写都走这把锁，写入本身也是原子的，免得有人读到只写了一半的文件——
# 那样解析会失败，回落到空白设置，接着一存盘，密钥就没了。
_LOCK = threading.RLock()

# 界面上不会把真密钥发下去，只发这个占位符。它绝不能被当成真密钥存回文件。
KEY_PLACEHOLDER = "set"

# 与当前选中语言无关、全局通用的设置。
SHARED_DEFAULTS = {
    "api_key": "",
    "language": languages.DEFAULT_LANGUAGE,

    # whisper-large-v3       -> 慢一些，但在声调和同音字上明显更准
    # whisper-large-v3-turbo -> 快约四倍、更便宜，细微差别上弱一些
    "model": "whisper-large-v3",

    # 生成英文翻译行时使用的对话模型。
    "translate_model": "llama-3.3-70b-versatile",

    "chunk_seconds": 600,        # 每次上传 10 分钟，远低于 25 MB 的上限
    "min_cue_seconds": 0.4,
    "parallel_chunks": 3,        # 同时进行的转写请求数

    "prefer_existing_subs": True,
    "allow_auto_subs": False,    # 自动生成的字幕通常还不如自己转写

    # 音乐模式：先用 Demucs 把人声从伴奏里分出来，再用更短的分段和面向
    # 歌词的提示词去转写。唱腔本来就把声调抹平了，伴奏再一压，Whisper
    # 就只能靠猜——把伴奏去掉，猜的成分能少一大截。
    "music_mode": False,

    "reading": False,            # 每条字幕下面加一行拼音／假名
    "translate": False,          # 每条字幕下面加一行英文

    # 仅中文使用："s" 简体，"t" 繁体，"" 保持原样。
    "script_variant": "s",
}

# 随语言而变的设置项，默认值来自 languages.py。
PER_LANGUAGE_KEYS = ("output_dir", "prompt", "max_chars_per_line", "reading_style")

DEFAULTS = dict(SHARED_DEFAULTS)
DEFAULTS["languages"] = {
    code: dict(languages.get(code)["defaults"]) for code in languages.codes()
}


def _blank() -> dict:
    cfg = dict(SHARED_DEFAULTS)
    cfg["languages"] = {code: dict(languages.get(code)["defaults"]) for code in languages.codes()}
    return cfg


def _read_raw() -> dict:
    """按原样读出 config.json；读不出来就返回空字典。"""
    with _LOCK:
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[!] Could not read config.json ({exc}); falling back to defaults.")
            return {}
    return stored if isinstance(stored, dict) else {}


def load() -> dict:
    cfg = _blank()
    stored = _read_raw()
    if not stored:
        return cfg

    stored = _migrate(stored)
    for key, value in stored.items():
        if key == "languages":
            continue
        if key in SHARED_DEFAULTS:
            cfg[key] = value
    for code, values in (stored.get("languages") or {}).items():
        if code in cfg["languages"] and isinstance(values, dict):
            cfg["languages"][code].update(
                {k: v for k, v in values.items() if k in PER_LANGUAGE_KEYS}
            )
    if cfg["language"] not in languages.codes():
        cfg["language"] = languages.DEFAULT_LANGUAGE
    return cfg


def _migrate(stored: dict) -> dict:
    """把还不支持多语言时期的 config.json 转换成现在的结构。"""
    if "languages" in stored:
        return stored
    stored = dict(stored)
    zh = {}
    for old, new in (("output_dir", "output_dir"), ("prompt", "prompt"),
                     ("max_chars_per_line", "max_chars_per_line")):
        if old in stored:
            zh[new] = stored.pop(old)
    if "pinyin" in stored:
        stored["reading"] = stored.pop("pinyin")
    if zh:
        stored["languages"] = {"zh": zh}
    return stored


def save(cfg: dict) -> None:
    with _LOCK:
        out = {k: cfg.get(k, v) for k, v in SHARED_DEFAULTS.items()}
        out["languages"] = {
            code: {k: values[k] for k in PER_LANGUAGE_KEYS if k in values}
            for code, values in (cfg.get("languages") or {}).items()
            if code in languages.codes()
        }

        # 存盘只有一种情况会动密钥：确实传进来一个新的。空字符串、占位符，
        # 或者读文件失败留下的空白设置，一律保留文件里原来那一个——
        # 换语言、改个目录，都不该把密钥弄丢。
        incoming = out.get("api_key")
        incoming = incoming.strip() if isinstance(incoming, str) else ""
        if not incoming or incoming == KEY_PLACEHOLDER:
            existing = _read_raw().get("api_key")
            out["api_key"] = existing.strip() if isinstance(existing, str) else ""
        else:
            out["api_key"] = incoming

        # 万一文件已经坏了，先留一份原件再覆盖——密钥说不定还能从里面抠出来。
        if not out["api_key"] and CONFIG_FILE.exists() and not _read_raw():
            try:
                os.replace(CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bad"))
                print(f"[!] The old config.json was unreadable; kept a copy as {CONFIG_FILE.name}.bad")
            except OSError:
                pass

        # 先写临时文件再整体替换：别人读到的要么是旧的，要么是新的，
        # 不会是写了一半的。
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)


def active(cfg: dict) -> dict:
    """把当前选中语言的设置压平成一个字典，供后续流程直接使用。"""
    code = cfg.get("language") or languages.DEFAULT_LANGUAGE
    spec = languages.get(code)
    flat = {k: v for k, v in cfg.items() if k != "languages"}
    flat.update(spec["defaults"])
    flat.update(cfg.get("languages", {}).get(code, {}))
    flat["language"] = code
    flat["whisper_language"] = spec["whisper_language"]
    flat["sub_codes"] = spec["sub_codes"]
    flat["language_name"] = spec["name"]
    # 提示词是可以在设置里改的，音乐模式那条不行——它跟着语言走，
    # 只在音乐模式真正启用时才顶上来。
    flat["music_prompt"] = spec.get("music_prompt") or flat.get("prompt", "")
    if not spec["has_script_variant"]:
        flat["script_variant"] = ""      # 对日文做简繁转换会把汉字弄坏
    return flat


def api_key(cfg: dict | None = None) -> str:
    env = os.environ.get("GROQ_API_KEY", "").strip()
    if env:
        return env
    return (cfg or load()).get("api_key", "").strip()


def output_dir(cfg: dict) -> Path:
    """完整设置和压平后的设置都能接受。"""
    raw = cfg.get("output_dir") or active(cfg)["output_dir"]
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path
