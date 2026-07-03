// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import path from "node:path";

const BASE = "/";
const DOCS_ROOT = path.resolve("./src/content/docs");

/**
 * Rewrite relative Markdown links (e.g. `foo.md`, `./foo.md`, `../bar/baz.md`)
 * to their final Starlight route under `base`. Astro does not do this on its own.
 */
function remarkRelativeMdLinks() {
  return (/** @type {any} */ tree, /** @type {any} */ file) => {
    const filePath = (file.history && file.history[0]) || file.path;
    if (!filePath) return;
    const fileDir = path.dirname(filePath);
    const visit = (/** @type {any} */ node) => {
      if (node.type === "link" && typeof node.url === "string") {
        const url = node.url;
        // Root-absolute internal links (e.g. "/why-tau/") need `base` prepended;
        // Astro does not do this for Markdown links.
        if (
          url.startsWith("/") &&
          !url.startsWith("//") &&
          !url.startsWith(BASE)
        ) {
          node.url = BASE + url.replace(/^\//, "");
        } else if (
          url &&
          !/^https?:\/\//.test(url) &&
          !url.startsWith("/") &&
          !url.startsWith("#")
        ) {
          const hash = url.indexOf("#");
          const p = hash >= 0 ? url.slice(0, hash) : url;
          const anchor = hash >= 0 ? url.slice(hash) : "";
          if (p.endsWith(".md")) {
            const abs = path.resolve(fileDir, p);
            let slug = path
              .relative(DOCS_ROOT, abs)
              .replace(/\\/g, "/")
              .replace(/\.md$/, "")
              .toLowerCase();
            if (slug.endsWith("/index")) slug = slug.slice(0, -6);
            if (slug === "index") slug = "";
            node.url = BASE + (slug ? slug + "/" : "") + anchor;
          }
        }
      }
      if (node.children) for (const c of node.children) visit(c);
    };
    visit(tree);
  };
}

// https://astro.build/config
export default defineConfig({
  site: "https://twotimespi.dev",
  base: BASE,
  markdown: {
    remarkPlugins: [remarkRelativeMdLinks],
  },
  integrations: [
    starlight({
      title: "Tau",
      description:
        "An educational Python project for learning how coding agents are built.",
      logo: { src: "./src/assets/tau-glyph.svg", replacesTitle: false },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/alejandro-ao/tau",
        },
      ],
      editLink: {
        baseUrl: "https://github.com/alejandro-ao/tau/edit/main/website/",
      },
      customCss: ["./src/styles/custom.css"],
      // Landing pages live as standalone routes in src/pages/.
      pagefind: true,
      sidebar: [
        { label: "Home", link: "/" },
        { label: 'Why "Tau"?', link: "/why-tau/" },
        {
          label: "Use Tau",
          items: [
            { label: "What is Tau?", slug: "what-is-tau" },
            { label: "Quickstart", slug: "quickstart" },
            { label: "Core concepts", slug: "concepts" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "The interactive session", slug: "guides/tui" },
            { label: "Sessions", slug: "guides/sessions" },
            { label: "Providers & models", slug: "guides/providers-and-models" },
            { label: "Skills & prompt templates", slug: "guides/skills-and-prompts" },
            { label: "Project instructions", slug: "guides/project-instructions" },
            { label: "Managing context", slug: "guides/context" },
            { label: "Print mode & scripting", slug: "guides/print-mode" },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "CLI", slug: "reference/cli" },
            { label: "Slash commands", slug: "reference/slash-commands" },
            { label: "Keyboard shortcuts", slug: "reference/keybindings" },
            { label: "Configuration & files", slug: "reference/configuration" },
            { label: "Built-in tools", slug: "reference/tools" },
          ],
        },
        {
          label: "How Tau works",
          items: [
            { label: "Architecture overview", slug: "internals/architecture" },
            { label: "The agent loop & events", slug: "internals/agent-loop" },
            { label: "Design principles", slug: "internals/design-principles" },
            { label: "Build your own frontend", slug: "internals/custom-frontend" },
          ],
        },
        { label: "Release notes", link: "/releases/" },
        { label: "Contributing", slug: "contributing" },
      ],
    }),
  ],
});
