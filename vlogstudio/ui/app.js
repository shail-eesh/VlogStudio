/* Vlog Studio — local app UI (no external libraries). */
"use strict";

const S = { meta: null, cfg: null, state: null, media: null, plan: null, page: "home", logSince: 0,
            saveTimer: null, saving: null, lookTimer: null, models: [], editShots: {} };
const PAGES = [
  ["home", "⌂", "Make a video"], ["media", "▦", "Footage"], ["look", "◐", "Colour look"],
  ["music", "♫", "Music"], ["story", "☰", "Storyboard"], ["render", "▶", "Render & export"],
  ["ai", "✦", "AI assistant"], ["settings", "⚙", "All settings"],
];

// ---------------------------------------------------------------- helpers
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c instanceof Node ? c : document.createTextNode(c));
  return e;
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 3500);
}
const fileUrl = p => p ? "/file?path=" + encodeURIComponent(p) + "&v=" + Date.now() % 100000 : "";
const thumbUrl = p => p ? "/file?path=" + encodeURIComponent(p) : "";
const fmtT = t => t == null ? "" : (t >= 60 ? `${Math.floor(t / 60)}:${String((t % 60).toFixed(1)).padStart(4, "0")}` : `${t.toFixed(1)}s`);
const fmtSize = b => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(1) + " MB";
function getP(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setP(obj, path, v) {
  const ks = path.split("."); let o = obj;
  for (const k of ks.slice(0, -1)) { if (o[k] == null || typeof o[k] !== "object") o[k] = {}; o = o[k]; }
  if (v === null) delete o[ks[ks.length - 1]]; else o[ks[ks.length - 1]] = v;
}
const fieldDef = path => S.meta.fields.find(f => f.path === path);
const running = () => S.state && S.state.job.running;
const REVEAL = () => (S.meta && S.meta.platform === "darwin") ? "Show in Finder" : "Show in folder";

// ---------------------------------------------------------------- settings fields
function changed(path, v, opts = {}) {
  setP(S.cfg, path, v);
  if (path === "llm.enabled") setTimeout(render, 0);
  if (opts.immediate) return saveNow();
  clearTimeout(S.saveTimer);
  $("#saved").textContent = "editing…";
  S.saveTimer = setTimeout(saveNow, 600);
  if (path.startsWith("look.")) schedLookPreview();
}
async function saveNow() {
  clearTimeout(S.saveTimer);
  if (running()) { $("#saved").textContent = "will save after the job"; S.saveTimer = setTimeout(saveNow, 2000); return; }
  const payload = {};
  for (const sec of ["project", "llm", "look", "orientation", "analysis", "music", "output", "titles"]) payload[sec] = S.cfg[sec];
  S.saving = api("/api/config", payload).then(() => { $("#saved").textContent = "saved ✓"; setTimeout(() => $("#saved").textContent = "", 1500); })
    .catch(e => toast("Could not save: " + e.message, "err"));
  await S.saving;
  S.saving = null;
}
async function flush() { if (S.saveTimer) await saveNow(); if (S.saving) await S.saving; }

function field(path, extra = {}) {
  const f = { ...fieldDef(path), ...extra };
  if (!f.path) return h("div", {}, "unknown field " + path);
  let v = getP(S.cfg, path);
  let input;
  const id = "f_" + path.replace(/\W/g, "_");
  if (f.type === "bool") {
    input = h("label", { class: "toggle" }, h("input", { type: "checkbox", id, checked: !!v, onchange: e => changed(path, e.target.checked) }), h("span"));
  } else if (f.type === "select") {
    input = h("select", { id, onchange: e => { const o = f.options.find(x => String(x) === e.target.value); changed(path, o); if (f.after) f.after(o); } },
      f.options.map(o => h("option", { value: String(o), selected: String(o) === String(v) }, String(o).replace(/_/g, " "))));
  } else if (f.type === "range") {
    const preset = path.startsWith("look.overrides.") ? (S.meta.look_presets[S.cfg.look.preset] || {})[path.split(".").pop()] : undefined;
    const cur = v ?? preset ?? f.min;
    const val = h("span", { class: "val" }, v == null && preset != null ? "preset" : String(cur));
    const r = h("input", { type: "range", id, min: f.min, max: f.max, step: f.step, value: cur,
      oninput: e => { val.textContent = e.target.value; changed(path, parseFloat(e.target.value)); } });
    const kids = [r, val];
    if (f.optional) kids.push(h("button", { class: "ghost small", title: "back to preset", onclick: () => { changed(path, null); render(); } }, "↺"));
    input = h("div", { class: "rangewrap" }, kids);
  } else if (f.type === "number") {
    input = h("input", { type: "number", id, min: f.min, max: f.max, step: f.step, value: v ?? "",
      onchange: e => changed(path, e.target.value === "" ? 0 : parseFloat(e.target.value)) });
  } else if (f.type === "list") {
    input = h("input", { type: "text", id, value: (v || []).join(", "),
      onchange: e => changed(path, e.target.value.split(",").map(s => s.trim()).filter(Boolean)) });
  } else if (f.type === "multi") {
    const cur = new Set(v || []);
    input = h("div", { class: "chips" }, f.options.map(o => h("span", { class: "chip" + (cur.has(o) ? " on" : ""), onclick: e => {
      cur.has(o) ? cur.delete(o) : cur.add(o); e.target.classList.toggle("on"); changed(path, f.options.filter(x => cur.has(x))); } }, o)));
  } else if (f.type === "path") {
    const inp = h("input", { type: "text", id, value: v || "", onchange: e => changed(path, e.target.value) });
    input = h("div", { class: "pathwrap" }, inp, h("button", { class: "small", onclick: async () => {
      const r = await api("/api/pick", { kind: f.pick || "folder", prompt: f.label }).catch(e => toast(e.message, "err"));
      if (r && r.path) { inp.value = r.path; changed(path, r.path, { immediate: true }); if (f.after) f.after(r.path); }
    } }, "Browse…"));
  } else if (f.type === "model") {
    const dl = h("datalist", { id: id + "_dl" }, S.models.map(m => h("option", { value: m })));
    input = h("div", { class: "pathwrap" }, h("input", { type: "text", id, list: id + "_dl", value: v || "", onchange: e => changed(path, e.target.value) }), dl);
  } else {
    input = h("input", { type: f.type === "password" ? "password" : "text", id, value: v ?? "", placeholder: f.placeholder || "",
      onchange: e => changed(path, e.target.value) });
  }
  return h("div", { class: "field" + (extra.wide ? " wide" : "") }, h("label", { for: id }, f.label), input, f.help ? h("div", { class: "help" }, f.help) : null);
}
const fields = (...paths) => paths.map(p => field(p));
function inlineSelect(path, label) {
  const f = fieldDef(path), v = getP(S.cfg, path);
  return h("label", { class: "row", title: f.help || "" }, h("span", { class: "muted" }, label),
    h("select", { style: "width:auto", onchange: e => changed(path, e.target.value, { immediate: true }) },
      f.options.map(o => h("option", { value: o, selected: String(o) === String(v) }, String(o).replace(/_/g, " ")))));
}

// ---------------------------------------------------------------- jobs
async function run(action, args = {}, label) {
  await flush();
  try {
    await api("/api/run", { action, args });
    S.state.job.running = true; S.state.job.action = action;
    toast((label || action) + " started");
    paintJob(); pollJob();
  } catch (e) { toast(e.message, "err"); }
}
let jobTimer = null, lastAction = null;
async function pollJob() {
  clearTimeout(jobTimer);
  try {
    const j = await api("/api/job?since=" + S.logSince);
    for (const l of j.log) { appendLog(l); S.logSince = Math.max(S.logSince, l.t); }
    const wasRunning = S.state.job.running;
    S.state.job = { ...S.state.job, ...j };
    paintJob(j.log.length ? j.log[j.log.length - 1].line : null);
    if (j.running) { jobTimer = setTimeout(pollJob, 900); if (S.page === "ai") refreshAgentLog(); return; }
    if (wasRunning) {
      if (j.error) toast(`${j.action}: ${j.error}`, "err"); else toast(`${j.action} finished ✓`, "ok");
      await refreshAll();
    }
  } catch (e) { jobTimer = setTimeout(pollJob, 3000); }
}
function appendLog(l) {
  const pre = $("#log");
  pre.append(h("span", { class: l.level }, l.line + "\n"));
  if (pre.childNodes.length > 1500) pre.removeChild(pre.firstChild);
  pre.scrollTop = pre.scrollHeight;
}
function paintJob(line) {
  const j = S.state.job, bar = $("#jobbar");
  bar.className = j.running ? "running" : (j.error && j.error !== "cancelled" ? "failed" : "idle");
  $("#jobTitle").textContent = j.running ? `Working: ${j.action}…` : (j.action ? (j.error ? `${j.action}: ${j.error}` : `${j.action} finished`) : "Ready");
  if (line) $("#jobLine").textContent = line.replace(/^\[\s*[\d.]+s\]\s*/, "");
  $("#cancelBtn").hidden = !j.running;
  const aiOff = !(S.cfg && S.cfg.llm && S.cfg.llm.enabled);
  document.querySelectorAll("[data-job]").forEach(b => b.disabled = !!j.running || (b.dataset.ai !== undefined && aiOff));
}

// ---------------------------------------------------------------- data
async function refreshAll() {
  [S.state, S.cfg] = await Promise.all([api("/api/state"), api("/api/config")]);
  S.media = await api("/api/media").catch(() => []);
  S.plan = await api("/api/plan").catch(() => null);
  paintProjects(); paintJob(); render();
}
function paintProjects() {
  const sel = $("#projSel");
  sel.innerHTML = "";
  for (const p of S.state.projects) sel.append(h("option", { value: p.path, selected: p.path === S.state.config_path }, p.name));
}

// ---------------------------------------------------------------- pages
function render() {
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.p === S.page));
  const page = $("#page"); page.innerHTML = "";
  const fn = { home: pHome, media: pMedia, look: pLook, music: pMusic, story: pStory, render: pRender, ai: pAI, settings: pSettings }[S.page];
  page.append(fn());
  paintJob();
}
function btn(label, action, args, cls = "", title, needsAI = false) {
  const aiOff = needsAI && !S.cfg.llm.enabled;
  return h("button", { class: cls, "data-job": true, "data-ai": needsAI || null, disabled: running() || aiOff,
    title: aiOff ? "switch on the AI assistant first" : title, onclick: () => run(action, args, label) }, label);
}
function steps() {
  const st = S.state.status;
  const items = [["Footage analysed", st.analyzed], ["Music", st.music], ["Storyboard", st.plan_shots > 0], ["Preview", st.preview], ["Final video", (st.final || []).length > 0]];
  return h("div", { class: "steps" }, items.map(([t, ok], i) => h("div", { class: "step" + (ok ? " done" : "") }, h("span", { class: "n" }, ok ? "✓" : i + 1), t)));
}
function latestVideo() {
  const outs = (S.state.outputs || []).filter(o => o.name.endsWith(".mp4"));
  const fin = outs.filter(o => o.name !== "preview.mp4").sort((a, b) => b.mtime - a.mtime)[0];
  return fin || outs.find(o => o.name === "preview.mp4");
}

