"""Render brief.html to one PNG per page and a PDF with headless Chromium.

usage: python scripts/render_brief.py runs/assistant_demo
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/project/home/p201509/cache/ms-playwright")
LIBS = "/project/home/p201509/envs/browser-libs/lib"
run = Path(sys.argv[1]).resolve()
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(env=dict(os.environ, LD_LIBRARY_PATH=LIBS))
    page = b.new_page(viewport={"width": 1680, "height": 1200}, device_scale_factor=2)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((run / "brief.html").as_uri())
    pages = page.query_selector_all(".page")
    for n, el in enumerate(pages, 1):
        el.screenshot(path=str(run / f"brief_p{n}.png"))
    page.emulate_media(media="print")
    page.pdf(path=str(run / "brief.pdf"), width="1600px", height="1560px", print_background=True,
             margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}, prefer_css_page_size=False)  # page 1 is 1560 px
    heights = [el.bounding_box()["height"] for el in pages]
    b.close()
print("pages", len(pages), "heights", heights, "errors", errors)
