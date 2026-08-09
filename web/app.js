const $ = (id) => document.getElementById(id);
const jobs = new Map();
const openLogs = new Set();

let cfg = {};        // 完整设置，包含每种语言各自的配置
let active = {};     // 当前语言的设置，已压平
let langs = [];      // 服务端给出的语言定义
let models = { whisper: [], chat: [] };

let allFiles = [];   // 未过滤的文件列表
let cues = [];       // 当前预览的字幕
let cueIndex = -1;   // j/k 选中的那一条
let currentFile = "";
let sessionCount = 0;
let sessionStart = Date.now();

// 背景纯粹是本机的口味问题，不进 config.json，省得两台机器互相覆盖。
// 注意：init() 在文件顶上就跑了，这几个 const 必须待在它前面，否则 TDZ 报错。
const BG_KEY = "subs.background";
const BG_DEFAULT = { mode: "circuit", dim: 45, url: "", motion: "auto", theme: "anki" };
let bg = { ...BG_DEFAULT };

// 小工具：搜索框每敲一下就发一次请求／重排一次列表太浪费，压一压。
function debounce(fn, ms = 180) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const FALLBACK_WHISPER = ["whisper-large-v3", "whisper-large-v3-turbo"];
const FALLBACK_CHAT = ["llama-3.3-70b-versatile", "qwen/qwen3-32b", "moonshotai/kimi-k2-instruct"];

// 界面上那些方括号标签，跟着语言换。
const TAGS = {
  zh: { links: "【链接】", queue: "【队列】", output: "【输出】", preview: "【字幕】",
        settings: "【设置】", help: "【快捷键】", stats: "【统计】" },
  ja: { links: "【リンク】", queue: "【待ち】", output: "【出力】", preview: "【字幕】",
        settings: "【設定】", help: "【ショートカット】", stats: "【統計】" },
};
const GLYPHS = { zh: "字", ja: "か" };

// 熊猫说的话。点一下随机挑一句。
const SAYINGS = {
  zh: [
    ["别摸我", "bié mō wǒ", "don't poke me"],
    ["我在吃竹子", "wǒ zài chī zhúzi", "I'm eating bamboo"],
    ["加油！", "jiāyóu", "keep going!"],
    ["再看一集", "zài kàn yī jí", "one more episode"],
    ["你今天学了吗", "nǐ jīntiān xué le ma", "did you study today?"],
    ["慢慢来", "màn màn lái", "take it slow"],
    ["困了", "kùn le", "I'm sleepy"],
    ["好好休息", "hǎohāo xiūxi", "get some rest"],
    ["熟能生巧", "shú néng shēng qiǎo", "practice makes perfect"],
    ["再来一遍", "zài lái yī biàn", "one more time"],
  ],
  ja: [
    ["さわらないで", "sawaranaide", "don't touch me"],
    ["笹を食べてる", "sasa o tabeteru", "I'm eating bamboo"],
    ["がんばって！", "ganbatte", "you got this!"],
    ["もう一話だけ", "mō ichiwa dake", "just one more episode"],
    ["今日は勉強した？", "kyō wa benkyō shita?", "did you study today?"],
    ["ゆっくりでいいよ", "yukkuri de ii yo", "slow is fine"],
    ["ねむい", "nemui", "sleepy"],
    ["継続は力なり", "keizoku wa chikara nari", "persistence is strength"],
  ],
};

// ---------------------------------------------------------------- 启动

init();

async function init() {
  loadBackground();          // 先铺背景，免得先闪一下灰底
  await refreshState();
  await refreshFiles();
  loadModels();
  loadPhrases();
  connectStream();
  wireUp();
  startPanda();
  startClock();
}

function wireUp() {
  $("go").onclick = submit;
  $("urls").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
  });
  $("urls").addEventListener("input", countLinks);

  $("clear-done").onclick = async () => {
    await fetch("/api/jobs/clear", { method: "POST" });
    await refreshState();
    toast("Cleared finished jobs.");
  };
  $("open-folder").onclick = openFolder;
  $("file-search").addEventListener("input", debounce(renderFiles, 120));

  $("lookup-open").onclick = () => openLookup();
  $("lookup-close").onclick = () => ($("lookup-overlay").hidden = true);
  // 全库搜索要读文件，压得比本地筛选久一点
  $("lookup-input").addEventListener("input", debounce(runLookup, 320));

  $("settings-open").onclick = openSettings;
  $("settings-close").onclick = () => ($("settings-overlay").hidden = true);
  $("settings-save").onclick = saveSettings;
  $("preview-close").onclick = () => ($("preview-overlay").hidden = true);
  $("help-open").onclick = () => ($("help-overlay").hidden = false);
  $("help-close").onclick = () => ($("help-overlay").hidden = true);

  wireBackground();

  $("cue-search").addEventListener("input", debounce(renderCues, 140));
  $("toggle-insights").onclick = toggleInsights;
  $("cue-anki").onclick = ankiExport;
  $("toggle-reading").onclick = () => toggleCueClass("toggle-reading", "no-reading");
  $("toggle-trans").onclick = () => toggleCueClass("toggle-trans", "no-trans");
  $("toggle-study").onclick = () => {
    const on = $("toggle-study").classList.toggle("on");
    $("preview-cues").classList.toggle("study", on);
    // 关掉的时候把揭开过的行清一遍，下次开自测又是全糊的
    if (!on) for (const n of $("preview-cues").children) n.classList.remove("reveal");
    // 触屏上没有 hover，说明文字得跟着设备走，否则就是叫人去做做不到的事
    if (on) toast(`Study mode — ${canHover() ? "hover" : "tap"} a line to reveal it.`, "gold");
  };
  $("cue-copy").onclick = copyAllCues;
  $("cue-download").onclick = downloadCues;

  document.addEventListener("keydown", shortcuts);
  for (const overlay of document.querySelectorAll(".overlay")) {
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
  }

  for (const [id, key] of [["opt-existing", "prefer_existing_subs"], ["opt-auto", "allow_auto_subs"],
                           ["opt-reading", "reading"], ["opt-translate", "translate"]]) {
    $(id).onchange = () => patch({ [key]: $(id).checked });
  }
  $("opt-model").onchange = () => patch({ model: $("opt-model").value });

  // 把链接拖到窗口里也能用
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const text = e.dataTransfer.getData("text");
    if (!text) return;
    $("urls").value = ($("urls").value.trim() + "\n" + text).trim();
    countLinks();
    toast("Link dropped in.");
  });
}

