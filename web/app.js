const $ = (id) => document.getElementById(id);
const jobs = new Map();
const openLogs = new Set();

let cfg = {};        // 完整设置，包含每种语言各自的配置
let active = {};     // 当前语言的设置，已压平
let langs = [];      // 服务端给出的语言定义
let models = { whisper: [], chat: [] };

const FALLBACK_WHISPER = ["whisper-large-v3", "whisper-large-v3-turbo"];
const FALLBACK_CHAT = ["llama-3.3-70b-versatile", "qwen/qwen3-32b", "moonshotai/kimi-k2-instruct"];
const GLYPHS = { zh: "字", ja: "か" };

// ---------------------------------------------------------------- 启动

init();

async function init() {
  await refreshState();
  await refreshFiles();
  loadModels();
  connectStream();

  $("go").onclick = submit;
  $("urls").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
  });
  $("clear-done").onclick = async () => {
    await fetch("/api/jobs/clear", { method: "POST" });
    await refreshState();
  };
  $("open-folder").onclick = () => fetch("/api/reveal", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });

  $("settings-open").onclick = openSettings;
  $("settings-close").onclick = () => ($("settings-overlay").hidden = true);
  $("settings-save").onclick = saveSettings;
  $("preview-close").onclick = () => ($("preview-overlay").hidden = true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $("settings-overlay").hidden = true;
      $("preview-overlay").hidden = true;
    }
  });
  for (const overlay of document.querySelectorAll(".overlay")) {
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
  }

  // 这几个快捷开关直接写回保存的设置。
  for (const [id, key] of [["opt-existing", "prefer_existing_subs"], ["opt-auto", "allow_auto_subs"],
                           ["opt-reading", "reading"], ["opt-translate", "translate"]]) {
    $(id).onchange = () => patch({ [key]: $(id).checked });
  }
  $("opt-model").onchange = () => patch({ model: $("opt-model").value });
}

// ---------------------------------------------------------------- 状态

async function refreshState() {
  const data = await (await fetch("/api/state")).json();
  cfg = data.config;
  active = data.active;
  langs = data.languages;
  renderLanguages();
  renderHealth(data.env);
  applyConfigToControls();
  jobs.clear();
  for (const job of data.jobs) jobs.set(job.id, job);
  renderQueue();
}

function currentLang() {
  return langs.find((l) => l.code === cfg.language) || langs[0] || {};
}

function renderLanguages() {
  $("langs").innerHTML = langs.map((l) => `
    <button class="lang ${l.code === cfg.language ? "on" : ""}" data-lang="${esc(l.code)}"
            title="${esc(l.name)}">
      <span class="lang-native">${esc(l.native)}</span>
      <span class="lang-name">${esc(l.name)}</span>
    </button>`).join("");
  for (const btn of $("langs").children) {
    btn.onclick = () => switchLanguage(btn.dataset.lang);
  }
  const lang = currentLang();
  $("brand-glyph").textContent = GLYPHS[lang.code] || "字";
  $("brand-sub").textContent = `给 asbplayer 用的${lang.name || ""}字幕`;
  $("opt-reading-label").textContent = `${lang.reading_label || "注音"}行`;
}

async function switchLanguage(code) {
  if (code === cfg.language) return;
  await patch({ language: code });
  await refreshState();
  await refreshFiles();
  setHint(`已切换到${currentLang().name}，文件存到 ${active.output_dir}`);
}

