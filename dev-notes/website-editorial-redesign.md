# Website: the computational atlas

The initial field-guide treatment was rejected as too close to the existing website. This revision replaces it with an independent visual direction: oversized sans-serif typography, charcoal exhibits, mint accents matching the agent's dark theme, mathematical geometry, and a navigable index rather than a succession of marketing feature grids.

## What changed

- Accent color matches `src/tau_coding/tui/themes/tau-dark.json`: `#a7f3f0`. The `--tau-accent` token drives highlights, hero text, buttons, and wireframes; `#226663` is the readable teal variant for text on light surfaces. Canvas takes its accent from computed CSS, keeping it in sync with the SVG fallback. Browser checks compare the website colors with the source theme.

- The homepage is rebuilt around an introduction, a mathematical sculpture, three architectural layers, a user-controlled loop schematic, a directory, and a full-width closing invitation.
- The hero and homepage navigation form one full black viewport (`100vh`, using `100svh` on supporting mobile browsers). Navigation overlays the reserved top area; flexible text/artwork rows keep the install command inside the viewport, including compact portrait and landscape layouts.
- `website/assets/js/manifold.js` interpolates a precomputed parametric mesh between a torus, sphere, and Möbius strip. Shapes change automatically every four seconds with a two-second eased transition; no clicks are needed. Rotation is time-based. Canvas draws depth-batched wireframes without a 3D library. Hugo bundles this with the loop exhibit script.
- There is no shape picker: surfaces cycle automatically. The remaining keyboard-accessible pause/play control freezes or resumes the current frame. Reduced-motion preferences start the exhibit static and respond to runtime changes. Playback suspends offscreen or in a hidden tab. Pixel density is capped at 2, and resizing redraws even while paused.
- `website/layouts/partials/manifold.html` remains the original static SVG fallback when JavaScript or canvas is unavailable. Animation controls appear only after successful canvas initialization.
- `website/assets/css/atlas.css` defines the new identity for all page types. Hugo concatenates it with the shared component stylesheet and fingerprints the production asset. The prior `editorial.css` is replaced, not layered underneath it.
- `website/assets/js/atlas.js` powers four native buttons in the loop exhibit. The selected state uses `aria-pressed`; explanations update in a polite live region. It is explicitly a schematic, not a fake live agent session. Without JavaScript, the first explanation remains readable.
- The three-layer section now uses a connected runtime diagram instead of isolated decorative cards. It reads from the app (`tau_coding`) through the loop (`tau_agent`) to the model connection (`tau_ai`), with labeled request/response arrows. A concrete “Explain this project” example shows the loop executing an app-supplied `read` tool and returning results to the model. The caption distinguishes runtime communication from one-way Python dependencies. Mobile stacks the same semantic HTML with vertical arrows; no new animation or JavaScript is required.
- Documentation retains a restrained white reading surface, with the new type, navigation, code blocks, accent colors, and responsive sidebar. Releases, roadmap, math essays, search, and 404 share the same identity.
- The skip link, main landmark, visible touch copy controls, clipboard error handling, and reduced-motion support from the first pass remain.

The website describes the existing Pi-inspired separation of provider layer, portable harness, and coding application. No application architecture or dependencies changed. The mathematical artwork is decorative, not an architectural diagram.

## Preview

```sh
hugo server --source website
```

## Production checks

```sh
hugo --source website --destination /tmp/tau-atlas-preview --minify
npx --yes pagefind@latest --site /tmp/tau-atlas-preview
node --check website/assets/js/atlas.js
node --check website/assets/js/manifold.js
node --check website/static/landing.js
uv run --with playwright python website/tests/hero_smoke.py /tmp/tau-atlas-preview
uv run --with playwright python website/tests/architecture_smoke.py /tmp/tau-atlas-preview
uv run ruff check website/tests/hero_smoke.py website/tests/architecture_smoke.py
uv run ruff format --check website/tests/hero_smoke.py website/tests/architecture_smoke.py
git diff --check
```

Chromium checks passed on home, quickstart, why Tau, roadmap, releases, and 404 at widths 1440, 1024, 768, 680, 375, and 320: one main landmark and no horizontal document overflow. Also exercised all four loop steps, the mobile menu, indexed search, clipboard copying, and all three architecture anchors; no JavaScript errors. Inspected full-page desktop/mobile homepage and desktop documentation screenshots.

The animated-hero regression script checks twelve viewport sizes (including compact portrait and landscape), exact full-viewport hero height, visible artwork, controls staying within the viewport, absence of a shape picker, actual frame changes, the full automatic morph cycle, keyboard pause/play, runtime reduced-motion changes, offscreen suspension/resumption, resize, and JavaScript/canvas fallbacks. Desktop screenshots of each surface and a mobile homepage screenshot were inspected.

Hugo still reports the existing missing taxonomy-layout warning. Pagefind indexed 30 pages. Preview output stays under `/tmp`; existing generated files in `website/public/` are left untouched.
