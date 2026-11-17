/* Capsid Hub. Progressive enhancement only: every page is complete without this script. */
(function () {
  "use strict";
  document.documentElement.classList.add("js");

  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function highlight(text, q) {
    var t = esc(text);
    if (!q) return t;
    var i = String(text).toLowerCase().indexOf(q);
    if (i < 0) return t;
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
  }
  function bytes(n) {
    if (n === null || n === undefined) return "—";
    var u = ["B", "kB", "MB", "GB"], i = 0;
    while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
    return i === 0 ? n + " B" : n.toFixed(1) + " " + u[i];
  }

  /* ---------- copy ---------- */
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    var code = btn.parentElement.querySelector("code");
    if (!code || !navigator.clipboard) return;
    navigator.clipboard.writeText(code.textContent).then(function () {
      btn.textContent = "copied";
      btn.setAttribute("data-done", "");
      setTimeout(function () { btn.textContent = "copy"; btn.removeAttribute("data-done"); }, 1600);
    });
  });

  /* ---------- tabs (the chosen environment is remembered across pages) ---------- */
  function initTabs(root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    function select(tab, focus, remember) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
      if (remember) store.set("capsid.env", tab.textContent.trim());
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t, false, true); });
      t.addEventListener("keydown", function (e) {
        var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
        if (j === null) return;
        e.preventDefault();
        select(tabs[(j + tabs.length) % tabs.length], true, true);
      });
    });
    var saved = store.get("capsid.env");
    var initial = tabs.filter(function (t) { return t.textContent.trim() === saved; })[0] || tabs[0];
    select(initial, false, false);
  }
  document.querySelectorAll("[data-tabs]").forEach(initTabs);

  /* ---------- pager helper ---------- */
  function renderPager(nav, page, pages, go) {
    nav.innerHTML = "";
    if (pages <= 1) { nav.hidden = true; return; }
    nav.hidden = false;
    function button(label, target, disabled, rel) {
      var b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.disabled = disabled;
      if (rel) b.setAttribute("rel", rel);
      b.addEventListener("click", function () { go(target); });
      return b;
    }
    nav.appendChild(button("← Previous", page - 1, page === 1, "prev"));
    var ol = document.createElement("ol");
    var shown = [];
    for (var n = 1; n <= pages; n++) {
      if (n === 1 || n === pages || Math.abs(n - page) <= 1) shown.push(n);
    }
    var last = 0;
    shown.forEach(function (n) {
      if (n - last > 1) { var gap = document.createElement("li"); gap.innerHTML = '<span class="gap">…</span>'; ol.appendChild(gap); }
      var li = document.createElement("li");
      var b = button(String(n), n, false);
      if (n === page) { b.setAttribute("aria-current", "page"); }
      li.appendChild(b); ol.appendChild(li); last = n;
    });
    nav.appendChild(ol);
    nav.appendChild(button("Next →", page + 1, page === pages, "next"));
  }

  /* ---------- catalogue search and filters ---------- */
  var cat = document.querySelector("[data-catalogue]");
  var finder = document.querySelector("[data-finder]");
  if (cat && finder) {
    var index = JSON.parse(document.getElementById("catalogue-index").textContent);
    var root = cat.getAttribute("data-root");
    var size = parseInt(cat.getAttribute("data-page-size"), 10);
    var staticBody = cat.querySelector("[data-static]");
    var staticPager = document.querySelector("[data-static-pager]");
    var status = finder.querySelector("[data-status]");
    var staticStatus = status.textContent;
    var dyn = document.createElement("tbody");
    var dynPager = document.createElement("nav");
    dynPager.className = "pager"; dynPager.setAttribute("aria-label", "Pagination"); dynPager.hidden = true;
    cat.appendChild(dyn);
    cat.parentNode.insertBefore(dynPager, staticPager.nextSibling);
    var page = 1;

    var f = { q: finder.elements.q, category: finder.elements.category, build: finder.elements.build, arch: finder.elements.arch };
    var params = new URLSearchParams(window.location.search);
    Object.keys(f).forEach(function (k) { if (params.get(k)) f[k].value = params.get(k); });

    function matches(t, q, c, b, a) {
      if (c && t.categories.indexOf(c) < 0) return false;
      if (b && t.build !== b) return false;
      if (a === "arm64" && t.platforms.indexOf("arm64") < 0) return false;
      if (a === "amd64only" && t.platforms.indexOf("arm64") >= 0) return false;
      if (!q) return true;
      return (t.name + " " + t.description + " " + t.categories.join(" ") + " " + t.versions.join(" ")).toLowerCase().indexOf(q) >= 0;
    }
    function row(t, q) {
      var versions = t.versions.map(function (v) {
        return '<a href="' + root + "tools/" + esc(t.name) + "/" + esc(v) + '/index.html">' + esc(v) + "</a>";
      }).join(", ");
      return "<tr><td class=\"name\"><a href=\"" + root + esc(t.url) + "\">" + highlight(t.name, q) + "</a><span class=\"desc\">" +
        highlight(t.description, q) + "</span></td><td class=\"mono\">" + versions + "</td><td class=\"mono\">" + esc(t.build) +
        "</td><td class=\"mono\">" + esc(t.platforms.join(" ")) + "</td><td class=\"num\">" + t.closure +
        "</td><td class=\"mono small\">" + esc(t.license) + "</td></tr>";
    }
    function update(resetPage) {
      var q = f.q.value.trim().toLowerCase(), c = f.category.value, b = f.build.value, a = f.arch.value;
      var active = q || c || b || a;
      var qs = new URLSearchParams();
      Object.keys(f).forEach(function (k) { if (f[k].value.trim()) qs.set(k, f[k].value.trim()); });
      history.replaceState(null, "", window.location.pathname + (qs.toString() ? "?" + qs : ""));
      staticBody.hidden = !!active;
      staticPager.hidden = !!active;
      if (!active) { dyn.innerHTML = ""; dynPager.hidden = true; status.textContent = staticStatus; return; }
      if (resetPage) page = 1;
      var hits = index.filter(function (t) { return matches(t, q, c, b, a); });
      var pages = Math.max(1, Math.ceil(hits.length / size));
      page = Math.min(page, pages);
      var slice = hits.slice((page - 1) * size, page * size);
      dyn.innerHTML = slice.length ? slice.map(function (t) { return row(t, q); }).join("")
        : '<tr><td colspan="6" class="muted">No tool matches. Missing one? <a href="https://github.com/LaBiOmicS/capsid-hub/issues/new/choose">Propose a recipe</a>.</td></tr>';
      status.textContent = hits.length
        ? "Showing " + ((page - 1) * size + 1) + "–" + ((page - 1) * size + slice.length) + " of " + hits.length + " matching tools (" + index.length + " in total); page " + page + " of " + pages + "."
        : "0 of " + index.length + " tools match.";
      renderPager(dynPager, page, pages, function (n) { page = n; update(false); cat.scrollIntoView({ block: "start" }); });
    }
    ["input", "change"].forEach(function (ev) { finder.addEventListener(ev, function () { update(true); }); });
    update(true);
  }

  /* ---------- locked closure: platform, filter, pagination ---------- */
  document.querySelectorAll("[data-closure]").forEach(function (box) {
    var rows = JSON.parse(box.querySelector("[data-closure-json]").textContent);
    var body = box.querySelector("[data-rows]");
    var nav = box.querySelector("[data-closure-pager]");
    var filter = box.querySelector("[data-filter]");
    var size = parseInt(box.getAttribute("data-page-size"), 10);
    var buttons = box.querySelectorAll("[data-platform]");
    var platforms = {};
    rows.forEach(function (r) { platforms[r[4]] = true; });
    var platform = buttons.length ? buttons[0].getAttribute("data-platform") : Object.keys(platforms)[0];
    var page = 1;

    function draw() {
      var q = filter.value.trim().toLowerCase();
      var list = rows.filter(function (r) {
        if (r[4] !== platform && r[4] !== "any") return false;
        return !q || (r[0] + " " + r[1] + " " + r[5] + " " + r[3]).toLowerCase().indexOf(q) >= 0;
      });
      var pages = Math.max(1, Math.ceil(list.length / size));
      page = Math.min(page, pages);
      body.innerHTML = list.slice((page - 1) * size, page * size).map(function (r) {
        return '<tr><td class="mono"><a href="' + esc(r[8]) + '">' + highlight(r[0], q) + '</a></td><td class="mono">' + esc(r[1]) +
          '</td><td class="mono small">' + esc(r[2]) + '</td><td class="small">' + esc(r[3]) + '</td><td class="mono small">' +
          highlight(r[5], q) + '</td><td class="num">' + bytes(r[6]) + '</td><td class="mono small hash" title="' + esc(r[7]) + '">' +
          esc(r[7].slice(0, 16)) + "…</td></tr>";
      }).join("") || '<tr><td colspan="7" class="muted">No package matches.</td></tr>';
      renderPager(nav, page, pages, function (n) { page = n; draw(); });
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        platform = b.getAttribute("data-platform");
        buttons.forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        page = 1; draw();
      });
    });
    filter.addEventListener("input", function () { page = 1; draw(); });
    draw();
  });

  /* ---------- tool page: version switch in the "Use" box ---------- */
  document.querySelectorAll("[data-use]").forEach(function (box) {
    var buttons = box.querySelectorAll("[data-version]");
    var link = box.querySelector("[data-version-link]");
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var v = b.getAttribute("data-version");
        buttons.forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        box.querySelectorAll("[data-refbox]").forEach(function (t) { t.hidden = t.getAttribute("data-refbox") !== v; });
        if (link) link.href = link.href.replace(/\/[^\/]+\/index\.html$/, "/" + encodeURIComponent(v) + "/index.html");
      });
    });
  });

  /* ---------- Fig. 1: rotating icosahedral capsid ---------- */
  var svg = document.querySelector("[data-capsid]");
  if (svg) {
    var phi = (1 + Math.sqrt(5)) / 2, V = [];
    [-1, 1].forEach(function (a) { [-phi, phi].forEach(function (b) { V.push([0, a, b], [a, b, 0], [b, 0, a]); }); });
    var E = [];
    for (var i = 0; i < V.length; i++) for (var j = i + 1; j < V.length; j++) {
      var d = Math.hypot(V[i][0] - V[j][0], V[i][1] - V[j][1], V[i][2] - V[j][2]);
      if (Math.abs(d - 2) < 1e-6) E.push([i, j]);
    }
    var lines = svg.querySelectorAll("line");
    var norm = Math.hypot(1, phi), S = 88, C = 120;
    var ax = 0.42, ay = 0.63, vx = 0, vy = 0.0035, dragging = false, lastX = 0, lastY = 0;
    var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) vy = 0;
    function rot(p) {
      var x = p[0], y = p[1], z = p[2], t;
      t = x * Math.cos(ay) + z * Math.sin(ay); z = -x * Math.sin(ay) + z * Math.cos(ay); x = t;
      t = y * Math.cos(ax) - z * Math.sin(ax); z = y * Math.sin(ax) + z * Math.cos(ax); y = t;
      return [x, y, z];
    }
    function frame() {
      var R = V.map(rot);
      E.forEach(function (e, k) {
        var l = lines[k]; if (!l) return;
        var a = R[e[0]], b = R[e[1]];
        l.setAttribute("x1", (C + a[0] / norm * S).toFixed(2)); l.setAttribute("y1", (C + a[1] / norm * S).toFixed(2));
        l.setAttribute("x2", (C + b[0] / norm * S).toFixed(2)); l.setAttribute("y2", (C + b[1] / norm * S).toFixed(2));
        l.setAttribute("class", (a[2] + b[2]) < 0 ? "back" : "front");
      });
    }
    function tick() {
      if (!dragging) { ay += vy; ax += vx; vx *= 0.96; if (!still) vy += (0.0035 - vy) * 0.02; else vy *= 0.94; }
      frame();
      if (dragging || Math.abs(vx) > 1e-5 || Math.abs(vy) > 1e-5) requestAnimationFrame(tick); else running = false;
    }
    var running = false;
    function start() { if (!running) { running = true; requestAnimationFrame(tick); } }
    svg.addEventListener("pointerdown", function (e) { dragging = true; lastX = e.clientX; lastY = e.clientY; svg.setPointerCapture(e.pointerId); start(); });
    svg.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - lastX, dy = e.clientY - lastY; lastX = e.clientX; lastY = e.clientY;
      vy = dx * 0.01; vx = -dy * 0.01; ay += vy; ax += vx;
    });
    ["pointerup", "pointercancel"].forEach(function (ev) { svg.addEventListener(ev, function () { dragging = false; start(); }); });
    frame();
    if (!still) start();
  }
})();
