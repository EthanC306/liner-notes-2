// Liner Notes frontend: vanilla JS + Chart.js.
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n) => Number(n).toLocaleString();
const KEY_STORE = "linernotes.key";

let key = null;
let charts = [];

function getKey() { try { return localStorage.getItem(KEY_STORE); } catch { return null; } }
function setKey(k) { key = k; try { k ? localStorage.setItem(KEY_STORE, k) : localStorage.removeItem(KEY_STORE); } catch {} }

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 3200);
}

// GET responses are cached per profile + URL (filters are in the URL), so revisiting a tab is instant.
// Any write (upload, save, delete, tag) clears the cache, since it can change what the stats say.
const cache = new Map();

function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  if (method !== "GET") { cache.clear(); return fetchApi(path, opts); }
  const k = `${key}|${path}`;
  if (!cache.has(k)) cache.set(k, fetchApi(path, opts).catch((e) => { cache.delete(k); throw e; }));
  return cache.get(k);
}

async function fetchApi(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (key) headers["X-Profile-Key"] = key;
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}

function filterQuery() {
  const p = new URLSearchParams();
  for (const [id, name] of [["f-start", "start"], ["f-end", "end"], ["f-genre", "genre"]]) {
    const v = $("#" + id).value; if (v) p.set(name, v);
  }
  return p.toString();
}
const stat = (name, extra = "") => api(`/api/stats/${name}?${filterQuery()}${extra}`);

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const palette = () => ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"].map(css);

function chart(canvas, config) {
  Chart.defaults.font.family = "JetBrains Mono, monospace";
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--rule");
  const c = new Chart(canvas, config);
  charts.push(c);
  return c;
}

// ---------- Upload / landing ----------
const status = (msg) => ($("#landing-status").textContent = msg);

// Used by the import page and "Add more data" on the You tab.
async function upload(files, name) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("name", name || "Listener");
  toast("Reading your history…");
  const st = $("#upload-status"); if (st) st.textContent = "Reading your history…";
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    setKey(r.key);
    toast(`Read ${fmt(r.plays_parsed)} plays and ${fmt(r.playlist_tracks)} playlist tracks.`);
    if (st) st.textContent = "";
    start();
  } catch (e) { toast(e.message); if (st) st.textContent = e.message; }
}

const DEMO_STORE = "linernotes.demo";

async function openDemo() {
  // Reuse the demo profile from last time if it still exists, so going back and forth is instant.
  let demoKey = null;
  try { demoKey = localStorage.getItem(DEMO_STORE); } catch {}
  if (demoKey) {
    setKey(demoKey);
    try { await api("/api/me"); return start(); } catch { setKey(null); }
  }
  status("Loading the demo listener…");
  try {
    const r = await api("/api/demo", { method: "POST" });
    setKey(r.key);
    try {
      localStorage.setItem(DEMO_STORE, r.key);
      if (r.friend_key) localStorage.setItem("linernotes.friend", r.friend_key);
    } catch {}
    status("");
    start();
  } catch (e) { status(e.message); }
}

function initLanding() {
  $("#demo-btn").addEventListener("click", openDemo);
  // Account buttons: the landing corner, the demo's Sign up, and the logged-in corner.
  $("#login-btn").addEventListener("click", () => openAuth("login"));
  $("#landing-signup-btn").addEventListener("click", () => openAuth("signup"));
  $("#signup-btn").addEventListener("click", () => openAuth("signup"));
  $("#open-notes-btn").addEventListener("click", () => start());
  $("#logout-btn").addEventListener("click", logout);
  initAuth();
  initImport();
  initSpotify();
}

function showLanding() {
  $("#app").hidden = true; $("#landing").hidden = false;
  document.title = "Liner Notes";
  refreshCorner();
}

// ---------- Accounts ----------
// Logging in returns the profile key, which is what every other request already uses.
const ACCOUNT_STORE = "linernotes.account";
const AUTH_MODES = {
  signup: { title: "Create your account", submit: "Sign up", path: "/api/auth/signup", pw: "Password", ac: "new-password" },
  login: { title: "Log in", submit: "Log in", path: "/api/auth/login", pw: "Password", ac: "current-password" },
  forgot: { title: "Forgot your password?", submit: "Send reset link", path: "/api/auth/forgot" },
  reset: { title: "Choose a new password", submit: "Save password", path: "/api/auth/reset", pw: "New password", ac: "new-password" },
};
let authMode = "signup";
let resetToken = null;

function accountEmail() { try { return localStorage.getItem(ACCOUNT_STORE); } catch { return null; } }
function setAccount(email) { try { email ? localStorage.setItem(ACCOUNT_STORE, email) : localStorage.removeItem(ACCOUNT_STORE); } catch {} }

function refreshCorner() {
  const signedIn = !!(key && accountEmail());
  $("#corner-account").hidden = !signedIn;
  $(".corner-auth:not(#corner-account)").hidden = signedIn;
  $("#home-public").hidden = signedIn;
  $("#home-import").hidden = !signedIn;
  if (signedIn) $("#import-who").textContent = `Logged in as ${accountEmail()}`;
}

// Quick setup: connect Spotify. The server sends us to Spotify's login and back to /home.
async function initSpotify() {
  const btn = $("#spotify-connect"), st = $("#spotify-status");
  try {
    const sp = await api("/api/spotify/status");
    if (!sp.configured) { btn.disabled = true; st.textContent = "Spotify isn't set up on this server yet."; }
    else if (sp.fake) { btn.textContent = "Connect Spotify (sample data)"; st.textContent = "Test mode: uses made-up Spotify data, no login."; }
  } catch {}
  btn.addEventListener("click", async () => {
    btn.disabled = true; st.textContent = "Opening Spotify…";
    try { location.assign((await api("/api/spotify/start", { method: "POST" })).url); }
    catch (e) { st.textContent = e.message; btn.disabled = false; }
  });
  // Coming back from Spotify: /home?spotify=connected or /?spotify_error=...
  const q = new URLSearchParams(location.search);
  if (q.has("spotify") || q.has("spotify_error")) {
    toast(q.get("spotify_error") || "Spotify connected. Here's your first look.");
    history.replaceState(null, "", location.pathname);
  }
}

// Import page drop zone (logged in only).
function initImport() {
  const drop = $("#drop"), input = $("#files");
  let picked = [];
  const ready = () => (drop.querySelector("strong").textContent = `${picked.length} file(s) ready`);
  input.addEventListener("change", () => { picked = [...input.files]; ready(); });
  ["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); picked = [...ev.dataTransfer.files]; ready(); });
  $("#upload-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!picked.length) { $("#upload-status").textContent = "Pick your export first."; return; }
    upload(picked);
  });
}

