import json

import pytest

from app.agent.autonomous import parse_model_action


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "I will inspect the source.\n```json\n{}\n```\nDone."])
def test_action_accepts_model_prose_and_fences(wrapper):
    payload = {"tool": "file.write_patch", "input": {"patch": '+value = {"nested": {"tool": "finish"}}'}, "reason": "fix"}
    assert parse_model_action(wrapper.format(json.dumps(payload))).input == payload["input"]


@pytest.mark.parametrize("output", ["no action", '{"tool":"unknown"}', '{"tool":"finish"} {"tool":"test.run"}', '{"tool":'])
def test_action_rejects_invalid_or_ambiguous_responses(output):
    with pytest.raises(ValueError):
        parse_model_action(output)