function pHome() {
  const st = S.state.status, v = latestVideo();
  const styleSel = field("project.style");
  return h("div", {},
    h("h1", {}, S.cfg.project.name || "My video"),
    h("p", { class: "lead" }, "Pick your footage folder, choose the kind of video, then press Make. Everything runs on this computer; nothing is uploaded."),
    steps(),
    h("div", { class: "grid2" },
      h("div", { class: "card" },
        h("h2", {}, "1 · Footage"),
        ...fields("project.media_folder", "project.output_folder", "project.include_images"),
        h("div", { class: "row", style: "margin-top:10px" },
          h("span", { class: "muted" }, st.analyzed ? `${st.items} items · ${st.usable} usable` + (st.orientation_suspect?.length ? ` · ${st.orientation_suspect.length} to check` : "") : "not analysed yet"),
          h("span", { class: "spacer" }), btn("Analyse footage", "analyze", {}, "small"))),
      h("div", { class: "card" },
        h("h2", {}, "2 · What are you making?"),
        styleSel,
        h("div", { class: "row", style: "justify-content:flex-end;margin:-2px 0 6px" }, h("button", { class: "small", onclick: applyStyle }, "Apply type defaults")),
        ...fields("project.pacing", "project.target_duration", "project.title", "project.title_script", "project.subtitle",
                  "project.hook", "project.hook_sub", "project.end_line", "project.end_sub"))),
    h("div", { class: "card", style: "margin-top:18px" },
      h("h2", {}, "3 · Make it"),
      h("div", { class: "row" },
        h("button", { class: "primary big", "data-job": true, disabled: running(), onclick: () => run("make", { preview: $("#mkPrev").checked, graded: $("#mkGraded").checked, rescan: $("#mkRescan").checked }, "Make video") }, "✦  Make my video"),
        h("label", { class: "row" }, h("input", { type: "checkbox", id: "mkPrev" }), "720p preview first"),
        h("label", { class: "row" }, h("input", { type: "checkbox", id: "mkGraded" }), "also export graded clips"),
        h("label", { class: "row" }, h("input", { type: "checkbox", id: "mkRescan" }), "start from scratch"),
        h("span", { class: "spacer" }),
        h("span", { class: "muted" }, S.cfg.llm.enabled ? `AI: ${S.cfg.llm.model}` : "AI off · automatic mode")),
      h("p", { class: "muted", style: "margin:12px 0 0;font-size:12.5px" },
        "Make runs every step: analyse → (AI checks) → music → storyboard → 4K render → LUTs. Steps that are already done are reused. You can also run each step from its own page.")),
    v ? h("div", { class: "card", style: "margin-top:18px" }, h("div", { class: "row" }, h("h2", { style: "margin:0" }, v.name), h("span", { class: "spacer" }),
          h("button", { class: "small", onclick: () => api("/api/reveal", { path: v.path }) }, REVEAL())),
        h("video", { src: fileUrl(v.path), controls: true, style: "margin-top:12px;max-height:60vh" })) : null);
}
function applyStyle() {
  const st = S.cfg.project.style, preset = S.meta.styles[st] || {}, d = S.meta.defaults;
  // reset fields that any style touches to the defaults, then apply this style
  const touched = new Set();
  for (const s of Object.values(S.meta.styles)) for (const [sec, body] of Object.entries(s)) for (const k of Object.keys(body)) touched.add(sec + "." + k);
  for (const p of touched) setP(S.cfg, p, getP(d, p));
  for (const [sec, body] of Object.entries(preset)) for (const [k, v] of Object.entries(body)) setP(S.cfg, sec + "." + k, v);
  S.cfg.project.style = st;
  saveNow().then(render);
  toast(`Applied “${st.replace(/_/g, " ")}” defaults`, "ok");
}

