"""scripts/check_quota.py — Check Vertex AI Llama MaaS quota."""
import httpx, json, asyncio, subprocess

PROJECT = "ieor-4576-agents-487001"
BASE = f"https://serviceusage.googleapis.com/v1beta1/projects/{PROJECT}/services/aiplatform.googleapis.com/consumerQuotaMetrics"

token = subprocess.run("gcloud auth print-access-token", shell=True, capture_output=True, text=True).stdout.strip()
headers = {"Authorization": f"Bearer {token}"}

async def main():
    all_metrics = []
    url = f"{BASE}?pageSize=200"
    async with httpx.AsyncClient(timeout=30) as c:
        while url:
            r = await c.get(url, headers=headers)
            d = r.json()
            batch = d.get("metrics", [])
            all_metrics.extend(batch)
            next_token = d.get("nextPageToken")
            url = f"{BASE}?pageSize=200&pageToken={next_token}" if next_token else None

    print(f"Total metrics: {len(all_metrics)}\n")

    # Find anything Llama or MaaS related (by name field, not just display name)
    KEYS = ("llama", "maas", "scout", "online_serving", "online-serving")
    for m in all_metrics:
        raw = json.dumps(m).lower()
        if any(k in raw for k in KEYS):
            print("=" * 70)
            print(f"displayName: {m.get('displayName', '?')}")
            print(f"name:        {m.get('name', '?')}")
            for group in m.get("consumerQuotaLimits", []):
                print(f"  limit unit:  {group.get('unit', '?')}")
                for bucket in group.get("quotaBuckets", []):
                    eff = bucket.get("effectiveLimit", "?")
                    dims = bucket.get("dimensions", {})
                    override = bucket.get("consumerOverride", {})
                    default = bucket.get("defaultLimit", "?")
                    print(f"    effectiveLimit={eff}  defaultLimit={default}  dims={dims}  override={override}")

asyncio.run(main())
