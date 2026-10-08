import json
import re
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError


class CriticVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: StrictBool
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=20)


def apply_model_verdict(review: dict, output: str) -> dict:
    content = output.strip()
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
    try:
        payload = json.loads(content)
        if isinstance(payload, dict):
            raw_score = payload.get("score")
            if isinstance(raw_score, str):
                match = re.search(r"[-+]?\d+(?:\.\d+)?", raw_score)
                if match:
                    raw_score = float(match.group(0))
            if isinstance(raw_score, (int, float)):
                score = float(raw_score)
                if 1 < score <= 10:
                    payload["score"] = score / 10
                elif 10 < score <= 100:
                    payload["score"] = score / 100
        verdict = CriticVerdict.model_validate(payload)
    except (ValueError, ValidationError):
        review.update(accepted=False, score=0.0, observation="模型评审未返回有效的结构化判定。")
        review["checks"]["model_verdict_valid"] = False
        return review
    review["checks"]["model_verdict_valid"] = True
    review["checks"]["model_accepted"] = verdict.accepted
    review["accepted"] = bool(review["accepted"] and verdict.accepted)
    review["score"] = min(float(review["score"]), verdict.score)
    review["model_verdict"] = verdict.model_dump()
    if not verdict.accepted:
        review["observation"] = "模型评审拒绝：" + "; ".join(verdict.reasons)
    return review