function pMedia() {
  const items = S.media || [];
  const filt = S.mediaFilter || "all";
  const shown = items.filter(m => filt === "all" || (filt === "usable" && m.usable) || (filt === "skipped" && !m.usable) || (filt === "check" && m.suspect) || (filt === "photos" && m.kind === "image"));
  const ai = S.cfg.llm.enabled;
  return h("div", {},
    h("h1", {}, "Footage"),
    h("p", { class: "lead" }, "Every clip and photo after automatic exposure, white balance and orientation. Click one to rotate, brighten or skip it."),
    h("div", { class: "row", style: "margin-bottom:16px" },
      btn("Analyse footage", "analyze", {}, "primary"),
      btn("AI orientation check", "orient_ai", {}, "", "", true),
      btn("AI descriptions", "captions", {}, "", "", true),
      btn("Refresh thumbnails", "rethumb", {}, "ghost"),
      h("span", { class: "spacer" }),
      h("div", { class: "chips" }, ["all", "usable", "check", "skipped", "photos"].map(k =>
        h("span", { class: "chip" + (filt === k ? " on" : ""), onclick: () => { S.mediaFilter = k; render(); } },
          k === "check" ? `check orientation (${items.filter(m => m.suspect).length})` : k)))),
    items.length ? h("div", { class: "mediagrid" }, shown.map(mediaCard)) :
      h("div", { class: "empty" }, "No footage analysed yet. Choose the footage folder on the first page and press “Analyse footage”."));
}
function mediaCard(m) {
  const b = [];
  if (!m.usable) b.push(h("span", { class: "badge err" }, m.reason || "skipped"));
  if (m.suspect) b.push(h("span", { class: "badge warn" }, "check orientation"));
  if (m.kind === "image") b.push(h("span", { class: "badge" }, "photo"));
  if (m.override && Object.keys(m.override).length) b.push(h("span", { class: "badge ok" }, "edited"));
  return h("div", { class: "mcard" + (m.usable ? "" : " off"), onclick: () => openClip(m.id) },
    h("img", { src: thumbUrl(m.thumb), loading: "lazy" }), h("div", { class: "badges" }, b),
    h("div", { class: "meta" }, h("div", { class: "id" }, m.id),
      h("div", { class: "sub" }, [m.kind === "video" ? fmtT(m.duration) : "photo", m.place, (m.created || "").slice(0, 16).replace("T", " ")].filter(Boolean).join(" · ")),
      m.caption ? h("div", { class: "sub" }, m.caption) : null));
}
async function openClip(id) {
  const m = S.media.find(x => x.id === id);
  const body = $("#modalBody"); body.innerHTML = "";
  const img = h("img", { class: "sheet", alt: "loading contact sheet…" });
  const stops = h("input", { type: "range", min: -2.5, max: 2.5, step: 0.05, value: m.stops ?? 0 });
  const sval = h("span", { class: "val" }, (m.stops ?? 0).toFixed(2));
  stops.oninput = () => sval.textContent = parseFloat(stops.value).toFixed(2);
  const act = async (kw, msg) => {
    try { await api("/api/override", { clip: id, ...kw }); toast(msg, "ok"); S.media = await api("/api/media"); openClip(id); render(); }
    catch (e) { toast(e.message, "err"); }
  };
  body.append(
    h("h2", {}, m.id),
    h("div", { class: "kv" }, h("span", {}, "Type"), h("span", {}, `${m.kind} · ${m.size} · ${m.profile}`),
      h("span", {}, "Length"), h("span", {}, m.kind === "video" ? fmtT(m.duration) : "photo"),
      h("span", {}, "Recorded"), h("span", {}, (m.created || "").replace("T", " ")),
      h("span", {}, "Place"), h("span", {}, m.place || "—"),
      m.caption ? h("span", {}, "AI says") : null, m.caption ? h("span", {}, m.caption) : null,
      m.moments?.length ? h("span", {}, "Best moments") : null,
      m.moments?.length ? h("span", {}, m.moments.map(b => `${fmtT(b.t)} (${b.dur}s) ${b.why || ""}`).join(" · ")) : null,
      m.avoid?.length ? h("span", {}, "Avoid") : null, m.avoid?.length ? h("span", {}, m.avoid.map(a => `${fmtT(a.t)} ${a.why || ""}`).join(" · ")) : null,
      h("span", {}, "File"), h("span", { class: "muted" }, m.path)),
    img,
    h("h3", {}, "Fix this clip"),
    h("div", { class: "row" },
      h("button", { "data-job": true, onclick: () => act({ fix_rotation_cw: 270 }, "Rotated left") }, "⟲ Rotate left"),
      h("button", { "data-job": true, onclick: () => act({ fix_rotation_cw: 180 }, "Flipped") }, "↻ Upside down"),
      h("button", { "data-job": true, onclick: () => act({ fix_rotation_cw: 90 }, "Rotated right") }, "⟳ Rotate right"),
      m.suspect ? h("button", { "data-job": true, onclick: () => act({ fix_rotation_cw: 0 }, "Marked as upright") }, "✓ Looks right") : null,
      h("span", { class: "spacer" }),
      m.usable ? h("button", { class: "danger", "data-job": true, onclick: () => act({ exclude: true }, "Clip will be skipped") }, "Skip this clip")
               : h("button", { "data-job": true, onclick: () => act({ exclude: false }, "Clip will be used") }, "Use this clip")),
    h("div", { class: "field" }, h("label", {}, "Exposure (stops)"), h("div", { class: "rangewrap" }, stops, sval,
      h("button", { class: "small", "data-job": true, onclick: () => act({ stops: parseFloat(stops.value) }, "Exposure saved") }, "Apply"))),
    h("p", { class: "muted", style: "font-size:12.5px" }, "Changes are saved per clip and used by the next storyboard / render."));
  $("#modal").hidden = false;
  paintJob();
  const r = await api("/api/sheet", { clip: id }).catch(() => null);
  if (r && r.path) img.src = fileUrl(r.path);
}

