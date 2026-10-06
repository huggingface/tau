# Moving the website from Hugo to Astro

## What and why

The website is now a static Astro site. This replaces Hugo templates with typed
Astro components without changing Tau's Python packages or its frontend adapter
boundaries. Like Pi's separation of brain, environment, and frontend, the website
remains an independent presentation layer: neither Astro nor Node is a runtime
dependency of the agent.

Markdown stays in `website/content/`. A content collection validates metadata;
the catch-all route maps `_index.md` to its section URL and all other pages to
their existing trailing-slash URLs. Hugo `relref` links became ordinary Markdown
links, and callouts became portable `[!NOTE]`, `[!TIP]`, and `[!CAUTION]`
blockquotes with an optional title. A small remark plugin retains their styling.

Shared layouts retain the CSS, mobile navigation, search modal, clipboard
buttons, metadata, analytics, and MathJax. Home and Why Tau retain the canvas
animations. Roadmap/sidebar YAML, `pyproject.toml`'s version, and Tau's existing
release JSON remain the source of truth; no copied release catalog is maintained.

## Hosting and checks

GitHub Pages and `twotimespi.dev` are unchanged. Astro copies `website/static/`
(including CNAME and the installers) into `dist/`. Pagefind indexes that output.
RSS remains at `/index.xml`; Astro generates a sitemap index referenced by
`robots.txt`. CI builds and tests the site, and the deployment workflow uploads
`dist/` rather than Hugo's `public/`.

Use Node 22.12+ and run from `website/`:

```bash
npm ci
npm run check
npm run format:check
npm run build
npm test
npm run preview
```

The preview server runs at http://localhost:4321/. Check the home animation,
Why Tau equations, roadmap, releases, mobile menu, search (also Ctrl/Cmd+K),
sidebar navigation, code-copy controls, and a nonexistent URL. `npm run dev`
is convenient for editing, but search needs the production build's index.

Built-site tests check every Markdown route, local asset/link targets, canonical
URLs, domain/installer/search artifacts, resolved templates, and key docs features.
Existing clipboard accessibility tests also run. Python CI checks remain unchanged.
Historical build journals referring to Hugo describe the commands used at their
time; the current contributor guide documents the Astro equivalents.