function setAuthMode(mode) {
  authMode = mode;
  const m = AUTH_MODES[mode];
  $("#auth-title").textContent = m.title;
  $("#auth-submit").textContent = m.submit;
  $("#auth-error").textContent = "";
  $("#auth-submit").disabled = false;
  if (m.pw) { $("#pw-label").textContent = m.pw; $("#auth-form").password.autocomplete = m.ac; }
  document.querySelectorAll("#auth .pw-eye").forEach((b) => {
    b.previousElementSibling.type = "password"; b.setAttribute("aria-pressed", "false"); b.setAttribute("aria-label", "Show password");
  });
  // Show only the fields and links that belong to this mode; hidden inputs are also skipped by validation.
  document.querySelectorAll("#auth [data-for]").forEach((el) => {
    const on = el.dataset.for.split(" ").includes(mode);
    el.hidden = !on;
    el.querySelectorAll("input").forEach((i) => (i.disabled = !on));
  });
}

function openAuth(mode) {
  setAuthMode(mode);
  const d = $("#auth");
  if (!d.open) d.showModal();
  d.querySelector("input:not([disabled])")?.focus();
}

async function submitAuth(e) {
  e.preventDefault();
  const form = $("#auth-form");
  if (!form.reportValidity()) return;
  const body = Object.fromEntries(new FormData(form));
  if (authMode === "signup" || authMode === "reset") {
    if (body.password !== body.confirm) {
      $("#auth-error").textContent = "Passwords don't match.";
      form.confirm.focus();
      return;
    }
    delete body.confirm;
  }
  if (authMode === "reset") body.token = resetToken;
  $("#auth-submit").disabled = true; $("#auth-error").textContent = "";
  try {
    const r = await api(AUTH_MODES[authMode].path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (authMode === "forgot") {
      $("#auth-form").hidden = true;
      $("#auth-error").textContent = "";
      $("#auth-title").textContent = "Check for your reset link";
      $(".auth-links").insertAdjacentHTML("beforebegin", '<p class="auth-sent muted">If an account exists for that email, a reset link has been created. It works for one hour.</p>');
      return;
    }
    setKey(r.key); setAccount(r.email);
    if (authMode === "reset") history.replaceState(null, "", "/");
    $("#auth").close(); form.reset();
    toast(authMode === "signup" ? `Welcome, ${r.name}!` : authMode === "reset" ? "Password updated." : `Welcome back, ${r.name}.`);
    navigate("/");  // logged-in home = import page
  } catch (err) {
    $("#auth-error").textContent = err.message;
    $("#auth-submit").disabled = false;
  }
}

function initAuth() {
  $("#auth-form").addEventListener("submit", submitAuth);
  // Eye buttons show or hide the password next to them.
  document.querySelectorAll("#auth .pw-eye").forEach((b) => b.addEventListener("click", () => {
    const input = b.previousElementSibling, show = input.type === "password";
    input.type = show ? "text" : "password";
    b.setAttribute("aria-pressed", show); b.setAttribute("aria-label", show ? "Hide password" : "Show password");
  }));
  // Live match check on the confirm field.
  const checkMatch = () => {
    const f = $("#auth-form"), mismatch = f.confirm.value && f.confirm.value !== f.password.value;
    f.confirm.classList.toggle("mismatch", !!mismatch);
    f.confirm.setCustomValidity(mismatch ? "Passwords don't match." : "");
  };
  $("#auth-form").password.addEventListener("input", checkMatch);
  $("#auth-form").confirm.addEventListener("input", checkMatch);
  $("#auth-close").addEventListener("click", () => $("#auth").close());
  $("#auth").addEventListener("close", () => {
    $("#auth-form").hidden = false; document.querySelectorAll(".auth-sent").forEach((el) => el.remove());
  });
  document.querySelectorAll("#auth [data-mode]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    $("#auth-form").hidden = false; document.querySelectorAll(".auth-sent").forEach((el) => el.remove());
    setAuthMode(a.dataset.mode);
    $("#auth").querySelector("input:not([disabled])")?.focus();
  }));
  // Reset links look like /?reset=<token>
  resetToken = new URLSearchParams(location.search).get("reset");
  if (resetToken) openAuth("reset");
  // Keep the saved key across visits so links like /stats/top open straight into the app.
  key = getKey();
  refreshCorner();
}

function logout() {
  setKey(null); setAccount(null); cache.clear();
  history.pushState(null, "", "/");
  showLanding();
  toast("Logged out.");
}

// ---------- Pages and URLs ----------
// Every page has its own URL (/stats/top, /explore/mixtape, ...), so the back button and shared links work.
// `filtered` marks the pages whose numbers follow the From / To / Genre filters.
const SECTIONS = [
  { id: "home", label: "Home", pages: [{ id: "home", label: "Overview" }] },
  { id: "stats", label: "Stats", pages: [
    { id: "top", label: "Top", filtered: true },
    { id: "time", label: "Over time", filtered: true },
    { id: "decades", label: "Decades & genres", filtered: true },
    { id: "clock", label: "Clock", filtered: true },
  ] },
  { id: "habits", label: "Habits", pages: [
    { id: "ghosts", label: "Ghosts", filtered: true },
    { id: "love", label: "Skips & love", filtered: true },
    { id: "cleanup", label: "Clean up" },
  ] },
  { id: "explore", label: "Explore", pages: [
    { id: "discover", label: "Discover" },
    { id: "mixtape", label: "Mixtape" },
    { id: "local", label: "Local scene" },
  ] },
  { id: "social", label: "Social", pages: [
    { id: "compare", label: "Compare" },
    { id: "you", label: "You" },
  ] },
];
const pageUrl = (sec, page) => (sec.id === "home" ? "/home" : `/${sec.id}/${page.id}`);

// "/stats/top" -> { sec, page }. "/stats" opens the section's first page. Anything else is not an app page.
function parseRoute(path) {
  const [secId, pageId] = path.replace(/^\/+|\/+$/g, "").split("/");
  const sec = SECTIONS.find((x) => x.id === secId);
  if (!sec) return null;
  const page = sec.pages.find((p) => p.id === pageId) || (!pageId || sec.id === "home" ? sec.pages[0] : null);
  return page ? { sec, page } : null;
}

function navigate(path, { replace = false } = {}) {
  if (path !== location.pathname) history[replace ? "replaceState" : "pushState"](null, "", path);
  route();
}