function shortcuts(e) {
  if (e.key === "Escape") {
    for (const o of document.querySelectorAll(".overlay")) o.hidden = true;
    return;
  }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  const previewOpen = !$("preview-overlay").hidden;

  if (previewOpen && !typing && (e.key === "j" || e.key === "k")) {
    e.preventDefault();
    moveCue(e.key === "j" ? 1 : -1);
    return;
  }
  if (typing) return;

  if (e.key === "?" || (e.key === "/" && e.shiftKey)) { e.preventDefault(); $("help-overlay").hidden = false; }
  else if (e.key === "/") { e.preventDefault(); $("urls").focus(); }
  else if (e.key === "f") { e.preventDefault(); $("file-search").focus(); }
  else if (e.key === "g") { e.preventDefault(); openFolder(); }
  else if (e.key === "s") { e.preventDefault(); openSettings(); }
  else if (e.key === "l") { e.preventDefault(); openLookup(); }
  else if (e.key === "t") { e.preventDefault(); cycleTheme(); }
  else if (e.key === "i" && previewOpen) { e.preventDefault(); toggleInsights(); }
}

// ---------------------------------------------------------------- 状态

async function refreshState() {
  const data = await (await fetch("/api/state")).json();
  cfg = data.config;
  active = data.active;
  langs = data.languages;
  renderLanguages();
  renderTags();
  renderHealth(data.env);
  applyConfigToControls();
  jobs.clear();
  for (const job of data.jobs) jobs.set(job.id, job);
  renderQueue();
}

function currentLang() {
  return langs.find((l) => l.code === cfg.language) || langs[0] || {};
}

// 标签文字跟着语言换，换的时候闪一下。
function renderTags() {
  const set = TAGS[cfg.language] || TAGS.zh;
  for (const node of document.querySelectorAll("[data-tag]")) {
    const next = set[node.dataset.tag];
    if (!next || node.textContent === next) continue;
    node.textContent = next;
    node.classList.remove("swap");
    void node.offsetWidth;          // 重新触发动画
    node.classList.add("swap");
  }
}

function renderLanguages() {
  $("langs").innerHTML = langs.map((l) => `
    <button class="lang ${l.code === cfg.language ? "on" : ""}" data-lang="${esc(l.code)}"
            title="${esc(l.name)}">
      <span class="lang-native">${esc(l.native)}</span>
      <span class="lang-name">${esc(l.name)}</span>
    </button>`).join("");
  for (const btn of $("langs").children) btn.onclick = () => switchLanguage(btn.dataset.lang);

  const lang = currentLang();
  const glyph = $("brand-glyph");
  const next = GLYPHS[lang.code] || "字";
  if (glyph.textContent !== next) {
    glyph.classList.remove("flip"); void glyph.offsetWidth; glyph.classList.add("flip");
  }
  glyph.textContent = next;
  // 中文看竹林里的熊猫，日文看池子里的锦鲤
  $("scene").dataset.mascot = lang.code === "ja" ? "ja" : "zh";
  // 汉字字形中日有别，字体整组跟着语言换
  document.documentElement.dataset.lang = lang.code || "zh";
  applyTheme();   // 主题设成 auto 时要跟着语言换一套色
  $("brand-sub").textContent = `${lang.name || ""} subtitles for asbplayer`;
  $("opt-reading-label").textContent = `${lang.reading_label || "Reading"} line`;
  $("toggle-reading").textContent = lang.reading_label || "Reading";
}

async function switchLanguage(code) {
  if (code === cfg.language) return;
  await patch({ language: code });
  await refreshState();
  await refreshFiles();
  await loadPhrases();        // 另一个语言另一个目录，句子池得重新捞
  setHint(`Switched to ${currentLang().name} — files go to ${active.output_dir}`);
  speak();
}

function applyConfigToControls() {
  $("opt-existing").checked = !!cfg.prefer_existing_subs;
  $("opt-auto").checked = !!cfg.allow_auto_subs;
  $("opt-reading").checked = !!cfg.reading;
  $("opt-translate").checked = !!cfg.translate;
  fillSelect($("opt-model"), models.whisper.length ? models.whisper : FALLBACK_WHISPER, cfg.model);
}

async function openFolder() {
  try {
    const res = await fetch("/api/reveal", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setHint(data.error || "Could not open the output folder.", true); toast(data.error || "Could not open the folder.", "bad"); }
    else toast(`Opened ${data.path || "the output folder"}.`);
  } catch (err) {
    setHint(String(err), true);
  }
}

async function patch(fields) {
  const data = await (await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  })).json();
  cfg = data.config;
  active = data.active;
}

function renderHealth(env) {
  const pills = [];
  if (env.ffmpeg_missing.length) pills.push(["bad", `${env.ffmpeg_missing.join(", ")} missing`]);
  else pills.push(["ok", "ffmpeg"]);
  pills.push(cfg.api_key ? ["ok", env.key_from_env ? "key (env)" : "key"] : ["bad", "no key"]);
  if (!env.reading_ok) pills.push(["warn", `${currentLang().reading_label || "reading"} support missing`]);
  else if (cfg.language === "zh" && !env.segmenter) pills.push(["warn", "jieba missing"]);
  if (cfg.language === "zh" && !env.opencc) pills.push(["warn", "opencc missing"]);
  $("health").innerHTML = pills
    .map(([kind, label]) => `<span class="pill ${kind}">${esc(label)}</span>`).join("");
}

