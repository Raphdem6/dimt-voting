/* Public live results dashboard. Polls the JSON API every 10s. */
(function () {
  const REFRESH_MS = 10000;
  const COLORS = ["#1f5c2e", "#c9a227", "#1c2a6b", "#8a4b9c", "#2a8fa5", "#b5543c", "#6b7d3a", "#555555"]; // colour-blind friendly spread
  const $ = (id) => document.getElementById(id);
  const API_URL = document.getElementById("app").dataset.api;
  const charts = {};
  let active = null, lastData = null, endTime = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function draw(key, canvasId, config) {
    if (typeof Chart === "undefined") return;           // offline: the tables still work
    if (charts[key]) { charts[key].destroy(); }
    const c = $(canvasId);
    if (!c) return;
    charts[key] = new Chart(c, config);
  }

  function setStatus(d) {
    const s = $("el-status");
    s.textContent = {upcoming: "Upcoming", open: "Voting open", closed: "Closed", certified: "Results certified"}[d.election.status];
    $("el-prov").hidden = !d.election.provisional;
    endTime = new Date(d.election.end);
  }

  function tick() {
    const cd = $("countdown");
    if (!endTime || !lastData) return;
    const ms = endTime - new Date();
    if (lastData.election.status === "open" && ms > 0) {
      const h = Math.floor(ms / 3600000), m = Math.floor(ms % 3600000 / 60000), s = Math.floor(ms % 60000 / 1000);
      cd.textContent = "Closes in " + (h >= 24 ? Math.floor(h / 24) + "d " + (h % 24) + "h " : h + "h ") + m + "m " + s + "s";
    } else cd.textContent = "";
  }

  function renderTurnout(t) {
    $("stats").hidden = false;
    $("s-eligible").textContent = t.eligible;
    $("s-voted").textContent = t.voted;
    $("s-pct").textContent = t.percent + "%";
    $("turnout-panel").hidden = false;
    draw("turnout", "ch-turnout", {
      type: "doughnut",
      data: {labels: ["Voted", "Not yet voted"], datasets: [{data: [t.voted, Math.max(t.eligible - t.voted, 0)], backgroundColor: ["#1f5c2e", "#dfe6e1"]}]},
      options: {maintainAspectRatio: false, plugins: {legend: {position: "bottom"}}}
    });
    draw("branch", "ch-branch", {
      type: "bar",
      data: {labels: t.by_branch.map(b => b.branch), datasets: [{label: "Turnout %", data: t.by_branch.map(b => b.percent), backgroundColor: "#c9a227"}]},
      options: {maintainAspectRatio: false, scales: {y: {beginAtZero: true, max: 100, title: {display: true, text: "% voted"}}}, plugins: {legend: {display: false}}}
    });
  }

  function showNotice(title, text) {
    const n = $("notice");
    n.hidden = false;
    n.replaceChildren(el("h2", "", title), el("p", "hint", text));
  }

  function renderPositions(d) {
    $("positions").hidden = false;
    const tabs = $("tabs");
    if (active === null || !d.positions.find(p => p.id === active)) active = d.positions[0] && d.positions[0].id;
    tabs.replaceChildren();
    d.positions.forEach(p => {
      const b = el("button", "", p.title);
      b.setAttribute("aria-pressed", p.id === active);
      b.onclick = () => { active = p.id; renderPositions(lastData); };
      tabs.appendChild(b);
    });
    const p = d.positions.find(x => x.id === active);
    const view = $("pos-view");
    view.replaceChildren();
    if (!p) return;
    if (p.withheld) {
      const n = el("div", "panel notice"); n.append(el("h2", "", "Results withheld"), el("p", "hint", "Counts for this position appear once enough votes have been cast, to protect voter privacy."));
      view.appendChild(n); return;
    }

    // leaderboard
    const lb = el("div", "panel");
    lb.appendChild(el("h2", "", p.title));
    if (p.leader) {
      const msg = "Leading: " + p.leader + (p.margin !== null ? " by " + p.margin + " vote" + (p.margin === 1 ? "" : "s") : "");
      lb.appendChild(el("div", "callout", msg));
    } else lb.appendChild(el("div", "callout", "No votes counted for this position yet."));
    p.candidates.forEach((c, i) => {
      const row = el("div", "lb-row" + (i === 0 && c.votes > 0 ? " lead" : ""));
      row.appendChild(el("div", "rank", String(i + 1)));
      if (c.photo) { const im = el("img", "avatar"); im.src = c.photo; im.alt = ""; row.appendChild(im); }
      else row.appendChild(el("div", "avatar", c.name.charAt(0)));
      const mid = el("div");
      mid.appendChild(el("div", "name", c.name));
      if (c.branch) mid.appendChild(el("div", "sub", c.branch));
      const bar = el("div", "bar"), fill = document.createElement("i");
      fill.style.width = c.percent + "%"; bar.appendChild(fill); mid.appendChild(bar);
      row.appendChild(mid);
      const v = el("div", "votes", String(c.votes)); v.appendChild(el("small", "", c.percent + "%"));
      row.appendChild(v);
      lb.appendChild(row);
    });
    if (p.abstain) lb.appendChild(el("p", "hint", p.abstain + " abstention" + (p.abstain === 1 ? "" : "s") + " not counted above."));
    view.appendChild(lb);

    // charts
    const g = el("div", "grid2");
    [["bar", "Votes per candidate"], ["pie", "Share of votes"]].forEach(([k, t]) => {
      const box = el("div", "panel"); box.appendChild(el("h3", "", t));
      const cb = el("div", "chart-box"), cv = document.createElement("canvas");
      cv.id = "pc-" + k; cv.setAttribute("role", "img"); cv.setAttribute("aria-label", t + " for " + p.title);
      cb.appendChild(cv); box.appendChild(cb); g.appendChild(box);
    });
    view.appendChild(g);
    const tl = el("div", "panel"); tl.appendChild(el("h3", "", "Votes over time (hourly)"));
    const tcb = el("div", "chart-box"), tcv = document.createElement("canvas"); tcv.id = "pc-line";
    tcv.setAttribute("role", "img"); tcv.setAttribute("aria-label", "Cumulative votes over time for " + p.title);
    tcb.appendChild(tcv); tl.appendChild(tcb); view.appendChild(tl);

    // accessible data table
    const det = document.createElement("details"); det.className = "panel";
    det.appendChild(el("summary", "", "View as table"));
    const tbl = el("table", "data"), thead = el("tr");
    ["Candidate", "Votes", "Share"].forEach(h => thead.appendChild(el("th", "", h)));
    tbl.appendChild(thead);
    p.candidates.forEach(c => { const r = el("tr"); [c.name, c.votes, c.percent + "%"].forEach(x => r.appendChild(el("td", "", String(x)))); tbl.appendChild(r); });
    det.appendChild(tbl); view.appendChild(det);

    const labels = p.candidates.map(c => c.name), vals = p.candidates.map(c => c.votes);
    const colors = p.candidates.map((_, i) => COLORS[i % COLORS.length]);
    draw("pc-bar", "pc-bar", {type: "bar", data: {labels, datasets: [{label: "Votes", data: vals, backgroundColor: colors}]},
      options: {maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}}});
    draw("pc-pie", "pc-pie", {type: "doughnut", data: {labels, datasets: [{data: vals, backgroundColor: colors}]},
      options: {maintainAspectRatio: false, plugins: {legend: {position: "bottom"}}}});
    draw("pc-line", "pc-line", {type: "line",
      data: {labels: p.timeline.map(x => new Date(x.t).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})),
             datasets: [{label: "Total votes", data: p.timeline.map(x => x.total), borderColor: "#1f5c2e", backgroundColor: "rgba(31,92,46,.15)", fill: true, tension: .25}]},
      options: {maintainAspectRatio: false, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}}});
  }

  function render(d) {
    lastData = d;
    setStatus(d);
    $("s-updated").textContent = new Date(d.updated).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    $("notice").hidden = true;
    if (d.mode === "hidden") {
      $("stats").hidden = $("turnout-panel").hidden = $("positions").hidden = true;
      const upcoming = d.election.status === "upcoming";
      showNotice(upcoming ? "Voting has not started" : "Results are not public right now",
        upcoming ? "Results and turnout will appear here once voting opens." :
        "The election committee will publish results when the polls close and the count is verified.");
      return;
    }
    renderTurnout(d.turnout);
    if (d.mode === "turnout") {
      $("positions").hidden = true;
      showNotice("Candidate results are hidden while voting is open", "Turnout is shown live. Full results appear here when polls close.");
      return;
    }
    renderPositions(d);
  }

  async function load() {
    try {
      const r = await fetch(API_URL, {headers: {"Accept": "application/json"}});
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch (e) {
      $("el-status").textContent = "Reconnecting…";
    }
  }

  load();
  setInterval(load, REFRESH_MS);
  setInterval(tick, 1000);
})();
