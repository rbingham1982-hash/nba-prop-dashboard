from playwright.sync_api import sync_playwright
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(_HERE, "feature_graphic.html")
OUT_PATH  = os.path.join(_HERE, "screenshots", "feature_graphic_1024x500.png")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
    page = browser.new_page(viewport={"width": 1024, "height": 500}, device_scale_factor=1)
    page.goto(f"file:///{HTML_PATH}")
    page.wait_for_timeout(500)
    page.screenshot(path=OUT_PATH, clip={"x": 0, "y": 0, "width": 1024, "height": 500})
    browser.close()

print(f"Saved: {OUT_PATH}")
