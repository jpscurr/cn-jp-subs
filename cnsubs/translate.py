"""可选的英文翻译行，通过 Groq 的对话模型生成。

每次按批翻译，并且同一批里能看到前后相邻的句子，
这样代词和省略的主语才能像实际对话里那样被正确还原。
"""

import json
import re

BATCH = 40

SYSTEM = (
    "You are a subtitle translator. Translate the {language} subtitles below into "
    "natural English, line by line. They are numbered sentences taken in order from "
    "one video. Translate every line, keep the numbering unchanged, and return exactly "
    "as many lines as you were given. Each translation must stand on its own as that "
    "line's subtitle: do not merge lines, do not add commentary, and do not write "
    "pinyin or romaji. Keep the English short and idiomatic. "
    'Output JSON only, in the form {{"1": "...", "2": "..."}}.'
)


def translate_cues(client, cues: list[dict], model: str, log=print,
                   should_stop=lambda: False, language: str = "Chinese") -> dict[int, str]:
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
            log(f"    [!] Batch {offset // BATCH + 1} failed to translate: {exc}")
        log(f"    Translated {min(offset + BATCH, total)}/{total} lines")
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
