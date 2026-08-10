"""字幕生成器的本地网页界面。

    python app.py          -> 在 http://127.0.0.1:5000 启动并自动打开浏览器
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response

from cnsubs import analyze, config, languages, library, meta, pipeline, separate, srt, text
from cnsubs.text import HAS_KANA, HAS_OPENCC, HAS_PINYIN, HAS_SEGMENTER

HOST = "127.0.0.1"
PORT = int(os.environ.get("CNSUBS_PORT", "5000"))

app = Flask(__name__, static_folder=None)
WEB_DIR = Path(__file__).resolve().parent / "web"

# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------

JOBS: dict[str, dict] = {}
ORDER: list[str] = []
WORK_QUEUE: "queue.Queue[str]" = queue.Queue()
SUBSCRIBERS: list["queue.Queue[str]"] = []
LOCK = threading.Lock()


def broadcast(event: str, payload: dict) -> None:
    message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with LOCK:
        dead = []
        for sub in SUBSCRIBERS:
            try:
                sub.put_nowait(message)
            except queue.Full:
                dead.append(sub)
        for sub in dead:
            SUBSCRIBERS.remove(sub)


def public(job: dict) -> dict:
    """除内部字段外的全部内容，浏览器没必要看到那些内部字段。"""
    return {k: v for k, v in job.items() if k not in ("cancel", "cfg")}


def update(job: dict, **fields) -> None:
    job.update(fields)
    broadcast("job", public(job))


def add_log(job: dict, line: str) -> None:
    job["log"].append(line)
    del job["log"][:-400]
    broadcast("log", {"id": job["id"], "line": line})


def worker_loop() -> None:
    while True:
        job_id = WORK_QUEUE.get()
        job = JOBS.get(job_id)
        if not job:
            continue
        if job["cancel"].is_set():
            update(job, status="cancelled")
            continue

        update(job, status="running", started=time.time())
        try:
            result = pipeline.process(
                job["url"], job["cfg"],
                log=lambda line: add_log(job, str(line)),
                should_stop=job["cancel"].is_set,
            )
            update(job, status="done", result=result,
                   title=result["title"], finished=time.time())
        except pipeline.Cancelled:
            update(job, status="cancelled", finished=time.time())
            add_log(job, "[=] Cancelled.")
        except Exception as exc:
            update(job, status="failed", error=f"{type(exc).__name__}: {exc}",
                   finished=time.time())
            add_log(job, f"[!] {type(exc).__name__}: {exc}")


threading.Thread(target=worker_loop, daemon=True).start()


def _warm_segmenter() -> None:
    """jieba 第一次分词要先建前缀词典，得好几秒。

    那几秒如果落在用户第一次点「Insights」上，界面就干等着。
    开机时在后台先切一个字把它捂热，等真要用的时候已经就绪了。
    """
    try:
        text.reading_line("热身", "zh")
    except Exception:
        pass


threading.Thread(target=_warm_segmenter, daemon=True).start()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/app.js")
def script():
    return send_from_directory(WEB_DIR, "app.js")


@app.get("/style.css")
def style():
    return send_from_directory(WEB_DIR, "style.css")


@app.get("/api/state")
def state():
    cfg = config.load()
    flat = config.active(cfg)
    cfg["api_key"] = config.KEY_PLACEHOLDER if config.api_key(cfg) else ""
    flat.pop("api_key", None)
    with LOCK:
        jobs = [public(JOBS[i]) for i in ORDER if i in JOBS]
    return jsonify({
        "config": cfg,
        "active": flat,
        "languages": languages.summary(),
        "jobs": jobs,
        "env": {
            "ffmpeg_missing": pipeline.missing_dependencies(),
            "opencc": HAS_OPENCC,
            "pinyin": HAS_PINYIN,
            "segmenter": HAS_SEGMENTER,
            "kana": HAS_KANA,
            "demucs": separate.available(),
            "reading_ok": text.has_reading_support(flat["language"]),
            "key_from_env": bool(os.environ.get("GROQ_API_KEY", "").strip()),
            "output_dir": flat["output_dir"],
        },
    })


@app.get("/api/stream")
def stream():
    sub: "queue.Queue[str]" = queue.Queue(maxsize=2000)
    with LOCK:
        SUBSCRIBERS.append(sub)

    def generate():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield sub.get(timeout=20)
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with LOCK:
                if sub in SUBSCRIBERS:
                    SUBSCRIBERS.remove(sub)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs")
def create_jobs():
    body = request.get_json(force=True) or {}
    raw = body.get("urls", "")
    overrides = body.get("overrides") or {}

    urls = list(dict.fromkeys(
        m.group(0) for m in pipeline.URL_RE.finditer(raw if isinstance(raw, str) else "")
    ))
    if not urls:
        return jsonify({"error": "No YouTube links found in that text."}), 400

    # 现在就把设置拍下快照，这样中途切换语言也不会把已经排队的任务
    # 改到另一个语言上去。
    snapshot = config.active(config.load())
    snapshot.update({k: v for k, v in overrides.items() if k in config.SHARED_DEFAULTS})

    created = []
    for url in urls:
        try:
            videos = pipeline.expand(url)
        except Exception as exc:
            return jsonify({"error": f"Could not read {url}: {exc}"}), 400
        for video in videos:
            job = {
                "id": uuid.uuid4().hex[:10],
                "url": video["url"],
                "title": video["title"],
                "language": snapshot["language"],
                "status": "queued",
                "log": [],
                "result": None,
                "error": None,
                "cfg": snapshot,
                "cancel": threading.Event(),
            }
            with LOCK:
                JOBS[job["id"]] = job
                ORDER.append(job["id"])
            created.append(public(job))
            broadcast("job", public(job))
            WORK_QUEUE.put(job["id"])
    return jsonify({"jobs": created})


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "No such job"}), 404
    job["cancel"].set()
    if job["status"] == "queued":
        update(job, status="cancelled")
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/retry")
def retry(job_id):
    """把失败或取消的任务原样再排一次。

    用的是任务当初那份设置快照，不是现在的设置——失败多半是网络抖了一下，
    重来一次就好，不该因为期间换了语言就把它扔进另一个目录。
    """
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "No such job"}), 404
    if job["status"] not in ("failed", "cancelled"):
        return jsonify({"error": "That job has not finished failing yet."}), 400

    job["cancel"] = threading.Event()
    update(job, status="queued", error=None, result=None, started=None, finished=None)
    add_log(job, "[=] Retrying.")
    WORK_QUEUE.put(job_id)
    return jsonify({"ok": True})


@app.post("/api/jobs/clear")
def clear_finished():
    with LOCK:
        for job_id in list(ORDER):
            if JOBS.get(job_id, {}).get("status") in ("done", "failed", "cancelled"):
                JOBS.pop(job_id, None)
                ORDER.remove(job_id)
    return jsonify({"ok": True})


@app.post("/api/settings")
def settings():
    body = request.get_json(force=True) or {}
    cfg = config.load()

    for key, value in body.items():
        if key not in config.SHARED_DEFAULTS:
            continue
        if key == "api_key":
            # 留空、或者原样送回界面上那个占位符，都表示"保持原样"。
            if not isinstance(value, str) or value.strip() in ("", config.KEY_PLACEHOLDER):
                continue
            value = value.strip()
        if key == "language" and value not in languages.codes():
            continue
        cfg[key] = value

    # 分语言的设置以 {"languages": {"ja": {"output_dir": ...}}} 的形式传进来。
    for code, values in (body.get("languages") or {}).items():
        if code not in cfg["languages"] or not isinstance(values, dict):
            continue
        cfg["languages"][code].update(
            {k: v for k, v in values.items() if k in config.PER_LANGUAGE_KEYS}
        )

    config.save(cfg)
    safe = dict(cfg)
    safe["api_key"] = config.KEY_PLACEHOLDER if config.api_key(cfg) else ""
    flat = config.active(cfg)
    flat.pop("api_key", None)
    return jsonify({"config": safe, "active": flat})


@app.get("/api/models")
def models():
    key = config.api_key()
    if not key:
        return jsonify({"whisper": [], "chat": [], "error": "No API key"})
    try:
        from groq import Groq
        listed = Groq(api_key=key).models.list().data
        ids = sorted(m.id for m in listed)
    except Exception as exc:
        return jsonify({"whisper": [], "chat": [], "error": str(exc)})
    whisper = [i for i in ids if "whisper" in i]
    chat = [i for i in ids if not any(x in i for x in ("whisper", "tts", "guard", "vision"))]
    return jsonify({"whisper": whisper, "chat": chat})


def active_output_dir() -> Path:
    return Path(config.active(config.load())["output_dir"]).expanduser()


@app.get("/api/files")
def files():
    out = active_output_dir()
    if not out.exists():
        return jsonify({"files": []})
    table = meta.read(out)
    rows = []
    for path in sorted(out.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        info = table.get(path.name) or {}
        rows.append({"name": path.name, "size": path.stat().st_size,
                     "modified": path.stat().st_mtime,
                     # 列表上那几个小标记：有注音行、有英文行、走的音乐模式
                     "marks": [k for k in ("reading", "translation", "music") if info.get(k)],
                     "source": info.get("source", "")})
    return jsonify({"files": rows})


@app.get("/api/file")
def file_preview():
    name = Path(request.args.get("name", "")).name
    out = active_output_dir()
    path = out / name
    if not path.exists() or path.suffix.lower() != ".srt":
        return jsonify({"error": "File not found"}), 404
    cues = srt.parse(path.read_text(encoding="utf-8", errors="ignore"))
    # layout 告诉浏览器第二行是注音还是英文。生成时记下来的，没有记录
    # （以前做的文件）就发空的，那边自己去猜。
    return jsonify({"name": name, "path": str(path), "cues": cues[:2000],
                    "layout": meta.get(out, name)})


@app.get("/api/analyze")
def analyze_file():
    """一个文件的词频、用字和难度。"""
    name = Path(request.args.get("name", "")).name
    path = active_output_dir() / name
    if not path.exists() or path.suffix.lower() != ".srt":
        return jsonify({"error": "File not found"}), 404
    cues = srt.parse(path.read_text(encoding="utf-8", errors="ignore"))
    language = config.active(config.load())["language"]
    return jsonify({"name": name, **analyze.report(cues, language)})


@app.get("/api/search")
def search_library():
    """在当前语言的整个输出目录里找一个词。"""
    term = request.args.get("q", "")
    return jsonify(library.search(active_output_dir(), term))


@app.get("/api/phrases")
def phrases():
    """给小动物的气泡用：从你自己的字幕里挑出来的句子。"""
    flat = config.active(config.load())
    pool = library.phrase_pool(Path(flat["output_dir"]).expanduser(), flat["language"])
    return jsonify({"phrases": pool})


@app.post("/api/reveal")
def reveal():
    out = active_output_dir()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Could not create {out}: {exc}"}), 500

    # 这个请求跑在 Flask 的工作线程上，那里没人初始化过 COM，os.startfile
    # 走的 ShellExecuteW 因此可能直接不响应。改成起一个 explorer 进程，
    # 跟线程无关，稳当得多。（explorer 成功时也会返回 1，别去判它的退出码。）
    if sys.platform == "win32":
        launcher = ["explorer", str(out)]
    elif sys.platform == "darwin":
        launcher = ["open", str(out)]
    else:
        launcher = ["xdg-open", str(out)]

    try:
        subprocess.Popen(launcher, close_fds=True)
    except (OSError, ValueError) as exc:
        return jsonify({"error": f"Could not open {out}: {exc}"}), 500
    return jsonify({"ok": True, "path": str(out)})


def sweep_leftovers() -> None:
    """上一次跑到一半被强杀时，留在输出目录里的临时文件。"""
    cfg = config.load()
    for code in languages.codes():
        try:
            out = Path(config.active({**cfg, "language": code})["output_dir"]).expanduser()
            gone = pipeline.sweep_work_dirs(out)
            if gone:
                print(f"[=] Cleaned up {gone} leftover work folder(s) in {out}")
        except OSError:
            pass


def main():
    sweep_leftovers()
    missing = pipeline.missing_dependencies()
    if missing:
        print(f"[!] Not on PATH: {', '.join(missing)} - transcription will fail.")
    url = f"http://{HOST}:{PORT}"
    print(f"\n  Subtitle generator running at {url}")
    print("  Press Ctrl+C to stop.\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
