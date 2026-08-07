var parser = new DOMParser();

function makeSVG(str) {
  return parser.parseFromString(str, "image/svg+xml").documentElement;
}

var COPY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
var CHECK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".docs-content pre").forEach(function (pre) {
    var btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.setAttribute("aria-label", "Copy code to clipboard");
    btn.appendChild(makeSVG(COPY_SVG));

    pre.style.position = "relative";
    pre.appendChild(btn);

    btn.addEventListener("click", function () {
      var code = pre.querySelector("code");
      var text = code ? code.innerText : pre.innerText;
      navigator.clipboard.writeText(text).then(function () {
        btn.replaceChildren(makeSVG(CHECK_SVG));
        btn.classList.add("copy-btn--copied");
        setTimeout(function () {
          btn.replaceChildren(makeSVG(COPY_SVG));
          btn.classList.remove("copy-btn--copied");
        }, 2000);
      });
    });
  });
});