async function loadModels() {
  try {
    const data = await (await fetch("/api/models")).json();
    if (data.whisper?.length) models = data;
  } catch {}
  applyConfigToControls();
}

// ---------------------------------------------------------------- 提交

function countLinks() {
  const found = ($("urls").value.match(/https?:\/\/[^\s]+/g) || []).length;
  $("link-count").textContent = found ? `${found} link${found > 1 ? "s" : ""}` : "";
}

async function submit() {
  const urls = $("urls").value.trim();
  if (!urls) { setHint("Paste a YouTube link first.", true); shake($("urls")); return; }

  $("go").disabled = true;
  setHint("Reading the links...");
  try {
    const res = await fetch("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    const data = await res.json();
    if (!res.ok) { setHint(data.error || "Something went wrong.", true); toast(data.error || "Something went wrong.", "bad"); return; }
    $("urls").value = "";
    countLinks();
    setHint(`Queued ${data.jobs.length} ${currentLang().name} video(s).`);
    toast(`Queued ${data.jobs.length} video${data.jobs.length > 1 ? "s" : ""}.`);
    for (const job of data.jobs) jobs.set(job.id, job);
    renderQueue();
  } catch (err) {
    setHint(String(err), true);
  } finally {
    $("go").disabled = false;
  }
}

function setHint(message, bad = false) {
  const hint = $("compose-hint");
  hint.textContent = message;
  hint.classList.toggle("bad", bad);
}

function shake(node) {
  node.animate(
    [{ transform: "translateX(0)" }, { transform: "translateX(-6px)" },
     { transform: "translateX(6px)" }, { transform: "translateX(0)" }],
    { duration: 240, easing: "ease-in-out" });
}

// ---------------------------------------------------------------- 实时更新

function connectStream() {
  const source = new EventSource("/api/stream");
  source.addEventListener("job", (e) => {
    const job = JSON.parse(e.data);
    const previous = jobs.get(job.id);
    job.log = job.log?.length ? job.log : previous?.log || [];
    const isNew = !previous;
    jobs.set(job.id, job);
    // 播放列表几十条时，每来一个事件就把整个队列重建一遍太浪费，
    // 而且会把展开的日志滚动位置弄丢。只有新增任务才整体重排。
    if (isNew) renderQueue(); else refreshJob(job);
    if (job.status === "done" && previous?.status !== "done") {
      refreshFiles();
      loadPhrases();          // 新字幕落地了，让小动物有新句子可说
      sessionCount += 1;
      bumpStat($("stat-session"), sessionCount);
      toast(`Finished: ${job.title || "video"}`, "gold");
      cheer();
    }
    if (job.status === "failed" && previous?.status !== "failed") {
      toast(job.error || "A job failed.", "bad");
    }
  });
  source.addEventListener("log", (e) => {
    const { id, line } = JSON.parse(e.data);
    const job = jobs.get(id);
    if (!job) return;
    job.log = job.log || [];
    job.log.push(line);
    appendLogLine(id, line);
  });
}

// ---------------------------------------------------------------- 队列

function renderQueue() {
  const list = [...jobs.values()];
  $("queue-empty").hidden = list.length > 0;
  const container = $("queue");
  container.innerHTML = "";

  let running = false;

  for (const job of list) {
    if (job.status === "running") running = true;
    const node = document.createElement("div");
    node.className = "job";
    node.dataset.id = job.id;

    const open = openLogs.has(job.id);
    node.innerHTML = `
      <div class="job-head">
        <span class="dot ${job.status}"></span>
        <div class="job-title">
          <span class="job-name">${esc(job.title || job.url)}</span>
          <div class="job-sub">${statusLine(job)}</div>
        </div>
        ${elapsed(job)}
        ${job.language ? `<span class="pill">${esc(langName(job.language))}</span>` : ""}
        ${actionButtons(job)}
      </div>
      ${job.status === "running" ? `<div class="job-bar"><i></i></div>` : ""}
      ${open ? `<div class="job-log" id="log-${job.id}"></div>` : ""}`;

    node.querySelector(".job-head").onclick = (e) => {
      if (e.target.closest("button")) return;
      openLogs.has(job.id) ? openLogs.delete(job.id) : openLogs.add(job.id);
      renderQueue();
    };
    wireJobButtons(node, job);
    container.appendChild(node);

    if (open) {
      const log = $(`log-${job.id}`);
      log.innerHTML = (job.log || []).map(logLine).join("");
      log.scrollTop = log.scrollHeight;
    }
  }

  setPandaState(running ? "working" : "");
}

// 只更新一条任务，不动其它节点。展开的日志、滚动位置都保住。
function refreshJob(job) {
  const node = document.querySelector(`.job[data-id="${job.id}"]`);
  if (!node) { renderQueue(); return; }

  node.querySelector(".dot").className = `dot ${job.status}`;
  node.querySelector(".job-sub").innerHTML = statusLine(job);
  node.querySelector(".job-name").textContent = job.title || job.url;

  // 时间、进度条、按钮会随状态出现或消失，这几处按需重建
  const head = node.querySelector(".job-head");
  const oldTime = node.querySelector(".job-time");
  const newTime = elapsed(job);
  if (oldTime && !newTime) oldTime.remove();
  else if (oldTime && newTime) oldTime.outerHTML = newTime;

  const bar = node.querySelector(".job-bar");
  if (job.status === "running" && !bar) {
    head.insertAdjacentHTML("afterend", `<div class="job-bar"><i></i></div>`);
  } else if (job.status !== "running" && bar) {
    bar.remove();
  }

  const actions = node.querySelector("[data-cancel], [data-view]");
  const markup = actionButtons(job);
  if (actions && !markup) actions.remove();
  else if (actions) actions.outerHTML = markup;
  else if (markup) head.insertAdjacentHTML("beforeend", markup);

  wireJobButtons(node, job);
  setPandaState([...jobs.values()].some((j) => j.status === "running") ? "working" : "");
}

function wireJobButtons(node, job) {
  const cancelBtn = node.querySelector("[data-cancel]");
  if (cancelBtn) cancelBtn.onclick = () => fetch(`/api/jobs/${job.id}/cancel`, { method: "POST" });
  const viewBtn = node.querySelector("[data-view]");
  if (viewBtn) viewBtn.onclick = () => preview(viewBtn.dataset.view);
}

function elapsed(job) {
  if (!job.started) return "";
  const end = job.finished || Date.now() / 1000;
  const secs = Math.max(0, Math.round(end - job.started));
  const label = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;
  return `<span class="job-time">${label}</span>`;
}

function langName(code) {
  return (langs.find((l) => l.code === code) || {}).name || code;
}

function statusLine(job) {
  if (job.status === "done" && job.result) {
    const via = job.result.source === "youtube" ? "from the video's own subtitles" : "transcribed";
    return `${job.result.lines} lines · ${via}`;
  }
  if (job.status === "failed") return esc(job.error || "Failed");
  return { queued: "Waiting", running: "Working...", cancelled: "Cancelled" }[job.status] || job.status;
}

function actionButtons(job) {
  if (job.status === "queued" || job.status === "running") {
    return `<button class="ghost small" data-cancel>Cancel</button>`;
  }
  if (job.status === "done" && job.result && job.language === cfg.language) {
    const name = job.result.path.split(/[\\/]/).pop();
    return `<button class="ghost small" data-view="${esc(name)}">View</button>`;
  }
  return "";
}

function appendLogLine(id, line) {
  const log = $(`log-${id}`);
  if (!log) return;
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.insertAdjacentHTML("beforeend", logLine(line));
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function logLine(line) {
  const cls = line.includes("[!]") ? "err" : line.includes("[✓]") ? "ok" : "";
  return `<div class="${cls}">${esc(line)}</div>`;
}

// 队列里的计时每秒刷新一次，不然停在那儿像是卡住了。
setInterval(() => {
  for (const node of document.querySelectorAll(".job")) {
    const job = jobs.get(node.dataset.id);
    if (!job || job.status !== "running" || !job.started) continue;
    const time = node.querySelector(".job-time");
    if (time) {
      const secs = Math.round(Date.now() / 1000 - job.started);
      time.textContent = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;
    }
  }
}, 1000);

// ---------------------------------------------------------------- 文件

async function refreshFiles() {
  const data = await (await fetch("/api/files")).json();
  allFiles = data.files;
  bumpStat($("stat-files"), allFiles.length);
  renderFiles();
}

function renderFiles() {
  const term = $("file-search").value.trim().toLowerCase();
  const shown = term ? allFiles.filter((f) => f.name.toLowerCase().includes(term)) : allFiles;

  $("files-empty").hidden = shown.length > 0;
  $("files-empty").textContent = term
    ? "Nothing matches that."
    : `Nothing here yet — ${currentLang().name || ""} subtitles land in this folder.`;

  $("files").innerHTML = shown.map((f) => `
    <li data-name="${esc(f.name)}">
      <span class="name">${highlight(f.name, term)}</span>
      <span class="meta">${new Date(f.modified * 1000).toLocaleDateString()} · ${kb(f.size)}</span>
    </li>`).join("");
  for (const li of $("files").children) li.onclick = () => preview(li.dataset.name);
}

function kb(bytes) {
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function highlight(value, term) {
  if (!term) return esc(value);
  const index = value.toLowerCase().indexOf(term);
  if (index < 0) return esc(value);
  return esc(value.slice(0, index)) + "<mark>" + esc(value.slice(index, index + term.length)) +
         "</mark>" + esc(value.slice(index + term.length));
}

// ---------------------------------------------------------------- 预览

async function preview(name) {
  const res = await fetch(`/api/file?name=${encodeURIComponent(name)}`);
  if (!res.ok) { toast("Could not open that file.", "bad"); return; }
  const data = await res.json();
  currentFile = data.name;
  cues = data.cues;
  cueIndex = -1;
  // 换文件了，上一份分析作废，面板也收回去
  insights = null;
  $("insights").hidden = true;
  $("toggle-insights").classList.remove("on");
  $("preview-title").textContent = data.name;
  $("preview-path").textContent = data.path;
  $("cue-search").value = "";
  renderCues();
  $("preview-overlay").hidden = false;

  const lines = cues.length;
  bumpStat($("stat-lines"), lines);
}

function renderCues() {
  const term = $("cue-search").value.trim().toLowerCase();
  const shown = term
    ? cues.map((c, i) => ({ ...c, i })).filter((c) => c.text.toLowerCase().includes(term))
    : cues.map((c, i) => ({ ...c, i }));

  $("cue-count").textContent = term
    ? `${shown.length} of ${cues.length} lines match`
    : `${cues.length} lines`;

  $("preview-cues").innerHTML = shown.map((cue) => {
    const [head, ...rest] = cue.text.split("\n");
    // 第二行是注音，第三行才是英文——生成时就是这个顺序。
    const reading = rest.length > 1 ? rest[0] : (looksLikeReading(rest[0]) ? rest[0] : "");
    const trans = rest.length > 1 ? rest.slice(1).join(" ") : (reading ? "" : rest[0] || "");
    return `<div class="cue" data-i="${cue.i}">
      <time>${clock(cue.start)}</time>
      <div>
        <div class="zh">${highlight(head, term)}</div>
        ${reading ? `<div class="reading">${highlight(reading, term)}</div>` : ""}
        ${trans ? `<div class="trans">${highlight(trans, term)}</div>` : ""}
      </div>
    </div>`;
  }).join("");

  for (const node of $("preview-cues").children) {
    node.onclick = () => {
      // 自测模式下先当「揭开」用：没有鼠标的机器全靠这一下，
      // 揭开之后再点才是复制，不然想看一眼答案就得被塞一条 toast。
      if ($("preview-cues").classList.contains("study") && !node.classList.contains("reveal")) {
        node.classList.add("reveal");
        return;
      }
      const line = cues[Number(node.dataset.i)].text.split("\n")[0];
      copy(line);
      node.classList.remove("copied"); void node.offsetWidth; node.classList.add("copied");
      toast("Copied that line.");
    };
  }
}

// 触屏／笔没有 hover。matchMedia 比 'ontouchstart' in window 靠谱：
// 装了触摸屏的笔记本两样都有，这条问的是「当前指针能不能悬停」。
function canHover() {
  return matchMedia("(hover: hover)").matches;
}

// 注音行没有汉字/假名，只有字母和声调符号——用这个把它跟英文行分开。
function looksLikeReading(line) {
  if (!line) return false;
  return !/[\u4e00-\u9fff\u3040-\u30ff]/.test(line) && /[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]/i.test(line);
}

function moveCue(step) {
  const nodes = [...$("preview-cues").children];
  if (!nodes.length) return;
  cueIndex = Math.max(0, Math.min(nodes.length - 1, cueIndex + step));
  for (const n of nodes) n.classList.remove("sel");
  const node = nodes[cueIndex];
  node.classList.add("sel");
  node.scrollIntoView({ block: "center", behavior: "smooth" });
}

function toggleCueClass(buttonId, className) {
  const on = $(buttonId).classList.toggle("on");
  $("preview-cues").classList.toggle(className, !on);
}

function copyAllCues() {
  copy(cues.map((c) => c.text).join("\n\n"));
  toast(`Copied ${cues.length} lines.`);
}

// 直接从预览数据重建 SRT，不用再问服务端要一次。
function downloadCues() {
  const body = cues.map((c, i) =>
    `${i + 1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${c.text}\n`).join("\n");
  download(body, currentFile || "subtitles.srt");
  toast("Downloaded.");
}

// ---------------------------------------------------------------- 词频

let insights = null;        // 当前文件的分析结果，换文件就作废

async function toggleInsights() {
  const panel = $("insights");
  const on = $("toggle-insights").classList.toggle("on");
  panel.hidden = !on;
  if (!on || insights) return;

  $("insight-stats").innerHTML = `<p class="insight-note">Counting…</p>`;
  try {
    const res = await fetch(`/api/analyze?name=${encodeURIComponent(currentFile)}`);
    if (!res.ok) throw new Error("analyze failed");
    insights = await res.json();
  } catch {
    $("insight-stats").innerHTML = `<p class="insight-note">Could not analyse that file.</p>`;
    return;
  }
  renderInsights();
}

function renderInsights() {
  if (!insights) return;
  const s = insights.stats;
  const mins = Math.round(s.seconds / 60);

  $("insight-stats").innerHTML = `
    <div><dt>Lines</dt><dd>${s.lines}</dd></div>
    <div><dt>Length</dt><dd>${mins}m</dd></div>
    <div><dt>Chars/min</dt><dd>${s.chars_per_minute}</dd></div>
    <div><dt>Unique ${cfg.language === "ja" ? "漢字" : "汉字"}</dt><dd>${s.unique_chars}</dd></div>
    <div><dt>Avg line</dt><dd>${s.avg_line_chars}</dd></div>
    <div class="level"><dt>Pace</dt><dd>${esc(s.difficulty.level)}</dd></div>
    <p class="insight-note">${esc(s.difficulty.note)}</p>`;

  const words = insights.vocabulary;
  $("insight-words").innerHTML = words.length
    ? words.map((w) => `
        <li data-term="${esc(w.word)}" title="Filter this file for ${esc(w.word)}">
          <span class="w">${esc(w.word)}</span>
          ${w.reading ? `<span class="r">${esc(w.reading)}</span>` : ""}
          <span class="n">${w.count}</span>
        </li>`).join("")
    : `<li class="n">No repeated words found.</li>`;

  // 单字榜只对中文有意义，日文的汉字混着假名，看着容易误导
  const charsCol = $("insight-chars-col");
  charsCol.hidden = cfg.language !== "zh";
  $("insight-chars").innerHTML = insights.characters.map((c) => `
    <li data-term="${esc(c.char)}" title="Filter this file for ${esc(c.char)}">
      <span class="w">${esc(c.char)}</span><span class="n">${c.count}</span>
    </li>`).join("");

  // 点一个词，就把这个文件筛到只剩含它的句子
  for (const list of [$("insight-words"), $("insight-chars")]) {
    for (const li of list.children) {
      if (!li.dataset.term) continue;
      li.onclick = () => {
        $("cue-search").value = li.dataset.term;
        renderCues();
        $("preview-cues").scrollTop = 0;
      };
    }
  }
}

// ---------------------------------------------------------------- 导出 Anki

// 制表符分隔，一行一张卡。Anki 导入时字段依次是：
// 原文 / 注音 / 翻译 / 出处。字段里出现制表符或换行会把行拆坏，先换掉。
function ankiExport() {
  if (!cues.length) { toast("Nothing to export.", "bad"); return; }
  const safe = (v) => (v || "").replace(/[\t\r\n]+/g, " ").trim();

  const rows = cues.map((c) => {
    const [head, ...rest] = c.text.split("\n");
    const reading = rest.length > 1 ? rest[0] : (looksLikeReading(rest[0]) ? rest[0] : "");
    const trans = rest.length > 1 ? rest.slice(1).join(" ") : (reading ? "" : rest[0] || "");
    return [safe(head), safe(reading), safe(trans),
            `${safe(currentFile)} @ ${clock(c.start)}`].join("\t");
  });

  // 头两行是 Anki 的导入指令，它会自己吃掉，不会变成卡片。
  const body = ["#separator:tab", "#html:false", ...rows].join("\n");
  download(body, (currentFile || "subtitles").replace(/\.srt$/i, "") + "-anki.txt");
  toast(`Exported ${rows.length} cards for Anki.`, "gold");
}

function download(body, filename) {
  const blob = new Blob([body], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ---------------------------------------------------------------- 全库查词

function openLookup(seed = "") {
  $("lookup-overlay").hidden = false;
  if (seed) $("lookup-input").value = seed;
  $("lookup-input").focus();
  $("lookup-input").select();
  if ($("lookup-input").value.trim()) runLookup();
}

async function runLookup() {
  const term = $("lookup-input").value.trim();
  const hits = $("lookup-hits");
  const empty = $("lookup-empty");

  if (!term) {
    hits.innerHTML = "";
    $("lookup-count").textContent = "";
    empty.hidden = false;
    empty.textContent = "Type something to search your whole library.";
    return;
  }

  $("lookup-count").textContent = "Searching…";
  let data;
  try {
    data = await (await fetch(`/api/search?q=${encodeURIComponent(term)}`)).json();
  } catch {
    $("lookup-count").textContent = "Search failed.";
    return;
  }

  empty.hidden = data.hits.length > 0;
  empty.textContent = `No “${term}” anywhere in ${data.files_searched} file(s) yet.`;
  $("lookup-count").textContent = data.hits.length
    ? `${data.hits.length}${data.truncated ? "+" : ""} in ${data.files_searched} file(s)`
    : "";

  hits.innerHTML = data.hits.map((h) => `
    <div class="cue hit" data-file="${esc(h.file)}" data-start="${h.start}">
      <time>${clock(h.start)}</time>
      <div>
        <div class="hit-file"><b>${esc(h.file)}</b></div>
        <div class="zh">${highlight(h.text, term.toLowerCase())}</div>
      </div>
    </div>`).join("");

  // 点一条就跳到那个文件的那一句
  for (const node of hits.children) {
    node.onclick = async () => {
      $("lookup-overlay").hidden = true;
      await preview(node.dataset.file);
      jumpToTime(Number(node.dataset.start));
    };
  }
}

function jumpToTime(start) {
  const index = cues.findIndex((c) => Math.abs(c.start - start) < 0.01);
  if (index < 0) return;
  const node = $("preview-cues").querySelector(`[data-i="${index}"]`);
  if (!node) return;
  for (const n of $("preview-cues").children) n.classList.remove("sel");
  node.classList.add("sel");
  cueIndex = [...$("preview-cues").children].indexOf(node);
  node.scrollIntoView({ block: "center" });
}

function stamp(seconds) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:` +
         `${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

function copy(value) {
  navigator.clipboard?.writeText(value).catch(() => {});
}

// ---------------------------------------------------------------- 设置

function openSettings() {
  const lang = currentLang();

  $("set-key").value = "";
  $("key-note").textContent = cfg.api_key
    ? "A key is saved. Leave this blank to keep it."
    : "No key yet — transcription will not run without one.";

  $("set-out-lang").textContent = `· ${lang.name || ""}`;
  $("set-prompt-lang").textContent = `· ${lang.name || ""}`;
  $("set-out").value = active.output_dir || "";
  $("set-prompt").value = active.prompt || "";
  $("set-chars").value = active.max_chars_per_line;

  fillSelect($("set-model"), models.whisper.length ? models.whisper : FALLBACK_WHISPER, cfg.model);
  fillSelect($("set-translate-model"), models.chat.length ? models.chat : FALLBACK_CHAT, cfg.translate_model);

  $("row-variant").hidden = !lang.has_script_variant;
  $("set-variant").value = cfg.script_variant ?? "s";

  const styles = lang.reading_styles || {};
  $("row-reading-style").hidden = Object.keys(styles).length < 2;
  $("set-reading-style").innerHTML = Object.entries(styles)
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  if (active.reading_style) $("set-reading-style").value = active.reading_style;

  $("set-chunk").value = cfg.chunk_seconds;
  $("set-parallel").value = cfg.parallel_chunks;
  syncBackgroundControls();   // 背景是即时生效的，不等 Save
  $("settings-hint").textContent = "";
  $("settings-overlay").hidden = false;
}

async function saveSettings() {
  const body = {
    model: $("set-model").value,
    translate_model: $("set-translate-model").value,
    script_variant: $("set-variant").value,
    chunk_seconds: Number($("set-chunk").value),
    parallel_chunks: Number($("set-parallel").value),
    languages: {
      [cfg.language]: {
        output_dir: $("set-out").value.trim(),
        prompt: $("set-prompt").value,
        max_chars_per_line: Number($("set-chars").value),
        reading_style: $("set-reading-style").value || active.reading_style,
      },
    },
  };
  const key = $("set-key").value.trim();
  if (key) body.api_key = key;

  await patch(body);
  await refreshState();
  await refreshFiles();
  if (key) loadModels();
  $("settings-hint").textContent = "Saved.";
  toast("Settings saved.");
  setTimeout(() => ($("settings-overlay").hidden = true), 500);
}

function fillSelect(select, options, selected) {
  const values = [...new Set([...options, selected].filter(Boolean))];
  select.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  if (selected) select.value = selected;
}

// ---------------------------------------------------------------- 小东西

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("toasts").appendChild(node);
  setTimeout(() => {
    node.classList.add("out");
    setTimeout(() => node.remove(), 260);
  }, 3200);
}

function bumpStat(node, value) {
  if (!node) return;
  const before = node.textContent;
  node.textContent = value;
  if (String(before) === String(value)) return;
  node.classList.remove("bump"); void node.offsetWidth; node.classList.add("bump");
}

function startClock() {
  const tick = () => {
    const now = new Date();
    $("rail-clock").textContent =
      `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const mins = Math.floor((Date.now() - sessionStart) / 60000);
    $("rail-session").textContent = mins >= 60
      ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
  };
  tick();
  setInterval(tick, 10000);
}

// ---------------------------------------------------------------- 背景

function loadBackground() {
  try {
    const saved = JSON.parse(localStorage.getItem(BG_KEY) || "{}");
    bg = { ...BG_DEFAULT, ...saved };
  } catch { bg = { ...BG_DEFAULT }; }
  applyBackground();
}

// 只放行 http(s) 和 data:image。别的（javascript:、本地磁盘路径之类）一律当没设：
// 前者不能往 url() 里塞，后者浏览器根本不给读。
function bgImageValue(url) {
  if (!/^(https?:\/\/|data:image\/)/i.test(url || "")) return "";
  return `url("${url.replace(/["\\]/g, "\\$&")}")`;
}

// 现在动的东西只剩 transform 和 opacity，全在合成器上跑，几乎不占主线程，
// 所以 auto 只拦真正的老机器（双核／2G）。系统开了「减少动态效果」一律照办。
function motionOff() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return true;
  if (bg.motion === "off") return true;
  if (bg.motion === "full") return false;
  const cores = navigator.hardwareConcurrency || 8;
  const mem = navigator.deviceMemory || 8;
  return cores <= 2 || mem <= 2;
}

// 主题只是换一组 CSS 变量。"auto" 跟着当前语言走：中文偏朱红，日文偏藏青。
function applyTheme() {
  const choice = bg.theme || "anki";
  const resolved = choice === "auto"
    ? (cfg.language === "ja" ? "lang-ja" : "lang-zh")
    : choice;
  // anki 就是 :root 里那套默认值，不需要额外属性
  if (resolved === "anki") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = resolved;
}

const THEMES = ["anki", "auto", "ink", "matcha", "sakura", "nord", "terminal"];

// 按 t 一路换过去，比开设置面板快
function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(bg.theme) + 1) % THEMES.length];
  bg.theme = next;
  applyTheme(); saveBackground();
  if (!$("settings-overlay").hidden) syncBackgroundControls();
  toast(`Theme: ${next}`);
}

