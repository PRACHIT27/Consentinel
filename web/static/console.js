/* Two small pieces of behaviour, both optional.
 *
 * The console is server-rendered on purpose: every screen is complete before
 * this file loads, and if it never loads the pages still read fine. All this
 * adds is the slide-in detail panel and the registry search box.
 *
 * The detail panel is filled by *moving already-rendered nodes*, never by
 * building HTML from strings. That matters here more than usual: findings carry
 * text copied from pages we do not control, and Jinja has already escaped it.
 * Cloning the nodes keeps it escaped. Re-assembling it as innerHTML would hand
 * a stranger's page a way to run script inside our own app.
 */
(function () {
  "use strict";

  var overlay = document.getElementById("drawerOverlay");
  var drawer = document.getElementById("drawer");
  if (!drawer) return;

  var titleEl = document.getElementById("drawerTitle");
  var subEl = document.getElementById("drawerSub");
  var bodyEl = document.getElementById("drawerBody");

  function closeDrawer() {
    drawer.classList.remove("show");
    overlay.classList.remove("show");
  }

  function openDrawer(source) {
    var payload = source.querySelector("[data-drawer-body]");
    if (!payload) return;

    titleEl.textContent = source.getAttribute("data-drawer-title") || "";
    subEl.textContent = source.getAttribute("data-drawer-sub") || "";

    bodyEl.textContent = "";
    var copy = payload.cloneNode(true);   // nodes, not markup
    copy.removeAttribute("data-drawer-body");
    copy.hidden = false;
    while (copy.firstChild) bodyEl.appendChild(copy.firstChild);

    drawer.classList.add("show");
    overlay.classList.add("show");
  }

  document.querySelectorAll("[data-drawer-title]").forEach(function (row) {
    row.addEventListener("click", function () { openDrawer(row); });
  });

  overlay.addEventListener("click", closeDrawer);
  document.querySelectorAll("[data-drawer-close]").forEach(function (b) {
    b.addEventListener("click", closeDrawer);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });

  /* ---------- file picker echoes the chosen name ---------- */

  document.querySelectorAll("[data-filename-into]").forEach(function (input) {
    var target = document.getElementById(input.getAttribute("data-filename-into"));
    if (!target) return;
    var placeholder = target.textContent;
    input.addEventListener("change", function () {
      target.textContent = input.files && input.files.length
        ? input.files[0].name
        : placeholder;
    });
  });

  /* ---------- registry search ---------- */

  var search = document.getElementById("registrySearch");
  if (search) {
    var rows = Array.prototype.slice.call(
      document.querySelectorAll("#registryTable .reg-row:not(.head)")
    );
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      rows.forEach(function (row) {
        var hay = (row.getAttribute("data-search") || row.textContent).toLowerCase();
        row.style.display = !q || hay.indexOf(q) !== -1 ? "" : "none";
      });
    });
  }
})();
