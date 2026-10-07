import os, requests
from dotenv import load_dotenv
load_dotenv()
base = os.environ["FLEETBASE_API_URL"]
h = {"Authorization": "Bearer " + os.environ["FLEETBASE_API_KEY"]}

def short(v):
    if isinstance(v, dict):
        return {"id": v.get("id"), "name": v.get("name"), "campos": list(v)[:8]}
    return v

for path in ["/orders", "/drivers"]:
    r = requests.get(base + path, headers=h, params={"limit": 20}).json()
    items = r if isinstance(r, list) else r.get("data", [])
    print(f"\n=== {path}: {len(items)} itens ===")
    for k, v in items[0].items():
        if k != "payload":
            print(f"{k}: {short(v)}")
    if path == "/orders":
        print("\nstatus e motorista de cada pedido:")
        for o in items:
            d = o.get("driver_assigned")
            print(o["internal_id"], o.get("status"), o.get("scheduled_at"),
                  d.get("name") if isinstance(d, dict) else d)
EOF
