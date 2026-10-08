"""Narrow SCIM 2.0 user provisioning surface for enterprise identity providers."""
from __future__ import annotations

import hmac
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response

from app.config import get_settings
from app.domain.schemas import CreateUserRequest, UpdateUserRequest, UserResponse
from app.infra.store import store
from app.services.secrets import resolve_secret


router = APIRouter(tags=["scim"])
CORE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
EXT_SCHEMA = "urn:ietf:params:scim:schemas:extension:researchforge:2.0:User"
GROUP_EXT_SCHEMA = "urn:ietf:params:scim:schemas:extension:researchforge:2.0:Group"
SERVICE_PROVIDER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"


def _authorize(request: Request) -> None:
    settings = get_settings()
    expected = resolve_secret(settings.scim_bearer_token_env)
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not expected:
        raise HTTPException(503, "SCIM_NOT_CONFIGURED")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "SCIM_UNAUTHORIZED")


def _workspace(payload: dict[str, Any]) -> str:
    settings = get_settings()
    extension = payload.get(EXT_SCHEMA)
    requested = extension.get("workspace_id") if isinstance(extension, dict) else None
    workspace_id = str(requested or settings.scim_default_workspace_id)
    if workspace_id not in settings.scim_allowed_workspace_ids or workspace_id not in store.workspaces:
        raise HTTPException(400, "SCIM_WORKSPACE_NOT_ALLOWED")
    return workspace_id


def _role(payload: dict[str, Any], existing: UserResponse | None = None) -> str:
    extension = payload.get(EXT_SCHEMA)
    role = extension.get("role") if isinstance(extension, dict) else (existing.role if existing else "viewer")
    if role not in {"admin", "operator", "viewer"}:
        raise HTTPException(400, "SCIM_ROLE_INVALID")
    return str(role)


def _resource(user: UserResponse) -> dict[str, Any]:
    return {
        "schemas": [CORE_SCHEMA, EXT_SCHEMA],
        "id": user.id,
        "userName": user.email,
        "displayName": user.name,
        "name": {"formatted": user.name},
        "active": user.status == "active",
        EXT_SCHEMA: {"workspace_id": user.workspace_id, "role": user.role},
        "meta": {"resourceType": "User", "created": user.created_at.isoformat(), "lastModified": user.created_at.isoformat()},
    }


def _input(payload: dict[str, Any], existing: UserResponse | None = None) -> tuple[str, str, bool, str, str]:
    email = payload.get("userName", existing.email if existing else None)
    name_data = payload.get("name")
    formatted = name_data.get("formatted") if isinstance(name_data, dict) else None
    name = payload.get("displayName") or formatted or (existing.name if existing else None)
    active = payload.get("active", existing.status == "active" if existing else True)
    if not isinstance(email, str) or not email or len(email) > 320:
        raise HTTPException(400, "SCIM_USERNAME_REQUIRED")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise HTTPException(400, "SCIM_NAME_REQUIRED")
    if not isinstance(active, bool):
        raise HTTPException(400, "SCIM_ACTIVE_INVALID")
    return email.strip().lower(), name.strip(), active, _workspace(payload) if existing is None or EXT_SCHEMA in payload else existing.workspace_id, _role(payload, existing)


def _audit(action: str, user: UserResponse, decision: str) -> None:
    store.add_audit_log(action=action, resource_type="user", resource_id=user.id, actor_id="scim", decision=decision,
                        detail_json={"workspace_id": user.workspace_id, "role": user.role, "status": user.status})


def _groups_enabled() -> None:
    if not get_settings().scim_groups_enabled:
        raise HTTPException(404, "SCIM_GROUPS_DISABLED")


def _group_workspace(payload: dict[str, Any]) -> str:
    settings = get_settings()
    extension = payload.get(GROUP_EXT_SCHEMA)
    requested = extension.get("workspace_id") if isinstance(extension, dict) else None
    workspace_id = str(requested or settings.scim_default_workspace_id)
    if workspace_id not in settings.scim_allowed_workspace_ids or workspace_id not in store.workspaces:
        raise HTTPException(400, "SCIM_WORKSPACE_NOT_ALLOWED")
    return workspace_id