function pLook() {
  const grid = h("div", { class: "lookgrid", id: "lookGrid" }, h("div", { class: "muted" }, "loading preview…"));
  setTimeout(lookPreview, 0);
  return h("div", {},
    h("h1", {}, "Colour look"),
    h("p", { class: "lead" }, "One LUT-based grade for the whole video. Every shot is also balanced automatically for exposure and white balance. The preview updates as you move the sliders."),
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h2", {}, "Look"),
        field("look.preset", { after: () => setTimeout(() => { render(); }, 50) }),
        ...fields("look.strength", "look.target_exposure", "look.auto_exposure", "look.auto_white_balance"),
        h("h3", {}, "Fine tune (optional)"),
        ...fields("look.overrides.contrast", "look.overrides.vibrance", "look.overrides.saturation", "look.overrides.matte",
                  "look.overrides.exposure", "look.overrides.skin_protect"),
        h("div", { class: "row", style: "margin-top:12px" }, btn("Update all thumbnails", "rethumb", {}, "small"),
          btn("Export LUT files", "luts", {}, "small"), h("span", { class: "muted", style: "font-size:12px" }, "LUTs work in DaVinci Resolve, Premiere, Final Cut"))),
      h("div", { class: "card" }, h("h2", {}, "Preview"), grid)));
}
function schedLookPreview() { if (S.page !== "look") return; clearTimeout(S.lookTimer); S.lookTimer = setTimeout(lookPreview, 250); }
async function lookPreview() {
  const g = $("#lookGrid"); if (!g) return;
  try {
    const r = await api("/api/look_preview", { look: S.cfg.look });
    g.innerHTML = "";
    if (!r.length) g.append(h("div", { class: "muted" }, "Analyse your footage first."));
    r.forEach(x => g.append(h("img", { src: x.img, title: x.id })));
  } catch (e) { g.innerHTML = ""; g.append(h("div", { class: "muted" }, e.message)); }
}