// Links marked data-link change pages without reloading.
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  navigate(a.getAttribute("href"));
});
window.addEventListener("popstate", () => route());

// Show whatever the URL says: the landing page for "/", otherwise an app page (if there's a profile to show).
async function route() {
  const r = parseRoute(location.pathname);
  if (!r) { showLanding(); return; }
  if (!key) { history.replaceState(null, "", "/"); showLanding(); return; }
  if (bootedKey !== key && !(await boot())) return;
  renderRoute(r);
}

// ---------- App shell ----------
let bootedKey = null;
let hasPlays = true;
let quickMode = false;  // Spotify connected but no streaming history uploaded yet
// Pages that work from the Spotify snapshot in quick mode; every other page shows as locked.
const QUICK_PAGES = new Set(["home", "top", "decades", "you"]);
const isLocked = (pageId) => quickMode && !QUICK_PAGES.has(pageId);

// Load the profile once per key: header text and the filter options.
async function boot() {
  let me;
  try { me = await api("/api/me"); } catch { setKey(null); history.replaceState(null, "", "/"); showLanding(); return false; }
  let demoKey = null;
  try { demoKey = localStorage.getItem(DEMO_STORE); } catch {}
  $("#signup-btn").hidden = !demoKey || demoKey !== key;
  $("#who").textContent = `The listening history of ${me.name}`;
  const ov = await api("/api/stats/overview");
  hasPlays = me.has_plays;
  quickMode = !hasPlays && me.spotify;
  const opts = ['<option value="">Any</option>', ...ov.years.map((y) => `<option>${y}</option>`)].join("");
  $("#f-start").innerHTML = opts; $("#f-end").innerHTML = opts;
  $("#f-genre").innerHTML = '<option value="">All</option>' + ov.genres.map((g) => `<option>${esc(g)}</option>`).join("");
  // In quick mode a section whose pages are all locked is greyed out (still clickable, to explain why).
  $("#side-nav").innerHTML = SECTIONS.map((sec) => {
    const locked = sec.pages.every((p) => isLocked(p.id));
    return `<a href="${pageUrl(sec, sec.pages[0])}" data-link data-sec="${sec.id}" class="${locked ? "locked" : ""}">${esc(sec.label)}${locked ? LOCK : ""}</a>`;
  }).join("");
  bootedKey = key;
  return true;
}

// Enter the app (after the demo, an upload, logging in, or "My liner notes").
async function start(path = "/home") {
  bootedKey = null;  // the profile or its data may have changed
  history.pushState(null, "", path);
  await route();
  // A new account with no data yet goes straight to the upload step.
  if (bootedKey && !hasPlays && !quickMode && path === "/home") navigate("/social/you", { replace: true });
}

let renderSeq = 0;
function renderRoute({ sec, page }) {
  $("#landing").hidden = true; $("#app").hidden = false;
  document.title = `${page.id === "home" ? "Home" : page.label} · Liner Notes`;
  $("#page-title").textContent = sec.id === "home" ? "Home" : sec.label;
  $("#side-nav").querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.sec === sec.id));
  // Sub-tabs only for the current section (none for Home).
  const tabs = $("#tabs");
  tabs.hidden = sec.pages.length < 2;
  tabs.innerHTML = sec.pages.map((p) => `<a href="${pageUrl(sec, p)}" data-link
    class="${[p === page && "active", isLocked(p.id) && "locked"].filter(Boolean).join(" ")}"
    ${p === page ? 'aria-current="page"' : ""}>${esc(p.label)}${isLocked(p.id) ? LOCK : ""}</a>`).join("");
  // Filters only matter on pages whose numbers follow them (and never in quick mode: the snapshot has no years).
  $("#filters").hidden = !page.filtered || quickMode;
  renderChips(page.filtered && !quickMode);

  charts.forEach((c) => c.destroy()); charts = [];
  // Each render draws into a fresh box, so a slow page that finishes after you've moved on can't overwrite the new one.
  const seq = ++renderSeq;
  const box = document.createElement("div");
  box.innerHTML = skeleton();
  $("#view").replaceChildren(box);
  const view = isLocked(page.id) ? (v) => lockedView(v, page) : (quickMode && QUICK_VIEWS[page.id]) || VIEWS[page.id];
  view(box).catch((e) => { if (seq === renderSeq) box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
}
const rerender = () => { const r = parseRoute(location.pathname); if (r) renderRoute(r); };

// ---------- Active filter chips ----------
function renderChips(show) {
  const chips = [["f-start", "From"], ["f-end", "To"], ["f-genre", "Genre"]]
    .filter(([id]) => $("#" + id).value)
    .map(([id, label]) => `<button class="chip" type="button" data-clear="${id}" aria-label="Remove ${label} filter">
      ${label}: ${esc($("#" + id).value)} <span aria-hidden="true">✕</span></button>`);
  const box = $("#filter-chips");
  box.hidden = !show || !chips.length;
  box.innerHTML = `<span class="chip-label">Filtered</span>${chips.join("")}
    ${chips.length > 1 ? '<button class="chip-clear" type="button" data-clear="all">Clear all</button>' : ""}`;
}
$("#filter-chips").addEventListener("click", (e) => {
  const b = e.target.closest("[data-clear]");
  if (!b) return;
  const ids = b.dataset.clear === "all" ? ["f-start", "f-end", "f-genre"] : [b.dataset.clear];
  ids.forEach((id) => ($("#" + id).value = ""));
  rerender();
});

// ---------- Loading states ----------
const skeleton = (cards = 3) => `<div class="skel-wrap" aria-busy="true" aria-label="Loading">
  <div class="skel skel-line" style="width:40%"></div><div class="skel skel-line" style="width:65%"></div>
  <div class="grid">${'<div class="skel skel-card"></div>'.repeat(cards)}</div></div>`;
const spinner = (msg) => `<p class="loading"><span class="spinner" aria-hidden="true"></span>${esc(msg)}</p>`;

// ---------- Fix-it export ----------
async function exportTracks(tracks, filename) {
  const text = await api("/api/actions/export", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tracks }),
  });
  try { await navigator.clipboard.writeText(text); toast("Copied! In Spotify desktop, open a playlist and press Ctrl+V / Cmd+V."); }
  catch { toast("Downloaded. Paste the spotify:track lines into a Spotify desktop playlist."); }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = filename; a.click(); URL.revokeObjectURL(a.href);
}

// ---------- Views ----------
const empty = (msg) => `<div class="empty">${msg}</div>`;
const playlistsBadges = (pls) => pls.map((p) => `<span class="badge hot">in ${esc(p)}</span>`).join(" ");

