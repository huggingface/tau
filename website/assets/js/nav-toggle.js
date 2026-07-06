document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("navToggle");
  var links = document.getElementById("navlinks");
  if (btn && links) {
    btn.addEventListener("click", function () {
      var open = links.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", String(open));
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        links.classList.remove("is-open");
        btn.setAttribute("aria-expanded", "false");
      });
    });
  }

  var searchTrigger = document.getElementById("searchTrigger");
  var searchModal = document.getElementById("searchModal");
  var searchClose = document.getElementById("searchClose");
  if (!searchTrigger || !searchModal || !searchClose) return;
  var initialized = false;
  function focusInput() {
    var input = searchModal.querySelector("input");
    if (input) {
      input.focus();
    } else {
      requestAnimationFrame(focusInput);
    }
  }
  function openSearch() {
    searchModal.hidden = false;
    document.body.classList.add("search-open");
    if (!initialized && window.PagefindUI) {
      new window.PagefindUI({ element: "#search", showSubResults: true });
      initialized = true;
    }
    requestAnimationFrame(focusInput);
  }
  function closeSearch() {
    searchModal.hidden = true;
    document.body.classList.remove("search-open");
  }
  searchTrigger.addEventListener("click", openSearch);
  searchClose.addEventListener("click", closeSearch);
  searchModal.addEventListener("click", function (e) {
    if (e.target === searchModal) closeSearch();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeSearch();
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      openSearch();
    }
  });
});