function pMusic() {
  const m = S.state.music;
  return h("div", {},
    h("h1", {}, "Music"),
    h("p", { class: "lead" }, "An original royalty-free track is composed for your video, or use your own song: its tempo and beats are detected so every cut lands on the beat."),
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h2", {}, "Soundtrack"),
        ...fields("music.mode", "music.preset", "music.file", "music.bpm", "music.key", "music.seed", "music.ambient_level", "project.target_duration"),
        h("div", { class: "row", style: "margin-top:12px" }, btn("Create soundtrack", "music", {}, "primary"))),
      h("div", { class: "card" }, h("h2", {}, "Current track"),
        m ? h("div", {},
          h("div", { class: "kv", style: "margin-bottom:12px" }, h("span", {}, "Source"), h("span", {}, m.source === "file" ? "your file" : `generated · ${m.preset}`),
            h("span", {}, "Tempo"), h("span", {}, `${(m.bpm || 0).toFixed(1)} BPM`), h("span", {}, "Length"), h("span", {}, fmtT(m.duration))),
          h("audio", { src: fileUrl(m.wav), controls: true }),
          sectionBar(m.sections, m.duration)) : h("div", { class: "empty" }, "No soundtrack yet."))));
}
function sectionBar(sections, total, shots) {
  const bar = h("div", { class: "timeline" }, (sections || []).map(s =>
    h("div", { class: "sec-" + s.energy, style: `width:${(s.end - s.start) / total * 100}%`, title: `${s.name} ${fmtT(s.start)}–${fmtT(s.end)}` }, s.name)));
  const ticks = shots ? h("div", { class: "ticks" }, shots.map(s => h("i", { style: `left:${s.start / total * 100}%` }))) : null;
  return h("div", {}, bar, ticks);
}