def _group_role(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> str:
    extension = payload.get(GROUP_EXT_SCHEMA)
    role = extension.get("role") if isinstance(extension, dict) else (existing or {}).get("role", "viewer")
    if role not in {"workspace_admin", "operator", "viewer"}:
        raise HTTPException(400, "SCIM_GROUP_ROLE_INVALID")
    return str(role)


def _member_ids(value: Any, fallback: list[str] | None = None) -> list[str]:
    if value is None:
        return list(fallback or [])
    values = value if isinstance(value, list) else [value]
    if len(values) > 5000:
        raise HTTPException(400, "SCIM_GROUP_MEMBERS_LIMIT")
    result: list[str] = []
    for item in values:
        member_id = item.get("value") if isinstance(item, dict) else None
        if not isinstance(member_id, str) or not member_id or len(member_id) > 200:
            raise HTTPException(400, "SCIM_GROUP_MEMBER_INVALID")
        if store.get_user(member_id) is None:
            raise HTTPException(400, "SCIM_GROUP_MEMBER_NOT_FOUND")
        if member_id not in result:
            result.append(member_id)
    return result


def _group_input(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> tuple[str, str | None, list[str], str, str]:
    display_name = payload.get("displayName", (existing or {}).get("display_name"))
    external_id = payload.get("externalId", (existing or {}).get("external_id"))
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 200:
        raise HTTPException(400, "SCIM_GROUP_DISPLAY_NAME_REQUIRED")
    if external_id is not None and (not isinstance(external_id, str) or len(external_id) > 320):
        raise HTTPException(400, "SCIM_GROUP_EXTERNAL_ID_INVALID")
    current_members = list((existing or {}).get("member_ids") or [])
    members = _member_ids(payload.get("members"), current_members)
    workspace_id = _group_workspace(payload) if existing is None or GROUP_EXT_SCHEMA in payload else str(existing["workspace_id"])
    return display_name.strip(), external_id.strip() if isinstance(external_id, str) and external_id.strip() else None, members, workspace_id, _group_role(payload, existing)


def _group_resource(group: dict[str, Any]) -> dict[str, Any]:
    members = []
    for user_id in group.get("member_ids") or []:
        user = store.get_user(user_id)
        if user:
            members.append({"value": user.id, "$ref": f"/Users/{user.id}", "display": user.email})
    return {
        "schemas": [GROUP_SCHEMA, GROUP_EXT_SCHEMA],
        "id": group["id"],
        "displayName": group["display_name"],
        "externalId": group.get("external_id"),
        "members": members,
        GROUP_EXT_SCHEMA: {"workspace_id": group["workspace_id"], "role": group["role"]},
        "meta": {"resourceType": "Group", "created": group["created_at"], "lastModified": group["updated_at"]},
    }


def _sync_group_memberships(affected_user_ids: set[str]) -> None:
    """Rebuild only SCIM-owned membership grants, preserving manual grants."""
    rank = {"viewer": 1, "operator": 2, "workspace_admin": 3}
    for user_id in affected_user_ids:
        desired: dict[str, list[dict[str, Any]]] = {}
        for group in store.scim_groups.values():
            if group.get("status") == "active" and user_id in set(group.get("member_ids") or []):
                desired.setdefault(str(group["workspace_id"]), []).append(group)
        current_scim_workspaces = {
            str(item.get("workspace_id"))
            for item in store.workspace_memberships.values()
            if item.get("user_id") == user_id and item.get("source") == "scim"
        }
        for workspace_id in current_scim_workspaces | set(desired):
            key = f"{workspace_id}:{user_id}"
            existing = store.workspace_memberships.get(key)
            if existing is not None and existing.get("source") != "scim":
                continue
            candidates = desired.get(workspace_id, [])
            if not candidates:
                store.workspace_memberships.pop(key, None)
                continue
            selected = max(candidates, key=lambda item: rank[str(item["role"])])
            store.workspace_memberships[key] = {
                "id": key,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "role": selected["role"],
                "status": "active",
                "source": "scim",
                "scim_group_ids": sorted(str(item["id"]) for item in candidates),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }


@router.get("/scim/v2/ServiceProviderConfig")
async def service_provider_config(request: Request):
    _authorize(request)
    return {
        "schemas": [SERVICE_PROVIDER_SCHEMA],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer Token", "description": "ResearchForge SCIM provisioning token"}],
    }


def _user_resource_type() -> dict[str, Any]:
    return {
        "schemas": [RESOURCE_TYPE_SCHEMA],
        "id": "User",
        "name": "User",
        "endpoint": "/Users",
        "description": "ResearchForge workspace user",
        "schema": CORE_SCHEMA,
        "schemaExtensions": [{"schema": EXT_SCHEMA, "required": False}],
    }


def _group_resource_type() -> dict[str, Any]:
    return {
        "schemas": [RESOURCE_TYPE_SCHEMA],
        "id": "Group",
        "name": "Group",
        "endpoint": "/Groups",
        "description": "ResearchForge workspace role group",
        "schema": GROUP_SCHEMA,
        "schemaExtensions": [{"schema": GROUP_EXT_SCHEMA, "required": False}],
    }


def _user_schema() -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_SCHEMA],
        "id": CORE_SCHEMA,
        "name": "User",
        "description": "ResearchForge SCIM User",
        "attributes": [
            {"name": "userName", "type": "string", "required": True, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "server"},
            {"name": "displayName", "type": "string", "required": False, "mutability": "readWrite", "returned": "default"},
            {"name": "active", "type": "boolean", "required": False, "mutability": "readWrite", "returned": "default"},
        ],
    }


