// Copy buttons and job-status polling (no external libraries).
(function () {
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = document.getElementById(b.getAttribute("data-copy"));
      t.focus(); t.select();
      var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy all"; }, 1500); };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(t.value).then(done, function () { document.execCommand("copy"); done(); });
      } else { document.execCommand("copy"); done(); }
    });
  });

  var box = document.getElementById("jobbox") || document.getElementById("jobs");
  if (!box) return;
  var set = box.getAttribute("data-set");
  var url = "/jobs.json" + (set ? "?set=" + encodeURIComponent(set) : "");
  var first = null;
  function print(js) { return js.map(function (j) { return j.id + ":" + j.status; }).join(","); }
  function active(js) { return js.some(function (j) { return j.status === "queued" || j.status === "running"; }); }
  function poll() {
    fetch(url, {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (js) {
      var fp = print(js);
      if (first === null) first = fp;
      if (fp !== first) { location.reload(); return; }
      if (active(js)) setTimeout(poll, 2000);
    }).catch(function () { setTimeout(poll, 5000); });
  }
  poll();
})();
