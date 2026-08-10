"""离线自检 —— 不联网，也不需要 API 密钥。"""
import pathlib
import sys
from cnsubs import config, languages, srt, text as T, pipeline

fails = []


def check(name, cond, extra=""):
    print(("  通过  " if cond else "  失败  ") + f"{name} {extra if not cond else ''}")
    if not cond:
        fails.append(name)


print("\n-- 运行环境 --")
check("opencc", T.HAS_OPENCC)
check("pypinyin", T.HAS_PINYIN)
check("jieba 分词", T.HAS_SEGMENTER)
check("pykakasi 假名", T.HAS_KANA)
check("ffmpeg/ffprobe", not pipeline.missing_dependencies(), pipeline.missing_dependencies())

print("\n-- 文本处理 --")
check("繁转简", T.convert_script("我們學習中文", "s") == "我们学习中文", T.convert_script("我們學習中文", "s"))
check("简转繁", T.convert_script("我们学习中文", "t") == "我們學習中文")
check("保持原样", T.convert_script("我们", "") == "我们")
check("去掉汉字间空格", T.clean("我 们 学 习 中文") == "我们学习中文", T.clean("我 们 学 习 中文"))
check("日文保留空格", T.clean("彼は 日本語を 勉強", "ja") == "彼は 日本語を 勉強",
      T.clean("彼は 日本語を 勉強", "ja"))
check("标点前不留空格", T.clean("你好 ，世界") == "你好，世界", T.clean("你好 ，世界"))

py = T.pinyin_line("我们学习中文")
check("拼音", py == "wǒmen xuéxí zhōngwén", py)
check("拼音结合上下文选读音", T.pinyin_line("我了解了这个问题") == "wǒ liǎojiě le zhège wèntí",
      T.pinyin_line("我了解了这个问题"))
check("拼音保留拉丁词", "Mandarin" in T.pinyin_line("欢迎收听Mandarin"),
      T.pinyin_line("欢迎收听Mandarin"))

kana = T.kana_line("今日は学校に行きました。")
check("假名", kana == "こんにちは がっこう に いき ました.", kana)
check("保留片假名", "エピソード" in T.kana_line("今日のエピソードでは"), T.kana_line("今日のエピソードでは"))
roma = T.kana_line("東京は大きい街です", "romaji")
check("罗马字", roma.startswith("toukyou"), roma)
check("注音按语言分派（中）", T.reading_line("你好", "zh") == T.pinyin_line("你好"))
check("注音按语言分派（日）", T.reading_line("学校", "ja") == T.kana_line("学校"))

print("\n-- 垃圾过滤 --")
for junk in ["字幕由 Amara.org 社群提供", "請不吝點贊 訂閱", "谢谢观看", "。。。", "♪", ""]:
    check(f"垃圾 {junk!r}", T.is_junk(T.clean(junk)))
for keep in ["我今天去了学校", "这是什么？"]:
    check(f"保留 {keep!r}", not T.is_junk(keep))
for junk in ["ご視聴ありがとうございました", "チャンネル登録お願いします"]:
    check(f"日文垃圾 {junk!r}", T.is_junk(junk, "ja"))
check("日文正常句保留", not T.is_junk("今日は学校に行きました", "ja"))
check("日文规则不误伤中文", not T.is_junk("ご視聴ありがとうございました", "zh"))
check("死循环", T.is_stutter("好的好的好的好的好的"))
check("正常句不算死循环", not T.is_stutter("我今天去了学校然后回家吃饭"))
check("笑声不算死循环", not T.is_stutter("哈哈哈哈"))

print("\n-- 断行 --")
long_seg = {"start": 0.0, "end": 10.0,
            "text": "我今天早上去了学校，然后跟朋友一起吃饭。下午我们去图书馆看书，晚上才回家。"}
pieces = T.split_long(long_seg, 26)
check("断成多条", len(pieces) > 1, len(pieces))
check("每条不超上限", all(len(p["text"]) <= 26 for p in pieces),
      [len(p["text"]) for p in pieces])
check("总时长不变", abs(pieces[-1]["end"] - 10.0) < 1e-6)
check("时间轴不倒退", all(pieces[i]["end"] <= pieces[i + 1]["start"] + 1e-9
                          for i in range(len(pieces) - 1)))
