// Liner Notes frontend: vanilla JS + Chart.js.
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n) => Number(n).toLocaleString();
const KEY_STORE = "linernotes.key";

let key = null;
let charts = [];
let currentTab = "story";

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
async function upload(files, name) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("name", name || "Listener");
  $("#upload-status").textContent = "Reading your history…";
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    setKey(r.key);
    toast(`Read ${fmt(r.plays_parsed)} plays and ${fmt(r.playlist_tracks)} playlist tracks.`);
    start();
  } catch (e) {
    $("#upload-status").textContent = e.message;
  }
}

function initLanding() {
  const drop = $("#drop"), input = $("#files");
  let picked = [];
  input.addEventListener("change", () => { picked = [...input.files]; drop.querySelector("strong").textContent = `${picked.length} file(s) ready`; });
  ["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); picked = [...ev.dataTransfer.files]; drop.querySelector("strong").textContent = `${picked.length} file(s) ready`; });
  $("#upload-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!picked.length) { $("#upload-status").textContent = "Pick your export first."; return; }
    upload(picked, ev.target.name.value);
  });
  $("#key-form").addEventListener("submit", (ev) => { ev.preventDefault(); setKey($("#key-input").value.trim()); start(); });
  $("#demo-btn").addEventListener("click", async () => {
    $("#upload-status").textContent = "Loading the demo listener…";
    try {
      const r = await api("/api/demo", { method: "POST" });
      setKey(r.key);
      if (r.friend_key) try { localStorage.setItem("linernotes.friend", r.friend_key); } catch {}
      start();
    }
    catch (e) { $("#upload-status").textContent = e.message; }
  });
}

// ---------- App shell ----------
async function start() {
  let me;
  try { me = await api("/api/me"); } catch { setKey(null); $("#landing").hidden = false; $("#app").hidden = true; return; }
  $("#landing").hidden = true; $("#app").hidden = false;
  $("#who").textContent = `The listening history of ${me.name}`;
  const ov = await api("/api/stats/overview");
  const opts = ['<option value="">Any</option>', ...ov.years.map((y) => `<option>${y}</option>`)].join("");
  $("#f-start").innerHTML = opts; $("#f-end").innerHTML = opts;
  $("#f-genre").innerHTML = '<option value="">All</option>' + ov.genres.map((g) => `<option>${esc(g)}</option>`).join("");
  render();
}

function render() {
  charts.forEach((c) => c.destroy()); charts = [];
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === currentTab));
  const view = $("#view");
  view.innerHTML = '<p class="muted">Loading…</p>';
  (VIEWS[currentTab] || VIEWS.story)(view).catch((e) => (view.innerHTML = `<div class="empty">${esc(e.message)}</div>`));
}

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

const VIEWS = {
  async story(v) {
    const [ov, eras] = await Promise.all([stat("overview"), stat("eras")]);
    v.innerHTML = `
      <div class="stats">
        <div class="stat"><b>${fmt(Math.round(ov.minutes / 60))}</b><span>hours</span></div>
        <div class="stat"><b>${fmt(ov.plays)}</b><span>plays</span></div>
        <div class="stat"><b>${fmt(ov.artists)}</b><span>artists</span></div>
        <div class="stat"><b>${fmt(ov.tracks)}</b><span>songs</span></div>
        <div class="stat"><b>${ov.first ? ov.first.slice(0, 4) + "–" + ov.last.slice(2, 4) : "—"}</b><span>span</span></div>
      </div>
      <section class="section"><p class="kicker">Liner notes</p><p class="summary" id="summary">Writing your liner notes…</p></section>
      <section class="section"><p class="kicker">Tracklist</p><h2>Your eras</h2>
        ${eras.length ? `<ol class="eras">${eras.map((e) => `<li><div><h3>${esc(e.headline.replace("Your ", ""))}</h3>
          <p>${esc(e.top.join(" · "))}</p></div><span class="badge">${fmt(Math.round(e.minutes / 60))} h</span></li>`).join("")}</ol>`
          : empty("No plays in this range.")}
      </section>`;
    api("/api/summary").then((s) => { const el = $("#summary"); if (el) el.textContent = s.text; }).catch(() => {});
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
      ${g.length ? `<div class="grid">${g.map((x) => `<article class="ghost"><p class="gone">gone ${fmt(x.days_gone)} days</p>
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
    v.innerHTML = `<p class="kicker">Discover</p><h2>Who to try next</h2><p class="muted">Ranking candidates from real similar-artist data…</p>`;
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
      <div id="mx-out"><p class="muted">Rewinding…</p></div>
      <section class="section" id="mx-saved"></section>`;
    let tape = null;
    const draw = async () => {
      $("#mx-len-v").textContent = $("#mx-len").value; $("#mx-gone-v").textContent = $("#mx-gone").value;
      $("#mx-out").innerHTML = '<p class="muted">Rewinding…</p>';
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

  async settings(v) {
    const me = await api("/api/me");
    v.innerHTML = `<h2>${esc(me.name)}</h2>
      <section class="section"><h3>Your profile key</h3><p class="muted">This is your login. Keep it to come back, share it with a friend to compare, or use it to add more data.</p>
        <div class="keybox">${esc(key)}</div></section>
      <section class="section"><h3>Add more data</h3><p class="muted">${me.extended ? "Extended history loaded, so skips are exact." : "Only basic history so far, so skips are estimated (plays under 30s). Upload your extended history when it arrives."}
        Duplicates are merged automatically.</p>
        <input type="file" id="more" multiple accept=".zip,.json"> <button class="btn" id="more-btn">Upload</button></section>
      <section class="section"><h3>Privacy</h3><p class="muted">Raw files were never stored. This deletes every play, playlist and tag tied to this key.</p>
        <button class="btn danger" id="del">Delete everything</button> <button class="btn ghost" id="out">Sign out</button></section>`;
    $("#more-btn").addEventListener("click", () => { const f = $("#more").files; if (f.length) upload(f, me.name); });
    $("#out").addEventListener("click", () => { setKey(null); location.reload(); });
    $("#del").addEventListener("click", async () => {
      if (!confirm("Delete all of your data permanently?")) return;
      await api("/api/me", { method: "DELETE" }); setKey(null); location.reload();
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
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { currentTab = b.dataset.tab; render(); }));
["f-start", "f-end", "f-genre"].forEach((id) => $("#" + id).addEventListener("change", render));
initLanding();
const sharedSlug = new URLSearchParams(location.search).get("mixtape");
if (sharedSlug) showShared(sharedSlug);
// Always open on the landing page; a saved profile is one click away.
if (getKey()) {
  const resume = $("#resume-btn");
  resume.hidden = false;
  resume.addEventListener("click", () => { key = getKey(); start(); });
}
