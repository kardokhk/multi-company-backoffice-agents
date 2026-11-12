"""Screenshot the approval web app with headless Chromium.

usage: python scripts/screenshots.py BASE_URL OUT_DIR COMPANY
Signs in as the group finance lead, records two decisions on COMPANY (approve one grouped posting,
reject one blocked email) so the decision list and audit trail have content, then captures pages.
"""
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/project/home/p201509/cache/ms-playwright")
LIBS = "/project/home/p201509/envs/browser-libs/lib"
base, out, cid = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(env=dict(os.environ, LD_LIBRARY_PATH=LIBS))
    ctx = b.new_context(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login")
    page.screenshot(path=str(out / "login.png"))
    page.click("button[value=maria]")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(out / "portfolio.png"), full_page=True)
    page.goto(f"{base}/c/{cid}")
    html = page.content()
    rows = re.findall(r'<tr><td><span class="pill (\w+)">.*?</td><td>(.*?)</td>.*?/decide/(\d+)', html, flags=re.S)
    grouped = next((pid for st, title, pid in rows if st == "pending" and "lines from this counterparty" in title), None)
    risky = next((pid for st, title, pid in rows if st == "blocked" and "payment risk" in title), None)
    for pid, action in ((grouped, "approved"), (risky, "rejected")):
        if pid:
            page.click(f'form[action="/c/{cid}/decide/{pid}"] button[value={action}]')
            page.wait_for_load_state("networkidle")
    page.goto(f"{base}/c/{cid}")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(out / "company_full.png"), full_page=True)
    page.screenshot(path=str(out / "company_top.png"))
    b.close()
print("screenshots in", out, "errors:", errors)
