"""用 Demucs 把人声从伴奏里剥出来，再送去转写。

唱的东西——尤其是压在鼓和贝斯上面的说唱——Whisper 听错的多半不是口音，
而是伴奏。鼓点和人声抢的是同一段频率，模型只好去猜，猜出来的就是那些
看着像中文、跟原词却对不上的句子。先把人声单独分出来，词能对上不少。

Demucs 是可选依赖：装了才在界面上把开关点亮，没装照常走原来的流程。
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# htdemucs 是 Demucs v4 的默认模型。四轨拆分里人声那一轨最稳，而
# --two-stems=vocals 只算人声和其余部分，比拆满四轨快将近一倍。
MODEL = "htdemucs"

NO_WINDOW = {"creationflags": 0x08000000} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}

PERCENT_RE = re.compile(r"(\d{1,3})%")


def available() -> bool:
    """装没装 Demucs。

    只查有没有这个包，不去 import 它——Demucs 一 import 就把 torch 拉进来，
    好几秒起步。这个函数是 /api/state 每次刷新都要调的，界面等不起。
    """
    try:
        return importlib.util.find_spec("demucs") is not None
    except (ImportError, ValueError):
        return False


def device() -> str:
    """有显卡就用显卡。CPU 也能跑，只是慢得多。"""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def isolate_vocals(audio: Path, work: Path, log, should_stop=lambda: False) -> Path | None:
    """把 audio 分离成人声轨，返回那个 WAV。

    分离不成功就返回 None，由调用方决定退回原音频还是报错——分离只是
    让识别更准，不该因为它没跑起来就把整个任务毙掉。
    被 should_stop 打断时同样返回 None。
    """
    if not available():
        log("[!] Demucs is not installed; transcribing the mixed audio instead.")
        return None

    out_dir = work / "demucs"
    dev = device()
    if dev == "cpu":
        log("[+] Separating vocals with Demucs (CPU — this takes a few minutes)...")
    else:
        log("[+] Separating vocals with Demucs (GPU)...")

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems=vocals",
        "-n", MODEL,
        "-d", dev,
        "-o", str(out_dir),
        str(audio),
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **NO_WINDOW,
        )
    except OSError as exc:
        log(f"[!] Could not start Demucs ({exc}); transcribing the mixed audio instead.")
        return None

    # Demucs 把进度条打在 stderr 上，一行里能刷好几十次百分比。
    # 每涨 10% 报一次就够了，不然日志会被冲掉。
    shown = -10
    tail: list[str] = []
    try:
        for line in proc.stdout:
            tail.append(line.rstrip())
            del tail[:-15]
            if should_stop():
                proc.kill()
                proc.wait(timeout=10)
                return None
            found = PERCENT_RE.findall(line)
            if found:
                pct = int(found[-1])
                if pct >= shown + 10:
                    shown = pct - pct % 10
                    log(f"    Separating vocals {shown}%")
    finally:
        proc.stdout.close()
        code = proc.wait()

    if code != 0:
        log(f"[!] Demucs exited with code {code}; transcribing the mixed audio instead.")
        for line in tail[-4:]:
            if line.strip():
                log(f"    {line}")
        return None

    vocals = next(out_dir.rglob("vocals.*"), None)
    if vocals is None or vocals.stat().st_size == 0:
        log("[!] Demucs produced no vocals track; transcribing the mixed audio instead.")
        return None

    log(f"[+] Vocals isolated ({vocals.stat().st_size // 1024} KB).")
    return vocals
