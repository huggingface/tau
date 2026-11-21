// Theme switcher. The inline script in Base.astro sets the initial theme
// before paint; this module keeps the toggle, storage, and other tabs in sync.
const STORAGE_KEY = "tau-theme";
const LIGHT = "light";
const DARK = "dark";

const root = document.documentElement;
const media =
  typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

function storedTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === LIGHT || value === DARK ? value : null;
  } catch {
    return null;
  }
}

function currentTheme() {
  return root.dataset.theme === DARK ? DARK : LIGHT;
}

function applyTheme(theme, { persist = false } = {}) {
  root.dataset.theme = theme;
  root.style.colorScheme = theme;

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === DARK ? "#151412" : "#FDFCF8");

  const button = document.getElementById("themeToggle");
  if (button) {
    const next = theme === DARK ? "light" : "dark";
    const label = `Switch to ${next} mode`;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.setAttribute("aria-pressed", String(theme === DARK));
  }

  document.dispatchEvent(
    new CustomEvent("tau:themechange", { detail: { theme } }),
  );

  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* Storage can be unavailable in private or restricted contexts. */
    }
  }
}

function init() {
  const button = document.getElementById("themeToggle");
  if (button) {
    button.addEventListener("click", () => {
      applyTheme(currentTheme() === DARK ? LIGHT : DARK, { persist: true });
    });
  }

  // Reflect the theme chosen by the inline script, including in other tabs.
  applyTheme(currentTheme());

  if (media) {
    const onSystemChange = (event) => {
      if (!storedTheme()) applyTheme(event.matches ? DARK : LIGHT);
    };
    media.addEventListener("change", onSystemChange);
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const theme = storedTheme();
    if (theme) applyTheme(theme);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