function applyBackground() {
  const image = bgImageValue(bg.url);
  // 选了图片却没有能用的图片，就退回线路板，别留一块黑的
  const mode = bg.mode === "image" && !image ? "circuit" : bg.mode;
  $("backdrop").dataset.mode = mode;
  document.documentElement.dataset.motion = motionOff() ? "off" : "on";
  applyTheme();
  document.documentElement.style.setProperty("--bg-dim", String(bg.dim / 100));
  $("backdrop-img").style.setProperty("--bg-img", image || "none");
}

function saveBackground() {
  try {
    localStorage.setItem(BG_KEY, JSON.stringify(bg));
  } catch {
    toast("Couldn't save the background — browser storage is full.", "bad");
  }
}

function syncBackgroundControls() {
  $("set-bg-mode").value = bg.mode;
  $("set-bg-dim").value = bg.dim;
  $("set-theme").value = bg.theme;
  $("set-motion").value = bg.motion;
  $("motion-note").textContent = motionOff()
    ? "Animation is off — nothing on the page moves, so the browser can idle."
    : "Everything moves. Turn this off if the machine feels slow.";
  $("set-bg-url").value = bg.url.startsWith("data:") ? "" : bg.url;
  $("bg-note").textContent = bg.url.startsWith("data:")
    ? "A picked image is loaded. Stored in this browser only."
    : "Stored in this browser only — it never goes near config.json.";
  $("row-bg-image").hidden = bg.mode !== "image";
  $("row-bg-dim").hidden = bg.mode === "plain";
}