def _group_schema() -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_SCHEMA],
        "id": GROUP_SCHEMA,
        "name": "Group",
        "description": "ResearchForge SCIM Group",
        "attributes": [
            {"name": "displayName", "type": "string", "required": True, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
            {"name": "members", "type": "complex", "multiValued": True, "required": False, "mutability": "readWrite", "returned": "default", "subAttributes": [{"name": "value", "type": "string", "required": True, "mutability": "immutable"}]},
        ],
    }


@router.get("/scim/v2/ResourceTypes")
async def resource_types(request: Request):
    _authorize(request)
    resources = [_user_resource_type()]
    if get_settings().scim_groups_enabled:
        resources.append(_group_resource_type())
    return {"schemas": [LIST_SCHEMA], "totalResults": len(resources), "startIndex": 1, "itemsPerPage": len(resources), "Resources": resources}


@router.get("/scim/v2/ResourceTypes/User")
async def resource_type(request: Request):
    _authorize(request)
    return _user_resource_type()


@router.get("/scim/v2/ResourceTypes/Group")
async def group_resource_type(request: Request):
    _authorize(request)
    _groups_enabled()
    return _group_resource_type()


@router.get("/scim/v2/Schemas")
async def schemas(request: Request):
    _authorize(request)
    resources = [_user_schema()]
    if get_settings().scim_groups_enabled:
        resources.append(_group_schema())
    return {"schemas": [LIST_SCHEMA], "totalResults": len(resources), "startIndex": 1, "itemsPerPage": len(resources), "Resources": resources}


@router.get("/scim/v2/Schemas/{schema_id:path}")
async def schema(request: Request, schema_id: str):
    _authorize(request)
    if schema_id == CORE_SCHEMA:
        return _user_schema()
    if schema_id == GROUP_SCHEMA and get_settings().scim_groups_enabled:
        return _group_schema()
    raise HTTPException(404, "SCIM_SCHEMA_NOT_FOUND")


