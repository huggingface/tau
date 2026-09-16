"""Check the native demo gallery against a Hugo build, without depending on X."""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from hero_smoke import QuietHandler
from playwright.sync_api import expect, sync_playwright


def check_demos(root: Path, cdn_referrer: str | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            def new_page(**options):
                result = browser.new_page(**options)
                if cdn_referrer:
                    # Exercise real Bunny media with the deployed site's allowed origin.
                    result.route(
                        "https://*.b-cdn.net/**",
                        lambda route: route.continue_(
                            headers={**route.request.headers, "referer": cdn_referrer}
                        ),
                    )
                return result

            page = new_page(reduced_motion="reduce")
            errors = []
            media_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: (
                    media_requests.append(request.url) if request.resource_type == "media" else None
                ),
            )
            page.goto(url)
            page.locator("#demos").scroll_into_view_if_needed()
            videos = page.locator(".demo-recording video")
            expect(videos).to_have_count(4)
            page.wait_for_timeout(300)
            assert not media_requests, "Videos must not download before playback"

            for index in range(4):
                video = videos.nth(index)
                assert video.get_attribute("preload") == "none"
                source = video.locator("source").first.get_attribute("src")
                if cdn_referrer:
                    assert source.startswith("https://vz-3d011147-43d.b-cdn.net/")
                else:
                    assert source.startswith("/media/demos/")
                assert video.get_attribute("autoplay") is None
                assert video.get_attribute("controls") is not None
                assert video.get_attribute("muted") is not None
                assert video.get_attribute("playsinline") is not None
                poster = video.get_attribute("poster")
                assert page.request.get(url + poster).ok
                description = video.get_attribute("aria-describedby")
                assert page.locator(f"#{description}").text_content().strip()
                card = page.locator(".demo-card").nth(index)
                guide = card.locator(".atlas-text-link").get_attribute("href")
                assert page.request.get(url + guide).ok, guide
                assert (
                    card.locator(".demo-source")
                    .get_attribute("href")
                    .startswith("https://x.com/_alejandroao/status/")
                )

                await_play = "async video => { await video.play(); }"
                video.evaluate(await_play)
                page.wait_for_function(
                    "index => document.querySelectorAll('.demo-recording video')"
                    "[index].currentTime > 0",
                    arg=index,
                )
                video.evaluate("video => video.pause()")

            autoplay = new_page(reduced_motion="no-preference")
            autoplay.on("pageerror", lambda error: errors.append(str(error)))
            autoplay.goto(url)
            auto_video = autoplay.locator(".demo-recording video").first
            assert auto_video.evaluate("video => video.paused && video.readyState === 0")
            auto_video.scroll_into_view_if_needed()
            autoplay.wait_for_function(
                "document.querySelector('.demo-recording video').currentTime > 0"
            )
            assert auto_video.evaluate("video => video.muted && !video.paused")
            autoplay.locator(".atlas-finale").scroll_into_view_if_needed()
            autoplay.wait_for_function("document.querySelector('.demo-recording video').paused")
            auto_video.scroll_into_view_if_needed()
            autoplay.wait_for_function("!document.querySelector('.demo-recording video').paused")

            # A viewer's pause must survive scrolling away and back.
            auto_video.evaluate("video => video.pause()")
            autoplay.wait_for_timeout(100)
            autoplay.locator(".atlas-finale").scroll_into_view_if_needed()
            auto_video.scroll_into_view_if_needed()
            autoplay.wait_for_timeout(300)
            assert auto_video.evaluate("video => video.paused")
            auto_video.evaluate("video => video.play()")
            autoplay.emulate_media(reduced_motion="reduce")
            autoplay.wait_for_function("document.querySelector('.demo-recording video').paused")
            autoplay.wait_for_timeout(300)
            assert auto_video.evaluate("video => video.paused")
            autoplay.close()

            # Browser autoplay rejection must not hide controls or throw page errors.
            blocked = new_page(reduced_motion="no-preference")
            blocked.on("pageerror", lambda error: errors.append(str(error)))
            blocked.add_init_script(
                "HTMLMediaElement.prototype.play = () => "
                "Promise.reject(new DOMException('Blocked', 'NotAllowedError'))"
            )
            blocked.goto(url)
            blocked.locator(".demo-recording video").first.scroll_into_view_if_needed()
            blocked.wait_for_timeout(300)
            assert blocked.locator(".demo-recording video").first.evaluate(
                "video => video.paused && video.controls"
            )
            blocked.close()

            summary = page.locator(".demo-summary summary").first
            summary.focus()
            page.keyboard.press("Enter")
            assert page.locator(".demo-summary").first.get_attribute("open") is not None

            for width in [1440, 1024, 768, 375, 320]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.evaluate("document.fonts.ready")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
                for index in range(4):
                    box = videos.nth(index).bounding_box()
                    assert box["width"] > 200 and box["height"] > 150

            fallback = new_page(java_script_enabled=False)
            fallback.goto(url)
            expect(fallback.locator(".demo-recording video")).to_have_count(4)
            expect(fallback.locator(".demo-summary summary").first).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(
        "PASS: four playable demos, no eager downloads, source/docs links, "
        "viewport autoplay, manual pause, reduced motion, responsive layouts, and no-JS markup"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Hugo build output directory")
    parser.add_argument(
        "--cdn-referrer", help="Allowed production origin for testing Bunny playback from localhost"
    )
    args = parser.parse_args()
    check_demos(args.site.resolve(), args.cdn_referrer)
