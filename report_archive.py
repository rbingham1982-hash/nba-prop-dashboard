"""
report_archive.py — the Opportunity Report's back issues, published on konjureanalytics.com.

beehiiv's free plan refuses API-created posts (403 SEND_API_NOT_ENTERPRISE_PLAN), so every
issue after the first reached Discord as a headline and cards while the newsletter itself
never left newsletter_out/. This turns each built issue into a page under docs/report/
(served by GitHub Pages at /report/) plus an index, newest first.

Input: newsletter_out/<date>-newsletter.html (the email-ready fragment) and the matching .md
for the title. HTML comments are stripped: render_html leaves diagnostics in them ("sources
that refused us: ...") that were never meant for readers. Output is deterministic, so a
rebuild with no new issue changes nothing.

    python report_archive.py        # rebuild docs/report/; prints what changed
"""
from __future__ import annotations

import html as _html
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "newsletter_out"
OUT = ROOT / "docs" / "report"
SUBSCRIBE = "https://konjureanalytics.beehiiv.com/subscribe"

_CSS = """
:root{--bg:#0f1117;--card:#161a26;--line:#232838;--text:#c8cad4;--head:#f3f4f6;--muted:#7c8196;--accent:#818cf8;--accent-2:#6366f1}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);
line-height:1.65;padding:1.5rem 1rem 3rem}
.c{max-width:760px;margin:0 auto}
a{color:var(--accent)}
.top{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-bottom:1.6rem;font-size:.85rem}
.brand{display:flex;align-items:center;gap:.55rem;text-decoration:none;font-size:.7rem;letter-spacing:.18em;
text-transform:uppercase;font-weight:800;color:var(--accent)}
.brand img{width:28px;height:28px;border-radius:7px}
.top nav{display:flex;gap:1rem}
.top nav a{color:var(--text);text-decoration:none}
.top nav a:hover{color:var(--accent)}
.k{font-size:.72rem;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);font-weight:800}
h1.page{color:var(--head);font-size:1.8rem;line-height:1.2;margin:.4rem 0 .7rem}
.lede{max-width:600px;margin-bottom:1.6rem}
.btn{display:inline-block;padding:.7rem 1.2rem;border-radius:10px;font-weight:700;text-decoration:none;font-size:.92rem;
background:var(--accent-2);color:#fff}
.btn:hover{background:var(--accent)}
.list{list-style:none;display:grid;gap:.8rem;margin:1.8rem 0}
.list a{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.15rem;
text-decoration:none;color:var(--text)}
.list a:hover{border-color:var(--accent)}
.list .t{color:var(--head);font-weight:700}
.list .s{font-size:.85rem;color:var(--muted);margin-top:.2rem}
.issue{background:#fff;color:#111827;border-radius:14px;padding:1.6rem 1.4rem;overflow:hidden}
.issue h1{font-size:1.55rem;line-height:1.25;margin:.2rem 0 .4rem;color:#0b1020}
.issue h2{font-size:1.15rem;margin:1.6rem 0 .3rem;color:#0b1020}
.issue table{display:block;overflow-x:auto;max-width:100%}
.issue a{color:#4f46e5}
.cta{background:linear-gradient(135deg,#1b1f33,#161a26);border:1px solid var(--line);border-radius:14px;
padding:1.4rem;margin:1.8rem 0}
.cta p{margin:.3rem 0 1rem}
.pager{display:flex;justify-content:space-between;gap:1rem;font-size:.9rem;margin-top:1.4rem}
.foot{border-top:1px solid var(--line);margin-top:2rem;padding-top:1.2rem;font-size:.8rem;color:var(--muted)}
@media (max-width:520px){.issue{padding:1.1rem .9rem}h1.page{font-size:1.5rem}.top nav a.opt{display:none}}
"""


def _page(title: str, desc: str, body: str, depth: str = "../") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)}</title>
<meta name="description" content="{_html.escape(desc)}">
<link rel="icon" href="{depth}app-icon.jpg">
<style>{_CSS}</style></head><body><div class="c">
<div class="top"><a class="brand" href="{depth}"><img src="{depth}app-icon.jpg" alt="">Konjure Analytics</a>
<nav><a href="{depth}report/">Past issues</a><a class="opt" href="{depth}app/">Dashboard</a></nav></div>
{body}
<div class="foot">Model output for informational and entertainment purposes — not betting advice. 18+ only.
<a href="{depth}privacy.html">Privacy</a> · <a href="mailto:info@konjureanalytics.com">info@konjureanalytics.com</a></div>
</div></body></html>
"""


def _cta() -> str:
    return (f'<div class="cta"><div class="k">The Opportunity Report</div>'
            f'<p>Get the next issue in your inbox — free, weekly, unsubscribe any time.</p>'
            f'<a class="btn" href="{SUBSCRIBE}">Subscribe free</a></div>')


def issues() -> list[dict]:
    """Every built issue, newest first: date, title, cleaned HTML, and its section headings."""
    out = []
    for f in sorted(SRC.glob("*-newsletter.html"), reverse=True):
        date = f.name[:10]
        md = SRC / f"{date}-newsletter.md"
        title = (md.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
                 if md.exists() else f"The Opportunity Report — {date}")
        body = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
        body = re.sub(r'<p style="[^"]*">\s*</p>', "", body)          # paragraphs emptied by the strip
        heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<h2>(.*?)</h2>", body)]
        out.append({"date": date, "title": title, "html": body, "sections": heads})
    return out


def _write(path: pathlib.Path, text: str, changed: list) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    changed.append(str(path.relative_to(ROOT)).replace("\\", "/"))


def build() -> list[str]:
    """Write docs/report/<date>.html for every issue and docs/report/index.html. Returns changed paths."""
    items, changed = issues(), []
    for i, it in enumerate(items):
        newer = items[i - 1] if i > 0 else None
        older = items[i + 1] if i + 1 < len(items) else None
        pager = ('<div class="pager">'
                 + (f'<a href="{older["date"]}.html">← {_html.escape(older["title"].split("—")[-1].strip())}</a>'
                    if older else "<span></span>")
                 + (f'<a href="{newer["date"]}.html">{_html.escape(newer["title"].split("—")[-1].strip())} →</a>'
                    if newer else "<span></span>")
                 + "</div>")
        desc = "In this issue: " + ", ".join(it["sections"][:5]) if it["sections"] else it["title"]
        body = f'<article class="issue">{it["html"]}</article>{pager}{_cta()}'
        _write(OUT / f"{it['date']}.html", _page(it["title"], desc, body, depth="../"), changed)
    rows = "".join(
        f'<li><a href="{it["date"]}.html"><div class="t">{_html.escape(it["title"])}</div>'
        f'<div class="s">{_html.escape(" · ".join(it["sections"][:4]))}</div></a></li>' for it in items)
    index = (f'<div class="k">The Opportunity Report</div><h1 class="page">Past issues</h1>'
             f'<p class="lede">Every issue of our weekly report: player grades, projections, waiver and '
             f'start/sit calls, and a graded recap of the picks we logged. Newest first.</p>'
             f'<a class="btn" href="{SUBSCRIBE}">Get the next one free</a>'
             f'<ul class="list">{rows}</ul>')
    _write(OUT / "index.html",
           _page("Past issues · The Opportunity Report", "Every issue of The Opportunity Report from Konjure Analytics.",
                 index, depth="../"), changed)
    return changed


if __name__ == "__main__":
    names = build()
    print(f"{len(names)} file(s) updated" + (": " + ", ".join(names) if names else ""))
