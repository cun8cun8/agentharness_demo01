from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.domain.schemas import ToolManifest, ToolStatus


@dataclass
class ToolContext:
    task_id: str
    run_id: str
    step_id: str
    repo_path: str | None
    policy_version_id: str


@dataclass
class ToolResult:
    tool_name: str
    status: ToolStatus
    input: dict[str, Any]
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error_message: str | None = None


class Tool(ABC):
    name: str
    description: str
    risk_level: str
    requires_approval: bool = False
    sandbox_required: bool = True
    timeout_seconds: int = 120
    input_schema: dict[str, str] = {}
    output_schema: dict[str, str] = {}

    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            requires_approval=self.requires_approval,
            sandbox_required=self.sandbox_required,
            timeout_seconds=self.timeout_seconds,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

    @abstractmethod
    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        ...

