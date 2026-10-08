from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

from pydantic import BaseModel, Field
from typing import Literal

WORKSPACE_COOKIE = "researchforge_workspace"
MemberRole = Literal["workspace_admin", "operator", "viewer"]


class InviteMemberRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    role: MemberRole = "viewer"
    expires_in_hours: int = Field(default=48, ge=1, le=168)


class UpdateMemberRequest(BaseModel):
    role: MemberRole = "viewer"
    status: Literal["active", "disabled"] = "active"


def selected_workspace(request, store, user):
    workspace_id = request.headers.get("x-workspace-id") or request.cookies.get(WORKSPACE_COOKIE) or user.workspace_id
    workspace = store.get_workspace(workspace_id)
    if not workspace or workspace.status != "active" or not store.membership_role(user.id, workspace_id):
        raise ValueError("WORKSPACE_ACCESS_DENIED")
    return workspace_id


def members(store, workspace_id):
    result = []
    for user in store.users.values():
        explicit = store.workspace_memberships.get(f"{workspace_id}:{user.id}")
        role = store.membership_role(user.id, workspace_id)
        if explicit or (user.workspace_id == workspace_id and user.role != "admin"):
            result.append({"id": f"{workspace_id}:{user.id}", "user_id": user.id, "name": user.name,
                           "email": user.email, "role": explicit["role"] if explicit else role,
                           "status": explicit["status"] if explicit else user.status, "workspace_id": workspace_id})
    return result


def update_member(store, workspace_id, user_id, data, actor_id):
    user = store.get_user(user_id)
    if not user or user.role == "admin":
        raise ValueError("WORKSPACE_MEMBER_NOT_EDITABLE")
    key = f"{workspace_id}:{user_id}"
    if not store.membership_role(user_id, workspace_id) and key not in store.workspace_memberships:
        raise ValueError("WORKSPACE_MEMBERSHIP_REQUIRED")
    if actor_id == user_id and (data.role != "workspace_admin" or data.status != "active"):
        raise ValueError("CANNOT_REMOVE_OWN_ADMIN_ACCESS")
    record = {"id": key, "workspace_id": workspace_id, "user_id": user_id, **data.model_dump(),
              "updated_at": datetime.now(timezone.utc).isoformat()}
    store.workspace_memberships[key] = record
    store.add_audit_log(action="workspace.member.update", resource_type="workspace", resource_id=workspace_id,
                        actor_id=actor_id, decision="allow", detail_json={"user_id": user_id, **data.model_dump()})
    store._persist()
    return record


def invite_member(store, workspace_id, data, actor_id):
    user = store.get_user(data.user_id)
    if not user or user.status != "active" or user.role == "admin":
        raise ValueError("ACTIVE_NON_ADMIN_USER_REQUIRED")
    if store.membership_role(user.id, workspace_id):
        raise ValueError("USER_ALREADY_A_MEMBER")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    record = {"id": "invite_" + uuid4().hex, "workspace_id": workspace_id, "user_id": user.id,
              "role": data.role, "status": "pending", "created_by": actor_id, "created_at": now.isoformat(),
              "expires_at": (now + timedelta(hours=data.expires_in_hours)).isoformat(),
              "token_hash": hashlib.sha256(token.encode()).hexdigest()}
    store.workspace_invitations[record["id"]] = record
    store.add_audit_log(action="workspace.invite", resource_type="workspace", resource_id=workspace_id,
                        actor_id=actor_id, decision="allow", detail_json={"invitation_id": record["id"], "user_id": user.id})
    store._persist()
    return {**public_invitation(record), "token": token}


def public_invitation(record):
    return {key: value for key, value in record.items() if key != "token_hash"}


def accept_invitation(store, token, user_id):
    digest = hashlib.sha256(token.encode()).hexdigest()
    record = next((item for item in store.workspace_invitations.values()
                   if secrets.compare_digest(item["token_hash"], digest)), None)
    if not record or record["user_id"] != user_id or record["status"] != "pending":
        raise ValueError("INVITATION_INVALID")
    if datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
        raise ValueError("INVITATION_EXPIRED")
    workspace = store.get_workspace(record["workspace_id"])
    if not workspace or workspace.status != "active":
        raise ValueError("WORKSPACE_DISABLED")
    if store.membership_role(user_id, workspace.id):
        raise ValueError("USER_ALREADY_A_MEMBER")
    key = f"{workspace.id}:{user_id}"
    store.workspace_memberships[key] = {"id": key, "workspace_id": workspace.id, "user_id": user_id,
                                       "role": record["role"], "status": "active"}
    record.update(status="accepted", accepted_at=datetime.now(timezone.utc).isoformat())
    store.add_audit_log(action="workspace.invite.accept", resource_type="workspace", resource_id=workspace.id,
                        actor_id=user_id, decision="allow", detail_json={"invitation_id": record["id"]})
    store._persist()
    return public_invitation(record)
