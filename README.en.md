[中文](README.md) · [日本語](README.ja.md) · **English**

# Subtitle generator

Chinese and Japanese subtitles for sentence mining. The generated `.srt` drops straight into asbplayer.

Give it a YouTube link. If the uploader published subtitles in the target language, those get used as-is —
free, instant, and an exact match for what was actually said. If not, the audio is downloaded and
transcribed with Groq's Whisper. Either path produces a cleaned-up `.srt`, optionally with a reading line
and an English line under each subtitle.

Chinese and Japanese have separate output directories, so videos in the two languages never mix.

## Install

```
pip install -r requirements.txt
```

ffmpeg and ffprobe must be on PATH (`winget install Gyan.FFmpeg`).

Then add a Groq API key — either in **Settings** in the UI, or via the `GROQ_API_KEY` environment
variable, which takes priority. Keys are free at <https://console.groq.com/keys>.
The key is stored in `config.json`, which is gitignored and never written into source.

It works without a key too, but only for videos that already ship with subtitles.

## Run

**Web UI** — double-click `start.bat`, or:

```
python app.py
```

Paste one or more links (playlists work), hit **Generate**. Jobs run one at a time with logs streaming
live; click a job to expand its log, click a finished file to read it line by line in the page.

**Command line**

```
python cn.py                        watch the clipboard for links
python cn.py <link> [<link> ...]    process directly; playlists expand automatically
python cn.py --ui                   launch the web UI
python cn.py --lang ja <link>       treat this run as Japanese
```

Other flags: `--reading` / `--no-reading`, `--translate` / `--no-translate`,
`--model <name>`, `--turbo`, `--force` (ignore the "already processed" record).

## Settings

| Setting | What it does |
| --- | --- |
| Language | Chinese or Japanese. Switching also switches output directory, prompts, reading style, and line length. |
| Whisper model | `whisper-large-v3` is more accurate; `-turbo` is ~4x faster and cheaper but weaker on nuance. |
| Script | Force all output to Simplified or Traditional. Whisper is inconsistent about this, so forcing it is worth doing. Chinese only. |
| Reading style | Pinyin for Chinese; hiragana or romaji for Japanese. |
| Max chars per line | Longer lines get split at punctuation. Default 26 for Chinese, 24 for Japanese. |
| Chunk length | Seconds of audio per request. Lower it if uploads keep timing out. |
| Concurrent uploads | How many chunks transcribe at once. Raise for speed, lower if you hit rate limits. |
| English line | Runs a second translation pass over the cleaned subtitles, batched and with context. |
| Accept auto-generated subtitles | Whether to use YouTube's machine captions when no human ones exist. Usually worse than transcribing yourself, so off by default. |

## Layout

```
app.py              web server and job queue
cn.py               command-line entry point and clipboard watcher
web/                the UI (one HTML, one CSS, one JS)
cnsubs/
  languages.py      per-language definitions; adding a language is one entry here
  config.py         settings, from config.json and GROQ_API_KEY
  pipeline.py       yt-dlp, ffmpeg, Whisper, and the pipeline itself
  srt.py            SRT / VTT parsing and generation
  text.py           cleanup, Simplified/Traditional conversion, line breaking, readings
  translate.py      the optional English line
test_offline.py     runs with no network and no key
```

## Details that affect subtitle quality

A few things here matter a lot for sentence mining, so it's worth saying why:

- **Per-chunk time offsets are measured, not computed.** Cuts land on frame boundaries rather than
  exactly on the requested second, so every chunk's real duration is measured. Assume equal-length
  chunks instead and the drift compounds chunk by chunk — the longer the video, the worse it gets.
- **Whisper's stock hallucinations are filtered out** — the sign-off phrases it invents over silence
  ("字幕由…提供", "請不吝點贊訂閱", "ご視聴ありがとうございました"), plus the case where it falls into a
  decoding loop and repeats one phrase to the end.
- **Subtitles never overlap and are never zero-length**, both of which some players handle badly.
- **Pinyin is segmented with jieba before conversion**, so 了 reads as `le` rather than `liǎo`, 行 picks
  the right reading, and the tone sandhi on 不 and 一 comes out correctly. Grouping by word is also
  simply easier to read than loose syllables.
- **Japanese kana keeps existing katakana** — エピソード doesn't get flattened to えぴそーど.
- **No Simplified/Traditional conversion on Japanese**, which would mangle Japanese kanji.
