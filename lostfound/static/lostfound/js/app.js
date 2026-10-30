/* Progressive enhancement only: every page works without JavaScript. */
(function () {
  "use strict";

  // App sidebar as a drawer on small screens (Esc or backdrop closes it)
  var menu = document.querySelector("[data-menu-toggle]");
  var sidebar = document.getElementById("sidebar");
  var scrim = document.querySelector("[data-scrim]");
  var setDrawer = function (open) {
    if (!sidebar || !menu) return;
    sidebar.classList.toggle("open", open);
    if (scrim) scrim.classList.toggle("open", open);
    menu.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { var first = sidebar.querySelector("a"); if (first) first.focus(); }
  };
  if (menu && sidebar) {
    menu.addEventListener("click", function () { setDrawer(!sidebar.classList.contains("open")); });
    if (scrim) scrim.addEventListener("click", function () { setDrawer(false); });
  }

  // Public header menu on small screens
  var navToggle = document.querySelector("[data-nav-toggle]");
  var siteNav = document.getElementById("site-nav");
  if (navToggle && siteNav) {
    navToggle.addEventListener("click", function () {
      var open = siteNav.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Dropdowns (<details data-dropdown>): close on outside click or Esc
  var dropdowns = document.querySelectorAll("details[data-dropdown]");
  document.addEventListener("click", function (e) {
    dropdowns.forEach(function (d) { if (d.open && !d.contains(e.target)) d.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    dropdowns.forEach(function (d) { if (d.open) { d.open = false; d.querySelector("summary").focus(); } });
    if (sidebar && sidebar.classList.contains("open")) { setDrawer(false); menu.focus(); }
    if (siteNav && siteNav.classList.contains("open")) { siteNav.classList.remove("open"); navToggle.setAttribute("aria-expanded", "false"); }
  });

  // Photo upload preview (local only; nothing is sent until the form is submitted)
  document.querySelectorAll("[data-upload]").forEach(function (box) {
    var input = box.querySelector("input[type=file]");
    var preview = box.querySelector("[data-upload-preview]");
    if (!input || !preview) return;
    var original = preview.innerHTML;
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file || !/^image\//.test(file.type)) { preview.innerHTML = original; return; }
      var img = document.createElement("img");
      img.alt = "Selected photo preview";
      img.src = URL.createObjectURL(file);
      img.onload = function () { URL.revokeObjectURL(img.src); };
      preview.innerHTML = ""; preview.appendChild(img);
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    ["dragenter", "dragover"].forEach(function (t) { box.addEventListener(t, function () { box.classList.add("dragover"); }); });
    ["dragleave", "drop"].forEach(function (t) { box.addEventListener(t, function () { box.classList.remove("dragover"); }); });
  });

  // Live review summary on the report form (public fields only)
  var review = document.querySelector("[data-review]");
  if (review) {
    var form = review.closest("form");
    var text = function (el) {
      if (!el) return "";
      if (el.tagName === "SELECT") { var o = el.options[el.selectedIndex]; return o && o.value ? o.text.replace(/^[—\s]+/, "") : ""; }
      if (el.type === "file") { return el.files && el.files[0] ? el.files[0].name : ""; }
      if (el.type === "datetime-local" && el.value) { var d = new Date(el.value); return isNaN(d) ? el.value : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
      return el.value.trim();
    };
    var refresh = function () {
      review.querySelectorAll("[data-review-field]").forEach(function (slot) {
        var value = text(form.querySelector("[name='" + slot.getAttribute("data-review-field") + "']"));
        slot.textContent = value || slot.getAttribute("data-empty") || "Not provided";
        slot.classList.toggle("empty-val", !value);
      });
      var count = form.querySelectorAll("[data-private-question]");
      var filled = 0;
      count.forEach(function (q) { if (q.value.trim()) filled += 1; });
      var slot = review.querySelector("[data-review-private]");
      if (slot) { slot.textContent = filled ? filled + " recorded (kept private)" : "None yet (at least one needed)"; slot.classList.toggle("empty-val", !filled); }
    };
    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    refresh();
  }

  // Board grid/list view toggle
  document.querySelectorAll("[data-view-toggle] input").forEach(function (radio) {
    radio.addEventListener("change", function () {
      var list = document.querySelector("#board-results .tags");
      if (list) list.classList.toggle("as-list", radio.value === "list" && radio.checked);
    });
  });

  // Confirmation for sensitive actions, in the app's own dialog (not the browser popup)
  var confirmDialog = null;
  function askConfirm(msg, onYes) {
    if (!window.HTMLDialogElement) { if (window.confirm(msg)) onYes(); return; }
    if (!confirmDialog) {
      confirmDialog = document.createElement("dialog");
      confirmDialog.className = "confirm-dialog";
      confirmDialog.setAttribute("aria-labelledby", "confirm-dialog-title");
      confirmDialog.innerHTML = '<form method="dialog"><h2 id="confirm-dialog-title">Please confirm</h2>' +
        '<p class="confirm-msg"></p><div class="btn-row">' +
        '<button class="btn btn-ghost" value="cancel">Go back</button>' +
        '<button class="btn btn-primary" value="ok">Yes, continue</button></div></form>';
      document.body.appendChild(confirmDialog);
    }
    confirmDialog.querySelector(".confirm-msg").textContent = msg;
    confirmDialog.onclose = function () { if (confirmDialog.returnValue === "ok") onYes(); };
    confirmDialog.returnValue = "";
    confirmDialog.showModal();
    confirmDialog.querySelector("button[value=ok]").focus();
  }
  function markBusy(form) {
    var btn = form.querySelector("button[type=submit]");
    if (btn && !form.hasAttribute("data-no-busy")) {
      setTimeout(function () { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }, 0);
    }
  }
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.method === "dialog") return;
    if (form.dataset.busy === "1") { e.preventDefault(); return; }  // no double submissions
    var msg = form.getAttribute("data-confirm");
    if (msg && form.dataset.confirmed !== "1") {
      e.preventDefault();
      askConfirm(msg, function () {
        form.dataset.confirmed = "1";
        if (form.requestSubmit) form.requestSubmit(e.submitter || undefined); else form.submit();
      });
      return;
    }
    form.dataset.busy = "1";
    markBusy(form);
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
        .catch(function () {
          results.innerHTML = '<div class="panel empty" role="alert"><h3>Results couldn\'t load</h3><p>Check your connection and try again.</p>' +
            '<div class="btn-row"><button type="button" class="btn btn-ghost" data-retry>Try again</button></div></div>';
          var retry = results.querySelector("[data-retry]");
          if (retry) retry.addEventListener("click", load);
        })
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
