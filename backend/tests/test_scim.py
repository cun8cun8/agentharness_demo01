from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import get_settings
from app.infra.store import store
from app.main import app


TOKEN = "scim-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
EXTENSION = "urn:ietf:params:scim:schemas:extension:researchforge:2.0:User"
GROUP_EXTENSION = "urn:ietf:params:scim:schemas:extension:researchforge:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def test_scim_provisions_and_deactivates_user(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SCIM_BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()
    client = TestClient(app)
    email = f"scim-{uuid4().hex}@example.test"
    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User", EXTENSION],
        "userName": email,
        "displayName": "SCIM Operator",
        "active": True,
        EXTENSION: {"workspace_id": "workspace_default", "role": "operator"},
    }

    assert client.get("/api/v1/scim/v2/ServiceProviderConfig").status_code == 401
    config = client.get("/api/v1/scim/v2/ServiceProviderConfig", headers=HEADERS)
    assert config.status_code == 200
    assert config.json()["patch"]["supported"] is True
    assert client.get("/api/v1/scim/v2/ResourceTypes/User", headers=HEADERS).json()["endpoint"] == "/Users"

    created = client.post("/api/v1/scim/v2/Users", headers=HEADERS, json=payload)
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert created.json()[EXTENSION]["role"] == "operator"

    listed = client.get("/api/v1/scim/v2/Users", headers=HEADERS, params={"filter": f'userName eq "{email}"'})
    assert listed.status_code == 200
    assert listed.json()["totalResults"] == 1
    assert listed.json()["Resources"][0]["id"] == user_id

    patched = client.patch(
        f"/api/v1/scim/v2/Users/{user_id}",
        headers=HEADERS,
        json={"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    assert patched.status_code == 200
    assert patched.json()["active"] is False
    assert store.get_user(user_id).status == "disabled"

    deleted = client.delete(f"/api/v1/scim/v2/Users/{user_id}", headers=HEADERS)
    assert deleted.status_code == 204
    assert store.get_user(user_id).status == "disabled"
    get_settings.cache_clear()


def test_scim_rejects_unknown_workspace_and_unsupported_filter(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SCIM_BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()
    client = TestClient(app)
    payload = {
        "userName": f"bad-{uuid4().hex}@example.test",
        "displayName": "Outside workspace",
        EXTENSION: {"workspace_id": "workspace_unknown", "role": "viewer"},
    }
    assert client.post("/api/v1/scim/v2/Users", headers=HEADERS, json=payload).status_code == 400
    response = client.get("/api/v1/scim/v2/Users", headers=HEADERS, params={"filter": 'active eq "true"'})
    assert response.status_code == 400
    get_settings.cache_clear()


def test_scim_group_grants_and_revokes_workspace_membership(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SCIM_BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()
    client = TestClient(app)
    user = client.post(
        "/api/v1/scim/v2/Users",
        headers=HEADERS,
        json={"userName": f"group-{uuid4().hex}@example.test", "displayName": "Group User", EXTENSION: {"workspace_id": "workspace_default", "role": "viewer"}},
    )
    assert user.status_code == 201
    user_id = user.json()["id"]

    resource_types = client.get("/api/v1/scim/v2/ResourceTypes", headers=HEADERS)
    assert resource_types.status_code == 200
    assert {item["id"] for item in resource_types.json()["Resources"]} == {"User", "Group"}
    group = client.post(
        "/api/v1/scim/v2/Groups",
        headers=HEADERS,
        json={"displayName": "Platform Operators", "members": [{"value": user_id}], GROUP_EXTENSION: {"workspace_id": "workspace_default", "role": "workspace_admin"}},
    )
    assert group.status_code == 201
    group_id = group.json()["id"]
    membership = store.workspace_memberships[f"workspace_default:{user_id}"]
    assert membership["role"] == "workspace_admin"
    assert membership["source"] == "scim"

    removed = client.patch(
        f"/api/v1/scim/v2/Groups/{group_id}",
        headers=HEADERS,
        json={"schemas": [PATCH_SCHEMA], "Operations": [{"op": "remove", "path": f'members[value eq "{user_id}"]'}]},
    )
    assert removed.status_code == 200
    assert removed.json()["members"] == []
    assert f"workspace_default:{user_id}" not in store.workspace_memberships

    added = client.patch(
        f"/api/v1/scim/v2/Groups/{group_id}",
        headers=HEADERS,
        json={"schemas": [PATCH_SCHEMA], "Operations": [{"op": "add", "path": "members", "value": [{"value": user_id}]}]},
    )
    assert added.status_code == 200
    assert store.workspace_memberships[f"workspace_default:{user_id}"]["role"] == "workspace_admin"
    assert client.delete(f"/api/v1/scim/v2/Groups/{group_id}", headers=HEADERS).status_code == 204
    assert f"workspace_default:{user_id}" not in store.workspace_memberships
    get_settings.cache_clear()
