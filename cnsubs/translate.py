"""可选的英文翻译行，通过 Groq 的对话模型生成。

每次按批翻译，并且同一批里能看到前后相邻的句子，
这样代词和省略的主语才能像实际对话里那样被正确还原。
"""

import json
import re

BATCH = 40

SYSTEM = (
    "你是字幕翻译。把下面的{language}字幕逐行翻译成自然的英文。"
    "这些是同一个视频里按顺序排列的带编号的句子。"
    "每一行都要翻译，编号保持不变，行数也保持不变。"
    "每条译文都要能单独作为该行的字幕：不要合并行，不要加任何说明，不要写拼音或罗马字。"
    "译文要简短、地道。"
    '只输出 JSON，格式为 {{"1": "...", "2": "..."}}。'
)


def translate_cues(client, cues: list[dict], model: str, log=print,
                   should_stop=lambda: False, language: str = "中文") -> dict[int, str]:
    out: dict[int, str] = {}
    total = len(cues)
    system = SYSTEM.format(language=language)
    for offset in range(0, total, BATCH):
        if should_stop():
            break
        chunk = cues[offset:offset + BATCH]
        payload = "\n".join(
            f"{i + 1}. {c.get('source_text') or c['text'].split(chr(10))[0]}"
            for i, c in enumerate(chunk)
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": payload},
                ],
            )
            data = _parse(resp.choices[0].message.content)
            for key, value in data.items():
                index = _index(key)
                if index is not None and 0 <= index - 1 < len(chunk):
                    out[offset + index - 1] = str(value).strip()
        except Exception as exc:
            log(f"    [!] 第 {offset // BATCH + 1} 批翻译失败：{exc}")
        log(f"    已翻译 {min(offset + BATCH, total)}/{total} 行")
    return out


def _parse(content: str) -> dict:
    content = (content or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if isinstance(data, dict) and len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, (dict, list)):
            data = only
    if isinstance(data, list):
        return {str(i + 1): v for i, v in enumerate(data)}
    return data if isinstance(data, dict) else {}


def _index(key) -> int | None:
    match = re.search(r"\d+", str(key))
    return int(match.group(0)) if match else None
