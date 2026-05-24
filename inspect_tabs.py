from playwright.sync_api import sync_playwright

APP_URL = "https://nba-prop-dashboard-dhmu5b4fonzesfkydqwznu.streamlit.app/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(APP_URL, timeout=120000)
    page.wait_for_timeout(15000)

    # Check for iframes
    frames = page.frames
    print(f"Total frames: {len(frames)}")
    for i, f in enumerate(frames):
        url = f.url
        btns = f.query_selector_all("button")
        tabs = f.query_selector_all('[role="tab"]')
        print(f"  Frame {i}: {url[:80]}  buttons={len(btns)}  role-tabs={len(tabs)}")
        if tabs:
            for t in tabs[:5]:
                print(f"    tab: {t.inner_text()[:40]!r}")
    browser.close()
