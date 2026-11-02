/* Phone menu: the nav links open and close from the hamburger button. */
(function () {
  var btn = document.getElementById("menu-btn"), nav = document.getElementById("main-nav");
  if (!btn || !nav) return;
  function set(open) {
    nav.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  }
  btn.addEventListener("click", function () { set(!nav.classList.contains("open")); });
  nav.addEventListener("click", function (e) { if (e.target.closest("a")) set(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { set(false); btn.focus(); } });
  document.addEventListener("click", function (e) { if (!e.target.closest(".site-head")) set(false); });
  window.addEventListener("resize", function () { if (window.innerWidth > 600) set(false); });
})();
