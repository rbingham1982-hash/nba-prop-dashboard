import requests
resp = requests.get(
    "https://api.prizepicks.com/projections?league_id=2&per_page=50&single_stat=true",
    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    timeout=10
)
print("PrizePicks MLB status:", resp.status_code)
if resp.ok:
    data = resp.json()
    rows = [r for r in data.get("data", []) if r.get("type") == "projection"]
    stats = set(r.get("attributes", {}).get("stat_type", "") for r in rows)
    print("MLB stat types:", sorted(stats))
    print("Row count:", len(rows))
    player_map = {i["id"]: i["attributes"].get("display_name", "") for i in data.get("included", []) if i.get("type") == "new_player"}
    players = [player_map.get(r.get("relationships", {}).get("new_player", {}).get("data", {}).get("id", ""), "?") for r in rows[:5]]
    print("Sample players:", players)