function wireBackground() {
  $("set-bg-mode").onchange = () => {
    bg.mode = $("set-bg-mode").value;
    applyBackground(); saveBackground(); syncBackgroundControls();
  };
  $("set-bg-dim").oninput = () => {
    bg.dim = Number($("set-bg-dim").value);
    applyBackground();
  };
  $("set-bg-dim").onchange = saveBackground;

  $("set-bg-url").onchange = () => {
    const value = $("set-bg-url").value.trim();
    if (value && !bgImageValue(value)) {
      toast("That needs to be an http(s) image URL — for a file on disk, use Choose file.", "bad");
      return;
    }
    bg.url = value;
    if (value) bg.mode = "image";
    applyBackground(); saveBackground(); syncBackgroundControls();
  };

  $("set-theme").onchange = () => {
    bg.theme = $("set-theme").value;
    applyTheme(); saveBackground();
    toast(`Theme: ${$("set-theme").selectedOptions[0].textContent.split("—")[0].trim()}`);
  };

  $("set-motion").onchange = () => {
    bg.motion = $("set-motion").value;
    applyBackground(); saveBackground(); syncBackgroundControls();
  };

  // 页面看不见时把动画停掉，别在后台白烧 CPU。
  // 初始值也得设——页面可能就是在后台加载的，光等 change 事件等不到。
  const idle = () => (document.documentElement.dataset.idle = document.hidden ? "1" : "0");
  document.addEventListener("visibilitychange", idle);
  idle();

  $("bg-file-btn").onclick = () => $("bg-file").click();
  $("bg-file").onchange = () => {
    const file = $("bg-file").files[0];
    if (!file) return;
    // localStorage 大概只有 5MB，大图直接塞会炸，先拦一道
    if (file.size > 3_000_000) {
      toast("That image is over 3 MB — use a URL instead.", "bad");
      $("bg-file").value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      bg.url = String(reader.result);
      bg.mode = "image";
      applyBackground(); saveBackground(); syncBackgroundControls();
      toast("Background set.");
    };
    reader.readAsDataURL(file);
    $("bg-file").value = "";
  };

  $("bg-clear").onclick = () => {
    bg.url = "";
    bg.mode = "circuit";
    applyBackground(); saveBackground(); syncBackgroundControls();
    toast("Back to the circuit.");
  };
}