// ---------- Quick mode (Spotify snapshot only) ----------
const LOCK = `<svg class="lock" viewBox="0 0 24 24" width="12" height="12" role="img" aria-label="locked" fill="none"
  stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><title>Needs your streaming history</title>
  <rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`;
const UNLOCK_NOTE = `Upload your Spotify <b>extended streaming history</b> to unlock this.
  <a href="/" data-link>Go to the import page</a>`;

async function lockedView(v, page) {
  v.innerHTML = `<div class="locked-panel">
    <div class="locked-ghost" aria-hidden="true">${'<div class="skel-static"></div>'.repeat(6)}</div>
    <div class="locked-msg"><p class="kicker">${LOCK} Locked</p><h2>${esc(page.label)}</h2>
      <p>Spotify's quick connection only shares your top songs and artists, not your full history, plays or skips,
      so ${esc(page.label)} can't be worked out yet.</p><p>${UNLOCK_NOTE}</p></div></div>`;
}

const quickBanner = (q) => `<div class="quick-banner"><span>${LOCK} <b>Quick view</b> from Spotify
  (${new Date(q.fetched_at).toLocaleDateString()}). Plays, minutes, skips and history are locked.</span>
  <a class="btn small" href="/" data-link>Unlock everything</a></div>`;

const QUICK_VIEWS = {
  async home(v) {
    const q = await api("/api/spotify/quick");
    const short = q.top.short_term, long = q.top.long_term;
    const card = (kicker, title, sub, href, cta) => `<a class="home-card" href="${href}" data-link><p class="kicker">${esc(kicker)}</p>
      <h3>${esc(title)}</h3>${sub ? `<p class="muted">${esc(sub)}</p>` : ""}<span class="home-cta">${esc(cta)} →</span></a>`;
    const lockedCard = (kicker, title, href) => `<a class="home-card locked" href="${href}" data-link><p class="kicker">${LOCK} ${esc(kicker)}</p>
      <h3>${esc(title)}</h3><p class="muted">Needs your streaming history.</p><span class="home-cta">Why it's locked →</span></a>`;
    v.innerHTML = `${quickBanner(q)}
      <div class="stats">
        <div class="stat locked-stat"><b>—</b><span>hours ${LOCK}</span></div>
        <div class="stat locked-stat"><b>—</b><span>plays ${LOCK}</span></div>
        <div class="stat"><b>${fmt(long.artists.length)}</b><span>top artists</span></div>
        <div class="stat"><b>${fmt(long.tracks.length)}</b><span>top songs</span></div>
        <div class="stat"><b>${fmt(q.playlists.length)}</b><span>playlists</span></div>
      </div>
      <div class="home-cards">
        ${short.artists[0] ? card("Top artist · last 4 weeks", short.artists[0].artist, short.artists[0].genres.slice(0, 2).join(" · "), "/stats/top", "See your top artists") : ""}
        ${short.tracks[0] ? card("Top song · last 4 weeks", short.tracks[0].track, short.tracks[0].artist, "/stats/top", "See your top songs") : ""}
        ${q.genres[0] ? card("Your main genre", q.genres[0][0], q.genres.slice(1, 4).map((g) => g[0]).join(" · "), "/stats/decades", "See genres & decades") : ""}
        ${lockedCard("Biggest ghost", "Who did you stop playing?", "/habits/ghosts")}
        ${lockedCard("When you listen", "Your listening clock", "/stats/clock")}
        ${lockedCard("Rediscovery mixtape", "Songs you loved and forgot", "/explore/mixtape")}
      </div>
      ${q.recent.length ? `<section class="section recent"><p class="kicker">Just played</p><h2>Your last ${q.recent.length} plays</h2>
        <ol class="quick-list">${q.recent.slice(0, 10).map((t) => `<li><b>${esc(t.track)}</b> · ${esc(t.artist)}
          <span class="muted">${new Date(t.played_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span></li>`).join("")}</ol></section>` : ""}`;
  },

  async top(v) {
    const q = await api("/api/spotify/quick");
    let range = "short_term";
    const draw = () => {
      const t = q.top[range];
      v.innerHTML = `${quickBanner(q)}
        <div class="toolbar">Range ${Object.entries(q.ranges).map(([k, label]) =>
          `<button class="btn small ${k === range ? "primary" : ""}" data-range="${k}">${esc(label)}</button>`).join("")}</div>
        <p class="muted">Spotify ranks these but doesn't share play counts. ${LOCK} Counts and minutes unlock with your history.</p>
        <div class="grid two">
          <section><h2>Top songs</h2><ol class="quick-list">${t.tracks.map((x) =>
            `<li><b>${esc(x.track)}</b><br><span class="muted">${esc(x.artist)}${x.year ? ` · ${x.year}` : ""}</span></li>`).join("") || "<li>None yet</li>"}</ol></section>
          <section><h2>Top artists</h2><ol class="quick-list">${t.artists.map((a) =>
            `<li><b>${esc(a.artist)}</b><br>${a.genres.map((g) => `<span class="badge">${esc(g)}</span>`).join(" ")}</li>`).join("") || "<li>None yet</li>"}</ol></section>
        </div>`;
      v.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => { range = b.dataset.range; draw(); }));
    };
    draw();
  },

  async decades(v) {
    const q = await api("/api/spotify/quick");
    v.innerHTML = `${quickBanner(q)}
      <p class="muted">Worked out from your top 50 songs and artists of the last year, not your full library.</p>
      <div class="grid two">
        <section><h2>Decades</h2><div class="chart-box"><canvas id="q-dec"></canvas></div></section>
        <section><h2>Genres</h2><div class="chart-box"><canvas id="q-gen"></canvas></div></section>
      </div>`;
    const c = palette();
    chart($("#q-dec"), { type: "bar", data: { labels: q.decades.map((d) => d[0]),
      datasets: [{ label: "Top songs", data: q.decades.map((d) => d[1]), backgroundColor: c[0] }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } } } });
    chart($("#q-gen"), { type: "bar", data: { labels: q.genres.map((g) => g[0]),
      datasets: [{ label: "Weight", data: q.genres.map((g) => g[1]), backgroundColor: c[1] }] },
      options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } },
        scales: { x: { display: false } } } });
  },
};

// "silence for 300 days", or for a ghost with a few stray recent plays, "only 2 plays in the last 6 months".
const ghostGone = (g) => g.recent_plays
  ? `only ${fmt(g.recent_plays)} play${g.recent_plays > 1 ? "s" : ""} in the last 6 months`
  : `silence for ${fmt(g.days_gone)} days`;

