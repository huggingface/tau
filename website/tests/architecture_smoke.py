"""Check the connected architecture diagram in a built Hugo site."""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from hero_smoke import QuietHandler
from playwright.sync_api import expect, sync_playwright


def check_architecture(root: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            # The diagram, explanatory text, and links need no JavaScript.
            page = browser.new_page(java_script_enabled=False)
            for width in [1440, 1024, 860, 768, 760, 375, 320]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.goto(url)
                diagram = page.locator(".architecture-map")
                expect(diagram).to_be_visible()
                nodes = diagram.locator(".architecture-node")
                expect(nodes).to_have_count(3)
                assert nodes.locator("h3").all_text_contents() == [
                    "The app",
                    "The agent loop",
                    "The model connection",
                ]
                for connector in diagram.locator(".architecture-connector").all():
                    assert connector.get_attribute("role") == "img"
                    assert connector.get_attribute("aria-label")
                    expect(connector.locator(".connector-out")).to_be_visible()
                    expect(connector.locator(".connector-back")).to_be_visible()
                boxes = [node.bounding_box() for node in nodes.all()]
                if width > 760:
                    assert max(box["y"] for box in boxes) - min(box["y"] for box in boxes) < 1
                    assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
                else:
                    assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
                expect(page.locator(".architecture-example ol li")).to_have_count(3)
                assert "not code dependencies" in diagram.locator("figcaption").inner_text()

            links = page.locator(".architecture-package")
            assert [link.inner_text().split()[0] for link in links.all()] == [
                "tau_coding",
                "tau_agent",
                "tau_ai",
            ]
            destinations = [link.get_attribute("href") for link in links.all()]
            for destination in destinations:
                parts = urlsplit(destination)
                response = page.goto(url + parts.path)
                assert response.ok
                expect(page.locator(f'[id="{parts.fragment}"]')).to_be_visible()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(
        "PASS: connected layer order, request/response arrows, examples, links, and no-JS layouts"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Hugo build output directory")
    check_architecture(parser.parse_args().site.resolve())
