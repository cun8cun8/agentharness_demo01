from uuid import uuid4

from fastapi.testclient import TestClient

from app.infra.store import store
from app.main import app


client = TestClient(app)


def test_audit_log_search_matches_detail_and_actor() -> None:
    marker = f"audit-search-{uuid4().hex}"
    store.add_audit_log(
        action="audit.searchable",
        resource_type="test_resource",
        resource_id=marker,
        actor_id="operator-search",
        decision="completed",
        detail_json={"marker": marker},
    )

    response = client.get(f"/api/v1/audit-logs?query={marker}")

    assert response.status_code == 200
    assert any(item["resource_id"] == marker for item in response.json()["items"])


def test_audit_log_search_is_case_insensitive() -> None:
    marker = f"Audit-Case-{uuid4().hex}"
    store.add_audit_log(
        action="audit.case",
        resource_type="test_resource",
        resource_id=marker,
        decision="allow",
    )

    response = client.get(f"/api/v1/audit-logs?query={marker.lower()}")

    assert response.status_code == 200
    assert any(item["resource_id"] == marker for item in response.json()["items"])
