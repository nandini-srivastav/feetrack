// Progressive enhancement only; every page works without JavaScript.
document.addEventListener("submit", function (e) {
  var form = e.target;
  var msg = form.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
  // Prevent double submits (e.g. tapping Pay twice on a slow connection).
  var btn = e.submitter || form.querySelector("[type=submit]");
  if (btn && !form.hasAttribute("data-no-busy")) {
    if (form.dataset.busy === "1") { e.preventDefault(); return; }
    form.dataset.busy = "1";
    btn.classList.add("is-busy");
    btn.setAttribute("aria-busy", "true");
  }
});
// Restore buttons when navigating back to a cached page.
window.addEventListener("pageshow", function () {
  document.querySelectorAll("form[data-busy]").forEach(function (f) { delete f.dataset.busy; });
  document.querySelectorAll(".is-busy").forEach(function (b) { b.classList.remove("is-busy"); b.removeAttribute("aria-busy"); });
});