// ---------------------------------------------------------------- 熊猫

let pokes = 0;
let lastActivity = Date.now();
let pandaState = "";

// 两只都在 DOM 里，只是按语言显示其中一只。状态类干脆两只都加，
// 免得切语言时还要把状态搬过去。
function mascots() {
  return [$("panda"), $("koi-btn")];
}
function mascotEl() {
  return cfg.language === "ja" ? $("koi-btn") : $("panda");
}
function mascotName() {
  return cfg.language === "ja" ? "koi" : "panda";
}

function startPanda() {
  for (const node of mascots()) {
    node.onclick = (e) => {
      lastActivity = Date.now();
      pokes += 1;
      wake();
      speak();
      burst(e.clientX, e.clientY);

      if (pokes % 10 === 0) {
        node.classList.add("spin");
        setTimeout(() => node.classList.remove("spin"), 1100);
        toast(`${pokes} pokes. The ${mascotName()} remembers.`, "gold");
      } else {
        node.classList.add("poke");
        setTimeout(() => node.classList.remove("poke"), 500);
      }
    };
  }

  // 随机眨眼，间隔不固定，固定了就像机器。鲤鱼没眼皮，不参与。
  const panda = $("panda");
  const blink = () => {
    if (pandaState !== "asleep") {
      panda.classList.add("blink");
      setTimeout(() => panda.classList.remove("blink"), 200);
    }
    setTimeout(blink, 2200 + Math.random() * 5200);
  };
  setTimeout(blink, 1800);

  for (const event of ["mousemove", "keydown", "click"]) {
    document.addEventListener(event, () => { lastActivity = Date.now(); wake(); }, { passive: true });
  }

  // 闲置两分钟就睡，任务在跑时不睡。
  setInterval(() => {
    if (pandaState === "working") return;
    if (Date.now() - lastActivity > 120000) setPandaState("asleep");
  }, 5000);
}