function applyConfigToControls() {
  $("opt-existing").checked = !!cfg.prefer_existing_subs;
  $("opt-auto").checked = !!cfg.allow_auto_subs;
  $("opt-reading").checked = !!cfg.reading;
  $("opt-translate").checked = !!cfg.translate;
  fillSelect($("opt-model"), models.whisper.length ? models.whisper : FALLBACK_WHISPER, cfg.model);
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
  if (env.ffmpeg_missing.length) pills.push(["bad", `缺少 ${env.ffmpeg_missing.join("、")}`]);
  else pills.push(["ok", "ffmpeg"]);
  pills.push(cfg.api_key ? ["ok", env.key_from_env ? "密钥（环境变量）" : "密钥"] : ["bad", "没有密钥"]);
  if (!env.reading_ok) pills.push(["warn", `缺少${currentLang().reading_label || "注音"}组件`]);
  else if (cfg.language === "zh" && !env.segmenter) pills.push(["warn", "缺少 jieba"]);
  if (cfg.language === "zh" && !env.opencc) pills.push(["warn", "缺少 opencc"]);
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

async function submit() {
  const urls = $("urls").value.trim();
  if (!urls) { setHint("先粘贴一个 YouTube 链接。", true); return; }

  $("go").disabled = true;
  setHint("正在读取链接……");
  try {
    const res = await fetch("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    const data = await res.json();
    if (!res.ok) { setHint(data.error || "出了点问题。", true); return; }
    $("urls").value = "";
    setHint(`已加入 ${data.jobs.length} 个${currentLang().name}视频。`);
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

// ---------------------------------------------------------------- 实时更新

function connectStream() {
  const source = new EventSource("/api/stream");
  source.addEventListener("job", (e) => {
    const job = JSON.parse(e.data);
    const previous = jobs.get(job.id);
    job.log = job.log?.length ? job.log : previous?.log || [];
    jobs.set(job.id, job);
    renderQueue();
    if (job.status === "done") refreshFiles();
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

  for (const job of list) {
    const node = document.createElement("div");
    node.className = "job";
    node.dataset.id = job.id;

    const open = openLogs.has(job.id);
    node.innerHTML = `
      <div class="job-head">
        <span class="dot ${job.status}"></span>
        <div class="job-title">
          ${esc(job.title || job.url)}
          <div class="job-sub">${statusLine(job)}</div>
        </div>
        ${job.language ? `<span class="tag">${esc(langName(job.language))}</span>` : ""}
        ${actionButtons(job)}
      </div>
      ${open ? `<div class="job-log" id="log-${job.id}"></div>` : ""}`;

    node.querySelector(".job-head").onclick = (e) => {
      if (e.target.closest("button")) return;
      openLogs.has(job.id) ? openLogs.delete(job.id) : openLogs.add(job.id);
      renderQueue();
    };
    const cancelBtn = node.querySelector("[data-cancel]");
    if (cancelBtn) cancelBtn.onclick = () => fetch(`/api/jobs/${job.id}/cancel`, { method: "POST" });
    const viewBtn = node.querySelector("[data-view]");
    if (viewBtn) viewBtn.onclick = () => preview(viewBtn.dataset.view);

    container.appendChild(node);

    if (open) {
      const log = $(`log-${job.id}`);
      log.innerHTML = (job.log || []).map(logLine).join("");
      log.scrollTop = log.scrollHeight;
    }
  }
}

function langName(code) {
  return (langs.find((l) => l.code === code) || {}).name || code;
}

function statusLine(job) {
  if (job.status === "done" && job.result) {
    const via = job.result.source === "youtube" ? "用的视频自带字幕" : "语音转写";
    return `${job.result.lines} 行 · ${via}`;
  }
  if (job.status === "failed") return esc(job.error || "失败");
  return { queued: "等待中", running: "处理中……", cancelled: "已取消" }[job.status] || job.status;
}

function actionButtons(job) {
  if (job.status === "queued" || job.status === "running") {
    return `<button class="ghost small" data-cancel>取消</button>`;
  }
  if (job.status === "done" && job.result) {
    const name = job.result.path.split(/[\\/]/).pop();
    // 只有当它所属的语言正是当前选中的语言时，才能就地打开预览。
    if (job.language && job.language === cfg.language) {
      return `<button class="ghost small" data-view="${esc(name)}">查看</button>`;
    }
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

// ---------------------------------------------------------------- 文件

async function refreshFiles() {
  const data = await (await fetch("/api/files")).json();
  const list = $("files");
  $("files-empty").hidden = data.files.length > 0;
  $("files-empty").textContent = `这里还是空的 —— ${currentLang().name || ""}字幕会存到这个目录。`;
  list.innerHTML = data.files.map((f) => `
    <li data-name="${esc(f.name)}">
      <span class="name">${esc(f.name)}</span>
      <span class="meta">${new Date(f.modified * 1000).toLocaleString()}</span>
    </li>`).join("");
  for (const li of list.children) li.onclick = () => preview(li.dataset.name);
}

async function preview(name) {
  const res = await fetch(`/api/file?name=${encodeURIComponent(name)}`);
  if (!res.ok) return;
  const data = await res.json();
  $("preview-title").textContent = data.name;
  $("preview-path").textContent = data.path;
  $("preview-cues").innerHTML = data.cues.map((cue) => {
    const [head, ...rest] = cue.text.split("\n");
    return `<div class="cue">
      <time>${clock(cue.start)}</time>
      <div>
        <div class="zh">${esc(head)}</div>
        ${rest.map((r) => `<div class="sub">${esc(r)}</div>`).join("")}
      </div>
    </div>`;
  }).join("");
  $("preview-overlay").hidden = false;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

// ---------------------------------------------------------------- 设置

function openSettings() {
  const lang = currentLang();

  $("set-key").value = "";
  $("key-note").textContent = cfg.api_key
    ? "已经保存了一个密钥，留空即保持不变。"
    : "还没有密钥 —— 没有密钥就无法进行语音转写。";

  $("set-out-lang").textContent = `· ${lang.name || ""}`;
  $("set-prompt-lang").textContent = `· ${lang.name || ""}`;
  $("set-out").value = active.output_dir || "";
  $("set-prompt").value = active.prompt || "";
  $("set-chars").value = active.max_chars_per_line;

  fillSelect($("set-model"), models.whisper.length ? models.whisper : FALLBACK_WHISPER, cfg.model);
  fillSelect($("set-translate-model"), models.chat.length ? models.chat : FALLBACK_CHAT, cfg.translate_model);

  // 简繁只跟中文有关。
  $("row-variant").hidden = !lang.has_script_variant;
  $("set-variant").value = cfg.script_variant ?? "s";

  const styles = lang.reading_styles || {};
  $("row-reading-style").hidden = Object.keys(styles).length < 2;
  $("set-reading-style").innerHTML = Object.entries(styles)
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  if (active.reading_style) $("set-reading-style").value = active.reading_style;

  $("set-chunk").value = cfg.chunk_seconds;
  $("set-parallel").value = cfg.parallel_chunks;
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
  $("settings-hint").textContent = "已保存。";
  setTimeout(() => ($("settings-overlay").hidden = true), 500);
}

function fillSelect(select, options, selected) {
  const values = [...new Set([...options, selected].filter(Boolean))];
  select.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  if (selected) select.value = selected;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