@router.get("/scim/v2/Users")
async def list_users(request: Request, startIndex: int = 1, count: int = 100, filter: str | None = None):
    _authorize(request)
    if startIndex < 1 or not 1 <= count <= 200:
        raise HTTPException(400, "SCIM_PAGINATION_INVALID")
    workspace_id = get_settings().scim_default_workspace_id
    users = store.list_users(workspace_id=workspace_id)
    if filter:
        match = re.fullmatch(r'\s*userName\s+eq\s+"([^"]{1,320})"\s*', filter, flags=re.IGNORECASE)
        if not match:
            raise HTTPException(400, "SCIM_FILTER_UNSUPPORTED")
        email = match.group(1).lower()
        users = [user for user in users if user.email.lower() == email]
    page = users[startIndex - 1:startIndex - 1 + count]
    return {"schemas": [LIST_SCHEMA], "totalResults": len(users), "startIndex": startIndex, "itemsPerPage": len(page), "Resources": [_resource(user) for user in page]}


@router.post("/scim/v2/Users", status_code=201)
async def create_user(request: Request, payload: dict[str, Any]):
    _authorize(request)
    email, name, active, workspace_id, role = _input(payload)
    if any(user.email.lower() == email for user in store.list_users(workspace_id=workspace_id)):
        raise HTTPException(409, "SCIM_USER_EXISTS")
    user = store.create_user(CreateUserRequest(workspace_id=workspace_id, email=email, name=name, role=role, status="active" if active else "disabled"))
    _audit("scim.user.create", user, "allow")
    return _resource(user)


@router.get("/scim/v2/Users/{user_id}")
async def get_user(request: Request, user_id: str):
    _authorize(request)
    user = store.get_user(user_id)
    if not user or user.workspace_id not in get_settings().scim_allowed_workspace_ids:
        raise HTTPException(404, "SCIM_USER_NOT_FOUND")
    return _resource(user)


@router.put("/scim/v2/Users/{user_id}")
async def replace_user(request: Request, user_id: str, payload: dict[str, Any]):
    _authorize(request)
    existing = store.get_user(user_id)
    if not existing or existing.workspace_id not in get_settings().scim_allowed_workspace_ids:
        raise HTTPException(404, "SCIM_USER_NOT_FOUND")
    email, name, active, workspace_id, role = _input(payload, existing)
    if workspace_id != existing.workspace_id:
        raise HTTPException(400, "SCIM_WORKSPACE_CHANGE_NOT_ALLOWED")
    user = store.update_user(user_id, UpdateUserRequest(email=email, name=name, role=role, status="active" if active else "disabled"))
    assert user is not None
    _audit("scim.user.replace", user, "allow")
    return _resource(user)


@router.patch("/scim/v2/Users/{user_id}")
async def patch_user(request: Request, user_id: str, payload: dict[str, Any]):
    _authorize(request)
    if payload.get("schemas") != [PATCH_SCHEMA] or not isinstance(payload.get("Operations"), list):
        raise HTTPException(400, "SCIM_PATCH_INVALID")
    existing = store.get_user(user_id)
    if not existing or existing.workspace_id not in get_settings().scim_allowed_workspace_ids:
        raise HTTPException(404, "SCIM_USER_NOT_FOUND")
    merged: dict[str, Any] = {"userName": existing.email, "displayName": existing.name, "active": existing.status == "active", EXT_SCHEMA: {"workspace_id": existing.workspace_id, "role": existing.role}}
    for operation in payload["Operations"]:
        if not isinstance(operation, dict) or str(operation.get("op", "replace")).lower() not in {"replace", "add"}:
            raise HTTPException(400, "SCIM_PATCH_OPERATION_UNSUPPORTED")
        path, value = operation.get("path"), operation.get("value")
        if path in {"userName", "displayName", "active"}:
            merged[path] = value
        elif path == f"{EXT_SCHEMA}:role":
            merged[EXT_SCHEMA]["role"] = value
        elif path == f"{EXT_SCHEMA}:workspace_id":
            merged[EXT_SCHEMA]["workspace_id"] = value
        elif path is None and isinstance(value, dict):
            merged.update(value)
        else:
            raise HTTPException(400, "SCIM_PATCH_PATH_UNSUPPORTED")
    return await replace_user(request, user_id, merged)


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
async def deactivate_user(request: Request, user_id: str):
    _authorize(request)
    existing = store.get_user(user_id)
    if not existing or existing.workspace_id not in get_settings().scim_allowed_workspace_ids:
        raise HTTPException(404, "SCIM_USER_NOT_FOUND")
    user = store.update_user(user_id, UpdateUserRequest(status="disabled"))
    assert user is not None
    _audit("scim.user.deactivate", user, "allow")
    return Response(status_code=204)


