/* Progressive enhancement only: every page works without JavaScript. */
(function () {
  "use strict";

  // Mobile navigation
  var menu = document.querySelector("[data-menu-toggle]");
  if (menu) {
    menu.addEventListener("click", function () {
      var nav = document.getElementById("sidebar");
      var open = nav.classList.toggle("open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Confirmation for sensitive actions
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    var btn = form.querySelector("button[type=submit]");
    if (btn && !form.hasAttribute("data-no-busy")) {
      setTimeout(function () { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }, 0);
    }
  });

  // Category-specific attribute fields on the report form
  var attrData = document.getElementById("attribute-map");
  var catSelect = document.getElementById("id_category");
  var attrBox = document.getElementById("attribute-fields");
  if (attrData && catSelect && attrBox) {
    var map = JSON.parse(attrData.textContent);
    var previous = JSON.parse(attrBox.getAttribute("data-values") || "{}");
    var render = function () {
      var defs = map[catSelect.value] || [];
      attrBox.innerHTML = "";
      defs.forEach(function (a) {
        var wrap = document.createElement("div");
        wrap.className = "field";
        var id = "attr_" + a.key;
        var label = document.createElement("label");
        label.htmlFor = id; label.textContent = a.label + " (optional)";
        wrap.appendChild(label);
        var input;
        if (a.kind === "choice") {
          input = document.createElement("select");
          var blank = document.createElement("option"); blank.value = ""; blank.textContent = "—";
          input.appendChild(blank);
          a.choices.forEach(function (c) { var o = document.createElement("option"); o.value = c; o.textContent = c; input.appendChild(o); });
        } else {
          input = document.createElement("input"); input.type = "text"; input.maxLength = 80;
        }
        input.id = id; input.name = id; input.value = previous[a.key] || "";
        wrap.appendChild(input);
        attrBox.appendChild(wrap);
      });
      attrBox.hidden = defs.length === 0;
    };
    catSelect.addEventListener("change", render);
    render();
  }

  // Searchable location select
  document.querySelectorAll("[data-filter-select]").forEach(function (input) {
    var select = document.getElementById(input.getAttribute("data-filter-select"));
    if (!select) return;
    var options = Array.prototype.slice.call(select.options);
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var first = null;
      options.forEach(function (o) {
        var show = !o.value || o.text.toLowerCase().indexOf(q) !== -1;
        o.hidden = !show;
        if (show && o.value && !first) first = o;
      });
      if (q && first) select.value = first.value;
    });
  });

  // Leaflet campus map picker (only when the campus centre is configured)
  var mapEl = document.getElementById("location-map");
  if (mapEl && window.L) {
    var cfg = JSON.parse(document.getElementById("map-config").textContent);
    var target = document.getElementById(mapEl.getAttribute("data-target"));
    var lmap = L.map(mapEl).setView([cfg.center_lat, cfg.center_lng], cfg.zoom);
    L.tileLayer(cfg.tile_url, { maxZoom: 19, attribution: cfg.attribution }).addTo(lmap);
    fetch(mapEl.getAttribute("data-source")).then(function (r) { return r.json(); }).then(function (data) {
      data.locations.forEach(function (loc) {
        L.marker([loc.lat, loc.lng], { title: loc.name }).addTo(lmap).bindPopup(loc.name).on("click", function () {
          if (target) { target.value = String(loc.id); target.dispatchEvent(new Event("change")); }
        });
      });
    }).catch(function () { mapEl.hidden = true; });
  }

  // Board: update results without a full reload (falls back to normal GET)
  var boardForm = document.getElementById("board-filters");
  var results = document.getElementById("board-results");
  if (boardForm && results && window.fetch) {
    var timer;
    var load = function () {
      var url = boardForm.action + "?" + new URLSearchParams(new FormData(boardForm)).toString();
      results.classList.add("loading");
      results.setAttribute("aria-busy", "true");
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) { results.innerHTML = html; history.replaceState(null, "", url); })
        .catch(function () { results.innerHTML = '<div class="empty"><h3>Results could not load</h3><p>Check your connection and try again.</p></div>'; })
        .finally(function () { results.classList.remove("loading"); results.removeAttribute("aria-busy"); });
    };
    boardForm.addEventListener("input", function (e) {
      if (e.target.type === "search") { clearTimeout(timer); timer = setTimeout(load, 300); } else { load(); }
    });
    boardForm.addEventListener("submit", function (e) { e.preventDefault(); load(); });
  }

  // Signature pad for handover confirmation
  var pad = document.getElementById("signature-pad");
  if (pad) {
    var hidden = document.getElementById("id_signature_data");
    var ctx = pad.getContext("2d");
    var drawing = false, inked = false;
    var resize = function () {
      var ratio = window.devicePixelRatio || 1;
      pad.width = pad.offsetWidth * ratio; pad.height = pad.offsetHeight * ratio;
      ctx.scale(ratio, ratio); ctx.lineWidth = 2.4; ctx.lineCap = "round"; ctx.strokeStyle = "#15233f";
    };
    resize();
    var pos = function (e) { var r = pad.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    pad.addEventListener("pointerdown", function (e) { drawing = true; var p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); pad.setPointerCapture(e.pointerId); });
    pad.addEventListener("pointermove", function (e) { if (!drawing) return; var p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); inked = true; });
    var end = function () { if (drawing && inked) hidden.value = pad.toDataURL("image/png"); drawing = false; };
    pad.addEventListener("pointerup", end); pad.addEventListener("pointerleave", end);
    var clear = document.getElementById("signature-clear");
    if (clear) clear.addEventListener("click", function () { ctx.clearRect(0, 0, pad.width, pad.height); hidden.value = ""; inked = false; });
  }

  // Handover code countdown
  var countdown = document.querySelector("[data-expires]");
  if (countdown) {
    var expires = new Date(countdown.getAttribute("data-expires")).getTime();
    var tick = function () {
      var s = Math.max(0, Math.round((expires - Date.now()) / 1000));
      countdown.textContent = s > 0 ? "Expires in " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "Expired — generate a new code.";
      if (s > 0) setTimeout(tick, 1000);
    };
    tick();
  }
})();