function pStory() {
  const p = S.plan, ai = S.cfg.llm.enabled;
  const head = h("div", {},
    h("h1", {}, "Storyboard"),
    h("p", { class: "lead" }, "The music fixes where the cuts are; each slot gets a clip and a start time. Change any shot or text, then press “Apply changes”."),
    h("div", { class: "row", style: "margin-bottom:16px" },
      btn("Build automatically", "plan", { use_llm: false }, "primary"),
      btn("Build with AI", "plan", { use_llm: true }, "", "", true),
      inlineSelect("project.pacing", "Pacing"),
      h("span", { class: "spacer" }), btn("Render preview", "preview", {})));
  if (!p) return h("div", {}, head, h("div", { class: "empty" }, "No storyboard yet. Press “Build automatically”."));
  S.editShots = {};
  const usable = (S.media || []).filter(m => m.usable);
  const text = {};
  for (const t of p.titles) text[t.id] = { ...t };
  const tField = (id, k, label) => text[id] ? h("div", { class: "field" }, h("label", {}, label),
    h("input", { type: "text", value: text[id][k] || "", onchange: e => { text[id][k] = e.target.value; } })) : null;
  const rows = p.shots.map(s => {
    const tr = h("tr", {});
    const sel = h("select", { onchange: () => { S.editShots[s.i] = { ...(S.editShots[s.i] || {}), clip: sel.value }; tr.classList.add("changed"); } },
      usable.map(m => h("option", { value: m.id, selected: m.id === s.clip }, `${m.id} (${m.kind === "video" ? fmtT(m.duration) : "photo"})`)));
    const inp = h("input", { type: "number", step: 0.1, min: 0, value: s.in, onchange: () => { S.editShots[s.i] = { ...(S.editShots[s.i] || {}), in: parseFloat(inp.value) }; tr.classList.add("changed"); } });
    tr.append(h("td", {}, s.i), h("td", {}, h("img", { src: thumbUrl(s.thumb), loading: "lazy" })), h("td", {}, fmtT(s.start)), h("td", {}, `${s.dur.toFixed(2)}s`),
      h("td", {}, s.section), h("td", {}, h("span", { class: "role " + s.role }, s.role)), h("td", {}, sel), h("td", {}, inp));
    return tr;
  });
  const apply = async () => {
    const shots = p.shots.map(s => ({ slot: s.i, clip: s.clip, in: s.in, speed: s.speed, ...(S.editShots[s.i] || {}) }));
    const tx = { hook: { line1: text.hook?.line1, line2: text.hook?.line2 }, title: { script: text.title?.script, main: text.title?.main, sub: text.title?.sub },
                 end: { main: text.end?.main, script: text.end?.script, sub: text.end?.sub },
                 chapters: p.titles.filter(t => t.kind === "chapter").map(t => ({ main: text[t.id].main, sub: text[t.id].sub })) };
    try { const r = await api("/api/plan/apply", { shots, text: tx }); toast("Storyboard updated" + (r.warnings.length ? ` (${r.warnings.length} fixed automatically)` : ""), "ok"); S.plan = await api("/api/plan"); render(); }
    catch (e) { toast(e.message, "err"); }
  };
  return h("div", {}, head,
    h("div", { class: "card" }, h("div", { class: "row" }, h("h2", { style: "margin:0" }, `${p.shots.length} shots · ${fmtT(p.duration)} · ${(p.music.bpm || 0).toFixed(0)} BPM · ${S.cfg.project.pacing} pacing`)),
      sectionBar(p.music.sections, p.duration, p.shots)),
    h("div", { class: "grid2", style: "margin-top:18px" },
      h("div", { class: "card" }, h("h2", {}, "On-screen text"),
        tField("hook", "line1", "Hook"), tField("hook", "line2", "Hook subline"), tField("title", "script", "Script line"),
        tField("title", "main", "Main title"), tField("title", "sub", "Title subline"),
        ...p.titles.filter(t => t.kind === "chapter").map(t => tField(t.id, "sub", `${t.main} places`)),
        tField("end", "main", "End title"), tField("end", "script", "End line"), tField("end", "sub", "End subline")),
      h("div", { class: "card" }, h("h2", {}, "Text timing"),
        h("div", { class: "kv" }, p.overlays.map(o => [h("span", {}, o.title), h("span", {}, `${fmtT(o.start)} – ${fmtT(o.end)}`)]).flat()),
        p.warnings?.length ? h("div", { class: "notice", style: "margin-top:12px" }, p.warnings.slice(0, 8).join("\n")) : null)),
    h("div", { class: "card", style: "margin-top:18px" },
      h("div", { class: "row", style: "margin-bottom:10px" }, h("h2", { style: "margin:0" }, "Shots"), h("span", { class: "spacer" }),
        h("button", { class: "primary", "data-job": true, onclick: apply }, "Apply changes")),
      h("div", { style: "max-height:60vh;overflow:auto" },
        h("table", { class: "shots" }, h("thead", {}, h("tr", {}, ["#", "", "at", "length", "section", "role", "clip", "start in clip (s)"].map(x => h("th", {}, x)))), h("tbody", {}, rows)))));
}