def _find_group(group_id: str) -> dict[str, Any]:
    group = store.scim_groups.get(group_id)
    if not group or group.get("workspace_id") not in get_settings().scim_allowed_workspace_ids:
        raise HTTPException(404, "SCIM_GROUP_NOT_FOUND")
    return group


def _audit_group(action: str, group: dict[str, Any], decision: str, affected_user_ids: set[str]) -> None:
    store.add_audit_log(
        action=action,
        resource_type="scim_group",
        resource_id=str(group["id"]),
        actor_id="scim",
        decision=decision,
        detail_json={
            "workspace_id": group["workspace_id"],
            "role": group["role"],
            "member_count": len(group.get("member_ids") or []),
            "affected_user_ids": sorted(affected_user_ids),
        },
    )


@router.get("/scim/v2/Groups")
async def list_groups(request: Request, startIndex: int = 1, count: int = 100, filter: str | None = None):
    _authorize(request)
    _groups_enabled()
    if startIndex < 1 or not 1 <= count <= 200:
        raise HTTPException(400, "SCIM_PAGINATION_INVALID")
    groups = [item for item in store.scim_groups.values() if item.get("workspace_id") in get_settings().scim_allowed_workspace_ids]
    if filter:
        match = re.fullmatch(r'\s*displayName\s+eq\s+"([^"]{1,200})"\s*', filter, flags=re.IGNORECASE)
        if not match:
            raise HTTPException(400, "SCIM_FILTER_UNSUPPORTED")
        display_name = match.group(1).casefold()
        groups = [group for group in groups if str(group.get("display_name", "")).casefold() == display_name]
    groups.sort(key=lambda item: (str(item.get("display_name", "")).casefold(), str(item["id"])))
    page = groups[startIndex - 1:startIndex - 1 + count]
    return {"schemas": [LIST_SCHEMA], "totalResults": len(groups), "startIndex": startIndex, "itemsPerPage": len(page), "Resources": [_group_resource(group) for group in page]}