check("内容无丢失", "".join(p["text"] for p in pieces).replace(" ", "")
      == long_seg["text"].replace(" ", ""))
check("短句不动", T.split_long({"start": 0, "end": 1, "text": "你好"}, 26)[0]["text"] == "你好")

print("\n-- SRT 往返 --")
raw = """1
00:00:01,000 --> 00:00:03,500
你好世界

2
00:00:03,500 --> 00:00:06,000
這是<c.zh>測試</c>
"""
cues = srt.parse(raw)
check("解析条数", len(cues) == 2, len(cues))
check("起始时间", cues[0]["start"] == 1.0)
check("结束时间", cues[1]["end"] == 6.0)
check("去掉内嵌标签", cues[1]["text"] == "這是測試", cues[1]["text"])

vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
<00:00:01.000><c>你好</c>
"""
check("解析 VTT", srt.parse(vtt)[0]["text"] == "你好", srt.parse(vtt))
check("时间戳格式", srt.format_timestamp(3661.5) == "01:01:01,500", srt.format_timestamp(3661.5))
check("时间戳进位", srt.format_timestamp(0.9999) == "00:00:01,000", srt.format_timestamp(0.9999))

print("\n-- 组装字幕 --")
cfg = config.active({**config.DEFAULTS, "language": "zh", "reading": True})
segments = [
    {"start": 0.0, "end": 2.0, "text": "我 們 好"},
    {"start": 2.0, "end": 4.0, "text": "謝謝觀看"},          # 幻觉
    {"start": 4.0, "end": 4.0, "text": "零長度"},            # 零长度
    {"start": 3.0, "end": 6.0, "text": "重疊的字幕"},        # 与前一条重叠
    {"start": 6.0, "end": 6.5, "text": "重疊的字幕"},        # 与前一条重复
]
built = srt.build(segments, cfg)
check("幻觉被剔除", all("谢谢观看" not in c["text"] for c in built))
check("重复被剔除", len(built) == 3, [c["text"] for c in built])
check("互不重叠", all(built[i]["end"] <= built[i + 1]["start"] + 1e-9
                      for i in range(len(built) - 1)), [(c["start"], c["end"]) for c in built])
check("最短时长", all(c["end"] - c["start"] >= cfg["min_cue_seconds"] - 1e-9 for c in built))
check("统一简体", built[0]["text"].startswith("我们好"), built[0]["text"])
check("含拼音行", "\n" in built[0]["text"] and "wǒmen" in built[0]["text"], built[0]["text"])
check("序列化", srt.serialize(built).startswith("1\n00:00:00,000 --> "), srt.serialize(built)[:60])

ja_cfg = config.active({**config.DEFAULTS, "language": "ja", "reading": True})
ja_built = srt.build([
    {"start": 0.0, "end": 2.0, "text": "今日は学校に行きました。"},
    {"start": 2.0, "end": 4.0, "text": "ご視聴ありがとうございました"},
], ja_cfg)
check("日文组装并过滤", len(ja_built) == 1, [c["text"] for c in ja_built])
check("含假名行", "がっこう" in ja_built[0]["text"], ja_built[0]["text"])
check("日文汉字未被改动", "学校" in ja_built[0]["text"], ja_built[0]["text"])

print("\n-- 多语言 --")
A = lambda **kw: config.active({**config.DEFAULTS, **kw})
check("语言列表", set(languages.codes()) == {"zh", "ja"}, languages.codes())
check("日文跳过简繁转换", A(language="ja")["script_variant"] == "")
check("中文保留字形设置", A(language="zh")["script_variant"] == "s")
check("日文识别语言", A(language="ja")["whisper_language"] == "ja")
check("中文识别语言", A(language="zh")["whisper_language"] == "zh")
check("日文字幕轨道", "ja" in A(language="ja")["sub_codes"])
check("中文字幕轨道", "zh-Hans" in A(language="zh")["sub_codes"])
check("输出目录相互独立", A(language="zh")["output_dir"] != A(language="ja")["output_dir"],
      (A(language="zh")["output_dir"], A(language="ja")["output_dir"]))
check("日文每行字数", A(language="ja")["max_chars_per_line"] == 24)
check("默认语言是日文", languages.DEFAULT_LANGUAGE == "ja", languages.DEFAULT_LANGUAGE)
check("空白设置默认日文", A()["whisper_language"] == "ja", A()["language"])
check("未知语言回退到默认", A(language="xx")["whisper_language"] == "ja")

per_lang = config.active({**config.DEFAULTS,
                          "language": "ja",
                          "languages": {**config.DEFAULTS["languages"],
                                        "ja": {"output_dir": "D:/JP"}}})
check("分语言覆盖生效", per_lang["output_dir"] == "D:/JP", per_lang["output_dir"])
check("覆盖后其余默认值保留", per_lang["max_chars_per_line"] == 24)

migrated = config._migrate({"output_dir": "D:/old", "pinyin": True, "prompt": "p"})
check("旧目录迁移到中文下", migrated["languages"]["zh"]["output_dir"] == "D:/old", migrated)
check("pinyin 迁移为 reading", migrated.get("reading") is True, migrated)
check("旧键已清除", "output_dir" not in migrated, migrated)
check("重复迁移不变", config._migrate(migrated) == migrated)

# 密钥必须扛得住换语言、扛得住界面把占位符原样送回来。用临时文件试，
# 不去碰真的 config.json。
print("\n-- 密钥不会丢 --")
import tempfile
_real_config = config.CONFIG_FILE
with tempfile.TemporaryDirectory() as _tmp:
    config.CONFIG_FILE = pathlib.Path(_tmp) / "config.json"
    config.save({**config.DEFAULTS, "api_key": "gsk_real", "language": "ja"})
    check("先存下密钥", config.load()["api_key"] == "gsk_real")

    config.save({**config.load(), "language": "zh"})       # 换语言
    check("换语言后仍在", config.load()["api_key"] == "gsk_real", config.load()["api_key"])

    config.save({**config.load(), "api_key": config.KEY_PLACEHOLDER})
    check("占位符不会覆盖", config.load()["api_key"] == "gsk_real", config.load()["api_key"])

    config.save({**config.load(), "api_key": ""})
    check("空值不会覆盖", config.load()["api_key"] == "gsk_real", config.load()["api_key"])

    config.save({**config.load(), "api_key": "gsk_new"})
    check("真换密钥才生效", config.load()["api_key"] == "gsk_new", config.load()["api_key"])
    check("存盘是原子的", not (pathlib.Path(_tmp) / "config.json.tmp").exists())
config.CONFIG_FILE = _real_config

print("\n-- 链接识别 --")
good = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=abc-123_XY&t=30s",
    "https://www.youtube.com/shorts/abc123",
    "https://www.youtube.com/playlist?list=PL1234567890",
    "https://www.youtube.com/live/abc123",
]
for url in good:
    check(f"匹配 {url[:45]}", pipeline.URL_RE.search(url) is not None)
check("非 YouTube 不匹配", pipeline.URL_RE.search("https://example.com/watch?v=x") is None)
blob = "see https://youtu.be/aaa and https://youtu.be/bbb"
check("一次提取多个", len(pipeline.URL_RE.findall(blob)) == 2, pipeline.URL_RE.findall(blob))

print("\n-- 文件名 --")
check("去掉非法字符", pipeline.safe_filename('中文: "Lesson 1" <ep/2>') == "中文 Lesson 1 ep2",
      pipeline.safe_filename('中文: "Lesson 1" <ep/2>'))
check("空名有兜底", pipeline.safe_filename("") == "subtitles")
check("过长截断", len(pipeline.safe_filename("字" * 300)) <= 110)

print("\n-- 翻译结果解析 --")
from cnsubs.translate import _parse, _index
check("标准 JSON", _parse('{"1": "hello", "2": "bye"}') == {"1": "hello", "2": "bye"})
check("带代码围栏", _parse('```json\n{"1": "hi"}\n```') == {"1": "hi"}, _parse('```json\n{"1": "hi"}\n```'))
check("多包了一层", _parse('{"translations": {"1": "hi"}}') == {"1": "hi"})
check("返回数组", _parse('{"lines": ["a", "b"]}') == {"1": "a", "2": "b"})
check("完全不是 JSON", _parse("not json at all") == {})
check("序号提取", _index("3.") == 3 and _index("line 7") == 7)

print("\n-- 统计与检索 --")
from cnsubs import analyze, library

# 三行结构：原文 / 注音 / 英文。统计只应该看第一行。
sample = [
    {"start": 0.0, "end": 2.0, "text": "我买了一个椰子\nwǒ mǎi le yīgè yēzi\nI bought a coconut"},
    {"start": 2.0, "end": 4.0, "text": "椰子很好吃\nyēzi hěn hǎochī\nCoconuts are tasty"},
    {"start": 4.0, "end": 6.0, "text": "这是我的椰子"},
]
check("只统计原文行", analyze.source_lines(sample) == ["我买了一个椰子", "椰子很好吃", "这是我的椰子"],
      analyze.source_lines(sample))

vocab = analyze.vocabulary(sample, "zh")
words = {v["word"]: v["count"] for v in vocab}
check("词频统计", words.get("椰子") == 3, words)
check("虚词已剔除", "的" not in words and "了" not in words, list(words))
check("词条带注音", any(v["reading"] for v in vocab), vocab[:2])
check("注音不进词表", "yēzi" not in words and "coconut" not in words, list(words))

chars = {c["char"]: c["count"] for c in analyze.characters(sample)}
check("单字统计", chars.get("椰") == 3, chars)
check("常见字不上榜", "的" not in chars and "我" not in chars, list(chars))

st = analyze.stats(sample, "zh")
check("时长取首尾", st["seconds"] == 6.0, st["seconds"])
check("行数", st["lines"] == 3, st["lines"])
check("难度有档位", st["difficulty"]["level"] in ("Gentle", "Steady", "Brisk", "Dense"),
      st["difficulty"])

check("空字幕不报错", analyze.report([], "zh")["stats"]["lines"] == 0)
check("日文也能分词", isinstance(analyze.vocabulary(
    [{"start": 0, "end": 1, "text": "今日は公園に行きました"}], "ja"), list))

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    folder = pathlib.Path(tmp)
    (folder / "a.srt").write_text(srt.serialize(sample), encoding="utf-8")
    found = library.search(folder, "椰子")
    check("全库检索命中", len(found["hits"]) == 3, found)
    check("检索只看原文行", not library.search(folder, "coconut")["hits"],
          library.search(folder, "coconut")["hits"])
    check("空词返回空", library.search(folder, "  ")["hits"] == [])
    check("检索结果带时间", found["hits"][0]["start"] == 0.0, found["hits"][0])
    pool = library.phrase_pool(folder, "zh")
    check("句子池可用", all(4 <= len(p["text"]) <= 18 for p in pool), pool)

print("\n-- 拆行：注音还是英文 --")
from cnsubs import meta

# 有记录的时候不许再猜。日文那条是当初出问题的原型：只有两行，
# 第二行是假名，靠猜会被当成英文，导出 Anki 时就填错字段。
zh_two = "我买了椰子\nwǒ mǎi le yēzi"
ja_two = "今日は学校に行きました\nきょう は がっこう に いき ました"
en_two = "我买了椰子\nI bought a coconut"

check("按记录拆：注音", meta.split_lines(ja_two, {"reading": True, "translation": False})
      == ("今日は学校に行きました", "きょう は がっこう に いき ました", ""),
      meta.split_lines(ja_two, {"reading": True, "translation": False}))
check("按记录拆：翻译", meta.split_lines(en_two, {"reading": False, "translation": True})
      == ("我买了椰子", "", "I bought a coconut"),
      meta.split_lines(en_two, {"reading": False, "translation": True}))
check("三行顺序固定", meta.split_lines("我买了椰子\nwǒ mǎi le yēzi\nI bought a coconut", {})
      == ("我买了椰子", "wǒ mǎi le yēzi", "I bought a coconut"))
check("只有原文", meta.split_lines("我买了椰子", {}) == ("我买了椰子", "", ""))
check("空的不炸", meta.split_lines("", {}) == ("", "", ""))
check("空行被忽略", meta.split_lines("我买了椰子\n\nI bought a coconut", {})
      == ("我买了椰子", "", "I bought a coconut"), meta.split_lines("我买了椰子\n\nI bought a coconut", {}))

# 没有记录（以前生成的文件）就只能猜，但四种注音都得猜对。
check("猜：假名是注音", meta.looks_like_reading("きょう は がっこう に いき ました"))
check("猜：片假名也是注音", meta.looks_like_reading("エピソード"))
check("猜：拼音是注音", meta.looks_like_reading("wǒ mǎi le yēzi"))
check("猜：罗马字是注音", meta.looks_like_reading("toukyou wa ookii machi desu"))
check("猜：带 to 的罗马字仍是注音", meta.looks_like_reading("watashi to anata"),
      meta.looks_like_reading("watashi to anata"))
check("猜：英文不是注音", not meta.looks_like_reading("I bought a coconut"))
check("猜：小写英文也不是", not meta.looks_like_reading("that is what you said"),
      meta.looks_like_reading("that is what you said"))
check("猜：带汉字的不是注音", not meta.looks_like_reading("我买了椰子"))
check("猜：空行不是注音", not meta.looks_like_reading(""))
check("两行没记录时靠猜", meta.split_lines(ja_two, {})[1] == "きょう は がっこう に いき ました",
      meta.split_lines(ja_two, {}))
check("两项全开时也靠猜", meta.split_lines(zh_two, {"reading": True, "translation": True})[1]
      == "wǒ mǎi le yēzi", meta.split_lines(zh_two, {"reading": True, "translation": True}))

with tempfile.TemporaryDirectory() as tmp:
    folder = pathlib.Path(tmp)
    (folder / "a.srt").write_text("x", encoding="utf-8")
    meta.record(folder, "a.srt", reading=True, translation=False, music=True)
    check("记录能读回来", meta.get(folder, "a.srt")["reading"] is True, meta.get(folder, "a.srt"))
    check("记录写在隐藏文件里", (folder / meta.INDEX_NAME).exists())
    check("没写临时文件", not list(folder.glob("*.tmp")))
    meta.record(folder, "b.srt", reading=False, translation=True)
    check("两条记录并存", len(meta.read(folder)) == 2, meta.read(folder))
    check("没记录的文件返回空", meta.get(folder, "nope.srt") == {})
    check("记录挡住路径穿越", meta.get(folder, "../../config.json") == {})
    (folder / meta.INDEX_NAME).write_text("{ 坏掉的 json", encoding="utf-8")
    check("表坏了当没有", meta.read(folder) == {})

print("\n-- 音乐模式 --")
music_cfg = config.active({**config.DEFAULTS, "language": "zh", "music_mode": True})
check("中文有专门的歌词提示词", "歌" in music_cfg["music_prompt"], music_cfg["music_prompt"])
check("日文有专门的歌词提示词",
      "歌" in config.active({**config.DEFAULTS, "language": "ja"})["music_prompt"])

applied = pipeline.apply_music_mode(music_cfg, lambda *a: None)
check("分段被压短", applied["chunk_seconds"] == pipeline.MUSIC_CHUNK_SECONDS,
      applied["chunk_seconds"])
check("提示词换成歌词那条", applied["prompt"] == music_cfg["music_prompt"], applied["prompt"])
check("自动字幕被关掉", applied["allow_auto_subs"] is False)
check("原设置没被就地改掉", music_cfg["chunk_seconds"] == 600, music_cfg["chunk_seconds"])
short = pipeline.apply_music_mode({**music_cfg, "chunk_seconds": 120}, lambda *a: None)
check("本来就短就不动它", short["chunk_seconds"] == 120, short["chunk_seconds"])

from cnsubs import separate
check("能查 Demucs 装没装", isinstance(separate.available(), bool))
check("查的时候不把 torch 拉进来", "torch" not in sys.modules or separate.available())
check("music_mode 是通用设置", "music_mode" in config.SHARED_DEFAULTS)
check("music_prompt 不写进 config.json",
      "music_prompt" not in config.SHARED_DEFAULTS and "music_prompt" not in config.PER_LANGUAGE_KEYS)

with tempfile.TemporaryDirectory() as tmp:
    out = pathlib.Path(tmp)
    work = out / ".work"
    (work / "fresh").mkdir(parents=True)
    (work / "stale").mkdir()
    import os as _os
    _os.utime(work / "stale", (0, 0))          # 假装是很久以前留下的
    gone = pipeline.sweep_work_dirs(out)
    check("清掉过期的临时目录", gone == 1 and not (work / "stale").exists(), gone)
    check("正在用的不动它", (work / "fresh").exists())
    check("目录不在也不报错", pipeline.sweep_work_dirs(out / "nope") == 0)

print("\n-- 气泡取句子 --")
with tempfile.TemporaryDirectory() as tmp:
    folder = pathlib.Path(tmp)
    (folder / "ja.srt").write_text(srt.serialize([
        {"start": 0.0, "end": 2.0, "text": ja_two},
    ]), encoding="utf-8")
    meta.record(folder, "ja.srt", reading=True, translation=False)
    library._CACHE.clear()
    pool = library.phrase_pool(folder, "ja")
    check("假名进的是注音位", pool and pool[0]["reading"].startswith("きょう"), pool)
    check("翻译位是空的", pool and pool[0]["translation"] == "", pool)

print("\n-- 网页服务 --")
import app as webapp
client = webapp.app.test_client()
check("首页 200", client.get("/").status_code == 200)
state = client.get("/api/state")
check("状态接口 200", state.status_code == 200)
body = state.get_json()
check("状态结构完整",
      all(k in body for k in ("config", "active", "languages", "jobs", "env")), list(body))
check("密钥未泄露", body["config"]["api_key"] in ("", "set"), body["config"]["api_key"])
check("语言列表已下发", len(body["languages"]) == 2, body["languages"])
check("样式文件", client.get("/style.css").status_code == 200)
check("脚本文件", client.get("/app.js").status_code == 200)
check("文件列表 200", client.get("/api/files").status_code == 200)
bad = client.post("/api/jobs", json={"urls": "这里没有链接"})
check("拒绝无链接的文本", bad.status_code == 400, bad.status_code)
check("不存在的文件 404", client.get("/api/file?name=nope.srt").status_code == 404)
check("阻止路径穿越", client.get("/api/file?name=../../config.json").status_code == 404)
check("统计接口挡住路径穿越", client.get("/api/analyze?name=../../config.json").status_code == 404)
check("统计接口 404", client.get("/api/analyze?name=nope.srt").status_code == 404)
check("检索接口 200", client.get("/api/search?q=测试").status_code == 200)
check("检索空词不报错", client.get("/api/search?q=").get_json()["hits"] == [])
check("句子接口 200", "phrases" in client.get("/api/phrases").get_json())
check("环境里报了 demucs", "demucs" in body["env"], list(body["env"]))
check("重试不存在的任务 404", client.post("/api/jobs/nope/retry").status_code == 404)

# 文件列表和预览都要带上「这份字幕里有什么」，浏览器靠它拆行。
_real_out = webapp.active_output_dir
with tempfile.TemporaryDirectory() as tmp:
    folder = pathlib.Path(tmp)
    (folder / "demo.srt").write_text(srt.serialize([
        {"start": 0.0, "end": 2.0, "text": ja_two}]), encoding="utf-8")
    meta.record(folder, "demo.srt", reading=True, translation=False, music=True)
    webapp.active_output_dir = lambda: folder

    listed = client.get("/api/files").get_json()["files"]
    check("文件列表带标记", listed and listed[0]["marks"] == ["reading", "music"], listed)

    one = client.get("/api/file?name=demo.srt").get_json()
    check("预览带 layout", one["layout"]["reading"] is True, one.get("layout"))
    check("预览仍然给字幕", len(one["cues"]) == 1, one["cues"])

    # 索引文件本身不是字幕，别混进列表里
    check("索引不出现在文件列表", all(f["name"] != meta.INDEX_NAME for f in listed), listed)
    check("索引读不出来", client.get(f"/api/file?name={meta.INDEX_NAME}").status_code == 404)
webapp.active_output_dir = _real_out

# 重试要挑状态：还在跑的不许重排，不然同一个任务会有两份在跑。
_job = {"id": "t1", "url": "u", "title": "t", "language": "zh", "status": "running",
        "log": [], "result": None, "error": None, "cfg": {},
        "cancel": webapp.threading.Event()}
webapp.JOBS["t1"] = _job
check("跑着的任务不给重试", client.post("/api/jobs/t1/retry").status_code == 400)
_job["status"] = "failed"
check("失败的任务可以重试", client.post("/api/jobs/t1/retry").status_code == 200)
check("重试后回到排队", _job["status"] == "queued", _job["status"])
check("重试清掉了上次的报错", _job["error"] is None)
check("重试换了新的取消开关", not _job["cancel"].is_set())
# 重试真的把它排进队列了，后台线程会来捡。先掐掉再拿走，免得它拿着一份空设置
# 真去跑一趟——这是离线自检，不该有任何联网动作。
_job["cancel"].set()
webapp.JOBS.pop("t1", None)

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：{fails}"))
sys.exit(1 if fails else 0)
