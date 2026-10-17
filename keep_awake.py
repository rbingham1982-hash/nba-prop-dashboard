"""
keep_awake.py — visit the hosted dashboard so Streamlit Community Cloud does not put it to sleep.

Community Cloud sleeps an app after a stretch with no visitors and then shows a "Zzzz"
page with a wake button, so the first visitor after a quiet night got that instead of the
dashboard. A plain HTTP request does not count as a visit (the app runs over a websocket),
so this opens the page in a real headless browser, presses the wake button if the app is
asleep, and waits until the dashboard itself has rendered.

Run by .github/workflows/keep-awake.yml. Exit code 1 means the app never came up.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

URL = "https://nba-prop-dashboard-dhmu5b4fonzesfkydqwznu.streamlit.app/"
WAKE = "Yes, get this app back up"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(8_000)
        woke = False
        if page.get_by_text(WAKE).count():
            page.get_by_text(WAKE).click()
            woke = True
            print("app was asleep; pressed the wake button")
        # The app renders inside an iframe on the share URL; its header text means it is up.
        for _ in range(36):                                   # up to ~6 minutes for a cold start
            for frame in page.frames:
                try:
                    if frame.get_by_text("KONJURE ANALYTICS", exact=False).count():
                        print("dashboard is up" + (" (after waking)" if woke else ""))
                        page.wait_for_timeout(20_000)         # stay connected for a moment
                        browser.close()
                        return 0
                except Exception:
                    pass
            page.wait_for_timeout(10_000)
        print("dashboard did not render", file=sys.stderr)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
