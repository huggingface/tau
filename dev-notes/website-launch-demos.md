# Launch-thread demos on the website

The homepage now follows the full-height animated hero with four real product recordings, feature descriptions, links to current guides, and links to their original posts. Local inference gets a wide featured card; the model catalog, extensions, and system prompt get three smaller cards. Mobile stacks the recordings vertically.

## Sources

All recordings come from Alejandro's August 28, 2026 Tau v0.4.0 thread:

| Feature | Source post | Video ID | Length |
| --- | --- | --- | --- |
| Local inference | https://x.com/_alejandroao/status/2093395753645314556 | 2093395419615072256 | 1:07 |
| Model catalog | https://x.com/_alejandroao/status/2093395756098990190 | 2093379787930677248 | 0:16 |
| Extensions | https://x.com/_alejandroao/status/2093395758754009432 | 2093380047964868608 | 0:10 |
| System prompt | https://x.com/_alejandroao/status/2093395761752948819 | 2093381346194288640 | 0:13 |

Firecrawl retrieved the thread text; yt-dlp extracted the original video variants. The gallery identifies these as v0.4.0 recordings, not a claim that every recorded screen is unchanged in the current release. Current setup instructions remain in the linked guides. Text distinguishes the extensions shown on screen from the broader capabilities announced in the post.

## Files

- `website/data/demos.yaml`: titles, descriptions, text alternatives, durations, documentation links, source attribution, Bunny library/video IDs, and public CDN playback URLs.
- `website/layouts/partials/video-card.html`: shared native video card.
- `website/static/media/demos/`: four optimized MP4 files and WebP posters, about 13 MiB combined.
- `website/assets/js/demos.js`: muted viewport autoplay, offscreen/tab-hidden pausing, reduced-motion support, and respect for manual pauses.
- `website/tests/demo_smoke.py`: native playback and layout regression checks.

These are Bunny-hosted MP4s in production, not X embeds: no login, third-party player, tracking script, or reliance on expiring X media URLs. Local assets remain as development backups. The videos preserve their original framing and length. ffmpeg encodes H.264, 30 fps, CRF 25, yuv420p, and fast-start MP4. Original audio tracks measured silence (maximum -91 dB) and were removed. Posters use frames at 30, 3, 5, and 8 seconds respectively, resized to 1100 px wide and encoded as WebP quality 85.

Example preparation after downloading the source:

```sh
ffmpeg -i source.mp4 -map 0:v:0 -an -vf fps=30 \
  -c:v libx264 -preset slow -crf 25 -pix_fmt yuv420p \
  -movflags +faststart -map_metadata -1 output.mp4
ffmpeg -ss 30 -i source.mp4 -frames:v 1 -vf scale=1100:-1 \
  -c:v libwebp -quality 85 poster.webp
```

## Hosting

All four optimized videos were uploaded with the `video-tool` skill, after explicit approval, to Bunny Stream library **506199** on September 7, 2026. Uploads ran in `tmux`; the receipt is `.firecrawl/tau-bunny-upload.json`, and the public IDs are also preserved in `website/data/demos.yaml`. No credentials are included in website data or templates.

| Demo | Bunny video ID |
| --- | --- |
| Local inference | `180e97fc-74d5-4d43-968e-e8f239870273` |
| Model catalog | `3372aa9f-ee46-4979-baf6-9f906f58f5fb` |
| Extensions | `9e44461c-5ced-4af9-98a7-ca30710dfaac` |
| System prompt | `eb1bae4e-ac2d-4614-8402-8788dab838db` |

Production templates use `video_url` on **`https://vz-3d011147-43d.b-cdn.net`**. For example:

```
https://vz-3d011147-43d.b-cdn.net/180e97fc-74d5-4d43-968e-e8f239870273/original
```

These originals are the already optimized, fast-start H.264 files. All four were verified with HTTP byte-range requests (206) and real Chromium playback. Bunny's additional encoding remained queued during verification. The native player also lists Bunny's documented `play_1080p.mp4` and `play_720p.mp4` sources as fallbacks; those renditions are not considered verified until processing finishes. If original retention is disabled, these encoded sources will be needed after the originals are removed. Do not delete local backups or retry paid uploads merely because transcoding is queued.

The existing CDN configuration requires an allowed referrer: requests with `Referer: https://twotimespi.dev/` succeed, while requests without a referrer are denied. No library settings or security rules were changed. Accordingly, `hugo server` / development builds use local `/media/demos/*.mp4` backups; production builds use Bunny. The live-CDN test supplies the allowed production referrer when testing from localhost. It does not proxy or mock the video responses.

Posters remain local WebP files. The existing `.github/workflows/docs.yml` still deploys the website to GitHub Pages; this change does not itself publish a new website deployment. Local MP4s remain under `website/static/media/demos/`, and raw downloads remain under ignored `.firecrawl/tau-videos/`. Source files are not deleted.

Bunny's documented storage paths: https://bunny.net/docs/stream/storage-structure.

## Playback and accessibility

Players use native controls, muted inline playback, descriptive labels, and `preload="none"`. JavaScript automatically plays each silent clip when at least 35% of it is visible, and pauses it offscreen or when the tab is hidden. Visible cards can play together; no clip loops. A manual pause persists across scrolling. Reduced-motion users get click-to-play, and browser autoplay rejection leaves the controls usable. No MP4 is fetched while its player remains below the fold. The posters remain visible before playback. Each silent recording has an expandable written alternative describing its actions, plus a link to the original post. Native playback, source links, and written summaries remain available without JavaScript. Fullscreen is available through the browser's player controls.

## Validation

```sh
# Live production CDN check (requires network access).
hugo --source website --destination /tmp/tau-bunny-preview --minify
uv run --with playwright python website/tests/demo_smoke.py /tmp/tau-bunny-preview \
  --cdn-referrer https://twotimespi.dev/

# Local preview/backups; no Bunny dependency.
hugo --source website --environment development --destination /tmp/tau-demos-preview
uv run --with playwright python website/tests/demo_smoke.py /tmp/tau-demos-preview
uv run --with playwright python website/tests/hero_smoke.py /tmp/tau-demos-preview
uv run ruff check website/tests/demo_smoke.py
uv run ruff format --check website/tests/demo_smoke.py
node --check website/assets/js/demos.js
git diff --check
```

Verified that all four videos actually play in Chromium, no MP4 is fetched before playback, docs/poster links resolve, visible clips autoplay muted, offscreen clips pause and resume, manual pauses persist, reduced-motion changes stop playback, blocked autoplay fails gracefully, summaries open with the keyboard, and layouts fit 320–1440 px widths. The full-viewport hero/animation suite also passes. Desktop and mobile gallery screenshots were inspected. Hugo still emits its existing missing taxonomy-layout warning. Original downloads and temporary build output are not added to the website's generated `public/` directory.
