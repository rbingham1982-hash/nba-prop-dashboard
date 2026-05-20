from playwright.sync_api import sync_playwright
import os

HTML_PATH = os.path.abspath(r"C:\Users\rbing\nba-prop-dashboard\feature_graphic.html")
OUT_PATH  = r"C:\Users\rbing\nba-prop-dashboard\screenshots\feature_graphic_1024x500.png"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
    page = browser.new_page(viewport={"width": 1024, "height": 500}, device_scale_factor=1)
    page.goto(f"file:///{HTML_PATH}")
    page.wait_for_timeout(500)
    page.screenshot(path=OUT_PATH, clip={"x": 0, "y": 0, "width": 1024, "height": 500})
    browser.close()

print(f"Saved: {OUT_PATH}")