function pRender() {
  const outs = S.state.outputs || [];
  const prev = outs.find(o => o.name === "preview.mp4");
  const fin = outs.filter(o => o.name.endsWith(".mp4") && o.name !== "preview.mp4").sort((a, b) => b.mtime - a.mtime)[0];
  return h("div", {},
    h("h1", {}, "Render & export"),
    h("p", { class: "lead" }, "Preview renders a fast 720p check. Final renders the full-quality video (plus 1080p copy and thumbnail)."),
    h("div", { class: "row", style: "margin-bottom:18px" },
      btn("Render preview (720p)", "preview", {}), btn("Render final video", "render", {}, "primary"),
      btn("Export graded clips", "graded", {}), btn("Export LUT files", "luts", {}),
      h("span", { class: "spacer" }),
      h("button", { class: "ghost", onclick: () => api("/api/reveal", { path: S.cfg.output_folder_abs || S.state.status.output_folder }) }, "Open output folder")),
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h2", {}, "Preview"), prev ? h("video", { src: fileUrl(prev.path), controls: true }) : h("div", { class: "empty" }, "No preview yet.")),
      h("div", { class: "card" }, h("h2", {}, "Final"), fin ? h("video", { src: fileUrl(fin.path), controls: true }) : h("div", { class: "empty" }, "No final video yet."))),
    h("div", { class: "grid2", style: "margin-top:18px" },
      h("div", { class: "card" }, h("h2", {}, "Files"),
        outs.length ? h("div", { class: "outlist" }, outs.map(o => h("div", { class: "o" }, h("span", { class: "nm" }, o.name), h("span", { class: "muted" }, fmtSize(o.size)),
          h("button", { class: "small", onclick: () => api("/api/reveal", { path: o.path }) }, REVEAL())))) : h("div", { class: "muted" }, "Nothing rendered yet.")),
      h("div", { class: "card" }, h("h2", {}, "Output settings"),
        ...fields("output.resolution", "output.letterbox", "output.grain", "output.vignette", "output.flashes", "output.make_1080p", "output.export_graded_clips", "output.basename"))));
}

function pAI() {
  const c = S.cfg.llm;
  const status = h("span", { class: "muted" }, "");
  const test = async () => {
    status.textContent = "testing…";
    try { const r = await api("/api/llm/test", { base_url: c.base_url, api_key: c.api_key, model: c.model });
      S.models = r.models || []; status.textContent = `✓ connected · ${S.models.length} models`;
      if (S.models.length && !S.models.includes(c.model)) toast(`Model “${c.model}” not found on the server. Pick one from the list.`, "err");
      render(); }
    catch (e) { status.textContent = "✗ " + e.message; }
  };
  const goal = h("textarea", { placeholder: "e.g. Make a 60 second happy travel vlog, check every clip is upright, use chill lofi music, and render the final video." },
    S.agentGoal || "");
  goal.oninput = () => S.agentGoal = goal.value;
  const steps = h("input", { type: "number", value: 40, min: 5, max: 200 });
  setTimeout(refreshAgentLog, 0);
  return h("div", {},
    h("h1", {}, "AI assistant (local model)"),
    h("p", { class: "lead" }, "Optional. Connect a model running on this computer (Ollama, LM Studio, llama.cpp, MLX). With a vision model it describes every clip, checks orientation, picks the best moments and writes the titles. Without one, everything is decided automatically."),
    !c.enabled ? h("div", { class: "notice" }, "AI is off. Install Ollama (ollama.com), run “ollama pull qwen2.5vl:7b”, then switch on “Use a local AI model” below and press Test connection.") : null,
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h2", {}, "Model"),
        ...fields("llm.enabled", "llm.base_url", "llm.model", "llm.vision", "llm.use_for"),
        h("div", { class: "row", style: "margin:10px 0" }, h("button", { onclick: test }, "Test connection & list models"), status),
        h("h3", {}, "Advanced"), ...fields("llm.temperature", "llm.max_tokens", "llm.timeout", "llm.tool_mode", "llm.json_mode", "llm.images_first", "llm.api_key")),
      h("div", {},
        h("div", { class: "card" }, h("h2", {}, "Smart moments"),
          ...fields("analysis.smart_moments", "analysis.long_clip_chunk", "analysis.max_moments_per_clip"),
          h("div", { class: "row", style: "margin-top:10px" }, btn("Run AI descriptions", "captions", { force: true }, "small", "", true),
            btn("AI orientation check", "orient_ai", {}, "small", "", true))),
        h("div", { class: "card" }, h("h2", {}, "Let the AI make the whole video"),
          h("p", { class: "muted", style: "margin-top:0;font-size:12.5px" }, "The agent uses the same tools you see in this app: it looks at your footage, fixes clips, builds the storyboard, previews, reviews frames and renders."),
          goal, h("div", { class: "row", style: "margin-top:10px" }, h("span", { class: "muted" }, "max steps"), h("div", { style: "width:90px" }, steps),
            h("span", { class: "spacer" }), h("button", { class: "primary", "data-job": true, "data-ai": true, disabled: running() || !c.enabled,
              onclick: () => run("agent", { goal: goal.value, max_steps: parseInt(steps.value) || 40 }, "AI agent") }, "Run AI agent")),
          h("h3", {}, "What the agent did"), h("div", { class: "agentlog", id: "agentLog" }, h("div", { class: "muted" }, "nothing yet"))))));
}
async function refreshAgentLog() {
  const box = $("#agentLog"); if (!box) return;
  const ev = await api("/api/agent_log").catch(() => []);
  if (!ev.length) return;
  box.innerHTML = "";
  for (const e of ev.slice(-60).reverse()) {
    if (e.tool) box.append(h("div", { class: "e" }, h("b", {}, e.tool), " ", h("span", { class: "muted" }, JSON.stringify(e.args || {}).slice(0, 160)), h("pre", {}, (e.result || "").slice(0, 1200))));
    else if (e.assistant && e.assistant.content) box.append(h("div", { class: "e" }, h("b", {}, "model: "), (e.assistant.content || "").slice(0, 600)));
  }
}

