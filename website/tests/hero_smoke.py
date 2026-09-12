"""Browser checks for a built site. Run with uv run --with playwright (see dev note)."""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def snapshot(page):
    return page.locator("#manifoldCanvas").evaluate("canvas => canvas.toDataURL()")


def check_site(root: Path):
    theme_path = Path(__file__).resolve().parents[2] / "src/tau_coding/tui/themes/tau-dark.json"
    accent = json.loads(theme_path.read_text())["colors"]["accent"].lstrip("#")
    rgb = ", ".join(str(int(accent[i : i + 2], 16)) for i in (0, 2, 4))
    expected_accent = f"rgb({rgb})"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            sizes = [(width, 1000) for width in [1440, 1024, 860, 768, 680, 425, 375, 320]]
            sizes += [(1440, 800), (375, 667), (320, 568), (844, 390)]
            for width, height in sizes:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(url)
                page.evaluate("document.fonts.ready")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                    f"Horizontal overflow at {width}px"
                )
                expect(page.locator("#manifoldCanvas")).to_be_visible()
                expect(page.locator("[data-surface]")).to_have_count(0)
                bounds = page.locator(".atlas-hero").bounding_box()
                assert abs(bounds["height"] - height) < 1, (width, height, bounds)
                assert bounds["y"] == 0, "Navigation must be included in the first viewport"
                for selector in [".hero-actions", ".sculpture-caption", ".atlas-install-row"]:
                    box = page.locator(selector).bounding_box()
                    assert box["y"] + box["height"] <= height + 1, (width, height, selector)
                assert page.locator("#manifoldCanvas").bounding_box()["height"] > 50
                assert (
                    page.locator(".atlas-hero").evaluate(
                        "el => getComputedStyle(el).backgroundColor"
                    )
                    == "rgb(0, 0, 0)"
                )

            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(url)
            for selector, property_name in [
                (".hero-copy h1 > span", "color"),
                (".hero-actions .atlas-button", "backgroundColor"),
                (".manifold-art", "color"),
                (".atlas-finale", "backgroundColor"),
            ]:
                actual = page.locator(selector).evaluate(
                    "(el, property) => getComputedStyle(el)[property]", property_name
                )
                assert actual == expected_accent, (selector, actual, expected_accent)
            expect(page.locator("#motionToggle")).to_have_attribute("aria-label", "Play animation")
            page.wait_for_timeout(100)
            still = snapshot(page)
            page.wait_for_timeout(200)
            assert still == snapshot(page), "Reduced motion must start static"

            page.set_viewport_size({"width": 375, "height": 1000})
            page.wait_for_timeout(100)
            assert page.locator("#manifoldCanvas").evaluate("c => c.width > 1 && c.height > 1"), (
                "Static canvas must redraw after resize"
            )
            page.locator("#navToggle").click()
            expect(page.locator("#navlinks")).to_be_visible()
            page.locator("#navToggle").click()

            page.set_viewport_size({"width": 1440, "height": 1000})
            page.emulate_media(reduced_motion="no-preference")
            page.clock.install()
            page.goto(url)
            page.clock.run_for(200)
            moving = snapshot(page)
            page.clock.run_for(500)
            assert moving != snapshot(page), "The surface should rotate"
            page.locator("#motionToggle").focus()
            page.keyboard.press("Enter")
            stopped = snapshot(page)
            page.clock.run_for(500)
            assert stopped == snapshot(page), "Pause should freeze the current frame"
            page.locator("#motionToggle").click()
            # The entire cycle advances automatically; no shape selection is needed.
            shapes = set()
            for label in ["02 / Sphere", "03 / Möbius strip", "01 / Torus"]:
                page.clock.run_for(4100)
                expect(page.locator("#surfaceName")).to_have_text(label)
                shapes.add(snapshot(page))
            assert len(shapes) == 3, "The automatic cycle must draw different shapes"
            page.emulate_media(reduced_motion="reduce")
            expect(page.locator("#motionToggle")).to_have_attribute("aria-label", "Play animation")
            reduced = snapshot(page)
            page.clock.run_for(1000)
            assert reduced == snapshot(page), "OS preference changes must stop motion"

            page.emulate_media(reduced_motion="no-preference")
            page.locator(".atlas-finale").scroll_into_view_if_needed()
            page.wait_for_timeout(100)
            offscreen = snapshot(page)
            page.clock.run_for(1000)
            assert offscreen == snapshot(page), "Offscreen animation should not render"
            page.locator(".hero-sculpture").scroll_into_view_if_needed()
            page.wait_for_timeout(100)
            page.clock.run_for(500)
            assert offscreen != snapshot(page), "Visible animation should resume"

            fallback = browser.new_page(java_script_enabled=False)
            fallback.goto(url)
            expect(fallback.locator(".manifold-fallback svg")).to_be_visible()
            expect(fallback.locator("#sculptureControls")).to_be_hidden()
            expect(fallback.get_by_role("link", name="Get started ↗", exact=True)).to_be_visible()

            no_canvas = browser.new_page()
            no_canvas.add_init_script("HTMLCanvasElement.prototype.getContext = () => null")
            no_canvas.goto(url)
            expect(no_canvas.locator(".manifold-fallback svg")).to_be_visible()
            expect(no_canvas.locator("#sculptureControls")).to_be_hidden()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(
        "PASS: responsive hero, rotation, morph cycle, keyboard controls, "
        "pause, reduced motion, offscreen suspension, and fallbacks"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Hugo build output directory")
    check_site(parser.parse_args().site.resolve())
