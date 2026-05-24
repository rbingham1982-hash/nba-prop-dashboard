import sys, requests, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

_PP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com",
}
_PP_DEAD = {"final","cancelled","failed","lost","won","scored","no_contest"}

r = requests.get("https://api.prizepicks.com/projections?league_id=7&per_page=500&single_stat=true",
                  headers=_PP_HEADERS, timeout=15)
payload = r.json()
stats = {}
for p in payload.get("data", []):
    if p.get("type") != "projection":
        continue
    a = p.get("attributes", {})
    if a.get("status","pre_game") in _PP_DEAD:
        continue
    st = a.get("stat_type","")
    stats[st] = stats.get(st, 0) + 1

print("PrizePicks NBA stat types (count):")
for k,v in sorted(stats.items()):
    print(f"  {k}: {v}")

# Now check which ones match the parlay filter defaults
defaults = ["Points", "Rebounds", "Assists", "3-PT Made", "Pts+Rebs+Asts"]
print(f"\nDefault parlay filter: {defaults}")
matched = [s for s in defaults if s in stats]
missing = [s for s in defaults if s not in stats]
print(f"Matched: {matched}")
print(f"Missing from PP data: {missing}")
print(f"\nTotal matching rows: {sum(stats.get(s,0) for s in matched)}")
