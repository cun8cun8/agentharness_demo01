import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.api import runs

def test_artifact_download_checks_run_access_and_preserves_patch_bytes(monkeypatch):
    patch='diff --git a/a.py b/a.py\n+value = "中文"\n'
    artifact=SimpleNamespace(id='a1',run_id='r1',name='fix.patch',content=patch,metadata={})
    monkeypatch.setattr(runs.store,'read_artifact',lambda _:artifact)
    monkeypatch.setattr(runs.store,'read_run',lambda _:SimpleNamespace(id='r1'))
    access=[]
    monkeypatch.setattr(runs,'require_run_access',lambda request,run:access.append(run.id))
    response=asyncio.run(runs.download_artifact(None,'a1'))
    assert access==['r1']
    assert response.body==patch.encode('utf-8')
    assert response.headers['content-disposition']=='attachment; filename="fix.patch"'
    assert response.headers['cache-control']=='no-store'

def test_artifact_download_cannot_bypass_workspace_access(monkeypatch):
    monkeypatch.setattr(runs.store,'read_artifact',lambda _:SimpleNamespace(run_id='other'))
    monkeypatch.setattr(runs.store,'read_run',lambda _:SimpleNamespace(id='other'))
    def forbidden(*args):
        raise HTTPException(403,'WORKSPACE_ACCESS_DENIED')
    monkeypatch.setattr(runs,'require_run_access',forbidden)
    with pytest.raises(HTTPException) as error:
        asyncio.run(runs.download_artifact(None,'a1'))
    assert error.value.status_code==403