function wake() {
  if (pandaState === "asleep") setPandaState("");
}

function setPandaState(state) {
  if (pandaState === state) return;
  pandaState = state;
  for (const node of mascots()) {
    node.classList.remove("working", "asleep");
    if (state) node.classList.add(state);
  }
}

function cheer() {
  wake();
  const node = mascotEl();
  node.classList.add("cheer");
  setTimeout(() => node.classList.remove("cheer"), 720);
  const box = node.getBoundingClientRect();
  burst(box.left + box.width / 2, box.top + box.height / 2, 7);
}

// 从你自己做过的字幕里捞来的句子。有货就优先说这些——
// 写死的那几句念多了就腻，你自己素材里的句子每次都不一样。
let phrasePool = [];

async function loadPhrases() {
  try {
    const data = await (await fetch("/api/phrases")).json();
    phrasePool = data.phrases || [];
  } catch { phrasePool = []; }
}

function speak() {
  // 大部分时候说你素材里的句子，偶尔穿插一句自带的，免得完全没了性格
  const useReal = phrasePool.length && Math.random() < 0.75;
  let han, pin, en;

  if (useReal) {
    const pick = phrasePool[Math.floor(Math.random() * phrasePool.length)];
    han = pick.text;
    pin = pick.reading || "";
    en = pick.translation || pick.file.replace(/\.srt$/i, "").slice(0, 40);
  } else {
    const lines = SAYINGS[cfg.language] || SAYINGS.zh;
    [han, pin, en] = lines[Math.floor(Math.random() * lines.length)];
  }

  $("bubble-han").textContent = han;
  $("bubble-pin").textContent = pin;
  $("bubble-en").textContent = en;
  $("bubble-pin").hidden = !pin;
  $("bubble-en").hidden = !en;

  const bubble = $("panda-bubble");
  bubble.hidden = false;
  clearTimeout(speak.timer);
  // 句子长的多留一会儿，不然还没读完就没了
  speak.timer = setTimeout(() => (bubble.hidden = true), useReal ? 4200 : 2600);
}

// 点一下飘几片竹叶／溅几滴水出来。纯属好玩。
function burst(x, y, count = 4) {
  const kind = cfg.language === "ja" ? "drop" : "leaf";
  for (let i = 0; i < count; i++) {
    const leaf = document.createElement("div");
    leaf.className = kind;
    leaf.style.left = `${x - 8}px`;
    leaf.style.top = `${y - 4}px`;
    leaf.style.setProperty("--dx", `${(Math.random() - 0.4) * 120}px`);
    leaf.style.setProperty("--dy", `${40 + Math.random() * 90}px`);
    leaf.style.setProperty("--rot", `${180 + Math.random() * 420}deg`);
    leaf.style.setProperty("--dur", `${1.6 + Math.random() * 1.2}s`);
    leaf.style.opacity = String(0.6 + Math.random() * 0.4);
    document.body.appendChild(leaf);
    setTimeout(() => leaf.remove(), 3000);
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
