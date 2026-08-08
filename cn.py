"""cn.py —— 字幕生成器的命令行入口。

    python cn.py                      监听剪贴板里的 YouTube 链接
    python cn.py <链接> [<链接> ...]   直接处理指定链接或播放列表
    python cn.py --ui                 改为启动网页界面

可选参数（只对本次运行生效，不写回 config.json）：
    --lang zh|ja                  目标语言，各自有独立的输出目录
    --reading / --no-reading      每条字幕下加一行拼音（中文）或假名（日文）
    --translate / --no-translate  每条字幕下加一行英文
    --model 名称                  指定 whisper 模型
    --turbo                       等同于 --model whisper-large-v3-turbo
    --force                       忽略"已处理过"的记录
"""

import argparse
import json
import sys
import time
from pathlib import Path

from cnsubs import config, languages, pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cn.py", add_help=True,
                                     description="为 asbplayer 生成中文和日文字幕。")
    parser.add_argument("urls", nargs="*", help="YouTube 视频或播放列表链接")
    parser.add_argument("--ui", action="store_true", help="启动网页界面")
    parser.add_argument("--lang", choices=languages.codes(), help="目标语言")
    parser.add_argument("--reading", dest="reading", action="store_true", default=None,
                        help="每条字幕下加一行拼音／假名")
    parser.add_argument("--no-reading", dest="reading", action="store_false")
    parser.add_argument("--translate", dest="translate", action="store_true", default=None,
                        help="每条字幕下加一行英文")
    parser.add_argument("--no-translate", dest="translate", action="store_false")
    parser.add_argument("--model", help="whisper 模型名称")
    parser.add_argument("--turbo", action="store_true", help="使用 whisper-large-v3-turbo")
    parser.add_argument("--force", action="store_true", help="已处理过的链接也重新处理")
    return parser


def resolve_config(args) -> dict:
    cfg = config.load()
    if args.lang:
        cfg["language"] = args.lang
    if args.reading is not None:
        cfg["reading"] = args.reading
    if args.translate is not None:
        cfg["translate"] = args.translate
    if args.turbo:
        cfg["model"] = "whisper-large-v3-turbo"
    if args.model:
        cfg["model"] = args.model
    return cfg


def preflight(cfg: dict) -> None:
    missing = pipeline.missing_dependencies()
    if missing:
        sys.exit(f"[!] 这些没在 PATH 里：{', '.join(missing)}。请先装好 ffmpeg 再试。")
    if not config.api_key(cfg):
        print("[!] 没有 Groq API 密钥。请设置环境变量 GROQ_API_KEY，或用 python cn.py --ui 在界面里填。")
        print("    视频本身自带字幕的话，不填密钥也能正常处理。\n")


def run(url: str, cfg: dict) -> bool:
    try:
        videos = pipeline.expand(url)
    except Exception as exc:
        print(f"[!] {url}：{exc}")
        return False

    ok = True
    for i, video in enumerate(videos, start=1):
        if len(videos) > 1:
            print(f"\n--- 第 {i}/{len(videos)} 个 ---")
        try:
            pipeline.process(video["url"], cfg)
        except Exception as exc:
            print(f"[!] {type(exc).__name__}: {exc}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# 监听剪贴板
# ---------------------------------------------------------------------------

def seen_file(cfg: dict) -> Path:
    return config.output_dir(cfg) / ".processed.json"


def load_seen(cfg: dict) -> set:
    try:
        return set(json.loads(seen_file(cfg).read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(cfg: dict, seen: set) -> None:
    try:
        seen_file(cfg).write_text(json.dumps(sorted(seen), indent=0), encoding="utf-8")
    except OSError:
        pass


def watch_clipboard(cfg: dict, force: bool) -> None:
    try:
        import pyperclip
    except ImportError:
        sys.exit("没有安装 pyperclip。请运行：pip install pyperclip")

    flat = config.active(cfg)
    seen = set() if force else load_seen(flat)
    print(f"正在监听剪贴板 —— {flat['language_name']}。输出目录：{config.output_dir(flat)}")
    print("复制一个 YouTube 链接就会开始生成字幕。按 Ctrl+C 停止。\n")

    last = ""
    while True:
        try:
            clip = (pyperclip.paste() or "").strip()
            if clip != last:
                last = clip
                match = pipeline.URL_RE.search(clip)
                if match:
                    url = match.group(0)
                    if url in seen:
                        print(f"[=] 已经处理过：{url}")
                    elif run(url, cfg):
                        seen.add(url)
                        save_seen(config.active(cfg), seen)
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n已停止。")
            return
        except Exception as exc:
            print(f"[!] {exc}")
            time.sleep(2)


def main() -> None:
    args = build_parser().parse_args()

    if args.ui:
        import app
        app.main()
        return

    cfg = resolve_config(args)
    preflight(cfg)

    if args.urls:
        for url in args.urls:
            run(url, cfg)
    else:
        watch_clipboard(cfg, args.force)


if __name__ == "__main__":
    main()
