"""Exercise fixed production fixtures through the deployed public API."""
import json, time, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/".run/acceptance"
key=(ROOT/".run/local-full/api-key").read_text(encoding="utf-8-sig").strip()
def api(path,payload=None):
    request=urllib.request.Request("http://127.0.0.1:18001/api/v1"+path,data=json.dumps(payload).encode() if payload is not None else None,headers={"X-API-Key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=120) as response: return json.loads(response.read())
fixtures=[("python","python -m pytest -q","pricing.py"),("node","npm test","pricing.js")]
report={"status":"running","fixtures":[],"scope":"live qwen-plus, fixed offline dependency and Node fixtures"}
try:
    for language,command,business in fixtures:
        existing=api("/integrations/repositories?query=Production%20"+language)["items"]
        repo=existing[0] if existing else api("/integrations/repositories",{"name":"Production "+language+" acceptance 20261004","provider":"local","local_path":"/var/lib/researchforge/workspaces/repositories/production-"+language+"-20261004","default_branch":"main"})
        profile=api("/integrations/repositories/"+repo["id"]+"/profile")
        recipe=next(r for r in profile["recipes"] if r["language"]==language)
        policy_id="policy_default_v1"
        if language=="node":
            policy=api("/policies/policy_default_v1")
            policy.update(id="policy_node_offline_v1",name="Node offline sandbox policy",allowed_commands=sorted(set(policy["allowed_commands"]+["node","npm"])))
            api("/policies",policy)
            policy_id=policy["id"]
        job=api("/integrations/repositories/"+repo["id"]+"/repair",{"goal":"Only read "+business+". Fix discount: subtract the percent amount instead of adding it. Apply exactly one business-file patch; do not change tests, manifests, dependencies, or lockfiles. Run "+command+" then finish when all 3 tests pass.","test_command":command,"setup_commands":recipe["setup_commands"],"model_name":"qwen-plus","policy_version_id":policy_id,"title":"Production "+language+" dependency acceptance","budget":{"max_steps":20,"max_tokens":80000,"max_model_cost":2,"max_runtime_seconds":900,"max_tool_calls":40}})["job"]
        item={"language":language,"repository_id":repo["id"],"profile":profile,"job_id":job["id"]}
        report["fixtures"].append(item)
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            job=api("/jobs/"+job["id"])
            if job["status"] in {"completed","failed","cancelled"}: break
            time.sleep(5)
        item["job"]=job
        run_id=job["metadata"].get("agent_run_id")
        if run_id:
            run=api("/runs/"+run_id);item["run"]=run
            item["tools"]=api("/runs/"+run_id+"/tool-calls")
            item["progress"]=api("/runs/"+run_id+"/progress")
            item["artifacts"]=api("/runs/"+run_id+"/artifacts")
            assert run["status"]=="completed",run.get("error_summary")
            assert run["metrics"]["validation_tests_passed"]==3
            assert not run["metrics"].get("touched_tests")
            assert len(run["metrics"]["setup_commands_completed"])==1
        assert job["status"]=="completed",job.get("error_summary")
        print(language+" passed: 3/3, fresh preparation, one business file",flush=True)
    report["quality"]=api("/workspaces/workspace_default/quality")
    report["retention_preview"]=api("/workspaces/workspace_default/retention")
    report["readiness"]=api("/system/production-readiness?probe_runtime=true&probe_dependencies=true")
    report["status"]="passed"
except Exception as exc:
    report["status"]="failed";report["error"]=type(exc).__name__+": "+str(exc);raise
finally:
    (OUT/"product-workflow-live-20261004.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
