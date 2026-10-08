import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import CreateUserRequest, CreateWorkspaceRequest, CreateTaskRequest
from app.infra.store import InMemoryStore
from app.services import memberships


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_ENV", "local")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_CONTROLLER_ENABLED", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_invitation_is_single_use_identity_bound_and_persisted():
    store = InMemoryStore()
    store.create_workspace(CreateWorkspaceRequest(id="workspace_two", name="Two"))
    store.create_user(CreateUserRequest(id="member", email="m@test.local", name="Member", role="viewer"))
    invite = memberships.invite_member(store, "workspace_two", memberships.InviteMemberRequest(user_id="member", role="operator"), "user_admin")
    assert "token" not in str(store.workspace_invitations[invite["id"]].keys()).replace("token_hash", "")
    with pytest.raises(ValueError, match="INVITATION_INVALID"):
        memberships.accept_invitation(store, invite["token"], "user_admin")
    memberships.accept_invitation(store, invite["token"], "member")
    with pytest.raises(ValueError, match="INVITATION_INVALID"):
        memberships.accept_invitation(store, invite["token"], "member")
    loaded = InMemoryStore()
    loaded._restore_snapshot_data(store._snapshot_data())
    assert loaded.auth_session("member", "workspace_two").workspace_role == "operator"
    assert loaded.auth_session("member").workspace_role == "viewer"
    memberships.update_member(loaded, "workspace_two", "member", memberships.UpdateMemberRequest(status="disabled"), "user_admin")
    assert loaded.membership_role("member", "workspace_two") is None


def test_expired_invitation_does_not_grant_membership():
    store = InMemoryStore()
    store.create_workspace(CreateWorkspaceRequest(id="w_expiry", name="Expiry"))
    store.create_user(CreateUserRequest(id="member_expiry", name="Member", email="e@test.local"))
    invite = memberships.invite_member(store, "w_expiry", memberships.InviteMemberRequest(user_id="member_expiry"), "user_admin")
    store.workspace_invitations[invite["id"]]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="INVITATION_EXPIRED"):
        memberships.accept_invitation(store, invite["token"], "member_expiry")
    assert store.membership_role("member_expiry", "w_expiry") is None


def test_api_switch_uses_workspace_role_not_global_role_and_revocation_is_immediate():
    from app.main import app
    from app.infra.store import store
    key = uuid4().hex
    workspace = store.create_workspace(CreateWorkspaceRequest(name="Secondary " + key))
    user = store.create_user(CreateUserRequest(name="Operator", email=key + "@test.local", role="operator"))
    invite = memberships.invite_member(store, workspace.id, memberships.InviteMemberRequest(user_id=user.id, role="viewer"), "user_admin")
    with TestClient(app) as client:
        headers = {"X-User-ID": user.id}
        assert client.post("/api/v1/auth/invitations/accept", headers=headers, json={"token": invite["token"]}).status_code == 200
        switched = client.post("/api/v1/auth/workspace", headers=headers, json={"workspace_id": workspace.id})
        assert switched.status_code == 200 and switched.json()["workspace_role"] == "viewer"
        assert client.post("/api/v1/tasks", headers=headers, json={"title": "blocked", "goal": "read only", "workspace_id": workspace.id}).status_code == 403
        memberships.update_member(store, workspace.id, user.id, memberships.UpdateMemberRequest(role="workspace_admin"), "user_admin")
        assert client.post("/api/v1/tasks", headers=headers, json={"title": "allowed", "goal": "work", "workspace_id": workspace.id}).status_code == 200
        assert client.post("/api/v1/users", headers=headers, json={"name": "No", "email": "no@test.local", "role": "admin"}).status_code == 403
        assert client.post("/api/v1/tasks", headers=headers, json={"title": "cross", "goal": "denied", "workspace_id": "workspace_default"}).status_code == 403
        memberships.update_member(store, workspace.id, user.id, memberships.UpdateMemberRequest(status="disabled"), "user_admin")
        assert client.get("/api/v1/tasks", headers=headers).status_code == 403
        assert client.post("/api/v1/auth/workspace", headers=headers, json={"workspace_id": "workspace_default"}).status_code == 200


def test_controller_capacity_fifo_and_cancellation(monkeypatch):
    from app.services import training
    from app.services.training_controller import TrainingController, enqueue_training, training_action
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_CONTROLLER_ENABLED", "1")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_CONCURRENCY", "1")
    store = InMemoryStore()
    for key in ["first", "second", "third"]:
        store.training_jobs[key] = {"id": key, "status": "prepared"}
        enqueue_training(store, store.training_jobs[key])
    starts = []
    def start(_store, record):
        starts.append(record["id"])
        record["status"] = "running"
    monkeypatch.setattr(training, "start_training", start)
    monkeypatch.setattr(training, "refresh_training", lambda *_: None)
    async def run():
        controller = TrainingController(store)
        await controller.tick()
        await controller.tick()
        assert starts == ["first"]
        await training_action(store, "second", "cancel")
        store.training_jobs["first"]["status"] = "succeeded"
        await controller.tick()
        assert starts == ["first", "third"]
        assert store.training_jobs["second"]["status"] == "cancelled"
    asyncio.run(run())


def test_query_search_covers_all_pages_and_literals():
    from app.infra.queries import page_records
    store = InMemoryStore()
    for i in range(30):
        store.create_task(CreateTaskRequest(title=f"task {i}", goal="target 100%_literal" if i == 0 else "different"))
    found = page_records(store, "tasks", query="100%_literal", limit=25)
    assert found["total"] == 1 and found["items"][0]["title"] == "task 0"
    unfiltered = page_records(store, "tasks", filters={"status": ""}, limit=50)
    assert unfiltered["total"] == 30