@router.post("/scim/v2/Groups", status_code=201)
async def create_group(request: Request, payload: dict[str, Any]):
    _authorize(request)
    _groups_enabled()
    display_name, external_id, member_ids, workspace_id, role = _group_input(payload)
    timestamp = datetime.now(timezone.utc).isoformat()
    group = {
        "id": "scim_group_" + uuid4().hex,
        "display_name": display_name,
        "external_id": external_id,
        "member_ids": member_ids,
        "workspace_id": workspace_id,
        "role": role,
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    store.scim_groups[group["id"]] = group
    affected = set(member_ids)
    _sync_group_memberships(affected)
    _audit_group("scim.group.create", group, "allow", affected)
    return _group_resource(group)


@router.get("/scim/v2/Groups/{group_id}")
async def get_group(request: Request, group_id: str):
    _authorize(request)
    _groups_enabled()
    return _group_resource(_find_group(group_id))


@router.put("/scim/v2/Groups/{group_id}")
async def replace_group(request: Request, group_id: str, payload: dict[str, Any]):
    _authorize(request)
    _groups_enabled()
    existing = _find_group(group_id)
    display_name, external_id, member_ids, workspace_id, role = _group_input(payload, existing)
    if workspace_id != existing["workspace_id"]:
        raise HTTPException(400, "SCIM_WORKSPACE_CHANGE_NOT_ALLOWED")
    group = {
        **existing,
        "display_name": display_name,
        "external_id": external_id,
        "member_ids": member_ids,
        "role": role,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    store.scim_groups[group_id] = group
    affected = set(existing.get("member_ids") or []) | set(member_ids)
    _sync_group_memberships(affected)
    _audit_group("scim.group.replace", group, "allow", affected)
    return _group_resource(group)


def _patch_group_payload(existing: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schemas") != [PATCH_SCHEMA] or not isinstance(payload.get("Operations"), list):
        raise HTTPException(400, "SCIM_PATCH_INVALID")
    merged: dict[str, Any] = {
        "displayName": existing["display_name"],
        "externalId": existing.get("external_id"),
        "members": [{"value": user_id} for user_id in existing.get("member_ids") or []],
        GROUP_EXT_SCHEMA: {"workspace_id": existing["workspace_id"], "role": existing["role"]},
    }
    for operation in payload["Operations"]:
        if not isinstance(operation, dict):
            raise HTTPException(400, "SCIM_PATCH_OPERATION_UNSUPPORTED")
        op = str(operation.get("op", "")).lower()
        path, patch_value = operation.get("path"), operation.get("value")
        if op not in {"add", "replace", "remove"}:
            raise HTTPException(400, "SCIM_PATCH_OPERATION_UNSUPPORTED")
        if path is None:
            if op == "remove" or not isinstance(patch_value, dict):
                raise HTTPException(400, "SCIM_PATCH_PATH_UNSUPPORTED")
            merged.update(patch_value)
            continue
        if path in {"displayName", "externalId"}:
            if op == "remove":
                if path == "displayName":
                    raise HTTPException(400, "SCIM_GROUP_DISPLAY_NAME_REQUIRED")
                merged[path] = None
            else:
                merged[path] = patch_value
            continue
        if path == "members":
            if op == "replace":
                merged["members"] = patch_value
            elif op == "add":
                merged["members"] = [*merged["members"], *[{"value": member_id} for member_id in _member_ids(patch_value)]]
            elif patch_value is None:
                merged["members"] = []
            else:
                removing = set(_member_ids(patch_value))
                merged["members"] = [item for item in merged["members"] if item["value"] not in removing]
            continue
        member_match = re.fullmatch(r'members\[value\s+eq\s+"([^\"]{1,200})"\]', str(path), flags=re.IGNORECASE)
        if member_match and op == "remove":
            member_id = member_match.group(1)
            merged["members"] = [item for item in merged["members"] if item["value"] != member_id]
            continue
        if path in {f"{GROUP_EXT_SCHEMA}:role", f"{GROUP_EXT_SCHEMA}.role"} and op != "remove":
            merged[GROUP_EXT_SCHEMA]["role"] = patch_value
            continue
        if path in {f"{GROUP_EXT_SCHEMA}:workspace_id", f"{GROUP_EXT_SCHEMA}.workspace_id"} and op != "remove":
            merged[GROUP_EXT_SCHEMA]["workspace_id"] = patch_value
            continue
        raise HTTPException(400, "SCIM_PATCH_PATH_UNSUPPORTED")
    return merged


@router.patch("/scim/v2/Groups/{group_id}")
async def patch_group(request: Request, group_id: str, payload: dict[str, Any]):
    _authorize(request)
    _groups_enabled()
    existing = _find_group(group_id)
    return await replace_group(request, group_id, _patch_group_payload(existing, payload))


@router.delete("/scim/v2/Groups/{group_id}", status_code=204)
async def delete_group(request: Request, group_id: str):
    _authorize(request)
    _groups_enabled()
    group = _find_group(group_id)
    affected = set(group.get("member_ids") or [])
    store.scim_groups.pop(group_id, None)
    _sync_group_memberships(affected)
    _audit_group("scim.group.delete", group, "allow", affected)
    return Response(status_code=204)
