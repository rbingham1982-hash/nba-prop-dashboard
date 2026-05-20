from playwright.sync_api import sync_playwright

APP_URL = "https://nba-prop-dashboard-dhmu5b4fonzesfkydqwznu.streamlit.app/"
OUT = r"C:\Users\rbing\nba-prop-dashboard\screenshots"

APP_FRAME_URL = "/~/+/"  # Streamlit app content lives in this iframe

def get_app_frame(page):
    for f in page.frames:
        if APP_FRAME_URL in f.url:
            return f
    return None

def wait_st(page, ms=5000):
    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    page.wait_for_timeout(ms)

def click_tab(frame, page, label, wait_ms=3500):
    tabs = frame.query_selector_all('[role="tab"]')
    for t in tabs:
        if label.lower() in t.inner_text().lower():
            t.scroll_into_view_if_needed()
            t.click()
            page.wait_for_timeout(wait_ms)
            print(f"  clicked tab: {t.inner_text().strip()!r}")
            return
    print(f"  WARNING: tab {label!r} not found (available: {[t.inner_text()[:20] for t in tabs]})")

def select_mlb(frame, page):
    box = frame.query_selector('[data-testid="stSelectbox"]')
    if box:
        box.click()
        page.wait_for_timeout(600)
        opts = frame.query_selector_all('li[role="option"]')
        for o in opts:
            if "MLB" in o.inner_text():
                o.click()
                page.wait_for_timeout(5000)
                print("  switched to MLB")
                return
    print("  WARNING: could not switch to MLB")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-gpu"])

    # ── 1. NBA Home (desktop 1280×900) ────────────────────────────────────────
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    print("Loading NBA Home (desktop)…")
    page.goto(APP_URL, timeout=120000)
    wait_st(page, 12000)
    frame = get_app_frame(page)
    page.screenshot(path=f"{OUT}\\01_nba_home.png")
    print("  01_nba_home.png")

    # ── 2. NBA Player Stats ───────────────────────────────────────────────────
    click_tab(frame, page, "PLAYER STATS")
    page.screenshot(path=f"{OUT}\\02_nba_player_stats.png")
    print("  02_nba_player_stats.png")

    # ── 3. Sportsbook tab ─────────────────────────────────────────────────────
    click_tab(frame, page, "SPORTSBOOK")
    page.screenshot(path=f"{OUT}\\03_nba_sportsbook.png")
    print("  03_nba_sportsbook.png")

    # ── 4. Parlays tab — subscriber gate ─────────────────────────────────────
    click_tab(frame, page, "PARLAYS")
    page.screenshot(path=f"{OUT}\\04_parlays_gate.png")
    print("  04_parlays_gate.png")

    page.close()

    # ── 5–6. MLB Home + Hitter Analysis ──────────────────────────────────────
    page2 = browser.new_page(viewport={"width": 1280, "height": 900})
    print("\nLoading MLB (desktop)…")
    page2.goto(APP_URL, timeout=120000)
    wait_st(page2, 12000)
    frame2 = get_app_frame(page2)
    select_mlb(frame2, page2)
    page2.screenshot(path=f"{OUT}\\05_mlb_home.png")
    print("  05_mlb_home.png")

    click_tab(frame2, page2, "HITTER")
    page2.screenshot(path=f"{OUT}\\06_mlb_hitter.png")
    print("  06_mlb_hitter.png")

    page2.close()

    # ── 7–8. Phone portrait 390×844 ───────────────────────────────────────────
    page3 = browser.new_page(viewport={"width": 390, "height": 844},
                             device_scale_factor=3)
    print("\nLoading phone viewport…")
    page3.goto(APP_URL, timeout=120000)
    wait_st(page3, 12000)
    frame3 = get_app_frame(page3)
    page3.screenshot(path=f"{OUT}\\07_phone_home.png")
    print("  07_phone_home.png")

    click_tab(frame3, page3, "PLAYER STATS", wait_ms=4000)
    page3.screenshot(path=f"{OUT}\\08_phone_player_stats.png")
    print("  08_phone_player_stats.png")

    click_tab(frame3, page3, "PARLAYS", wait_ms=4000)
    page3.screenshot(path=f"{OUT}\\09_phone_parlays_gate.png")
    print("  09_phone_parlays_gate.png")

    page3.close()
    browser.close()

print(f"\nDone — screenshots in {OUT}")