// "Saturdays around 21:00": the busiest weekday + hour in a clock grid.
function peakHour({ grid }) {
  const days = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];
  const [, d, h] = grid.flatMap((r, d) => r.map((n, h) => [n, d, h])).sort((a, b) => b[0] - a[0])[0];
  return `${days[d]} around ${h}:00`;
}

// A card on Home that fills itself in when its data arrives and links into its section.
function homeCard(v, id, { kicker, href }, load) {
  const card = v.querySelector(`[data-card="${id}"]`);
  card.innerHTML = `<p class="kicker">${esc(kicker)}</p><div class="skel skel-line" style="width:70%"></div>
    <div class="skel skel-line" style="width:45%"></div>`;
  card.href = href;
  load().then(({ title, sub, cta }) => {
    card.innerHTML = `<p class="kicker">${esc(kicker)}</p><h3>${esc(title)}</h3>
      ${sub ? `<p class="muted">${esc(sub)}</p>` : ""}<span class="home-cta">${esc(cta)} →</span>`;
  }).catch(() => {
    card.innerHTML = `<p class="kicker">${esc(kicker)}</p><p class="muted">Couldn't load this one.</p><span class="home-cta">Open →</span>`;
  });
}

const VIEWS = {
  async home(v) {
    const ov = await api("/api/stats/overview");
    if (!ov.plays) {
      v.innerHTML = empty('No listening data yet. <a href="/social/you" data-link>Upload your Spotify export</a> to fill this in.');
      return;
    }
    const year = ov.years[ov.years.length - 1];
    v.innerHTML = `
      <div class="stats">
        <div class="stat"><b>${fmt(Math.round(ov.minutes / 60))}</b><span>hours</span></div>
        <div class="stat"><b>${fmt(ov.plays)}</b><span>plays</span></div>
        <div class="stat"><b>${fmt(ov.artists)}</b><span>artists</span></div>
        <div class="stat"><b>${fmt(ov.tracks)}</b><span>songs</span></div>
        <div class="stat"><b>${ov.first.slice(0, 4)}–${ov.last.slice(2, 4)}</b><span>span</span></div>
      </div>
      <div class="home-cards">
        ${["artist", "song", "ghost", "next", "mixtape", "clock"].map((id) => `<a class="home-card" data-link data-card="${id}"></a>`).join("")}
      </div>`;
    const topYear = api(`/api/stats/top?start=${year}&end=${year}`);
    homeCard(v, "artist", { kicker: `Top artist of ${year}`, href: "/stats/top" }, async () => {
      const a = (await topYear).artists[0];
      return a ? { title: a.artist, sub: `${fmt(a.minutes)} minutes · ${fmt(a.plays)} plays`, cta: "See your top artists" }
               : { title: "Nothing yet", cta: "See your top artists" };
    });
    homeCard(v, "song", { kicker: `Most-played song of ${year}`, href: "/stats/top" }, async () => {
      const t = (await topYear).tracks[0];
      return t ? { title: t.track, sub: `${t.artist} · ${fmt(t.plays)} plays`, cta: "See your top songs" }
               : { title: "Nothing yet", cta: "See your top songs" };
    });
    homeCard(v, "ghost", { kicker: "Biggest ghost", href: "/habits/ghosts" }, async () => {
      const g = (await api("/api/stats/ghosts"))[0];
      return g ? { title: g.artist, sub: `${fmt(g.plays)} plays, then ${ghostGone(g)}`, cta: "Meet your ghosts" }
               : { title: "No ghosts", sub: "You haven't dropped anyone you used to love.", cta: "Open Ghosts" };
    });
    homeCard(v, "next", { kicker: "Try next", href: "/explore/discover" }, async () => {
      const c = (await api("/api/recommendations?ai=false")).items[0];
      return c ? { title: c.artist, sub: c.because?.length ? `Because you play ${c.because.slice(0, 2).join(" and ")}` : "", cta: "More to discover" }
               : { title: "Still looking", sub: "Similar-artist data is still being looked up.", cta: "Open Discover" };
    });
    homeCard(v, "mixtape", { kicker: "Rediscovery mixtape", href: "/explore/mixtape" }, async () => (
      { title: "Songs you loved and forgot", sub: "A tape built from your old favorites, walked through your eras.", cta: "Make a mixtape" }));
    homeCard(v, "clock", { kicker: "When you listen", href: "/stats/clock" }, async () => {
      const c = await api("/api/stats/clock");
      return { title: peakHour(c), sub: "Your busiest time for music", cta: "See your listening clock" };
    });
  },

  async top(v) {
    const by = VIEWS.top.by || "plays";
    const d = await stat("top", `&by=${by}`);
    const max = (rows, k) => Math.max(1, ...rows.map((r) => r[k]));
    const mt = max(d.tracks, by), ma = max(d.artists, by);
    v.innerHTML = `
      <div class="toolbar"><span class="muted">Rank by</span>
        <button class="btn small ${by === "plays" ? "primary" : ""}" data-by="plays">Plays</button>
        <button class="btn small ${by === "minutes" ? "primary" : ""}" data-by="minutes">Minutes</button></div>
      <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr))">
        <section><h2>Top songs</h2><div class="table-wrap"><table><thead><tr><th></th><th>Song</th><th class="num">${by}</th></tr></thead><tbody>
          ${d.tracks.slice(0, 30).map((t, i) => `<tr><td class="rank">${i + 1}</td><td>${esc(t.track)}<div class="sub">${esc(t.artist)}</div>
            <div class="bar"><i style="width:${(100 * t[by]) / mt}%"></i></div></td><td class="num">${fmt(t[by])}</td></tr>`).join("")}
        </tbody></table></div></section>
        <section><h2>Top artists</h2><div class="table-wrap"><table><thead><tr><th></th><th>Artist</th><th class="num">${by}</th></tr></thead><tbody>
          ${d.artists.slice(0, 30).map((a, i) => `<tr><td class="rank">${i + 1}</td><td>${esc(a.artist)}
            <div class="sub">since ${a.since} ${a.genres.map((g) => `<span class="badge">${esc(g)}</span>`).join(" ")}</div>
            <div class="bar"><i style="width:${(100 * a[by]) / ma}%"></i></div></td><td class="num">${fmt(a[by])}</td></tr>`).join("")}
        </tbody></table></div></section>
      </div>`;
    v.querySelectorAll("[data-by]").forEach((b) => b.addEventListener("click", () => { VIEWS.top.by = b.dataset.by; render(); }));
  },

  async time(v) {
    const d = await stat("timeline");
    v.innerHTML = `<h2>Who you listened to over time</h2><p class="muted">Minutes per month for your top artists in this range.</p>
      <div class="chart-box tall"><canvas id="tl"></canvas></div>`;
    const pal = palette();
    chart($("#tl"), {
      type: "line",
      data: { labels: d.months, datasets: d.series.map((s, i) => ({ label: s.artist, data: s.data, borderColor: pal[i % pal.length],
        backgroundColor: pal[i % pal.length], tension: 0.35, pointRadius: 0, borderWidth: 2 })) },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } }, scales: { y: { beginAtZero: true, title: { display: true, text: "minutes" } } } },
    });
  },

  async ghosts(v) {
    const g = await stat("ghosts");
    v.innerHTML = `<p class="kicker">Artists you ghosted</p><h2>They didn't leave. You did.</h2>
      <p class="muted">Artists you played a lot who've barely come up in your last 6 months.</p>
      <div class="toolbar"><button class="btn primary" id="ghost-pl">Build a playlist of your ghosts</button></div>
      ${g.length ? `<div class="grid">${g.map((x) => `<article class="ghost"><p class="gone">${x.recent_plays ? `only ${fmt(x.recent_plays)} play${x.recent_plays > 1 ? "s" : ""} in 6 months` : `gone ${fmt(x.days_gone)} days`}</p>
        <h3>${esc(x.artist)}</h3><p class="sub">${fmt(x.plays)} plays · peaked ${x.peak_month} (${x.peak_plays} plays)<br>last played ${x.last_played}</p></article>`).join("")}</div>`
        : empty("No ghosts. You're loyal.")}`;
    $("#ghost-pl").addEventListener("click", async () => exportTracks(await api("/api/actions/ghost-playlist"), "ghosts-playlist.txt"));
  },

  async decades(v) {
    const [d, g] = await Promise.all([stat("decades"), stat("genres")]);
    v.innerHTML = `<div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr))">
      <section><h2>Decades</h2><p class="muted">Plays by release decade. ${fmt(d.unknown)} plays still waiting on release-year lookups.</p>
        ${d.decades.length ? '<div class="chart-box"><canvas id="dec"></canvas></div>' : empty("Release years are still being looked up. Check back soon.")}</section>
      <section><h2>Genres</h2><p class="muted">Minutes by each artist's main genre. Use the Genre filter up top to slice every page.</p>
        ${g.genres.length ? '<div class="chart-box"><canvas id="gen"></canvas></div>' : empty("Genres are still being looked up.")}</section></div>`;
    const accent = css("--accent");
    if (d.decades.length) chart($("#dec"), { type: "bar",
      data: { labels: d.decades.map((x) => x.decade), datasets: [{ data: d.decades.map((x) => x.plays), backgroundColor: accent, borderRadius: 3 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false },
        tooltip: { callbacks: { afterBody: (it) => d.decades[it[0].dataIndex].examples } } } } });
    if (g.genres.length) chart($("#gen"), { type: "bar",
      data: { labels: g.genres.map((x) => x.genre), datasets: [{ data: g.genres.map((x) => x.minutes), backgroundColor: css("--c2"), borderRadius: 3 }] },
      options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } },
        onClick: (_, els) => { if (els[0]) { $("#f-genre").value = g.genres[els[0].index].genre; render(); } } } });
  },

  async clock(v) {
    const { grid } = await stat("clock");
    const max = Math.max(1, ...grid.flat());
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const accent = css("--accent");
    let cells = '<div class="lbl"></div>' + Array.from({ length: 24 }, (_, h) => `<div class="hr">${h % 3 ? "" : h}</div>`).join("");
    grid.forEach((row, d) => {
      cells += `<div class="lbl">${days[d]}</div>` + row.map((n, h) =>
        `<div title="${days[d]} ${h}:00 · ${n} plays" style="background:color-mix(in srgb, ${accent} ${Math.round((100 * n) / max)}%, var(--paper-2))"></div>`).join("");
    });
    const busiest = grid.flatMap((r, d) => r.map((n, h) => [n, d, h])).sort((a, b) => b[0] - a[0])[0];
    v.innerHTML = `<h2>Your listening clock</h2><p class="muted">Peak: ${days[busiest[1]]}s around ${busiest[2]}:00.</p><div class="clock">${cells}</div>`;
  },

  async love(v) {
    const [sk, lv] = await Promise.all([stat("skips"), stat("love")]);
    const row = (t, cols) => `<tr><td>${esc(t.track)}<div class="sub">${esc(t.artist)} ${playlistsBadges(t.playlists)}</div></td>${cols}</tr>`;
    v.innerHTML = `
      <section class="section"><p class="kicker">Skips as data</p><h2>Songs you keep skipping but never remove</h2>
        <p class="muted">Skipped at least half the time. The ones still sitting in your playlists come first.${""}</p>
        <div class="toolbar"><button class="btn primary" id="skip-exp">Export the skipped playlist songs</button></div>
        ${sk.length ? `<div class="table-wrap"><table><thead><tr><th>Song</th><th class="num">skipped</th><th class="num">rate</th></tr></thead><tbody>
          ${sk.map((t) => row(t, `<td class="num">${t.skips}/${t.attempts}</td><td class="num">${Math.round(t.skip_rate * 100)}%</td>`)).join("")}
        </tbody></table></div>` : empty("No chronic skips.")}</section>
      <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr))">
        <section><p class="kicker">Count vs. love</p><h2>Loved, barely played</h2>
          <p class="muted">Always finished, never skipped, only a few plays. Maybe they deserve more.</p>
          ${lv.loved_rarely.length ? `<table><tbody>${lv.loved_rarely.map((t) => row(t, `<td class="num">${t.plays}×</td>`)).join("")}</tbody></table>` : empty("Nothing here yet.")}
          ${lv.loved_rarely.length ? '<div class="toolbar"><button class="btn" id="love-exp">Make these a playlist</button></div>' : ""}</section>
        <section><p class="kicker">Count vs. love</p><h2>Played a lot, not loved</h2>
          <p class="muted">High play count, high skip rate. Probably shuffle filler.</p>
          ${lv.played_not_loved.length ? `<table><tbody>${lv.played_not_loved.map((t) => row(t, `<td class="num">${Math.round(t.skip_rate * 100)}% skipped</td>`)).join("")}</tbody></table>` : empty("Nothing here.")}</section>
      </div>`;
    $("#skip-exp").addEventListener("click", () => exportTracks(sk.filter((t) => t.playlists.length), "skipped-in-playlists.txt"));
    $("#love-exp")?.addEventListener("click", () => exportTracks(lv.loved_rarely, "loved-rarely.txt"));
  },

  async cleanup(v) {
    const days = VIEWS.cleanup.days || 365;
    const d = await api(`/api/actions/dead-weight?days=${days}`);
    v.innerHTML = `<p class="kicker">Fix-it actions</p><h2>Dead weight</h2>
      <p class="muted">Songs in your playlists you haven't played in
        <select id="dw-days">${[90, 180, 365, 730].map((n) => `<option ${n === days ? "selected" : ""} value="${n}">${n} days</option>`).join("")}</select>.
        Export a list, then in Spotify desktop select those songs and remove them.</p>
      ${d.length ? d.map((pl, i) => `<section class="section"><div class="toolbar"><h3 style="margin:0">${esc(pl.playlist)}</h3>
          <span class="badge hot">${pl.dead.length} of ${pl.size} unplayed</span>
          <button class="btn small" data-i="${i}">Export list</button></div>
          <table><tbody>${pl.dead.map((t) => `<tr><td>${esc(t.track)}<div class="sub">${esc(t.artist)}</div></td></tr>`).join("")}</tbody></table></section>`).join("")
        : empty("No dead weight. Every playlist song has been played recently. (Did you upload Playlist1.json?)")}`;
    $("#dw-days").addEventListener("change", (e) => { VIEWS.cleanup.days = +e.target.value; render(); });
    v.querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", () =>
      exportTracks(d[+b.dataset.i].dead, `dead-weight-${d[+b.dataset.i].playlist}.txt`)));
  },

  async discover(v) {
    v.innerHTML = `<p class="kicker">Discover</p><h2>Who to try next</h2>${spinner("Ranking candidates from real similar-artist data…")}${skeleton(6)}`;
    const r = await api("/api/recommendations");
    v.innerHTML = `<p class="kicker">Discover</p><h2>Who to try next</h2>
      <p class="muted">${r.ai ? "Ranked and explained by the local AI, which can only pick from real similar-artist data." : "Ranked by similarity to what you play now. (The local AI is off, so no written explanations.)"}</p>
      ${r.items.length ? `<div class="grid">${r.items.map((c) => `<article class="ghost"><h3>${esc(c.artist)}</h3>
        <p>${esc(c.why)}</p>${c.tracks?.length ? `<p class="sub">Start with: ${c.tracks.map(esc).join(" · ")}</p>` : ""}
        <p><a href="https://open.spotify.com/search/${encodeURIComponent(c.artist)}" target="_blank" rel="noopener">Open in Spotify ↗</a></p></article>`).join("")}</div>`
        : empty("No recommendations yet. Similar-artist data is still being looked up, or LASTFM_API_KEY isn't set.")}`;
  },

  async compare(v) {
    v.innerHTML = `<p class="kicker">Friend comparison</p><h2>Taste compatibility</h2>
      <p class="muted">Ask a friend to upload their export, then paste their profile key (from their "You" tab).</p>
      <form class="inline" id="cmp-form" style="max-width:560px"><input id="cmp-key" placeholder="Friend's profile key" required value="${esc((() => { try { return localStorage.getItem("linernotes.friend") || ""; } catch { return ""; } })())}"><button class="btn primary">Compare</button></form>
      <div id="cmp-out" style="margin-top:24px"></div>`;
    $("#cmp-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = $("#cmp-out");
      try {
        const c = await api(`/api/compare?friend_key=${encodeURIComponent($("#cmp-key").value.trim())}`);
        out.innerHTML = `<div class="compat">${c.compatibility}%</div><p>compatible: <b>${esc(c.a)}</b> &amp; <b>${esc(c.b)}</b> share ${c.shared_count} artists
          (${c.only_a} only ${esc(c.a)}, ${c.only_b} only ${esc(c.b)}).</p>
          <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr))">
          <section><h3>Shared artists</h3><table><thead><tr><th>Artist</th><th class="num">${esc(c.a)}</th><th class="num">${esc(c.b)}</th></tr></thead><tbody>
            ${c.shared.map((s) => `<tr><td>${esc(s.artist)}</td><td class="num">${s.a_share}%</td><td class="num">${s.b_share}%</td></tr>`).join("")}</tbody></table></section>
          <section><h3>Who got who into what</h3><table><tbody>
            ${c.intros.map((i) => `<tr><td><b>${esc(i.first_by === "a" ? c.a : c.b)}</b> found <b>${esc(i.artist)}</b> first</td><td class="num">${fmt(i.days_ahead)} days ahead</td></tr>`).join("") || "<tr><td>You found everything together.</td></tr>"}
          </tbody></table>
          <h3 style="margin-top:20px">Genres</h3><p>${esc(c.a)}: ${c.genres_a.map((g) => `<span class="badge">${esc(g)}</span>`).join(" ")}</p>
          <p>${esc(c.b)}: ${c.genres_b.map((g) => `<span class="badge">${esc(g)}</span>`).join(" ")}</p></section></div>`;
      } catch (err) { out.innerHTML = empty(esc(err.message)); }
    });
  },

  async local(v) {
    const draw = (tags) => {
      $("#local-list").innerHTML = tags.length ? `<table><thead><tr><th>Artist</th><th>City</th><th class="num">your plays</th><th></th></tr></thead><tbody>
        ${tags.map((t) => `<tr><td>${esc(t.artist)}</td><td>${esc(t.city)}</td><td class="num">${fmt(t.plays)}</td>
        <td class="num"><button class="btn small" data-del="${t.id}">remove</button></td></tr>`).join("")}</tbody></table>` : empty("Tag the local artists you listen to and build your own scene map.");
      v.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () =>
        draw(await api(`/api/local/${b.dataset.del}`, { method: "DELETE" }))));
    };
    const artists = (await stat("top", "&by=plays")).artists;
    v.innerHTML = `<p class="kicker">Local scene</p><h2>Your local scene</h2><p class="muted">Tag artists from your area. Local music is more personal than any algorithm.</p>
      <form class="toolbar" id="local-form"><input id="la" list="artist-list" placeholder="Artist" required>
        <datalist id="artist-list">${artists.map((a) => `<option value="${esc(a.artist)}">`).join("")}</datalist>
        <input id="lc" placeholder="City (e.g. Athens, OH)" required><button class="btn primary">Tag as local</button></form>
      <div id="local-list"></div>`;
    $("#local-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      draw(await api("/api/local", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ artist: $("#la").value, city: $("#lc").value }) }));
      e.target.reset();
    });
    draw(await api("/api/local"));
  },

  async mixtape(v) {
    v.innerHTML = `<p class="kicker">Side B</p><h2>Rediscovery Mixtape</h2>
      <p class="muted">Songs you loved, played to death and never skipped, then quietly stopped playing.</p>
      <div class="toolbar mix-controls">
        <label>Length <input type="range" id="mx-len" min="5" max="30" value="15"> <b id="mx-len-v">15</b></label>
        <label>Gone at least <input type="range" id="mx-gone" min="1" max="24" value="6"> <b id="mx-gone-v">6</b> mo</label>
        <label class="check"><input type="checkbox" id="mx-ai" checked> AI liner notes</label>
      </div>
      <div id="mx-out">${spinner("Rewinding…")}</div>
      <section class="section" id="mx-saved"></section>`;
    let tape = null;
    const draw = async () => {
      $("#mx-len-v").textContent = $("#mx-len").value; $("#mx-gone-v").textContent = $("#mx-gone").value;
      $("#mx-out").innerHTML = spinner("Rewinding…");
      tape = await api(`/api/mixtape?length=${$("#mx-len").value}&months_gone=${$("#mx-gone").value}&ai=${$("#mx-ai").checked}`);
      if (!tape.tracks.length) { $("#mx-out").innerHTML = empty("Nothing you loved has gone quiet that long. Try a shorter gap."); return; }
      $("#mx-out").innerHTML = `${cassette(tape.title, tape.tracks, tape.ai ? "liner notes by AI" : "")}
        <div class="toolbar"><button class="btn primary" id="mx-copy">Copy to Spotify</button>
          <button class="btn" id="mx-save">Save &amp; get share link</button></div>
        ${tape.fresh.length ? `<h3>Bonus: something new</h3><ul class="plain">${tape.fresh.map((f) =>
          `<li><b>${esc(f.track)}</b> · ${esc(f.artist)} <span class="muted">(if you miss ${esc(f.because)})</span></li>`).join("")}</ul>` : ""}`;
      $("#mx-copy").addEventListener("click", () => exportTracks(tape.tracks, "rediscovery-mixtape.txt"));
      $("#mx-save").addEventListener("click", async () => {
        const { slug } = await api("/api/mixtape", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: tape.title, tracks: tape.tracks }) });
        const link = `${location.origin}/?mixtape=${slug}`;
        try { await navigator.clipboard.writeText(link); toast("Saved! Share link copied."); } catch { toast(`Saved: ${link}`); }
        drawSaved();
      });
    };
    const drawSaved = async () => {
      const list = await api("/api/mixtapes");
      $("#mx-saved").innerHTML = list.length ? `<h3>Your saved tapes</h3><ul class="plain">${list.map((m) =>
        `<li><a href="/?mixtape=${esc(m.slug)}" target="_blank">${esc(m.title)}</a> <span class="muted">${m.count} tracks · ${m.created}</span>
         <button class="btn small" data-del="${esc(m.slug)}">delete</button></li>`).join("")}</ul>` : "";
      v.querySelectorAll("#mx-saved [data-del]").forEach((b) => b.addEventListener("click", async () => {
        await api(`/api/mixtape/${b.dataset.del}`, { method: "DELETE" }); drawSaved(); }));
    };
    $("#mx-len").addEventListener("input", () => ($("#mx-len-v").textContent = $("#mx-len").value));
    $("#mx-gone").addEventListener("input", () => ($("#mx-gone-v").textContent = $("#mx-gone").value));
    ["mx-len", "mx-gone", "mx-ai"].forEach((id) => $("#" + id).addEventListener("change", draw));
    await Promise.all([draw(), drawSaved()]);
  },

  async you(v) {
    const me = await api("/api/me");
    v.innerHTML = `<h2>${esc(me.name)}</h2>
      ${me.email ? `<p class="muted">Logged in as ${esc(me.email)}</p>` : ""}
      <section class="section"><h3>Your profile key</h3><p class="muted">This is your login. Keep it to come back, share it with a friend to compare, or use it to add more data.</p>
        <div class="keybox">${esc(key)}</div></section>
      <section class="section"><h3>Add more data</h3><p class="muted">${me.extended ? "Extended history loaded, so skips are exact." : "Only basic history so far, so skips are estimated (plays under 30s). Upload your extended history when it arrives."}
        Duplicates are merged automatically.</p>
        <input type="file" id="more" multiple accept=".zip,.json"> <button class="btn" id="more-btn">Upload</button></section>
      <section class="section"><h3>Privacy</h3><p class="muted">Raw files were never stored. This deletes every play, playlist and tag tied to this key.</p>
        <button class="btn danger" id="del">Delete everything</button> <button class="btn ghost" id="out">Log out</button></section>`;
    $("#more-btn").addEventListener("click", () => { const f = $("#more").files; if (f.length) upload(f, me.name); });
    $("#out").addEventListener("click", logout);
    $("#del").addEventListener("click", async () => {
      if (!confirm("Delete all of your data permanently?")) return;
      await api("/api/me", { method: "DELETE" }); setKey(null); setAccount(null); location.reload();
    });
  },
};

