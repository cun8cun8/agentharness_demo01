from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.context import current_user, require_workspace_access
from app.infra.store import store
from app.services import memberships as service
from app.services.authentication import development_auth

router = APIRouter(tags=["workspace-memberships"])


def manage(request, workspace_id):
    require_workspace_access(request, workspace_id)
    user = current_user(request)
    if store.membership_role(user.id, workspace_id) not in {"platform_admin", "workspace_admin"}:
        raise HTTPException(403, "MEMBERSHIP_ADMIN_REQUIRED")
    if workspace_id not in store.workspaces:
        raise HTTPException(404, "WORKSPACE_NOT_FOUND")
    return user


def invoke(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/workspaces/{workspace_id}/members")
async def list_members(workspace_id: str, request: Request):
    require_workspace_access(request, workspace_id)
    items = service.members(store, workspace_id)
    return {"items": items, "total": len(items)}


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
async def update_member(workspace_id: str, user_id: str, body: service.UpdateMemberRequest, request: Request):
    actor = manage(request, workspace_id)
    return invoke(service.update_member, store, workspace_id, user_id, body, actor.id)


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
async def invite(workspace_id: str, body: service.InviteMemberRequest, request: Request):
    actor = manage(request, workspace_id)
    return invoke(service.invite_member, store, workspace_id, body, actor.id)


@router.get("/workspaces/{workspace_id}/invitations")
async def invitations(workspace_id: str, request: Request):
    manage(request, workspace_id)
    items = [service.public_invitation(item) for item in store.workspace_invitations.values() if item["workspace_id"] == workspace_id]
    return {"items": items, "total": len(items)}


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}")
async def revoke(workspace_id: str, invitation_id: str, request: Request):
    actor = manage(request, workspace_id)
    item = store.workspace_invitations.get(invitation_id)
    if not item or item["workspace_id"] != workspace_id:
        raise HTTPException(404, "INVITATION_NOT_FOUND")
    if item["status"] != "pending":
        raise HTTPException(409, "INVITATION_NOT_PENDING")
    item["status"] = "revoked"
    store.add_audit_log(action="workspace.invite.revoke", resource_type="workspace", resource_id=workspace_id,
                        actor_id=actor.id, decision="allow", detail_json={"invitation_id": invitation_id})
    store._persist()
    return service.public_invitation(item)


class AcceptInvitation(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.post("/auth/invitations/accept")
async def accept(body: AcceptInvitation, request: Request):
    return invoke(service.accept_invitation, store, body.token, current_user(request).id)


class SwitchWorkspace(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200)


@router.post("/auth/workspace")
async def switch(body: SwitchWorkspace, request: Request, response: Response):
    session = invoke(store.auth_session, current_user(request).id, body.workspace_id)
    response.set_cookie(service.WORKSPACE_COOKIE, body.workspace_id, httponly=True, secure=not development_auth(),
                        samesite="lax", max_age=30 * 24 * 3600)
    return session
