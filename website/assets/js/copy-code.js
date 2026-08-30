// Copy-to-clipboard button for docs code blocks (issue #343).
// Injects a "Copy" button into every <pre> inside the docs content area.
// The button is hidden until the parent <pre> is hovered or focused, and
// flashes a "Copied!" confirmation after a successful write.
document.addEventListener("DOMContentLoaded", () => {
  const blocks = document.querySelectorAll(".docs-content pre");
  blocks.forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-btn";
    button.setAttribute("aria-label", "Copy code to clipboard");
    button.textContent = "Copy";

    button.addEventListener("click", async () => {
      const codeEl = pre.querySelector("code");
      const text = codeEl ? codeEl.textContent : pre.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied!";
        button.classList.add("copied");
      } catch {
        button.textContent = "Failed";
      }
      window.setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("copied");
      }, 1500);
    });

    pre.appendChild(button);
  });
});
