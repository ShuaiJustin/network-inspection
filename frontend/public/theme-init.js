(function () {
  try {
    var stored = localStorage.getItem("inspection-theme");
    var dark = stored === "dark" || (stored !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  } catch (_) {
    document.documentElement.classList.add("dark");
  }
})();