function pSettings() {
  const groups = [["Project & text", "project."], ["Colour", "look."], ["Orientation", "orientation."], ["Analysis & smart moments", "analysis."],
                  ["Music", "music."], ["Output", "output."], ["AI model", "llm."]];
  return h("div", {},
    h("h1", {}, "All settings"),
    h("p", { class: "lead" }, `Saved automatically to ${S.state.config_path}. Per-clip fixes are kept in the project's work folder.`),
    h("div", { class: "grid2" }, groups.map(([t, pre]) => h("div", { class: "card" }, h("h2", {}, t),
      ...S.meta.fields.filter(f => f.path.startsWith(pre)).map(f => field(f.path))))));
}

// ---------------------------------------------------------------- boot
async function boot() {
  S.meta = await api("/api/meta");
  const nav = $("#nav");
  for (const [k, ic, label] of PAGES) nav.append(h("button", { "data-p": k, onclick: () => { S.page = k; location.hash = k; render(); } }, h("span", { class: "ic" }, ic), label));
  S.page = (location.hash || "#home").slice(1);
  if (!PAGES.find(p => p[0] === S.page)) S.page = "home";
  $("#projSel").onchange = async e => { await flush(); await api("/api/project/open", { path: e.target.value }); S.logSince = 0; await refreshAll(); toast("Project opened", "ok"); };
  $("#newProj").onclick = async () => {
    await flush();
    try { const r = await api("/api/project/new", {}); if (r.ok) { await refreshAll(); S.page = "home"; render(); toast("New project created — press Analyse footage", "ok"); } }
    catch (e) { toast(e.message, "err"); }
  };
  $("#toggleLog").onclick = () => { const p = $("#logpane"); p.hidden = !p.hidden; $("#toggleLog").textContent = p.hidden ? "Show log" : "Hide log"; };
  $("#closeLog").onclick = () => { $("#logpane").hidden = true; $("#toggleLog").textContent = "Show log"; };
  $("#cancelBtn").onclick = () => api("/api/cancel", {}).then(() => toast("Cancelling…"));
  $("#quitBtn").onclick = async () => { if (!confirm("Stop the Vlog Studio app? (You can start it again from the launcher.)")) return; await flush(); await api("/api/quit", {}).catch(() => {}); document.body.innerHTML = "<div class='empty' style='margin:60px'>Vlog Studio stopped. You can close this tab.</div>"; };
  $("#modalClose").onclick = () => $("#modal").hidden = true;
  $("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").hidden = true; };
  window.addEventListener("hashchange", () => { const p = location.hash.slice(1); if (p && p !== S.page && PAGES.find(x => x[0] === p)) { S.page = p; render(); } });
  await refreshAll();
  if (S.state.job.running) pollJob();
  setInterval(async () => { if (!running()) { try { const st = await api("/api/state"); if (st.job.running && !S.state.job.running) { S.state = st; pollJob(); } } catch (e) {} } }, 5000);
}
boot().catch(e => { document.body.innerHTML = `<div class="empty" style="margin:60px">Could not start: ${e.message}</div>`; });