function cassette(title, tracks, tag) {
  return `<article class="cassette"><header><span class="reel"></span><h3>${esc(title)}</h3><span class="reel"></span></header>
    ${tag ? `<p class="kicker">${esc(tag)}</p>` : ""}
    <ol class="tracklist">${tracks.map((t) => `<li><div><b>${esc(t.track)}</b> · ${esc(t.artist)}
      <span class="badge">${esc(t.era)}</span></div><p class="note">${esc(t.note)}</p></li>`).join("")}</ol></article>`;
}

async function showShared(slug) {
  $("#landing").hidden = true;
  const v = $("#shared"); v.hidden = false;
  try {
    const m = await api(`/api/mixtape/${encodeURIComponent(slug)}`);
    v.innerHTML = `<p class="kicker">A mixtape from ${esc(m.by)} · ${m.created}</p>${cassette(m.title, m.tracks, "")}
      <div class="toolbar"><button class="btn primary" id="sh-copy">Copy to Spotify</button>
      <a class="btn ghost" href="/">Make your own</a></div>`;
    $("#sh-copy").addEventListener("click", () => exportTracks(m.tracks, "mixtape.txt"));
  } catch (e) { v.innerHTML = empty(esc(e.message)) + '<a class="btn ghost" href="/">Liner Notes</a>'; }
}

// ---------- Boot ----------
["f-start", "f-end", "f-genre"].forEach((id) => $("#" + id).addEventListener("change", rerender));
initLanding();
const sharedSlug = new URLSearchParams(location.search).get("mixtape");
if (sharedSlug) showShared(sharedSlug);
else route();
