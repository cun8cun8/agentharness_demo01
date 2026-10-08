"""Read-only application checks and a local Alertmanager routing fixture."""
import json,hashlib,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/".run/acceptance"
key=(ROOT/".run/local-full/api-key").read_text(encoding="utf-8-sig").strip()
def request(base,path,data=None,headers=None):
    r=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={"Content-Type":"application/json",**(headers or {})})
    with urllib.request.urlopen(r,timeout=120) as f:return f.read(),dict(f.headers)
def api(path):return json.loads(request("http://127.0.0.1:18001/api/v1",path,headers={"X-API-Key":key})[0])
report={"status":"failed"}
try:
    report["queue"]=api("/jobs/summary")["queue"]
    assert report["queue"]["active_workers"]==2 and report["queue"]["dead_letters"]==0
    report["quality"]=api("/workspaces/workspace_default/quality")
    report["retention_preview"]=api("/workspaces/workspace_default/retention")
    previous=json.loads((OUT/"multifile-final-20261004-artifacts.json").read_text(encoding="utf-8"))
    artifact=next(a for a in previous["items"] if a["name"]=="fix.patch")
    info=api("/artifacts/"+artifact["id"]+"/download-info")
    data,headers=request("http://127.0.0.1:18001/api/v1","/artifacts/"+artifact["id"]+"/download",headers={"X-API-Key":key})
    assert hashlib.sha256(data).hexdigest()==info["sha256"] and len(data)==info["size_bytes"]
    assert next(v for k,v in headers.items() if k.lower()=="x-artifact-sha256")==info["sha256"]
    report["download_integrity"]={**info,"verified":True,"scope":"authenticated HTTP bytes, not browser file landing"}
    rules=json.loads(request("http://127.0.0.1:19091","/api/v1/rules")[0])
    report["prometheus_rules"]=[{"name":r["name"],"health":r["health"]} for g in rules["data"]["groups"] for r in g["rules"]]
    assert len(report["prometheus_rules"])==11
    now=datetime.now(timezone.utc);labels={"alertname":"ResearchForgeAcceptanceRouting","severity":"info","scope":"local-acceptance-20261004"}
    alert={"labels":labels,"annotations":{"summary":"Controlled local routing acceptance; no outbound receiver"},"startsAt":now.isoformat(),"endsAt":(now+timedelta(minutes=2)).isoformat()}
    request("http://127.0.0.1:19093","/api/v2/alerts",[alert])
    alerts=json.loads(request("http://127.0.0.1:19093","/api/v2/alerts")[0]);matched=next(a for a in alerts if a["labels"]==labels)
    report["alertmanager"]={"status":"passed","receivers":matched["receivers"],"scope":"local routing only; outbound notification not configured"}
    alert["endsAt"]=(now+timedelta(seconds=1)).isoformat();request("http://127.0.0.1:19093","/api/v2/alerts",[alert])
    report["readiness"]=api("/system/production-readiness?probe_runtime=true&probe_dependencies=true")
    report["status"]="passed"
finally:
    (OUT/"product-operations-live-20261004.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
