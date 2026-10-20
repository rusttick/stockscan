// One file per request, a few in parallel, with progress and retry.
(function () {
  var drop = document.getElementById("drop"), picker = document.getElementById("picker");
  var prog = document.getElementById("progress");
  if (!drop) return;
  var PARALLEL = 3, RETRIES = 3;
  var queue = [], running = 0, done = 0, total = 0, counts = {added: 0, duplicate: 0, failed: 0};

  function line(name) {
    var d = document.createElement("div"); d.textContent = name + ": waiting"; prog.appendChild(d); return d;
  }
  function add(files) {
    for (var i = 0; i < files.length; i++) { queue.push({file: files[i], tries: 0, el: line(files[i].name)}); total++; }
    pump();
  }
  function pump() {
    while (running < PARALLEL && queue.length) send(queue.shift());
    if (!running && !queue.length && total) {
      var s = document.createElement("div");
      s.innerHTML = "<b>Done: " + counts.added + " added, " + counts.duplicate + " duplicates, " + counts.failed +
        " failed.</b> <a href=''>Reload</a>";
      prog.appendChild(s);
      if (!counts.failed) setTimeout(function () { location.reload(); }, 800);
    }
  }
  function send(item) {
    running++; item.tries++;
    var fd = new FormData(); fd.append("file", item.file, item.file.name);
    var x = new XMLHttpRequest();
    x.open("POST", window.UPLOAD_URL);
    x.upload.onprogress = function (e) {
      if (e.lengthComputable) item.el.textContent = item.file.name + ": " + Math.round(100 * e.loaded / e.total) + "%";
    };
    x.onload = function () {
      running--;
      var r = {}; try { r = JSON.parse(x.responseText); } catch (e) {}
      if (x.status === 200) {
        counts[r.status === "duplicate" ? "duplicate" : "added"]++;
        item.el.textContent = item.file.name + ": " + r.status + (r.name && r.name !== item.file.name ? " as " + r.name : "");
      } else if (x.status === 413 || x.status === 415 || x.status === 400) {
        counts.failed++; item.el.textContent = item.file.name + ": REJECTED " + (r.error || x.status);
        item.el.className = "bad";
      } else { retry(item, "HTTP " + x.status); return; }
      pump();
    };
    x.onerror = function () { running--; retry(item, "network error"); };
    x.send(fd);
  }
  function retry(item, why) {
    if (item.tries < RETRIES) {
      item.el.textContent = item.file.name + ": " + why + ", retrying";
      setTimeout(function () { queue.push(item); pump(); }, 1000 * item.tries);
    } else {
      counts.failed++; item.el.textContent = item.file.name + ": FAILED (" + why + ")"; item.el.className = "bad"; pump();
    }
  }
  picker.addEventListener("change", function () { add(picker.files); picker.value = ""; });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files) add(e.dataTransfer.files); });

  var addrow = document.getElementById("addrow");
  if (addrow) addrow.addEventListener("click", function () {
    var t = document.getElementById("truth"), rows = t.rows;
    var last = rows[rows.length - 1], next = parseInt(last.querySelector("input[name=index]").value || "0", 10);
    for (var k = 1; k <= 5; k++) {
      var r = last.cloneNode(true);
      r.querySelectorAll("input").forEach(function (i) { i.value = ""; });
      r.querySelector("input[name=index]").value = next + k;
      t.tBodies[0].appendChild(r);
    }
  });
})();
