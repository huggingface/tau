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
  var searchBox = document.getElementById("search");
  if (!searchTrigger || !searchModal || !searchClose || !searchBox) return;
  var initialized = false;
  var unavailableNoticeShown = false;
  function focusInput() {
    var input = searchModal.querySelector("input");
    if (input) input.focus();
  }
  function openSearch() {
    searchModal.hidden = false;
    document.body.classList.add("search-open");
    if (!initialized && window.PagefindUI) {
      new window.PagefindUI({ element: "#search", showSubResults: true });
      initialized = true;
      requestAnimationFrame(focusInput);
    } else if (!initialized && !unavailableNoticeShown) {
      // pagefind-ui.js only exists after a real `hugo` build + the pagefind
      // postbuild step — `hugo server` never runs that, so this is expected
      // in local dev. Run `hugo --minify && npx pagefind@latest --site public
      // && npx serve public` to test search for real.
      searchBox.innerHTML =
        '<p class="search-unavailable">Search index not built. Run <code>hugo --minify &amp;&amp; npx pagefind@latest --site public</code>, then serve <code>public/</code>, to test search locally.</p>';
      unavailableNoticeShown = true;
    } else if (initialized) {
      requestAnimationFrame(focusInput);
    }
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

  // ---- GitHub star count ----
  var starsEl = document.getElementById("ghStars");
  var starsCountEl = document.getElementById("ghStarsCount");
  if (starsEl && starsCountEl) {
    var repo = starsEl.getAttribute("data-repo");
    var cacheKey = "tau-gh-stars:" + repo;
    var cacheTtl = 1000 * 60 * 60; // 1 hour

    function formatStars(n) {
      if (n >= 1000) return (n / 1000).toFixed(n % 1000 >= 100 ? 1 : 0) + "k";
      return String(n);
    }

    function show(n) {
      starsCountEl.textContent = formatStars(n);
      starsEl.hidden = false;
    }

    var cached = null;
    try {
      cached = JSON.parse(localStorage.getItem(cacheKey));
    } catch (e) {
      cached = null;
    }

    if (cached && typeof cached.count === "number" && Date.now() - cached.time < cacheTtl) {
      show(cached.count);
    } else if (repo) {
      fetch("https://api.github.com/repos/" + repo)
        .then(function (res) {
          if (!res.ok) throw new Error("bad response");
          return res.json();
        })
        .then(function (data) {
          var count = data && typeof data.stargazers_count === "number" ? data.stargazers_count : null;
          if (count === null) return;
          show(count);
          try {
            localStorage.setItem(cacheKey, JSON.stringify({ count: count, time: Date.now() }));
          } catch (e) {
            /* ignore storage errors */
          }
        })
        .catch(function () {
          if (cached && typeof cached.count === "number") show(cached.count);
        });
    }
  }
});
