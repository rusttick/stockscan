// Manual measurement on the straight-on image.
// Each corner: a click on the main view opens a zoomed loupe; the click in the loupe sets the corner.
// Image coordinates use pixel centres (pixel c spans c-0.5 .. c+0.5), as the server does.
(function () {
  var cv = document.getElementById("cv"), ctx = cv.getContext("2d");
  var lp = document.getElementById("loupe"), lctx = lp.getContext("2d");
  var stage = document.getElementById("stage"), status = document.getElementById("mstatus");
  var flist = document.getElementById("flist");
  var img = new Image(), meta = null, faces = [], pending = null, loupe = null;
  var s = 1, ox = 0, oy = 0, dpr = window.devicePixelRatio || 1;

  function toImg(x, y) { return [(x - ox) / s - 0.5, (y - oy) / s - 0.5]; }
  function toScr(c, r) { return [(c + 0.5) * s + ox, (r + 0.5) * s + oy]; }

  function resize() {
    cv.width = Math.round(cv.clientWidth * dpr); cv.height = Math.round(cv.clientHeight * dpr); draw();
  }
  function fit() {
    if (!meta) return;
    var W = cv.clientWidth, H = cv.clientHeight;
    s = Math.min(W / meta.width, H / meta.height);
    ox = (W - meta.width * s) / 2; oy = (H - meta.height * s) / 2; draw();
  }
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#111"; ctx.fillRect(0, 0, cv.clientWidth, cv.clientHeight);
    if (!meta) return;
    ctx.imageSmoothingEnabled = s < 2;
    ctx.drawImage(img, ox, oy, meta.width * s, meta.height * s);
    ctx.lineWidth = 2; ctx.font = "13px system-ui, sans-serif";
    faces.forEach(function (f, i) {
      var a = toScr(f[0], f[1]), b = toScr(f[2], f[3]);
      ctx.strokeStyle = "#00e060"; ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.fillStyle = "#ffd000";
      ctx.fillText((i + 1) + ": " + dims(f), a[0], a[1] - 4);
    });
    if (pending) {
      var p = toScr(pending[0], pending[1]);
      ctx.strokeStyle = "#ff4040"; ctx.beginPath();
      ctx.moveTo(p[0] - 10, p[1]); ctx.lineTo(p[0] + 10, p[1]); ctx.moveTo(p[0], p[1] - 10); ctx.lineTo(p[0], p[1] + 10);
      ctx.stroke();
    }
  }
  function dims(f) {
    return (Math.abs(f[2] - f[0]) / meta.ppi).toFixed(3) + " x " + (Math.abs(f[3] - f[1]) / meta.ppi).toFixed(3) + " in";
  }
  function table() {
    var h = "<tr><th>#</th><th>width in</th><th>height in</th><th></th></tr>";
    faces.forEach(function (f, i) {
      h += "<tr><td>" + (i + 1) + "</td><td>" + (Math.abs(f[2] - f[0]) / meta.ppi).toFixed(3) + "</td><td>" +
        (Math.abs(f[3] - f[1]) / meta.ppi).toFixed(3) + "</td><td><button class='small' data-del='" + i +
        "'>delete</button></td></tr>";
    });
    flist.innerHTML = faces.length ? h : "";
  }
  flist.addEventListener("click", function (e) {
    var i = e.target.getAttribute && e.target.getAttribute("data-del");
    if (i !== null && i !== undefined) { faces.splice(parseInt(i, 10), 1); save(); }
  });

  function save() {
    faces.sort(function (a, b) { return Math.min(a[0], a[2]) - Math.min(b[0], b[2]); });
    table(); draw();
    status.textContent = "saving...";
    fetch(window.MEASURE_URL, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({faces: faces})})
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.error) { status.textContent = d.error; return; } faces = d.faces; table(); draw(); info("saved"); })
      .catch(function () { status.textContent = "NOT saved (network error) - try again"; });
  }
  function info(extra) {
    status.textContent = "front row " + meta.front + ", " + (meta.calibrated ? "calibrated" : "NOT calibrated") + ", " +
      faces.length + " faces" + (pending ? ", now click the BOTTOM-RIGHT corner" : ", next: TOP-LEFT corner") +
      (extra ? " (" + extra + ")" : "");
  }

  // ---- loupe
  var LOUPE = 320;
  function openLoupe(c, r, x, y) {
    var L = Math.max(s * 6, 4);
    loupe = {c: c, r: r, L: L};
    var W = stage.clientWidth, H = stage.clientHeight;
    var lx = x + 20, ly = y - LOUPE / 2;
    if (lx + LOUPE > W) lx = x - LOUPE - 20;
    lx = Math.max(0, Math.min(W - LOUPE, lx)); ly = Math.max(0, Math.min(H - LOUPE, ly));
    lp.style.left = lx + "px"; lp.style.top = ly + "px"; lp.style.width = LOUPE + "px"; lp.style.height = LOUPE + "px";
    lp.width = LOUPE * dpr; lp.height = LOUPE * dpr; lp.hidden = false;
    drawLoupe();
  }
  function drawLoupe() {
    var L = loupe.L, half = LOUPE / 2 / L;
    lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    lctx.fillStyle = "#000"; lctx.fillRect(0, 0, LOUPE, LOUPE);
    lctx.imageSmoothingEnabled = false;
    // image pixel c spans [c, c+1) in drawImage source space (centre at c + 0.5)
    lctx.drawImage(img, loupe.c + 0.5 - half, loupe.r + 0.5 - half, 2 * half, 2 * half, 0, 0, LOUPE, LOUPE);
    lctx.strokeStyle = "rgba(255,60,60,.8)"; lctx.lineWidth = 1; lctx.beginPath();
    lctx.moveTo(LOUPE / 2, 0); lctx.lineTo(LOUPE / 2, LOUPE); lctx.moveTo(0, LOUPE / 2); lctx.lineTo(LOUPE, LOUPE / 2);
    lctx.stroke();
  }
  function closeLoupe() { loupe = null; lp.hidden = true; }
  lp.addEventListener("click", function (e) {
    var b = lp.getBoundingClientRect(), L = loupe.L;
    var c = loupe.c + (e.clientX - b.left - LOUPE / 2) / L, r = loupe.r + (e.clientY - b.top - LOUPE / 2) / L;
    closeLoupe();
    corner(c, r);
  });
  lp.addEventListener("wheel", function (e) {
    e.preventDefault(); loupe.L = Math.max(1, Math.min(64, loupe.L * Math.pow(1.2, -e.deltaY / 100))); drawLoupe();
  }, {passive: false});

  function corner(c, r) {
    if (!pending) { pending = [c, r]; info(); draw(); return; }
    var f = [Math.min(pending[0], c), Math.min(pending[1], r), Math.max(pending[0], c), Math.max(pending[1], r)];
    pending = null;
    if (f[2] - f[0] < 2 || f[3] - f[1] < 2) { info("too small, ignored"); draw(); return; }
    faces.push(f); save();
  }

  // ---- pan / zoom / click on the main view
  var ptrs = {}, start = null;
  cv.addEventListener("pointerdown", function (e) {
    cv.setPointerCapture(e.pointerId);
    ptrs[e.pointerId] = [e.offsetX, e.offsetY];
    var ids = Object.keys(ptrs);
    if (ids.length === 1) start = {x: e.offsetX, y: e.offsetY, ox: ox, oy: oy, moved: false};
    if (ids.length === 2) {
      var a = ptrs[ids[0]], b = ptrs[ids[1]];
      start = {pinch: true, d: Math.hypot(a[0] - b[0], a[1] - b[1]), mx: (a[0] + b[0]) / 2, my: (a[1] + b[1]) / 2,
        s: s, ox: ox, oy: oy, moved: true};
    }
  });
  cv.addEventListener("pointermove", function (e) {
    if (!(e.pointerId in ptrs) || !start) return;
    ptrs[e.pointerId] = [e.offsetX, e.offsetY];
    var ids = Object.keys(ptrs);
    if (start.pinch && ids.length === 2) {
      var a = ptrs[ids[0]], b = ptrs[ids[1]];
      var k = Math.hypot(a[0] - b[0], a[1] - b[1]) / start.d;
      s = start.s * k;
      ox = start.mx - (start.mx - start.ox) * k; oy = start.my - (start.my - start.oy) * k;
      draw();
    } else if (!start.pinch) {
      var dx = e.offsetX - start.x, dy = e.offsetY - start.y;
      if (Math.abs(dx) + Math.abs(dy) > 6) start.moved = true;
      if (start.moved) { ox = start.ox + dx; oy = start.oy + dy; draw(); }
    }
  });
  function up(e) {
    if (!(e.pointerId in ptrs)) return;
    var wasClick = start && !start.moved && Object.keys(ptrs).length === 1;
    delete ptrs[e.pointerId];
    if (wasClick && meta) {
      var p = toImg(e.offsetX, e.offsetY);
      openLoupe(p[0], p[1], e.offsetX, e.offsetY);
    }
    if (!Object.keys(ptrs).length) start = null;
  }
  cv.addEventListener("pointerup", up);
  cv.addEventListener("pointercancel", function (e) { delete ptrs[e.pointerId]; start = null; });
  cv.addEventListener("wheel", function (e) {
    e.preventDefault();
    var k = Math.pow(1.15, -e.deltaY / 100);
    ox = e.offsetX - (e.offsetX - ox) * k; oy = e.offsetY - (e.offsetY - oy) * k; s *= k; draw();
  }, {passive: false});

  document.getElementById("undo").addEventListener("click", function () { if (faces.length) { faces.pop(); save(); } });
  document.getElementById("cancel").addEventListener("click", function () { pending = null; closeLoupe(); info(); draw(); });
  document.getElementById("fit").addEventListener("click", fit);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeLoupe(); } });
  window.addEventListener("resize", resize);

  fetch(window.MEASURE_URL, {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (d) {
    if (d.error) { status.textContent = "Cannot measure this photo: " + d.error; status.className = "bad"; return; }
    meta = d.meta; meta.front = d.front; meta.calibrated = d.calibrated; faces = d.faces || [];
    img.onload = function () { resize(); fit(); table(); info(); };
    img.src = d.image + "?t=" + Date.now();
  }).catch(function () { status.textContent = "failed to load"; });
})();
