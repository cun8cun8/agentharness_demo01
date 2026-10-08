from datetime import datetime, timedelta, timezone

from app.domain.schemas import ToolCall
from app.infra.store import InMemoryStore


def test_tool_calls_are_returned_in_creation_order() -> None:
    store = InMemoryStore()
    start = datetime.now(timezone.utc)
    store.tool_calls = {
        "later": ToolCall(
            id="later",
            step_id="step-2",
            run_id="run-1",
            tool_name="test.run",
            created_at=start + timedelta(seconds=2),
        ),
        "earlier": ToolCall(
            id="earlier",
            step_id="step-1",
            run_id="run-1",
            tool_name="file.read",
            created_at=start,
        ),
    }

    assert [item.id for item in store.list_tool_calls("run-1")] == ["earlier", "later"]
