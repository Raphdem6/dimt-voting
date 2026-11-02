(function () {
  var s = document.getElementById("switch");
  if (s) s.addEventListener("change", function () { location.href = s.dataset.base + s.value + "/"; });
})();
