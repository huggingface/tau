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

    const status = document.createElement("span");
    status.className = "copy-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.setAttribute("aria-atomic", "true");

    let resetTimer;
    button.addEventListener("click", async () => {
      const codeEl = pre.querySelector("code");
      const text = codeEl ? codeEl.textContent : pre.textContent;
      try {
        const clipboard = navigator.clipboard;
        if (!clipboard || typeof clipboard.writeText !== "function") {
          throw new Error("Clipboard API unavailable");
        }
        await clipboard.writeText(text);
        button.textContent = "Copied!";
        button.classList.add("copied");
        status.textContent = "Code copied to clipboard.";
      } catch {
        button.textContent = "Failed";
        button.classList.remove("copied");
        status.textContent = "Unable to copy code to clipboard.";
      }
      if (resetTimer) window.clearTimeout(resetTimer);
      resetTimer = window.setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("copied");
        status.textContent = "";
        resetTimer = undefined;
      }, 1500);
    });

    pre.appendChild(button);
    pre.appendChild(status);
  });
});
